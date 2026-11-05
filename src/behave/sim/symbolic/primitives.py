"""The single semantic registry for Behavior IR primitives.

A primitive is admitted only through :data:`REGISTRY`.  One immutable record
then supplies every stage with the same name, type scheme, concrete
denotation, range transformer and exact symbolic operation.  Source operators
are bindings to those records; they are not a second primitive table.

Partial Python operations are guarded by the frontend before their total IR
primitive is emitted.  Division, modulo and shifts therefore have total
denotations here (zero outside the Python domain), while their source bindings
carry the domain and artifact error that lowering must establish first.

``INTEGER`` denotes mathematical integers, as Python source arithmetic does.
A task field's width and signedness refine its finite boundary carrier; they do
not silently make ``add`` or another ordinary primitive modular.  Fixed-width
behavior is instead named by the wrap, saturation, comparison and shift
primitives below.  Their values are still mathematical integers: a positive
literal ``WIDTH`` parameter says which finite carrier is being observed, and
``SHIFT`` says that the count is in the total non-negative IR domain.
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from .values import copy_value as _copy

__all__ = [
    "ARRAY",
    "ARRAY_LENGTH",
    "ARRAY_OF_SAME",
    "BOOLEAN",
    "INTEGER",
    "INTEGER_TREE",
    "REGISTRY",
    "REGISTRY_VERSION",
    "SAME",
    "SAME_SCALAR",
    "SCALAR",
    "SHIFT",
    "SOURCE_OPERATIONS",
    "VALUE",
    "WIDTH",
    "PrimitiveInterval",
    "PrimitiveRegistry",
    "PrimitiveRegistryError",
    "PrimitiveSpec",
    "SourceBinding",
    "SourceDomain",
    "SourceFamily",
    "TypeSignature",
    "TypeTerm",
    "argument_domain_error",
    "primitive",
    "primitive_names",
    "source_intrinsic",
    "source_intrinsic_names",
    "source_primitive",
]


REGISTRY_VERSION = "behavior-primitives/15"

# Source-level questions eliminated before the IR boundary.  They are named
# here only so the boundary can distinguish an unfinished lowering from an
# entirely unknown operation.
SOURCE_OPERATIONS = frozenset({"len"})


class PrimitiveRegistryError(ValueError):
    """A primitive registry is incomplete or internally inconsistent."""


class TypeTerm(Enum):
    """One term in a primitive's polymorphic type scheme."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    SCALAR = "boolean-or-integer"
    SAME = "a"
    SAME_SCALAR = "scalar-a"
    INTEGER_TREE = "integer-tree-a"
    ARRAY = "array[a]"
    ARRAY_LENGTH = "nonnegative-literal-array-length"
    ARRAY_OF_SAME = "array[n,a]"
    WIDTH = "positive-literal-width"
    SHIFT = "nonnegative-shift"


BOOLEAN = TypeTerm.BOOLEAN
INTEGER = TypeTerm.INTEGER
# Compatibility spelling for older registry clients.  VALUE does not denote a
# Boolean-or-integer union; it is exactly INTEGER.
VALUE = INTEGER
SCALAR = TypeTerm.SCALAR
SAME = TypeTerm.SAME
SAME_SCALAR = TypeTerm.SAME_SCALAR
INTEGER_TREE = TypeTerm.INTEGER_TREE
ARRAY = TypeTerm.ARRAY
ARRAY_LENGTH = TypeTerm.ARRAY_LENGTH
ARRAY_OF_SAME = TypeTerm.ARRAY_OF_SAME
WIDTH = TypeTerm.WIDTH
SHIFT = TypeTerm.SHIFT


def argument_domain_error(term, *, literal=False, value=None, bounds=None):
    """Return why one refined primitive argument is outside its IR domain.

    ``WIDTH`` and ``SHIFT`` are integer refinements, not alternative scalar
    carriers.  Keeping their rules here makes the registry's signature the one
    authority read by the type checker, interpreter, range planner and exact
    encoder.  ``bounds`` is an interval-like object with ``lo``/``hi`` fields;
    it lets symbolic admission prove a dynamic shift count non-negative.
    """
    if term in (WIDTH, ARRAY_LENGTH):
        if not literal:
            return ("requires a positive literal width" if term is WIDTH
                    else "requires a nonnegative literal array length")
        if type(value) is not int or value < (1 if term is WIDTH else 0):
            return ("requires a positive literal width" if term is WIDTH
                    else "requires a nonnegative literal array length")
        # This is the scalar representation capability already reported by
        # the frontend.  Check before constructing 2**width so an adversarial
        # literal cannot allocate an unbounded host integer during planning.
        from .capabilities import SCALAR_BITS

        if term is WIDTH and value > SCALAR_BITS:
            return (f"uses width {value}, over the reported fixed-width "
                    f"capability of {SCALAR_BITS}")
        if term is ARRAY_LENGTH and value.bit_length() > SCALAR_BITS:
            return (f"uses an array length needing {value.bit_length()} bits, "
                    f"over the reported scalar capability of {SCALAR_BITS}")
        return None
    if term is SHIFT:
        if value is not None:
            if type(value) is not int or value < 0:
                return "requires a nonnegative shift count"
            return None
        if bounds is not None and (bounds.lo is None or bounds.lo < 0):
            return "has a shift count whose range is not nonnegative"
    return None


