"""Semantic mapping bindings remain write-once, including nested trace values."""
import copy
import pickle

import pytest

from behave.sim.core.frozen import FrozenMap
from behave.sim.core.record_value import FrozenValueMap, canonical_json, freeze_record_value


@pytest.mark.parametrize("make", (FrozenMap, lambda values: FrozenValueMap(values.items())))
def test_frozen_mapping_bindings_are_write_once(make):
    value = make({"x": 1})
    before = canonical_json(value)
    with pytest.raises(AttributeError):
        value.__init__((("x", 2),))
    for name in type(value).__slots__:
        with pytest.raises(AttributeError):
            setattr(value, name, ())
        with pytest.raises(AttributeError):
            delattr(value, name)
    assert canonical_json(value) == before
    for rebuilt in (copy.copy(value), copy.deepcopy(value), pickle.loads(pickle.dumps(value))):
        assert canonical_json(rebuilt) == before
        with pytest.raises(AttributeError):
            rebuilt.__init__((("x", 2),))


def test_reentry_refusal_does_not_evaluate_replacement_items():
    value = freeze_record_value({"x": 1})

    def replacement():
        raise AssertionError("a sealed value must reject before consuming new items")
        yield  # pragma: no cover

    with pytest.raises(AttributeError):
        value.__init__(replacement())
    assert canonical_json(value) == '{"x":1}'


def test_freeze_detaches_unsealed_input_payload_before_retaining_it():
    payload = [1]
    input_map = FrozenValueMap((("x", payload),))
    frozen = freeze_record_value(input_map)
    payload.append(2)
    assert canonical_json(input_map) == '{"x":[1,2]}'
    assert canonical_json(frozen) == '{"x":[1]}'


def test_owned_projection_reuses_only_recursively_validated_mappings(monkeypatch):
    from behave.sim.core import record_value

    raw = {"nested": [{"value": [True, -0.0, "汉字"]}]}
    value = freeze_record_value(raw)
    restored = pickle.loads(pickle.dumps(value))
    before = canonical_json(value)
    raw["nested"][0]["value"].clear()
    assert canonical_json(value) == before
    original = record_value._mapping_items
    def checked(item, path):
        assert item is not value and item is not restored, "owned graph was frozen again"
        return original(item, path)
    monkeypatch.setattr(record_value, "_mapping_items", checked)
    assert freeze_record_value(value) is value
    assert freeze_record_value(restored) is restored
    # Unchecked public wrappers must still validate/detach recursively.
    for bad in (float("nan"), object()):
        with pytest.raises((ValueError, TypeError)):
            freeze_record_value(FrozenValueMap((("bad", bad),)))


def test_unsealed_wrapper_pickle_does_not_grant_validated_status():
    value = FrozenValueMap((("bad", float("nan")),))
    with pytest.raises(ValueError, match="non-finite"):
        freeze_record_value(pickle.loads(pickle.dumps(value)))


def test_only_validated_projections_reuse_hashes(monkeypatch):
    from behave.sim.core import record_value

    raw = {"nested": [{"value": [True, -0.0, "汉字"]}]}
    value = freeze_record_value(raw)
    expected, wire = hash(value), canonical_json(value)
    raw["nested"][0]["value"].clear()
    restored = pickle.loads(pickle.dumps(value))
    assert restored._hash is None  # Never transport process-specific hashes.
    assert hash(restored) == expected
    with monkeypatch.context() as patch:
        patch.setattr(record_value, "frozenset", lambda _: pytest.fail("rehashing immutable state"),
                      raising=False)
        assert hash(value) == hash(restored) == expected
    assert canonical_json(value) == wire == canonical_json(restored)

    class MutableHash:
        value = 1
        def __hash__(self):
            return self.value

    foreign = MutableHash()
    wrapper = FrozenValueMap((("value", foreign),))
    before = hash(wrapper)
    foreign.value = 2
    assert hash(wrapper) != before and wrapper._hash is None
    with pytest.raises(TypeError):
        freeze_record_value(wrapper)


def test_cached_hash_is_not_equality_or_mapping_order():
    a = freeze_record_value({"a": {"x": True}, "b": -0.0})
    same = freeze_record_value({"b": -0.0, "a": {"x": True}})
    collision = freeze_record_value({"a": {"x": 1}, "b": 0.0})
    assert hash(a) == hash(same) == hash(collision)
    assert a == same and a != collision
    assert len({a, same, collision}) == 2


@pytest.mark.parametrize("capacity", [0, 1, 2, 256])
def test_repeated_dictionary_projection_is_local_and_preserves_every_position(monkeypatch, capacity):
    from behave.core import runtime_defaults as defaults

    monkeypatch.setattr(defaults, "SIM_RECORD_PROJECTION_CACHE_ENTRIES", capacity)
    a, b = {"x": [True, -0.0]}, {"x": [False, 0.0]}
    source = [a, b] * 100
    frozen = freeze_record_value(source)
    assert canonical_json(frozen) == canonical_json(source)
    assert len(frozen) == 200
    if capacity >= 2:
        assert all(item is frozen[index % 2] for index, item in enumerate(frozen))
    a["x"].append(7)
    assert canonical_json(frozen[0]) == '{"x":[true,-0.0]}'
    assert canonical_json(freeze_record_value(a)) == '{"x":[true,-0.0,7]}'
    # Eviction or a warm entry must never skip validation of another branch.
    with pytest.raises(ValueError, match="non-finite"):
        freeze_record_value([a, a, {"invalid": float("nan")}])
    cyclic = {"before": a}
    cyclic["cycle"] = cyclic
    with pytest.raises(ValueError, match="reference cycle"):
        freeze_record_value([a, cyclic])
