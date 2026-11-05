"""Equivalent record transitions must not recopy unchanged analysis witnesses."""
import copy
import json
import pickle

import pytest

from behave.sim import api, result_store
from behave.sim.core.record_value import (
    FrozenValueMap,
    canonical_json,
    freeze_record_value,
    same_record_value,
)

from .test_analysis_report_handoff import _decided


def test_progress_reads_frozen_headers_without_expanding_whole_records(tmp_path, monkeypatch):
    store, authority, _ = _decided(tmp_path)
    from behave.sim.analysis_report import analyse_after_decision

    analyse_after_decision(store, authority,
        wall_timeout_seconds=60)
    before = store.snapshot()

    def forbidden(*args, **kwargs):
        pytest.fail("an in-memory phase update expanded an envelope")

    monkeypatch.setattr(result_store, "_plain", forbidden)
    store.record(phase_reached="done")
    after = store.snapshot()
    assert after.authority is before.authority
    assert after.report is before.report
    assert after.progress["phase_reached"] == "done"


def test_report_field_update_shares_evidence_and_matches_full_replacement(tmp_path):
    store, authority, _ = _decided(tmp_path)
    from behave.sim.analysis_report import analyse_after_decision

    outcome = analyse_after_decision(store, authority,
        wall_timeout_seconds=60)
    before = store.snapshot()
    update = {"cost": {"stages": [1, 2]}, "why": "diagnostic"}
    expected = before.advance(report={**store.standing_report(), **update})
    store.commit(report_update=update)
    after = store.snapshot()
    update["cost"]["stages"].append(3)
    assert after.as_record() == expected.as_record()
    assert after.authority is before.authority
    assert after.report["analysis"] is before.report["analysis"]
    assert json.loads(store.result_path.read_text())["report"]["analysis"] == outcome.as_dict()
    assert after.report["cost"]["stages"] == (1, 2)
    with pytest.raises(ValueError, match="not both"):
        store.commit(report={}, report_update={})


@pytest.mark.parametrize("field", ["passed", "authority", "analysis"])
def test_report_updates_cannot_change_decided_fields(tmp_path, field):
    store, authority, _ = _decided(tmp_path)
    from behave.sim.analysis_report import analyse_after_decision

    outcome = analyse_after_decision(store, authority,
        wall_timeout_seconds=60)
    before = store.result_path.read_bytes()
    replacement = {
        "passed": False,
        "authority": {**store.standing_authority_summary(), "passed": False},
        "analysis": {**outcome.as_dict(), "why": "different"},
    }
    with pytest.raises(ValueError):
        store.commit(report_update={field: replacement[field]})
    assert store.result_path.read_bytes() == before
    assert store.committed_decision().passed is True


def test_report_update_cannot_introduce_untyped_analysis(tmp_path):
    store, _authority, evaluation = _decided(tmp_path)
    outcome = api.analyse(evaluation,
        wall_timeout_seconds=60)
    with pytest.raises(ValueError, match="attach_analysis"):
        store.commit(report_update={"analysis": outcome.as_dict()})


def test_reconstructed_bad_authority_still_requires_complete_fingerprint(tmp_path):
    store, _, _ = _decided(tmp_path)
    current = store.snapshot()
    outer = store.standing_authority()
    outer["evaluation"]["results"][0]["accepted"] = 99
    # A fresh reconstruction is not a previously checked, shared authority.
    with pytest.raises(ValueError):
        store._admits(result_store.RunEnvelope(store.invocation, store.started),
                      current.advance(authority=outer))


def test_envelope_seal_cannot_be_deleted_or_reinitialized(tmp_path):
    store, _, _ = _decided(tmp_path)
    value = store.snapshot()
    for field in value.__slots__:
        with pytest.raises(AttributeError):
            delattr(value, field)
    with pytest.raises(AttributeError):
        value.__init__("another", 1.0)
    for restored in (copy.copy(value), copy.deepcopy(value), pickle.loads(pickle.dumps(value))):
        assert restored.as_record() == value.as_record()


def test_index_preserves_order_duplicates_mutable_wrappers_and_wire_identity():
    payload = [1]
    value = FrozenValueMap((("z", payload), ("a", True), ("z", 2)))
    assert list(value) == ["z", "a", "z"]
    assert value["z"] is payload  # Original lookup is first-match, not dict's last-match.
    with pytest.raises(ValueError, match="more than once"):
        freeze_record_value(value)
    pair = ["old", 3]
    mutable = FrozenValueMap((pair,))
    pair[0] = "new"
    assert mutable["new"] == 3
    with pytest.raises(KeyError):
        _ = mutable["old"]
    clean = freeze_record_value({"z": payload, "a": True})
    payload.append(4)
    assert canonical_json(clean) == '{"a":true,"z":[1]}'
    assert clean["a"] is True
    with pytest.raises(TypeError):
        clean._index["a"] = 1
    for restored in (copy.copy(clean), copy.deepcopy(clean), pickle.loads(pickle.dumps(clean))):
        assert same_record_value(restored, clean)
        assert list(restored) == ["z", "a"]
    assert not same_record_value(clean, freeze_record_value({"z": [1], "a": 1}))
    assert not same_record_value(freeze_record_value({"x": 0.0}),
                                 freeze_record_value({"x": -0.0}))


def test_index_does_not_hash_foreign_keys_at_construction():
    class Key:
        def __hash__(self):  # noqa: PLE0309 -- calling this is the regression
            pytest.fail("an unvalidated key was hashed")

    value = FrozenValueMap(((Key(), 1),))
    with pytest.raises(TypeError, match="non-string key"):
        freeze_record_value(value)


def test_pair_projection_does_not_allocate_lookup_indexes():
    import behave.sim.core.record_value as values

    frozen = freeze_record_value({"witness": [{"a": 1}, {"b": 2}]})
    rows = frozen.pairs()[0][1]
    assert canonical_json(frozen) == '{"witness":[{"a":1},{"b":2}]}'
    assert frozen._index is values._UNINDEXED
    assert all(row._index is values._UNINDEXED for row in rows)
    assert frozen["witness"] is rows
    assert frozen._index is not values._UNINDEXED
    assert all(row._index is values._UNINDEXED for row in rows)