@dataclass(frozen=True, slots=True)
class TypeSignature:
    """Argument and result types for one primitive.

    ``SAME`` is one type variable shared by every occurrence in the signature;
    ``INTEGER_TREE`` binds that same variable to an integer or a fixed recursive
    array with integer leaves (including its exact shape).
    ``ARRAY`` is a fixed array of that element type.  A variadic signature
    repeats its sole argument term zero or more times.
    """

    arguments: tuple[TypeTerm, ...]
    result: TypeTerm
    variadic: bool = False

    def __post_init__(self):
        object.__setattr__(self, "arguments", tuple(self.arguments))
        if self.variadic and len(self.arguments) != 1:
            raise PrimitiveRegistryError(
                "a variadic primitive needs exactly one repeated type term")
        if not self.arguments:
            raise PrimitiveRegistryError("a primitive needs an argument type")
        if any(not isinstance(term, TypeTerm) for term in (
                *self.arguments, self.result)):
            raise PrimitiveRegistryError("a primitive has an invalid type term")

    def terms_for(self, arity):
        if self.variadic:
            return self.arguments * arity
        return self.arguments if len(self.arguments) == arity else None


class SourceFamily(Enum):
    """The Python syntax category bound to a primitive."""

    BINARY = "binary"
    UNARY = "unary"
    COMPARISON = "comparison"


class SourceDomain(Enum):
    """Domain of a partial binary source operator.

    Core IR primitives are total.  A source binding whose Python operation is
    partial names both this domain and the error emitted before the total IR
    primitive is evaluated.
    """

    RIGHT_NONZERO = "right-nonzero"
    RIGHT_NONNEGATIVE = "right-nonnegative"


@dataclass(frozen=True, slots=True)
class SourceBinding:
    family: SourceFamily
    operator: type

    def __post_init__(self):
        roots = {
            SourceFamily.BINARY: ast.operator,
            SourceFamily.UNARY: ast.unaryop,
            SourceFamily.COMPARISON: ast.cmpop,
        }
        if not isinstance(self.operator, type) or not issubclass(
                self.operator, roots[self.family]):
            raise PrimitiveRegistryError(
                f"{self.operator!r} is not a {self.family.value} operator")


@dataclass(frozen=True, slots=True)
class PrimitiveInterval:
    """Inclusive interval returned by a primitive range transformer.

    Either endpoint may be open for structural planning.  Exact encoding uses
    only results whose two endpoints are finite.
    """

    lo: int | None
    hi: int | None

    def __post_init__(self):
        if self.lo is not None:
            object.__setattr__(self, "lo", int(self.lo))
        if self.hi is not None:
            object.__setattr__(self, "hi", int(self.hi))
        if (self.lo is not None and self.hi is not None
                and self.hi < self.lo):
            raise PrimitiveRegistryError(
                f"a primitive interval from {self.lo} to {self.hi}")

    @property
    def magnitude(self):
        if self.lo is None or self.hi is None:
            raise PrimitiveRegistryError("the magnitude of an open interval")
        return max(abs(self.lo), abs(self.hi))


@dataclass(frozen=True, slots=True)
class PrimitiveSpec:
    """All semantic components required to admit one core primitive."""

    name: str
    version: int
    signature: TypeSignature
    denotation: object
    range_transform: object
    symbolic: object
    range_capability_error: object | None = None
    sources: tuple[SourceBinding, ...] = ()
    source_error: str | None = None
    source_domain: SourceDomain | None = None
    source_symbol: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "sources", tuple(self.sources))
        if not self.name or not self.name.isidentifier():
            raise PrimitiveRegistryError(
                f"invalid primitive name {self.name!r}")
        if type(self.version) is not int or self.version < 1:
            raise PrimitiveRegistryError(
                f"invalid version for primitive {self.name}")
        if not isinstance(self.signature, TypeSignature):
            raise PrimitiveRegistryError(
                f"primitive {self.name} has no type signature")
        for field in ("denotation", "range_transform", "symbolic"):
            if not callable(getattr(self, field)):
                raise PrimitiveRegistryError(
                    f"primitive {self.name} has no {field.replace('_', ' ')}")
        if (self.range_capability_error is not None
                and not callable(self.range_capability_error)):
            raise PrimitiveRegistryError(
                f"primitive {self.name} has an invalid range capability")
        if any(not isinstance(binding, SourceBinding)
               for binding in self.sources):
            raise PrimitiveRegistryError(
                f"primitive {self.name} has an invalid source binding")
        if (self.source_error is None) != (self.source_domain is None):
            raise PrimitiveRegistryError(
                f"primitive {self.name} must declare its source error and "
                "source domain together")
        if self.source_domain is not None and not isinstance(
                self.source_domain, SourceDomain):
            raise PrimitiveRegistryError(
                f"primitive {self.name} has an invalid source domain")
        if self.source_domain is not None and not any(
                binding.family is SourceFamily.BINARY
                for binding in self.sources):
            raise PrimitiveRegistryError(
                f"primitive {self.name} has a partial domain but no binary "
                "source binding")
        if self.source_symbol is not None:
            if (not isinstance(self.source_symbol, str)
                    or not self.source_symbol.isidentifier()):
                raise PrimitiveRegistryError(
                    f"primitive {self.name} has an invalid source symbol")
            if self.sources:
                raise PrimitiveRegistryError(
                    f"primitive {self.name} has both operator and intrinsic "
                    "source bindings")

    @property
    def identity(self):
        return f"{self.name}/{self.version}"


