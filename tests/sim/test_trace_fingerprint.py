"""Trace identity is unchanged; repeated reads do not serialize the horizon."""
import hashlib
import pickle
from concurrent.futures import ThreadPoolExecutor

import pytest

from behave.sim.core.hit_vector import HitVector
from behave.sim.core.stimulus import (
    AuditedTrace,
    Completion,
    EventSlot,
    SlotRecord,
    Stimulus,
    canonical_json,
)


def _original(trace):
    return hashlib.sha256(canonical_json(trace.as_dict()).encode()).hexdigest()


def _trace(*, compact=False, incomplete=False):
    stimulus = Stimulus(queues={"in": [(True,), (1,)]},
                        slots=[EventSlot(offers=["in"])] * 2, source="seed α")
    hits = HitVector.from_names(("a", "b"), ("a",), ("b",)) if compact else {"a": True}
    records = [SlotRecord(accepted={"in": (value,)}, hits=hits,
                          state={"nested": [{"x": [value, -0.0]}]},
                          environment={"ram": [0, 7]}, outputs={"y": value},
                          monitor={"pending": index})
               for index, value in enumerate((True, 1))]
    return AuditedTrace(stimulus, records=records[:1] if incomplete else records,
                        completion=Completion.INCOMPLETE if incomplete else Completion.NORMAL,
                        domain="domain", artifact="artifact", initial_state={"x": 3},
                        source_evidence={"intervals": [{"a": [1, 2]}]})


@pytest.mark.parametrize("compact", (False, True))
@pytest.mark.parametrize("incomplete", (False, True))
def test_original_digest_and_wire_survive_both_roundtrips(compact, incomplete):
    trace = _trace(compact=compact, incomplete=incomplete)
    before_read = pickle.loads(pickle.dumps(trace))
    expected, wire = _original(trace), trace.as_dict()
    assert trace.fingerprint == expected
    for copy in (before_read, pickle.loads(pickle.dumps(trace)), AuditedTrace.from_dict(wire)):
        assert copy.fingerprint == expected == _original(copy)
        assert copy.as_dict() == wire
        assert "_fingerprint" not in copy.as_dict()


def test_one_hash_per_exact_trace_not_per_consumer(monkeypatch):
    trace = _trace()
    original = AuditedTrace._fields
    calls = []

    def project(value, **kwargs):
        calls.append(value)
        return original(value, **kwargs)

    monkeypatch.setattr(AuditedTrace, "_fields", project)
    assert calls == []
    first = trace.fingerprint
    assert [trace.fingerprint for _ in range(32)] == [first] * 32
    assert calls == [trace]
    # An independently reconstructed replay remains independently checked/hashed.
    # Public projection also reads the shared schema, outside the measured hashes.
    monkeypatch.setattr(AuditedTrace, "_fields", original)
    other = AuditedTrace.from_dict(trace.as_dict())
    monkeypatch.setattr(AuditedTrace, "_fields", project)
    assert other.fingerprint == first
    assert calls == [trace, other]


def test_input_aliases_and_public_views_cannot_change_identity():
    payload = {"nested": [{"value": True}]}
    trace = AuditedTrace(Stimulus(slots=[EventSlot()]),
                        records=[SlotRecord(state=payload)], source_evidence=payload)
    expected = trace.fingerprint
    payload["nested"][0]["value"] = False
    view = trace.as_dict()
    view["source_evidence"]["nested"].clear()
    view["records"].clear()
    assert trace.fingerprint == expected == _original(trace)
    for name in ("_fingerprint", "records", "source_evidence"):
        with pytest.raises(AttributeError):
            setattr(trace, name, None)
        with pytest.raises(AttributeError):
            delattr(trace, name)
    with pytest.raises(AttributeError):
        trace.__init__(trace.stimulus)


@pytest.mark.parametrize("location", ("source", "source-nested", "initial", "slot", "queue"))
def test_nested_evidence_cannot_be_reinitialized_or_deleted(location):
    trace = AuditedTrace(
        Stimulus(queues={"in": [({"x": 1},)]}, slots=[EventSlot()]),
        initial_state={"x": {"nested": 1}},
        records=[SlotRecord(state={"x": {"nested": 1}})],
        source_evidence={"x": {"nested": 1}})
    value = {
        "source": trace.source_evidence,
        "source-nested": trace.source_evidence["x"],
        "initial": dict(trace.initial_state)["x"],
        "slot": dict(trace.records[0].state)["x"],
        "queue": dict(trace.stimulus.queues)["in"][0][0],
    }[location]
    trace_identity, stimulus_identity = trace.fingerprint, trace.stimulus.fingerprint
    with pytest.raises(AttributeError):
        value.__init__((("changed", 9),))
    with pytest.raises(AttributeError):
        del value._pairs
    assert trace.fingerprint == trace_identity == _original(trace)
    assert trace.stimulus.fingerprint == stimulus_identity


@pytest.mark.parametrize("field", ("artifact", "domain", "completion", "initial_state",
                                   "source_evidence", "records", "stimulus"))
def test_changed_evidence_changes_new_record_digest(field):
    trace = _trace()
    wire = trace.as_dict()
    if field == "records":
        wire[field][0]["state"] = [["other", 7]]
    elif field == "stimulus":
        wire[field]["source"] = "different trace provenance"
    elif field == "completion":
        wire[field] = Completion.INCOMPLETE.value
    elif field in ("source_evidence", "initial_state"):
        wire[field] = {"other": 7}
    else:
        wire[field] = "different"
    other = AuditedTrace.from_dict(wire)
    assert other.fingerprint == _original(other) != trace.fingerprint


def test_concurrent_first_reads_agree():
    trace = _trace(compact=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: trace.fingerprint, range(32)))
    assert set(values) == {_original(trace)}


def test_subclass_projection_is_not_assumed_immutable():
    class Changing(AuditedTrace):
        label = "a"

        def as_dict(self):
            return {**super().as_dict(), "extra": self.label}

    trace = Changing(Stimulus())
    before = trace.fingerprint
    Changing.label = "b"
    assert trace.fingerprint == _original(trace) != before
