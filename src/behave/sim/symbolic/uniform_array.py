"""Immutable concrete value for a compactly initialized finite array.

SolverIR arrays have value semantics.  Materializing every cell is unnecessary
when a large finite buffer starts with one value and a step touches only a
small active prefix.  ``UniformArray`` stores that value once and records the
finite writes persistently; selection still has exactly the componentwise
SolverIR meaning.

The exact symbolic backend has the analogous :class:`FunctionalArray`.  This
class is deliberately solver-free and is used only by the concrete IR
interpreter and boundary reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["UniformArray"]

_CONSTANT = "constant"
_MEMORY_JUNK = "memory-junk/index-mix/2"


def _freeze(value):
    if type(value) in (bool, int):
        return value
    if type(value) is list:
        return ("list", tuple(_freeze(item) for item in value))
    if type(value) is UniformArray:
        return value
    raise TypeError(
        "a uniform SolverIR array contains only Boolean, integer, or array values")


def _thaw(value):
    if type(value) is tuple and len(value) == 2 and value[0] == "list":
        return [_thaw(item) for item in value[1]]
    return value


@dataclass(frozen=True, slots=True)
class _Write:
    index: int
    value: object


class UniformArray:
    """One fixed finite array represented by a compact base and sparse writes."""

    __slots__ = ("_base", "_writes", "length")

    def __init_subclass__(cls, **_kwargs):
        raise TypeError("UniformArray is a closed internal value type")

    def __init__(self, length, default, writes=()):
        self._set(length, (_CONSTANT, _freeze(default)), writes)

    @classmethod
    def _from_base(cls, length, base, writes=()):
        value = object.__new__(cls)
        value._set(length, base, writes)
        return value

    def _set(self, length, base, writes):
        if type(length) is not int or length < 0:
            raise TypeError("a compact array has a nonnegative fixed length")
        if type(base) is not tuple or not base:
            raise TypeError("a compact array has one closed base descriptor")
        if base[0] == _CONSTANT:
            if len(base) != 2:
                raise TypeError("a constant array base has one value")
            base = (_CONSTANT, _freeze(_thaw(base[1])))
        elif base[0] == _MEMORY_JUNK:
            if (len(base) != 3 or type(base[1]) is not str or not base[1]
                    or type(base[2]) is not int or isinstance(base[2], bool)
                    or base[2] < 1):
                raise TypeError(
                    "an external-memory array base has a name and data width")
        else:
            raise TypeError(f"unknown compact array base {base[0]!r}")
        rows = tuple(writes)
        if any(type(row) is not _Write or type(row.index) is not int
               or row.index < 0 or row.index >= length
               for row in rows):
            raise TypeError("a uniform array write has an in-range integer index")
        object.__setattr__(self, "length", length)
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_writes", rows)

    def __setattr__(self, _name, _value):
        raise AttributeError("a uniform array is immutable")

    @classmethod
    def uniform(cls, length, value):
        return cls(length, value)

    @classmethod
    def from_values(cls, values):
        """Compress a nonempty, homogeneously typed concrete vector once."""
        default = _freeze(values[0])
        rows = []
        for index, value in enumerate(values[1:], 1):
            value = _freeze(value)
            if type(value) is not type(default) or value != default:
                rows.append(_Write(index, value))
        return cls._from_base(len(values), (_CONSTANT, default), rows)

    @classmethod
    def memory(cls, length, name, data_width, initial=()):
        """A task memory's implicit initialization plus explicit cells."""

        value = cls._from_base(
            length, (_MEMORY_JUNK, name, data_width))
        rows = initial.items() if type(initial) is dict else initial
        writes = {}
        for index, item in rows:
            # Match total store semantics, including last-write order and
            # ignoring out-of-range values before attempting to freeze them.
            # Repeated persistent stores copy/validate the entire history for
            # every cell, making a dense external-memory image quadratic.
            if type(index) is not int:
                raise TypeError("a uniform array index is an integer")
            if not 0 <= index < length:
                continue
            row = _Write(index, _freeze(item))
            writes.pop(index, None)
            writes[index] = row
        return cls._from_base(length, value._base, writes.values())

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        if type(index) is not int:
            raise TypeError("a uniform array index is an integer")
        if index < 0 or index >= self.length:
            raise IndexError(index)
        for row in reversed(self._writes):
            if row.index == index:
                return _thaw(row.value)
        return self.base_value(index)

    @property
    def base_image(self):
        """The immutable base descriptor shared with the symbolic encoding."""

        return self._base

    @property
    def is_uniform(self):
        return self._base[0] == _CONSTANT

    @property
    def default(self):
        """A value copy of the uniform default."""

        if not self.is_uniform:
            raise AttributeError("an indexed array has no single default value")
        return _thaw(self._base[1])

    def base_value(self, index):
        """The value before explicit writes at one in-range index."""

        if type(index) is not int or not 0 <= index < self.length:
            raise IndexError(index)
        if self.is_uniform:
            return self.default
        from ..core.memory import memory_junk_value

        _tag, name, data_width = self._base
        return memory_junk_value(name, index, data_width)

    @property
    def base_bounds(self):
        """A scalar hull for an indexed base, or ``None`` for a value base."""

        if self.is_uniform:
            return None
        return 0, (1 << self._base[2]) - 1

    @property
    def element_width(self):
        """Declared scalar width of an indexed base, if it has one."""

        return None if self.is_uniform else self._base[2]

    def sparse_items(self):
        """The explicit writes as immutable ``(index, value)`` pairs."""
        return tuple((row.index, _thaw(row.value)) for row in self._writes)

    def store(self, index, value):
        """Return the array after one total SolverIR store."""
        if type(index) is not int:
            raise TypeError("a uniform array index is an integer")
        if index < 0 or index >= self.length:
            return self
        row = _Write(index, _freeze(value))
        # A later write to the same concrete index hides the earlier one.
        kept = tuple(old for old in self._writes if old.index != index)
        return UniformArray._from_base(self.length, self._base, kept + (row,))

    def zeroed(self):
        """The same recursive shape filled with the scalar zero."""
        from .values import zero_value

        return zero_value(self)

    def values_match(self, predicate):
        """Whether every represented cell value satisfies ``predicate``."""
        sample = (self.default if self.is_uniform
                  else self.base_value(0) if self.length else 0)
        return (predicate(sample)
                and all(predicate(_thaw(row.value)) for row in self._writes))

    def materialize(self):
        """Materialize explicitly when a caller intentionally needs all cells."""
        from .values import materialize_value

        return materialize_value(self)

    def extensionally_equal(self, other):
        """Exact value equality, sparse when both arrays share their base."""
        from .values import value_equal

        return value_equal(self, other)

    def __eq__(self, other):
        """Value equality across compact and dense array representations."""
        from .values import value_equal

        return value_equal(self, other)

    __hash__ = None

    def __reduce__(self):
        return _restore_uniform_array, (self.length, self._base, self._writes)

    def __repr__(self):
        return (f"UniformArray(length={self.length}, base={self._base[0]!r}, "
                f"writes={len(self._writes)})")


def _restore_uniform_array(length, base, writes):
    return UniformArray._from_base(length, base, writes)
