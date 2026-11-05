"""Persistent preparation must equal fresh jobs without retaining query state."""

from __future__ import annotations

import base64
import copy
import io
import json
import weakref
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.record_value import thaw_record_value
from behave.sim.engines.behavior import generation as parent
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.engines.behavior import generation_session as child
from behave.sim.engines.behavior import generation_transport as transport
from behave.sim.job_isolation import IsolatedJobFailure
from behave.sim.worker import WorkerFailed
from tests.support.reference import load

from .test_behavior_generation_provider import (
    FALLIBLE_SOURCE,
    SOURCE,
    _contract,
    _realization,
    _stimulus,
    _trace,
)

pytestmark = pytest.mark.solver


@pytest.fixture
def context():
    reference = load({"behavior.py": FALLIBLE_SOURCE}, config={"class": "M"})
    return {
        "source": thaw_record_value(parent._authenticated_source_image(reference)),
        "contract": _contract().as_dict(),
        "generation_spec": api.GenerationSpec(3, 1, 1, 1, 0).as_dict(),
        "target_artifact": "target-a",
    }


def _query(seed=None):
    seed = _stimulus() if seed is None else seed
    return {"seed": seed.as_dict(), "trace": _trace(seed).as_dict()}


def _without_time(answer):
    return {k: v for k, v in answer.items() if k != "solve_ms"}


def _requests(selection):
    """Inspect canonical requests independently of their compact wire image."""
    return [request.as_dict() for request in jobs.decode_selection_reply(selection).requests]


def test_transport_stage_and_query_time_are_not_preparation_time(context, monkeypatch):
    from behave.sim.search_policy import SearchBudget, search_scope

    prepared = child.PreparedGeneration({**context, "targets": []})
    layout = jobs.decode_layout_reply(prepared.layout())
    lifetimes, exchanges = [], []

    class Session:
        def __init__(self, *_args, **kwargs):
            lifetimes.append(kwargs["wall_s"])
            self.opened = prepared.layout()

        def _exchange(self, op, payload, timeout_s):
            exchanges.append((op, timeout_s))
            if op == "probe":
                assert payload["timeout_ms"] == 1
                return {"retained": "construction"}
            return getattr(prepared, op)(payload)

        def close(self):
            pass

    monkeypatch.setattr(transport, "JobSession", Session)
    monkeypatch.setattr(transport, "decode_probe_reply", lambda result, **_kwargs: result)
    search = transport.BehaviorSearch(context, layout, timeout_s=1, probe_budget=2)
    with search_scope(SearchBudget(api.SearchPolicy(time_budget_s=90)).direction(1)):
        search.select(_stimulus(), _trace(_stimulus()), ())
        assert search._call("probe", {"timeout_ms": 1}) == {"retained": "construction"}
    # Two probes do not turn a one-second preparation cap into a six-second
    # process lifetime. The explicitly bounded owning stage controls shutdown.
    assert 80 < lifetimes[0] <= 90
    assert exchanges[0] == ("select", 1)
    # A tiny cooperative quantum must not kill a long carrier-plan build or
    # replace the stage's deadline with preparation time / arbitrary overhead.
    assert exchanges[1][0] == "probe" and 80 < exchanges[1][1] <= 90


def test_standalone_probe_transport_still_has_a_finite_deadline(monkeypatch):
    exchanges = []

    class Session:
        def _exchange(self, op, payload, timeout_s):
            exchanges.append((op, timeout_s))
            return {}

    monkeypatch.setattr(transport, "decode_probe_reply", lambda result, **_kwargs: result)
    search = transport.BehaviorSearch({}, SimpleNamespace(catalogue=SimpleNamespace(
        digest=lambda: "catalogue")), timeout_s=7, probe_budget=1)
    search.session = Session()
    search._call("probe", {"timeout_ms": 1})
    assert exchanges == [("probe", 7)]


def _observation_images(occurrences):
    return tuple({**item, "bindings": tuple(
        (name, field.as_dict()) for name, field in item["bindings"])}
        for item in occurrences)


@pytest.mark.parametrize("stateful", [False, True])
@pytest.mark.parametrize('prefix', ('path-proposal:', 'native-path:'))
def test_local_search_audits_and_solves_without_binding_a_whole_run(context, monkeypatch, stateful, prefix):
    from behave.sim.behavior import lowering
    from behave.sim.symbolic import ranges
    from behave.sim.behavior.instance import BehaviorInstance

    source = SOURCE
    if stateful:
        source = source.replace(b"        pass\n", b"        self.count = 0\n").replace(
            b"        if a < b:\n",
            b"        self.count = (self.count + 1) & 255\n        if a == self.count:\n",
        ).replace(b"            y = a - b\n", b"            y = b\n")
    reference = load({"behavior.py": source}, config={"class": "M"})
    payload = {**context, "source": thaw_record_value(parent._authenticated_source_image(reference)),
               "generation_spec": api.GenerationSpec(102, 100, 1, 1, 0).as_dict()}

    def no_bounded_analysis(*_args, **_kwargs):
        pytest.fail("local search must not prepare a whole-run analysis product")

    monkeypatch.setattr(BehaviorInstance, "bind", no_bounded_analysis)
    monkeypatch.setattr(lowering, "output_contract_evidence", no_bounded_analysis)
    monkeypatch.setattr(ranges, "bounded", no_bounded_analysis)
    monkeypatch.setattr(ranges, "fixpoint", no_bounded_analysis)
    built = jobs._build(payload)
    assert built[4]._position_plan is None
    assert built[4].proc.state_range_bound is None
    assert bool(built[4].proc.state) == stateful
    targets = [goal for goal in built[6] if built[5][goal].family == "decision"]
    assert targets
    selected = jobs.SelectedTrace(built, **_query(), targets=targets)
    request = next(r for r in selected.requests.values() if r.occurrence.startswith(prefix))
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == ('proposed' if prefix == 'path-proposal:' else 'sat'), answer
    assert (answer['goal'] is None) == (prefix == 'path-proposal:')

    candidate = api.Stimulus.from_dict(answer["candidate"])
    queues = dict(candidate.queues)
    trace = api.AuditedTrace(candidate, records=[
        api.SlotRecord(),
        api.SlotRecord(accepted={name: queues[name][0] for name in ("left", "right")}),
        api.SlotRecord(),
    ], artifact="target-a", domain="test-domain")
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), trace.as_dict(), targets)
    assert set(request.targets) & set(confirmed.replay.hits) - set(selected.replay.hits)
    if answer['goal'] is not None:
        assert answer["goal"] in confirmed.replay.hits


