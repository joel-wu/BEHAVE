"""Lazy state identities keep constructor validation and ownership semantics."""

import copy
import hashlib
import pickle
import sys

import pytest

from behave.sim.core import product
from behave.sim.core.product import Component, ProductState, reconcile
from behave.sim.core.record_value import FrozenValueMap, canonical_json


def test_reconciliation_does_not_compute_unused_digests(monkeypatch):
    original = product._state_digest
    calls = []

    def counted(manifest, values):
        calls.append(values)
        return original(manifest, values)

    monkeypatch.setattr(product, "_state_digest", counted)
    manifest = [Component("pointer", "driver"), Component("register", "artifact")]
    before = ProductState(manifest, {"pointer": 0, "register": (1, 2)})
    after = ProductState(manifest, {"pointer": 1, "register": (1, 2)})
    reconcile(before, after, {"driver": ["pointer"]})
    assert not calls
    expected = hashlib.sha256(canonical_json(after.as_dict()).encode()).hexdigest()
    assert after.digest == expected
    assert hash(after) == hash(expected)
    assert after.digest == expected
    assert len(calls) == 1


def test_component_identity_eligibility_is_not_rewalked_per_state(monkeypatch):
    manifest = [Component("pointer", "driver"), Component("register", "artifact")]
    calls = []
    original = product._immutable_identity_value

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(product, "_immutable_identity_value", counted)
    states = [ProductState(manifest, {"pointer": index, "register": (1, 2)})
              for index in range(3)]
    assert calls == [state.values for state in states]
    assert all(state._digest is None for state in states)


def test_lazy_identity_detaches_inputs_and_survives_record_roundtrips():
    raw = [1, {"x": [2]}]
    state = ProductState([Component("value", "artifact")], {"value": raw})
    assert state._digest is None
    raw[1]["x"].append(3)
    expected = hashlib.sha256(canonical_json(state.as_dict()).encode()).hexdigest()
    for rebuilt in (copy.copy(state), copy.deepcopy(state), pickle.loads(pickle.dumps(state))):
        assert rebuilt.digest == expected
        with pytest.raises(AttributeError):
            rebuilt.__init__([], {})
    assert state.digest == expected


@pytest.mark.parametrize("value", (object(), float("nan"), float("inf")))
def test_unsupported_values_still_fail_at_construction(value):
    with pytest.raises((TypeError, ValueError)):
        ProductState([Component("value", "artifact")], {"value": value})


def test_unproven_projection_keeps_the_original_eager_identity():
    raw = [1]
    value = FrozenValueMap((("x", raw),))
    state = ProductState([Component("value", "artifact")], {"value": value})
    expected = state._digest
    assert expected is not None
    raw.append(2)
    assert state.digest == expected  # same constructor-time snapshot as before

    class CustomComponent(Component):
        pass

    custom = ProductState([CustomComponent("value", "artifact")], {"value": 1})
    assert custom._digest is not None


def test_encoding_errors_are_not_delayed_until_the_first_digest_read():
    with pytest.raises(UnicodeEncodeError):
        ProductState([Component("value", "artifact")], {"value": "\ud800"})
    with pytest.raises(UnicodeEncodeError):
        ProductState([Component("value", "artifact", why="\ud800")], {"value": 1})

    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        with pytest.raises(ValueError, match="integer string conversion"):
            ProductState([Component("value", "artifact")], {"value": 10**1000})
    finally:
        sys.set_int_max_str_digits(previous)


def test_fallback_carriers_are_still_accepted_with_identical_identity():
    for value in ("状态", 1 << 3000):
        state = ProductState([Component("value", "artifact")], {"value": value})
        assert state._digest is not None
        assert state.digest == hashlib.sha256(canonical_json(state.as_dict()).encode()).hexdigest()
