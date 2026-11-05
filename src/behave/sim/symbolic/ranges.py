"""Ranges of the values an expression can take.

Two things need this. A loop whose trip count depends on a value needs a proved
finite maximum before an EncodingPlan may expand it, and a bitvector encoding
is only faithful if each subterm is represented at a width injective on the
values it can hold: a sum of two 16-bit numbers is a 17-bit quantity, and
encoding it in 16 bits would wrap where the source does not.

A range is an inclusive interval of integers, computed upwards from the ranges
of the operands.  Each primitive's transformer lives in the shared registry;
this module supplies the abstract domain, structural refinements and fixpoint
that apply those transformers.  A wider interval costs bits while a wrong one
costs correctness, so every transformer is sound rather than necessarily tight.
"""

from __future__ import annotations

import enum
import threading
import weakref
from collections.abc import Mapping
from itertools import pairwise
from types import MappingProxyType

from behave.core import runtime_defaults as defaults

from . import ir, primitives
from .capabilities import STATE_MAGNITUDE, STATE_ROUNDS

_RANGE_CHECKPOINT = object()

__all__ = [
    "UNKNOWN",
    "AbstractRangeMap",
    "Bounds",
    "Range",
    "UnknownRange",
    "affine_form",
    "assume",
    "assume_bounds",
    "bounded",
    "bounds_of_expression",
    "encoding_width",
    "fixpoint",
    "graph_post",
    "initial_ranges",
    "loop_trip_range",
    "of_expression",
    "post",
    "procedure_post",
    "range_of_value",
    "trip_range",
    "unproved",
    "unranged_subterms",
    "width_for",
]


def affine_form(expression, *, env=None):
    """Return ``(constant, coefficients)`` for a linear SolverIR term.

    Interval arithmetic deliberately forgets correlations.  This small exact
    form retains enough of them to prove facts such as
    ``(2 * i + 2) - (2 * i) == 2`` without invoking a solver or accepting a
    nonlinear expression.
    """

    def combine(left, right, scale=1):
        constant = left[0] + scale * right[0]
        coefficients = dict(left[1])
        for name, value in right[1].items():
            coefficients[name] = coefficients.get(name, 0) + scale * value
            if coefficients[name] == 0:
                del coefficients[name]
        return constant, coefficients

    if isinstance(expression, ir.Lit) and type(expression.value) is int:
        return expression.value, {}
    if isinstance(expression, ir.Var):
        known = None if env is None else env.get(expression.name)
        if known is not None and type(known.lo) is int and known.lo == known.hi:
            return known.lo, {}
        return 0, {expression.name: 1}
    if not isinstance(expression, ir.Op):
        return None
    if expression.op == "neg" and len(expression.args) == 1:
        inner = affine_form(expression.args[0], env=env)
        return (None if inner is None else
                (-inner[0], {name: -value
                             for name, value in inner[1].items()}))
    if expression.op in {"add", "sub"} and len(expression.args) == 2:
        left = affine_form(expression.args[0], env=env)
        right = affine_form(expression.args[1], env=env)
        if left is None or right is None:
            return None
        return combine(left, right, -1 if expression.op == "sub" else 1)
    if expression.op == "mul" and len(expression.args) == 2:
        left, right = expression.args
        if isinstance(left, ir.Lit) and type(left.value) is int:
            scalar, inner = left.value, affine_form(right, env=env)
        elif isinstance(right, ir.Lit) and type(right.value) is int:
            scalar, inner = right.value, affine_form(left, env=env)
        elif env is not None:
            first, second = affine_form(left, env=env), affine_form(right, env=env)
            if first is None or second is None:
                return None
            if not first[1]:
                scalar, inner = first[0], second
            elif not second[1]:
                scalar, inner = second[0], first
            else:
                return None
        else:
            return None
        if inner is None:
            return None
        return (scalar * inner[0],
                {name: scalar * value for name, value in inner[1].items()
                 if scalar * value})
    return None


class Range:
    """An inclusive interval of integers."""

    __slots__ = ("hi", "lo")

    def __init__(self, lo, hi):
        # Swapping the ends would turn a transformer that computed them the
        # wrong way round into an interval that looks reasonable, and the
        # width taken from it would be wrong without anything saying so.
        if hi < lo:
            raise ValueError(f"an interval from {lo} to {hi}")
        self.lo = int(lo)
        self.hi = int(hi)

    @property
    def magnitude(self):
        return max(abs(self.lo), abs(self.hi))

    def join(self, other):
        """The smallest interval containing both, for a value that may come
        from either arm of a conditional."""
        if self.lo <= other.lo and other.hi <= self.hi:
            return self
        if other.lo <= self.lo and self.hi <= other.hi:
            return other
        return Range(min(self.lo, other.lo), max(self.hi, other.hi))

    def __eq__(self, other):
        return isinstance(other, Range) and (other.lo, other.hi) == (self.lo, self.hi)

    def __hash__(self):
        return hash((self.lo, self.hi))

    def __repr__(self):
        return f"[{self.lo},{self.hi}]"


class _CellIdentity:
    """Canonical identity of one immutable cell-product shape."""

    __slots__ = ("__weakref__",)


_CELL_IDENTITIES = weakref.WeakValueDictionary()
_CELL_IDENTITY_LOCK = threading.Lock()


def _cell_identity(cells):
    key = tuple(
        ("cells", cell._identity)
        if type(cell) in (_CellRange, _UniformCellRange) else
        ("range", cell.lo, cell.hi)
        for cell in cells
    )
    with _CELL_IDENTITY_LOCK:
        identity = _CELL_IDENTITIES.get(key)
        if identity is None:
            identity = _CellIdentity()
            _CELL_IDENTITIES[key] = identity
    return identity


class _CellRange(Range):
    """A finite array hull with optional finite facts for each cell.

    Public range results remain scalar intervals.  During one abstract step,
    however, compiler temporaries often carry heterogeneous tuples such as
    ``(sign, exponent, mantissa)``.  Replacing that value immediately by the
    hull of all cells makes a fixed selection of the exponent inherit the
    mantissa's much wider range.  This private value retains the product until
    an operation genuinely forgets which cell it reads.
    """

    __slots__ = ("_hash", "_identity", "cells")

    def __init__(self, cells):
        cells = tuple(cells)
        if any(not isinstance(cell, Range) for cell in cells):
            raise TypeError("a cell range needs one finite range per cell")
        # The empty product is a proper fixed-array value.  It has no numeric
        # leaf whose interval could be joined, so the neutral 0..0 hull is
        # representation metadata only: a zero-cell array cannot be selected,
        # and its exact closure contributes no scalar membership predicate.
        object.__setattr__(self, "lo", min(
            (cell.lo for cell in cells), default=0))
        object.__setattr__(self, "hi", max(
            (cell.hi for cell in cells), default=0))
        object.__setattr__(self, "cells", cells)
        # Canonicalize the complete product from canonical child identities.
        # Equality and hashing are then constant-time even for deeply nested
        # arrays.  The weak table cannot retain products after an analysis,
        # and exact (rather than digest) keys keep collisions irrelevant to
        # the abstract semantics.
        identity = _cell_identity(cells)
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_hash", hash(identity))

    def __setattr__(self, name, value):
        raise AttributeError("a cell range is immutable")

    def __reduce__(self):
        return type(self), (self.cells,)

    def join(self, other):
        return _join_range(self, other)

    def __eq__(self, other):
        return (self is other or (
            type(other) is _CellRange
            and self._identity is other._identity))

    def __hash__(self):
        return self._hash


class _UniformCellRange(Range):
    """A fixed array range represented by one default and finite runs.

    ``arrayfill`` is semantic structure, not a request to allocate every
    represented cell.  Retaining non-default intervals lets exact-index
    stores preserve per-cell facts while a long consecutively written prefix
    remains a single piece of abstract structure.
    """

    __slots__ = ("_hash", "_identity", "default", "length", "runs")

    def __init__(self, length, default, overrides=()):
        if type(length) is not int or length < 0:
            raise TypeError("a uniform cell range has a natural length")
        if not isinstance(default, Range):
            raise TypeError("a uniform cell range needs a finite default")
        cells = {}
        for index, cell in tuple(overrides):
            if (type(index) is not int or not 0 <= index < length
                    or not isinstance(cell, Range)):
                raise TypeError("a sparse cell range needs an in-range cell")
            cells[index] = cell
        runs = []
        for index, cell in sorted(cells.items()):
            if cell == default:
                continue
            if (runs and runs[-1][1] == index
                    and runs[-1][2] == cell):
                start, _stop, previous = runs[-1]
                runs[-1] = (start, index + 1, previous)
            else:
                runs.append((index, index + 1, cell))
        self._install(length, default, runs)

    @classmethod
    def _from_runs(cls, length, default, runs):
        value = cls.__new__(cls)
        value._install(length, default, runs)
        return value

    def _install(self, length, default, runs):
        if type(length) is not int or length < 0:
            raise TypeError("a uniform cell range has a natural length")
        if not isinstance(default, Range):
            raise TypeError("a uniform cell range needs a finite default")
        normalized = []
        previous_stop = 0
        for start, stop, cell in tuple(runs):
            if (type(start) is not int or type(stop) is not int
                    or not 0 <= start < stop <= length
                    or start < previous_stop or not isinstance(cell, Range)):
                raise TypeError("a cell run must be ordered and in range")
            previous_stop = stop
            if cell == default:
                continue
            if (normalized and normalized[-1][1] == start
                    and normalized[-1][2] == cell):
                old_start, _old_stop, previous = normalized[-1]
                normalized[-1] = (old_start, stop, previous)
            else:
                normalized.append((start, stop, cell))
        runs = tuple(normalized)
        if length:
            represented = [cell for _start, _stop, cell in runs]
            if sum(stop - start for start, stop, _cell in runs) < length:
                represented.append(default)
            lo = min(cell.lo for cell in represented)
            hi = max(cell.hi for cell in represented)
        else:
            lo = hi = 0
        object.__setattr__(self, "lo", lo)
        object.__setattr__(self, "hi", hi)
        object.__setattr__(self, "length", length)
        object.__setattr__(self, "default", default)
        object.__setattr__(self, "runs", runs)
        identity = _cell_identity((
            Range(length, length), default,
            *(_CellRange((Range(start, start), Range(stop, stop), cell))
              for start, stop, cell in runs),
        ))
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_hash", hash(identity))

    def __setattr__(self, name, value):
        raise AttributeError("a uniform cell range is immutable")

    def __reduce__(self):
        return _uniform_cell_range_from_runs, (
            self.length, self.default, self.runs)

    def __eq__(self, other):
        return (self is other or (
            type(other) is _UniformCellRange
            and self._identity is other._identity))

    def __hash__(self):
        return self._hash

    def join(self, other):
        return _join_range(self, other)

    def cell(self, index):
        if type(index) is not int or not 0 <= index < self.length:
            raise IndexError(index)
        lo, hi = 0, len(self.runs)
        while lo < hi:
            middle = (lo + hi) // 2
            if self.runs[middle][0] <= index:
                lo = middle + 1
            else:
                hi = middle
        if lo:
            start, stop, value = self.runs[lo - 1]
            if start <= index < stop:
                return value
        return self.default

    def store(self, index, value):
        if type(index) is not int or not 0 <= index < self.length:
            raise IndexError(index)
        if not isinstance(value, Range):
            raise TypeError("a cell store needs a finite range")
        if self.cell(index) == value:
            return self
        rows = []
        inserted = False
        for start, stop, cell in self.runs:
            if stop <= index:
                rows.append((start, stop, cell))
                continue
            if start > index:
                if not inserted and value != self.default:
                    rows.append((index, index + 1, value))
                inserted = True
                rows.append((start, stop, cell))
                continue
            if start < index:
                rows.append((start, index, cell))
            if value != self.default:
                rows.append((index, index + 1, value))
            if index + 1 < stop:
                rows.append((index + 1, stop, cell))
            inserted = True
        if not inserted and value != self.default:
            rows.append((index, index + 1, value))
        return type(self)._from_runs(self.length, self.default, rows)


def _uniform_cell_range_from_runs(length, default, runs):
    return _UniformCellRange._from_runs(length, default, runs)


def _is_cell_range(value):
    return type(value) in (_CellRange, _UniformCellRange)


def _cell_length(value):
    if type(value) is _CellRange:
        return len(value.cells)
    if type(value) is _UniformCellRange:
        return value.length
    raise TypeError("not a cell range")


def _cell_at(value, index):
    if type(value) is _CellRange:
        return value.cells[index]
    if type(value) is _UniformCellRange:
        return value.cell(index)
    raise TypeError("not a cell range")


def _selected_range(array, index):
    """Join possible selected cells, not unrelated cells or the outer product.

    Index validity remains a separate source obligation. Empty/out-of-range
    selections keep a conservative leaf hull rather than proving an exit.
    Compressed arrays visit overlapping runs, never their represented length.
    """
    if not _is_cell_range(array):
        return array
    length = _cell_length(array)
    lower = 0 if index is None or index.lo is None else max(0, index.lo)
    upper = length if index is None or index.hi is None else min(length, index.hi + 1)
    if lower >= upper:
        return Range(array.lo, array.hi)
    if upper == lower + 1:
        return _cell_at(array, lower)
    if type(array) is _CellRange:
        result = array.cells[lower]
        for position in range(lower + 1, upper):
            result = _join_range(result, array.cells[position])
        return result
    lo, hi = 0, len(array.runs)
    while lo < hi:
        middle = (lo + hi) // 2
        if array.runs[middle][1] <= lower:
            lo = middle + 1
        else:
            hi = middle
    result, covered = None, 0
    while lo < len(array.runs):
        start, stop, cell = array.runs[lo]
        if start >= upper:
            break
        covered += min(stop, upper) - max(start, lower)
        result = cell if result is None else _join_range(result, cell)
        lo += 1
    if covered < upper - lower:
        result = array.default if result is None else _join_range(result, array.default)
    return result


def _store_cell(value, index, cell):
    if type(value) is _CellRange:
        cells = list(value.cells)
        cells[index] = cell
        return _CellRange(cells)
    if type(value) is _UniformCellRange:
        return value.store(index, cell)
    raise TypeError("not a cell range")


def _stored_range(array, index, value):
    """One array write: strong at a known cell, weak at possible cells.

    Both statement stores and functional stores use this transfer. Dynamic
    indices lose correlation, not the array's shape or its untouched cells.
    Uniform products are updated by runs, without enumerating their length.
    """
    if value is None:
        return None
    if not _is_cell_range(array):
        return value if array is None else _join_range(array, value)
    length = _cell_length(array)
    if index is not None and index.lo == index.hi and 0 <= index.lo < length:
        return _store_cell(array, index.lo, value)
    lower = 0 if index is None else max(0, index.lo)
    upper = length if index is None else min(length, index.hi + 1)
    if lower >= upper:
        # No represented cell is selected. This does not discharge the
        # separate source-domain obligation for an invalid index.
        return array
    if type(array) is _CellRange:
        return _CellRange(
            _join_range(cell, value) if lower <= i < upper else cell
            for i, cell in enumerate(array.cells))
    default = (_join_range(array.default, value)
               if lower == 0 and upper == length else array.default)
    boundaries = {0, length, lower, upper}
    for start, stop, _cell in array.runs:
        boundaries.update((start, stop))
    runs = []
    for start, stop in pairwise(sorted(boundaries)):
        cell = array.cell(start)
        if lower <= start < upper:
            cell = _join_range(cell, value)
        if cell != default:
            runs.append((start, stop, cell))
    return _UniformCellRange._from_runs(length, default, runs)


def _join_range(left, right, cache=None):
    """Join two immutable range products with structural sharing.

    Retained nested loops repeatedly join the same immutable array products.
    Cache by object identity within one abstract transfer and preserve either
    operand when the componentwise result is already that operand.  The
    identity check stored with each cache entry prevents an ``id`` reuse from
    changing the result.
    """
    if left is right:
        return left
    if type(left) is Range and type(right) is Range:
        # Scalar hulls already take constant work. Retaining every temporary
        # pair makes a long finite transfer's memory grow with its visits;
        # reserve memoization for the componentwise array work below.
        return left.join(right)
    key = (id(left), id(right))
    if cache is not None:
        found = cache.get(key)
        if (found is not None and found[0] is left
                and found[1] is right):
            return found[2]

    if (_is_cell_range(left) and _is_cell_range(right)
            and _cell_length(left) == _cell_length(right)):
        if left == right:
            result = left
        elif (type(left) is _UniformCellRange
              and type(right) is _UniformCellRange):
            default = _join_range(left.default, right.default, cache)
            boundaries = {0, left.length}
            for start, stop, _cell in left.runs + right.runs:
                boundaries.add(start)
                boundaries.add(stop)
            points = sorted(boundaries)
            runs = []
            for start, stop in pairwise(points):
                cell = _join_range(
                    left.cell(start), right.cell(start), cache)
                if cell != default:
                    runs.append((start, stop, cell))
            joined = _UniformCellRange._from_runs(
                left.length, default, runs)
            if joined == left:
                result = left
            elif joined == right:
                result = right
            else:
                result = joined
        else:
            cells = tuple(
                _join_range(
                    _cell_at(left, index), _cell_at(right, index), cache)
                for index in range(_cell_length(left)))
            if (type(left) is _CellRange
                    and all(cell is old for cell, old
                            in zip(cells, left.cells))):
                result = left
            elif (type(right) is _CellRange
                  and all(cell is old for cell, old
                          in zip(cells, right.cells))):
                result = right
            else:
                result = _CellRange(cells)
    elif _is_cell_range(left):
        result = Range(min(left.lo, right.lo), max(left.hi, right.hi))
    else:
        result = left.join(right)

    if cache is not None:
        cache[key] = (left, right, result)
    return result


class UnknownRange(enum.Enum):
    """An abstract range that the analysis did not establish."""

    UNKNOWN = "unknown"

    def __bool__(self):
        raise TypeError("an unknown range is not a truth value")


UNKNOWN = UnknownRange.UNKNOWN