def test_local_search_pins_later_array_state_without_reset_specialization(context):
    source = b'''class M:
    def reset(self): self.values = []
    def process(self, a, b):
        self.values.append(b)
        if a == len(self.values) + 37:
            return {"y": 1}
        return {"y": 0}
'''
    reference = load({"behavior.py": source}, config={"class": "M"})
    context = {**context,
               "source": thaw_record_value(parent._authenticated_source_image(reference)),
               "generation_spec": api.GenerationSpec(5, 4, 1, 0, 0).as_dict()}
    built = jobs._build(context)
    targets = [goal for goal in built[6] if built[5][goal].family == "decision"]
    slots = [api.EventSlot(reset=True)] + [api.EventSlot(offers=("left", "right"))] * 4
    seed = api.Stimulus(queues={"left": [(0,)] * 4, "right": [(3,)] * 4}, slots=slots)

    def trace(stimulus):
        queues = dict(stimulus.queues)
        return api.AuditedTrace(stimulus, records=[api.SlotRecord()] + [
            api.SlotRecord(accepted={name: queues[name][index] for name in ("left", "right")})
            for index in range(4)], artifact="target-a", domain="test-domain")

    selected = jobs.SelectedTrace(built, seed.as_dict(), trace(seed).as_dict(), targets)
    remaining = sorted(set(targets) - set(selected.replay.hits))
    assert remaining
    selected.select(remaining)
    request = next(item for item in selected.requests.values()
                   if item.occurrence in selected.occurrences
                   and selected.occurrences[item.occurrence]["position"] == 3)
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert dict(candidate.queues)["left"][:3] == dict(seed.queues)["left"][:3]
    assert dict(candidate.queues)["left"][3] == (41,)
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), trace(candidate).as_dict(), targets)
    assert answer["goal"] in confirmed.replay.hits
    assert built[4].proc.state_range_bound is None
    assert any("state ranges were not derived" in issue for issue in built[4].well_formed())


def test_guidance_deferral_does_not_certify_an_invalid_output_contract(context):
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.task_boundary import lower_task_behavior

    # The selected native seed (a=2) has a legal output; other inputs do not.
    # A local query can inspect this procedure without claiming that all its
    # outputs fit the nine-bit task domain.
    source = SOURCE.replace(b"            y = a + b\n",
                            b"            y = a * 512 if a > 128 else a\n")
    reference = load({"behavior.py": source}, config={"class": "M"})
    payload = {**context, "source": thaw_record_value(parent._authenticated_source_image(reference))}
    built = jobs._build(payload)
    assert any("output contract" in issue for issue in built[4].well_formed())
    with pytest.raises(LoweringError):
        lower_task_behavior(source.decode(), "behavior.py", _contract(), 1, cls="M")


def test_guidance_deferral_preserves_ir_ranges_and_catalogue(context):
    from behave.sim.symbolic.ir import command_fingerprint
    from behave.sim.behavior.task_boundary import lower_task_behavior

    built = jobs._build(context)
    strict, _ = lower_task_behavior(
        FALLIBLE_SOURCE.decode(), "behavior.py", _contract(), 1, cls="M")
    local = built[4]
    assert command_fingerprint(local.proc.body) == command_fingerprint(strict.proc.body)
    assert local.env == strict.env
    assert local.proc.semantics == strict.proc.semantics
    assert local.frontend_catalogue_manifest() == strict.frontend_catalogue_manifest()


@pytest.mark.isolation
def test_search_does_not_assume_the_candidate_output_contract(tmp_path):
    from .test_settled_protocol import contract

    source = """class M:
    def reset(self):
        pass
    def process(self, in_a):
        if in_a == 37:
            out_y = in_a * 2
        else:
            out_y = in_a
        return {"out_y": out_y}
"""
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": source.encode()},
        config={"class": "M"})
    candidate = source.replace("in_a * 2", "in_a * 8")
    path = tmp_path / "behavior.py"
    path.write_text(candidate)
    c = contract()
    task = api.admit(c, api.realization_of_behavior_source(candidate, c, file=path.name),
                     reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name, timeout_s=60),
                                  api.GenerationSpec(3, 3, 0, 0))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=3),
        providers=handle.generation_providers(reference, workdir=tmp_path / "search"),
        probe_budget=2, reference=reference))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and not result.decision.passed
    assert result.decision.failures
    assert any(search.generated for search in result.generated.searches)


