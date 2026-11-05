"""The declaration-owned recursive value packing used by RTL ports."""

from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.core.physical_value import (
    PHYSICAL_VALUE_LAYOUT_VERSION,
    PhysicalValueError,
    is_valid_pattern,
    pack_value,
    packed_leaves,
    packed_signed,
    packed_width,
    unpack_value,
)
from behave.sim.core.record_value import FrozenValueMap
from behave.sim.rtl.physical_symbolic import valid_packed_pattern

U3 = api.FiniteType(3)
S4 = api.FiniteType(4, signed=True)


def test_nested_layout_is_canonical_and_round_trips_every_value():
    kind = api.ValueType.record({
        "flag": api.ValueType.boolean(),
        "rows": api.ValueType.array(2, api.ValueType.record({
            "mode": api.ValueType.enumeration(("idle", "run", "wait")),
            "value": api.ValueType.integer(S4),
        })),
    })
    value = {
        "flag": True,
        "rows": [
            {"mode": "wait", "value": -3},
            {"mode": "run", "value": 7},
        ],
    }

    assert PHYSICAL_VALUE_LAYOUT_VERSION == "packed-value/1"
    assert packed_width(kind) == 1 + 2 * (2 + 4)
    assert [(leaf.path, leaf.offset, leaf.width)
            for leaf in packed_leaves(kind)] == [
        (("flag",), 0, 1),
        (("rows", 0, "mode"), 1, 2),
        (("rows", 0, "value"), 3, 4),
        (("rows", 1, "mode"), 7, 2),
        (("rows", 1, "value"), 9, 4),
    ]
    pattern = pack_value(kind, value)
    decoded = unpack_value(kind, pattern)
    assert type(decoded) is FrozenValueMap
    assert kind.admits(decoded)
    assert decoded["flag"] is True
    assert decoded["rows"][0]["mode"] == "wait"
    assert decoded["rows"][0]["value"] == -3
    assert decoded["rows"][1]["mode"] == "run"
    assert decoded["rows"][1]["value"] == 7
    assert pack_value(kind, decoded) == pattern


def test_scalar_integer_preserves_signed_port_semantics_but_products_do_not():
    assert packed_width(api.ValueType.integer(S4)) == 4
    assert packed_signed(api.ValueType.integer(S4)) is True
    assert packed_signed(api.ValueType.array(1, api.ValueType.integer(S4))) is False
    assert pack_value(api.ValueType.integer(S4), -1) == 0b1111
    assert unpack_value(api.ValueType.integer(S4), 0b1000) == -8


def test_unused_enumeration_tag_is_outside_the_physical_carrier():
    kind = api.ValueType.enumeration(("a", "b", "c"))

    assert packed_width(kind) == 2
    assert [pack_value(kind, name) for name in ("a", "b", "c")] == [0, 1, 2]
    assert is_valid_pattern(kind, 2)
    assert not is_valid_pattern(kind, 3)
    with pytest.raises(PhysicalValueError) as caught:
        unpack_value(kind, 3)
    assert caught.value.code == "physical-value-enumeration-tag-invalid"


@pytest.mark.parametrize("bad", [True, -1, 8, "1"])
def test_port_pattern_has_exact_nonnegative_width(bad):
    assert not is_valid_pattern(api.ValueType.integer(U3), bad)


def test_empty_array_has_no_signal_level_carrier():
    kind = api.ValueType.array(0, api.ValueType.boolean())

    assert packed_width(kind) == 0
    with pytest.raises(PhysicalValueError) as caught:
        pack_value(kind, ())
    assert caught.value.code == "physical-value-carrier-empty"


def test_runtime_shape_cannot_choose_record_or_array_layout():
    kind = api.ValueType.record({
        "flag": api.ValueType.boolean(),
        "values": api.ValueType.array(2, api.ValueType.integer(U3)),
    })

    for malformed in (
        {"flag": True, "values": [1]},
        {"flag": True, "values": [1, 2], "extra": 0},
        {"flag": 1, "values": [1, 2]},
        {"flag": True, "values": [1, 9]},
    ):
        with pytest.raises(PhysicalValueError):
            pack_value(kind, malformed)


def test_symbolic_validity_is_exactly_the_concrete_recursive_codec():
    kind = api.ValueType.record({
        "flags": api.ValueType.array(2, api.ValueType.boolean()),
        "mode": api.ValueType.enumeration(("idle", "run", "hold")),
    })
    width = packed_width(kind)
    term = z3.BitVec("packed", width)
    valid = valid_packed_pattern(term, kind)
    logical = [
        {"flags": (first, second), "mode": mode}
        for first in (False, True)
        for second in (False, True)
        for mode in ("idle", "run", "hold")
    ]
    declared_patterns = [pack_value(kind, value) for value in logical]
    assert len(set(declared_patterns)) == len(logical), \
        "canonical comparison is injective over the declared logical carrier"

    symbolic_patterns = []
    for pattern in range(1 << width):
        solver = z3.Solver()
        solver.add(term == pattern, valid)
        symbolic = solver.check() == z3.sat
        assert symbolic is is_valid_pattern(kind, pattern)
        if symbolic:
            symbolic_patterns.append(pattern)
            assert pack_value(kind, unpack_value(kind, pattern)) == pattern
    assert symbolic_patterns == sorted(declared_patterns)
