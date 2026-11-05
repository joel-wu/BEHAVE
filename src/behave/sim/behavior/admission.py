"""Admission of a behavior model into the certified subset.

A goal set is only as good as the guarantee that nothing was missed while
building it. The enumeration in :mod:`behave.sim.behavior.goals` walks a fixed
list of syntactic forms, so a construct it has no rule for would contribute no
goal and leave no trace of having been skipped. Admission closes that hole:
every node of the model is visited, and anything the enumeration does not
handle is a rejection rather than a silent omission.

Rejection is therefore not a statement that a construct is unsupportable. It
records that no rule exists for it yet, which is what keeps the goal catalog
honest about its own coverage of the source.

A list of what to reject cannot do that, and this was one. Thirteen forms were
named and everything else passed, so ten constructs the lowering refuses were
admitted and refused a stage later: ``continue``, ``:=``, a lambda, a generator
expression, ``*args``, an augmented assignment to a subscript, a chained
comparison, an f-string, ``del`` and a nested class. Worse than the noise is
what the shape implies: a form nobody thought of is admitted by default, which
is the opposite of the guarantee this module exists to give.

So the grammar is written down instead, and a node whose type no rule names is
a rejection. Adding a form to the subset means adding it here, which is a
decision someone makes rather than one the language makes for us.
"""

from __future__ import annotations

import ast

from . import budgets, source_intrinsics
from .source_calls import is_plain_pop_call, record_update_items
from .source_syntax import future_annotations_import, local_annotations, unevaluated_annotations

__all__ = ["Rejection", "admitted", "check"]

#: The grammar. A node whose type is not in one of these sets is rejected, so
#: what is admitted is a decision written down rather than what happens to be
#: left over after a list of refusals.
#:
#: These are the forms the lowering has a rule for. When the two drift apart a
#: model is either admitted and then refused, which wastes a stage and confuses
#: the report, or refused while the lowering would have handled it. The test
#: that keeps them together reads both.
_STATEMENTS = {
    "Module", "ClassDef", "FunctionDef", "Assign", "AnnAssign", "AugAssign", "If", "For",
    "While", "Break", "Continue", "Return", "Pass", "Expr", "Assert", "ImportFrom", "Import",
    "arguments", "arg", "alias", "keyword",
}

#: Helper calls have named binding and fixed positional expansion. Their
#: exact signature and lexical target are checked by the shared source binder;
#: this grammar gate only checks the position in which these nodes occur.
_EXPRESSIONS = {
    "Name", "Constant", "Attribute", "BinOp", "UnaryOp", "BoolOp", "Compare",
    "Subscript", "IfExp", "Call", "List", "Tuple", "Dict", "ListComp",
    "DictComp", "GeneratorExp", "JoinedStr", "FormattedValue", "Slice", "Lambda",
    "comprehension", "Starred",
}

#: Operators and contexts. They carry no rule of their own but a node of an
#: operator type nobody handles would otherwise reach the lowering, so they are
#: enumerated with the rest.
#:
#: `UAdd` is absent, and its absence is the shape of what a whitelist of node types
#: cannot say by itself. `+a` has no rule in the lowering, `_UNARYOPS` covering negation,
#: inversion and `not` and nothing else, so admitting the type admitted a form refused a
#: stage later. No model in the population writes it, so this is the cheap end of the
#: same gap that `Dict` is the expensive end of.
_OPERATORS = {
    "Add", "Sub", "Mult", "FloorDiv", "Mod", "LShift", "RShift",
    "BitAnd", "BitOr", "BitXor", "USub", "Invert", "Not",
    "And", "Or", "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "In", "NotIn",
    "Load", "Store",
}

ADMITTED = _STATEMENTS | _EXPRESSIONS | _OPERATORS