@pytest.mark.parametrize("retained", [False, True])
def test_session_and_one_shot_queries_agree_and_reconstruct_once(context, monkeypatch, retained):
    built, image, _ = jobs._build_with_context(context)
    original = jobs._occurrences
    calls = []
    false_proofs = []
    original_proof = child.literal_false_goals

    def prove(proc):
        false_proofs.append(proc)
        return original_proof(proc)

    monkeypatch.setattr(child, "literal_false_goals", prove)

    def occurrences(*args):
        calls.append(args[2].fingerprint)
        return original(*args)

    monkeypatch.setattr(jobs, "_occurrences", occurrences)
    targets = list(built[6])
    query = {**_query(), "targets": targets}
    opened = {**context, "targets": targets,
              "cached_context": base64.b64encode(image).decode("ascii")}
    prepared = child.PreparedGeneration(opened)
    selected = prepared.select(query)
    assert prepared.select(query) == selected
    assert len(calls) == 1
    plan = jobs.decode_selection_reply(selected).requests
    # Query order can grow with exploratory contexts. Select the roles this
    # test exercises, not the last row (which need not be a safety query).
    coverage = next(r.as_dict() for r in plan if r.rank in plan.explorations
                    and all(built[5][goal].cls == "coverage" for goal in r.targets))
    safety = next(r.as_dict() for r in plan if r.occurrence in prepared.selected.occurrences
                  and all(built[5][goal].cls == "safety" for goal in r.targets))
    # Coverage, safety, then coverage again: fresh solver state every time.
    answers = []
    for request in [coverage, safety, coverage]:
        base = {"retained": "selected", "targets": targets} if retained else query
        probe = {**base, "request": request, "timeout_ms": 20_000}
        answers.append(prepared.probe(probe))
    assert len(calls) == 1
    assert _without_time(answers[0]) == _without_time(answers[2])
    for request, expected in zip([coverage, safety], answers):
        raw = jobs.probe_job({**context, **query, "request": request, "timeout_ms": 20_000})
        actual, _, _ = jobs.decode_context_reply(raw)
        assert _without_time(actual) == _without_time(expected)
    assert [a["outcome"] for a in answers] == ["proposed", "sat", "proposed"]
    assert answers[0]['goal'] is None and answers[2]['goal'] is None
    assert len(false_proofs) == 1


def test_one_shot_pause_cannot_offer_a_continuation_after_its_owner_exits(context, monkeypatch):
    from behave.sim.symbolic import encoder
    from behave.sim.engines import local_ir

    built, image, _ = jobs._build_with_context(context)
    query = {**_query(), "targets": list(built[6])}
    selected = jobs.SelectedTrace(built, **query)
    request = next(iter(selected.requests.values())).as_dict()
    encode = local_ir._encode_query_step

    def pause_new_owner(*args, **kwargs):
        owner = kwargs["_construction"]
        if owner.iterator is None and owner.result is None:
            def pause():
                raise encoder._EncodingPaused
            kwargs["_checkpoint"] = pause
        return encode(*args, **kwargs)

    monkeypatch.setattr(local_ir, "_encode_query_step", pause_new_owner)
    assert selected.probe(request, 20_000)["outcome"] == "constructing"
    payload = {**context, **query, "request": request, "timeout_ms": 20_000}
    for cached in (None, base64.b64encode(image).decode("ascii")):
        raw = jobs.probe_job({**payload, "cached_context": cached})
        reply, _, _ = jobs.decode_context_reply(raw)
        answer = jobs.decode_probe_reply(reply, expected_catalogue_digest=built[5].digest())
        assert answer.outcome.value == "unknown", answer.detail
        assert answer.candidate is None and answer.goal is None
    # Only the still-live selection can resume its existing encoder.
    answer = selected.probe(request, 20_000)
    assert answer["outcome"] == "proposed" and answer['goal'] is None, answer


def test_transport_sends_trace_once_and_restores_evicted_context(context, monkeypatch):
    context = {**context, "generation_spec": api.GenerationSpec(101, 100, 1, 0, 0).as_dict()}
    built = jobs._build(context)
    targets = tuple(built[6])
    prepared = child.PreparedGeneration({**context, "targets": list(targets)})
    wire = []

    class Session:
        def __init__(self, *_args, **_kwargs):
            self.opened = prepared.layout()

        def _exchange(self, op, payload, _timeout):
            wire.append((op, copy.deepcopy(payload)))
            return getattr(prepared, op)(json.loads(json.dumps(payload)))

        def close(self):
            pass

    monkeypatch.setattr(transport, "JobSession", Session)
    search = transport.BehaviorSearch(
        context, jobs.decode_layout_reply(prepared.layout()), timeout_s=60, probe_budget=None)
    seed = api.Stimulus(
        queues={"left": [(2,)] * 100, "right": [(3,)] * 100},
        slots=[api.EventSlot(reset=True)] +
              [api.EventSlot(offers=("left", "right")) for _ in range(100)])
    trace = api.AuditedTrace(seed, records=[api.SlotRecord()] +
        [api.SlotRecord(accepted={"left": (2,), "right": (3,)}) for _ in range(100)],
        artifact="target-a", domain="test-domain")
    selection = search.select(seed, trace, targets)
    request = selection.requests[0]
    expected = search.probe(seed, trace, targets, request, 20_000)
    for _ in range(99):
        answer = search.probe(seed, trace, targets, request, 20_000)
        assert _without_time(answer.as_dict()) == _without_time(expected.as_dict())
        assert answer.candidate == expected.candidate
    assert all(payload["retained"] == "selected" for _op, payload in wire[1:])
    assert all("seed" not in payload and "trace" not in payload for _op, payload in wire[1:])

    # Candidate feedback can replace latest Select but not the active Probe seed.
    empty = api.Stimulus({}, [api.EventSlot(reset=True)])
    search.select(empty, _trace(empty), targets)
    search.probe(seed, trace, targets, request, 20_000)
    assert wire[-1][1]["retained"] == "probing"

    # An equal value with another object identity uses full checked restoration.
    equal_seed = api.Stimulus.from_dict(seed.as_dict())
    search.probe(equal_seed, trace, targets, request, 20_000)
    assert "retained" not in wire[-1][1]
    assert wire[-1][1]["seed"] == seed.as_dict()

    # Moving Probe to another seed evicts the old execution context, not its
    # confirmation. Revisit it without another Select or an extra round trip.
    other = api.Stimulus.from_dict({**seed.as_dict(), "source": "other seed"})
    other_trace = api.AuditedTrace(
        other, records=trace.records, artifact=trace.artifact, domain=trace.domain)
    selected = search.select(other, other_trace, targets)
    search.probe(other, other_trace, targets, selected.requests[0], 20_000)
    answer = search.probe(seed, trace, targets, request, 20_000)
    assert "retained" not in wire[-1][1]
    assert _without_time(answer.as_dict()) == _without_time(expected.as_dict())
    assert answer.candidate == expected.candidate
    search.close()
    assert search._selected is None and search._probing is None


