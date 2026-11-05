"""Injective structured-value layouts shared by Behavior boundaries."""

import itertools
import pickle

import pytest

from behave.sim.symbolic import typecheck as types
from behave.sim.symbolic.value_layout import (
    BOOLEAN_LAYOUT,
    EnumerationLayout,
    FixedArrayLayout,
    IntegerLayout,
    RecordLayout,
    ValueLayoutError,
    layout_of_type,
)
from behave.sim.core.contract import FiniteType


def test_boolean_and_one_bit_integer_have_distinct_exact_images():
    integer = IntegerLayout(FiniteType(1))

    assert BOOLEAN_LAYOUT.flatten(True) == (True,)
    assert integer.flatten(1) == (1,)
    assert type(BOOLEAN_LAYOUT.flatten(True)[0]) is bool
    assert type(integer.flatten(1)[0]) is int
    with pytest.raises(ValueLayoutError, match="exact int"):
        integer.flatten(True)
    with pytest.raises(ValueLayoutError, match="Boolean layout"):
        BOOLEAN_LAYOUT.flatten(1)
    with pytest.raises(TypeError, match="does not accept an integer carrier"):
        layout_of_type(types.BOOLEAN, FiniteType(1))


def test_nested_records_arrays_and_enumerations_share_one_round_trip():
    pixel = RecordLayout({
        "enabled": BOOLEAN_LAYOUT,
        "level": IntegerLayout(FiniteType(3)),
    })
    layout = RecordLayout({
        "mode": EnumerationLayout(("idle", "run", "fault")),
        "pixels": FixedArrayLayout(2, pixel),
    })
    value = {
        "pixels": [
            {"level": 3, "enabled": True},
            {"enabled": False, "level": 7},
        ],
        "mode": "fault",
    }

    flattened = layout.flatten(value)
    assert flattened == (2, True, 3, False, 7)
    assert layout.reconstruct(flattened) == {
        "mode": "fault",
        "pixels": [
            {"enabled": True, "level": 3},
            {"enabled": False, "level": 7},
        ],
    }
    assert tuple(leaf.path for leaf in layout.leaves()) == (
        ("mode",),
        ("pixels", 0, "enabled"),
        ("pixels", 0, "level"),
        ("pixels", 1, "enabled"),
        ("pixels", 1, "level"),
    )
    assert layout.finite


def test_record_enum_flattening_is_injective_on_its_finite_domain():
    layout = RecordLayout({
        "flag": BOOLEAN_LAYOUT,
        "mode": EnumerationLayout(("a", "b", "c")),
        "number": IntegerLayout(FiniteType(2)),
    })
    values = [
        {"flag": flag, "mode": mode, "number": number}
        for flag, mode, number in itertools.product(
            (False, True), ("a", "b", "c"), range(4))
    ]
    images = [layout.flatten(value) for value in values]

    assert len(images) == len(set(images)) == len(values)
    assert [layout.reconstruct(image) for image in images] == values


def test_invalid_record_shapes_enum_tags_and_flat_arities_refuse():
    enumeration = EnumerationLayout(("a", "b", "c"))
    record = RecordLayout({"tag": enumeration, "value": IntegerLayout()})

    with pytest.raises(ValueLayoutError) as caught:
        record.flatten({"tag": "a"})
    assert caught.value.code == "value-layout-record-schema-mismatch"
    with pytest.raises(ValueLayoutError) as caught:
        enumeration.reconstruct((3,))
    assert caught.value.code == "value-layout-enum-tag-invalid"
    with pytest.raises(ValueLayoutError) as caught:
        record.reconstruct((0, 1, 2))
    assert caught.value.code == "value-layout-flat-arity-mismatch"


def test_recursive_solver_type_layout_is_pickle_stable_and_preserves_carrier():
    kind = types.Array(2, types.Array(3, types.INTEGER))
    layout = layout_of_type(kind, FiniteType(4, signed=True))
    restored = pickle.loads(pickle.dumps(layout))

    assert restored == layout
    assert restored.identity == layout.identity
    assert tuple(leaf.path for leaf in restored.leaves()) == (
        (0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2))
    assert all(leaf.carrier == FiniteType(4, signed=True)
               for leaf in restored.leaves())
    value = [[-8, -1, 0], [1, 6, 7]]
    assert restored.reconstruct(restored.flatten(value)) == value
