"""Solver-free value operations for concrete SolverIR values.

An IR array may be a dense ``list`` or a compact :class:`UniformArray`.  These
are two representations of the same recursively defined value domain, so all
consumers use the operations here instead of branching on a carrier and then
falling back to Python object equality.
"""

from __future__ import annotations

from .uniform_array import UniformArray

__all__ = [
    "copy_value",
    "materialize_value",
    "value_equal",
    "value_matches_type",
    "zero_value",
]


def copy_value(value):
    """Copy a value without expanding an immutable compact array."""

    if type(value) is UniformArray:
        return value
    if type(value) is list:
        return [copy_value(item) for item in value]
    if type(value) in (bool, int):
        return value
    raise TypeError(f"not a concrete SolverIR value: {type(value).__name__}")


def zero_value(value):
    """Return zero with the same recursive array shape and scalar types."""

    if type(value) is UniformArray:
        if value.is_uniform:
            element = value.default
        elif value.length:
            element = value.base_value(0)
        else:
            # External-memory bases are scalar.  A zero-length uniform array still
            # takes the branch above and therefore retains its nested element shape.
            element = 0
        return UniformArray.uniform(value.length, zero_value(element))
    if type(value) is list:
        return [zero_value(item) for item in value]
    if type(value) is bool:
        return False
    if type(value) is int:
        return 0
    raise TypeError(f"not a concrete SolverIR value: {type(value).__name__}")


def value_matches_type(value, kind):
    """Whether ``value`` inhabits one recursive SolverIR type."""

    from . import types

    if isinstance(kind, types.Array):
        if type(value) is UniformArray:
            return (len(value) == kind.cells
                    and value.values_match(
                        lambda item: value_matches_type(item, kind.elem)))
        return (type(value) is list and len(value) == kind.cells
                and all(value_matches_type(item, kind.elem) for item in value))
    if kind == types.BOOLEAN:
        return type(value) is bool
    return kind == types.INTEGER and type(value) is int


def _same_implicit_base(left, right):
    if left.is_uniform and right.is_uniform:
        return value_equal(left.default, right.default)
    return left.base_image == right.base_image


def value_equal(left, right):
    """Extensional equality, independent of dense or compact representation."""

    if type(left) is UniformArray and type(right) is UniformArray:
        if len(left) != len(right):
            return False
        if _same_implicit_base(left, right):
            indices = ({index for index, _ in left.sparse_items()}
                       | {index for index, _ in right.sparse_items()})
        else:
            # Different implicit bases require reading every cell, but still do not
            # allocate a dense copy of either array.
            indices = range(len(left))
        return all(value_equal(left[index], right[index]) for index in indices)
    if type(left) is UniformArray and type(right) is list:
        return (len(left) == len(right)
                and all(value_equal(left[index], item)
                        for index, item in enumerate(right)))
    if type(right) is UniformArray and type(left) is list:
        return value_equal(right, left)
    if type(left) is list and type(right) is list:
        return (len(left) == len(right)
                and all(value_equal(a, b) for a, b in zip(left, right)))
    if type(left) is not type(right) or type(left) not in (bool, int):
        return False
    return left == right


def materialize_value(value):
    """Produce a fully dense recursive value when a boundary explicitly needs one."""

    if type(value) is UniformArray:
        return [materialize_value(value[index]) for index in range(len(value))]
    if type(value) is list:
        return [materialize_value(item) for item in value]
    if type(value) in (bool, int):
        return value
    raise TypeError(f"not a concrete SolverIR value: {type(value).__name__}")
