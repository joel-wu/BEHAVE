"""Derived trace summaries preserve evidence, membership and score identities."""

import pickle
import random

import pytest

from behave.sim import api
from behave.sim.core import stimulus as stimulus_module
from behave.sim.core.hit_vector import HitVector, HitVectorCodec
from behave.sim.run_assessment import assess_run

from .test_api_path import Doubling, _ref, contract, in_memory, realization, schedule


def _slot_summary(trace):
    names = set()
    reached = set()
    for record in trace.records:
        if type(record.hits) is HitVector:
            names.update(record.hits.catalogue)
        for name, value in record.hits:
            names.add(name)
            if value:
                reached.add(name)
    return frozenset(names), frozenset(reached)


def _slot_count(trace, endpoints=None):
    return sum(1 for record in trace.records for name in record.transfers
               if endpoints is None or name in endpoints)


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("size", [0, 1, 7, 8, 9, 65, 1000])
def test_summary_matches_three_valued_slot_union(compact, size):
    rng = random.Random(19)
    catalogue = tuple(f"goal.{index:04d}" for index in range(size))
    codec = HitVectorCodec(catalogue)
    records = []
    for _ in range(9):
        values = {name: rng.choice((True, False, None)) for name in catalogue}
        hits = (codec.vector([name for name, value in values.items() if value is True],
                             [name for name, value in values.items() if value is None])
                if compact else values)
        records.append(api.SlotRecord(hits=hits))
    trace = api.AuditedTrace(api.Stimulus({}, [api.EventSlot()] * len(records)), records=records)
    wire, identity = trace.as_dict(), trace.fingerprint
    expected = _slot_summary(trace)

    assert trace.goal_summary == expected
    assert trace.goal_summary is trace.goal_summary
    assert trace.as_dict() == wire and trace.fingerprint == identity
    assert api.AuditedTrace.from_dict(wire).goal_summary == expected
    assert pickle.loads(pickle.dumps(trace)).goal_summary == expected


def test_summary_retains_false_and_unknown_names_without_promoting_hits(monkeypatch):
    codec = HitVectorCodec(("false", "reached", "unknown"))
    trace = api.AuditedTrace(api.Stimulus({}, [api.EventSlot()] * 2), records=[
        api.SlotRecord(hits=codec.vector(unreadable=("reached", "unknown"))),
        api.SlotRecord(hits=codec.vector(reached=("reached",))),
    ])
    assert trace.goal_summary == (frozenset(codec.catalogue), frozenset({"reached"}))

    def must_not_merge_again(*args):
        pytest.fail("immutable trace goals must not be re-aggregated")

    monkeypatch.setattr(stimulus_module, "_reached_union", must_not_merge_again)
    assert trace.goal_summary[1] == {"reached"}
    assert dict(trace.records[0].hits) == {"reached": None, "unknown": None}
    assert dict(trace.records[1].hits) == {"reached": True}


def test_empty_trace_summary_and_count():
    trace = api.AuditedTrace(api.Stimulus({}, []))
    assert trace.goal_summary == (frozenset(), frozenset())
    assert trace.accepted_count() == trace.accepted_count({"in"}) == 0


@pytest.mark.parametrize("incomplete", [False, True])
def test_accepted_counts_keep_endpoint_filtering_reset_stalls_and_outputs(incomplete):
    stimulus = api.Stimulus({"a": [(1,), (2,)], "b": [(3,)]}, [
        api.EventSlot(reset=True), api.EventSlot(offers=("a", "b")),
        api.EventSlot(offers=("a",)), api.EventSlot(offers=("a",)), api.EventSlot(),
    ])
    records = [api.SlotRecord(), api.SlotRecord(accepted={"a": (1,), "b": (3,)}),
               api.SlotRecord(accepted={"a": None, "out": (7,)}),
               api.SlotRecord(accepted={"a": (2,)}), api.SlotRecord(accepted={"out": (8,)})]
    trace = api.AuditedTrace(stimulus, records=records[:3] if incomplete else records,
                            completion=api.Completion.INCOMPLETE if incomplete else api.Completion.NORMAL)
    wire, identity = trace.as_dict(), trace.fingerprint
    for endpoints in (None, set(), {"a"}, {"a", "b"}, {"out"}, {"absent"}, ["a", "a"]):
        assert trace.accepted_count(endpoints) == _slot_count(trace, endpoints)
    counts = trace._accepted_counts
    assert trace.accepted_count() == (3 if incomplete else 5)
    assert trace._accepted_counts is counts
    assert trace.as_dict() == wire and trace.fingerprint == identity
    assert pickle.loads(pickle.dumps(trace)).accepted_count() == trace.accepted_count()


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("safety", [False, True])
def test_reused_summary_keeps_full_and_budgeted_evaluation_images(compact, safety, monkeypatch):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    stimulus = schedule([1, 2])
    original = in_memory(task, stimulus, Doubling())
    names = ("false", "hit", "unknown")
    codec = HitVectorCodec(names)
    hits = codec.vector(("hit",), ("unknown",)) if compact else {
        "false": False, "hit": True, "unknown": None}
    trace = api.AuditedTrace(stimulus, records=[api.SlotRecord(
        accepted=record.accepted, state=record.state, outputs=record.outputs,
        environment=record.environment, monitor=record.monitor, hits=hits)
        for record in original.records], initial_state=dict(original.initial_state),
        completion=original.completion, domain=original.domain, artifact=original.artifact)
    catalogue = api.CatalogueManifest(tuple(api.Goal(
        name, api.SAFETY if name == "hit" and safety else api.COVERAGE,
        provider="test", family="test") for name in names))
    assessment = assess_run(task, stimulus, trace, reference)
    for budgeted in (False, True):
        kwargs = {"results": (assessment.result,), "hits": assessment.hits, "catalogue": catalogue,
                  "requested_stimuli": (stimulus, schedule([3])) if budgeted else (stimulus,),
                  "budget_exhausted": budgeted}
        actual = api.ConcreteEvaluation(task, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(api.AuditedTrace, "goal_summary", property(_slot_summary))
            patch.setattr(api.AuditedTrace, "accepted_count", _slot_count)
            expected = api.ConcreteEvaluation(task, **kwargs)
            expected_image = expected.as_evidence_dict()
        assert actual.as_evidence_dict() == expected_image
        assert actual.fingerprint == expected.fingerprint
        assert actual.passed is (not safety and not budgeted)
        assert actual.complete is not budgeted


def test_compact_summary_does_not_expand_individual_slots(monkeypatch):
    def no_iteration(self):
        pytest.fail("aggregation must merge bits before enumerating goals")
    monkeypatch.setattr(HitVector, "__iter__", no_iteration)
    codec = HitVectorCodec(("hit",))
    trace = api.AuditedTrace(api.Stimulus({}, [api.EventSlot()] * 200), records=[
        api.SlotRecord(hits=codec.vector(("hit",))) for _ in range(200)])
    assert trace.goal_summary[1] == {"hit"}