@pytest.mark.parametrize("mutation", ["seed", "trace", "source", "target", "request", "role"])
def test_retained_probe_cannot_change_its_audited_context(context, monkeypatch, mutation):
    built = jobs._build(context)
    targets = list(built[6])
    prepared = child.PreparedGeneration({**context, "targets": targets})
    selected = prepared.select({**_query(), "targets": targets})
    payload = {"retained": "selected", "targets": targets,
               "request": _requests(selected)[0], "timeout_ms": 20_000}
    if mutation in {"seed", "trace", "source"}:
        payload[mutation] = {}
    elif mutation == "target":
        payload["targets"] = []
    elif mutation == "request":
        payload["request"] = {**payload["request"], "rank": 999}
    else:
        payload["retained"] = "missing"
    with pytest.raises((ValueError, api.Refused)):
        prepared.probe(payload)


def test_retained_probe_does_not_reserialize_or_hash_trace(context, monkeypatch):
    built = jobs._build(context)
    targets = list(built[6])
    prepared = child.PreparedGeneration({**context, "targets": targets})
    query = {**_query(), "targets": targets}
    selected = prepared.select(query)
    payload = {"retained": "selected", "targets": targets,
               "request": _requests(selected)[0], "timeout_ms": 20_000}

    def no_trace_key(*_args):
        pytest.fail("retained Probe must not serialize or hash the trace again")

    monkeypatch.setattr(child._TraceKey, "from_payload", no_trace_key)
    assert prepared.probe(payload)["outcome"] == "proposed"
    assert prepared.probe(payload)["outcome"] == "proposed"


@pytest.mark.parametrize("role", ["selected", "probing"])
def test_retained_probe_requires_a_live_context(context, role):
    built = jobs._build(context)
    prepared = child.PreparedGeneration({**context, "targets": list(built[6])})
    with pytest.raises(ValueError, match="available retained context"):
        prepared.probe({"retained": role, "targets": list(built[6]),
                        "request": {}, "timeout_ms": 1000})


def test_preparation_owner_survives_seed_feedback_but_not_other_source_contexts(context):
    from behave.sim.engines.local_ir import _LocalQueryPreparation

    built = jobs._build(context)
    targets = list(built[6])
    prepared = child.PreparedGeneration({**context, "targets": targets})
    first = {**_query(), "targets": targets}
    selection = prepared.select(first)
    assert prepared.selected._query_preparation is prepared.queries
    answer = prepared.probe({**first, "request": _requests(selection)[0], "timeout_ms": 20_000})
    assert answer["outcome"] == "proposed" and answer['goal'] is None
    events = prepared.queries.events()
    empty = api.Stimulus({}, [api.EventSlot(reset=True)])
    prepared.select({**_query(empty), "targets": targets})
    assert prepared.selected._query_preparation is prepared.queries
    assert prepared.queries.events() is events
    another = child.PreparedGeneration({**context, "targets": targets})
    assert another.queries is not prepared.queries
    with pytest.raises(ValueError, match="another procedure or carrier"):
        jobs.selected_trace(another.built, first["seed"], first["trace"], targets,
                            preparation=prepared.queries)
    proc, env = prepared.built[4].proc, prepared.built[4].env
    wrong_carrier = _LocalQueryPreparation(proc, {**env, "foreign": None})
    with pytest.raises(ValueError, match="another procedure or carrier"):
        jobs.selected_trace(prepared.built, first["seed"], first["trace"], targets,
                            preparation=wrong_carrier)


def test_remaining_targets_bind_session_selection_without_changing_feedback(context, monkeypatch):
    counts = {"_occurrences": 0, "_replay_feedback": 0}

    def counted(name, function):
        def call(*args):
            counts[name] += 1
            return function(*args)
        return call

    for name in counts:
        monkeypatch.setattr(jobs, name, counted(name, getattr(jobs, name)))
    built = jobs._build(context)
    available = list(built[6])
    prepared = child.PreparedGeneration({**context, "targets": available})
    query = {**_query(), "targets": available}
    complete = prepared.select(query)
    assert _requests(complete) and complete["replay"]["hits"]
    targets = _requests(complete)[-1]["targets"][:1]
    reduced_query = {**query, "targets": targets}
    expected = []
    for row in _requests(complete):
        kept = [goal for goal in row["targets"] if goal in targets]
        if kept:
            request = api.ProbeRequest.from_dict(row)
            expected.append(request.with_targets(kept).as_dict())

    # A confirmed full selection is not a confirmation of another target set.
    with pytest.raises(ValueError, match="confirmed Select"):
        prepared.probe({**reduced_query, "request": expected[0], "timeout_ms": 1000})
    reduced = prepared.select(reduced_query)
    assert _requests(reduced) == expected
    assert reduced["replay"] == complete["replay"]
    assert counts == {"_occurrences": 1, "_replay_feedback": 1}
    one_shot, _, _ = jobs.decode_context_reply(jobs.select_job({**context, **reduced_query}))
    assert one_shot == reduced
    assert prepared.probe({**reduced_query, "request": expected[0],
                           "timeout_ms": 20_000})["outcome"] == "proposed"

    observation = prepared.select({**query, "targets": []})
    assert _requests(observation) == []
    assert observation["replay"] == complete["replay"]
    assert prepared.select(query) == complete  # No old narrower selection leaks.
    assert counts == {"_occurrences": 2, "_replay_feedback": 2}  # Session + one-shot oracle.
    assert prepared.targets == tuple(available)  # Capability remains immutable.
    with pytest.raises(api.Refused, match="target-without-exact-encoding"):
        prepared.select({**query, "targets": ["not-declared"]})