class AbstractRangeMap(Mapping):
    """A total finite map from its declared variable universe to range facts.

    ``dict`` absence used to mean both "this variable is outside the analysis" and
    "the analysis could not bound it".  The universe is now explicit and every member
    maps to either :class:`Range` or :data:`UNKNOWN`.  Encoders consume
    :attr:`known`, the deliberate projection of facts with finite widths.
    :attr:`products` retains the same proof's cell structure for subsequent
    range checks. Both are detached views of one immutable result.
    """

    __slots__ = ("_values",)

    def __init__(self, universe, known=()):
        universe = tuple(universe)
        if any(type(name) is not str or not name for name in universe):
            raise TypeError("an abstract-range universe contains exact names")
        if len(universe) != len(set(universe)):
            raise ValueError("an abstract-range universe contains no duplicates")
        known = dict(known)
        outside = sorted(set(known) - set(universe))
        if outside:
            raise ValueError(f"known ranges lie outside their universe: {outside}")
        if any(not isinstance(value, Range) for value in known.values()):
            raise TypeError("known abstract ranges are finite Range values")
        memo = {}
        object.__setattr__(self, "_values", MappingProxyType({
            name: (UNKNOWN if name not in known else
                   _range_image(known[name], memo))
            for name in sorted(universe)
        }))

    def __setattr__(self, name, value):
        raise AttributeError("an abstract-range result is immutable")

    def __delattr__(self, name):
        raise AttributeError("an abstract-range result is immutable")

    def __getitem__(self, name):
        value = self._values[name]
        return UNKNOWN if value is UNKNOWN else Range(*value[:2])

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    @property
    def known(self):
        """The finite-range projection accepted by width/encoding consumers."""

        return {
            name: Range(*value[:2]) for name, value in self._values.items()
            if value is not UNKNOWN
        }

    @property
    def products(self):
        """Detached finite products, without collapsing separate array cells."""
        memo = {}
        return {name: _range_from_image(value, memo)
                for name, value in self._values.items()
                if value is not UNKNOWN}

    @property
    def universe(self):
        """The complete, declared key set of this result."""

        return tuple(self._values)

    @property
    def unknown(self):
        return tuple(
            name for name, value in self._values.items() if value is UNKNOWN)

    def as_dict(self):
        memo = {}
        return {
            name: ({"kind": "unknown"} if value is UNKNOWN else
                   _range_document(value, memo))
            for name, value in self._values.items()
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or any(type(name) is not str for name in value):
            raise TypeError("an abstract-range image is a string-keyed mapping")
        known, memo = {}, {}
        for name, row in value.items():
            if type(row) is not dict:
                raise TypeError("an abstract-range row is a mapping")
            if row == {"kind": "unknown"}:
                continue
            known[name] = _range_from_document(row, memo)
        return cls(value, known)

    def __eq__(self, other):
        return (type(other) is AbstractRangeMap and self.universe == other.universe
                and self.products == other.products)

    def __repr__(self):
        return (f"AbstractRangeMap({len(self.known)} known, "
                f"{len(self.unknown)} unknown)")

    def __reduce__(self):
        return (AbstractRangeMap.from_dict, (self.as_dict(),))


def _range_image(value, memo):
    """Freeze products once per shared node, keeping compressed runs compact."""
    key = id(value)
    if key not in memo:
        head = value.lo, value.hi
        if type(value) is _CellRange:
            image = (*head, "cells", tuple(
                _range_image(cell, memo) for cell in value.cells))
        elif type(value) is _UniformCellRange:
            image = (*head, "runs", value.length,
                     _range_image(value.default, memo), tuple(
                         (start, stop, _range_image(cell, memo))
                         for start, stop, cell in value.runs))
        else:
            image = head
        memo[key] = image
    return memo[key]


def _range_from_image(image, memo):
    key = id(image)
    if key not in memo:
        if len(image) == 2:
            value = Range(*image)
        elif image[2] == "cells":
            value = _CellRange(tuple(
                _range_from_image(cell, memo) for cell in image[3]))
        else:
            value = _UniformCellRange._from_runs(
                image[3], _range_from_image(image[4], memo), tuple(
                    (start, stop, _range_from_image(cell, memo))
                    for start, stop, cell in image[5]))
        memo[key] = value
    return memo[key]


def _range_document(image, memo):
    key = id(image)
    if key not in memo:
        if len(image) == 2:
            row = {"kind": "range", "lo": image[0], "hi": image[1]}
        elif image[2] == "cells":
            row = {"kind": "cells", "cells": [
                _range_document(cell, memo) for cell in image[3]]}
        else:
            row = {"kind": "runs", "length": image[3],
                   "default": _range_document(image[4], memo), "runs": [
                       [start, stop, _range_document(cell, memo)]
                       for start, stop, cell in image[5]]}
        memo[key] = row
    return memo[key]


def _range_from_document(row, memo):
    key = id(row)
    if key not in memo:
        memo[key] = None
        memo[key] = _decode_range_document(row, memo)
    if memo[key] is None:
        raise ValueError("an abstract-range image cannot contain a cycle")
    return memo[key]


def _decode_range_document(row, memo):
    if type(row) is dict:
        if (set(row) == {"kind", "lo", "hi"} and row["kind"] == "range"
                and type(row["lo"]) is int and type(row["hi"]) is int):
            return Range(row["lo"], row["hi"])
        if (set(row) == {"kind", "cells"} and row["kind"] == "cells"
                and type(row["cells"]) is list):
            return _CellRange(tuple(_range_from_document(cell, memo)
                                    for cell in row["cells"]))
        if (set(row) == {"kind", "length", "default", "runs"}
                and row["kind"] == "runs" and type(row["runs"]) is list
                and all(type(run) is list and len(run) == 3
                        for run in row["runs"])):
            return _UniformCellRange._from_runs(
                row["length"], _range_from_document(row["default"], memo), tuple(
                    (start, stop, _range_from_document(cell, memo))
                    for start, stop, cell in row["runs"]))
    raise ValueError("an abstract-range row is not canonical")


class Bounds:
    """An inclusive integer interval whose lower or upper end may be open.

    ``Range`` is the finite interval required by the bit-vector encoder.
    ``Bounds`` is deliberately separate: a one-sided fact such as ``x < 8``
    can bound a Python loop's trip count without claiming that every value of
    ``x`` has a finite-width encoding.
    """

    __slots__ = ("hi", "lo")

    def __init__(self, lo=None, hi=None):
        lo = None if lo is None else int(lo)
        hi = None if hi is None else int(hi)
        if lo is not None and hi is not None and hi < lo:
            raise ValueError(f"bounds from {lo} to {hi}")
        self.lo = lo
        self.hi = hi

    @classmethod
    def of(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, Range):
            return cls(value.lo, value.hi)
        raise TypeError(f"not integer bounds: {value!r}")

    def finite(self):
        if self.lo is None or self.hi is None:
            return None
        return Range(self.lo, self.hi)

    def join(self, other):
        other = Bounds.of(other)
        lo = (None if self.lo is None or other.lo is None
              else min(self.lo, other.lo))
        hi = (None if self.hi is None or other.hi is None
              else max(self.hi, other.hi))
        return Bounds(lo, hi)

    def meet(self, other):
        """Their intersection, or ``None`` when it is empty."""
        other = Bounds.of(other)
        lo = (other.lo if self.lo is None else self.lo
              if other.lo is None else max(self.lo, other.lo))
        hi = (other.hi if self.hi is None else self.hi
              if other.hi is None else min(self.hi, other.hi))
        if lo is not None and hi is not None and hi < lo:
            return None
        return Bounds(lo, hi)

    def __eq__(self, other):
        return (isinstance(other, Bounds)
                and (other.lo, other.hi) == (self.lo, self.hi))

    def __hash__(self):
        return hash((self.lo, self.hi))

    def __repr__(self):
        return f"[{self.lo if self.lo is not None else '-inf'}," \
               f"{self.hi if self.hi is not None else 'inf'}]"


def encoding_width(rng):
    """The width the encoder actually carries ``rng`` at.

    Signed always, and at least one bit. Both halves matter and neither is
    `width_for`'s default: **every term in the encoder is read as a signed
    bitvector**, widening sign-extends and comparison is signed, so a range that
    never goes negative still needs the sign bit or its largest value reads as a
    negative number and a range check fires on it.

    It is a function because it was three copies of an expression and one of them
    was different. `encoder.encode_step` and `residual.enc_width` passed
    `signed=True`; `plan.plan_encoding` did not, so it inferred signedness from
    `lo < 0` and every non-negative range came out **one bit narrower than the
    encoder's**. Measured before fixing: 20 of 20 widths disagreed across four
    models, each by exactly one. Nothing had broken, because the plan's widths
    were not yet read by anything -- and M3's remaining task is precisely to read
    them, which would have narrowed every unsigned name in the population by a
    bit.
    """
    return max(width_for(rng, signed=True), 1)


def width_for(rng, signed=None):
    """A bitvector width injective on ``rng``.

    ``signed=None`` infers it from the range, which is the right default for a
    question about the range and the wrong one for a question about the encoder --
    see `encoding_width`, which is what a caller building a term wants.

    Injective rather than merely large enough: two distinct values of the range
    must have distinct encodings, or the formula would identify states the
    program keeps apart.
    """
    if signed is None:
        signed = rng.lo < 0
    if signed:
        # Two's-complement negative endpoints need the complement's payload
        # bits, not abs(lo)'s: -2**k already fits in k+1 signed bits. Avoid
        # allocating each intervening power of two for wide packed ports.
        return max((~rng.lo).bit_length() if rng.lo < 0 else 0,
                   rng.hi.bit_length() if rng.hi > 0 else 0) + 1
    return max(1, max(0, rng.hi).bit_length())


# ── transformers, one per primitive ──────────────────────────────────

def _primitive_interval(part):
    if part is None:
        return None
    return primitives.PrimitiveInterval(part.lo, part.hi)


def _registered_interval(name, *parts):
    """Apply the single range transformer registered for ``name``."""
    return primitives.primitive(name).range_transform(*(
        _primitive_interval(part) for part in parts))


def _registered_range(name, *parts):
    if name == "arraylit" and not parts:
        # No scalar leaf needs a width. Match the empty product used by
        # concrete-value and occurrence planning, not an unknown scalar hull.
        return _CellRange(())
    got = _registered_interval(name, *parts)
    if got is None or got.lo is None or got.hi is None:
        return None
    return Range(got.lo, got.hi)


def _registered_bounds(name, *parts):
    got = _registered_interval(name, *parts)
    return None if got is None else Bounds(got.lo, got.hi)


def _argument_domain_error(expr, parts):
    """Validate refined registry terms against syntax and abstract values."""
    spec = primitives.primitive(expr.op)
    terms = spec.signature.terms_for(len(expr.args))
    if terms is None:
        return None
    for term, argument, part in zip(terms, expr.args, parts):
        literal = isinstance(argument, ir.Lit)
        error = primitives.argument_domain_error(
            term,
            literal=literal,
            value=argument.value if literal else None,
            bounds=part,
        )
        if error is not None:
            return error
    return None


def of_expression(expr, env, arrays=None):
    """The range of ``expr`` under ``env``, a map from variable to range.

    A variable the environment does not mention has no range, and the result is
    ``None``: a missing range is reported rather than guessed, since a guess
    would be a width that silently wraps.
    """
    if isinstance(expr, ir.Lit):
        return Range(expr.value, expr.value)
    if isinstance(expr, ir.Var):
        return env.get(expr.name)
    return _ranges_of_tree(expr, env, arrays or {}).get(id(expr))


def _bound(value):
    return Bounds.of(value) if value is not None else None


def _bounds_of_op(expr, parts):
    selected = _ordered_choice_range(expr, parts)
    if selected is not None:
        return _bound(selected)
    if expr.op == "len":
        inner = expr.args[0]
        if isinstance(inner, ir.Op) and inner.op == "arraylit":
            return Bounds(len(inner.args), len(inner.args))
        return None
    if expr.op == "mul" and len(expr.args) == 2 \
            and ir.expressions_equal(expr.args[0], expr.args[1]) \
            and parts[0] is not None \
            and parts[0].lo is not None and parts[0].hi is not None:
        value = parts[0]
        squares = (value.lo * value.lo, value.hi * value.hi)
        return Bounds(
            0 if value.lo <= 0 <= value.hi else min(squares),
            max(squares))
    spec = primitives.primitive(expr.op)
    if spec.signature.terms_for(len(parts)) is None:
        return None
    if _argument_domain_error(expr, parts) is not None:
        return None
    return _registered_bounds(expr.op, *parts)


def bounds_of_expression(expr, env, arrays=None, *, _cache=None):
    """Structural integer bounds for ``expr`` under ``env``.

    Unlike :func:`of_expression`, the result may have only an upper or lower
    endpoint. Such a result is sufficient for structural questions such as a
    loop ceiling, but is never used to choose an encoding width.
    """
    # Atomic operands are common in every retained-loop comparison. They need
    # no dependency worklist or per-expression result/cache dictionaries.
    if isinstance(expr, ir.Lit):
        return Bounds(expr.value, expr.value)
    if isinstance(expr, ir.Var):
        return _bound(env.get(expr.name))
    arrays = arrays or {}
    reusable = None if _cache is None else _cache.setdefault(_BOUNDS_CACHE, {})
    result = {}
    bindings = {}
    scheduled = set()
    stack = [(expr, False)]
    while stack:
        current, expanded = stack.pop()
        key = id(current)
        if key in result:
            continue
        if not expanded:
            if key in scheduled:
                continue
            # Sel(arraylit(...), i) reads the selected child's range directly,
            # not just the literal array's hull. Visit those children even if
            # that hull was seen before; their own arithmetic is still reused.
            if (reusable is not None and isinstance(current, (ir.Op, ir.Sel))
                    and not (isinstance(current, ir.Op) and current.op == "arraylit")):
                binding = (key, (arrays.get(None, _MISSING_RANGE_INPUT), tuple(
                    (env.get(name, _MISSING_RANGE_INPUT),
                     arrays.get(name, _MISSING_RANGE_INPUT))
                    for name in _expression_names_cached(current, _cache))))
                found = _range_cache_get(reusable, binding)
                if found is not _NO_RANGE:
                    result[key] = found
                    continue
                bindings[key] = binding
            scheduled.add(key)
            stack.append((current, True))
            for child in reversed(_children(current)):
                if id(child) not in result:
                    stack.append((child, False))
            continue
        if isinstance(current, ir.Lit):
            value = Bounds(current.value, current.value)
        elif isinstance(current, ir.Var):
            value = _bound(env.get(current.name))
        elif isinstance(current, ir.Sel):
            index = result.get(id(current.index))
            array = (arrays.get(_array_name(current.array))
                     or env.get(_array_name(current.array)))
            if _is_cell_range(array):
                value = _bound(_selected_range(array, index))
            elif (isinstance(current.array, ir.Op)
                    and current.array.op == "arraylit"
                    and index is not None
                    and index.lo is not None
                    and index.lo == index.hi
                    and 0 <= index.lo < len(current.array.args)):
                # Graph lowering emits one body copy for each cell of a
                # source-fixed iterable.  The structural bound for that copy
                # is the selected cell, not the hull of the whole array.  Keep
                # this in lockstep with the finite-range rule below so exact
                # closure activations remain exact in either domain.
                value = result.get(id(current.array.args[index.lo]))
            else:
                value = (_bound(array)
                         or result.get(id(current.array)))
        elif isinstance(current, ir.Op):
            value = _bounds_of_op(
                current, tuple(result.get(id(arg)) for arg in current.args))
        else:
            value = None
        result[key] = value
        if key in bindings:
            _range_cache_put(reusable, bindings[key], value)
    return result.get(id(expr))


_NEGATED_COMPARISON = {
    "eq": "ne", "ne": "eq", "lt": "ge", "le": "gt",
    "gt": "le", "ge": "lt",
}
_SWAPPED_COMPARISON = {
    "eq": "eq", "ne": "ne", "lt": "gt", "le": "ge",
    "gt": "lt", "ge": "le",
}


def _join_bound_envs(left, right):
    if left is None:
        return right
    if right is None:
        return left
    if left is right:
        return left
    joined = {}
    for name, value in left.items():
        if name not in right:
            continue
        other = right[name]
        if value is None or other is None:
            joined[name] = None
        elif value is other:
            joined[name] = value
        elif _is_cell_range(value) and _is_cell_range(other):
            joined[name] = _join_range(value, other)
        else:
            joined[name] = _bound(value).join(other)
    return joined


def _refine_name(env, name, constraint):
    current = _bound(env.get(name)) or Bounds()
    narrowed = current.meet(constraint)
    if narrowed is None:
        return None
    if (name in env and current.lo == narrowed.lo
            and current.hi == narrowed.hi):
        return env
    out = dict(env)
    out[name] = narrowed
    return out


def _selected_cell(expr, env, cache=None):
    if not isinstance(expr, ir.Sel) or not isinstance(expr.array, ir.Var):
        return None
    array = env.get(expr.array.name)
    index = bounds_of_expression(expr.index, env, _cache=cache)
    if (_is_cell_range(array) and index is not None and index.lo is not None
            and index.lo == index.hi and 0 <= index.lo < _cell_length(array)):
        value = _cell_at(array, index.lo)
        if not _is_cell_range(value):
            return expr.array.name, index.lo, value
    return None


def _assume_affine(form, op, env):
    """Project one linear guard onto its operands, without solving a system.

    For ``a*x + rest`` in [L,U], subtract the *other* operands' bounds
    before dividing by a. All projections use the original environment;
    finite totals and open-end counts make this linear in the operand count.
    No fixed-point iteration or Cartesian product of intervals is needed.
    """
    constant, coefficients = form
    constraint = {"lt": Bounds(None, -1), "le": Bounds(None, 0),
                  "gt": Bounds(1, None), "ge": Bounds(0, None),
                  "eq": Bounds(0, 0)}[op]
    total_lo = total_hi = constant
    open_lo = open_hi = 0
    terms = []
    for name, coefficient in coefficients.items():
        value = _bound(env.get(name)) or Bounds()
        lo, hi = ((value.lo, value.hi) if coefficient > 0 else (value.hi, value.lo))
        lo = None if lo is None else coefficient * lo
        hi = None if hi is None else coefficient * hi
        total_lo += 0 if lo is None else lo
        total_hi += 0 if hi is None else hi
        open_lo += lo is None
        open_hi += hi is None
        terms.append((name, coefficient, value, lo, hi))
    possible = Bounds(None if open_lo else total_lo, None if open_hi else total_hi)
    if possible.meet(constraint) is None:
        return None
    out = None
    for name, coefficient, value, term_lo, term_hi in terms:
        rest_lo = (None if open_lo - (term_lo is None)
                   else total_lo - (0 if term_lo is None else term_lo))
        rest_hi = (None if open_hi - (term_hi is None)
                   else total_hi - (0 if term_hi is None else term_hi))
        lo = (None if constraint.lo is None or rest_hi is None
              else constraint.lo - rest_hi)
        hi = (None if constraint.hi is None or rest_lo is None
              else constraint.hi - rest_lo)
        if coefficient < 0:
            lo, hi = hi, lo
        lo = None if lo is None else -((-lo) // coefficient)
        hi = None if hi is None else hi // coefficient
        # Integer division can expose an empty interval, e.g. 2*x == 1.
        if lo is not None and hi is not None and lo > hi:
            return None
        narrowed = value.meet(Bounds(lo, hi))
        if narrowed is None:
            return None
        if narrowed != value:
            if out is None:
                out = dict(env)
            out[name] = narrowed
    return env if out is None else out


def value_preserving_operand(value, env, *, cache=None):
    """Expose only conversions that preserve every current numeric value."""
    while isinstance(value, ir.Op):
        if value.op == "toint" and len(value.args) == 1:
            value = value.args[0]
            continue
        if value.op not in {"uwrap", "swrap"} or len(value.args) != 2:
            break
        inner, width = value.args
        if not isinstance(width, ir.Lit) or type(width.value) is not int or width.value <= 0:
            break
        bound = bounds_of_expression(inner, env, _cache=cache)
        bits = width.value - (value.op == "swrap")
        low = -(1 << bits) if value.op == "swrap" else 0
        # Width alone cannot justify dropping a wrap or sign reinterpretation.
        if bound is None or bound.lo is None or bound.hi is None or not (
                low <= bound.lo <= bound.hi < (1 << bits)):
            break
        value = inner
    return value


def truth_preserving_operand(value, env, *, cache=None):
    """Expose scalar conversions preserving zero versus nonzero, not value.

    A sized Boolean can become -1 at signed width one, but never zero. An
    arbitrary narrowed integer has no such guarantee. Share this distinction
    between interval assumptions and conditional facts carried across joins.
    """
    if isinstance(value, ir.Op) and value.op == "toint" and len(value.args) == 1:
        return value.args[0]
    converted = value
    while (isinstance(converted, ir.Op) and converted.op in {"uwrap", "swrap"}
           and len(converted.args) == 2 and isinstance(converted.args[1], ir.Lit)
           and type(converted.args[1].value) is int and converted.args[1].value > 0):
        converted = converted.args[0]
    if isinstance(converted, ir.Op) and converted.op == "toint" and len(converted.args) == 1:
        original = converted.args[0]
        domain = bounds_of_expression(original, env, _cache=cache)
        if (domain is not None and domain.lo is not None and domain.hi is not None
                and 0 <= domain.lo <= domain.hi <= 1):
            return original
    return value


def _assume_comparison(expr, truth, env, cache=None):
    op = expr.op if truth else _NEGATED_COMPARISON[expr.op]
    left, right = (value_preserving_operand(value, env, cache=cache) for value in expr.args)
    if op != "ne":
        form = affine_form(ir.Op("sub", left, right))
        if form is not None and not any(_is_cell_range(env.get(name)) for name in form[1]):
            return _assume_affine(form, op, env)
    cell = _selected_cell(left, env, cache)
    if not isinstance(left, ir.Var) and cell is None and (
            isinstance(right, ir.Var) or _selected_cell(right, env, cache) is not None):
        left, right = right, left
        op = _SWAPPED_COMPARISON[op]
        cell = _selected_cell(left, env, cache)
    rhs = bounds_of_expression(right, env, _cache=cache)
    if rhs is None:
        return env
    if not isinstance(left, ir.Var) and cell is None:
        # A compound value need not be invertible to refute its comparison.
        # E.g. a bounded ``k - 1`` can never exceed an array's last index.
        # Affine guards were handled above. For other compound expressions,
        # check contradictory endpoints without attempting to invert them.
        lhs = bounds_of_expression(left, env, _cache=cache)
        if lhs is None:
            return env
        lower_separated = lhs.lo is not None and rhs.hi is not None
        upper_separated = lhs.hi is not None and rhs.lo is not None
        impossible = (
            (op == "lt" and lower_separated and lhs.lo >= rhs.hi)
            or (op == "le" and lower_separated and lhs.lo > rhs.hi)
            or (op == "gt" and upper_separated and lhs.hi <= rhs.lo)
            or (op == "ge" and upper_separated and lhs.hi < rhs.lo)
            or (op == "eq" and (
                (lower_separated and lhs.lo > rhs.hi)
                or (upper_separated and lhs.hi < rhs.lo)))
            or (op == "ne" and lhs.lo is not None
                and lhs.lo == lhs.hi == rhs.lo == rhs.hi)
        )
        return None if impossible else env
    def refine(constraint):
        if cell is None:
            return _refine_name(env, left.name, constraint)
        name, index, value = cell
        narrowed = Bounds.of(value).meet(constraint)
        if narrowed is None:
            return None
        if narrowed.lo == value.lo and narrowed.hi == value.hi:
            return env
        # The index is a singleton in this exact environment. Constrain only
        # that cell, never the hull shared by other array elements.
        return {**env, name: _store_cell(env[name], index, narrowed.finite())}

    if op == "lt" and rhs.hi is not None:
        return refine(Bounds(None, rhs.hi - 1))
    if op == "le" and rhs.hi is not None:
        return refine(Bounds(None, rhs.hi))
    if op == "gt" and rhs.lo is not None:
        return refine(Bounds(rhs.lo + 1, None))
    if op == "ge" and rhs.lo is not None:
        return refine(Bounds(rhs.lo, None))
    if op == "eq":
        return refine(rhs)
    if op == "ne":
        current = _bound(env.get(left.name) if cell is None else cell[2])
        if (current is not None and current.lo is not None and current.lo == current.hi
                and rhs.lo == rhs.hi == current.lo):
            return None
        if current is not None and rhs.lo is not None and rhs.lo == rhs.hi:
            # Excluding an endpoint tightens the interval without introducing
            # a disjunction. An interior exclusion keeps the original hull.
            if current.lo == rhs.lo:
                return refine(Bounds(rhs.lo + 1, None))
            if current.hi == rhs.lo:
                return refine(Bounds(None, rhs.lo - 1))
    return env


_NOT_A_BIT_TEST = object()


def _assume_single_bit_test(expr, truth, env, cache=None):
    """Refine one selected bit of an integer, including lowered packed reads.

    Values with one bit set form periodic blocks.  Their interval hull can
    still tighten the two ends of a finite carrier exactly; values between the
    ends may include the other polarity, which is conservative interval
    abstraction rather than an invented path fact.
    """
    if not isinstance(expr, ir.Op) or len(expr.args) != 2:
        return _NOT_A_BIT_TEST

    def constant(value):
        bound = bounds_of_expression(value, env, _cache=cache)
        return bound.lo if bound is not None and bound.lo == bound.hi else None

    if expr.op in {"uwrap", "swrap"} and constant(expr.args[1]) == 1:
        # Both signed and unsigned one-bit carriers are nonzero exactly
        # when their low bit is set. Wider wraps cannot be stripped this way.
        variable, bit_index = expr.args[0], 0
    elif expr.op == "and":
        variable, mask_expr = expr.args
        mask = constant(mask_expr)
        if mask is None:
            variable, mask_expr = mask_expr, variable
            mask = constant(mask_expr)
        if mask is None or mask <= 0 or mask & (mask - 1):
            return _NOT_A_BIT_TEST
        bit_index = mask.bit_length() - 1
    else:
        return _NOT_A_BIT_TEST
    while isinstance(variable, ir.Op) and variable.op == "shr" and len(variable.args) == 2:
        shift = constant(variable.args[1])
        if shift is None or shift < 0:
            return _NOT_A_BIT_TEST
        bit_index += shift
        variable = variable.args[0]
    if not isinstance(variable, ir.Var):
        return _NOT_A_BIT_TEST
    current = _bound(env.get(variable.name))
    if current is None:
        return _NOT_A_BIT_TEST
    finite = current.finite()
    if finite is None:
        return _NOT_A_BIT_TEST
    # A bit beyond the finite interval's magnitude is its sign extension.
    # Avoid constructing a huge mask merely because a constant shift is huge.
    if bit_index >= max(finite.lo.bit_length(), finite.hi.bit_length()):
        return _refine_name(env, variable.name, Bounds(None, -1) if truth else Bounds(0, None))
    bit = 1 << bit_index
    period = 2 * bit

    def first_at_or_after(value, wanted):
        offset = value % period
        if wanted:
            return value if offset >= bit else value + bit - offset
        return value if offset < bit else value + period - offset

    def last_at_or_before(value, wanted):
        offset = value % period
        if wanted:
            return value if offset >= bit else value - offset - 1
        return value if offset < bit else value - (offset - bit) - 1

    lo = first_at_or_after(finite.lo, truth)
    hi = last_at_or_before(finite.hi, truth)
    if lo > hi:
        return None
    return _refine_name(env, variable.name, Bounds(lo, hi))


def _constant_truth(expression, range_of):
    """A decision forced by its occurrence carriers, or no conclusion.

    The caller supplies sound ranges at this exact expression occurrence.
    This is the same predicate refinement used by the range planner, with no
    solver, input sample, normal-completion premise or source-level guess.
    """
    domain = range_of(expression)
    if domain.lo == domain.hi == 0:
        return False
    if domain.lo > 0 or domain.hi < 0:
        return True
    entry, pending, seen = {}, [expression], set()
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, ir.Var):
            entry[node.name] = range_of(node)
        else:
            pending.extend(_children(node))
    if assume(expression, False, entry) is None:
        return True
    if assume(expression, True, entry) is None:
        return False
    return None


def assume(expr, truth, env):
    """Bounds that hold on the normal path where ``expr`` has ``truth``.

    ``None`` means the path is infeasible. Unsupported predicates return the
    incoming environment unchanged; the analysis refines only what it proves.
    """
    # Normalize once at the public boundary.  Compiler-generated guards are
    # nested Boolean trees; recursively rebuilding the complete environment
    # for every node made one finite loop quadratic in guard depth and live
    # variables.  The recursive transformer below is functional: unchanged
    # paths may share this private mapping, while every refinement copies it.
    normalized = {name: _bound(value) for name, value in env.items()}
    return _assume(expr, truth, normalized)


def assume_bounds(expr, truth, env: Mapping[str, Bounds | None]):
    """Refine an already normalized structural environment.

    Lowering keeps ``Bounds`` separately from finite encoder ranges. Repeated
    guards need not normalize every unrelated live binding again. Copy the
    mapping so even an unchanged result remains owned by this branch; bound
    values have the same sharing semantics as ``assume``.
    """
    return _assume(expr, truth, dict(env))


def _assume(expr, truth, env, cache=None):
    if isinstance(expr, ir.Lit):
        # A literal used directly as a predicate is semantically Boolean.
        # Integer truthiness reaches this analysis only through the explicit
        # ``truth`` primitive below; treating ``1`` as ``True`` here would
        # reintroduce the carrier conflation the IR type boundary rejects.
        if type(expr.value) is not bool:
            return env
        return env if expr.value is bool(truth) else None
    if isinstance(expr, ir.Op) and expr.op == "not" and len(expr.args) == 1:
        return _assume(expr.args[0], not truth, env, cache)
    if isinstance(expr, ir.Op) and expr.op == "truth" and len(expr.args) == 1:
        inner = expr.args[0]
        converted = truth_preserving_operand(inner, env, cache=cache)
        if converted is not inner:
            return _assume(ir.Op("truth", converted), truth, env, cache)
        # Lowering deliberately makes every source truth test explicit, even
        # when its operand is already the Boolean result of a registered
        # comparison.  Preserve the predicate structure in that case so its
        # numeric facts can still prune a branch.  Only a genuine scalar-to-
        # Boolean conversion is rewritten as a comparison with integer zero.
        if isinstance(inner, ir.Lit) and type(inner.value) is bool:
            return _assume(inner, truth, env, cache)
        if isinstance(inner, ir.Op):
            try:
                result = primitives.primitive(inner.op).signature.result
            except primitives.PrimitiveRegistryError:
                result = None
            if result is primitives.BOOLEAN:
                return _assume(inner, truth, env, cache)
            narrowed = _assume_single_bit_test(inner, truth, env, cache)
            if narrowed is not _NOT_A_BIT_TEST:
                return narrowed
        relation = "ne" if truth else "eq"
        return _assume(ir.Op(relation, inner, ir.Lit(0)), True, env, cache)
    if isinstance(expr, ir.Op) and expr.op in {"land", "lor"} \
            and len(expr.args) == 2:
        left, right = expr.args
        both = (expr.op == "land" and truth) or (
            expr.op == "lor" and not truth)
        if both:
            first = _assume(left, truth, env, cache)
            return None if first is None else _assume(right, truth, first, cache)
        # The other polarity is a disjunction of the two ways the Boolean can
        # decide. Join alternatives; intersecting them would invent facts.
        left_state = _assume(left, truth, env, cache)
        right_state = _assume(right, truth, env, cache)
        return _join_bound_envs(left_state, right_state)
    if isinstance(expr, ir.Op) and expr.op in _NEGATED_COMPARISON \
            and len(expr.args) == 2:
        return _assume_comparison(expr, truth, env, cache)
    if isinstance(expr, ir.Var):
        current = _bound(env.get(expr.name))
        if current is not None and current.lo is not None and current.lo == current.hi:
            return env if bool(current.lo) is bool(truth) else None
    return env


def trip_range(expr, env):
    """Finite range of Python's one-argument ``range(expr)`` trip count."""
    value = bounds_of_expression(expr, env)
    if value is None or value.hi is None:
        return None
    return Range(0 if value.lo is None else max(0, value.lo),
                 max(0, value.hi))


def _ordered_choice(expr):
    """Recognize a scalar comparison selecting its own operands.

    The arms may be compound expressions. Refining only named variables then
    misses the order that this selection establishes, e.g. max(1, x // d).
    This is an expression identity, not a source-builtin or task-name rule.
    """
    if expr.op != "ite" or len(expr.args) != 3:
        return None
    guard, yes, no = expr.args
    if not isinstance(guard, ir.Op) or guard.op not in {"lt", "le", "gt", "ge"}:
        return None
    if len(guard.args) != 2:
        return None

    left, right = guard.args
    maximum = guard.op in {"gt", "ge"}
    if ir.expressions_equal(yes, left) and ir.expressions_equal(no, right):
        return max if maximum else min
    if ir.expressions_equal(yes, right) and ir.expressions_equal(no, left):
        return min if maximum else max
    return None


def _ordered_choice_range(expr, parts):
    choose = _ordered_choice(expr)
    if choose is None:
        return None
    left, right = parts[1:]
    if (left is None or right is None
            or any(end is None for end in (left.lo, left.hi, right.lo, right.hi))):
        return None
    return Range(choose(left.lo, right.lo), choose(left.hi, right.hi))


def _of_op(expr, parts):
    selected = _ordered_choice_range(expr, parts)
    if selected is not None:
        return selected
    if expr.op == "len":
        inner = expr.args[0]
        if isinstance(inner, ir.Op) and inner.op == "arraylit":
            n = len(inner.args)
            return Range(n, n)
        return None
    if expr.op == "mul" and len(expr.args) == 2 \
            and ir.expressions_equal(expr.args[0], expr.args[1]) \
            and parts[0] is not None:
        value = parts[0]
        squares = (value.lo * value.lo, value.hi * value.hi)
        return Range(
            0 if value.lo <= 0 <= value.hi else min(squares),
            max(squares))
    spec = primitives.primitive(expr.op)
    if spec.signature.terms_for(len(parts)) is None:
        return None
    if _argument_domain_error(expr, parts) is not None:
        return None
    return _registered_range(expr.op, *parts)


def _ranges_of_tree(root, env, arrays):
    """Range every subexpression without using the Python call stack.

    If-conversion naturally builds long nested ``ite`` expressions.  Their
    depth is a property of the represented program, not a reason for range
    analysis to crash at Python's recursion limit.  A postorder worklist also
    computes a shared subterm once rather than once through every parent.
    """
    result = {}
    scheduled = set()
    stack = [(root, False)]
    while stack:
        expr, expanded = stack.pop()
        key = id(expr)
        if key in result:
            continue
        if not expanded:
            if key in scheduled:
                continue
            scheduled.add(key)
            stack.append((expr, True))
            for child in reversed(_children(expr)):
                if id(child) not in result:
                    stack.append((child, False))
            continue
        if isinstance(expr, ir.Lit):
            value = Range(expr.value, expr.value)
        elif isinstance(expr, ir.Var):
            value = env.get(expr.name)
        elif isinstance(expr, ir.Sel):
            index = result.get(id(expr.index))
            array = (arrays.get(_array_name(expr.array))
                     or result.get(id(expr.array)))
            if _is_cell_range(array):
                value = _selected_range(array, index)
            elif (isinstance(expr.array, ir.Op)
                    and expr.array.op == "arraylit"
                    and index is not None and index.lo == index.hi
                    and 0 <= index.lo < len(expr.array.args)):
                # A bounded loop over a literal collection emits one graph
                # body per element.  Preserve the selected cell's exact range
                # instead of widening every copy to the whole collection.
                value = result.get(id(expr.array.args[index.lo]))
            else:
                value = array
        elif isinstance(expr, ir.Op):
            value = _of_op(
                expr, tuple(result.get(id(arg)) for arg in expr.args))
        else:
            value = None
        result[key] = value
    return result


def _array_name(expr):
    return expr.name if isinstance(expr, ir.Var) else None


def unranged_subterms(expr, env, arrays=None):
    """Subterms of ``expr`` that have no range, in preorder.

    A bounded result does not make an expression encodable. ``x & 0xFF`` lies
    in ``[0,255]`` whatever ``x`` is, which is what bounds a loop, but the
    encoder still has to represent ``x`` itself at some width, and a width
    chosen for a value with no range would wrap where the source does not. The
    wrapping is not always harmless either: truncating before a mask changes
    nothing, while truncating before a modulus changes the answer. A caller
    that needs widths therefore asks this rather than reading the result range.
    """
    ranges = _ranges_of_tree(expr, env, arrays or {})
    out = []
    seen = set()
    stack = [expr]
    while stack:
        current = stack.pop()
        key = id(current)
        if key in seen:
            continue
        seen.add(key)
        if ranges.get(key) is None:
            out.append(current)
        stack.extend(reversed(_children(current)))
    return out


def _children(expr):
    if isinstance(expr, ir.Op):
        return expr.args
    if isinstance(expr, ir.Sel):
        return (expr.array, expr.index)
    return ()


# ── state across steps ───────────────────────────────────────────────

def range_of_value(value, _active=None):
    """Smallest interval containing every integer/Boolean leaf of a fixed value.

    Recursive arrays share one abstract cell range, so their entry fact is the
    join of the leaf ranges. Boolean facts use 0/1 in the interval domain;
    their distinct semantic type remains on the procedure. Omitting a Boolean
    initial value would let a later write exclude it from the state envelope.
    An empty array is the zero-cell product; cyclic or other values establish
    no numeric fact.
    """
    if type(value) in (int, bool):
        return Range(value, value)
    from .uniform_array import UniformArray
    if type(value) is UniformArray:
        bounds = value.base_bounds
        default = (Range(*bounds) if bounds is not None
                   else range_of_value(value.default, _active))
        if default is None:
            return None
        rows = []
        for index, item in value.sparse_items():
            child = range_of_value(item, _active)
            if child is None:
                return None
            rows.append((index, child))
        return _UniformCellRange(len(value), default, rows)
    if type(value) not in (list, tuple):
        return None
    if not value:
        return _CellRange(())
    active = set() if _active is None else _active
    identity = id(value)
    if identity in active:
        return None
    active.add(identity)
    try:
        result = None
        for item in value:
            child = range_of_value(item, active)
            if child is None:
                return None
            result = child if result is None else result.join(child)
        return result
    finally:
        active.remove(identity)


# Kept private as the state-entry spelling used by older internal callers.
_initial_range = range_of_value
_UNVALIDATED_GRAPH = object()


def _state_product_range(value, active=None):
    """Keep fixed initial cells, compressing repeated values into runs."""
    if type(value) not in (list, tuple):
        return range_of_value(value)
    active = set() if active is None else active
    if id(value) in active:
        return None
    active.add(id(value))
    try:
        cells = tuple(_state_product_range(child, active) for child in value)
        if any(cell is None for cell in cells):
            return None
        return _UniformCellRange(len(cells), cells[0] if cells else Range(0, 0), enumerate(cells))
    finally:
        active.remove(id(value))


def initial_ranges(proc, *, products=False):
    """Exact finite hulls of state values present at the step boundary."""

    entry_dead = set(getattr(proc, "entry_dead", ()))
    out = {}
    for name, value in getattr(proc, "initial", {}).items():
        if name in entry_dead or name not in proc.state:
            continue
        seeded = _state_product_range(value) if products else _initial_range(value)
        if seeded is not None:
            out[name] = seeded
    return out


def fixpoint(proc, env, rounds=STATE_ROUNDS, limit=STATE_MAGNITUDE, *,
             _graph=_UNVALIDATED_GRAPH, _state_planning=False,
             _retain_products=False):
    """Ranges of the state variables that hold from one step to the next.

    A step reads the state it wrote on the previous step, so the range of a
    state variable is not given by the source of one step: it is the least
    interval closed under the step, an interval containing where the run
    starts and closed under running once more,

        I ⊇ initial   and   I ⊇ post(I).

    This iterates that until it stops moving. Each round starts from the
    state, not from what the last round left inside the step, so what
    accumulates is the state and not the locals.

    A variable whose interval keeps growing has no bound this can state. That
    is reported by leaving it out, and everything computed from it then has no
    range either, so a caller refuses rather than picking a number. A state
    that grows without limit is exactly the case where an unrolling would be
    unsound, and the appendix's survey found such fields in real models.
    """
    # Reading ``proc.graph`` verifies the sealed graph/body pair.  This pure
    # analysis never exposes or mutates either object, so one validation owns
    # all of its abstract iterations; re-hashing the complete procedure in
    # every round is redundant and can dominate large finite programs.
    graph = (getattr(proc, "graph", None)
             if _graph is _UNVALIDATED_GRAPH else _graph)
    # State planning feeds the public output-domain proof. Preserve the
    # sealed loop CFG's guarded cell facts, not merely finite term hulls:
    # collapsing separate array banks here cannot be undone at that boundary.
    flows = {_SOURCE_PATH_RANGES: True} if _state_planning else {}
    point_product = (_POINTWISE_LOOP_COMBINATION_LIMIT + 1
                     if _state_planning else 1)
    base = dict(env)
    for name, seeded in initial_ranges(proc, products=_state_planning).items():
        base.setdefault(name, seeded)
    # An entry-dead field has a canonical value only to make the IR state vector
    # total.  Its source value is absent, so the filler is not a range fact.  The
    # CFG proof says the step defines it before use and on every normal exit;
    # ``post`` below therefore derives its range from the real definition.
    held = {
        name: base[name]
        for name in proc.state if name in base
    }
    growing, before_growing = set(), set()
    for _ in range(rounds):
        after = _procedure_post_with_graph(
            proc, {**base, **held}, graph, _flows=flows,
            _point_product=point_product) or {}
        moved = {}
        for name in proc.state:
            was = held.get(name)
            now = after.get(name)
            if now is None:
                if was is not None:
                    moved[name] = was
            elif was is None:
                # The first bound this variable has, which is what the step wrote
                # into it. Nothing was assumed before it.
                moved[name] = now
            else:
                moved[name] = was.join(now)
        growing, before_growing = {
            name for name in proc.state
            if moved.get(name) != held.get(name)}, growing
        held = moved
        if not growing:
            # The intervals closed, so what the step computes from them is
            # bounded too and is reported alongside. Through the same exit as
            # every other outcome: this used to return here directly, and the
            # gate below was therefore never asked on the path most models take,
            # since converging quickly is the common case. Five models kept an
            # interval a run leaves for exactly that reason, and the population
            # sweep is what said so, after the delay-chain regression passed.
            break
        # The limit is against divergence, not against width. A state masked
        # to two hundred and fifty six bits reaches its interval in one pass
        # and stays there; stopping the first time an interval is large would
        # call that unbounded, when what is unbounded is an interval that is
        # still moving after it was already large.
        if any(held[k].magnitude > limit for k in growing & before_growing):
            break
    settled = {k: v for k, v in held.items() if k not in growing}
    # And now the part that was missing: an interval that stopped moving is not an
    # interval that is closed. `growing` names what moved on the last round, and a
    # change travelling down a chain of copies arrives at the far end long after it
    # left, so the tail of such a chain sits still while the change is still coming.
    # Over the population ten models claimed an interval a run leaves, eight of them
    # `[0,0]`, which is not a near miss: it says the variable is constantly zero, so
    # its symbol is one bit wide and any witness needing it non-zero is unsatisfiable.
    # Caller-supplied state ranges seeded the iteration; they are not permanent
    # invariants. Reintroducing a rejected state bound through `env` makes the
    # closure loop reject the same absent field forever. The final transfer
    # must likewise use only the state bounds that actually survived closure.
    state_names = set(proc.state)
    outside = {name: rng for name, rng in env.items() if name not in state_names}
    settled = _closed(
        proc, outside, settled, graph, flows, point_product)
    out = _procedure_post_with_graph(
        proc, {**outside, **settled}, graph, _flows=flows,
        _point_product=point_product) or {}
    # A one-step post-state is not an inductive interval (it can, for example,
    # exclude Init). Only `settled` may publish state bounds; retain the local
    # and output facts derived from it without reviving rejected state bounds.
    out = {name: rng for name, rng in out.items() if name not in state_names}
    out.update(settled)
    return out if _retain_products else _plain_ranges(out)


def bounded(proc, env, bound, *, _graph=_UNVALIDATED_GRAPH,
            _state_planning=False, _retain_products=False, _checkpoint=None):
    """Ranges that cover a run of ``bound`` steps, and claim nothing beyond it.

        R(0) = Init          R(t+1) = post#(R(t))          for t < bound

    and what a variable is encoded with is the join of its iterates, since one width has
    to hold every step of the unrolling. There is no fixpoint here: nothing iterates to
    closure, nothing is declared settled, and there is no divergence limit, because
    nothing is claimed about step ``bound + 1``. That is the shape the verification
    actually has -- a bounded instance is bound before evidence is gathered, and the
    ranges are a property of *that* instance rather than of the step in general.

    It buys coverage, and that is the point rather than a side effect. `self.x = self.x
    + 1` has no interval closed under the step, so `fixpoint` leaves it out and
    everything computed from it loses its range; over ten steps from a known start it is
    `[0,10]`. The fixpoint is answering a harder question than the verification asks.

    It is also **strictly narrower where both answer**, so a loop bound resolved from
    these unrolls no further than one resolved from the fixpoint, and no less far.

    What it needs that the fixpoint does not is the **starting state**. R(0) is Init, so
    a variable whose starting value nothing could read has no R(0), and by B1a's reading
    absence is unbounded rather than an oversight: the join is absent too, and a caller
    refuses instead of picking a number. The fixpoint sidesteps this by starting from
    nothing and asking what the step can write, which is true of every run and is why it
    is the fallback where no bound is known.

    Soundness and capability stay separate, which the earlier plan for this got wrong.
    Ten steps can reach a width nothing can build: the interval here covers every
    bounded execution, and whether it can be encoded is a capability question with its
    own answer.
    """
    if bound is None or bound < 0:
        raise ValueError(f"a bound of {bound!r}")
    graph = (getattr(proc, "graph", None)
             if _graph is _UNVALIDATED_GRAPH else _graph)
    flows = {_SOURCE_PATH_RANGES: True} if _state_planning else {}
    if _checkpoint is not None:
        _checkpoint()
        flows[_RANGE_CHECKPOINT] = _checkpoint
    point_product = (_POINTWISE_LOOP_COMBINATION_LIMIT + 1
                     if _state_planning else 1)
    start = dict(env)
    for name, seeded in initial_ranges(proc, products=_state_planning).items():
        start.setdefault(name, seeded)
    # Entry-dead representation fillers are likewise absent from R(0).  Their
    # first real definition supplies R(1), rather than a made-up zero widening
    # every bounded range.
    held = {
        name: start[name]
        for name in proc.state if name in start
    }
    accelerated = (_bounded_state_envelopes(
                       proc, {**start, **held}, bound, graph, flows)
                   if _state_planning else {})
    held.update(accelerated)
    reached = dict(held)
    stable_after = None
    for _ in range(bound):
        after = _procedure_post_with_graph(
            proc, {**start, **held}, graph, _flows=flows,
            _point_product=point_product)
        if after is None:
            break
        moved = {name: rng for name, rng in
                 ((name, after.get(name)) for name in proc.state)
                 if rng is not None}
        # These envelopes already cover every finite iterate through ``bound``.
        # Presenting them from the first pass lets dependent arrays and locals
        # reach their cover without replaying a scalar counter once per slot.
        moved.update(accelerated)
        # R(t+1) = R(t) ends the sequence, and this is arithmetic rather than a
        # judgement. `start` is fixed and `_post` is a function of it and of the
        # iterate, so R(t+1) = R(t) gives R(t+2) = post#(R(t+1)) = post#(R(t)) = R(t+1),
        # and every remaining iterate equals this one: the join is already complete and
        # the rest of the loop would add nothing.
        #
        # Not the fixpoint's early exit, which stopped when nothing *grew* and needed
        # `_closed` behind it because a chain of copies keeps moving after the far end
        # sits still. This stops only on an iterate identical to the last, and it is
        # not a claim about time past `bound` -- nothing here makes one.
        #
        # It is also most of the cost. Without it every model paid `bound` passes of
        # `_post` where the fixpoint pays the two or three its convergence takes, and
        # bound-indexed ranges came out **five times slower** than the fixpoint over the
        # population -- 280s against 54s -- which is the reverse of what P6 predicted.
        # The models that need all `bound` passes are the ones whose state really does
        # keep moving, `self.x = self.x + 1` among them, and those are exactly the ones
        # this analysis exists to bound.
        if moved == held:
            # The final projection below asks for this exact postcondition:
            # ``reached`` still equals ``held`` and this call just computed
            # ``post(start | held)``.  Retaining it avoids executing a large
            # retained-loop procedure twice merely to recover its local and
            # output ranges.
            stable_after = after
            break
        held = moved
        for name, rng in held.items():
            was = reached.get(name)
            reached[name] = rng if was is None else was.join(rng)
        # A variable that had a range and lost one is unbounded from here on, and the
        # join of an interval with nothing is nothing. Dropping it is what says so.
        for name in [n for n in reached if n in proc.state and n not in held]:
            reached.pop(name)
    out = (dict(stable_after) if stable_after is not None else
           (_procedure_post_with_graph(
               proc, {**env, **reached}, graph, _flows=flows,
               _point_product=point_product) or {}))
    out.update(reached)
    return out if _retain_products else _plain_ranges(out)


def unproved(proc, env, _graph=_UNVALIDATED_GRAPH, _flows=None,
             _point_product=1):
    """State variables whose claimed interval an actual run can leave.

    Two conditions, and the first version checked only the second, which is a gate
    that passes an interval no run is ever inside. What makes an interval sound for a
    width is

        Init subset of I    and    post#(I) subset of I,

    and induction over the steps then puts every reachable state in I. Closure alone
    is not enough: for a step ``x := 5`` on a variable that starts at 0, the interval
    ``[5,5]`` is closed under ``post#`` and excludes the state the run begins in, so a
    width taken from it cannot carry the first step.

    What this still rests on and does not check: that ``_post`` over-approximates the
    concrete step and is monotone. Without monotonicity the induction does not go
    through, since it needs ``post(R_t) subset of post#(R_t) subset of post#(I)`` for
    every ``R_t`` inside ``I``. The transformers are exhaustively probed on small
    intervals and property tests over them are owed; that is where this assumption
    lives, not here.

    The gate, and it has to be written the right way round or it certifies what it
    was added to catch. ``post#`` is evaluated over exactly what is claimed, so a
    variable reading something unclaimed **drops out** of the result rather than
    coming back with a wider interval, and there it compares equal to nothing rather
    than unequal to its interval. **Absent has to be read as unbounded**, which is the
    strongest possible disagreement with an interval.

    Exact rather than conservative, which is worth knowing since the obvious objection
    is that an unwritten variable would be reported. It would not: ``_post`` leaves a
    variable it does not write alone, so absence can only be a write whose value had no
    range. All ten of the population's failures are that kind.

    The initial state is not seeded into ``post#`` either, and that is separate from
    containing it. Seeding is what let an unproved bound survive: a variable reading a
    still-growing one still got a range, because the growing one was re-supplied with
    the value ``reset`` gave it. So ``Init`` is compared against the interval and not
    handed to the step.
    """
    graph = (getattr(proc, "graph", None)
             if _graph is _UNVALIDATED_GRAPH else _graph)
    claimed = {name: rng for name, rng in env.items() if name in proc.state}
    if not claimed:
        return []
    out = []
    entry_dead = set(getattr(proc, "entry_dead", ()))
    for name, rng in claimed.items():
        # Where the source run has a value, it has to be inside the interval.
        # An entry-dead IR filler is not such a value.  Otherwise an array's
        # cells are each a starting value, since the interval joins the cells.
        if name in entry_dead:
            continue
        start = proc.initial.get(name)
        initial = (_state_product_range(start) if _is_cell_range(rng)
                   else range_of_value(start))
        if initial is not None and not _range_contains(rng, initial):
            out.append(name)
    outside = {name: rng for name, rng in env.items() if name not in proc.state}
    after = _procedure_post_with_graph(
        proc, {**outside, **claimed}, graph, _flows=_flows,
        _point_product=_point_product)
    if after is None:
        return out
    for name, rng in claimed.items():
        if name in out:
            continue
        got = after.get(name)
        if got is None or not _range_contains(rng, got):
            out.append(name)
    return out


def _range_contains(outer, inner):
    if not _is_cell_range(outer):
        return outer.lo <= inner.lo and inner.hi <= outer.hi
    # Equal scalar hulls do not establish containment of separate cells.
    # Joining products checks every represented component, including shape.
    return _join_range(outer, inner) == outer


def _closed(proc, env, settled, graph, flows=None, point_product=1):
    """``settled`` with every interval one more step leaves taken out.

    Iterated, because dropping one variable takes the bound off whatever reads it: the
    step no longer has a range for that read, so the next round finds the reader
    unproved too. It ends because the set only shrinks.

    Dropping rather than widening. A variable with no interval has no width, so the
    encoder refuses the step and says which name it refused for, which is the honest
    answer and is what the caller can act on. Widening to something arbitrary would
    keep the model encodable at a width nothing justifies, which is the fault this is
    here to remove.
    """
    claimed = dict(settled)
    while claimed:
        lost = unproved(
            proc, {**env, **claimed}, graph, flows, point_product)
        if not lost:
            break
        for name in lost:
            claimed.pop(name, None)
    return claimed


def _join_envs(left, right, cache=None):
    """What holds after either of two paths: the join, variable by variable.

    A variable one path cannot bound is a variable that has no bound after
    them, whatever the other path made of it, so only what both sides hold
    survives. Keeping the bounded side would report a bound that holds on one
    path and not on the run.
    """
    if left is right or left == right:
        return left
    joined = None if cache is None else cache.setdefault(
        _RANGE_JOIN_CACHE, {})
    return {
        name: (rng if rng is right[name] else
               _join_range(rng, right[name], joined))
        for name, rng in left.items() if name in right
    }


_NO_NORMAL = object()
_NO_RANGE = object()
_VALUE_RANGE_CACHE = object()
_BOUNDS_CACHE = object()
_EXPRESSION_NAME_CACHE = object()
_FINITE_ASSUMPTION_CACHE = object()
_RANGE_JOIN_CACHE = object()
_COMMAND_POST_CACHE = object()
_GRAPH_POST_CACHE = object()
_GRAPH_FOOTPRINT_CACHE = object()
_GRAPH_DECISIONS_CACHE = object()
_LOOP_ASSIGNMENTS_CACHE = object()
_SSA_FLOW_CACHE = object()
_SOURCE_PATH_RANGES = object()
_LOOP_SOURCE_REGIONS = object()
_SOURCE_REGION_TRANSFERS = object()
_MISSING_RANGE_INPUT = object()
_POINTWISE_LOOP_COMBINATION_LIMIT = defaults.SIM_BEHAVIOR_RANGE_COMBINATIONS


def _range_cache_get(cache, key):
    """Reuse an operation's latest exact input binding, not its visit history.

    The key prefix identifies immutable syntax and precision mode; its final
    item is the complete input binding. A different binding replaces the old
    entry rather than retaining every intermediate abstract state of a long
    loop. This changes memoization only: misses execute the same transfer.
    """
    found = cache.get(key[:-1], _NO_RANGE)
    return (found[1] if found is not _NO_RANGE and found[0] == key[-1]
            else _NO_RANGE)


def _range_cache_put(cache, key, value):
    cache[key[:-1]] = (key[-1], value)


def _range_transfer_cache(flow_validations=None, *, loop_posts=None, syntax=None):
    """Fresh expression/join caches, with optional planning-owned loop results.

    A planner revisits local CFGs at different outer-loop entries. Their inner
    loops often have identical inputs; share those complete postconditions for
    this planning call only. Observed bodies still execute, and each summary
    key includes every read/write range and the transfer's precision mode.
    Syntax facts may also be shared: they depend only on the immutable IR,
    not on a particular entry range. Expression-value and join tables remain
    local to each transfer.
    """
    cache = {}
    if flow_validations is not None:
        cache[_SSA_FLOW_CACHE] = flow_validations
    if loop_posts is not None:
        cache[_COMMAND_POST_CACHE] = loop_posts
    if syntax is not None:
        for key in (_FOOTPRINT_CACHE, _GRAPH_FOOTPRINT_CACHE,
                    _GRAPH_DECISIONS_CACHE, _EXPRESSION_NAME_CACHE,
                    _SEQUENCE_PREDICATES):
            cache[key] = syntax.setdefault(key, {})
    return cache


def _graph_range_footprint(graph, cache):
    """Names a sealed local CFG may read or replace."""

    from . import cfg

    footprints = cache.setdefault(_GRAPH_FOOTPRINT_CACHE, {})
    cached = footprints.get(id(graph))
    if cached is not None and cached[0] is graph:
        return cached[1]
    names = set()
    for label in graph.order():
        block = graph.blocks[label]
        for instruction in block.instrs:
            names.update(_range_footprint(instruction, cache)[0])
        if isinstance(block.term, cfg.Branch):
            names.update(_expression_names(block.term.guard))
    result = frozenset(names)
    footprints[id(graph)] = graph, result
    return result


def _sum_ranges(left, right):
    return Range(left.lo + right.lo, left.hi + right.hi)


def _self_delta_expression(expr, name, written, env):
    """Finite change made to ``name`` by one self-relative assignment.

    This is deliberately a proof recognizer, not a general simplifier.  It
    accepts only an unchanged value, a finite additive update independent of
    every value the loop body writes, or an ``ite`` over such alternatives.
    Ignoring the ``ite`` guard joins both possible changes and is therefore a
    sound per-iteration envelope.
    """

    if isinstance(expr, ir.Var) and expr.name == name:
        return Range(0, 0)
    if isinstance(expr, ir.Op) and expr.op == "ite" \
            and len(expr.args) == 3:
        left = _self_delta_expression(expr.args[1], name, written, env)
        right = _self_delta_expression(expr.args[2], name, written, env)
        return None if left is None or right is None else left.join(right)
    if not isinstance(expr, ir.Op) or len(expr.args) != 2 \
            or expr.op not in {"add", "sub"}:
        return None
    left, right = expr.args
    if isinstance(left, ir.Var) and left.name == name:
        change = right
        sign = -1 if expr.op == "sub" else 1
    elif expr.op == "add" \
            and isinstance(right, ir.Var) and right.name == name:
        change = left
        sign = 1
    else:
        return None
    if _expression_names(change) & written:
        return None
    value = of_expression(change, env)
    if value is None or _is_cell_range(value):
        return None
    return (value if sign > 0 else Range(-value.hi, -value.lo))


def _self_delta_command(cmd, name, written, env):
    """Envelope of ``name_after - name_before`` on one normal body path."""

    if isinstance(cmd, (ir.Skip, ir.Mark, ir.Fail)):
        # A failing path has no normal successor.  Retaining a zero-change
        # alternative is a harmless over-approximation and keeps this helper
        # independent of feasibility reasoning.
        return Range(0, 0)
    if isinstance(cmd, ir.Assign):
        return (Range(0, 0) if cmd.var != name else
                _self_delta_expression(cmd.expr, name, written, env))
    if isinstance(cmd, ir.Store):
        return None if cmd.var == name else Range(0, 0)
    if isinstance(cmd, ir.Seq):
        total = Range(0, 0)
        pending = [cmd]
        while pending:
            part = pending.pop()
            if isinstance(part, ir.Seq):
                pending.append(part.second)
                pending.append(part.first)
                continue
            delta = _self_delta_command(part, name, written, env)
            if delta is None:
                return None
            total = _sum_ranges(total, delta)
        return total
    if isinstance(cmd, ir.If):
        left = _self_delta_command(cmd.then, name, written, env)
        right = _self_delta_command(cmd.orelse, name, written, env)
        return None if left is None or right is None else left.join(right)
    if isinstance(cmd, ir.For):
        _footprint, nested_writes = _range_footprint(cmd.body)
        if name == cmd.index or name in nested_writes:
            return None
        return Range(0, 0)
    return None


def _source_loop_prefix(loop, flow):
    """Target bindings followed by exactly the sealed source body, or None."""
    parts = ir._parts(loop.body)
    prefix_size = len(flow.entry_defined)
    prefix = parts[:prefix_size]
    if (any(not isinstance(part, ir.Assign) for part in prefix)
            or {part.var for part in prefix} != set(flow.entry_defined)
            or ir.command_fingerprint(ir._sequence(parts[prefix_size:]))
            != flow.proof_body_seal):
        return None
    return prefix


def _loop_definitions(loop):
    definitions = {}
    for command in ir.walk(loop.body):
        if isinstance(command, ir.Assign):
            definitions[command.var] = (command.expr if command.var not in definitions else None)
        elif isinstance(command, (ir.Store, ir.For)):
            definitions[command.var if isinstance(command, ir.Store) else command.index] = None
    return definitions


def _readonly_loop_expressions(loop, current, maximum, written, *, forbidden=(),
                              definitions=None):
    """Expand unique per-iteration definitions into read-only loop inputs.

    An initial-value dependence must be forbidden by the caller, even if its
    variable has only one syntactic assignment later in the body. Missing,
    cyclic or multiply defined aliases establish no replacement expression.
    """
    definitions = _loop_definitions(loop) if definitions is None else definitions
    stable = {name: value for name, value in current.items() if name not in written}
    stable[loop.index] = Range(0, maximum - 1)
    normalized = {}

    def normalize(expression):
        active, pending = set(), [(expression, None)]
        while pending:
            node, children = pending.pop()
            if node in normalized:
                continue
            if children is None:
                if node in active:
                    normalized[node] = None
                    continue
                if isinstance(node, ir.Var):
                    if node.name not in written:
                        normalized[node] = node
                        continue
                    definition = None if node.name in forbidden else definitions.get(node.name)
                    if definition is None:
                        normalized[node] = None
                        continue
                    children = (definition,)
                elif isinstance(node, ir.Lit):
                    normalized[node] = node
                    continue
                elif isinstance(node, (ir.Op, ir.Sel)):
                    children = _children(node)
                else:
                    normalized[node] = None
                    continue
                active.add(node)
                pending.append((node, children))
                pending.extend((child, None) for child in reversed(children))
                continue
            active.discard(node)
            args = tuple(normalized[child] for child in children)
            if any(arg is None for arg in args):
                value = None
            elif isinstance(node, ir.Var):
                value = args[0]
            elif isinstance(node, ir.Sel):
                value = ir.Sel(*args)
            elif node.op == 'ite' and len(args) == 3:
                condition = of_expression(args[0], stable)
                value = (args[2] if condition is not None and condition.hi == 0 else
                         args[1] if condition is not None and condition.lo > 0 else None)
            else:
                value = ir.Op(node.op, *args)
            normalized[node] = value
        return normalized[expression]

    return stable, definitions, normalize


def _loop_assignment_conditions(loop, current, flows):
    """Preserve jointly replaced values without enumerating execution paths.

    Variables assigned in exactly the same source blocks are replaced together.
    If any of them differs from its singleton entry value, a replacement must
    have occurred, so all members satisfy their replacement-value bounds. Only
    expressions independent of loop-carried writes are admitted. This is an
    inductive implication, including zero trips, not an assumption that some
    candidate was selected or that every body path executes.
    """
    if not isinstance(loop, ir.For):
        return {}
    trip, flow = _loop_trip_range(loop, current, flows)
    if flow is None or trip is None or trip.hi <= 0:
        return {}
    plans = flows.setdefault(_LOOP_ASSIGNMENTS_CACHE, {})
    found = plans.get(loop)
    if found is None:
        if _source_loop_prefix(loop, flow) is None:
            plans[loop] = (), frozenset(), {}
            return {}
        writes, excluded = {}, set(flow.entry_defined)
        for label in flow.graph.order():
            for command in flow.graph.blocks[label].instrs:
                if isinstance(command, ir.Assign):
                    writes.setdefault(command.var, {}).setdefault(label, []).append(command.expr)
                else:
                    excluded.update(_range_footprint(command, flows)[1])
        groups = {}
        for name, sites in writes.items():
            if name not in excluded:
                groups.setdefault(tuple(sorted(sites)), []).append((
                    name, tuple(expr for values in sites.values() for expr in values)))
        found = (tuple(tuple(group) for group in groups.values() if len(group) > 1),
                 _range_footprint(loop.body, flows)[1], _loop_definitions(loop))
        plans[loop] = found
    groups, written, definitions = found
    groups = tuple(group for group in groups if any(
        name in current and not _is_cell_range(current[name])
        and current[name].lo == current[name].hi for name, _expressions in group))
    if not groups or loop.index in written:
        return {}
    stable, _definitions, normalize = _readonly_loop_expressions(
        loop, current, trip.hi, written, forbidden=set(flow.required) & written,
        definitions=definitions)
    result, values_cache = {}, {}
    for group in groups:
        replacements, markers = {}, []
        for name, expressions in group:
            values = []
            for expression in expressions:
                source = normalize(expression)
                # Substituted expressions belong to this visit, not the shared
                # syntax cache; retaining them would grow with loop visits.
                value = None if source is None else _value_range(source, stable, values_cache)
                if value is None or _is_cell_range(value):
                    break
                values.append(value)
            if len(values) != len(expressions):
                continue
            replacements[((name, 1),)] = Bounds(
                min(value.lo for value in values), max(value.hi for value in values))
            before = current.get(name)
            if before is not None and not _is_cell_range(before) and before.lo == before.hi:
                markers.append((name, before.lo))
        if markers and replacements:
            result[tuple(sorted(markers))] = replacements
    return result


def _independent_array_loop_post(loop, current, minimum, maximum, witness,
                                 flow, flows, point_product):
    """Cover a cell-independent map by one arbitrary iteration.

    The sealed body may carry arrays, but no scalar recurrence. Every use of
    a carried array must select the same injective affine function of this
    loop's index. Each iteration therefore sees its own cells' incoming values,
    never a previous iteration's writes. The ordinary body transformer over
    the full index interval covers every final cell and final local value;
    composing that weak update ``maximum`` times would instead invent a
    recurrence between distinct cells. No source or IR is rewritten here.
    """
    if (witness is not None or flow is None or maximum <= 1
            or point_product <= _POINTWISE_LOOP_COMBINATION_LIMIT):
        return None
    # The source graph does not include a for-loop's break/return guard or a
    # live iterator wrapper. Accept only its exact target-binding prefix and
    # sealed body, so loop mechanics cannot hide a carried control dependency.
    prefix = _source_loop_prefix(loop, flow)
    if prefix is None:
        return None
    _footprint, written = _range_footprint(loop.body, flows)
    carried = written & set(flow.required)
    if not carried or any(not _is_cell_range(current.get(name)) for name in carried):
        return None

    commands = tuple(ir.walk(loop.body))
    if loop.index in written:
        return None
    stable, definitions, normalize = _readonly_loop_expressions(
        loop, current, maximum, written)

    indices = {}

    def index_matches(name, expression):
        value = normalize(expression)
        form = None if value is None else affine_form(value, env=stable)
        if form is None or not form[1].get(loop.index):
            return False
        key = form[0], tuple(sorted(form[1].items()))
        return indices.setdefault(name, key) == key

    checked = {}

    def independent(expression):
        if expression in checked:
            return checked[expression]
        if isinstance(expression, ir.Var):
            valid = expression.name not in carried
        elif (isinstance(expression, ir.Sel) and isinstance(expression.array, ir.Var)
              and expression.array.name in carried):
            valid = (index_matches(expression.array.name, expression.index)
                     and independent(expression.index))
        else:
            valid = all(independent(child) for child in _children(expression))
        checked[expression] = valid
        return valid

    for command in commands:
        if isinstance(command, ir.Assign) and command.var in carried:
            value = command.expr
            if (not isinstance(value, ir.Op) or value.op != 'store'
                    or len(value.args) != 3 or not isinstance(value.args[0], ir.Var)
                    or value.args[0].name != command.var):
                return None
            index, expression = value.args[1:]
            if (not index_matches(command.var, index)
                    or not independent(index) or not independent(expression)):
                return None
        elif isinstance(command, ir.Store):
            if command.var in carried and not index_matches(command.var, command.index):
                return None
            if not independent(command.index) or not independent(command.expr):
                return None
        elif isinstance(command, ir.Assign):
            if not independent(command.expr):
                return None
        elif isinstance(command, ir.If):
            if not independent(command.guard):
                return None
        elif isinstance(command, ir.For):
            if not independent(command.trip_count):
                return None
    if set(indices) != carried:
        return None
    entering = dict(current)
    entering[loop.index] = stable[loop.index]
    after = _retained_loop_body_post(
        loop, entering, None, flow, flows, point_product)
    if after is _NO_NORMAL:
        return dict(current) if minimum == 0 else _NO_NORMAL
    if minimum == maximum:
        final = {**stable, loop.index: Range(maximum - 1, maximum - 1)}
        after[loop.index] = final[loop.index]
        for binding in prefix:
            if definitions.get(binding.var) is binding.expr:
                value = normalize(binding.expr)
                got = None if value is None else of_expression(value, final)
                if got is not None:
                    after[binding.var] = got
    return _join_envs(current, after, flows) if minimum == 0 else after


def _overwritten_before_read(body):
    """Names a loop body assigns before any read: their loop-head value is dead.

    Only the leading straight-line assignments count, such as the source loop
    target copied from the IR index. Every turn assigns these names again
    before anything can observe the previous turn's value.
    """
    dead, read = set(), set()
    for part in ir._parts(body):
        if not isinstance(part, ir.Assign):
            break
        read |= _expression_names(part.expr)
        if part.var not in read:
            dead.add(part.var)
    return frozenset(dead)


def _finite_linear_loop_post(
        loop, current, maximum, body_witness, flow, flows, point_product):
    """Accelerate a large finite loop using proved additive recurrences.

    A retained loop can have a large finite trip count while its semantic body
    is small.  Interpreting a counter update once per possible trip makes range
    planning proportional to that count even though the complete range is
    elementary.  For each variable whose every body assignment is proved to
    be ``v := v + d`` (possibly skipped by a branch), derive its ranges over
    all loop heads and exits.  With those finite envelopes fixed, iterate the
    remaining abstract state to a post-fixpoint.  If either proof is
    unavailable, return ``None`` and the caller uses the ordinary exact finite
    iteration.
    """

    # Keep the existing pointwise index precision. Beyond that mode, a proved
    # recurrence is useful for nested medium-sized loops too: a separate
    # 256-trip cutoff makes their Cartesian product fall back to enumeration.
    # Do not spend the closure attempt's own round allowance on a loop that
    # ordinary finite iteration can already finish in that many transfers.
    if body_witness is not None or maximum <= STATE_ROUNDS \
            or point_product <= _POINTWISE_LOOP_COMBINATION_LIMIT:
        return None
    support = Range(0, maximum - 1)
    analysis_env = dict(current)
    analysis_env[loop.index] = support
    _footprint, written = _range_footprint(loop.body, flows)
    summaries = {}
    for name in sorted(written):
        initial = current.get(name)
        if initial is None or _is_cell_range(initial):
            continue
        delta = _self_delta_command(
            loop.body, name, written, analysis_env)
        if delta is None or delta == Range(0, 0):
            continue
        head_turns = maximum - 1
        head = Range(
            initial.lo + min(0, head_turns * delta.lo),
            initial.hi + max(0, head_turns * delta.hi),
        )
        exit_range = Range(
            initial.lo + min(0, maximum * delta.lo),
            initial.hi + max(0, maximum * delta.hi),
        )
        summaries[name] = head, exit_range
    if not summaries:
        return None

    base = dict(current)
    candidate = dict(base)
    for name, (_head, exit_range) in summaries.items():
        candidate[name] = exit_range

    # The recurrence envelopes describe the finite horizon, not an unbounded
    # inductive loop.  Present the head envelope to the body, restore the exit
    # envelope afterwards, and ask the ordinary monotone transformer to close
    # every other value.  A small fixed number of rounds is only an
    # acceleration attempt: failure to settle falls back to finite iteration.
    for _round in range(STATE_ROUNDS):
        entering = dict(candidate)
        entering[loop.index] = support
        for name, (head, _exit) in summaries.items():
            entering[name] = head
        after = _retained_loop_body_post(
            loop, entering, body_witness, flow, flows, point_product)
        if after is _NO_NORMAL:
            return candidate
        following = _join_envs(base, after, flows)
        for name, (_head, exit_range) in summaries.items():
            following[name] = exit_range
        closed = _join_envs(candidate, following, flows)
        for name, (_head, exit_range) in summaries.items():
            closed[name] = exit_range
        if closed == candidate:
            return candidate
        candidate = closed
    return None


def _bounded_assignment_envelope(expr, name, written, env):
    """Return the proved relative and absolute alternatives of one assignment.

    A relative alternative has the form ``name + d`` where ``d`` has a finite
    range independent of every value written by the step.  An absolute
    alternative is independent of every written value.  The pair contains
    either range only when that form has been proved; ``None`` means that this
    expression is outside the accelerator's deliberately small language.
    """

    if isinstance(expr, ir.Op) and expr.op == "ite" \
            and len(expr.args) == 3:
        alternatives = [
            _bounded_assignment_envelope(arm, name, written, env)
            for arm in expr.args[1:]
        ]
        if any(item is None for item in alternatives):
            return None
        relative = [item[0] for item in alternatives if item[0] is not None]
        absolute = [item[1] for item in alternatives if item[1] is not None]

        def joined(values):
            if not values:
                return None
            out = values[0]
            for value in values[1:]:
                out = out.join(value)
            return out

        return joined(relative), joined(absolute)

    delta = _self_delta_expression(expr, name, written, env)
    if delta is not None:
        return delta, None
    if _expression_names(expr) & written:
        return None
    value = of_expression(expr, env)
    if value is None or _is_cell_range(value):
        return None
    return None, value


def _bounded_state_envelopes(proc, env, bound, graph, flows):
    """Prove finite-horizon envelopes for additive scalar state recurrences.

    This is an acceleration of :func:`bounded`, not another range authority.
    Every admitted write is either an absolute finite value or a self-relative
    additive update independent of all step-written values.  Along a path, the
    final value therefore starts at the incoming value or one of the absolute
    values and applies a subset of the admitted deltas.  Summing each delta with
    zero and multiplying that one-step envelope by ``bound`` covers every such
    subset over the complete finite run.  Any unrecognised write disables the
    acceleration for that state variable.
    """

    # The sealed acyclic CFG is what proves that every listed instruction can
    # execute at most once per step.  A term-only procedure keeps the ordinary
    # finite iteration rather than asking this accelerator to reconstruct that
    # control fact from an if-converted command tree.
    if bound < 2 or graph is None:
        return {}
    commands = [command
                for label in graph.order()
                for command in graph.blocks[label].instrs]
    written = set()
    for command in commands:
        _footprint, command_writes = _range_footprint(command, flows)
        written.update(command_writes)

    def assignments(command, name, out):
        if isinstance(command, ir.Assign):
            if command.var == name:
                out.append(command.expr)
            return True
        if isinstance(command, ir.Store):
            return command.var != name
        if isinstance(command, ir.Seq):
            return (assignments(command.first, name, out) and
                    assignments(command.second, name, out))
        if isinstance(command, ir.If):
            return (assignments(command.then, name, out) and
                    assignments(command.orelse, name, out))
        if isinstance(command, ir.For):
            _footprint, nested_writes = _range_footprint(command, flows)
            return name not in nested_writes
        _footprint, other_writes = _range_footprint(command, flows)
        return name not in other_writes

    envelopes = {}
    for name in proc.state:
        initial = env.get(name)
        if initial is None or _is_cell_range(initial):
            continue
        expressions = []
        if not all(assignments(command, name, expressions)
                   for command in commands):
            continue
        if not expressions:
            continue
        seed = initial
        delta = Range(0, 0)
        admitted = True
        for expression in expressions:
            alternative = _bounded_assignment_envelope(
                expression, name, written, env)
            if alternative is None:
                admitted = False
                break
            relative, absolute = alternative
            if absolute is not None:
                seed = seed.join(absolute)
            if relative is not None:
                # A CFG instruction or conditional arm need not execute.  The
                # zero alternative also covers a relative update overwritten
                # later by an absolute assignment on the same path.
                optional = relative.join(Range(0, 0))
                delta = _sum_ranges(delta, optional)
        if not admitted:
            continue
        envelopes[name] = Range(
            seed.lo + min(0, bound * delta.lo),
            seed.hi + max(0, bound * delta.hi),
        )
    return envelopes


def _copy_names(copies):
    """Names a predicate's copies read: aliases and cast-copy definitions."""
    names = set()
    for name, root in copies:
        names.add(name)
        names |= {root} if type(root) is str else _expression_names(root)
    return names


def _finite_assumption(expr, truth, env, copies=(), *, cache=None):
    if cache is not None:
        # Refinement reads only the predicate's operands and its still-equal
        # copies. Repeated loop positions often have identical bindings for
        # these names even though unrelated accumulators/indices have changed.
        # Reuse the exact transfer, not a loop summary: observers still visit
        # every position. The table belongs to this range walk, not mutable IR
        # across preparations or runs.
        names = _expression_names_cached(expr, cache)
        if copies:
            names = tuple(sorted(set(names) | _copy_names(copies)))
        key = (id(expr), truth, copies, tuple(
            env.get(name, _MISSING_RANGE_INPUT) for name in names))
        assumptions = cache.setdefault(_FINITE_ASSUMPTION_CACHE, {})
        narrowed = _range_cache_get(assumptions, key)
        if narrowed is _NO_RANGE:
            local = {name: env[name] for name in names if name in env}
            narrowed = _finite_assumption_transfer(expr, truth, local, copies, cache)
            _range_cache_put(assumptions, key, narrowed)
        if narrowed is None:
            return None
        out = dict(env)
        for name in names:
            out.pop(name, None)
        out.update(narrowed)
        return out
    return _finite_assumption_transfer(expr, truth, env, copies)


def _finite_assumption_transfer(expr, truth, env, copies, cache=None):
    original = env
    # This caller already owns a finite range environment.  Feeding it through
    # the public ``assume`` API converted every live value to ``Bounds`` and
    # then immediately converted the whole mapping back, once per branch.
    # Retained loops can visit tens of thousands of compiler guards.  Refine
    # lazily and copy an unchanged environment only to isolate the branch.
    narrowed = _assume(expr, truth, env, cache)
    if narrowed is None:
        return None
    if copies:
        # A predicate may be recovered through y := x := input. Its refined
        # input interval also holds for both still-equal copies, including a
        # copy assigned by the selected arm later. Join only after that arm.
        groups = {}
        for name, root in copies:
            if type(root) is str:
                groups.setdefault(root, {root}).add(name)
        narrowed = dict(narrowed)
        for names in groups.values():
            bound = None
            for name in names:
                value = _bound(narrowed.get(name))
                if value is not None:
                    bound = value if bound is None else bound.meet(value)
                    if bound is None:
                        return None
            if bound is not None:
                narrowed.update((name, bound) for name in names)
        for name, root in copies:
            if type(root) is str:
                continue
            # A cast copy such as x := swrap(a, 32) still equals its
            # definition. The recovered predicate refined a, so evaluate the
            # definition again to refine x. The cast's own range transfer
            # decides whether it preserves the number.
            operands = {}
            for operand in _expression_names(root):
                value = narrowed.get(operand)
                finite = (None if value is None or _is_cell_range(value)
                          else _bound(value).finite())
                if finite is None:
                    break
                operands[operand] = finite
            else:
                copied = of_expression(root, operands)
                if copied is not None and not _is_cell_range(copied):
                    current = _bound(narrowed.get(name))
                    bound = (_bound(copied) if current is None
                             else current.meet(_bound(copied)))
                    if bound is None:
                        return None
                    narrowed[name] = bound
    if narrowed is original:
        return dict(original)
    out = {}
    for name, value in narrowed.items():
        if _is_cell_range(value):
            out[name] = value
            continue
        finite = _bound(value).finite()
        if finite is None:
            continue
        previous = original.get(name)
        # A predicate about another value does not destroy a product fact.
        # If it left this variable's hull unchanged, retain its cell ranges;
        # a genuinely narrowed array falls back to the sound scalar hull.
        if (_is_cell_range(previous)
                and (finite.lo, finite.hi) == (previous.lo, previous.hi)):
            finite = previous
        out[name] = finite
    return out


def walk_post(cmd, env, witness, *, _flows=None):
    """Run `_post` over ``cmd``, calling ``witness(cmd, env)`` before each command.

    For a caller that has to ask something of every command *in the environment that
    command reads*, which is not the environment the step starts in: `#taken:entry = 0`
    then `#taken:entry = ne(gt(a, 3), 0)` gives that name a range partway through, and a
    reader using the starting environment finds none and reports a variable the encoder
    has no trouble with. 365 models were reported that way by the first version of
    `plan_encoding`, all of them for the predicates if-conversion introduces.

    A hook rather than a second walk. The alternative was mirroring the twenty lines
    below in another module, and two walks that have to agree about what a loop leaves
    are two walks that can differ about it.
    """
    got = _post(cmd, env, witness, _flows)
    return {} if got is _NO_NORMAL else got


def _enter_witness_loop(witness, loop, turn):
    """Tell an internal observer which concrete abstract turn it enters.

    Range environments are projected to the variables a remaining command can
    read.  An outer loop index may therefore disappear after it has produced a
    derived value, even though allocation planning still needs that control
    context.  Keep control history in the observer protocol instead of
    reconstructing it from data that is intentionally no longer live.
    """
    enter = getattr(witness, "enter_loop_iteration", None)
    if enter is None:
        return False
    enter(loop, turn)
    return True


def _leave_witness_loop(witness, loop, turn, entered):
    if not entered:
        return
    leave = getattr(witness, "leave_loop_iteration", None)
    if leave is None:
        raise TypeError("a loop-aware range witness must leave each iteration")
    leave(loop, turn)


def post(cmd, env):
    """A fresh range environment after executing ``cmd``.

    This is the public, non-mutating boundary for the command transformer used
    internally by the encoding plan. Frontend control-flow lowering needs the
    same transformer when a later source construct relies on a value written by
    an earlier loop; using it here keeps one definition of branch and loop join.
    """
    got = _post(cmd, dict(env))
    return {} if got is _NO_NORMAL else _plain_ranges(got)


def _plain_ranges(env):
    """Remove private product precision at a public range-map boundary."""

    return {
        name: Range(value.lo, value.hi)
        for name, value in env.items() if value is not None
    }


def _expression_names(expr):
    """Variable names read by one expression, without revisiting shared nodes."""

    names = set()
    seen = set()
    stack = [expr]
    while stack:
        node = stack.pop()
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(node, ir.Var):
            names.add(node.name)
        else:
            stack.extend(_children(node))
    return names


_FOOTPRINT_CACHE = object()


def _range_footprint(command, cache=None):
    """Names whose ranges one command can read or replace.

    Range transfer is local: values absent from this footprint pass through
    unchanged.  Projecting them out avoids copying an ever-growing compiler
    environment through every predicated assignment and retained-loop turn.
    """
    footprints = {} if cache is None else cache.setdefault(_FOOTPRINT_CACHE, {})
    # Compose nested control regions once instead of rescanning their complete
    # subtrees for every enclosing branch. Flatten Seq spines: storing a growing
    # name set for each suffix would itself require quadratic space. These are
    # syntax facts only, scoped to the existing single-transfer cache; no input
    # ranges, branch outcomes or postconditions are reused here.
    pending = [(command, None)]
    while pending:
        current, parts = pending.pop()
        found = footprints.get(id(current))
        if found is not None and found[0] is current:
            continue
        if parts is None:
            parts, stack = [], []
            if isinstance(current, ir.Seq):
                stack.append(current)
            elif isinstance(current, ir.If):
                stack.extend((current.orelse, current.then))
            elif isinstance(current, ir.For):
                stack.append(current.body)
            while stack:
                part = stack.pop()
                if isinstance(part, ir.Seq):
                    stack.extend((part.second, part.first))
                else:
                    parts.append(part)
            pending.append((current, parts))
            pending.extend((part, None) for part in reversed(parts))
            continue
        names, written = set(), set()
        for expression in ir.expressions(current):
            names.update(_expression_names(expression))
        if isinstance(current, (ir.Assign, ir.Store)):
            written.add(current.var)
        elif isinstance(current, ir.For):
            written.add(current.index)
        for part in parts:
            child_names, child_writes = footprints[id(part)][1]
            names.update(child_names)
            written.update(child_writes)
        names.update(written)
        footprints[id(current)] = current, (frozenset(names), frozenset(written))
    return footprints[id(command)][1]


class _PredicateCopies:
    """Current straight-line copies, never an invariant or replay sample.

    Follow evaluated predicates and copies/selections with scalar casts.
    Arithmetic definitions stay named: this exposes guards without expanding
    their entire dataflow cone. An intervening write (including inside a
    branch or loop) invalidates the affected equation.
    """

    def __init__(self):
        self.definitions = {}
        self.versions = {}
        self.position = 0

    def append(self, command, cache=None):
        self.position += 1
        if isinstance(command, ir.Assign):
            dependencies = tuple((name, self.versions.get(name, 0))
                                 for name in _expression_names(command.expr))
            self.versions[command.var] = self.position
            self.definitions[command.var] = command.expr, dependencies
        else:
            for name in _range_footprint(command, cache)[1]:
                self.versions[name] = self.position
                self.definitions.pop(name, None)

    def predicate(self, guard):
        def copied_value(value):
            # Preserve casts syntactically. Range refinement, not this alias
            # walk, decides whether they preserve the underlying number.
            while isinstance(value, ir.Op) and value.op in {"uwrap", "swrap", "toint"}:
                value = value.args[0]
            return isinstance(value, (ir.Var, ir.Lit, ir.Sel))

        result, aliases, casts, pending = {}, {}, {}, [(guard, True, None)]
        while pending:
            expression, decision, children = pending.pop()
            key = id(expression), decision
            if key in result:
                continue
            if children is None:
                children = ()
                if isinstance(expression, ir.Var):
                    definition = self.definitions.get(expression.name)
                    if definition is not None:
                        value, dependencies = definition
                        if ((decision or copied_value(value))
                                and all(self.versions.get(name, 0) == version
                                        for name, version in dependencies)):
                            children = ((value, decision),)
                elif isinstance(expression, ir.Op):
                    boolean = expression.op in {"truth", "not", "toint", "land", "lor"}
                    children = tuple((arg, boolean or (
                        decision and index == 0 and expression.op in {"uwrap", "swrap"}))
                        for index, arg in enumerate(expression.args))
                if not children:
                    result[key] = expression
                    continue
                pending.append((expression, decision, children))
                pending.extend((child, mode, None) for child, mode in reversed(children))
                continue
            values = tuple(result[id(child), mode] for child, mode in children)
            result[key] = (values[0] if isinstance(expression, ir.Var) else
                           expression if all(a is b for a, b in zip(values, expression.args)) else
                           ir.Op(expression.op, *values))
            if isinstance(expression, ir.Var) and isinstance(result[key], ir.Var):
                aliases[expression.name] = result[key].name
            elif (isinstance(expression, ir.Var) and not decision
                  and isinstance(result[key], ir.Op)):
                # The predicate now reads through the cast copy. Keep its
                # definition so that the copy is refined with its operands.
                casts[expression.name] = result[key]
        return result[id(guard), True], tuple(aliases.items()) + tuple(casts.items())


def _branch_predicate(guard, instructions):
    copies = _PredicateCopies()
    for command in instructions:
        copies.append(command)
    return copies.predicate(guard)


_SEQUENCE_PREDICATES = object()


def _sequence_predicates(command, cache):
    """Prepare syntax-only guard copies once per range walk, also in loops."""
    prepared = cache.setdefault(_SEQUENCE_PREDICATES, {})
    found = prepared.get(id(command))
    if found is not None and found[0] is command:
        return found[1]
    parts, stack = [], [command]
    while stack:
        part = stack.pop()
        if isinstance(part, ir.Seq):
            stack.extend((part.second, part.first))
        else:
            parts.append(part)
    copies, steps = _PredicateCopies(), []
    for part in parts:
        guard = copies.predicate(part.guard) if isinstance(part, ir.If) else None
        steps.append((part, guard))
        copies.append(part, cache)
    prepared[id(command)] = command, steps
    return steps


def _truth_binding(expression):
    """A named decision, possibly converted to truth or negated."""
    positive = True
    while isinstance(expression, ir.Op):
        converted = truth_preserving_operand(expression, {})
        if converted is not expression:
            expression = converted
            continue
        if len(expression.args) != 1:
            return None
        if expression.op == "not":
            positive = not positive
        elif expression.op != "truth":
            return None
        expression = expression.args[0]
    return (expression.name, positive) if isinstance(expression, ir.Var) else None


def _join_guard_facts(left, right):
    from . import range_relations

    joined = {}
    for name in left.keys() & right.keys():
        a, b = left[name], right[name]
        joined[name] = {
            truth: (range_relations.join(a[truth], b[truth])
                    if truth in a and truth in b else
                    a[truth] if truth in a else b[truth])
            for truth in a.keys() | b.keys()
        }
    return joined


def _decision_lifetimes(graph, cache):
    """Named decisions, last uses and recovered guards of one source graph."""
    from . import cfg

    prepared = cache.setdefault(_GRAPH_DECISIONS_CACHE, {})
    found = prepared.get(id(graph))
    if found is not None and found[0] is graph:
        return found[1]
    order = tuple(graph.order())
    decisions, copies, last_use = set(), {}, {}
    predicates, straight = {}, {}
    incoming = dict.fromkeys(order, 0)
    for label in order:
        for target in graph.blocks[label].term.successors():
            incoming[target] = incoming.get(target, 0) + 1
    for index, label in enumerate(order):
        if _RANGE_CHECKPOINT in cache:
            cache[_RANGE_CHECKPOINT]()
        block = graph.blocks[label]
        aliases = straight.pop(label, None)
        if aliases is None:
            aliases = _PredicateCopies()
        for instruction in block.instrs:
            aliases.append(instruction, cache)
        if isinstance(block.term, cfg.Branch):
            # Observation blocks can split a captured read from its guard.
            # Reuse one tracker along a unique straight-line chain; branches
            # and joins end it. No copied environments or path enumeration.
            # Recreating equivalent guards on each loop visit defeats every
            # syntax-keyed range cache and retains the new expression trees.
            predicates[label] = aliases.predicate(block.term.guard)
            binding = _truth_binding(block.term.guard)
            if binding is not None:
                decisions.add(binding[0])
                last_use[binding[0]] = index
        for instruction in block.instrs:
            if isinstance(instruction, ir.Assign):
                binding = _truth_binding(instruction.expr)
                if binding is not None:
                    copies.setdefault(instruction.var, set()).add(binding[0])
                    last_use[binding[0]] = index
        if isinstance(block.term, cfg.Goto) and incoming[block.term.target] == 1:
            straight[block.term.target] = aliases
    pending = list(decisions)
    while pending:
        for name in copies.get(pending.pop(), ()):
            if name not in decisions:
                decisions.add(name)
                pending.append(name)
    result = order, frozenset(decisions), last_use, predicates
    prepared[id(graph)] = graph, result
    return result


def graph_post(
        graph, env, witness=None, terminator_witness=None, *,
        _include_infeasible_edges=False, _flows=None, _point_product=1,
        _recursive_witness=False,
        _unreachable_witness=None,
        _unreachable_terminator_witness=None):
    """The finite normal postcondition read directly from one acyclic CFG.

    If-conversion preserves execution but not the source joins that a range
    proof needs.  In particular, the two definitions of a value chosen by a
    source conditional become two separately predicated assignments; a term
    walk then sees each assignment as optional and loses the value before its
    use.  The graph still says exactly which definitions meet at that use.

    This transformer follows only reachable normal edges, refines each branch
    by its edge predicate, and joins incoming predecessors at the block.  A
    missing bound on any reachable predecessor therefore remains missing.  It
    does not initialise a carrier to a convenient filler and does not infer
    feasibility from the converted ``#reached`` variables.

    ``witness`` receives each retained instruction with the environment at
    that occurrence.  ``terminator_witness`` receives a block label, its
    terminator, and the environment after that block's instructions.  The
    latter is the source fact needed by the synthetic branch-decision command
    that if-conversion inserts at the same boundary.

    Procedure observers use ``_recursive_witness`` to see retained loop bodies
    during their existing transfer. Graph position observers stay at this
    graph's lexical depth; they plan each loop's local graph separately.

    The private ``_include_infeasible_edges`` mode is only for representation
    planning.  If a branch is impossible in the supplied layer, its term is
    still present in the symbolic formula and therefore still needs finite
    operand carriers.  That mode visits the syntactic edge with the unchanged
    predecessor environment; callers must not use its postcondition as a
    reachable-state fact.

    Position planning can instead supply ``_unreachable_witness``.  The same
    traversal then reports instructions reached only through an infeasible
    edge to that observer, while its return value and ordinary witnesses stay
    restricted to reachable executions.  Once a block is reachable, its exact
    environment subsumes the representation-only path, so the block is never
    interpreted twice.
    """
    # Imported here so the foundational expression transformers do not make
    # the CFG module part of their import cycle.
    from . import cfg
    from . import range_relations

    if _flows is None:
        _flows = {}
    collect_unreachable = (
        _unreachable_witness is not None
        or _unreachable_terminator_witness is not None)
    if _include_infeasible_edges and collect_unreachable:
        raise ValueError(
            "infeasible edges can either affect the postcondition or be "
            "observed separately")
    # A lowered short-circuit expression assigns a decision on separate
    # paths, then tests it after their join. Retain each truth value's affine
    # consequences for names actually used as decisions. Joining the ordinary
    # intervals alone forgets, for example, that ``not (x < 0 or x > 3)``
    # implies 0 <= x <= 3. These are conditional facts, not extra assumptions.
    order, decisions, last_use, branch_predicates = _decision_lifetimes(graph, _flows)
    cached_post = None
    if (witness is None and terminator_witness is None
            and not collect_unreachable):
        # Nested retained loops can request this same sealed CFG after the
        # term view has already produced identical entry ranges. Reuse only a
        # complete, unobserved transfer with the exact same binding and mode.
        # As with command summaries, retain the latest binding, not its history.
        key = (id(graph), _include_infeasible_edges,
               _flows.get(_SOURCE_PATH_RANGES, False),
               min(_point_product, _POINTWISE_LOOP_COMBINATION_LIMIT + 1),
               tuple(sorted(env.items())))
        cache = _flows.setdefault(_GRAPH_POST_CACHE, {})
        found = _range_cache_get(cache, key)
        if found is _NO_NORMAL:
            return _NO_NORMAL
        if found is not _NO_RANGE:
            return dict(found)
        cached_post = cache, key
    incoming = {graph.entry: [(dict(env), {}, {}, {})]}
    unreachable_incoming = {} if collect_unreachable else None
    normal_exits = []
    for index, label in enumerate(order):
        reachable_alternatives = incoming.get(label, ())
        unreachable_alternatives = (
            () if unreachable_incoming is None
            else unreachable_incoming.get(label, ()))
        reachable = bool(reachable_alternatives)
        alternatives = (reachable_alternatives if reachable
                        else unreachable_alternatives)
        if not alternatives:
            continue
        first, facts, guarded, conditions = alternatives[0]
        current = dict(first)
        for other, other_facts, other_guarded, other_conditions in alternatives[1:]:
            current = _join_envs(current, other, _flows)
            facts = range_relations.join(facts, other_facts)
            guarded = _join_guard_facts(guarded, other_guarded)
            conditions = range_relations.join_conditions(conditions, other_conditions)
        block = graph.blocks[label]
        active_witness = witness if reachable else _unreachable_witness
        active_terminator_witness = (
            terminator_witness if reachable
            else _unreachable_terminator_witness)
        alive = True
        for instruction in block.instrs:
            nested_witness = witness if reachable and _recursive_witness else None
            if active_witness is not None and nested_witness is None:
                active_witness(instruction, current)
            decision = None
            if isinstance(instruction, ir.Assign) and instruction.var in decisions:
                decision = {}
                binding = _truth_binding(instruction.expr)
                for truth in (False, True):
                    if _finite_assumption(
                            instruction.expr, truth, current, cache=_flows) is None:
                        continue
                    conditional = range_relations.assume(
                        facts, instruction.expr, truth, env=current)
                    if binding is not None:
                        name, positive = binding
                        conditional = range_relations.conjoin(
                            conditional, guarded.get(name, {}).get(
                                truth == positive, {}))
                    if conditional is None:
                        continue
                    decision[truth] = conditional
            # Capture relations before the instruction mutates its environment.
            # In particular x = x - q*d must refer to the old x, not its result.
            refinement = quotient = copied = None
            if isinstance(instruction, ir.Assign):
                copied = range_relations.copied_name(instruction.expr, current)
                if facts:
                    refinement = range_relations.bound(instruction.expr, facts, env=current)
                quotient = range_relations.quotient(instruction.var, instruction.expr, current)
            replacements = _loop_assignment_conditions(instruction, current, _flows)
            updated = _post(
                instruction, current, nested_witness, _flows=_flows,
                _point_product=_point_product)
            if updated is _NO_NORMAL:
                alive = False
                break
            current = updated
            if conditions or replacements:
                _read, written = _range_footprint(instruction, _flows)
                conditions = range_relations.forget_conditions(conditions, written)
                conditions.update(replacements)
            if facts or guarded or decision is not None:
                # The RHS and guard refer to pre-assignment values, including
                # for x = x - y. Refine its already-computed result, then kill
                # facts about overwritten operands; never reevaluate the RHS.
                if refinement is not None and current.get(instruction.var) is not None:
                    refined = Bounds.of(current[instruction.var]).meet(refinement)
                    if refined is not None and refined.finite() is not None:
                        current[instruction.var] = refined.finite()
                _read, written = _range_footprint(instruction, _flows)
                facts = range_relations.forget(facts, written)
                guarded = {
                    name: {truth: range_relations.forget(value, written)
                           for truth, value in choices.items()}
                    for name, choices in guarded.items() if name not in written
                }
                if decision is not None:
                    guarded[instruction.var] = {
                        truth: range_relations.forget(value, written)
                        for truth, value in decision.items()
                    }
            if copied is not None:
                # Helper arguments are ordinary value copies. A later source
                # block may test that copy and then store the original value;
                # retain their equality across CFG edges, not just inside the
                # block where _branch_predicate can recover the assignment.
                facts = range_relations.copy(facts, instruction.var, copied)
            if quotient:
                # Contradictory abstract predecessors can be visited for
                # representation planning. Forgetting their relation is
                # conservative; never invent a finite value or an assertion.
                facts = range_relations.conjoin(facts, quotient) or {}
        if not alive:
            continue

        terminator = block.term
        if active_terminator_witness is not None:
            active_terminator_witness(label, terminator, current)
        elif (reachable and _recursive_witness and witness is not None
              and isinstance(terminator, cfg.Fail)):
            witness(ir.Fail(terminator.code, terminator.goal), current)
        # Once a decision has no later read, its conditional metadata cannot
        # refine any future command. Keep selected ordinary path facts, but
        # do not carry a growing history of old decisions through the graph.
        outgoing_guards = {name: value for name, value in guarded.items()
                           if last_use.get(name, -1) > index}
        if isinstance(terminator, cfg.Goto):
            destination = incoming if reachable else unreachable_incoming
            destination.setdefault(terminator.target, []).append((
                dict(current), facts, outgoing_guards, conditions))
        elif isinstance(terminator, cfg.Branch):
            predicate, copies = branch_predicates[label]
            predicates = ((predicate,) if predicate is terminator.guard else
                          (terminator.guard, predicate))
            for truth, target in (
                    (True, terminator.then), (False, terminator.orelse)):
                entered = _finite_assumption(
                    predicate, truth, current, copies, cache=_flows)
                if entered is not None:
                    # Copy recovery exposes the current operand, but cannot
                    # replace facts about the source spelling. A saved Boolean
                    # may carry conditional facts about that spelling across
                    # a short-circuit join. Both equivalent predicates hold
                    # on this edge; retain both until their operands change.
                    selected = facts
                    for condition in predicates:
                        selected = range_relations.assume(selected, condition, truth, env=current)
                        binding = _truth_binding(condition)
                        if binding is not None:
                            name, positive = binding
                            selected = range_relations.conjoin(
                                selected, guarded.get(name, {}).get(
                                    truth == positive, {}))
                            if selected is None:
                                break
                    entered = (None if selected is None else
                               range_relations.refine_env(entered, selected))
                    if entered is not None and conditions:
                        entered = range_relations.refine_conditions(entered, conditions, selected)
                if entered is not None:
                    destination = incoming if reachable else unreachable_incoming
                    destination.setdefault(target, []).append((
                        entered, selected, outgoing_guards, conditions))
                elif _include_infeasible_edges:
                    incoming.setdefault(target, []).append((dict(current), {}, {}, {}))
                elif unreachable_incoming is not None:
                    unreachable_incoming.setdefault(target, []).append(
                        (dict(current), {}, {}, {}))
        elif isinstance(terminator, cfg.Return) and reachable:
            normal_exits.append(current)
        # Fail and Pruned have no normal successor.

    joined = _NO_NORMAL
    if normal_exits:
        joined = dict(normal_exits[0])
        for state in normal_exits[1:]:
            joined = _join_envs(joined, state, _flows)
    if cached_post is not None:
        cache, key = cached_post
        _range_cache_put(cache, key, _NO_NORMAL if joined is _NO_NORMAL else dict(joined))
    return joined


def _typed_entry_ranges(proc, env):
    """Retain fixed array shape without assuming any new leaf-value facts.

    A public array hull covers every cell. Repeating that hull in the declared
    shape denotes the same set of typed values, but permits later exact-index
    writes to replace a cell. Products already carrying finer facts stay intact;
    unknown values stay unknown. Uniform products cost space per dimension,
    not per represented element.
    """
    from .typecheck import Array

    before = dict(env)
    for name, interval in env.items():
        if not isinstance(interval, Range) or _is_cell_range(interval):
            continue
        shape, kind = [], proc.types.get(name)
        while isinstance(kind, Array):
            shape.append(kind.cells)
            kind = kind.elem
        for length in reversed(shape):
            interval = _UniformCellRange(length, interval)
        before[name] = interval
    return before


_STRUCTURED_RANGE_GRAPHS = object()


def _structured_post(body, env, witness, flows, point_product):
    """Use the shared CFG transfer, preserving source command observations."""
    from . import cfg

    prepared = flows.setdefault(_STRUCTURED_RANGE_GRAPHS, {})
    found = prepared.get(id(body))
    if found is None:
        origins = {}
        graph = cfg.of_command(body, origins=origins)
        prepared[id(body)] = body, graph, origins
    else:
        _, graph, origins = found

    terminator_witness = None
    if witness is not None:
        def terminator_witness(label, _terminator, at):
            command = origins.get(label)
            if isinstance(command, (ir.If, ir.Fail, ir.Skip)):
                witness(command, at)
    result = graph_post(
        graph, env, witness, terminator_witness, _flows=flows,
        _point_product=point_product, _recursive_witness=True)
    # Like _post, a loop-body transfer updates the caller's environment.
    env.clear()
    if result is _NO_NORMAL:
        return result
    env.update(result)
    return env


def _procedure_post_with_graph(
        proc, env, graph, witness=None, _flows=None, _point_product=1):
    """Postcondition using the graph already validated by this analysis."""
    before = _typed_entry_ranges(proc, env)
    if graph is None and _flows is not None and _flows.get(_SOURCE_PATH_RANGES, False):
        result = _structured_post(proc.body, before, witness, _flows, _point_product)
        return None if result is _NO_NORMAL else result
    if graph is not None:
        graph_after = graph_post(
            graph, before, witness, _flows=_flows,
            _point_product=_point_product, _recursive_witness=True)
        return None if graph_after is _NO_NORMAL else graph_after
    term_after = _post(
        proc.body, before, witness, _flows,
        _point_product)
    return None if term_after is _NO_NORMAL else term_after


def procedure_post(proc, env, witness=None, *, source_paths=False):
    """Finite normal postcondition of one complete lowered procedure.

    A graph-lowered procedure seals the source CFG and its if-converted term at
    their common producer.  The graph is therefore the direct semantic
    authority for source-path joins; interpreting the flattened term first
    would execute the same procedure twice and retain only compiler control
    variables that no semantic postcondition consumes.  A term-only procedure
    has no such authority and is interpreted directly.

    ``source_paths`` also uses the same graph transfer for structured producers
    without a source CFG, preserving their original command observations. It
    retains each sealed loop CFG's path joins when the
    flattened loop already has finite (but possibly much wider) ranges. Output
    contract proofs need that precision; carrier planning can retain its cheaper
    conservative hull. Both use the same transfers and unchanged loop bounds.
    """
    graph = getattr(proc, "graph", None)
    flows = {_SOURCE_PATH_RANGES: True} if source_paths else None
    return _procedure_post_with_graph(proc, env, graph, witness, flows)


class _LoopBodyWitness:
    """Observe one loop CFG locally while preserving a public observer."""

    __slots__ = ("entries", "external", "graph_commands")

    def __init__(self, external, graph_commands, entries):
        self.external = external
        self.graph_commands = graph_commands
        self.entries = entries

    def __call__(self, command, at):
        if self.external is not None:
            self.external(command, at)
        if id(command) not in self.graph_commands:
            return
        for name in self.entries:
            if name in at:
                self.entries[name].append(at[name])


class _DirectCommandWitness:
    """Observe one CFG's instructions without entering retained loop bodies.

    A retained loop owns a separately sealed local CFG.  Position planning
    visits that CFG explicitly at its lexical depth, so an observer collecting
    entries for the enclosing CFG must see the loop instruction but not replay
    the local CFG once per abstract trip.  The range transfer still executes
    the complete loop; only propagation of this observer stops at the boundary.
    """

    __slots__ = ("callback",)

    def __init__(self, callback):
        self.callback = callback

    def __call__(self, command, at):
        self.callback(command, at)


def _retained_loop_body_post(
        loop, env, witness, validated_flow=None, _flows=None,
        _point_product=1):
    """Range postcondition of one retained loop-body execution.

    The executable body is an if-converted term and remains authoritative for
    synthetic control flags.  ``LoopFlow`` also seals the source CFG from which
    that term was produced.  Its joins are authoritative for source values: a
    range lost behind predication must not disappear merely because the
    executable representation flattened the paths.

    Source-path transfers execute the source CFG inside its entry/exit wrappers;
    structured producers use the same transfer without a sealed flow. Carrier
    planning keeps its conservative term walk, using the source CFG only to
    recover lost source facts or to observe lexical positions.
    """
    source_paths = _flows is not None and _flows.get(_SOURCE_PATH_RANGES, False)
    flow = validated_flow if validated_flow is not None else loop.flow
    if flow is None:
        if source_paths:
            return _structured_post(loop.body, env, witness, _flows, _point_product)
        return _post(
            loop.body, env, witness, _flows,
            _point_product=_point_product)

    if validated_flow is None:
        flow = _checked_loop_flow(loop, _flows)
    before = dict(env)
    source_values = set(flow.required) | set(flow.defined)
    source_observer = getattr(witness, "source_graph", None)
    if source_paths and source_observer is None:
        from . import ssa

        regions = _flows.setdefault(_LOOP_SOURCE_REGIONS, {})
        if loop not in regions:
            regions[loop] = ssa.loop_source_region(
                loop, checkpoint=_flows.get(_RANGE_CHECKPOINT))
        region = regions[loop]
        if region is not None:
            node, prefix, graph = region
            _flows.setdefault(_SOURCE_REGION_TRANSFERS, {})[node] = prefix, graph
            # Execute the entry/exit wrappers normally, but the sealed source
            # body only once, through its CFG. Do not first observe weaker
            # predicated paths and try to repair their postcondition later.
            return _post(loop.body, env, witness, _flows, _point_product)
    if witness is None and not source_paths:
        # Most bodies retain every finite source fact in their executable
        # term.  Do not install a per-command observer in that common case;
        # it exists solely to recover the local-CFG entry if predication has
        # actually lost one of those facts.
        term_after = _post(
            loop.body, env, None, _flows,
            _point_product=_point_product)
        if (term_after is _NO_NORMAL
                or source_values <= set(term_after)):
            return term_after
        # The graph below needs its live-ins from `before`; replaying the
        # term can recover only the loop-prefix bindings in entry_defined.
        # If another required range is absent, that replay cannot enable the
        # graph and would merely repeat the complete (possibly nested) body.
        # Public/source-path observers still take the original observed path.
        if any(name not in before for name in
               set(flow.required) - set(flow.entry_defined)):
            return term_after
    graph_commands = {
        id(command)
        for label in flow.graph.order()
        for command in flow.graph.blocks[label].instrs
    }
    entries = {name: [] for name in flow.entry_defined}
    # A while loop's mechanical counter is often already bound and never
    # written by its body. Recovering that same value at every instruction
    # needlessly disables complete-transfer reuse throughout the body. Only
    # omit this private observer when every entry value is unchanged; source
    # for-loop targets and caller-requested observations keep the full walk.
    fixed_entries = (witness is None and entries.keys() <= before.keys()
                     and not (entries.keys() & _range_footprint(loop.body, _flows)[1]))
    if fixed_entries:
        entries = {name: [before[name]] for name in entries}
    observed_env = env if witness is not None or source_paths else dict(before)
    observed_after = _post(
        loop.body,
        observed_env,
        None if fixed_entries else _LoopBodyWitness(witness, graph_commands, entries),
        _flows,
        _point_product,
    )
    if witness is not None or source_paths:
        term_after = observed_after
    # The executable term is already a sound carrier hull. Unless this caller
    # needs source-path precision, use the graph only to restore a forgotten
    # value: replaying both views on every turn otherwise doubles frontend work.
    # The source-boundary proof also needs narrower, guarded values. ``required``
    # covers live-ins and ``defined`` covers values established on every body
    # exit, exactly the source facts the graph can restore. A position observer
    # also needs intermediate source entries, even on failing visits or when
    # only a partial entry environment is available. Never let an omitted
    # later visit make the first visit's carrier appear valid for every visit.
    if term_after is _NO_NORMAL and source_observer is None:
        return term_after
    if not source_paths and source_observer is None and source_values <= set(term_after):
        return term_after
    # A sealed local CFG reads only its declared live-ins plus values bound by
    # the retained loop prefix below.  Carrying every surrounding compiler
    # temporary through each branch join changes no source fact and makes a
    # nested finite loop pay for the size of the whole procedure on every
    # iteration.
    needed = set(flow.required)
    needed.update(_graph_range_footprint(flow.graph, _flows))
    seed = {
        name: before[name]
        for name in needed
        if name in before
    }
    for name, values in entries.items():
        # The retained-loop prefix binds source targets before this local CFG.
        # Record an entry value when a graph command observes one.  A target
        # that the body never reads may have no observation at all; omitting
        # that irrelevant name is sound, while abandoning the complete graph
        # postcondition would also discard every value the body does define.
        if not values:
            continue
        value = values[0]
        for other in values[1:]:
            value = value.join(other)
        seed[name] = value
    if source_observer is None and any(name not in seed for name in flow.required):
        return term_after
    graph_after = (
        source_observer(flow.graph, seed) if source_observer is not None else
        graph_post(flow.graph, seed, _flows=_flows,
                   _point_product=_point_product))
    if term_after is _NO_NORMAL:
        return term_after
    if graph_after is not _NO_NORMAL:
        term_after.update(graph_after)
    return term_after


def _checked_loop_flow(loop, flows):
    """Validate one sealed loop once during one abstract transfer."""
    from . import ssa

    if flows is None:
        return ssa.loop_flow_facts(loop)
    cached = flows.get(id(loop))
    if cached is not None and cached[0] is loop:
        return cached[1]
    ssa_cache = flows.setdefault(_SSA_FLOW_CACHE, {})
    checked = ssa.loop_flow_facts(loop, _cache=ssa_cache)
    flows[id(loop)] = (loop, checked)
    return checked


def _loop_trip_range(loop, env, flows=None):
    """Return the finite semantic trip interval and its checked flow proof."""
    inferred = of_expression(loop.trip_count, env)
    if inferred is not None:
        inferred = Range(max(0, inferred.lo), max(0, inferred.hi))

    flow = loop.flow
    if flow is None:
        return inferred, None

    flow = _checked_loop_flow(loop, flows)
    planned = Range(flow.minimum, flow.maximum)
    if inferred is None:
        return planned, flow

    lower = max(inferred.lo, planned.lo)
    upper = min(inferred.hi, planned.hi)
    if lower > upper:
        # The term range is path-insensitive across compiler predicates,
        # whereas ``LoopFlow`` describes only source paths that reach this
        # loop.  A nested loop under ``outer < k`` can consequently be
        # visited here with the abstract value from the false arm even though
        # that source path never enters the loop.  Neither fact may be chosen
        # as the sole authority in that case: take their finite hull.  This is
        # conservative for carrier planning and avoids turning lost
        # correlation into either an untyped failure or a narrower trace set.
        return Range(
            min(inferred.lo, planned.lo),
            max(inferred.hi, planned.hi),
        ), flow
    return Range(lower, upper), flow


def loop_trip_range(loop, env):
    """Finite task-domain trip interval of one retained semantic loop."""
    return _loop_trip_range(loop, env)[0]


def _post(cmd, env, witness=None, _flows=None, _point_product=1, *, _guard=None):
    """The environment a command leaves, given the one it reads.

    An assignment overwrites, since within one step a variable holds what the
    last command wrote and not also what it held before. A conditional
    evaluates its arms against the environment it was given and joins what
    they leave, rather than running one arm on what the other left. A loop is
    taken to its bound, stopping as soon as the environment stops moving,
    because taking the body once misses what iterating accumulates: two turns
    of ``x = x + 1`` from zero reach two, and one pass reports one.

    ``env`` is written into rather than copied. Copying belongs where control
    forks, since that is where two environments really exist, and copying at
    every command would cost the whole environment per command on a step that
    holds thousands of variables.
    """
    if _flows is None:
        _flows = {}
    if _RANGE_CHECKPOINT in _flows:
        _flows[_RANGE_CHECKPOINT]()
    if _flows.get(_SOURCE_PATH_RANGES, False):
        region = _flows.get(_SOURCE_REGION_TRANSFERS, {}).get(cmd)
        if region is not None:
            prefix, graph = region
            for binding in prefix:
                if _post(binding, env, witness, _flows, _point_product) is _NO_NORMAL:
                    return _NO_NORMAL
            result = graph_post(graph, env, witness, _flows=_flows,
                                _point_product=_point_product, _recursive_witness=True)
            env.clear()
            if result is _NO_NORMAL:
                return result
            env.update(result)
            return env
    if witness is not None and not isinstance(cmd, ir.Seq):
        # Before the command runs, since what it reads is what a caller is asking
        # about. A `Seq` is not a command, so it is not offered.
        witness(cmd, env)
    if isinstance(cmd, ir.Assign):
        rng = _value_range(cmd.expr, env, _flows)
        if rng is None:
            env.pop(cmd.var, None)
        else:
            env[cmd.var] = rng
        return env
    if isinstance(cmd, ir.Store):
        rng = _stored_range(env.get(cmd.var), of_expression(cmd.index, env),
                            _value_range(cmd.expr, env, _flows))
        if rng is None:
            env.pop(cmd.var, None)
        else:
            env[cmd.var] = rng
        return env
    if isinstance(cmd, ir.Seq):
        # Flattened rather than recursed into, for the reason every other walk
        # over a term in this package is: a sequence is nested one node per
        # command, so the depth follows the length of the unrolled step and not
        # the nesting of the source. The environment is written into, so running
        # the parts in order is the whole of the rule.
        for part, guard in _sequence_predicates(cmd, _flows):
            if _post(
                    part, env, witness, _flows,
                    _point_product, _guard=guard) is _NO_NORMAL:
                return _NO_NORMAL
        return env
    if isinstance(cmd, ir.If):
        # Refine each arm by the condition under which it runs.  A failing arm
        # has no normal successor and therefore contributes nothing to the
        # join; treating its unknown environment as a live path would erase a
        # fact established by the guard that excludes it.
        guard, copies = (cmd.guard, ()) if _guard is None else _guard
        footprint, _written = _range_footprint(cmd, _flows)
        # A recovered guard can differ from cmd.guard, but its lexical reads
        # do not change between loop visits. Reuse syntax dependencies only;
        # _finite_assumption still keys refinement by the current bindings.
        footprint = footprint.union(_expression_names_cached(guard, _flows))
        footprint = footprint | _copy_names(copies)
        local = {
            name: env[name] for name in footprint if name in env
        }
        # Nested loop visits frequently repeat a predicated region with the
        # same local inputs even when the surrounding loop state changes.
        # Reuse its complete transfer, never observations or interrupted work.
        # Keep the actual guard object alive: recovered guards can differ
        # from cmd.guard, and an identity integer alone could be recycled.
        cache = None
        joined = _NO_RANGE
        if witness is None:
            cache = _flows.setdefault(_COMMAND_POST_CACHE, {})
            key = (cmd, guard, copies, _flows.get(_SOURCE_PATH_RANGES, False),
                   min(_point_product, _POINTWISE_LOOP_COMBINATION_LIMIT + 1),
                   tuple(sorted(local.items())))
            joined = _range_cache_get(cache, key)
        if joined is _NO_RANGE:
            live = []
            for truth, arm in ((True, cmd.then), (False, cmd.orelse)):
                entered = _finite_assumption(
                    guard, truth, local, copies, cache=_flows)
                if entered is None:
                    continue
                left = _post(arm, entered, witness, _flows, _point_product)
                if left is not _NO_NORMAL:
                    live.append(left)
            joined = live[0] if live else _NO_NORMAL
            for state in live[1:]:
                joined = _join_envs(joined, state, _flows)
            if cache is not None:
                _range_cache_put(cache, key, joined)
        if joined is _NO_NORMAL:
            env.clear()
            return _NO_NORMAL
        for name in footprint:
            env.pop(name, None)
        env.update(joined)
        return env
    if isinstance(cmd, ir.For):
        # A parent loop's private observer only recovers entries of that
        # parent's sealed CFG.  It has already observed this nested For and
        # does not need its descendants.  A caller-supplied observer, however,
        # continues through the complete command tree.
        direct_summary = type(witness) is _DirectCommandWitness
        if type(witness) is _LoopBodyWitness:
            body_witness = witness.external
        elif direct_summary:
            body_witness = None
        else:
            body_witness = witness
        # A direct CFG witness deliberately stops at retained loop
        # boundaries.  It needs the loop's joined postcondition, not a
        # pointwise image for every concrete index; each loop's sealed local
        # CFG is visited separately by the position planner.  Start this
        # summary beyond the pointwise budget so nested loops inherit the same
        # policy and cannot make an outer occurrence scan enumerate their
        # Cartesian product.
        analysis_product = (
            _POINTWISE_LOOP_COMBINATION_LIMIT + 1
            if direct_summary else _point_product)
        cached_post = None
        if body_witness is None:
            footprint, _written = _range_footprint(cmd, _flows)
            ordered = tuple(sorted(footprint))
            key = (
                id(cmd),
                _flows.get(_SOURCE_PATH_RANGES, False),
                min(analysis_product,
                    _POINTWISE_LOOP_COMBINATION_LIMIT + 1),
                tuple((name, env.get(name, _MISSING_RANGE_INPUT))
                      for name in ordered),
            )
            cache = _flows.setdefault(_COMMAND_POST_CACHE, {})
            found = _range_cache_get(cache, key)
            if found is _NO_NORMAL:
                return _NO_NORMAL
            if found is not _NO_RANGE:
                for name in ordered:
                    env.pop(name, None)
                env.update(found)
                return env
            cached_post = cache, key, ordered

        def finish(result):
            if cached_post is not None:
                cache, key, ordered = cached_post
                _range_cache_put(cache, key, (
                    _NO_NORMAL if result is _NO_NORMAL else
                    {name: result[name] for name in ordered if name in result}
                ))
            return result

        trip_range, flow = _loop_trip_range(cmd, env, _flows)
        if trip_range is None:
            # There is no finite semantic postcondition to claim.  The
            # encoding plan reports the unanswered trip bound by occurrence;
            # dropping the environment here keeps every downstream width
            # proof fail-closed without pretending the loop has no normal
            # execution.
            _footprint, written = _range_footprint(cmd, _flows)
            for name in written:
                env.pop(name, None)
            return finish(env)
        maximum = trip_range.hi
        minimum = trip_range.lo
        # Allocation planning has a second, CFG-backed proof of each retained
        # loop's occurrence-wide maximum.  The term transformer can be weaker
        # after if-conversion (in particular, it can accumulate mutually
        # exclusive sequence growth).  Let that internal planning witness cap
        # the visits it asks us to enumerate.  A smaller certified maximum also
        # drops the term transformer's lower bound: the latter may describe only
        # an infeasible abstract path, while zero is always a sound lower bound.
        certified_maximum = getattr(
            body_witness, "certified_loop_maximum", None)
        if certified_maximum is not None:
            certified = certified_maximum(cmd, maximum)
            if type(certified) is not int or certified < 0:
                raise TypeError(
                    "a certified retained-loop maximum must be a natural number")
            if certified < maximum:
                maximum = certified
                minimum = 0
        if maximum == 0:
            return finish(env)
        # Exact point indices preserve useful cell precision while the complete
        # nest has only a small number of index combinations.  Counting each
        # loop separately would turn four nine-trip loops into 9^4 transfers
        # while claiming to observe a limit of sixteen.  Beyond the cumulative
        # budget, one interval for every possible turn is the standard sound
        # abstract index.  Stop once that abstract state is unchanged; a
        # non-stationary recurrence still runs once per possible trip and keeps
        # its finite-horizon bound.
        point_product = analysis_product * maximum
        exact_indices = getattr(
            body_witness, "requires_pointwise_loop_indices", False)
        index_support = (
            Range(0, maximum - 1)
            if (point_product > _POINTWISE_LOOP_COMBINATION_LIMIT
                and not exact_indices) else None)
        # A loop body can observe only its read/write footprint. Project even
        # while a planning witness is installed: the witness is invoked before
        # each command and every value that command can read is in this same
        # footprint. Carrying unrelated compiler temporaries through every
        # finite turn changes no range fact and makes planning quadratic in the
        # surrounding procedure size.
        projected = True
        footprint, _written = _range_footprint(cmd, _flows)
        current = {
            name: env[name] for name in footprint if name in env
        }
        mapped = _independent_array_loop_post(
            cmd, current, minimum, maximum, body_witness, flow, _flows,
            point_product)
        if mapped is not None:
            if mapped is _NO_NORMAL:
                env.clear()
                return finish(_NO_NORMAL)
            for name in footprint:
                env.pop(name, None)
            env.update(mapped)
            return finish(env)
        accelerated = _finite_linear_loop_post(
            cmd, current, maximum, body_witness, flow, _flows,
            point_product)
        if accelerated is not None:
            if projected:
                for name in footprint:
                    env.pop(name, None)
            else:
                env.clear()
            env.update(accelerated)
            return finish(env)
        joined = None
        overwritten = (_overwritten_before_read(cmd.body)
                       if index_support is not None else frozenset())
        for turn in range(maximum + 1):
            if turn >= minimum:
                joined = (dict(current) if joined is None
                          else _join_envs(joined, current, _flows))
            if turn == maximum:
                break
            current[cmd.index] = (
                Range(turn, maximum - 1) if index_support is not None
                else Range(turn, turn))
            before_turn = dict(current)
            entered = _enter_witness_loop(body_witness, cmd, turn)
            try:
                body_after = _retained_loop_body_post(
                    cmd, current, body_witness, flow, _flows,
                    point_product)
            finally:
                _leave_witness_loop(body_witness, cmd, turn, entered)
            if body_after is _NO_NORMAL:
                break
            if (index_support is not None
                    and {name: value for name, value in current.items()
                         if name not in overwritten}
                    == {name: value for name, value in before_turn.items()
                        if name not in overwritten}
                    and not getattr(
                        body_witness, "requires_complete_loop_visits", False)):
                # All remaining normal exit counts produce this same abstract
                # state. The index interval covers this turn and its suffix,
                # not already-completed turns; every later suffix is a subset,
                # so the fixed-point proof still covers every remaining visit.
                # A name the body assigns before reading it, such as the
                # source target copied from the index, cannot carry one turn's
                # value into the next. Its suffix image changes every turn, so
                # comparing it would make every such loop run once per trip.
                # Its later images are subsets of this turn's, which is joined.
                # Include it even before the loop's minimum trip count.
                if maximum >= minimum:
                    joined = (dict(current) if joined is None else
                              _join_envs(joined, current, _flows))
                break
        if joined is None:
            env.clear()
            return finish(_NO_NORMAL)
        if projected:
            for name in footprint:
                env.pop(name, None)
        else:
            env.clear()
        env.update(joined)
        return finish(env)
    if isinstance(cmd, ir.Fail):
        env.clear()
        return _NO_NORMAL
    return env


def _expression_names_cached(expr, cache):
    names = cache.setdefault(_EXPRESSION_NAME_CACHE, {})
    # Compose bottom-up so asking for every subterm's dependencies does not
    # repeatedly traverse a deep arithmetic DAG. Keep the nodes alive for the
    # lifetime of this transfer's identity-keyed entries.
    pending = [(expr, False)]
    while pending:
        node, expanded = pending.pop()
        identity = id(node)
        if identity in names:
            continue
        children = _children(node)
        if not expanded:
            pending.append((node, True))
            pending.extend((child, False) for child in reversed(children))
            continue
        dependencies = {node.name} if isinstance(node, ir.Var) else set()
        for child in children:
            dependencies.update(names[id(child)][1])
        names[identity] = (node, tuple(sorted(dependencies)))
    return names[id(expr)][1]


def _value_range(expr, env, cache=None):
    """Range of an assigned value, retaining a finite array product locally."""

    # A named value is already the current immutable range, including its
    # array cells. Memoizing this dictionary lookup adds dependency traversal
    # and another dictionary lookup without avoiding any computation.
    if isinstance(expr, ir.Var):
        return env.get(expr.name)
    if isinstance(expr, ir.Lit):
        return Range(expr.value, expr.value)
    if cache is not None:
        key = (
            id(expr),
            tuple((name, env.get(name, _MISSING_RANGE_INPUT))
                  for name in _expression_names_cached(expr, cache)),
        )
        values = cache.setdefault(_VALUE_RANGE_CACHE, {})
        found = _range_cache_get(values, key)
        if found is not _NO_RANGE:
            return None if found is _NO_NORMAL else found

        result = _value_range(expr, env)
        _range_cache_put(values, key, _NO_NORMAL if result is None else result)
        return result

    if isinstance(expr, ir.Op) and expr.op == "arraylit":
        cells = tuple(_value_range(item, env) for item in expr.args)
        return (None if any(cell is None for cell in cells)
                else _CellRange(cells))
    if isinstance(expr, ir.Op) and expr.op == "arrayfill" \
            and len(expr.args) == 2:
        cell = _value_range(expr.args[0], env)
        length = _value_range(expr.args[1], env)
        if (cell is None or length is None or length.lo != length.hi
                or type(length.lo) is not int or length.lo < 0):
            return None
        return _UniformCellRange(length.lo, cell)
    if isinstance(expr, ir.Op) and expr.op == "store" and len(expr.args) == 3:
        array = _value_range(expr.args[0], env)
        if array is None:
            return None
        return _stored_range(array, of_expression(expr.args[1], env),
                             _value_range(expr.args[2], env))
    if isinstance(expr, ir.Op) and expr.op == "ite" and len(expr.args) == 3:
        if _ordered_choice(expr) is not None:
            # Use both operands' unconditional intervals. Combining intervals
            # from different selected paths as a min/max would be unsound.
            return of_expression(expr, env, env)
        values = []
        for truth, branch in ((True, expr.args[1]),
                              (False, expr.args[2])):
            entered = _finite_assumption(expr.args[0], truth, env)
            if entered is None:
                continue
            value = _value_range(branch, entered)
            if value is None:
                return None
            values.append(value)
        if not values:
            return None
        result = values[0]
        for value in values[1:]:
            result = result.join(value)
        return result
    return of_expression(expr, env, env)