@dataclass(frozen=True, slots=True)
class PrimitiveRegistry:
    """An immutable, closed collection of complete primitive specifications."""

    version: str
    _specs: object
    _sources: object
    _intrinsics: object

    def __init__(self, version, specs):
        if not isinstance(version, str) or not version:
            raise PrimitiveRegistryError("a primitive registry needs a version")
        by_name = {}
        by_source = {}
        by_intrinsic = {}
        for spec in tuple(specs):
            if not isinstance(spec, PrimitiveSpec):
                raise PrimitiveRegistryError(
                    f"not a primitive specification: {spec!r}")
            if spec.name in by_name:
                raise PrimitiveRegistryError(
                    f"duplicate primitive {spec.name}")
            by_name[spec.name] = spec
            for binding in spec.sources:
                key = (binding.family, binding.operator)
                if key in by_source:
                    raise PrimitiveRegistryError(
                        f"duplicate {binding.family.value} binding for "
                        f"{binding.operator.__name__}")
                by_source[key] = spec
            if spec.source_symbol is not None:
                if spec.source_symbol in by_intrinsic:
                    raise PrimitiveRegistryError(
                        f"duplicate intrinsic source binding "
                        f"{spec.source_symbol}")
                by_intrinsic[spec.source_symbol] = spec
        if not by_name:
            raise PrimitiveRegistryError("an empty primitive registry")
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "_specs", MappingProxyType(by_name))
        object.__setattr__(self, "_sources", MappingProxyType(by_source))
        object.__setattr__(self, "_intrinsics",
                           MappingProxyType(by_intrinsic))

    @property
    def identity(self):
        return (self.version,
                tuple(spec.identity for spec in self._specs.values()))

    @property
    def names(self):
        return frozenset(self._specs)

    @property
    def specs(self):
        return tuple(self._specs.values())

    def get(self, name):
        try:
            return self._specs[name]
        except KeyError:
            raise PrimitiveRegistryError(
                f"unregistered primitive {name}") from None

    def source(self, family, operator_type):
        if not isinstance(family, SourceFamily):
            family = SourceFamily(family)
        return self._sources.get((family, operator_type))

    @property
    def intrinsic_names(self):
        return frozenset(self._intrinsics)

    def intrinsic(self, symbol):
        try:
            return self._intrinsics[symbol]
        except KeyError:
            raise PrimitiveRegistryError(
                f"unregistered source intrinsic {symbol}") from None


def _truth(value):
    return bool(value)


def _to_integer(value):
    return int(value)


def _store(array, index, value):
    from .uniform_array import UniformArray

    if type(array) is UniformArray:
        return array.store(index, value)
    result = _copy(array)
    if 0 <= index < len(result):
        result[index] = _copy(value)
    return result


def _conditional(condition, when_true, when_false):
    return _copy(when_true if condition else when_false)


def _floor_div(a, b):
    return 0 if b == 0 else a // b


def _floor_mod(a, b):
    return 0 if b == 0 else a % b


def _left_shift(a, b):
    """Total IR denotation; source lowering fails before a negative count."""
    return 0 if b < 0 else a << b


def _right_shift(a, b):
    """Total IR denotation; source lowering fails before a negative count."""
    return 0 if b < 0 else a >> b


def _unsigned_wrap(value, width):
    """The low ``width`` bits, read as a non-negative integer."""
    return value % (1 << width)


def _signed_wrap(value, width):
    """The low ``width`` bits, read as a two's-complement integer."""
    pattern = _unsigned_wrap(value, width)
    sign = 1 << (width - 1)
    return pattern - (1 << width) if pattern & sign else pattern


def _unsigned_saturate(value, width):
    """Clamp a mathematical integer to the unsigned ``width``-bit range."""
    return min(max(value, 0), (1 << width) - 1)


def _signed_saturate(value, width):
    """Clamp a mathematical integer to the signed ``width``-bit range."""
    bound = 1 << (width - 1)
    return min(max(value, -bound), bound - 1)


def _fixed_compare(function, signed):
    convert = _signed_wrap if signed else _unsigned_wrap
    return lambda left, right, width: _truth(
        function(convert(left, width), convert(right, width)))


def _fixed_shift(value, amount, width, *, signed, direction):
    """One total fixed-width shift on its declared non-negative domain.

    Counts at least as large as the width have the SMT/RTL bit-vector result:
    zero for left and logical-right shifts, and the sign fill for arithmetic
    right shift.  A negative count is outside the ``SHIFT`` argument domain and
    is rejected by every consumer before this denotation is called.
    """
    if amount < 0:
        raise ValueError("a fixed-width shift count is negative")
    pattern = _unsigned_wrap(value, width)
    if direction == "left":
        shifted = 0 if amount >= width else (pattern << amount)
    elif direction == "logical-right":
        shifted = 0 if amount >= width else (pattern >> amount)
    else:
        signed_value = _signed_wrap(pattern, width)
        shifted = signed_value >> amount
    convert = _signed_wrap if signed else _unsigned_wrap
    return convert(shifted, width)


def _fixed_shift_denotation(*, signed, direction):
    return lambda value, amount, width: _fixed_shift(
        value, amount, width, signed=signed, direction=direction)


def _array_literal(*values):
    return [_copy(value) for value in values]


def _array_fill(value, length):
    from .uniform_array import UniformArray

    return UniformArray.uniform(length, value)


def _interval(lo, hi):
    return PrimitiveInterval(lo, hi)


def _join(parts):
    if not parts or any(part is None for part in parts):
        return None
    lo = (None if any(part.lo is None for part in parts)
          else min(part.lo for part in parts))
    hi = (None if any(part.hi is None for part in parts)
          else max(part.hi for part in parts))
    return _interval(lo, hi)


def _range_add(a, b):
    if a is None or b is None:
        return None
    return _interval(
        None if a.lo is None or b.lo is None else a.lo + b.lo,
        None if a.hi is None or b.hi is None else a.hi + b.hi)


def _range_sub(a, b):
    if a is None or b is None:
        return None
    return _interval(
        None if a.lo is None or b.hi is None else a.lo - b.hi,
        None if a.hi is None or b.lo is None else a.hi - b.lo)


def _range_mul(a, b):
    if a is None or b is None:
        return None
    if None not in (a.lo, a.hi, b.lo, b.hi):
        corners = (a.lo * b.lo, a.lo * b.hi,
                   a.hi * b.lo, a.hi * b.hi)
        return _interval(min(corners), max(corners))
    for point, other in ((a, b), (b, a)):
        if point.lo is not None and point.lo == point.hi:
            value = point.lo
            if value == 0:
                return _interval(0, 0)
            if value > 0:
                return _interval(
                    None if other.lo is None else value * other.lo,
                    None if other.hi is None else value * other.hi)
            return _interval(
                None if other.hi is None else value * other.hi,
                None if other.lo is None else value * other.lo)
    return None


