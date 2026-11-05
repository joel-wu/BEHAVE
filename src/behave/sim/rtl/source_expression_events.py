"""Source expression coordinates and truth-preserving native observation.

Only a self-determined predicate or Boolean term is wrapped. Conditional value
arms never cross a function boundary changing contextual width or signedness. The
SystemVerilog function returns the same four-state truth value; an indeterminate
choice invalidates the two-state observation instead of being recorded as false.
"""

from dataclasses import dataclass

CHOICE_FUNCTION = "__behave_native_event_choice"
LOGICAL_CELLS = {"LogicalAndExpression": "$logic_and", "LogicalOrExpression": "$logic_or"}


@dataclass(frozen=True, slots=True)
class LogicalOperand:
    """One exact source operator edge, including its short-circuit context."""

    start: int
    end: int
    cell: str
    port: str


def choice_function(callback: str) -> str:
    return f"""
function automatic logic {CHOICE_FUNCTION}(
    input logic value, input int unsigned when_true, when_false, input bit observed,
    input int unsigned scope);
  if (observed) case (value)
    1'b1: {callback}(when_true, scope);
    1'b0: {callback}(when_false, scope);
    default: {callback}(32'hffffffff, scope);
  endcase
  {CHOICE_FUNCTION} = value;
endfunction
"""


def choice_wrapper(when_true: str, when_false: str, *, observed: str = "1'b1", scope: str = "0") -> tuple[str, str]:
    # Logical negation, unlike truncation to one bit, preserves the truth of
    # vectors, signed values and the language's other legal scalar predicates.
    return f"{CHOICE_FUNCTION}(!(!(", f")), {when_true}, {when_false}, {observed}, {scope})"
