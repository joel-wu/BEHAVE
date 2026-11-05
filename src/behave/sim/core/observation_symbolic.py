"""Exact transaction predicates over mathematical integer/Boolean values.

Loaded by symbolic paths only. RTL callers decode their physical bit patterns;
Behavior callers may also supply SolverIR's signed mathematical bitvectors.
"""

import z3

from .goal_predicate import COMPARISONS, GoalField


def _compare(op, left, right, *, context=None):
    """Compare logical integers without narrowing either operand or literal.

    SolverIR and decoded RTL values are signed mathematical bitvectors. Extend
    them to a common width instead of introducing mixed Int/BV arithmetic. An
    actual unbounded Int caller retains the mathematical-integer encoding.
    """
    if z3.is_bool(left):
        return COMPARISONS[op](left, right)
    if z3.is_int(left) or z3.is_int(right):
        def integer(value):
            return z3.BV2Int(value, is_signed=True) if z3.is_bv(value) else value
        return COMPARISONS[op](integer(left), integer(right))
    width = max(value.size() if z3.is_bv(value) else value.bit_length() + 1
                for value in (left, right))
    context = next((value.ctx for value in (left, right) if z3.is_expr(value)), context)

    def vector(value):
        return (z3.SignExt(width - value.size(), value) if z3.is_bv(value)
                else z3.BitVecVal(value, width, ctx=context))

    return COMPARISONS[op](vector(left), vector(right))


def predicate_term(rule, endpoint, current, previous, *, context=None):
    if rule.consecutive and previous is None:
        return z3.BoolVal(False, ctx=context)
    readable, condition = condition_terms(rule.condition, endpoint, current, previous, context=context)
    return z3.And(readable, condition)


def condition_terms(condition, endpoint, current, previous=None, *, context=None):
    """Keep readability distinct for ordered, first-match event classifiers."""
    fields = {field.name: (index, field.type) for index, field in enumerate(endpoint.fields)}
    valid = []

    def compare(op, left, right):
        return _compare(op, left, right, context=context)

    def term(node):
        if type(node) is bool:
            return z3.BoolVal(node, ctx=context)
        if type(node) is int:
            return node
        if type(node) is GoalField:
            index, kind = fields[node.name]
            payload = previous if node.previous else current
            value = None if payload is None or index >= len(payload) else payload[index]
            boolean = kind.kind == "boolean"
            if boolean and z3.is_bool(value):
                return value
            if boolean and type(value) is bool:
                return z3.BoolVal(value, ctx=context)
            symbolic = z3.is_bv(value) or (not boolean and z3.is_int(value))
            if not symbolic and type(value) is not (bool if boolean else int):
                valid.append(z3.BoolVal(False, ctx=context))
                return (z3.BoolVal(False, ctx=context) if boolean or node.bit is not None else
                        z3.IntVal(0, ctx=context))
            low, high = (0, 1) if boolean else kind.carrier.bounds
            valid.append(z3.And(compare("ge", value, low), compare("le", value, high)))
            if boolean:
                return compare("eq", value, 1)
            if node.bit is not None:
                if z3.is_bv(value):
                    value = z3.SignExt(max(0, node.bit + 1 - value.size()), value)
                    return z3.Extract(node.bit, node.bit, value) == 1
                if type(value) is int:
                    return z3.BoolVal(bool((value >> node.bit) & 1), ctx=context)
                return (value / (1 << node.bit)) % 2 == 1
            return value
        args = tuple(term(arg) for arg in node.args)
        if node.op in COMPARISONS:
            return compare(node.op, *args)
        return {"and": z3.And, "or": z3.Or, "not": z3.Not}[node.op](*args)

    value = term(condition)
    return z3.And(z3.BoolVal(True, ctx=context), *valid), value


def logical_payload(endpoint, packed):
    """Decode RTL into signed logical bitvectors, extending unsigned values."""
    values = []
    for field, value in zip(endpoint.fields, packed):
        kind = field.type
        if kind.kind == "boolean":
            values.append(value if z3.is_bool(value) else value == 1)
        elif kind.kind == "integer":
            if z3.is_bool(value):
                value = z3.If(value, z3.BitVecVal(1, 1, ctx=value.ctx), z3.BitVecVal(0, 1, ctx=value.ctx))
            values.append(value if kind.carrier.signed else z3.ZeroExt(1, value))
        else:
            values.append(None)
    return tuple(values)


def logical_zero(kind, *, context=None):
    """Initial observation storage has the same sort as a decoded payload."""
    return (z3.BoolVal(False, ctx=context) if kind.kind == "boolean" else
            z3.BitVecVal(0, kind.carrier.width + int(not kind.carrier.signed), ctx=context))


def logical_scalar(value, *, context=None):
    if type(value) is bool:
        return z3.BoolVal(value, ctx=context)
    if type(value) is int:
        return z3.IntVal(value, ctx=context)
    return z3.BV2Int(value, is_signed=True) if z3.is_bv(value) else value


def history_image(state):
    """Expose logical values for native comparison and complete boundary pins."""
    image = {"observations": {name: {"seen": previous["seen"], "payload": tuple(
        z3.BV2Int(value, is_signed=True) if z3.is_bv(value) else value
        for value in previous["payload"])} for name, previous in state["observations"].items()},
        "automata": state["automata"]}
    if "latency" in state:
        image["latency"] = state["latency"]
    return image