@pytest.mark.parametrize("reset_at", [None, 49])
def test_select_collects_one_ir_execution_per_input(context, monkeypatch, reset_at):
    """A length-100 seed is audited once, including a mid-trace state reset."""
    from behave.sim.symbolic import interp
    from behave.sim.behavior.instance import BehaviorInstance

    source = FALLIBLE_SOURCE.replace(
        b"        pass\n", b"        self.phase = 0\n").replace(
        b"        if a < b:\n",
        b"        self.phase = self.phase ^ 1\n        if a < b:\n")
    reference = load({"behavior.py": source}, config={"class": "M"})
    slots, records = [api.EventSlot(reset=True)], [api.SlotRecord()]
    for index in range(100):
        if index == reset_at:
            slots.append(api.EventSlot(reset=True))
            records.append(api.SlotRecord())
        slots.append(api.EventSlot(offers=("left", "right")))
        records.append(api.SlotRecord(accepted={"left": (2,), "right": (3,)}))
    seed = api.Stimulus(
        queues={"left": [(2,)] * 100, "right": [(3,)] * 100}, slots=slots)
    trace = api.AuditedTrace(seed, records=records, artifact="target-a", domain="test-domain")
    context = {**context,
               "source": thaw_record_value(parent._authenticated_source_image(reference)),
               "generation_spec": api.GenerationSpec(len(slots), 100, 1, 0, 0).as_dict()}
    built, image, _ = jobs._build_with_context(context)
    prepared = child.PreparedGeneration({
        **context, "targets": list(built[6]),
        "cached_context": base64.b64encode(image).decode("ascii")})
    query = {"seed": seed.as_dict(), "trace": trace.as_dict(), "targets": list(built[6])}
    calls = []
    run_step = interp._run_step
    identity_reads = []
    original_identity = BehaviorInstance.identity.fget

    def identity(instance):
        identity_reads.append(instance)
        return original_identity(instance)

    def counted(proc, semantics, inputs, state=None, *args, **kwargs):
        calls.append((inputs, state))
        return run_step(proc, semantics, inputs, state, *args, **kwargs)

    monkeypatch.setattr(interp, "_run_step", counted)
    monkeypatch.setattr(BehaviorInstance, "identity", property(identity))
    selected = prepared.select(query)
    assert len(calls) == 100
    assert identity_reads == [prepared.built[4]]
    assert selected["replay"]["hits"]
    observed = tuple(prepared.selected.occurrences.values())
    assert len(observed) == 100
    assert observed[0]["state"]["phase"] == 0
    if reset_at is not None:
        assert observed[reset_at]["state"]["phase"] == 0
        assert observed[reset_at]["position"] == 0

    # The independent one-shot reconstruction agrees in every input, pre-state,
    # occurrence ID and hit. It never substitutes an unverified native result.
    mapped = jobs._occurrences(built[4], built[7], seed, trace, _contract())
    reconstructed = jobs._interpret_occurrences(built[4], mapped, _contract(), prepared.selected.observations)
    assert _observation_images(reconstructed) == _observation_images(observed)
    assert len(calls) == 200
    assert prepared.select(query) == selected
    assert len(calls) == 200  # A new target selection does not execute the seed.

    # Feedback Select must not evict the seed currently supplying Probe's
    # requests. In particular, none of its 100 states or reset/history prefixes
    # need interpreting again after confirming a new trace.
    empty = api.Stimulus({}, [api.EventSlot(reset=True), api.EventSlot()])
    feedback = {**_query(empty), "targets": list(built[6])}
    probe = {**query, "request": _requests(selected)[0], "timeout_ms": 20_000}
    expected = prepared.probe(probe)
    active = prepared.selected
    for _ in range(3):
        prepared.select(feedback)
        assert _without_time(prepared.probe(probe)) == _without_time(expected)
        assert prepared.selected is active
        assert len(calls) == 200
        assert _observation_images(active.occurrences.values()) == _observation_images(observed)


@pytest.mark.parametrize("corrupt", ["hits", "outputs"])
def test_select_discards_collected_observations_when_audit_disagrees(context, monkeypatch, corrupt):
    from behave.sim.symbolic import interp
    from behave.sim.core.instance import StepOutput

    built, image, _ = jobs._build_with_context(context)
    prepared = child.PreparedGeneration({
        **context, "targets": list(built[6]),
        "cached_context": base64.b64encode(image).decode("ascii")})
    run_step = interp._run_step

    def wrong(*args, **kwargs):
        state, outputs, hits, error = run_step(*args, **kwargs)
        if corrupt == "hits":
            hits = set()
        else:
            outputs = StepOutput()
        return state, outputs, hits, error

    monkeypatch.setattr(interp, "_run_step", wrong)
    with pytest.raises(api.Refused, match="behavior-generation-feedback-unverified"):
        prepared.select({**_query(), "targets": list(built[6])})
    assert prepared.selected is None
    assert prepared.confirmed == set()


def test_select_keeps_the_failure_step_but_no_post_failure_occurrence(context):
    seed = api.Stimulus(
        queues={"left": [(2,), (2,), (5,)], "right": [(3,), (4,), (7,)]},
        slots=[api.EventSlot(reset=True)] + [
            api.EventSlot(offers=("left", "right")) for _ in range(3)])
    trace = api.AuditedTrace(
        seed, records=[api.SlotRecord()] + [
            api.SlotRecord(accepted={"left": (left,), "right": (right,)})
            for left, right in [(2, 3), (2, 4), (5, 7)]],
        artifact="target-a", domain="test-domain")
    context = {**context, "generation_spec": api.GenerationSpec(4, 3, 1, 0, 0).as_dict()}
    built, image, _ = jobs._build_with_context(context)
    prepared = child.PreparedGeneration({
        **context, "targets": list(built[6]),
        "cached_context": base64.b64encode(image).decode("ascii")})
    selected = prepared.select({"seed": seed.as_dict(), "trace": trace.as_dict(),
                                "targets": list(built[6])})
    observed = tuple(prepared.selected.occurrences.values())
    assert [item["rank"] for item in observed] == [1, 2]
    assert any(built[5][hit].cls == "safety" for hit in selected["replay"]["hits"])
    mapped = jobs._occurrences(built[4], built[7], seed, trace, _contract())
    reconstructed = jobs._interpret_occurrences(built[4], mapped, _contract(), prepared.selected.observations)
    assert _observation_images(reconstructed) == _observation_images(observed)


