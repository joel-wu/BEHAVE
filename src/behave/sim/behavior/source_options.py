"""Flow refinement for finite source ``None | T`` values.

The contract algebra belongs to :mod:`behave.sim.symbolic.value_contract`: ``NONE`` is the
``None``-only contract and ``ValueContract.option(T)`` is the finite sum
``None | T``.  This module recognizes Python's exact identity test and narrows
that shared contract on its two control-flow arms.

An internal optional is distinct from the transaction-boundary convention in
which a complete ``process`` return of ``None`` emits no output.  Lowering an
internal option uses a presence bit plus the existing payload representation;
it does not reinterpret the procedure result convention.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..symbolic.value_contract import (
    NONE, ValueContract, narrow_none, optional_sequence as optional_sequence,
)

__all__ = [
    "NONE",
    "NoneTest",
    "OptionValue",
    "narrow_none",
    "none_test",
]


@dataclass(frozen=True)
class OptionValue:
    """One lowering-time tag/payload product.

    The two fields are emitter-defined objects (normally SolverIR
    expressions).  A ``NONE`` literal has no payload yet; once an enclosing
    expression or binding supplies ``Option[T]``, lowering fills the payload
    with the canonical representation of ``T`` and the presence bit remains
    false.  The payload is never observed on that arm.
    """

    present: object
    payload: object | None
    contract: ValueContract

    def __post_init__(self):
        if not (self.contract.is_none or self.contract.is_option):
            raise ValueError("an option value needs a None or Option contract")
        if self.contract.is_none and self.payload is not None:
            raise ValueError("a None-only value has no planned payload")
        if self.contract.is_option and self.payload is None:
            raise ValueError("a planned Option value needs its payload slot")


@dataclass(frozen=True)
class NoneTest:
    """One exact Python identity test and its non-``None`` operand."""

    operand: ast.expr
    none_when_true: bool


def none_test(node):
    """Recognize ``x is None`` or ``x is not None``, in either order.

    Equality is intentionally excluded: ``x == None`` may call user-defined
    ``__eq__`` and is not the tag test used by the certified finite subset.
    Chained comparisons are excluded because they contain more than one test.
    """
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 \
            or len(node.comparators) != 1:
        return None
    operator = node.ops[0]
    if not isinstance(operator, (ast.Is, ast.IsNot)):
        return None
    left, right = node.left, node.comparators[0]
    left_none = isinstance(left, ast.Constant) and left.value is None
    right_none = isinstance(right, ast.Constant) and right.value is None
    if left_none == right_none:
        return None
    return NoneTest(right if left_none else left,
                    none_when_true=isinstance(operator, ast.Is))
