"""Typed native capture for an eager integral source operation.

This producer consumes Slang's contextual operand/result types. Copying only
the result width would be wrong, e.g. for widening arithmetic, mixed signedness
or arithmetic shifts. Each operand is evaluated once at the original callsite;
the generated automatic function performs the operation once and reports its
values before returning the same four-state result.

Lazy operators, assignments and increment/decrement need different producers.
This module does not declare goals or make a search-capability claim for them.
"""

from dataclasses import dataclass

from ..core.refusal import Refusal, Refused
from .source_integral import integral_value_facts

_BINARY = {
    "Add": "+", "Subtract": "-", "Multiply": "*", "Divide": "/", "Mod": "%",
    "Power": "**", "BinaryAnd": "&", "BinaryOr": "|", "BinaryXor": "^",
    "BinaryXnor": "~^", "LogicalShiftLeft": "<<", "LogicalShiftRight": ">>",
    "ArithmeticShiftLeft": "<<<", "ArithmeticShiftRight": ">>>",
    "Equality": "==", "Inequality": "!=", "CaseEquality": "===",
    "CaseInequality": "!==", "LessThan": "<", "LessThanEqual": "<=",
    "GreaterThan": ">", "GreaterThanEqual": ">=",
}
_UNARY = {
    "Plus": "+", "Minus": "-", "BitwiseNot": "~", "LogicalNot": "!",
    "BitwiseAnd": "&", "BitwiseNand": "~&", "BitwiseOr": "|",
    "BitwiseNor": "~|", "BitwiseXor": "^", "BitwiseXnor": "~^",
}


@dataclass(frozen=True, slots=True)
class CaptureType:
    """Scalar packed value at an operation, not the source storage layout."""

    width: int
    signed: bool
    four_state: bool = True


def _capture_type(node, symbols):
    return CaptureType(*integral_value_facts(node, symbols))


def _hdl_type(value: CaptureType, width=None) -> str:
    # Four-state values stay four-state; declared two-state conversions also
    # keep their meaning. Neither choice is inferred from observed test inputs.
    return (f"{'logic' if value.four_state else 'bit'} "
            f"{'signed' if value.signed else 'unsigned'} "
            f"[{value.width - 1 if width is None else '(' + width + ')-1'}:0]")


@dataclass(frozen=True, slots=True)
class OperationCapture:
    operator: str
    operands: tuple[CaptureType, ...]
    result: CaptureType

    def goal_values(self):
        """The actual captured types, shared by declarations and queries."""
        from ..core.operation_goal import OperationValue

        return tuple(OperationValue(name, "boolean") if name == "result" and self.operator == "!"
                     else OperationValue(name, width=value.width, signed=value.signed)
                     for name, value in (
                         *((f"operand{i}", value) for i, value in enumerate(self.operands)),
                         ("result", self.result)))

    def predicate_callback(self, name, goals, *, hit, unreadable,
                           observer_parameters=(), observed="1'b1"):
        """Emit named hits through the existing sink after operation completion.

        ``goals`` holds (numeric event index, OperationGoal) pairs from a source
        plan. Generated sink names and indices belong to the planner, not the
        candidate. Unknown values have a distinct sink, never a guessed hit.
        """
        from .source_operation_predicates import predicate_expression

        values = self.goal_values()
        names = {value.name: f"v{i}" for i, value in enumerate(values[:-1])}
        names["result"] = "result"
        statements = []
        for index, rule in goals:
            bound = rule.bind(values)
            readable, predicate = predicate_expression(bound, names)
            statements.append(
                f"if (!({readable})) {unreadable}({index}, scope);\n"
                f"else if ({predicate}) {hit}({index}, scope);")
        parameters = [f"input {_hdl_type(value)} v{i}" for i, value in enumerate(self.operands)]
        parameters.extend((f"input {_hdl_type(self.result)} result", "input int unsigned scope"))
        parameters.extend(f"input {kind} {parameter}" for parameter, kind in observer_parameters)
        return (f"function automatic void {name}({', '.join(parameters)});\n"
                + f"if ({observed}) begin\n" + "\n".join(statements) + "\nend\nendfunction\n")

    @classmethod
    def from_node(cls, node, symbols):
        kind, operation = node.get("kind"), node.get("op")
        if kind == "BinaryOp" and operation in _BINARY:
            spelling, operands = _BINARY[operation], (node["left"], node["right"])
        elif kind == "UnaryOp" and operation in _UNARY:
            spelling, operands = _UNARY[operation], (node["operand"],)
        else:
            raise Refused(Refusal(
                "rtl-operation-capture-unsupported",
                "operation capture requires an eager integral expression, not a lazy or mutating operator",
                node_kind=kind, operation=operation))
        return cls(spelling, tuple(_capture_type(value["type"], symbols) for value in operands),
                   _capture_type(node["type"], symbols))

    def function(self, name: str, callback: str | None, *, observer_parameters=(), widths=(), value_types=()) -> str:
        """Emit a per-site function and invoke a typed internal observer callback.

        Names are generated by the source planner, never supplied as user HDL.
        The callback receives operand values, the result and the existing scope
        token. It can evaluate a declared predicate and call the shared hit sink;
        it must not write the artifact's state. No operation is recomputed there.
        """
        sizes = widths or (None,) * (len(self.operands) + 1)
        kinds = tuple((value_types[i] if value_types else "") or _hdl_type(value, sizes[i])
                      for i, value in enumerate((*self.operands, self.result)))
        parameters = [f"input {kinds[i]} v{i}" for i in range(len(self.operands))]
        values = [f"v{i}" for i in range(len(self.operands))]
        expression = (f"{self.operator}v0" if len(values) == 1
                      else f"v0 {self.operator} v1")
        extra = "".join(f", input {kind} {parameter}" for parameter, kind in observer_parameters)
        arguments = "".join(f", {parameter}" for parameter, _ in observer_parameters)
        report = "" if callback is None else f"{callback}({', '.join(values)}, result, scope{arguments});"
        return f"""
function automatic {kinds[-1]} {name}(
    {', '.join(parameters)}, input int unsigned scope{extra});
  {kinds[-1]} result;
  result = ({expression});
  {report}
  return result;
endfunction
"""

    def call(self, name: str, operands: tuple[str, ...], *, scope: str = "0") -> str:
        if len(operands) != len(self.operands):
            raise ValueError("operation capture requires exactly the declared operands")
        values = [f"{kind.width}'({'signed' if kind.signed else 'unsigned'}'({value}))"
                  for kind, value in zip(self.operands, operands)]
        return f"{name}({', '.join(values)}, {scope})"