@pytest.mark.parametrize("probes", [1, 8, 64])
@pytest.mark.parametrize("prefix", ['path-proposal:', 'native-path:', 'behavior/'])
def test_preparation_is_constant_but_every_probe_is_executed(context, monkeypatch, probes, prefix):
    from behave.sim.engines.local_ir import _LocalQueryPreparation

    counts = {"build": 0, "trace": 0, "query": 0}

    def counted(name, function):
        def call(*args, **kwargs):
            counts[name] += 1
            return function(*args, **kwargs)
        return call

    monkeypatch.setattr(child, "_build_with_context",
                        counted("build", child._build_with_context))
    monkeypatch.setattr(jobs, "_occurrences", counted("trace", jobs._occurrences))
    monkeypatch.setattr(_LocalQueryPreparation, "probe",
                        counted("query", _LocalQueryPreparation.probe))
    available = jobs._build(context)[6]
    prepared = child.PreparedGeneration({**context, "targets": list(available)})
    query = {**_query(), "targets": list(available)}
    def chosen(selection):
        return next(r for r in _requests(selection)
                    if r['occurrence'].startswith(prefix))

    request = chosen(prepared.select(query))
    for _ in range(probes):
        assert chosen(prepared.select(query)) == request
        result = prepared.probe({**query, "request": request, "timeout_ms": 20_000})
        assert result["outcome"] == ('proposed' if prefix == 'path-proposal:' else 'sat')
    assert counts == {"build": 1, "trace": 1, "query": probes}


@pytest.mark.parametrize("mutation", ["seed", "trace", "target", "source", "request"])
def test_session_cannot_splice_trace_context_or_request(context, mutation):
    built = jobs._build(context)
    prepared = child.PreparedGeneration({**context, "targets": list(built[6])})
    query = {**_query(), "targets": list(built[6])}
    selected = prepared.select(query)
    probe = {**copy.deepcopy(query), "request": _requests(selected)[0], "timeout_ms": 1000}
    if mutation == "seed":
        probe["seed"]["source"] = "another seed identity"
    elif mutation == "trace":
        probe["trace"]["domain"] = "another domain"
    elif mutation == "target":
        probe["targets"] = []
    elif mutation == "source":
        probe["source"] = context["source"]
    else:
        probe["request"] = {**probe["request"], "rank": 999}
    with pytest.raises((ValueError, api.Refused)):
        prepared.probe(probe)


def test_new_selection_replaces_old_state_and_refusal_does_not_keep_old_selection(context):
    built = jobs._build(context)
    targets = list(built[6])
    prepared = child.PreparedGeneration({**context, "targets": targets})
    first = {**_query(), "targets": targets}
    selected = prepared.select(first)
    empty = api.Stimulus({}, [api.EventSlot(reset=True), api.EventSlot(), api.EventSlot()])
    assert _requests(prepared.select({**_query(empty), "targets": targets})) == []
    # Baseline-first guidance may revisit an earlier, confirmed seed. The
    # worker reconstructs its occurrence rather than retaining every state.
    result = prepared.probe({**first, "request": _requests(selected)[0], "timeout_ms": 1000})
    assert result["outcome"] == "proposed" and result['goal'] is None
    assert prepared.select(first) == selected
    bad = copy.deepcopy(first)
    bad["trace"]["artifact"] = "different target"
    with pytest.raises(api.Refused, match="wrong-artifact"):
        prepared.select(bad)
    assert prepared.selected is None


def test_feedback_does_not_accumulate_seed_histories_or_reuse_solver_answers(context, monkeypatch):
    from behave.sim.engines.local_ir import _LocalQueryPreparation

    built = jobs._build(context)
    targets = list(built[6])
    prepared = child.PreparedGeneration({**context, "targets": targets})
    first = {**_query(), "targets": targets}
    selected = prepared.select(first)
    probe = {**first, "request": _requests(selected)[0], "timeout_ms": 20_000}
    queries = []
    local_hit = _LocalQueryPreparation.probe

    def solve(*args, **kwargs):
        queries.append(id(args[0]))
        return local_hit(*args, **kwargs)

    monkeypatch.setattr(_LocalQueryPreparation, "probe", solve)
    expected = prepared.probe(probe)
    active = weakref.ref(prepared.selected)
    for index in range(5):
        empty = api.Stimulus({}, [api.EventSlot(reset=True)] * (index + 1))
        prepared.select({**_query(empty), "targets": targets})
        feedback = weakref.ref(prepared.selected)
        assert active() is not None
        assert _without_time(prepared.probe(probe)) == _without_time(expected)
        assert feedback() is None  # Not a cache of all feedback histories.
        assert prepared.selected is active()
    assert len(queries) == 6  # Even identical requests really call the solver.

    # A second active seed releases the first; memory does not grow with the
    # worklist. These are execution roles, not a hidden numeric cache quota.
    other_seed = api.Stimulus.from_dict({**first["seed"], "source": "second seed"})
    other = {**_query(other_seed), "targets": targets}
    other_selected = prepared.select(other)
    prepared.probe({**other, "request": _requests(other_selected)[0], "timeout_ms": 20_000})
    assert active() is None


def test_retained_probe_seed_still_checks_new_target_selection_and_failed_audit(context):
    built = jobs._build(context)
    targets = list(built[6])
    prepared = child.PreparedGeneration({**context, "targets": targets})
    first = {**_query(), "targets": targets}
    complete = prepared.select(first)
    request = _requests(complete)[0]
    prepared.probe({**first, "request": request, "timeout_ms": 20_000})
    active = prepared.selected
    empty = api.Stimulus({}, [api.EventSlot(reset=True)])
    prepared.select({**_query(empty), "targets": targets})
    reduced = {**first, "targets": request["targets"][:1]}
    with pytest.raises(ValueError, match="confirmed Select"):
        prepared.probe({**reduced, "request": request, "timeout_ms": 20_000})
    selected = prepared.select(reduced)
    assert prepared.selected is active
    assert selected["replay"] == complete["replay"]
    assert prepared.select(first) == complete

    confirmations = prepared.confirmed.copy()
    bad = copy.deepcopy(first)
    bad["trace"]["artifact"] = "different target"
    with pytest.raises(api.Refused, match="wrong-artifact"):
        prepared.select(bad)
    assert prepared.selected is None
    assert prepared.confirmed == confirmations
    assert prepared.probing[1] is active
    assert prepared.select(first) == complete