#: Why a form a reader is likely to reach for is not in the grammar. Only for
#: the message: a form absent from this table and from ``ADMITTED`` is still
#: rejected, which is the whole point of the shape.
_WHY = {
    "Match": "pattern matching has no branch rule",
    "Try": "exception handling is not modelled",
    "Raise": "raising is not modelled",
    "Yield": "generators are not a bounded iterable",
    "YieldFrom": "generators are not a bounded iterable",
    "Await": "coroutines are not modelled",
    "AsyncFor": "coroutines are not modelled",
    "AsyncWith": "coroutines are not modelled",
    "AsyncFunctionDef": "coroutines are not modelled",
    "With": "context managers are not modelled",
    "Global": "module-level mutable state is not modelled",
    "Nonlocal": "closure mutation is not modelled",
    "NamedExpr": "an assignment inside an expression has no evaluation-order "
                 "rule here",
    "Lambda": "an anonymous function has no body to inline",
    "GeneratorExp": "a generator is not a bounded iterable",
    "SetComp": "sets are not modelled",
    "DictComp": "a comprehension over a mapping is not modelled",
    "Set": "sets are not modelled",
    "JoinedStr": "string formatting is not modelled",
    "FormattedValue": "string formatting is not modelled",
    "Delete": "deleting a binding has no rule",
    "Pow": "exponentiation has no bounded-width rule",
    "Div": "true division produces a rational, not a bitvector",
    "MatMult": "matrix multiplication is not modelled",
    "In": "membership has no rule",
    "NotIn": "membership has no rule",
    "Is": "identity is not modelled",
    "IsNot": "identity is not modelled",
}

#: Names the instrumentation binds in the model's namespace. A model that binds
#: one of them is writing its own coverage report, so the source is refused
#: rather than the collision being resolved: which of the two won would be a
#: property of the order things were bound in.
_RESERVED = {"_behave_hit", "_behave_loop"}

# Callables an admitted loop may iterate. Anything else is data whose length
# the range analysis cannot bound.
_ITER_CALLS = {"range", "enumerate", "reversed", "zip", "divmod"}


class Rejection:
    """One reason a model is not admitted."""

    __slots__ = ("col", "construct", "line", "reason")

    def __init__(self, line, col, construct, reason):
        self.line = line
        self.col = col
        self.construct = construct
        self.reason = reason

    def as_dict(self):
        return {"line": self.line, "col": self.col,
                "construct": self.construct, "reason": self.reason}

    def __repr__(self):
        return f"{self.construct} at {self.line}:{self.col}: {self.reason}"


