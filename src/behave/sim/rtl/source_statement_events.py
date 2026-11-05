"""Statement-entry edits that leave original declarations in their scope.

An automatic initializer is entered before its expression is evaluated. A
preceding automatic observer variable records that entry without wrapping the
original declaration in a new block or passing its value through a function.
Comma-separated declarators are split using their unchanged common type prefix;
their initializers still run once, in order, in the original lexical scope.
"""

from .source_edits import Insertion, Replacement

ENTRY_FUNCTION = "__behave_native_event_entry"
RESUME_FUNCTION = "__behave_native_event_resume"
# Native report queues bind this reserved marker to the source process. An
# ordinary compiler sees an empty call; a native settle revisit that does not
# pass the source activation guard must not flush that process's reports.
RESUME_DECLARATION = f"\nfunction automatic void {RESUME_FUNCTION}(); endfunction\n"


def entry_function(callback: str) -> str:
    return f"""
function automatic bit {ENTRY_FUNCTION}(input int unsigned id, input bit observed, input int unsigned scope);
  if (observed) {callback}(id, scope);
  {ENTRY_FUNCTION} = 1'b0;
endfunction
"""


def block_entry(node, spans, callback, *, ordinal, observed, scope, hit):
    """Observe before automatic initializers without adding a lexical scope.

    An outer wrapper would turn a process's outermost named block into an
    inner block, changing disable/report cancellation. A first automatic
    declaration preserves that block and precedes its original declarations.
    Without declaration initializers, insert a statement after declarations:
    adding a first local variable would create a new anonymous scope and
    change diagnostic %m. Uninitialized declarations need no lifetime hook.
    """
    header = node.get("blockName", node["begin"])
    declarations = [item for item in node.get("items", ())
                    if item.get("kind", "").endswith("Declaration")]
    initialized = any("initializer" in declarator for declaration in declarations
                      for declarator in declaration.get("declarators", ()))
    if not initialized:
        after = declarations[-1] if declarations else header
        return Insertion(spans[id(after)][1], f" if ({observed}) {hit}({callback}, {scope}); ")
    return Insertion(spans[id(header)][1],
        f" automatic bit __behave_native_event_block_{ordinal} = "
        f"{ENTRY_FUNCTION}({callback}, {observed}, {scope}); ")


def initializer_entries(source, node, spans, callbacks, *, observed, scope):
    """Return insertions/replacements for selected DataDeclaration declarators.

    ``callbacks`` maps the declared name's byte span to its source event ID.
    An uninitialized or static declarator has no automatic-entry callback.
    """
    declarators = node["declarators"]
    first = declarators[0]
    start = spans[id(node)][0]
    prefix = source.encode()[start:spans[id(first)][0]].decode()
    insertions, replacements = [], []
    for index, declarator in enumerate(declarators):
        if declarator.get("kind") == "Comma":
            continue
        name = spans[id(declarator["name"])]
        if name not in callbacks:
            continue
        entry = (f"automatic bit __behave_native_event_initializer_{name[0]} = "
                 f"{ENTRY_FUNCTION}({callbacks[name]}, {observed}, {scope}); ")
        if index == 0:
            insertions.append(Insertion(start, entry))
        else:
            comma = spans[id(declarators[index - 1])]
            replacements.append(Replacement(*comma, ";"))
            insertions.append(Insertion(comma[1], entry + prefix))
    return insertions, replacements


def for_initializer_entry(declarator, spans, callback, *, observed, scope):
    """Precede a loop declarator without moving its initialization or scope.

    The extra, unused declarator inherits the same type. Its callback executes
    before the original initializer, once per loop entry (even for zero trips).
    """
    start = spans[id(declarator)][0]
    return Insertion(start, f"__behave_native_event_initializer_{start} = "
                     f"{ENTRY_FUNCTION}({callback}, {observed}, {scope}), ")
