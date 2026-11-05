"""Exact finite floor-division hulls without irrelevant big-integer divisions."""

from itertools import combinations_with_replacement, product
from types import SimpleNamespace

import pytest

from behave.sim.symbolic.primitives import PrimitiveInterval, primitive


def _hull(a, b):
    return primitive("fdiv").range_transform(a, b)


def test_every_small_integer_rectangle_has_the_exact_totalized_hull():
    intervals = tuple(combinations_with_replacement(range(-5, 6), 2))
    for (alo, ahi), (blo, bhi) in product(intervals, repeat=2):
        values = [x // y if y else 0
                  for x in range(alo, ahi + 1)
                  for y in range(blo, bhi + 1)]
        assert _hull(PrimitiveInterval(alo, ahi), PrimitiveInterval(blo, bhi)) \
            == PrimitiveInterval(min(values), max(values))


@pytest.mark.parametrize("a", [(-9, -2), (-9, 12), (0, 0), (2, 12)])
@pytest.mark.parametrize("b", [(-7, 11), (-7, 0), (0, 11), (-7, -2), (2, 11)])
def test_only_divisions_that_determine_an_extremum_are_evaluated(a, b):
    calls = []

    class Endpoint(int):
        def __neg__(self):
            return Endpoint(-int(self))

        def __floordiv__(self, other):
            calls.append((self, other))
            return int(self) // other

    # A duck-typed interval retains counted endpoints; PrimitiveInterval itself
    # canonicalizes endpoints to int.  The transformer uses the same arithmetic.
    left = SimpleNamespace(lo=Endpoint(a[0]), hi=Endpoint(a[1]))
    right = SimpleNamespace(lo=Endpoint(b[0]), hi=Endpoint(b[1]))
    got = _hull(left, right)
    assert len(calls) == (0 if b[0] <= 0 <= b[1] else 2)
    values = [x // y if y else 0
              for x in range(a[0], a[1] + 1)
              for y in range(b[0], b[1] + 1)]
    assert got == PrimitiveInterval(min(values), max(values))


@pytest.mark.parametrize("bits", [127, 1024, 16384])
def test_large_endpoints_match_the_preceding_corner_construction(bits):
    big = (1 << bits) + 31
    intervals = ((-big, -3), (-big, big + 2), (3, big), (0, big), (-big, 0))
    for a, b in product(intervals, repeat=2):
        divisors = {b[0], b[1]}
        divisors.update(d for d in (-1, 1) if b[0] <= d <= b[1])
        divisors.discard(0)
        values = [x // y for x in a for y in divisors]
        if b[0] <= 0 <= b[1]:
            values.append(0)
        assert _hull(PrimitiveInterval(*a), PrimitiveInterval(*b)) \
            == PrimitiveInterval(min(values), max(values))


@pytest.mark.parametrize("a,b,expected", [
    ((None, 17), (4, 4), (None, 4)),
    ((-3, None), (-2, -2), (None, 1)),
    ((-9, 10), (2, None), (-5, 5)),
    ((-9, 10), (None, -2), (-5, 4)),
    ((None, None), (0, 0), (0, 0)),
    ((None, 1), (-2, 2), None),
])
def test_open_endpoint_and_totalized_zero_rules_are_unchanged(a, b, expected):
    got = _hull(PrimitiveInterval(*a), PrimitiveInterval(*b))
    assert got == (None if expected is None else PrimitiveInterval(*expected))


def test_unknown_operands_still_have_no_hull():
    point = PrimitiveInterval(0, 0)
    assert _hull(None, point) is None
    assert _hull(point, None) is None
    assert primitive("fdiv").source_error == "DivZero"
