"""Bind the native operation declaration to SolverIR's sampled values."""

from ..core.goal_predicate import COMPARISONS, GoalField
from . import ir


def predicate_expression(bound, payload):
    values = {value.name: item for value, item in zip(bound.values, payload, strict=True)}

    def term(node):
        if type(node) in (int, bool):
            return ir.Lit(node)
        if type(node) is GoalField:
            value = values[node.name]
            return value if node.bit is None else ir.Op(
                "ne", ir.Op("and", ir.Op("shr", value, ir.Lit(node.bit)), ir.Lit(1)), ir.Lit(0))
        args = tuple(term(arg) for arg in node.args)
        if node.op in COMPARISONS:
            return ir.Op(node.op, *args)
        if node.op == "not":
            return ir.Op("lnot", args[0])
        result = args[0]
        for arg in args[1:]:
            result = ir.Op("land" if node.op == "and" else "lor", result, arg)
        return result

    return term(bound.rule.condition)