def check(source, file="behavior.py"):
    """Every reason ``source`` is not admitted, in source order.

    Every node is visited and every node must be named by the grammar. A form
    with no rule is a rejection whether or not anyone anticipated it, which is
    the property a list of refusals cannot have.

    A node type is not always enough, and that is the whole reason this function is
    longer than a set membership. `Call` covers a call to a helper and a call to
    `sorted`; `Assign` covers one target and three; `For` covers a name and a tuple.
    So the shapes are stated too, and which shapes were missing was found by walking the
    grammar rather than by thinking of them: one minimal model per admitted node type,
    lowered by the production path, which turned up nine where hand-listing had found
    six.

    A rule belongs here when a position in the tree settles it and nowhere else. A call to
    `list`, `abs`, `bit_count` or `sorted` is 22 of the models that do not lower and is
    settled by the name being neither a helper nor an import, so it moves here from a
    message about an expression. A dict is 24 more and is not settled that way at all,
    because whether it is an answer or a table depends on where its value flows; that
    story is above `_constructed_bodies`, and it stays with the stage that follows values.

    `+a` accounts for no models today and is refused anyway: a form nobody
    writes today is a form the next problem set writes.

    Refusing is not the end state for most of them. Y6 settles that a rule which
    generalises gets added; until it exists the refusal names the form and the line, which
    is what an author can act on where "expression Dict at line 41" is not.
    """
    if isinstance(source, str) and len(source) > budgets.SOURCE_TEXT_CHARS:
        return [Rejection(
            0, 0, "SourceText",
            f"the source has {len(source)} characters, over the reported "
            f"capability of {budgets.SOURCE_TEXT_CHARS}")]
    tree = ast.parse(source)
    parents = {id(child): parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}

    def output_key_format(node):
        """Whether a formatted string occurs in a DictComp record key."""
        current = node
        while current is not None and not isinstance(current, ast.DictComp):
            current = parents.get(id(current))
        return (isinstance(current, ast.DictComp)
                and any(candidate is node for candidate in ast.walk(current.key)))

    def consumed_generator(node):
        """Whether a finite consumer owns the generator's whole lifetime."""
        parent = parents.get(id(node))
        return (isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Name)
                and parent.func.id in {"sum", "all", "any", "list", "tuple"}
                and parent.args and parent.args[0] is node)

    def sorted_call(node):
        """Whether this is the finite call shape modelled for ``sorted``."""
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "sorted"
                and len(node.args) == 1
                and all(keyword.arg in {"key", "reverse"}
                        for keyword in node.keywords)
                and len({keyword.arg for keyword in node.keywords})
                    == len(node.keywords))

    def sorted_keyword_or_lambda(node):
        parent = parents.get(id(node))
        if isinstance(node, ast.keyword):
            return sorted_call(parent)
        return (isinstance(node, ast.Lambda)
                and isinstance(parent, ast.keyword)
                and parent.arg == "key"
                and parent.value is node
                and sorted_call(parents.get(id(parent))))

    helper_names = {node.name for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)}
    from .extract import _approved_primitives
    helper_names.update(_approved_primitives(tree))

    def helper_call(node):
        # A syntax inventory is not permission to resolve a shadowed function:
        # planning and lowering still require the actual lexical helper target.
        return (isinstance(node, ast.Call)
                and (getattr(node.func, "id", None)
                     or getattr(node.func, "attr", None)) in helper_names)

    nested = _nested_classes(tree)
    constructed = _constructed_bodies(tree)
    unevaluated = {node for annotation in unevaluated_annotations(tree) for node in ast.walk(annotation)}
    out = []
    out.extend(_check_calls(tree, constructed | unevaluated))
    for node in _visit(tree):
        if node in unevaluated:
            continue
        name = type(node).__name__
        if name not in ADMITTED:
            out.append(_at(node, name,
                           _WHY.get(name, f"{name} is not in the admitted "
                                          f"grammar")))
            continue
        if isinstance(node, ast.GeneratorExp) and not consumed_generator(node):
            out.append(_at(
                node, "GeneratorExp",
                "a generator is represented only when builtin sum/all/any/list/tuple consumes "
                "it immediately"))
        if isinstance(node, (ast.keyword, ast.Lambda)) \
                and not sorted_keyword_or_lambda(node) \
                and not (isinstance(node, ast.keyword)
                         and (helper_call(parents.get(id(node)))
                              or record_update_items(parents.get(id(node))) is not None
                              or (node.arg is not None and isinstance(
                                  parents.get(id(node)), ast.Call)
                                  and isinstance(parents[id(node)].func, ast.Name)
                                  and parents[id(node)].func.id == "dict"))):
            out.append(_at(
                node, name,
                "keywords require a helper, fixed record operation or finite sorted builtin; "
                "lambdas require the finite sorted key"))
        if (isinstance(node, ast.Starred) and not isinstance(node.ctx, ast.Store)
                and not helper_call(parents.get(id(node)))):
            out.append(_at(node, name, "starred values require a fixed helper argument"))
        if isinstance(node, (ast.JoinedStr, ast.FormattedValue)) \
                and not output_key_format(node):
            out.append(_at(
                node, name,
                "string formatting is represented only for source-fixed "
                "names in a returned output mapping"))
        if isinstance(node, ast.AugAssign) and not isinstance(
                node.target, (ast.Name, ast.Attribute)):
            out.append(_at(node, "AugAssign",
                           "a compound assignment to anything but a name or a "
                           "field has no rule"))
        # Shapes a node type admits and the lowering has no rule for. Each was refused
        # from inside the lowering before, which is the wrong stage: what the subset
        # contains is this module's question, and a refusal that names a form and a line
        # is what an author can act on where "expression Dict at line 41" is not.
        #
        # These questions apply only to code the lowering sees. `reset` and
        # `__init__` are run, so a form in one of them needs no lowering rule.
        if node in constructed:
            pass
        elif (isinstance(node, ast.Assign) and len(node.targets) > 1
              and not all(isinstance(target, (ast.Name, ast.Attribute))
                          for target in node.targets)):
            out.append(_at(node, "Assign",
                           "chained assignment has a compound target; only "
                           "plain names and model fields have an ordered rule"))
        if node in nested:
            out.append(_at(node, "ClassDef",
                           "a class declared inside another scope has no rule"))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) \
                and node.id in _RESERVED:
            out.append(_at(node, "Name",
                           f"{node.id} is the name the instrumentation binds "
                           f"its hook to, and a model that rebinds it decides "
                           f"what its own coverage says"))
        if isinstance(node, ast.ImportFrom):
            if future_annotations_import(node):
                continue
            if node.level or node.module not in \
                    source_intrinsics.APPROVED_MODULES:
                out.append(_at(node, "ImportFrom",
                               f"{node.module} is not an approved absolute "
                               "library"))
            elif node.module == source_intrinsics.MODULE:
                if not source_intrinsics.top_level_import(node, tree):
                    out.append(_at(
                        node, "ImportFrom",
                        "Behavior intrinsics must be imported directly at "
                        "module scope"))
                for alias in node.names:
                    if alias.name not in source_intrinsics.names():
                        out.append(_at(
                            node, "ImportFrom",
                            f"{alias.name} is not a registered Behavior "
                            "intrinsic"))
                    binding = alias.asname or alias.name
                    if binding in _RESERVED or binding == "__builtins__":
                        out.append(_at(
                            node, "ImportFrom",
                            f"{binding} is reserved by the Behavior runtime"))
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == source_intrinsics.MODULE:
                    out.append(_at(
                        node, "Import",
                        "Behavior intrinsics use named from-imports, not a "
                        "module object"))
                elif alias.name not in source_intrinsics.APPROVED_MODULES:
                    out.append(_at(node, "Import",
                                   f"{alias.name} is not an approved library"))
        if isinstance(node, ast.For):
            out.extend(_check_iterable(node.iter))
        if isinstance(node, ast.comprehension):
            out.extend(_check_iterable(node.iter))
    out.sort(key=lambda r: (r.line, r.col))
    return out


