"""Deterministic, type-directed payload patterns shared by all Sim frontends.

This module knows values, not pins, clocks, reset or floating-point encodings.
Cases are budgeted prefixes of named families, not coverage guarantees. No
Cartesian product of values, pairs or bit positions is materialized.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import NamedTuple

from .core.value_type import ValueType
from .generation_defaults import DEFAULT_STIMULUS_SEED

PATTERN_VERSION = "typed-patterns/4"
PATTERN_FAMILIES = (
    "low", "zero", "high", "random", "counter", "walking-one",
    "walking-zero", "boundaries", "field-boundaries", "pairwise-boundaries",
    "alternating", "exhaustive-prefix", "field-boundaries-high",
    "numeric-walk", "binary-windows",
)


class PatternCase(NamedTuple):
    family: str
    page: int = 0
    variant: str = ""

    def as_dict(self):
        return {"family": self.family, "page": self.page,
                **({"variant": self.variant} if self.variant else {})}


# Value recipes, not task roles: these trajectories apply to every finite leaf.
_WALKS = {
    "up": (1,), "down": (-1,), "wobble": (1, -1), "pulse": (1, 1, -1, -1),
    "geometric": (1, 2, 4, 8, -1, -2, -4, -8),
    "pauses": (1, 0, 1, 0, -1, 0, -1, 0), "asymmetric": (3, -1),
}
_WALK_PREFIXES = {name: tuple(sum(values[:i]) for i in range(len(values) + 1))
                  for name, values in _WALKS.items()}
PATTERN_CASES = (
    *(PatternCase(family) for family in PATTERN_FAMILIES[:13]),
    *(PatternCase("numeric-walk", variant=f"{start}/{walk}")
      for start in ("low", "zero", "high") for walk in _WALKS),
    *(PatternCase("binary-windows", page) for page in range(3)),
)
_REPEATING_CASES = tuple(case for case in PATTERN_CASES
                         if case.family not in ("low", "zero", "high", "random")
                         and (case.family != "binary-windows" or case.page == 2))


def pattern_case(index: int) -> PatternCase:
    """Primary recipes, then interleaved random samples and directed pages.

    One budget selects a stable prefix; random samples do not form an extra
    pool. Continuing directed pages keeps wider sweeps progressing as the
    budget grows, while fresh samples exercise values outside the boundaries.
    """
    if type(index) is not int or index < 0:
        raise ValueError("a pattern case index is a nonnegative exact integer")
    if index < len(PATTERN_CASES):
        return PATTERN_CASES[index]
    continuation, kind = divmod(index - len(PATTERN_CASES), 2)
    if kind == 0:
        return PatternCase("random", page=continuation + 1)
    page, position = divmod(continuation, len(_REPEATING_CASES))
    case = _REPEATING_CASES[position]
    return case._replace(page=case.page + page + 1)


@lru_cache(maxsize=1)
def _binary_cycle():
    """A binary De Bruijn cycle of order eight (256 bits, independent of width)."""
    order, sequence = 8, []
    work = [0] * (2 * order)

    def visit(t, period):
        if t > order:
            if order % period == 0:
                sequence.extend(work[1:period + 1])
            return
        work[t] = work[t - period]
        visit(t + 1, period)
        for bit in range(work[t - period] + 1, 2):
            work[t] = bit
            visit(t + 1, t)

    visit(1, 1)
    return tuple(sequence)


def seeded_number(seed: str, salt: str, modulus: int) -> int:
    """A reproducible sample with entropy spanning the whole finite carrier.

    Length-prefixing avoids seed/salt delimiter collisions. SHAKE's output
    length follows the carrier, so wide payloads are not capped at 256 bits.
    This is a sampling policy, not a claim of exact uniformity after modulo.
    """
    encoded = seed.encode("utf-8")
    payload = len(encoded).to_bytes(8, "big") + encoded + salt.encode("utf-8")
    size = max(1, ((modulus - 1).bit_length() + 7) // 8)
    return int.from_bytes(hashlib.shake_256(payload).digest(size), "big") % modulus


@dataclass(frozen=True)
class _Leaf:
    type: ValueType
    low: int
    high: int
    bits: int
    boundaries: tuple[int, ...]

    @classmethod
    def of(cls, value_type: ValueType) -> _Leaf:
        if value_type.kind == "integer":
            low, high = value_type.bounds
            bits = value_type.width
        else:
            low, high = 0, (1 if value_type.kind == "boolean"
                            else len(value_type.members) - 1)
            bits = max(1, high.bit_length())
        zero = max(low, min(high, 0))
        boundaries = tuple(dict.fromkeys(
            value for value in (low, zero, high, low + 1, high - 1, -1, 1)
            if low <= value <= high))
        return cls(value_type, low, high, bits, boundaries)

    @property
    def size(self) -> int:
        return self.high - self.low + 1

    @property
    def zero(self) -> int:
        return max(self.low, min(self.high, 0))

    def from_bits(self, value: int) -> int:
        if self.type.kind == "integer":
            if self.type.signed and value >= 1 << (self.bits - 1):
                return value - (1 << self.bits)
            return value
        # Non-power-of-two enums have no values at the unused bit tags.
        return value % self.size

    def decode(self, value: int):
        if self.type.kind == "boolean":
            return bool(value)
        if self.type.kind == "enumeration":
            return self.type.members[value]
        return value


def _leaves(value_type: ValueType):
    if type(value_type) is not ValueType:
        raise TypeError("patterns need exact ValueTypes")
    if value_type.kind == "array":
        for _ in range(value_type.cells):
            yield from _leaves(value_type.element)
    elif value_type.kind == "record":
        for _, child in value_type.fields:
            yield from _leaves(child)
    else:
        yield _Leaf.of(value_type)


def _assemble(value_type: ValueType, values):
    if value_type.kind == "array":
        return tuple(_assemble(value_type.element, values)
                     for _ in range(value_type.cells))
    if value_type.kind == "record":
        return {name: _assemble(child, values) for name, child in value_type.fields}
    return next(values)


def _pair_at(count: int, index: int) -> tuple[int, int]:
    """Lexicographic unordered pair in O(log count), without an O(count²) list."""
    low, high = 0, count - 1
    while low + 1 < high:
        middle = (low + high) // 2
        prefix = middle * (2 * count - middle - 1) // 2
        if prefix <= index:
            low = middle
        else:
            high = middle
    prefix = low * (2 * count - low - 1) // 2
    return low, low + 1 + index - prefix


class PatternPlan:
    """One compiled recursive schema; rows cost O(number of payload leaves)."""

    def __init__(self, types):
        self.types = tuple(types)
        self.leaves = tuple(leaf for value_type in self.types
                            for leaf in _leaves(value_type))
        bits, boundaries = [0], [0]
        for leaf in self.leaves:
            bits.append(bits[-1] + leaf.bits)
            boundaries.append(boundaries[-1] + len(leaf.boundaries))
        self.bit_offsets = tuple(bits)
        self.boundary_offsets = tuple(boundaries)

    def rows(self, case: int, depth: int, *, seed: str = DEFAULT_STIMULUS_SEED):
        recipe = pattern_case(case)
        # Overlap windows at trace boundaries: 3 length-100 pages include all
        # 256 eight-bit windows. Shorter budgets remain explicitly partial.
        stride = max(1, depth - 7) if recipe.family == "binary-windows" else depth
        for position in range(depth):
            yield self.row(recipe.family, recipe.page * stride + position, seed=seed,
                           sample=f"case:{case}", variant=recipe.variant)

    def row(self, family: str, position: int, *, seed: str = DEFAULT_STIMULUS_SEED,
            sample: str = "", variant: str = "") -> tuple:
        if family not in PATTERN_FAMILIES:
            raise ValueError(f"unknown stimulus family {family!r}")
        if type(position) is not int or position < 0:
            raise ValueError("pattern position is a nonnegative exact integer")
        values = [leaf.zero for leaf in self.leaves]
        if not values:
            return tuple(_assemble(value_type, iter(())) for value_type in self.types)

        if family.startswith("walking-"):
            offset = position % self.bit_offsets[-1]
            index = bisect_right(self.bit_offsets, offset) - 1
            leaf = self.leaves[index]
            bits = 1 << (offset - self.bit_offsets[index])
            if family == "walking-zero":
                bits ^= (1 << leaf.bits) - 1
            values[index] = leaf.from_bits(bits)
        elif family in ("field-boundaries", "field-boundaries-high"):
            if family == "field-boundaries-high":
                values = [leaf.high for leaf in self.leaves]
            offset = position % self.boundary_offsets[-1]
            index = bisect_right(self.boundary_offsets, offset) - 1
            values[index] = self.leaves[index].boundaries[
                offset - self.boundary_offsets[index]]
        elif family == "pairwise-boundaries" and len(values) > 1:
            pair_count = len(values) * (len(values) - 1) // 2
            combination, ordinal = divmod(position, pair_count)
            left, right = _pair_at(len(values), ordinal)
            a, b = self.leaves[left].boundaries, self.leaves[right].boundaries
            # Stagger value combinations across pairs. Without this offset the
            # entire first pass is (low, low), i.e. all-zero rows for unsigned
            # schemas, which can consume the whole trace budget. Each fixed pair
            # still visits every combination in len(a) * len(b) passes.
            combination += ordinal
            values[left] = a[(combination // len(b)) % len(a)]
            values[right] = b[combination % len(b)]
        else:
            residual = position
            if family == "numeric-walk":
                start, walk = (variant or "zero/up").split("/")
                prefix = _WALK_PREFIXES[walk]
                cycles, remainder = divmod(position + 1, len(prefix) - 1)
                displacement = cycles * prefix[-1] + prefix[remainder]
            for index, leaf in enumerate(self.leaves):
                if family == "low":
                    value = leaf.low
                elif family == "zero":
                    value = leaf.zero
                elif family == "high":
                    value = leaf.high
                elif family == "random":
                    value = leaf.low + seeded_number(
                        seed, f"{sample}/row:{position}/leaf:{index}", leaf.size)
                elif family == "counter":
                    value = leaf.low + (leaf.zero - leaf.low + position + index) % leaf.size
                elif family in ("boundaries", "pairwise-boundaries"):
                    value = leaf.boundaries[(position + index) % len(leaf.boundaries)]
                elif family == "alternating":
                    mask = (1 << leaf.bits) - 1
                    value = leaf.from_bits((mask // 3) ^ (mask if position % 2 else 0))
                elif family == "numeric-walk":
                    value = leaf.low + (getattr(leaf, start) - leaf.low + displacement) % leaf.size
                elif family == "binary-windows":
                    cycle = _binary_cycle()
                    value = leaf.high if cycle[(position + index) % len(cycle)] else leaf.low
                else:  # Mixed-radix prefix; even huge domains need no enumeration.
                    residual, digit = divmod(residual, leaf.size)
                    value = leaf.low + digit
                values[index] = value
        decoded = iter(leaf.decode(value) for leaf, value in zip(self.leaves, values))
        return tuple(_assemble(value_type, decoded) for value_type in self.types)