def test_select_does_not_carry_array_state_across_seeds(context):
    source = FALLIBLE_SOURCE.replace(b"        pass", b"        self.buf = [0, 0]").replace(
        b"        if a < b:", b"        self.buf[0] = (self.buf[0] + a) & 255\n        if a < b:")
    reference = load({"behavior.py": source}, config={"class": "M"})
    context = {**context,
               "source": thaw_record_value(parent._authenticated_source_image(reference))}
    built = jobs._build(context)
    targets = list(built[6])
    prepared = child.PreparedGeneration({**context, "targets": targets})
    initial = copy.deepcopy(dict(prepared.built[4].proc.initial))
    first = {**_query(), "targets": targets}
    expected = prepared.select(first)
    states = copy.deepcopy({name: row["state"]
                            for name, row in prepared.selected.occurrences.items()})
    empty = api.Stimulus({}, [api.EventSlot(reset=True), api.EventSlot(), api.EventSlot()])
    prepared.select({**_query(empty), "targets": targets})
    assert prepared.select(first) == expected
    assert {name: row["state"] for name, row in prepared.selected.occurrences.items()} == states
    assert dict(prepared.built[4].proc.initial) == initial


def test_session_start_failure_is_not_retried(context, monkeypatch):
    starts = []

    def unavailable(*args, **kwargs):
        starts.append(args)
        error = IsolatedJobFailure("timeout", "prepare deadline")
        error.add_note("Generation session cleanup unconfirmed: original namespace evidence")
        raise error

    monkeypatch.setattr(transport, "JobSession", unavailable)
    search = transport.BehaviorSearch(context, None, timeout_s=1, probe_budget=2)
    for _ in range(3):
        with pytest.raises(WorkerFailed, match="prepare deadline") as caught:
            search._call("select", {})
        assert caught.value.__notes__ == [
            "Generation session cleanup unconfirmed: original namespace evidence"]
    search.close()
    assert len(starts) == 1


def test_source_audit_refusal_keeps_its_evidence_through_public_select(monkeypatch):
    refusal = api.Refusal(
        "behavior-generation-feedback-unverified",
        "source and IR disagree",
        audit={"status": "inconsistent", "findings": ["state differs at step 0"]})
    failure = api.Refused(refusal)
    failure.add_note("original cleanup evidence")
    starts = []

    def unavailable(*args, **kwargs):
        starts.append(args)
        raise failure

    monkeypatch.setattr(transport, "JobSession", unavailable)
    search = transport.BehaviorSearch({}, None, timeout_s=1, probe_budget=None)
    provider = SimpleNamespace(_searches=[search])
    seed = _stimulus()
    for _ in range(2):
        answer = parent.BehaviorIsolatedGenerationProvider._select_from_trace(
            provider, seed, _trace(seed), ())
        assert answer is refusal
        assert answer.as_dict()["detail"]["audit"]["findings"] == ["state differs at step 0"]
    assert starts == [("behavior_generation_session", {})]
    assert search.failure is failure
    assert failure.__notes__ == ["original cleanup evidence"]


