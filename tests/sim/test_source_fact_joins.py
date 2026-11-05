"""Cheap source joins preserve carriers, path facts and normalization."""

from dataclasses import replace

import pytest

from behave.sim.symbolic import ranges
from behave.sim.behavior.source_batches import BatchContract, BatchForm
from behave.sim.behavior.source_sequences import (
    _BatchFact, _FunctionContracts, _RecordFact, _ScalarBindings, _StaticFact,
)
from behave.sim.behavior.source_values import (
    INCOMPATIBLE, NONE, SCALAR, UNKNOWN, ValueContract,
)


@pytest.mark.parametrize("contract", (
    SCALAR, NONE, UNKNOWN, INCOMPATIBLE,
    ValueContract.record({"y": SCALAR}),
    ValueContract.sequence("list", 0, 4, SCALAR),
    ValueContract.sequence("list", 0, 4, ValueContract.record({"y": SCALAR})),
    ValueContract.positional("tuple", (SCALAR, NONE)),
))
def test_identical_plain_carrier_is_the_same_join_as_distinct_equal_carriers(contract):
    for count in (1, 2, 4):
        envs = [{"value": contract, f"local{index}": SCALAR}
                for index in range(count)]
        result = _FunctionContracts._merge_envs(*envs)
        assert result["value"] is contract
        # Force the ordinary multi-path join, with distinct equal carriers.
        control = _FunctionContracts._merge_envs(
            {"value": replace(contract)}, {"value": replace(contract)})
        assert result["value"] == control["value"]
        if count > 1:
            assert set(result) == {"value"}


def test_join_still_normalizes_optional_batches_and_record_builders():
    row = ValueContract.record({"y": SCALAR})
    optional = ValueContract.option(row)
    result = _FunctionContracts._merge_envs({"value": optional})["value"]
    assert isinstance(result, _BatchFact)
    assert result.batch.forms == frozenset({BatchForm.NONE, BatchForm.MAPPING})
    record = _RecordFact((("y", INCOMPATIBLE),), frozenset({"y"}))
    result = _FunctionContracts._merge_envs({"value": record})["value"]
    assert result == _RecordFact((("y", UNKNOWN),), frozenset({"y"}),
                                 ranges=(("y", None),))
    independent = _BatchFact(UNKNOWN, BatchContract.one(row))
    assert _FunctionContracts._merge_envs({"value": independent}) == {
        "value": independent}


def test_static_boolean_and_integer_alternatives_are_not_collapsed():
    first, second = _StaticFact((True,)), _StaticFact((1,))
    result = _FunctionContracts._merge_envs(
        {"value": first}, {"value": second})["value"]
    assert len(result.values) == 2
    assert tuple(type(value) for value in result.values) == (bool, int)


def test_single_path_scalar_join_matches_general_join_without_aliasing():
    bindings = _ScalarBindings(
        {"boolean": True, "integer": 1, "text": "a", "none": None,
         "unproved": object()},
        scalar_bounds={"integer": ranges.Bounds(1, 3),
                       "lower_only": ranges.Bounds(0, None),
                       "upper_only": ranges.Bounds(None, 8)},
        guard_facts={("scope", "ready"): True})
    actual = _FunctionContracts._merge_bindings(bindings)
    expected = _FunctionContracts._merge_bindings(bindings, bindings.clone())
    assert dict(actual) == dict(expected) == {
        "boolean": True, "integer": 1, "text": "a"}
    assert type(actual["boolean"]) is bool
    assert type(actual["integer"]) is int
    assert actual.bounds == expected.bounds == bindings.bounds
    assert actual.ranges == expected.ranges == bindings.ranges
    assert actual.guards == expected.guards == bindings.guards
    actual.set_bounds("integer", ranges.Bounds(7, 9))
    actual.guards.clear()
    actual["integer"] = 9
    assert bindings["integer"] == 1
    assert bindings.bounds["integer"] == ranges.Bounds(1, 3)
    assert bindings.guards


def test_join_does_not_invent_a_common_fixed_value_or_numeric_fact():
    first = _ScalarBindings({"x": True}, scalar_bounds={"x": ranges.Bounds(1, 1)})
    second = _ScalarBindings({"x": 1}, scalar_bounds={"x": ranges.Bounds(1, 3)})
    result = _FunctionContracts._merge_bindings(first, second)
    assert "x" not in result
    assert result.bounds == {"x": ranges.Bounds(1, 3)}
    assert not _FunctionContracts._merge_bindings(first, {}).bounds
