"""Exact affine bit-vector indices for source write-footprint separation.

This only proves that two selectors cannot name the same element. It neither
changes a selector nor executes an additional solver query. Every intermediate
operation must retain its compiler-resolved width; narrowing/widening and a
four-state to two-state conversion are not erased by the proof.
"""

from dataclasses import dataclass

from ..core.refusal import Refused
from .source_integral import integer_literal, integral_value_facts


@dataclass(frozen=True, slots=True)
class AffineIndex:
    width: int
    signed: bool
    four_state: bool
    terms: tuple[tuple[str, int], ...]
    offset: int

    @property
    def type(self):
        return self.width, self.signed, self.four_state

    @property
    def constant(self):
        value = self.offset
        return value - (1 << self.width) if self.signed and value >> (self.width - 1) else value

    def disjoint(self, other):
        if not self.terms and not other.terms:
            return self.constant != other.constant
        return (self.type == other.type and self.terms == other.terms
                and self.offset != other.offset)


def affine_index(node, symbols, *, stable):
    """Normalize a pure index whose source variables stay fixed in the statement.

    Equal affine terms and unequal offsets modulo the same width are unequal
    for every valuation. Unknown four-state indices do not address an element;
    refusing conversions that turn X/Z into concrete bits keeps that argument
    valid without assuming that an uninitialised variable is zero.
    """
    try:
        facts = integral_value_facts(node["type"], symbols)
    except Refused:
        return None
    width, _, four_state = facts
    mask = (1 << width) - 1

    def value(terms, offset):
        return AffineIndex(*facts, tuple(sorted(
            (key, coefficient & mask) for key, coefficient in terms.items()
            if coefficient & mask)), offset & mask)

    kind = node.get("kind")
    if kind == "IntegerLiteral":
        try:
            return value({}, integer_literal(node["value"]))
        except Refused:
            return None
    if kind in {"NamedValue", "HierarchicalValue"}:
        symbol = node["symbol"].split(" ", 1)[0]
        return value({symbol: 1}, 0) if stable(symbol) else None
    if kind in {"Conversion", "UnaryOp"}:
        operand = affine_index(node["operand"], symbols, stable=stable)
        if operand is None or operand.width != width:
            return None
        if operand.four_state and not four_state:
            return None
        if kind == "Conversion" or node.get("op") == "Plus":
            return value(dict(operand.terms), operand.offset)
        if node.get("op") == "Minus":
            return value({key: -coefficient for key, coefficient in operand.terms}, -operand.offset)
        return None
    if kind != "BinaryOp" or node.get("op") not in {"Add", "Subtract", "Multiply"}:
        return None
    left = affine_index(node["left"], symbols, stable=stable)
    right = affine_index(node["right"], symbols, stable=stable)
    if (left is None or right is None or left.width != width or right.width != width
            or (left.four_state or right.four_state) and not four_state):
        return None
    if node["op"] == "Multiply":
        if left.terms and right.terms:
            return None
        variable, factor = (left, right.offset) if left.terms else (right, left.offset)
        return value({key: coefficient * factor for key, coefficient in variable.terms},
                     variable.offset * factor)
    sign = -1 if node["op"] == "Subtract" else 1
    terms = dict(left.terms)
    for key, coefficient in right.terms:
        terms[key] = terms.get(key, 0) + sign * coefficient
    return value(terms, left.offset + sign * right.offset)