@pytest.mark.parametrize("failure_kind", ["worker", "audit", "unknown", "unsupported"])
def test_closed_behavior_session_stops_queries_but_local_nonanswers_do_not(
        monkeypatch, failure_kind):
    from behave.sim.generation import generate

    reference = load({"behavior.py": FALLIBLE_SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    spec = api.GenerationSpec(3, 1, 1, 1, 0)
    provider = api.prepare_behavior_generation(
        task, spec, reference, _trace, timeout_s=60, timeout_ms=20_000)
    opened, queried = [], []
    audit = api.Refusal("behavior-generation-feedback-unverified", "source and IR disagree",
                        audit={"status": "inconsistent", "step": 0})

    class Session:
        def __init__(self, _name, payload, **_kwargs):
            self.prepared = child.PreparedGeneration(payload)
            self.opened = self.prepared.layout()
            opened.append(self)

        def _exchange(self, op, payload, _timeout):
            if op == "select":
                return self.prepared.select(payload)
            queried.append(payload["request"])
            if failure_kind == "audit":
                raise api.Refused(audit)
            if failure_kind == "worker":
                failure = IsolatedJobFailure("no-answer", "worker exited: memory-limit")
                failure.add_note("original cleanup evidence")
                raise failure
            return {"version": jobs._PROBE_VERSION, "outcome": failure_kind,
                    "scope": "exact-local-hit", "goal": None, "candidate": None,
                    "detail": "local query did not answer", "solve_ms": 0,
                    "catalogue_digest": provider.catalogue_manifest().digest()}

        def close(self):
            pass

    monkeypatch.setattr(transport, "JobSession", Session)
    original = _stimulus()
    seed = api.Stimulus(queues=original.queues, slots=[api.EventSlot(
        reset=slot.reset, offers=slot.offers,
        controls={"rst": int(slot.reset), "result_ready": 1}) for slot in original.slots])
    result = generate((seed,), provider, contract=_contract(), generation_spec=spec,
                      probe_budget=10)
    row = result.as_dict()["telemetry"]
    assert result.baseline == (seed,) and result.generated == ()
    assert len(opened) == 1
    fatal = failure_kind in {"worker", "audit"}
    if fatal:
        assert len(queried) == row["probes"] == row["probe_refusals"] == 1
        assert sum(row["outcomes"].values()) == 0
        assert row["termination"] == "provider-unavailable"
        refusal = provider.search_refusal()
        assert provider.search_refusal() is refusal
        assert row["provider_refusal"] == refusal.as_dict()
        if failure_kind == "audit":
            assert refusal is audit
        else:
            assert refusal.detail["failure"] == "no-answer"
            assert "memory-limit" in refusal.detail["detail"]
            assert list(refusal.detail["cleanup_notes"]) == ["original cleanup evidence"]
    else:
        assert len(queried) > 1
        assert row["outcomes"][failure_kind] == len(queried)
        assert provider.search_refusal() is None
        assert row["provider_refusal"] is None
    assert provider._searches == []  # Receipt survives; the worker does not.
    with provider.search_scope(probe_budget=1):
        assert provider.search_refusal() is None  # A new invocation can retry.


def test_protocol_cannot_reopen_context(context, monkeypatch):
    class Prepared:
        def __init__(self, payload):
            assert payload == context

        def layout(self):
            return {}

    monkeypatch.setattr(child, "PreparedGeneration", Prepared)
    wire = b"".join(json.dumps({"version": child.VERSION, "id": i,
                               "op": "open", "payload": context}).encode() + b"\n"
                    for i in range(3))
    output = io.BytesIO()
    child.serve(io.BytesIO(wire), output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [row["ok"] for row in replies] == [True, False]


@pytest.mark.isolation
@pytest.mark.parametrize("probe_budget", [2, None])
@pytest.mark.parametrize("restricted", [False, True])
def test_public_provider_owns_one_real_session_and_closes_on_exception(probe_budget, restricted, monkeypatch):
    from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope

    reference = load({"behavior.py": FALLIBLE_SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    provider = api.prepare_behavior_generation(
        task, api.GenerationSpec(3, 1, 1, 1, 0), reference, _trace, timeout_s=60)
    seed = _stimulus()
    targets = (tuple(g for g in provider.targetable_goals
                     if provider.catalogue_manifest()[g].cls == "safety")
               if restricted else None)
    with search_scope(SearchBudget(SearchPolicy(time_budget_s=60)).direction(1)):
        cold = provider.select(seed, targets=targets)
        assert cold.requests
        cold_answer = provider.probe(seed, cold.requests[0])
    budget = SearchBudget(SearchPolicy(time_budget_s=60)).direction(1)
    with (pytest.raises(RuntimeError, match="test interruption"), search_scope(budget),
          provider.search_scope(probe_budget=probe_budget)):
        def no_repacking(*_args, **_kwargs):
            pytest.fail("active session must not rebuild source payload or durable cache keys")

        monkeypatch.setattr(type(provider), "_common_payload", no_repacking)
        monkeypatch.setattr(parent.GenerationCacheKey, "select", no_repacking)
        monkeypatch.setattr(parent.GenerationCacheKey, "probe", no_repacking)
        provider.prepare_seeds((seed,))
        selected = provider.select(seed, targets=targets)
        assert selected.as_dict() == cold.as_dict()
        answer = provider.probe(seed, selected.requests[0])
        assert answer.outcome == cold_answer.outcome
        assert answer.candidate == cold_answer.candidate
        assert provider.probe(seed, selected.requests[0]).outcome == answer.outcome
        session = provider._searches[-1].session
        raise RuntimeError("test interruption")
    assert session._child is None and provider._searches == []
    assert [r["op"] for r in provider.execution_cost()["events"]] == [
        "open", "select", "probe", "probe"]
    # The handle has no retained worker or selection after its search exits.
    assert provider._selected == {} and provider._prepared_traces == {}


@pytest.mark.isolation
@pytest.mark.parametrize("wrong", [False, True])
def test_behavior_pair_searches_keep_independent_workers_and_public_score(tmp_path, monkeypatch, wrong):
    from behave.sim.deferred_generation import DeferredGenerationProvider
    from behave.sim.generation import UnavailableGenerationProvider

    from .test_settled_protocol import contract

    prepared = []
    bind = DeferredGenerationProvider.bind

    def remember(recipe, provider):
        bind(recipe, provider)
        prepared.append(provider)

    monkeypatch.setattr(DeferredGenerationProvider, "bind", remember)

    source = """
class M:
    def reset(self):
        pass
    def process(self, in_a):
        if in_a == 37:
            out_y = 52
        else:
            out_y = (in_a * 2) & 255
        return {"out_y": out_y}
"""
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": source.encode()},
        config={"class": "M"})
    candidate = source.replace("out_y = 52", "out_y = 0") if wrong else source
    path = tmp_path / "behavior.py"
    path.write_text(candidate)
    c = contract()
    task = api.admit(c, api.realization_of_behavior_source(candidate, c, file=path.name),
                     reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name, timeout_s=60),
                                  api.GenerationSpec(3, 3, 0, 0))
    providers = handle.generation_providers(reference, workdir=tmp_path / "search")
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=3),
        providers=providers, probe_budget=2, reference=reference))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed is not wrong
    if wrong:
        # A checked mismatch decides zero without executing the remaining
        # requested runs; incompleteness alone must never establish a pass.
        assert result.evaluation.failure_witness is not None
    else:
        assert result.evaluation.complete
    assert len(result.generated.searches) == 2
    assert len(prepared) == 2
    assert any(search.generated for search in result.generated.searches)
    # Portfolio order is canonical by provider name, not factory-bind order.
    prepared = {provider.name: provider for provider in prepared}
    assert set(prepared) == {search.provider for search in result.generated.searches}
    for search in result.generated.searches:
        provider = prepared[search.provider]
        if type(provider) is UnavailableGenerationProvider:
            assert wrong and not search.generated and search.telemetry["probes"] == 0
            refusal = provider.search_refusal()
            assert (refusal.kind, refusal.why) == ("generation-search-stopped", "candidate-failed")
        else:
            domains = provider.execution_cost()["domains"]
            assert len(domains) == 1
            assert [row["op"] for row in domains[0]["execution"]["events"]].count("open") == 1
            assert provider._active is None
        if wrong and not search.generated:
            # The other direction may find the counterexample first. Stop on
            # that concrete failure; do not require both workers to rediscover it.
            assert search.telemetry["termination"] == "candidate-failed"
            assert not search.telemetry.get("failure")
        else:
            assert search.generated
