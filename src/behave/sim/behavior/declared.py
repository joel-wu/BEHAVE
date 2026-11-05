"""Declare source failures independently of their IR producers.

The selected call closure owes every failure specified by the primitive registry,
plus subscript/pop index errors and assertion failures. A source-total operation
such as ``dict.get`` with a default owes no index error. Lowering must account for each declared goal with
either a producer or a checked discharge; a discharge does not delete the goal
from the catalogue. This independent declaration catches missing IR guards.
"""
import ast

from ..symbolic import primitives
from .source_bytes import BYTE_CARRIERS, is_from_bytes, is_to_bytes
from .source_calls import is_plain_named_call, is_pop_call
from .source_syntax import execution_children

#: Methods a model has that the lowering does not lower. Shared with
#: `admission.NOT_LOWERED` and `goals._Walker.NOT_LOWERED` for the same reason: `reset`
#: and `__init__` are executed, by constructing the model, so an operation in one of them
#: is performed by Python and owes no goal here.
NOT_LOWERED = ("reset", "__init__")

DIVZERO = "divzero"
INDEXERROR = "indexerror"
NEGATIVESHIFT = "negativeshift"
ASSERTIONERROR = "assertionerror"
ATTRIBUTEERROR = "attributeerror"
VALUEERROR = "valueerror"
OVERFLOWERROR = "overflowerror"

#: Analysis capacity exhaustion is not a source ``append`` failure. It belongs
#: to ``instance.analysis_limits``, not to the artifact's safety catalogue.

__all__ = [
    "ASSERTIONERROR",
    "ATTRIBUTEERROR",
    "DIVZERO",
    "INDEXERROR",
    "NEGATIVESHIFT",
    "VALUEERROR",
    "OVERFLOWERROR",
    "SafetySite",
    "declare_safety",
    "safety_goal_id",
    "unaccounted",
]


class SafetySite:
    """One designated source failure, and the goal it owes."""

    __slots__ = (
        "col", "end_col", "end_line", "file", "id", "kind", "line", "node",
    )

    def __init__(self, id, kind, line, col, file, node=None):
        self.id = id
        self.kind = kind
        self.line = line
        self.col = col
        self.end_line = getattr(node, "end_lineno", None)
        self.end_col = getattr(node, "end_col_offset", None)
        self.file = file
        #: The syntax the goal is owed for, so a caller can ask whether the lowering
        #: ever reached it. A site inside a loop that unrolls zero times, or in a
        #: helper nothing calls, is an operation no bounded execution performs.
        self.node = node

    def as_dict(self):
        return {"id": self.id, "kind": self.kind, "line": self.line,
                "col": self.col, "end_line": self.end_line,
                "end_col": self.end_col, "file": self.file}

    def __repr__(self):
        return f"{self.kind}@{self.line}:{self.col}"


def safety_goal_id(file, node, kind):
    """Stable identity of one partial source operation.

    A lowered library routine and the model may use the same line and column.
    The source file is therefore part of the occurrence identity, just as it is
    for structural goals.
    """
    if type(file) is not str or not file:
        raise TypeError("a safety goal needs its nonempty source file")
    if type(kind) is not str or not kind:
        raise TypeError("a safety goal needs its nonempty error kind")
    span = tuple(getattr(node, name, None) for name in (
        "lineno", "col_offset", "end_lineno", "end_col_offset"))
    if any(type(value) is not int or value < 0 for value in span):
        raise TypeError("a safety goal needs one complete source span")
    line, col, end_line, end_col = span
    return f"safety:{file}:{line}:{col}:{end_line}:{end_col}:{kind}"


def _scope_nodes(function):
    """Walk one function body without borrowing a nested scope's operations."""

    nested_scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    pending = list(reversed(function.body))
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, nested_scopes):
            continue
        pending.extend(reversed(execution_children(node)))


def declare_safety(tree, file="behavior.py", methods=None, *, file_of=None,
                   nodes_of=None, is_builtin=lambda _node, _name: False,
                   is_helper=lambda _node: False, state_fields=()):
    """Every safety goal the lowered code owes, by identifier.

    ``methods`` is the function definitions the lowering will lower. Passing them keeps
    this from having to answer which class and which method are being verified, which is
    `SourceModel`'s decision and must not be made twice. With nothing passed it takes
    every function except the constructors, which is what a whole-file walk means.
    Call partiality also needs ``is_builtin(node, name)`` from the lexical
    resolver; the spelling alone must not turn a user helper into a builtin.
    """
    if methods is None:
        methods = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name not in NOT_LOWERED]
    out = {}

    def declare(node, kind):
        source_file = file if file_of is None else file_of(node)
        key = safety_goal_id(source_file, node, kind)
        out.setdefault(key, SafetySite(key, kind, node.lineno,
                                      node.col_offset, source_file, node))

    def state_attribute(node):
        return (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name) and node.value.id == "self"
                and node.attr in state_fields)

    for fn in methods:
        for node in (nodes_of or _scope_nodes)(fn):
            kind = _kind_of(node, is_builtin)
            if kind is not None:
                declare(node, kind)
            if is_to_bytes(node) and not is_helper(node):
                declare(node, VALUEERROR)
                declare(node, OVERFLOWERROR)
            # Field reads are partial even if this construction proves them
            # total. Keep declaration source-owned; lowering may discharge.
            if state_attribute(node) and isinstance(node.ctx, ast.Load):
                declare(node, ATTRIBUTEERROR)
            if isinstance(node, ast.AugAssign) and state_attribute(node.target):
                declare(node.target, ATTRIBUTEERROR)
    return out


def _kind_of(node, is_builtin):
    """Which goal a node owes, or nothing.

    Binary partiality is read from the primitive registry; subscripts and
    ``pop`` can raise an index error. Slices clip their integer endpoints;
    the admitted step is proved nonzero, so these reads owe no index error.
    An append is not here for the reason recorded above.

    `x //= y` is the same operation as `x = x // y` and owes the same goal. The lowering
    desugars it into a `BinOp` carrying the statement's own position, so the identifier
    is the same either way -- and the audit found this by refusing a model that writes
    `tmp //= self.TAPS`, where a goal was produced that the declaration had nothing for.
    """
    if isinstance(node, ast.Assert):
        return ASSERTIONERROR
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in BYTE_CARRIERS and is_builtin(node, node.func.id)):
        return VALUEERROR
    if is_from_bytes(node, lambda name: is_builtin(node, name)):
        return VALUEERROR
    if (is_plain_named_call(node, "divmod", 2)
            and is_builtin(node, "divmod")):
        return DIVZERO
    if (is_plain_named_call(node, "pow", 3)
            and is_builtin(node, "pow")):
        return VALUEERROR
    if isinstance(node, (ast.BinOp, ast.AugAssign)):
        spec = primitives.source_primitive(
            primitives.SourceFamily.BINARY, type(node.op))
        if spec is not None and spec.source_error is not None:
            return spec.source_error.lower()
    if isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
        return INDEXERROR
    if is_pop_call(node):
        return INDEXERROR
    return None


def unaccounted(declared, produced, discharged):
    """Declared goals that were neither produced nor discharged, and the reverse.

    Both directions, because each catches a different fault. A declared goal in neither
    is a rule the frontend is missing, and it is the fault this module exists for. A
    produced goal that was never declared is an identifier minted somewhere the
    declaration cannot see, which makes the catalogue depend on the producer again by a
    longer route.
    """
    missing = sorted(g for g in declared
                     if g not in produced and g not in discharged)
    undeclared = sorted(g for g in produced if g not in declared)
    return missing, undeclared
