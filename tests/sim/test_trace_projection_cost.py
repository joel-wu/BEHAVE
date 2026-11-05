"""Owned trace serialization and decoding retain the full finite-value boundary."""

import hashlib
import random

import pytest

from behave.sim.core import record_value
from behave.sim.core.stimulus import AuditedTrace, EventSlot, SlotRecord, Stimulus
from tests.sim.test_trace_fingerprint import _original, _trace


@pytest.mark.parametrize("compact", (False, True))
def test_first_owned_hash_does_not_project_the_validated_graph_again(monkeypatch, compact):
    trace = _trace(compact=compact)
    expected = _original(trace)

    def redundant(*_args, **_kwargs):
        raise AssertionError("the owned trace was recursively validated again")

    monkeypatch.setattr(record_value, "_project", redundant)
    assert trace.fingerprint == expected


@pytest.mark.parametrize("field", ("accepted", "state", "environment", "outputs", "hits", "monitor"))
def test_raw_slot_pairs_keep_duplicates_and_reject_foreign_values(field):
    for value, error in (([["x", None], ["x", None]], ValueError),
                         ([[True, None]], TypeError), ([["x", object()]], TypeError),
                         ([["x", float("nan")]], ValueError)):
        with pytest.raises(error):
            SlotRecord.from_dict({field: value})


def test_slot_constructor_alone_detaches_the_raw_values(monkeypatch):
    raw = {"state": [["x", [{"flag": True, "n": -0.0}]]],
           "accepted": [["in", [[1, 2]]]]}
    calls = []
    original = record_value.record_pairs

    def counted(value, path):
        calls.append(path)
        return original(value, path)

    monkeypatch.setattr("behave.sim.core.stimulus.record_pairs", counted)
    record = SlotRecord.from_dict(raw)
    assert len(calls) == len(set(calls)) == 6
    wire = record.as_dict()
    raw["state"][0][1][0]["flag"] = False
    raw["accepted"][0][1][0].clear()
    assert record.as_dict() == wire


def test_subclass_does_not_bypass_wire_checks():
    class Unchecked(SlotRecord):
        def __init__(self, **_kwargs):
            pass

    with pytest.raises(ValueError, match="more than once"):
        Unchecked.from_dict({"state": [["x", 1], ["x", 2]]})


def test_canonical_identity_matches_the_general_encoder_for_varied_carriers():
    rng = random.Random(8753)
    atoms = [None, False, True, 1, -1, 2**257, 0.0, -0.0, 0.125,
             "", "汉字", '\\n"\\']

    def value(depth):
        if not depth or rng.randrange(3) == 0:
            return rng.choice(atoms)
        values = [value(depth - 1) for _ in range(rng.randrange(4))]
        return values if rng.randrange(2) else {f"key{i}": item for i, item in enumerate(values)}

    for _ in range(120):
        payload = value(4)
        trace = AuditedTrace(
            Stimulus(slots=[EventSlot()]),
            records=[SlotRecord(state={"value": payload})],
            source_evidence={"value": payload}, initial_state={"value": payload})
        expected = hashlib.sha256(record_value.canonical_json(trace.as_dict()).encode()).hexdigest()
        assert trace.fingerprint == expected
        assert AuditedTrace.from_dict(trace.as_dict()).fingerprint == expected


@pytest.mark.parametrize("compact", (False, True))
@pytest.mark.parametrize("incomplete", (False, True))
def test_owned_identity_bytes_equal_public_json_without_thawing_slots(monkeypatch, compact, incomplete):
    trace = _trace(compact=compact, incomplete=incomplete)
    expected = record_value.canonical_json(trace.as_dict())
    write = record_value._DigestWriter.write
    captured = []

    def counted(self, text):
        captured.append(text)
        return write(self, text)

    def redundant(*_args, **_kwargs):
        raise AssertionError("identity built a mutable slot/source-evidence view")

    monkeypatch.setattr(record_value._DigestWriter, "write", counted)
    monkeypatch.setattr(SlotRecord, "as_dict", redundant)
    # Stimulus still projects its own small schedule; no source/slot thaw is needed.
    monkeypatch.setattr(record_value, "thaw_record_value", redundant)
    assert trace.fingerprint == hashlib.sha256(expected.encode()).hexdigest()
    assert "".join(captured) == expected  # exact bytes, not merely equal digests
    assert len(captured) > 1  # no full encoded trace retained


def test_owned_encoder_failure_keeps_public_diagnostic_path(monkeypatch):
    from behave.sim.core import stimulus as module

    trace = _trace(compact=True)
    expected = _original(trace)

    def unavailable(*_args, **_kwargs):
        raise ValueError("injected encoder failure")

    monkeypatch.setattr(module, "_digest_json_projection", unavailable)
    assert trace.fingerprint == expected


def test_shared_slot_schema_keeps_the_original_detached_wire_shape():
    # A literal oracle, independent of the shared _fields implementation.
    wire = {
        "accepted": [["a", [True, {"x": [1, -0.0]}]], ["b", None]],
        "state": [["s", [False, 2]]],
        "environment": [["ram", {"3": 7}]],
        "outputs": [["y", [[9], [10]]]],
        "hits": [["g", True]],
        "monitor": [["pending", 2]],
    }
    record = SlotRecord.from_dict(wire)
    before = record_value.canonical_json(wire)
    public = record.as_dict()
    assert record_value.canonical_json(public) == before
    public["accepted"][0][1][1]["x"].clear()
    public["state"][0][1].clear()
    public["environment"][0][1].clear()
    public["outputs"][0][1].clear()
    assert record_value.canonical_json(record.as_dict()) == before
