"""Render a bound operation predicate on already-captured integral values.

No arithmetic from the candidate is repeated here. Comparisons use a lossless
signed carrier, so neither mixed signedness nor a large policy literal silently
narrows a sampled value. X/Z in a referenced value is reported as unreadable,
even if an unrelated Boolean clause would make the predicate true.
"""

from ..core.goal_predicate import COMPARISONS, GoalField

_OPERATORS = {"eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}


def predicate_expression(bound, names, *, carriers=None):
    fields = {value.name: value for value in bound.values}
    read = []

    def term(node):
        if type(node) is bool:
            return f"1'b{int(node)}", None
        if type(node) is int:
            width = node.bit_length() + 1
            return f"({'-' if node < 0 else ''}{width}'sd{abs(node)})", width
        if type(node) is GoalField:
            kind, name = fields[node.name], names[node.name]
            read.append(name)
            if kind.kind == "boolean":
                return name, None
            if carriers is not None and node.name in carriers:
                value, width = carriers[node.name]
                return (f"(({name} >> {node.bit}) & 1'b1)", None) if node.bit is not None else (value, width)
            if kind.width is None:
                raise ValueError("native RTL predicates require compiler-resolved finite types")
            if node.bit is not None:
                return f"(({name} >> {node.bit}) & 1'b1)", None
            value = f"$signed({name})" if kind.signed else f"$signed({{1'b0, {name}}})"
            return value, kind.width + int(not kind.signed)
        args = tuple(term(arg) for arg in node.args)
        if node.op in COMPARISONS:
            (left, lw), (right, rw) = args
            if lw is not None:
                width = (max(lw, rw) if type(lw) is int and type(rw) is int else
                         f"(({lw}) > ({rw}) ? ({lw}) : ({rw}))")
                left, right = f"{width}'({left})", f"{width}'({right})"
            return f"({left} {_OPERATORS[node.op]} {right})", None
        if node.op == "not":
            return f"(!({args[0][0]}))", None
        join = " && " if node.op == "and" else " || "
        return "(" + join.join(value for value, _ in args) + ")", None

    expression, _ = term(bound.rule.condition)
    readable = " && ".join(f"!$isunknown({name})" for name in dict.fromkeys(read)) or "1'b1"
    return readable, expression
