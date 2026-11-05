"""Exact query binding for the native operation-predicate declaration."""

import z3

from .goal_predicate import COMPARISONS, GoalField
from .observation_symbolic import _compare


def hit_term(bound, completed, payload):
    """Gate by the operation's completion edge, not merely operand equality.

    Finite RTL payloads are the physical vectors of their declared types;
    Python payloads are mathematical Ints or SolverIR's signed integer vectors.
    No source operation is reconstructed here.
    """
    fields = {value.name: (value, item) for value, item in zip(bound.values, payload, strict=True)}

    def term(node):
        if type(node) is bool:
            return z3.BoolVal(node)
        if type(node) is int:
            return node
        if type(node) is GoalField:
            kind, value = fields[node.name]
            if kind.kind == "boolean":
                return z3.BoolVal(value) if type(value) is bool else value
            if z3.is_bv(value) and kind.width is not None and not kind.signed:
                value = z3.ZeroExt(1, value)
            if node.bit is not None:
                if z3.is_bv(value):
                    value = z3.SignExt(max(0, node.bit + 1 - value.size()), value)
                    return z3.Extract(node.bit, node.bit, value) == 1
                if type(value) is int:
                    return z3.BoolVal(bool((value >> node.bit) & 1))
                return (value / (1 << node.bit)) % 2 == 1
            return value
        args = tuple(term(arg) for arg in node.args)
        if node.op in COMPARISONS:
            return _compare(node.op, *args)
        return {"and": z3.And, "or": z3.Or, "not": z3.Not}[node.op](*args)

    return z3.And(completed, term(bound.rule.condition))