def _range_div(a, b):
    if a is None or b is None:
        return None
    if b.lo is not None and b.lo == b.hi == 0:
        return _interval(0, 0)
    if b.lo is not None and b.lo == b.hi and b.lo != 0:
        # Division by a fixed nonzero integer is monotone, so an open
        # numerator endpoint remains open without losing the other endpoint.
        # Structural planning often needs only that finite side (for example,
        # an upper bound on a loop trip count).
        divisor = b.lo
        if divisor > 0:
            return _interval(
                None if a.lo is None else a.lo // divisor,
                None if a.hi is None else a.hi // divisor)
        return _interval(
            None if a.hi is None else a.hi // divisor,
            None if a.lo is None else a.lo // divisor)
    if a.lo is not None and a.hi is not None \
            and b.lo is not None and b.lo > 0 and b.hi is None:
        # With a positive denominator bounded away from zero, an unbounded
        # upper endpoint approaches 0 for a nonnegative numerator and -1 for
        # a negative numerator.  Together with the smallest denominator these
        # are the only extrema.  This one-sided case is useful for structural
        # loop proofs and does not claim a width for the open denominator.
        values = [a.lo // b.lo, a.hi // b.lo]
        if a.hi >= 0:
            values.append(0)
        if a.lo < 0:
            values.append(-1)
        return _interval(min(values), max(values))
    if a.lo is not None and a.hi is not None \
            and b.lo is None and b.hi is not None and b.hi < 0:
        # Symmetric case for a negative denominator unbounded away from zero.
        values = [a.lo // b.hi, a.hi // b.hi]
        if a.hi >= 0:
            values.append(-1)
        if a.lo < 0:
            values.append(0)
        return _interval(min(values), max(values))
    if None in (a.lo, a.hi, b.lo, b.hi):
        return None
    if b.lo <= 0 <= b.hi:
        # Each nonzero side contains +1 or -1.  Those and the totalized zero
        # branch attain the extrema; dividing by the far endpoints cannot
        # enlarge this hull.  DivZero remains a separate source failure.
        lo = hi = 0
        if b.hi > 0:
            lo, hi = min(lo, a.lo), max(hi, a.hi)
        if b.lo < 0:
            lo, hi = min(lo, -a.hi), max(hi, -a.lo)
        return _interval(lo, hi)
    if b.hi < 0:
        # x // -d == -x // d, including Python's rounding toward -infinity.
        nlo, nhi, dlo, dhi = -a.hi, -a.lo, -b.hi, -b.lo
    else:
        nlo, nhi, dlo, dhi = a.lo, a.hi, b.lo, b.hi
    # For a positive denominator, the numerator endpoints determine the
    # extrema.  Its sign selects which denominator endpoint attains each one.
    return _interval(nlo // (dlo if nlo < 0 else dhi),
                     nhi // (dhi if nhi < 0 else dlo))


def _range_mod(a, b):
    if b is None:
        return None
    if b.lo is not None and b.lo == b.hi == 0:
        return _interval(0, 0)
    if b.lo is not None and b.lo == b.hi and b.lo > 0:
        return _interval(0, b.lo - 1)
    if a is None or None in (a.lo, a.hi, b.lo, b.hi):
        return None
    magnitude = max(b.magnitude, 1)
    lo = -(magnitude - 1) if a.lo < 0 or b.lo < 0 else 0
    return _interval(lo, magnitude - 1)


def _fixed_width(width):
    """The admitted point width carried by a range-transform argument."""
    if (width is None or width.lo is None or width.hi is None
            or width.lo != width.hi):
        return None
    value = width.lo
    return (value if argument_domain_error(
        WIDTH, literal=True, value=value) is None else None)


def _range_fixed_wrap(value, width, signed):
    """Exact interval hull of modular conversion, including negative inputs.

    Between two consecutive wrap discontinuities the map has slope one. Across
    a discontinuity an integer interval contains both carrier extrema. This
    retains constants and already-in-range values without assuming no overflow.
    """
    width = _fixed_width(width)
    if width is None:
        return None
    period = 1 << width
    bias = period // 2 if signed else 0
    if value is not None and value.lo is not None and value.hi is not None:
        low, high = value.lo + bias, value.hi + bias
        if low // period == high // period:
            return _interval(low % period - bias, high % period - bias)
    return _interval(-bias, period - bias - 1)


def _range_uwrap(value, width):
    return _range_fixed_wrap(value, width, False)


def _range_swrap(value, width):
    return _range_fixed_wrap(value, width, True)


def _range_fixed_saturate(signed):
    def transform(value, width):
        width = _fixed_width(width)
        if width is None:
            return None
        if signed:
            bound = 1 << (width - 1)
            lo, hi = -bound, bound - 1
        else:
            lo, hi = 0, (1 << width) - 1
        if value is None:
            return _interval(lo, hi)
        return _interval(
            lo if value.lo is None else min(max(value.lo, lo), hi),
            hi if value.hi is None else min(max(value.hi, lo), hi),
        )

    return transform


def _range_fixed_compare(_left, _right, width):
    return None if _fixed_width(width) is None else _interval(0, 1)


def _range_fixed_shift(signed, direction):
    carrier = _range_swrap if signed else _range_uwrap

    def transform(value, amount, width):
        if (amount is None or argument_domain_error(
                SHIFT, bounds=amount) is not None):
            return None
        fixed = _fixed_width(width)
        if fixed is None:
            return None
        value = carrier(value, width)
        # Beyond the declared width only zero/sign fill is possible. Clamp
        # the count before host arithmetic, including open upper bounds; the
        # cost depends on the carrier width, not on the numeric shift count.
        low = min(amount.lo, fixed)
        high = fixed if amount.hi is None else min(amount.hi, fixed)
        if direction == 'left':
            if low == fixed:
                return _interval(0, 0)
            # Before wrapping, extrema occur at interval corners. Modular
            # conversion of their hull remains conservative across overflow;
            # never apply a no-overflow assumption or wrap only endpoints.
            shifted = _range_corners(operator.lshift, value,
                                     _interval(low, min(high, fixed - 1)))
            result = carrier(shifted, width)
            return _join((result, _interval(0, 0))) if high == fixed else result
        return _range_corners(operator.rshift, value, _interval(low, high))

    return transform


def _range_corners(function, a, b):
    values = [function(x, y) for x in (a.lo, a.hi)
              for y in (b.lo, b.hi)]
    return _interval(min(values), max(values))


def _range_shl(a, b):
    if a is None or b is None or b.lo is None or b.hi is None:
        return None
    # Kept in sync with the range planner's advertised capability without
    # importing it (and thereby creating a registry/planner dependency cycle).
    from .capabilities import SHIFT_LIMIT

    if b.hi < 0:
        return _interval(0, 0)
    valid = _interval(max(0, b.lo), b.hi)
    if valid.hi > SHIFT_LIMIT:
        return None
    if a.lo is not None and a.hi is not None:
        result = _range_corners(operator.lshift, a, valid)
        return (_join((result, _interval(0, 0)))
                if b.lo < 0 else result)
    if valid.lo != valid.hi:
        return None
    shift = valid.lo
    result = _interval(None if a.lo is None else a.lo << shift,
                       None if a.hi is None else a.hi << shift)
    return (_join((result, _interval(0, 0))) if b.lo < 0 else result)


def _range_shl_capability(_value, amount):
    """Explain the finite shifts too large to retain as scalar values."""
    if amount is None or amount.lo is None or amount.hi is None:
        return None
    from .capabilities import SHIFT_LIMIT

    if amount.hi <= SHIFT_LIMIT:
        return None
    return (f"a left-shift count may reach {amount.hi}, over the reported "
            f"representability limit of {SHIFT_LIMIT}")


def _range_shr(a, b):
    if a is None or b is None or b.lo is None or b.hi is None:
        return None
    if b.hi < 0:
        return _interval(0, 0)
    valid = _interval(max(0, b.lo), b.hi)
    if a.lo is not None and a.hi is not None:
        result = _range_corners(operator.rshift, a, valid)
        return (_join((result, _interval(0, 0)))
                if b.lo < 0 else result)
    if valid.lo != valid.hi:
        return None
    shift = valid.lo
    result = _interval(None if a.lo is None else a.lo >> shift,
                       None if a.hi is None else a.hi >> shift)
    return (_join((result, _interval(0, 0))) if b.lo < 0 else result)


def _width_for_signed(lo, hi):
    width = 1
    while -(1 << (width - 1)) > lo or hi > (1 << (width - 1)) - 1:
        width += 1
    return width


def _range_widened(a, b):
    lo, hi = min(a.lo, b.lo), max(a.hi, b.hi)
    # Sign extension commutes with &, | and ^. No result needs more signed
    # bits than their widest operand; extending the interval on every pass
    # would prevent a reusable state representation from ever closing.
    width = _width_for_signed(lo, hi)
    return _interval(-(1 << (width - 1)) if lo < 0 else 0,
                     (1 << (width - 1)) - 1)


def _range_and(a, b):
    if (a is not None and b is not None and a.lo is not None
            and b.lo is not None and a.lo == a.hi and b.lo == b.hi):
        value = a.lo & b.lo
        return _interval(value, value)
    # For every integer x and nonnegative m, 0 <= x & m <= m.
    # m need not be constant; a variable mask bounds even a signed or
    # otherwise unbounded operand. Preserve this fact across state joins.
    masks = [value.hi for value in (a, b)
             if value is not None and value.lo is not None
             and value.lo >= 0 and value.hi is not None]
    if masks:
        return _interval(0, min(masks))
    if a is None or b is None or None in (a.lo, a.hi, b.lo, b.hi):
        return None
    return _range_widened(a, b)


def _range_bitwise(a, b, operation):
    if a is None or b is None or None in (a.lo, a.hi, b.lo, b.hi):
        return None
    if a.lo == a.hi and b.lo == b.hi:
        value = operation(a.lo, b.lo)
        return _interval(value, value)
    if a.lo >= 0 and b.lo >= 0:
        width = max(a.hi.bit_length(), b.hi.bit_length())
        return _interval(0, (1 << width) - 1)
    return _range_widened(a, b)


def _range_or(a, b):
    return _range_bitwise(a, b, operator.or_)


def _range_xor(a, b):
    return _range_bitwise(a, b, operator.xor)


def _range_boolean(*_parts):
    return _interval(0, 1)


def _range_truth(value):
    """Boolean hull of zero/nonzero, retaining one-sided interval facts."""
    if value is None:
        return _range_boolean()
    nonzero = ((value.lo is not None and value.lo > 0)
               or (value.hi is not None and value.hi < 0))
    return _interval(int(nonzero), int(not (value.lo == value.hi == 0)))


def _range_not(value):
    value = _range_truth(value)
    return _interval(1 - value.hi, 1 - value.lo)


def _range_logical(a, b, combine):
    a, b = _range_truth(a), _range_truth(b)
    return _interval(combine(a.lo, b.lo), combine(a.hi, b.hi))


def _range_equal(a, b):
    """Equality's exact hull on independent scalar intervals, not arrays."""
    if a is None or b is None:
        return _range_boolean()
    same = a.lo is not None and a.lo == a.hi == b.lo == b.hi
    disjoint = ((a.hi is not None and b.lo is not None and a.hi < b.lo)
                or (b.hi is not None and a.lo is not None and b.hi < a.lo))
    return _interval(int(same), int(not disjoint))


def _range_less(a, b, *, inclusive=False):
    """Exact Boolean hull for an ordered comparison of two intervals.

    The comparison is decreasing in its left operand and increasing in its
    right operand. Open endpoints only prevent the corresponding proof; they
    do not erase a fact established by the other pair of endpoints.
    """
    if a is None or b is None:
        return _range_boolean()
    compare = operator.le if inclusive else operator.lt
    always = (a.hi is not None and b.lo is not None
              and compare(a.hi, b.lo))
    possible = (a.lo is None or b.hi is None or compare(a.lo, b.hi))
    return _interval(int(always), int(possible))


def _range_identity(value):
    return value


def _range_neg(a):
    return (None if a is None else
            _interval(None if a.hi is None else -a.hi,
                      None if a.lo is None else -a.lo))


def _range_inv(a):
    return (None if a is None else
            _interval(None if a.hi is None else -a.hi - 1,
                      None if a.lo is None else -a.lo - 1))


def _range_bit_measure(a):
    if a is None:
        return None
    hi = (None if a.lo is None or a.hi is None else
          max(abs(a.lo), abs(a.hi)).bit_length())
    return _interval(0, hi)


def _range_array(*parts):
    return _join(parts)


def _range_array_fill(value, _length):
    return value


def _range_ite(condition, when_true, when_false):
    if condition is not None and condition.lo == condition.hi:
        if condition.lo == 1:
            return when_true
        if condition.lo == 0:
            return when_false
    return _join((when_true, when_false))


def _range_store(array, _index, value):
    return _join((array, value))


def _merge_bits(current, data, mask, width, *, signed):
    """Componentwise finite-bit replacement, with recursive value semantics."""
    from .uniform_array import UniformArray

    if isinstance(current, (list, UniformArray)):
        # A zero mask base changes only its explicit cells. No RAM scan is
        # needed for the common sparse deferred-write case.
        if (isinstance(current, UniformArray) and current.is_uniform
                and type(current.default) is int and isinstance(mask, UniformArray)
                and mask.is_uniform and mask.default == 0):
            result = UniformArray.uniform(len(current),
                _merge_bits(current.default, 0, 0, width, signed=signed))
            for index, value in current.sparse_items():
                result = result.store(index, _merge_bits(value, 0, 0, width, signed=signed))
            for index, bits in mask.sparse_items():
                value = _merge_bits(current[index], data[index], bits, width, signed=signed)
                result = _store(result, index, value)
            return result
        return [_merge_bits(a, b, m, width, signed=signed)
                for a, b, m in zip(current, data, mask, strict=True)]
    bits = ((current & ~mask) | (data & mask)) & ((1 << width) - 1)
    return bits - (1 << width) if signed and bits & (1 << (width - 1)) else bits


def _sym_binary(function):
    return lambda _context, args, _width: function(args[0], args[1])


def _sym_bitwise_and(context, args, width):
    return context.bitwise_and(args[0], args[1], width)


def _sym_unary(function):
    return lambda _context, args, _width: function(args[0])


def _sym_compare(function):
    return lambda context, args, width: context.truth(
        function(args[0], args[1]), width)


def _sym_floor_div(context, args, _width):
    return context.floor_div(args[0], args[1])


def _sym_floor_mod(context, args, _width):
    return context.floor_mod(args[0], args[1])


def _sym_shift(function):
    return lambda context, args, _width: context.total_shift(
        function, args[0], args[1])


def _sym_fixed_wrap(signed):
    return lambda context, args, result_width: context.fixed_wrap(
        args[0], args[1], result_width, signed=signed)


def _sym_fixed_saturate(signed):
    return lambda context, args, result_width: context.fixed_saturate(
        args[0], args[1], result_width, signed=signed)


def _sym_fixed_compare(function, signed):
    return lambda context, args, result_width: context.fixed_compare(
        function, args[0], args[1], args[2], result_width,
        signed=signed)


def _sym_fixed_shift(*, signed, direction):
    return lambda context, args, result_width: context.fixed_shift(
        args[0], args[1], args[2], result_width,
        signed=signed, direction=direction)


def _sym_not(context, args, width):
    return context.logical_not(args[0], width)


def _sym_logical_and(context, args, width):
    return context.logical_and(args[0], args[1], width)


def _sym_logical_or(context, args, width):
    return context.logical_or(args[0], args[1], width)


def _sym_popcount(context, args, width):
    return context.popcount(args[0], width)


def _sym_bit_length(context, args, width):
    return context.bit_length(args[0], width)


def _sym_ite(context, args, width):
    return context.conditional(args[0], args[1], args[2], width)


def _sym_array_literal(context, args, _width):
    return context.array_literal(args)


def _sym_array_fill(context, args, _width):
    return context.array_fill(args[0], args[1])


def _sym_store(context, args, _width):
    return context.array_store(args[0], args[1], args[2])


def _source(family, operator_type):
    return (SourceBinding(family, operator_type),)


_INTEGER_UNARY = TypeSignature((INTEGER,), INTEGER)
_INTEGER_BINARY = TypeSignature((INTEGER, INTEGER), INTEGER)
_INTEGER_COMPARE = TypeSignature((INTEGER, INTEGER), BOOLEAN)
_FIXED_CONVERSION = TypeSignature((INTEGER, WIDTH), INTEGER)
_FIXED_COMPARE = TypeSignature((INTEGER, INTEGER, WIDTH), BOOLEAN)
_FIXED_SHIFT = TypeSignature((INTEGER, SHIFT, WIDTH), INTEGER)
_EQUALITY = TypeSignature((SAME_SCALAR, SAME_SCALAR), BOOLEAN)
_BOOLEAN_UNARY = TypeSignature((BOOLEAN,), BOOLEAN)
_BOOLEAN_BINARY = TypeSignature((BOOLEAN, BOOLEAN), BOOLEAN)
_TRUTH = TypeSignature((SCALAR,), BOOLEAN)
_TO_INTEGER = TypeSignature((SCALAR,), INTEGER)
_CONDITIONAL = TypeSignature((BOOLEAN, SAME, SAME), SAME)
_ARRAY_LITERAL = TypeSignature((SAME,), ARRAY, variadic=True)
_ARRAY_FILL = TypeSignature((SAME, ARRAY_LENGTH), ARRAY_OF_SAME)
_STORE = TypeSignature((ARRAY, INTEGER, SAME), ARRAY)
_MERGE = TypeSignature((INTEGER_TREE, INTEGER_TREE, INTEGER_TREE, WIDTH), SAME)


def _spec(name, signature, denotation, range_transform, symbolic, *,
          range_capability_error=None,
          sources=(), source_error=None, source_domain=None,
          source_symbol=None, version=2):
    return PrimitiveSpec(
        name=name,
        version=version,
        signature=signature,
        denotation=denotation,
        range_transform=range_transform,
        symbolic=symbolic,
        range_capability_error=range_capability_error,
        sources=tuple(sources),
        source_error=source_error,
        source_domain=source_domain,
        source_symbol=source_symbol,
    )


REGISTRY = PrimitiveRegistry(REGISTRY_VERSION, (
    _spec("add", _INTEGER_BINARY, operator.add, _range_add,
          _sym_binary(operator.add),
          sources=_source(SourceFamily.BINARY, ast.Add)),
    _spec("sub", _INTEGER_BINARY, operator.sub, _range_sub,
          _sym_binary(operator.sub),
          sources=_source(SourceFamily.BINARY, ast.Sub)),
    _spec("mul", _INTEGER_BINARY, operator.mul, _range_mul,
          _sym_binary(operator.mul),
          sources=_source(SourceFamily.BINARY, ast.Mult)),
    _spec("fdiv", _INTEGER_BINARY, _floor_div, _range_div, _sym_floor_div,
          sources=_source(SourceFamily.BINARY, ast.FloorDiv),
          source_error="DivZero",
          source_domain=SourceDomain.RIGHT_NONZERO),
    _spec("fmod", _INTEGER_BINARY, _floor_mod, _range_mod, _sym_floor_mod,
          sources=_source(SourceFamily.BINARY, ast.Mod),
          source_error="DivZero",
          source_domain=SourceDomain.RIGHT_NONZERO),
    _spec("and", _INTEGER_BINARY, operator.and_, _range_and,
          _sym_bitwise_and,
          sources=_source(SourceFamily.BINARY, ast.BitAnd), version=4),
    _spec("or", _INTEGER_BINARY, operator.or_, _range_or,
          _sym_binary(operator.or_),
          sources=_source(SourceFamily.BINARY, ast.BitOr), version=3),
    _spec("xor", _INTEGER_BINARY, operator.xor, _range_xor,
          _sym_binary(operator.xor),
          sources=_source(SourceFamily.BINARY, ast.BitXor), version=3),
    _spec("shl", _INTEGER_BINARY, _left_shift, _range_shl,
          _sym_shift(operator.lshift),
          range_capability_error=_range_shl_capability,
          sources=_source(SourceFamily.BINARY, ast.LShift),
          source_error="NegativeShift",
          source_domain=SourceDomain.RIGHT_NONNEGATIVE,
          version=3),
    _spec("shr", _INTEGER_BINARY, _right_shift, _range_shr,
          _sym_shift(operator.rshift),
          sources=_source(SourceFamily.BINARY, ast.RShift),
          source_error="NegativeShift",
          source_domain=SourceDomain.RIGHT_NONNEGATIVE,
          version=3),
    # Fixed-width operations are deliberately separate from the ordinary
    # mathematical integer operations above.  Signedness is part of the
    # primitive identity; width is its positive literal parameter.
    _spec("uwrap", _FIXED_CONVERSION, _unsigned_wrap, _range_uwrap,
          _sym_fixed_wrap(False), source_symbol="uwrap", version=3),
    _spec("swrap", _FIXED_CONVERSION, _signed_wrap, _range_swrap,
          _sym_fixed_wrap(True), source_symbol="swrap", version=3),
    _spec("usat", _FIXED_CONVERSION, _unsigned_saturate,
          _range_fixed_saturate(False), _sym_fixed_saturate(False),
          source_symbol="usat", version=1),
    _spec("ssat", _FIXED_CONVERSION, _signed_saturate,
          _range_fixed_saturate(True), _sym_fixed_saturate(True),
          source_symbol="ssat", version=1),
    _spec("ushl", _FIXED_SHIFT,
          _fixed_shift_denotation(signed=False, direction="left"),
          _range_fixed_shift(False, "left"),
          _sym_fixed_shift(signed=False, direction="left"),
          source_symbol="ushl", version=4),
    _spec("sshl", _FIXED_SHIFT,
          _fixed_shift_denotation(signed=True, direction="left"),
          _range_fixed_shift(True, "left"),
          _sym_fixed_shift(signed=True, direction="left"),
          source_symbol="sshl", version=4),
    _spec("ulshr", _FIXED_SHIFT,
          _fixed_shift_denotation(signed=False, direction="logical-right"),
          _range_fixed_shift(False, "logical-right"),
          _sym_fixed_shift(signed=False, direction="logical-right"),
          source_symbol="ulshr", version=4),
    _spec("sashr", _FIXED_SHIFT,
          _fixed_shift_denotation(signed=True, direction="arithmetic-right"),
          _range_fixed_shift(True, "arithmetic-right"),
          _sym_fixed_shift(signed=True, direction="arithmetic-right"),
          source_symbol="sashr", version=4),
    _spec("ult", _FIXED_COMPARE, _fixed_compare(operator.lt, False),
          _range_fixed_compare, _sym_fixed_compare(operator.lt, False),
          source_symbol="ult", version=2),
    _spec("ule", _FIXED_COMPARE, _fixed_compare(operator.le, False),
          _range_fixed_compare, _sym_fixed_compare(operator.le, False),
          source_symbol="ule", version=2),
    _spec("ugt", _FIXED_COMPARE, _fixed_compare(operator.gt, False),
          _range_fixed_compare, _sym_fixed_compare(operator.gt, False),
          source_symbol="ugt", version=2),
    _spec("uge", _FIXED_COMPARE, _fixed_compare(operator.ge, False),
          _range_fixed_compare, _sym_fixed_compare(operator.ge, False),
          source_symbol="uge", version=2),
    _spec("slt", _FIXED_COMPARE, _fixed_compare(operator.lt, True),
          _range_fixed_compare, _sym_fixed_compare(operator.lt, True),
          source_symbol="slt", version=2),
    _spec("sle", _FIXED_COMPARE, _fixed_compare(operator.le, True),
          _range_fixed_compare, _sym_fixed_compare(operator.le, True),
          source_symbol="sle", version=2),
    _spec("sgt", _FIXED_COMPARE, _fixed_compare(operator.gt, True),
          _range_fixed_compare, _sym_fixed_compare(operator.gt, True),
          source_symbol="sgt", version=2),
    _spec("sge", _FIXED_COMPARE, _fixed_compare(operator.ge, True),
          _range_fixed_compare, _sym_fixed_compare(operator.ge, True),
          source_symbol="sge", version=2),
    _spec("eq", _EQUALITY, lambda a, b: _truth(a == b),
          _range_equal, _sym_compare(operator.eq),
          sources=_source(SourceFamily.COMPARISON, ast.Eq), version=3),
    _spec("ne", _EQUALITY, lambda a, b: _truth(a != b),
          lambda a, b: _range_not(_range_equal(a, b)), _sym_compare(operator.ne),
          sources=_source(SourceFamily.COMPARISON, ast.NotEq), version=3),
    _spec("lt", _INTEGER_COMPARE, lambda a, b: _truth(a < b),
          _range_less, _sym_compare(operator.lt),
          sources=_source(SourceFamily.COMPARISON, ast.Lt), version=3),
    _spec("le", _INTEGER_COMPARE, lambda a, b: _truth(a <= b),
          lambda a, b: _range_less(a, b, inclusive=True), _sym_compare(operator.le),
          sources=_source(SourceFamily.COMPARISON, ast.LtE), version=3),
    _spec("gt", _INTEGER_COMPARE, lambda a, b: _truth(a > b),
          lambda a, b: _range_less(b, a), _sym_compare(operator.gt),
          sources=_source(SourceFamily.COMPARISON, ast.Gt), version=3),
    _spec("ge", _INTEGER_COMPARE, lambda a, b: _truth(a >= b),
          lambda a, b: _range_less(b, a, inclusive=True), _sym_compare(operator.ge),
          sources=_source(SourceFamily.COMPARISON, ast.GtE), version=3),
    _spec("neg", _INTEGER_UNARY, operator.neg, _range_neg,
          _sym_unary(operator.neg),
          sources=_source(SourceFamily.UNARY, ast.USub)),
    _spec("inv", _INTEGER_UNARY, operator.invert, _range_inv,
          _sym_unary(operator.invert),
          sources=_source(SourceFamily.UNARY, ast.Invert)),
    _spec("not", _BOOLEAN_UNARY, lambda value: not value,
          _range_not, _sym_not,
          sources=_source(SourceFamily.UNARY, ast.Not), version=3),
    _spec("truth", _TRUTH, _truth, _range_truth,
          lambda context, args, width: context.truth_value(args[0], width), version=3),
    _spec("toint", _TO_INTEGER, _to_integer, _range_identity,
          lambda context, args, width: context.integer_value(args[0], width)),
    _spec("land", _BOOLEAN_BINARY, operator.and_,
          lambda a, b: _range_logical(a, b, min), _sym_logical_and, version=4),
    _spec("lor", _BOOLEAN_BINARY, operator.or_,
          lambda a, b: _range_logical(a, b, max), _sym_logical_or, version=4),
    _spec("popcount", _INTEGER_UNARY, lambda value: int(value).bit_count(),
          _range_bit_measure, _sym_popcount),
    _spec("bitlen", _INTEGER_UNARY, lambda value: int(value).bit_length(),
          _range_bit_measure, _sym_bit_length),
    _spec("ite", _CONDITIONAL, _conditional, _range_ite, _sym_ite, version=3),
    _spec("arraylit", _ARRAY_LITERAL, _array_literal, _range_array,
          _sym_array_literal),
    _spec("arrayfill", _ARRAY_FILL, _array_fill, _range_array_fill,
          _sym_array_fill),
    _spec("store", _STORE, _store, _range_store, _sym_store),
    *(_spec(name, _MERGE,
        lambda a, b, m, w, signed=signed: _merge_bits(a, b, m, w, signed=signed),
        lambda a, b, m, w, signed=signed: (_range_swrap if signed else _range_uwrap)(None, w),
        lambda context, args, width, signed=signed: context.merge_bits(*args, signed=signed))
      for name, signed in (("umerge", False), ("smerge", True))),
))


def primitive(name):
    """The complete specification for ``name``, or a fail-closed error."""
    return REGISTRY.get(name)


def primitive_names():
    """The closed set of core primitive names."""
    return REGISTRY.names


def source_primitive(family, operator_type):
    """The primitive bound to one source operator, if the registry has one."""
    return REGISTRY.source(family, operator_type)


def source_intrinsic(symbol):
    """The primitive explicitly imported as ``symbol`` by source code."""
    return REGISTRY.intrinsic(symbol)


def source_intrinsic_names():
    """The closed public surface of the source intrinsic module."""
    return REGISTRY.intrinsic_names
