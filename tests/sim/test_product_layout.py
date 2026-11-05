"""Prepared schemas change neither product values nor ownership reconciliation."""

import copy
import hashlib
import pickle
import random

import pytest

from behave.sim.core import driver as driver_module
from behave.sim.core.driver import Driver
from behave.sim.core.frozen import freeze
from behave.sim.core.product import Component, ProductLayout, ProductState, reconcile
from behave.sim.core.record_value import canonical_json
from behave.sim.core.refusal import Refused

from .test_driver_preparation import _case, _run, UncachedDriver


@pytest.mark.parametrize("size", (0, 1, 8, 64))
def test_prepared_state_matches_canonical_projection_and_component_lookup(size):
    rng = random.Random(12)
    declarations = [Component(f"field.{i}", "artifact") for i in range(size)]
    rng.shuffle(declarations)
    layout = ProductLayout(declarations)
    for _ in range(4):
        raw = {c.name: [rng.randrange(100), {"nested": [False, None, 3]}]
               for c in declarations}
        state = ProductState(layout, raw)
        expected = {"manifest": [c.as_dict() for c in sorted(declarations, key=lambda c: c.name)],
                    "values": [list(pair) for pair in freeze(raw)]}
        assert state._layout is layout
        assert state.as_dict() == expected
        assert state.digest == hashlib.sha256(canonical_json(expected).encode()).hexdigest()
        assert dict(state.values) == {c.name: state[c.name] for c in declarations}
        assert all(state.owner_of(c.name) == "artifact" for c in declarations)
        assert state.owner_of("missing") is None
        with pytest.raises(KeyError):
            state["missing"]
        for value in raw.values():
            value[1]["nested"].append(99)
        assert state.as_dict() == expected


def test_layout_and_state_roundtrips_preserve_immutable_indices():
    layout = ProductLayout([Component("b", "driver"), Component("a", "artifact")])
    state = ProductState(layout, {"a": [1, 2], "b": 0})
    for restored in (copy.copy(state), copy.deepcopy(state), pickle.loads(pickle.dumps(state))):
        assert restored.as_dict() == state.as_dict()
        assert restored["a"] == (1, 2) and restored.owner_of("b") == "driver"
        with pytest.raises(AttributeError):
            restored._layout.names = ()
        with pytest.raises(TypeError):
            restored._layout.positions["a"] = 9


@pytest.mark.parametrize("values,code", (
    ({"a": 1, "unexpected": 2}, "product-undeclared-component"),
    ({}, "product-missing-component"),
))
def test_each_state_still_checks_its_actual_fields(values, code):
    layout = ProductLayout([Component("a", "artifact")])
    for declarations in (layout, layout.manifest):
        with pytest.raises(Refused) as error:
            ProductState(declarations, values)
        assert error.value.refusal.kind == code


@pytest.mark.parametrize("claims,code", (
    ({}, "product-unclaimed-change"),
    ({"artifact": ["a", "b"]}, "product-phantom-change"),
    ({"driver": ["a"]}, "product-wrong-owner"),
    ({"artifact": ["a"], "driver": ["a"]}, "product-two-owners-claim"),
))
def test_layout_sharing_does_not_skip_ownership_failures(claims, code):
    layout = ProductLayout([Component("a", "artifact"), Component("b", "driver")])
    before = ProductState(layout, {"a": 0, "b": 0})
    after = ProductState(layout, {"a": 1, "b": 0})
    with pytest.raises(Refused) as error:
        reconcile(before, after, claims)
    assert error.value.refusal.kind == code
    assert reconcile(before, after, {"artifact": ["a"]}) == ("a",)


def test_equal_ordered_values_do_not_hide_a_different_manifest():
    before = ProductState(ProductLayout([Component("a", "artifact")]), {"a": 1})
    after = ProductState(ProductLayout([Component("b", "artifact")]), {"b": 1})
    with pytest.raises(Refused, match="product-manifest-changed"):
        reconcile(before, after, {})


