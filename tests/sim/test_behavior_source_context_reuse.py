"""Reuse source preparation, never a selected trace or a physical domain."""

import base64

import pytest

from behave.sim import api
from behave.sim.core.record_value import thaw_record_value
from behave.sim.engines.behavior import generation as parent
from behave.sim.engines.behavior import generation_job as jobs
from tests.support.reference import load

from . import test_behavior_atomic_generation as atomic
from . import test_behavior_memory_generation as memory
from .test_behavior_generation_provider import (
    FALLIBLE_SOURCE,
    _contract,
    _realization,
    _stimulus,
    _trace,
)

pytestmark = pytest.mark.solver


def _ordinary():
    reference = load({"behavior.py": FALLIBLE_SOURCE}, config={"class": "M"})
    seed = _stimulus()
    return {
        "source": thaw_record_value(parent._authenticated_source_image(reference)),
        "contract": _contract().as_dict(),
        "generation_spec": api.GenerationSpec(3, 1, 1, 1, 0).as_dict(),
        "target_artifact": "target-a",
    }, {"seed": seed.as_dict(), "trace": _trace(seed).as_dict()}


def _fixture(kind):
    if kind == "ordinary":
        return _ordinary()
    if kind == "atomic":
        return atomic._payload(), atomic._query(atomic._seed())
    return atomic._payload(memory.SOURCE, memory.contract()), memory.query(memory.seed())


@pytest.mark.parametrize("kind", ["ordinary", "atomic", "memory"])
def test_rebound_source_plan_agrees_with_fresh_selection_query_and_native_hit(kind, monkeypatch):
    payload, query = _fixture(kind)
    old, image, reused = jobs._build_with_context(payload)
    assert not reused and image is not None
    bound = old[1].max_body_steps
    spec = api.GenerationSpec(bound + 17, bound, 3, 4, 2)
    current = {**payload, "generation_spec": spec.as_dict()}
    fresh = jobs._build(current)

    def no_lowering(*_args, **_kwargs):
        pytest.fail("unchanged source/contract/transaction bound must not be lowered again")

    monkeypatch.setattr(jobs, "_build", no_lowering)
    rebound, returned, reused = jobs._build_with_context({
        **current, "cached_context": base64.b64encode(image).decode("ascii")})
    assert reused and returned != image
    assert rebound[1].as_dict() == spec.as_dict()
    assert jobs._validated_context(current, (), returned)[1].as_dict() == spec.as_dict()
    assert jobs._layout(rebound) == jobs._layout(fresh)
    targets = [goal for goal in fresh[6] if fresh[5][goal].family == "decision"]
    assert targets
    selections = [jobs.selected_trace(built, **query, targets=targets)
                  for built in (fresh, rebound)]
    assert selections[0].as_dict() == selections[1].as_dict()
    remaining = sorted(set(targets) - set(selections[0].replay.hits))
    assert remaining
    selections = [jobs.selected_trace(built, **query, targets=remaining)
                  for built in (fresh, rebound)]
    # The memory predicate reads the earlier write. Mutating the first input
    # cannot witness that later read; keep the recorded write as the prefix.
    # Compare the exact local question, not a relaxed path proposal before it.
    # Vary the whole later transaction: its narrowest field scope fixes addr.
    request = next(request for request in selections[0].requests.values()
                   if request.occurrence in selections[0].occurrences and (kind != "memory" or (
                       selections[0].occurrences[request.occurrence]["rank"] == 2
                       and len(request.mutable_fields) == 3))).as_dict()
    answers = [selection.probe(request, 20_000) for selection in selections]
    semantics = [{k: v for k, v in answer.items() if k != "solve_ms"} for answer in answers]
    assert semantics[0] == semantics[1]
    assert answers[0]["outcome"] == "sat", answers
    candidate = api.Stimulus.from_dict(answers[0]["candidate"])
    if kind == "ordinary":
        records = [api.SlotRecord(), api.SlotRecord(accepted={
            name: rows[0] for name, rows in candidate.queues}), api.SlotRecord()]
        candidate_query = {"seed": candidate.as_dict(), "trace": api.AuditedTrace(
            candidate, records=records, artifact="target-a", domain="test-domain").as_dict()}
    elif kind == "atomic":
        candidate_query = atomic._query(candidate)
    else:
        candidate_query = memory.query(candidate)
    for built in (fresh, rebound):
        confirmation = jobs.selected_trace(built, **candidate_query, targets=remaining)
        assert answers[0]["goal"] in confirmation.replay.hits
    assert old[1].as_dict() == payload["generation_spec"]  # No mutation of the old owner.


@pytest.mark.parametrize("changed", ["bound", "target", "source", "contract"])
def test_source_context_rejects_changed_construction_inputs(changed):
    payload, _query = _ordinary()
    _built, image, _reused = jobs._build_with_context(payload)
    current = dict(payload)
    if changed == "bound":
        current["generation_spec"] = api.GenerationSpec(4, 2, 1, 1, 0).as_dict()
    elif changed == "target":
        current["target_artifact"] = "another-target"
    elif changed == "source":
        reference = load({"behavior.py": FALLIBLE_SOURCE + b"\n# another source\n"},
                         config={"class": "M"})
        current["source"] = thaw_record_value(parent._authenticated_source_image(reference))
    else:
        contract = _contract()
        current["contract"] = api.TaskContract(contract.endpoints, contract.clocks,
                                               source="another contract").as_dict()
    assert jobs._validated_context(current, (), image) is None


def test_only_layout_key_ignores_physical_schedule_changes():
    reference = load({"behavior.py": FALLIBLE_SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    first = api.GenerationSpec(3, 1, 1, 1, 0)
    other = api.GenerationSpec(18, 1, 3, 4, 2)
    larger = api.GenerationSpec(4, 2, 1, 1, 0)
    source = "reference-artifact/1:" + reference.digest
    bind = lambda spec, **kw: parent._generation_cache_binding(
        task, task.realization.identity, spec, source, **kw).as_dict()
    assert bind(first) == bind(other)
    assert bind(first) != bind(larger)
    payload, _query = _ordinary()
    catalogue = jobs._build(payload)[5]
    assert bind(first, catalogue=catalogue) != bind(other, catalogue=catalogue)


@pytest.mark.isolation
def test_public_isolated_providers_share_layout_but_replay_each_domain(tmp_path):
    reference = load({"behavior.py": FALLIBLE_SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    replayed, providers, selections = [], [], []

    def trace(seed):
        replayed.append(seed.fingerprint)
        return _trace(seed)

    for spec in (api.GenerationSpec(3, 1, 1, 1, 0), api.GenerationSpec(18, 1, 3, 4, 2)):
        provider = api.prepare_behavior_generation(task, spec, reference, trace,
                                                   cache_root=tmp_path / "source-contexts")
        selections.append(provider.select(_stimulus()).as_dict())
        providers.append(provider)
    assert selections[0] == selections[1]
    assert replayed == [_stimulus().fingerprint] * 2
    cold, warm = [provider.cache_telemetry().as_dict()["events"] for provider in providers]
    assert [event["disposition"] for event in cold] == ["miss", "miss"]
    assert [event["disposition"] for event in warm] == ["hit", "miss"]
    assert warm[1]["detail"] == "provider-context-reused"
    assert cold[0]["key_identity"] == warm[0]["key_identity"]
    assert cold[1]["key_identity"] != warm[1]["key_identity"]
