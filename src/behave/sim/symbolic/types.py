"""Recursive SolverIR type values, independent of source and graph checking.

Boolean, integer and fixed-array identity belongs to the shared representation.
Frontends separately infer and validate these types over their own programs.
"""

from __future__ import annotations

__all__ = ["BOOLEAN", "INTEGER", "VALUE", "Array", "is_type", "of_shape", "shape_of", "of_value"]


class _Value:
    """Base class for scalar semantic types.

    This private base remains useful to callers that only need to distinguish a
    scalar from an array.  Equality is exact by subclass, so it is not a route
    back to the former Boolean/integer conflation.
    """

    __slots__ = ()

    def __eq__(self, other):
        return type(self) is type(other)

    def __hash__(self):
        return hash(type(self))


class _Boolean(_Value):
    """A semantic truth value."""

    __slots__ = ()

    def __repr__(self):
        return "a Boolean"


class _Integer(_Value):
    """A mathematical integer, encoded at a width justified by its range."""

    __slots__ = ()

    def __repr__(self):
        return "an integer"


BOOLEAN = _Boolean()
INTEGER = _Integer()

# Compatibility spelling for callers that previously meant "the integer
# SolverIR scalar".  It is an alias, not a third or widened semantic type.  New
# code should say ``INTEGER`` so a future removal is mechanical.
VALUE = INTEGER


def is_type(value):
    """Whether ``value`` is one recursive SolverIR semantic type."""
    return isinstance(value, (_Value, Array))


class Array:
    """A fixed number of cells of one recursively defined element type.

    The length is part of the type because it is part of the meaning: a
    subscript of minus one names the last cell, so an array whose length is not
    fixed before the run has no reading. The lowering already refuses those; this
    is where the length is carried once they are past it.
    """

    __slots__ = ("cells", "elem")

    def __init__(self, cells, elem=INTEGER):
        if hasattr(self, "cells"):
            raise AttributeError("an array type is immutable")
        cells = int(cells)
        if cells < 0:
            raise ValueError(f"an array with {cells} cells")
        if not isinstance(elem, (_Value, Array)):
            raise TypeError(f"an array element type of {elem!r}")
        object.__setattr__(self, "cells", cells)
        object.__setattr__(self, "elem", elem)

    def __setattr__(self, name, value):
        raise AttributeError("an array type is immutable")

    def __delattr__(self, name):
        raise AttributeError("an array type is immutable")

    def __reduce__(self):
        # Reconstruct through the validating constructor.  Without this,
        # pickle restores slots by assignment and defeats the write-once rule
        # (or, after the rule was added, kills every worker process).
        return type(self), (self.cells, self.elem)

    def __repr__(self):
        return f"an array of {self.cells} {self.elem}s"

    def __eq__(self, other):
        return (isinstance(other, Array) and other.cells == self.cells
                and other.elem == self.elem)

    def __hash__(self):
        return hash(("array", self.cells, self.elem))


def of_shape(shape, elem=INTEGER):
    """Representation type of one proved recursive physical shape."""
    shape = tuple(int(part) for part in shape)
    if not isinstance(elem, _Value):
        raise TypeError(f"a scalar leaf type of {elem!r}")
    result = elem
    for cells in reversed(shape):
        result = Array(cells, result)
    return result


def shape_of(kind):
    """Physical dimensions of a declared type, not its scalar leaf identity."""
    shape = []
    while isinstance(kind, Array):
        shape.append(kind.cells)
        kind = kind.elem
    return tuple(shape)


def of_value(value):
    """The recursive representation type of a concrete initial value.

    An empty array has no element from which to infer a deeper type, so its
    element type is the scalar value type.  A later assignment of another shape
    is then rejected by the ordinary one-type-per-name rule.
    """
    if type(value) is bool:
        return BOOLEAN
    if type(value) is int:
        return INTEGER
    if type(value) is not list:
        return None
    parts = [of_value(item) for item in value]
    elem = parts[0] if parts else INTEGER
    if elem is None or any(part != elem for part in parts):
        return None
    return Array(len(parts), elem)


# Private compatibility for the few older internal callers.  Keep one public
# spelling so new code cannot accidentally create a second inference rule.
_of_value = of_value