def admitted(source, file="behavior.py"):
    """Whether ``source`` is admitted."""
    return not check(source, file)


def _visit(tree):
    """Every node, but not the inside of one already rejected.

    A `try` rejects, and so would its handler, its body and everything they
    contain. The first refusal is the one a reader acts on and the rest are its
    consequences, so the walk stops where it has already found something to
    say.
    """
    yield tree
    for child in ast.iter_child_nodes(tree):
        if type(child).__name__ not in ADMITTED:
            yield child
            continue
        yield from _visit(child)


def _nested_classes(tree):
    """Every class that is not declared at the top of the module.

    The model is a module-level class. One declared inside a method or inside
    another class is a scope the lowering has no rule for, and the node type
    alone cannot say which is which.
    """
    top = {node for node in getattr(tree, "body", [])
           if isinstance(node, ast.ClassDef)}
    return {node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node not in top}


def _at(node, construct, reason):
    return Rejection(getattr(node, "lineno", 0), getattr(node, "col_offset", 0),
                     construct, reason)


#: Methods a model has and the lowering does not lower: `reset` and `__init__` say what
#: the state holds before the first step and are *executed*, by constructing the model,
#: rather than turned into commands. So a form in one of them is not a form the lowering
#: needs a rule for, and checking them against the lowering's grammar refuses models for
#: writing ordinary Python where ordinary Python is what runs.
#:
#: `goals._Walker.NOT_LOWERED` is the same list for the same reason, and the two should
#: stay together: a method the enumeration skips is a method the lowering never sees.
NOT_LOWERED = ("reset", "__init__")


def _constructed_bodies(tree):
    """The nodes that become commands: everything except `reset` and `__init__`.

    Measured cost of not making this distinction: `bit_length` is called in eleven
    models' `reset` to compute a width, `abs` in others, and checking those against the
    lowering's callable names refused eleven models that lower perfectly well. The
    constructor is run, not lowered.
    """
    skip = set()
    for annotation in local_annotations(tree):
        skip.update(ast.walk(annotation))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in NOT_LOWERED:
            skip.update(ast.walk(node))
    return skip


#: Not a rule here: a dict inside the step.
#:
#: It was one, for three measurements. Whether a dict has a rule depends on where its
#: value flows, and admission cannot see that without redoing the frontend's work:
#: refusing every dict refused all 836 models, because a `return {...}` is how every one
#: of them answers; exempting the mapping at a `return` still cost 25, because a dict
#: bound at module level may satisfy `ScopeResolver`'s closed constant-table
#: contract; exempting
#: those still left 26 wrong, because `return [{...}] if c else [...]` is an answer too,
#: and so is a mapping built in a local. Each exemption was the previous rule's shape
#: read off the models it had just broken.
#:
#: So the shape rules above are the ones a position in the tree settles, and this
#: one is not. It stays with the stage that follows the values. The 24 models it would
#: have caught are a coverage item under Y6, and the rule to add there is the
#: generalisation of the constant table, not a rule for dicts.


#: What a call may name besides a helper the model defines and a primitive it imports.
#: The lowering has a rule for each and for nothing else.
_CALLABLE = {
    "range", "enumerate", "reversed", "zip", "len", "append", "extend", "get",
    "abs", "bool", "int", "list", "tuple", "dict", "sum", "all", "any", "min", "max", "sorted", "bit_count", "bit_length",
    "divmod", "pow",
}


