"""Observe a completed source operation without evaluating either operand twice.

This is a native producer, not a goal policy. SourcePlan owns the operation
occurrences; a consumer can bind predicates to their actual operands/results.
Failed operations emit no completion. The operation remains in the compiled
source, with its original span, so failure attribution uses the original site.
Scalar operands are immutable values. Opaque/mutable objects are passed through
without copying; this primitive does not declare numeric predicates for them or
claim to snapshot their contents before an overloaded operator changes them.

Only ordinary binary/unary expressions are handled here. Augmented assignment
has separate in-place and lvalue semantics; chained comparisons short-circuit.
Neither is silently rewritten as a binary expression.
"""

import ast
from dataclasses import dataclass, field

from ..core.catalogue import OccurrenceKey
from ..core.operation_goal import BoundOperationGoal

OPERATORS = {
    "Add": "add", "Sub": "subtract", "Mult": "multiply", "Div": "divide",
    "FloorDiv": "floor-divide", "Mod": "remainder", "Pow": "power",
    "LShift": "shift-left", "RShift": "shift-right", "BitAnd": "bit-and",
    "BitOr": "bit-or", "BitXor": "bit-xor", "UAdd": "positive", "USub": "negative",
    "Invert": "bit-not", "Not": "logical-not",
}


@dataclass(frozen=True, order=True, slots=True)
class OperationSite:
    file: str
    line: int
    col: int
    end_line: int
    end_col: int
    operator: str

    @classmethod
    def of(cls, node, file):
        return cls(file, node.lineno, node.col_offset, node.end_lineno,
                   node.end_col_offset, type(node.op).__name__)


@dataclass(frozen=True, slots=True)
class SourceOperationGoal:
    """One typed predicate at a source-plan occurrence, not a guessed name."""

    site: OperationSite
    predicate: BoundOperationGoal
    typed_variant: bool = False
    id: str = field(init=False)

    def __post_init__(self):
        site = self.site
        object.__setattr__(self, "id", (
            f"operation:{site.file}:{site.line}:{site.col}:"
            f"{site.end_line}:{site.end_col}:{site.operator}:{self.predicate.rule.name}"))

    def declaration(self):
        site = self.site
        return self.predicate.declaration(self.id, provider="python-structural", provenance={
            "file": site.file, "line": site.line, "col": site.col,
            "end_line": site.end_line, "end_col": site.end_col, "operator": site.operator,
            "typed_variant": self.typed_variant})

    def accepts(self, payload):
        return not self.typed_variant or all(type(item) is (bool if value.kind == "boolean" else int)
                                             for item, value in zip(payload, self.predicate.values, strict=True))

    def occurrence(self):
        site = self.site
        return OccurrenceKey("python-structural",
                             (site.file, site.line, site.col, site.end_line, site.end_col,
                              site.operator, "source"), f"operation:{self.predicate.rule.name}")


def declare_operations(functions, nodes_of, file):
    """Inventory the selected executable closure, not annotations or dead helpers."""
    return tuple(sorted({
        OperationSite.of(node, file)
        for function in functions for node in nodes_of(function)
        if isinstance(node, (ast.BinOp, ast.UnaryOp))
    }))


def instrument_operations(tree, sites):
    """Return the rewritten tree and the hygienic ``(index, operands, result)`` hook.

    The hook must return the unchanged result. Operand expressions stay in their
    original scope and execute as call arguments, left to right. A generated
    per-site function holds their values and executes the original operator once.
    Functions are installed once, not allocated for each dynamic operation.
    Unlike temporary assignment expressions this also works in a comprehension
    iterable, where Python disallows walrus expressions.
    """
    names = {
        item for node in ast.walk(tree) for _field, value in ast.iter_fields(node)
        for item in (value if isinstance(value, list) else (value,))
        if isinstance(item, str)
    }

    def fresh(name):
        while name in names:
            name += "_"
        names.add(name)
        return name

    hook = fresh("_behave_operation")
    left, right = fresh("_behave_left"), fresh("_behave_right")
    # File identity was fixed by the source plan before this rewrite. Coordinates
    # include the complete expression, so nested/same-line operations stay apart.
    by_span = {(site.line, site.col, site.end_line, site.end_col, site.operator): i
               for i, site in enumerate(sites)}
    emitted = set()
    definitions = []

    class Capture(ast.NodeTransformer):
        def operation(self, node):
            key = (node.lineno, node.col_offset, node.end_lineno,
                   node.end_col_offset, type(node.op).__name__)
            index = by_span.get(key)
            node = self.generic_visit(node)
            if index is None:
                return node
            emitted.add(index)
            binary = isinstance(node, ast.BinOp)
            operands = [node.left, node.right] if binary else [node.operand]
            parameters = [left, right] if binary else [left]
            values = [ast.Name(id=name, ctx=ast.Load()) for name in parameters]
            operation = (ast.BinOp(left=values[0], op=node.op, right=values[1])
                         if binary else ast.UnaryOp(op=node.op, operand=values[0]))
            ast.copy_location(operation, node)
            report = ast.Call(
                func=ast.Name(id=hook, ctx=ast.Load()),
                args=[ast.Constant(index), ast.Tuple(elts=values, ctx=ast.Load()),
                      operation], keywords=[])
            name = fresh(f"_behave_operation_{index}")
            capture = ast.FunctionDef(
                name=name,
                args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=name) for name in parameters],
                                   vararg=None, kwonlyargs=[], kw_defaults=[],
                                   kwarg=None, defaults=[]),
                body=[ast.Return(value=report)], decorator_list=[], returns=None)
            definitions.append(ast.copy_location(capture, node))
            return ast.copy_location(ast.Call(func=ast.Name(id=name, ctx=ast.Load()),
                                              args=operands, keywords=[]), node)

        visit_BinOp = operation
        visit_UnaryOp = operation

    rewritten = Capture().visit(tree)
    if emitted != set(range(len(sites))):
        raise ValueError("operation instrumentation omitted a declared source occurrence")
    # Keep the module docstring and future declarations in their required initial
    # positions. Definitions themselves do not execute any source operation.
    prefix = 0
    for statement in rewritten.body:
        if ((isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
             and isinstance(statement.value.value, str))
                or (isinstance(statement, ast.ImportFrom) and statement.module == "__future__")):
            prefix += 1
        else:
            break
    rewritten.body[prefix:prefix] = definitions
    ast.fix_missing_locations(rewritten)
    return rewritten, hook
