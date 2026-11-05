"""Native failure observation after the original argument effects and before exit."""

from dataclasses import dataclass

from .source_edits import Insertion, Replacement
from .source_effects import conflicts

ASSERT_FUNCTION = "__behave_native_event_assert"
DEFERRED_ASSERT_FUNCTION = "__behave_native_event_deferred_assert"
CASE_FAILURE_FUNCTION = "__behave_native_event_case_failure"
NO_EVENT = "32'hffffffff"


@dataclass(frozen=True, slots=True)
class SourceFailure:
    start: int
    end: int
    kind: str
    issue: str | None = None
    post_arguments: bool = False
    deferred: bool = False


def case_failure_function(callback):
    """Compiler-owned marker: called only in the original case failure action.

    The qualified compiler moves a call immediately following the case into
    its existing failure branches, inside the assertion-enable gate. Nothing
    here guesses a match, samples a selector, or duplicates source effects.
    """
    return f"""
function automatic void {CASE_FAILURE_FUNCTION}(
    input int unsigned event_id, input bit observed, input int unsigned scope);
  if (observed) {callback}(event_id, scope);
endfunction
"""


def argument_order_issue(arguments, effects):
    """Never choose an order between interfering diagnostic arguments.

    Literal formatting arguments have no runtime events. One evaluated argument
    may itself terminate; with two or more we need the existing storage/control
    effect certificate, including absence of such an early exit.
    """
    evaluated = [arg for arg in arguments
                 if arg['kind'] not in {'IntegerLiteral', 'RealLiteral', 'StringLiteral'}]
    if len(evaluated) < 2:
        return None
    previous = frozenset(), frozenset()
    for argument in evaluated:
        summary = effects(argument)
        if summary is None or conflicts(previous, summary):
            return "failure arguments need a qualified evaluation order"
        previous = previous[0] | summary[0], previous[1] | summary[1]
    return None


def post_argument_edits(source, node, spans, kind, callback):
    """Format the original argument list once, then observe and raise severity.

    $swrite uses the display/write argument convention (unlike $sformatf).
    Every original value expression and its nested source probes stay in place;
    only the system-task token/output argument changes. The original severity
    operation receives the completed text. A terminating argument never reaches
    either that operation or its failure callback.
    """
    call = node['expr']
    start, end = spans[id(node)]
    name = f"__behave_native_event_message_{start}"
    edits = [Insertion(start, ' begin ')]
    replaced = [Replacement(*spans[id(call['left'])], '$swrite')]
    if kind == '$fatal':
        first = next(arg for arg in call['arguments']['parameters'] if arg['kind'] != 'Comma')
        first_start, first_end = spans[id(first)]
        finish = source.encode()[first_start:first_end].decode()
        replaced.append(Replacement(first_start, first_end, name))
        raise_call = f'$fatal({finish},"%s",{name});'
    else:
        edits.append(Insertion(spans[id(call['arguments']['openParen'])][1], name + ', '))
        raise_call = f'$error("%s",{name});'
    edits.append(Insertion(end, ' ' + callback + raise_call + ' end '))
    # No new named/local scope around %m and no change to source helper lifetime.
    # Selected source calls are non-suspending and nonrecursive; this private
    # lexical buffer cannot be overwritten by another live invocation.
    return edits, replaced, f'string {name};\n'


def effect_free_argument(node):
    """A callback may precede these reads, but never an effectful argument.

    Do not rely on constant folding: an expression carrying a constant can
    still invoke a helper, update a variable or terminate the simulation.
    """
    kind = node.get("kind")
    if kind in {"IntegerLiteral", "RealLiteral", "StringLiteral", "NamedValue"}:
        return True
    if kind == 'ConditionalOp':
        return (all(set(condition) == {'expr'} and effect_free_argument(condition['expr'])
                    for condition in node['conditions'])
                and effect_free_argument(node['left']) and effect_free_argument(node['right']))
    if kind == "UnaryOp" and node.get("op") not in {
            "Plus", "Minus", "BitwiseNot", "LogicalNot", "BitwiseAnd", "BitwiseOr",
            "BitwiseXor", "BitwiseNand", "BitwiseNor", "BitwiseXnor"}:
        return False
    if kind not in {"UnaryOp", "BinaryOp", "Conversion",
                    "ElementSelect", "RangeSelect", "Concatenation", "Replication"}:
        return False
    children = [value for key, value in node.items()
                if key not in {"type", "elementType"} and isinstance(value, (dict, list))]
    return all(effect_free_argument(child) for value in children
               for child in (value if isinstance(value, list) else [value]))


def assertion_function(callback, *, deferred=False):
    # Keep the original assertion and its action block. In particular, a
    # recovering assertion still continues, and a disabled assertion does not
    # evaluate its condition or this observer. X/Z fails an immediate assertion.
    # The deferred name is a compiler marker, not an ordinary condition call:
    # its inputs are captured at encounter; its two phases observe maturity
    # and actual action entry separately.
    # Production admission remains closed until that compiler/observer/query
    # path is qualified; generating this helper alone does not enable support.
    name = DEFERRED_ASSERT_FUNCTION if deferred else ASSERT_FUNCTION
    phase_argument = (", input int unsigned pass_failure, fail_failure, input bit mature"
                      if deferred else "")
    # A mature violation survives an earlier report's fatal action. Action
    # entry does not: it is recorded only when that action actually dispatches.
    failure = f"if (value !== 1'b1) {callback}(event_id, scope);"
    entry = f"""
    if (value !== 1'b1) begin
      if (fail_entry != {NO_EVENT}) {callback}(fail_entry, scope);
    end
    else if (pass_entry != {NO_EVENT}) {callback}(pass_entry, scope);
"""
    if deferred:
        entry += f"""
    if (value !== 1'b1) begin
      if (fail_failure != {NO_EVENT}) {callback}(fail_failure, scope);
    end
    else if (pass_failure != {NO_EVENT}) {callback}(pass_failure, scope);
"""
    observation = (f"if (mature) begin {failure} end else begin {entry} end"
                   if deferred else failure + entry)
    return f"""
function automatic logic {name}(
    input logic value, input int unsigned event_id, input bit observed, input int unsigned scope,
    input int unsigned pass_entry, fail_entry{phase_argument});
  if (observed) begin
{observation}
  end
  {name} = value;
endfunction
"""


def assertion_wrapper(event_id, *, observed="1'b1", scope="0",
                      pass_entry=NO_EVENT, fail_entry=NO_EVENT, deferred=False,
                      pass_failure=NO_EVENT, fail_failure=NO_EVENT):
    name = DEFERRED_ASSERT_FUNCTION if deferred else ASSERT_FUNCTION
    phase = f", {pass_failure}, {fail_failure}, 1'b1" if deferred else ""
    return (f"{name}(!(!(",
            f")), {event_id}, {observed}, {scope}, {pass_entry}, {fail_entry}{phase})")


def assertion_actions(node):
    """The two optional action statements, not the condition or grammar tokens."""
    action = node["action"]
    if "statement" in action:
        yield True, action["statement"]
    if "elseClause" in action:
        yield False, action["elseClause"]["clause"]