def test_repeated_names_retain_first_declaration_ownership():
    declarations = [Component("x", "artifact"), Component("x", "driver")]
    state = ProductState(ProductLayout(declarations), {"x": 1})
    assert state.manifest == tuple(declarations)
    assert state.values == (("x", 1),)
    assert state.owner_of("x") == "artifact"


def test_values_are_consumed_once_without_losing_a_component():
    layout = ProductLayout([Component("a", "artifact"), Component("b", "driver")])
    state = ProductState(layout, iter((("b", 2), ("a", 1))))
    assert state.values == (("a", 1), ("b", 2))


def test_driver_prepares_one_layout_and_one_protocol_declaration(monkeypatch):
    contract, stimulus, _ = _case("start-done", 10)
    program_type = type(driver_module.adapter_program_for(contract))
    original = program_type.components
    declarations = []
    layouts = []
    original_init = ProductLayout.__init__

    def components(self, contract):
        declarations.append(contract)
        return original(self, contract)

    def layout_init(self, items):
        layouts.append(items)
        original_init(self, items)

    monkeypatch.setattr(program_type, "components", components)
    monkeypatch.setattr(ProductLayout, "__init__", layout_init)
    result = _run(Driver, "start-done", 10)
    # _run additionally asks an independent trace auditor and monitor to replay
    # the protocol. Count only layouts: neither should create a Driver state.
    assert len(layouts) == 1
    prepared_calls = len(declarations)
    declarations.clear()
    assert result == _run(UncachedDriver, "start-done", 10)
    assert len(declarations) - prepared_calls == len(stimulus.slots)


def test_declaring_after_inspection_or_editing_manifest_rebuilds_layout():
    contract, stimulus, _ = _case("ready-valid", 10)
    driver = Driver(contract, stimulus)
    first = driver.state()
    driver.declare([Component("artifact.x", "artifact")])
    second = driver.state({"artifact.x": 1})
    assert first._layout is not second._layout
    assert second["artifact.x"] == 1
    assert "artifact.x" not in dict(first.values)
    # Keep the existing diagnostic manifest view: even an in-place edit must
    # not silently reuse a stale schema and drop an undeclared/missing field.
    driver.manifest.append(Component("artifact.y", "artifact"))
    with pytest.raises(Refused, match="product-missing-component"):
        driver.state({"artifact.x": 1})
    assert driver.state({"artifact.x": 1, "artifact.y": 2})["artifact.y"] == 2


def test_protocol_declarations_follow_a_rebound_program_or_contract(monkeypatch):
    contract, stimulus, _ = _case("start-done", 10)
    driver = Driver(contract, stimulus)
    original = type(driver.protocol).components
    calls = []

    def components(program, bound_contract):
        calls.append((program, bound_contract))
        return original(program, bound_contract)

    monkeypatch.setattr(type(driver.protocol), "components", components)
    driver._adapter_components()
    assert not calls
    driver.protocol = copy.copy(driver.protocol)
    driver._adapter_components()
    driver._adapter_components()
    assert len(calls) == 1 and calls[-1][0] is driver.protocol
    driver.contract = copy.copy(driver.contract)
    driver._adapter_components()
    assert len(calls) == 2 and calls[-1][1] is driver.contract


def test_reconciliation_is_a_linear_value_comparison(monkeypatch):
    layout = ProductLayout([Component(f"x{i}", "artifact") for i in range(100)])
    before = ProductState(layout, {name: 0 for name in layout.names})
    after = ProductState(layout, {name: 1 for name in layout.names})

    def lookup_is_not_a_value_comparison(*args):
        raise AssertionError("reconciliation searched pairs instead of comparing them")

    monkeypatch.setattr(ProductState, "__getitem__", lookup_is_not_a_value_comparison)
    assert reconcile(before, after, {"artifact": layout.names}) == layout.names