def calls_this_build_will_not_answer(source):
    """Names a model calls that will not exist when it runs. `[]` where there are none.

    A **runtime** question, asked statically, and narrower than `check`. The model runs
    with `instrumented._ALLOWED_BUILTINS` and nothing else, so a call to anything outside
    that set, the approved library and the model's own helpers raises `NameError` on the
    slot that reaches it. One design in this population calls `locals()`, and what came
    back was `reference-failed: NameError` per stimulus -- a run refused four times over
    for a reason a reader cannot act on, when the source says it before anything runs.

    Not `check`, because `check` is about the subset the **lowering** can model: it
    refuses f-strings, which do not stop a model from replaying. A reference is
    not lowered, and refusing one for a construct the
    analysis cannot take would shrink what can be scored for a reason that is not about
    scoring.
    """
    from .extract import _approved_primitives
    from .instrumented import _ALLOWED_BUILTINS

    if isinstance(source, str) and len(source) > budgets.SOURCE_TEXT_CHARS:
        return [(f"the source has {len(source)} characters, over the reported "
                 f"capability of {budgets.SOURCE_TEXT_CHARS}")]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"the source does not parse: {exc.msg} at line {exc.lineno}"]
    known = (set(_CALLABLE) | set(_ALLOWED_BUILTINS)
             | set(_approved_primitives(tree))
             | set(source_intrinsics.declared_bindings(tree)))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module in source_intrinsics.CONCRETE_MODULES:
            known.update(alias.asname or alias.name for alias in source_intrinsics.concrete_import_aliases(node))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            known.add(node.name)
        elif isinstance(node, ast.Assign):
            known.update(t.id for t in node.targets if isinstance(t, ast.Name))
    out = []
    unevaluated = {node for annotation in unevaluated_annotations(tree) for node in ast.walk(annotation)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node in unevaluated:
            continue
        name = getattr(node.func, "id", None)
        if name is not None and name not in known:
            out.append(f"line {node.lineno}: {name}() is neither a helper this model "
                       f"defines, a routine of the approved library, nor a builtin this "
                       f"build provides")
    # This is a set of capability diagnostics, not an occurrence log. Several
    # calls on one physical line can produce the same message; both the source
    # judge and the executing worker publish the same canonical fact list.
    return sorted(set(out))


def _check_calls(tree, constructed=frozenset()):
    """Refuse a call to a name the lowering cannot resolve, in the code it lowers.

    The lowering inlines a model helper or an ``fp_lib`` routine, maps an
    explicitly imported Behavior intrinsic through the primitive registry, and
    has a rule for a short list of builtins. Anything else it refuses, from
    inside itself, with a message about a call:
    `sorted`, `list`, `abs`, `bit_count` and `get` are 21 of the 199 models that do not
    lower.

    Asked here because this is where what the subset contains is decided, and answerable
    here because the helpers and the imports are in the source: a name is resolvable when
    the model defines a function of that name or imported it from the approved library.
    `abs`, `list` and `bit_count` are expressible in the core and Y6 says a rule that
    generalises gets added; `sorted` is a sorting network at a fixed length and is real
    work. Until then the refusal names the call and the line.

    Only in the code the lowering sees. Eleven models compute a width in `reset` with
    `(n - 1).bit_length()`, which is executed and never lowered; asking the lowering's
    question of the constructor refused all eleven, and the population said `ok 612`
    where it had said 637.
    """
    from .extract import _approved_primitives

    known = (set(_CALLABLE) | set(_approved_primitives(tree))
             | set(source_intrinsics.declared_bindings(tree)))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            known.add(node.name)
    # This pass checks source shape only, as for the other builtin spellings.
    # The shared planner and lowerer separately require lexical ownership.
    counted_calls = set()
    for node in ast.walk(tree):
        if source_intrinsics.binary_digit_count(node, is_builtin=lambda name: True) is not None:
            counted_calls.update((node, node.func.value))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node in constructed or node in counted_calls:
            continue
        if is_plain_pop_call(node) or record_update_items(node) is not None:
            # Receiver type, bounds and IndexError belong to the shared
            # sequence rules; this shape gate must not reject that operation.
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name is None:
            out.append(_at(node, "Call",
                           "a call to an expression rather than a name has no rule"))
        elif name not in known:
            out.append(_at(node, "Call",
                           f"a call to {name} has no rule: it is neither a helper this "
                           f"model defines nor a routine of the approved library"))
    return out


def _check_iterable(node):
    """An admitted loop iterates a form whose length has a static bound.

    A call is admitted only when it is one of the length-preserving builtins;
    an arbitrary call may return any iterator, including one that never ends,
    and its trip count is then outside the bound the unrolling needs.
    """
    if isinstance(node, ast.Call):
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name not in _ITER_CALLS:
            return [_at(node, "iterable-call",
                        f"iterating {name or 'a call'}() has no bound rule")]
    return []
