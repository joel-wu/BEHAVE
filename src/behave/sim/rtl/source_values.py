"""Compiler-owned recursive source values, shared by queries and native capture.

Unpacked arrays retain the shared IR's value/shape semantics. Position zero is
the *left* declared index; Slang's sensitivity bit offsets also enumerate from
that end. A temporary packed change mask is not the representation of storage.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..symbolic import ir, typecheck as types
from ..symbolic import ranges
from ..core.value_type import ValueType, as_value_type
from .source_integral import IntegralType, integral_type


@dataclass(frozen=True, slots=True)
class ArrayType:
    left: int
    right: int
    element: IntegralType | ArrayType

    @property
    def cells(self):
        return abs(self.right - self.left) + 1

    @property
    def width(self):
        return self.cells * self.element.width

    @property
    def ir_type(self):
        return types.Array(self.cells, self.element.ir_type)

    @property
    def domain(self):
        return ranges._UniformCellRange(self.cells, self.element.domain)

    @property
    def signature_type(self):
        return ValueType.array(self.cells, as_value_type(self.element.signature_type))

    @property
    def observation(self):
        return {"left": self.left, "right": self.right, "element": self.element.observation}

    def wrap(self, value):
        # Slang already inserted element conversions for compatible assignments.
        # The array itself is not an integer and must not be bit-truncated.
        return value

    def position(self, index):
        return (ir.Op("sub", index, ir.Lit(self.left)) if self.left <= self.right
                else ir.Op("sub", ir.Lit(self.left), index))

    def decode(self, value):
        if not isinstance(value, (list, tuple)) or len(value) != self.cells:
            raise ValueError("a source array needs every declared native element")
        return [self.element.decode(item) for item in value]


def source_type(node, symbols):
    while isinstance(node, str):
        node = symbols[node.split(" ", 1)[0]]
    if node.get("kind") == "TypeAlias":
        return source_type(node["target"], symbols)
    if node.get("kind") == "FixedSizeUnpackedArrayType":
        left, right = map(int, node["range"][1:-1].split(":"))
        return ArrayType(left, right, source_type(node["elementType"], symbols))
    return integral_type(node, symbols)


def zero_value(kind):
    """Typed compiler scratch only; never a substitute for source boundary state."""
    return (ir.Op("arrayfill", zero_value(kind.element), ir.Lit(kind.cells))
            if isinstance(kind, ArrayType) else ir.Lit(0))
