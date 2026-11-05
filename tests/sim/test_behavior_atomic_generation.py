"""Public worker protocol: native baseline -> residual query -> native hit."""

import pytest

from behave.sim import api
from behave.sim.behavior import atomic_exact, lowering, signature
from behave.sim.core.record_value import thaw_record_value
from behave.sim.engines.behavior import generation as parent
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.engines.behavior.generation_session import PreparedGeneration
from tests.support.atomic_behavior import _contract, _schedule
from tests.support.reference import load

pytestmark = [pytest.mark.solver, pytest.mark.isolation]

SOURCE = b'''class M:
    def reset(self):
        self.x = 0
        self.y = 0
    def process_a(self, a):
        self.x = self.y
        if a == 3:
            return {"oa": 7}
        return {"oa": a}
    def process_b(self, b):
        self.y = b
        return {"ob": self.x}
'''


def _payload(source=SOURCE, contract=None):
    reference = load({"behavior.py": source}, config={"class": "M"})
    return {"source": thaw_record_value(parent._authenticated_source_image(reference)),
            "contract": (contract or _contract()).as_dict(),
            "generation_spec": api.GenerationSpec(3, 2, 0, 0, 0).as_dict(),
            "target_artifact": "target-a"}


def _seed(value=0, *, depth=2):
    schedule = _schedule((2, 2), n_slots=depth)
    return api.Stimulus(queues={"a_in": ((value,),) + ((0,),) * (depth - 1),
                               "b_in": tuple(((index + 1) % 4,) for index in range(depth))},
                        slots=[slot.event for slot in schedule.slots], clock_schedule=schedule)


def _query(seed):
    counters = dict.fromkeys(dict(seed.queues), 0)
    records = []
    for slot in seed.slots:
        accepted = {}
        for endpoint in slot.offers:
            index = counters[endpoint]
            accepted[endpoint] = dict(seed.queues)[endpoint][index]
            counters[endpoint] += 1
        records.append(api.SlotRecord(accepted=accepted))
    trace = api.AuditedTrace(seed, records=records, artifact="target-a", domain="target-domain")
    return {"seed": seed.as_dict(), "trace": trace.as_dict()}


def _decisions(built):
    return [goal for goal in built[6] if built[5][goal].family == "decision"]


def test_atomic_named_jobs_close_the_native_hit_loop_without_bounded_analysis(monkeypatch):
    monkeypatch.setattr(atomic_exact, "_AtomicFormula", lambda *a, **k:
                        pytest.fail("local generation constructed a whole-run proof"))
    def no_output_proof(*_args, **_kwargs):
        pytest.fail("local generation requested universal handler-output admission")

    # Both modules are imported before patching: a lazy from-import must not
    # retain a patched function beyond this fixture's lifetime.
    monkeypatch.setattr(lowering, "output_contract_evidence", no_output_proof)
    monkeypatch.setattr(signature, "output_contract_evidence", no_output_proof)
    payload = _payload()
    built = jobs._build(payload)
    layout = jobs.decode_layout_reply(jobs.decode_context_reply(jobs.layout_job(payload))[0])
    assert layout.input_endpoints == (("a_in", (("a", "a.a"),)), ("b_in", (("b", "b.b"),)))
    query = _query(_seed())
    selected = jobs.selected_trace(built, **query, targets=_decisions(built))
    assert selected.atomic.prefixes[1].state["x"] == 0
    assert selected.atomic.prefixes[1].state["y"] == 1
    remaining = sorted(set(_decisions(built)) - set(selected.replay.hits))
    assert remaining
    response = jobs.decode_selection_reply(jobs.decode_context_reply(
        jobs.select_job({**payload, **query, "targets": remaining}))[0])
    request = response.requests[0]
    result = jobs.decode_probe_reply(jobs.decode_context_reply(jobs.probe_job(
        {**payload, **query, "targets": remaining, "request": request.as_dict(), "timeout_ms": 20_000}))[0],
        expected_catalogue_digest=built[5].digest())
    assert result.outcome.value == "sat", result
    assert result.scope.value == "exact-local-hit"
    candidate = result.candidate
    assert dict(candidate.queues)["a_in"][0] == (3,)
    confirmed = jobs.selected_trace(built, **_query(candidate), targets=remaining)
    assert result.goal in confirmed.replay.hits
    assert result.goal not in selected.replay.hits
    assert built[5] == confirmed.manifest


def test_atomic_analysis_keeps_handler_output_admission(monkeypatch):
    class ProofRequested(Exception):
        pass

    def require_proof(*_args, **_kwargs):
        raise ProofRequested

    monkeypatch.setattr(signature, "output_contract_evidence", require_proof)
    monkeypatch.setattr(lowering, "output_contract_evidence", require_proof)
    schedule = _seed().clock_schedule
    local = atomic_exact.prepare_atomic_context(
        SOURCE.decode(), "behavior.py", _contract(), schedule, cls="M")
    assert not hasattr(local, "bind")
    with pytest.raises(ProofRequested):
        atomic_exact.build_atomic_behavior(
            SOURCE.decode(), "behavior.py", _contract(), schedule, cls="M")


@pytest.mark.parametrize("wide_domain", ("a", "b"))
def test_atomic_field_scope_keeps_other_domain_input_fixed(wide_domain):
    source = b'''class M:
    def reset(self):
        pass
    def process_a(self, a):
        if a == 173:
            return {"oa": 7}
        return {"oa": a & 15}
    def process_b(self, b):
        if b == 173:
            return {"ob": 7}
        return {"ob": b & 15}
'''
    base = _contract()
    contract = api.TaskContract([
        api.Endpoint(endpoint.name, endpoint.direction,
                     [api.Field(wide_domain, api.FiniteType(8), wide_domain)],
                     endpoint.adapter, endpoint.clock, kind=endpoint.kind)
        if endpoint.name == f"{wide_domain}_in" else endpoint
        for endpoint in base.endpoints], base.clocks)
    built = jobs._build(_payload(source, contract))
    seed = _seed()
    selected = jobs.selected_trace(built, **_query(seed), targets=_decisions(built))
    residual = sorted(set(_decisions(built)) - set(selected.replay.hits))
    selected.select(residual)
    first = [request for request in selected.requests.values()
             if selected.occurrences[request.occurrence]["position"] == 0]
    narrow = next(request for request in first if len(request.mutable_fields) == 1)
    assert narrow.mutable_fields[0].endpoint != f"{wide_domain}_in"
    assert selected.probe(narrow.as_dict(), 20_000)["outcome"] == "local-unsat"
    broad = next(request for request in first if len(request.mutable_fields) == 2)
    answer = selected.probe(broad.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert dict(candidate.queues)[f"{wide_domain}_in"][0] == (173,)
    confirmed = jobs.selected_trace(built, **_query(candidate), targets=residual)
    assert answer["goal"] in confirmed.replay.hits
    assert answer["goal"] not in selected.replay.hits


def test_atomic_field_scope_keeps_structured_input_whole():
    from .test_behavior_atomic_exact import MULTI_ENDPOINT_SOURCE, _two_endpoint_contract

    source = MULTI_ENDPOINT_SOURCE.replace(
        "self.x = pair[0] + pair[1] + int(flag)",
        "self.x = pair[0] + pair[1] + int(flag)\n"
        "        if pair[1] == 3:\n            self.x = 8")
    contract = _two_endpoint_contract()
    schedule = _schedule((2, 2), n_slots=1,
                         offers_by_domain={"a": ("a_left", "a_right"), "b": ("b_in",)})
    seed = api.Stimulus(queues={"a_left": ((False,),), "a_right": (((0, 0),),),
                               "b_in": ((1,),)},
                        slots=[slot.event for slot in schedule.slots], clock_schedule=schedule)
    built = jobs._build(_payload(source.encode(), contract))
    selected = jobs.selected_trace(built, **_query(seed), targets=_decisions(built))
    residual = sorted(set(_decisions(built)) - set(selected.replay.hits))
    selected.select(residual)
    for request in selected.requests.values():
        answer = selected.probe(request.as_dict(), 20_000)
        if len(request.mutable_fields) < 3:
            assert answer["outcome"] == "local-unsat", answer
        else:
            assert answer["outcome"] == "sat", answer
            candidate = api.Stimulus.from_dict(answer["candidate"])
            assert dict(candidate.queues)["a_right"][0][0][1] == 3
            confirmed = jobs.selected_trace(built, **_query(candidate), targets=residual)
            assert answer["goal"] in confirmed.replay.hits
    assert {len(request.mutable_fields) for request in selected.requests.values()} == {1, 2, 3}


def test_atomic_session_reuses_confirmation_and_reconstructs_evicted_seed_without_native_rerun(monkeypatch):
    from behave.sim.engines.behavior import atomic_generation

    built = jobs._build(_payload())
    targets = _decisions(built)
    session = PreparedGeneration({**_payload(), "targets": targets})
    calls = []
    audit = atomic_exact._AtomicContext.source_audit
    contexts = []
    prepare = atomic_generation.prepare_atomic_context

    def count_prepare(*args, **kwargs):
        contexts.append(args[3])
        return prepare(*args, **kwargs)

    monkeypatch.setattr(atomic_generation, "prepare_atomic_context", count_prepare)

    def counted(self, *args, **kwargs):
        calls.append(self.schedule.digest)
        return audit(self, *args, **kwargs)

    monkeypatch.setattr(atomic_exact._AtomicContext, "source_audit", counted)
    query = {**_query(_seed()), "targets": targets}
    first = session.select(query)
    assert session.select(query) == first
    assert len(calls) == 1
    session.select({**_query(_seed(1)), "targets": targets})
    assert len(calls) == 2
    result = session.probe({**query, "request": jobs.decode_selection_reply(first).requests[0].as_dict(),
                            "timeout_ms": 20_000})
    assert result["outcome"] == "sat", result
    assert session.selected.replay is None
    assert len(calls) == 2
    assert len(contexts) == 1  # Same schedule, even after wire reconstruction.


@pytest.mark.parametrize("depth", [2, 100])
def test_atomic_active_probe_survives_candidate_feedback_without_rebuilding_prefixes(monkeypatch, depth):
    from behave.sim.behavior.atomic_selection import AtomicSelectedTrace

    payload = {**_payload(), "generation_spec": api.GenerationSpec(depth, depth, 0, 0, 0).as_dict()}
    targets = _decisions(jobs._build(payload))
    session = PreparedGeneration({**payload, "targets": targets})
    query = {**_query(_seed(depth=depth)), "targets": targets}
    first = session.select(query)
    probe = {**query, "request": jobs.decode_selection_reply(first).requests[0].as_dict(),
             "timeout_ms": 20_000}
    expected = session.probe(probe)
    active = session.selected
    prefixes = active.atomic.prefixes
    construct = AtomicSelectedTrace.__init__
    constructions = []

    def counted(self, *args, **kwargs):
        constructions.append(kwargs.get("verify_feedback", True))
        construct(self, *args, **kwargs)

    monkeypatch.setattr(AtomicSelectedTrace, "__init__", counted)
    for value in (1, 3, 2):
        feedback = session.select({**_query(_seed(value, depth=depth)), "targets": targets})
        if value == 3:
            assert expected["goal"] in feedback["replay"]["hits"]
        actual = session.probe(probe)
        assert {k: v for k, v in actual.items() if k != "solve_ms"} == {
            k: v for k, v in expected.items() if k != "solve_ms"}
        assert session.selected is active
        assert session.selected.atomic.prefixes is prefixes
    # Each candidate is audited. The original multi-clock global-state prefix
    # is reused without another audit or semantic-witness reconstruction.
    assert constructions == [True, True, True]


def test_atomic_unconfirmed_probe_and_wrong_seed_are_not_accepted():
    built = jobs._build(_payload())
    targets = _decisions(built)
    session = PreparedGeneration({**_payload(), "targets": targets})
    query = {**_query(_seed()), "targets": targets}
    first = session.select(query)
    with pytest.raises(ValueError, match="confirmed Select"):
        session.probe({**_query(_seed(1)), "targets": targets,
                       "request": jobs.decode_selection_reply(first).requests[0].as_dict(),
                       "timeout_ms": 20_000})
    bad = jobs.select_job({**_payload(), **query, "seed": _seed(1).as_dict()})
    refusal = jobs.decode_selection_reply(jobs.decode_context_reply(bad)[0])
    assert refusal.kind == "behavior-generation-source-trace-wrong-seed"


@pytest.mark.parametrize("depth,wrong", [(2, False), (100, False), (100, True)])
def test_public_provider_search_scope_uses_actual_scored_replay(tmp_path, record_property, depth, wrong):
    import json
    from time import monotonic

    started = monotonic()
    path = tmp_path / "behavior.py"
    candidate = SOURCE.replace(b'"oa": 7', b'"oa": 6') if wrong else SOURCE
    path.write_bytes(candidate)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {"behavior.py": SOURCE}, config={"class": "M"})
    contract = _contract()
    realization = api.realization_of_behavior_source(candidate.decode(), contract, file="behavior.py")
    task = api.admit(contract, realization, reference=reference)
    spec = api.GenerationSpec(depth, depth, 0, 0, 0)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"), spec)
    evaluations = []

    def trace_of(seed):
        for evaluation in evaluations:
            if evaluation.stimuli == (seed,):
                return evaluation.results[0].trace
        authority = handle.replay((seed,), reference=reference)
        if not authority.authoritative:
            raise RuntimeError(str([run.refused.as_dict() if run.refused else None
                                    for run in authority.evaluation.results]))
        assert authority.authoritative, authority.as_dict()
        evaluations.append(authority.evaluation)
        return authority.evaluation.results[0].trace

    prepared_at = monotonic()
    seed = _seed(depth=depth)
    trace_of(seed)
    assert evaluations[0].passed
    baseline_at = monotonic()
    provider = api.prepare_behavior_generation(task, spec, reference, trace_of,
                                               timeout_s=60, timeout_ms=20_000)
    layout_at = monotonic()
    targets = [goal for goal, declaration in provider.catalogue_manifest().items()
               if declaration.family == "decision"]
    residual = sorted(set(targets) - set(evaluations[0].hits))
    assert residual
    with provider.search_scope(probe_budget=1):
        selected = provider.select(seed, targets=residual)
        assert isinstance(selected, api.ProbeSelection), selected.as_dict()
        assert set(residual).isdisjoint(selected.replay.hits)
        selected_at = monotonic()
        answer = provider.probe(seed, selected.requests[0])
        assert answer.outcome.value == "sat", answer.as_dict()
        probed_at = monotonic()
        confirmed = provider.select(answer.candidate, targets=residual)
        assert isinstance(confirmed, api.ProbeSelection), confirmed.as_dict()
        assert answer.goal in confirmed.replay.hits
        confirmed_at = monotonic()
    assert evaluations[-1].passed is not wrong
    if wrong:
        assert evaluations[-1].failure_witness is not None
    assert answer.goal in evaluations[-1].hits
    assert len(evaluations) == 2  # Each actual input stream executed once.
    record_property("decision_hits", json.dumps({
        "declared": len(targets), "baseline": len(set(targets) & set(evaluations[0].hits)),
        "final": len(set(targets) & (set(evaluations[0].hits) | set(evaluations[-1].hits)))}))
    record_property("stages_seconds", json.dumps({
        "prepare": prepared_at - started, "baseline": baseline_at - prepared_at,
        "layout": layout_at - baseline_at, "select": selected_at - layout_at,
        "probe": probed_at - selected_at, "new_replay_and_select": confirmed_at - probed_at,
        "total": monotonic() - started, "depth_per_endpoint": depth, "wrong_candidate": wrong}))


def test_inactive_domain_is_local_unsat_not_an_unsupported_goal():
    built = jobs._build(_payload())
    schedule = _schedule((2, 3), n_slots=3)
    seed = api.Stimulus(queues={"a_in": ((0,), (0,)), "b_in": ((0,), (0,))},
                        slots=[slot.event for slot in schedule.slots], clock_schedule=schedule)
    targets = _decisions(built)
    selected = jobs.selected_trace(built, **_query(seed), targets=targets)
    residual = sorted(set(targets) - set(selected.replay.hits))
    assert residual
    # Last slot invokes only b; a's branch remains in the catalogue.
    result = selected.atomic.local_hit(2, residual, timeout_ms=20_000)
    assert result.outcome.value == "local-unsat", result.as_dict()
    assert set(residual) <= set(selected.manifest)


def test_undefined_coincident_writes_are_not_filtered_out_to_find_a_candidate():
    source = b'''class M:
    def reset(self):
        self.x = 0
    def process_a(self, a):
        self.x = a
        return {"oa": a}
    def process_b(self, b):
        self.x = b
        if b == 3:
            return {"ob": 7}
        return {"ob": b}
'''
    built = jobs._build(_payload(source))
    schedule = _schedule((2, 2), n_slots=1)
    seed = api.Stimulus(queues={"a_in": ((0,),), "b_in": ((0,),)},
                        slots=[slot.event for slot in schedule.slots], clock_schedule=schedule)
    targets = _decisions(built)
    selected = jobs.selected_trace(built, **_query(seed), targets=targets)
    residual = sorted(set(targets) - set(selected.replay.hits))
    selected.select(residual)
    answer = selected.probe(next(iter(selected.requests.values())).as_dict(), 20_000)
    assert answer["outcome"] == "unsupported", answer
    assert "undefined atomic transition" in answer["detail"]


@pytest.mark.parametrize("reset", [(), ("a", "b"), ("a",)])
def test_worker_memory_search_preserves_written_state_across_reset(reset):
    from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
    from behave.sim.core.memory import MemoryEnvironment, MemoryInterface, MemoryPort, MemoryRun

    from .test_behavior_atomic_memory import SOURCE as MEMORY_SOURCE

    base = _contract()
    contract = api.TaskContract(base.endpoints, base.clocks, memories=(
        MemoryInterface("ram", 2, 4, "a", [MemoryPort("rw")]),))
    environment = MemoryEnvironment([MemoryRun("ram", initial={0: 0, 1: 0, 2: 7, 3: 0},
                                              ready={None: [1] * 3})])
    timings = _schedule((2, 2)).timings
    schedule = GlobalSchedule(timings, (
        GlobalEventSlot(0, event=api.EventSlot(ticks=("a", "b"), offers=("a_in",))),
        GlobalEventSlot(2, event=api.EventSlot(ticks=("a", "b"), resets=reset)),
        GlobalEventSlot(4, event=api.EventSlot(ticks=("a", "b"), offers=("b_in",)))))
    seed = api.Stimulus(queues={"a_in": ((1,),), "b_in": ((0,),)},
                        slots=[slot.event for slot in schedule.slots], clock_schedule=schedule,
                        memory_environment=environment)
    built = jobs._build(_payload(MEMORY_SOURCE.encode(), contract))
    selected = jobs.selected_trace(built, **_query(seed), targets=_decisions(built))
    assert selected.atomic.prefixes[2].state["ram"][1] == 5
    residual = sorted(set(_decisions(built)) - set(selected.replay.hits))
    selected.select(residual)
    request = next(request for request in selected.requests.values()
                   if selected.occurrences[request.occurrence]["position"] == 2)
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert dict(candidate.queues)["b_in"] == ((2,),)
    assert candidate.memory_environment.as_dict() == seed.memory_environment.as_dict()
    confirmed = jobs.selected_trace(built, **_query(candidate), targets=residual)
    assert answer["goal"] in confirmed.replay.hits
    assert confirmed.instance is selected.instance


def test_worker_query_retains_cross_domain_transaction_history():
    from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule

    from .test_behavior_atomic_transactions import IDENTITY, _declarations

    schedule = GlobalSchedule(_schedule((2, 2)).timings, (
        GlobalEventSlot(0, event=api.EventSlot(ticks=("a", "b"), offers=("a_in",))),
        GlobalEventSlot(2, event=api.EventSlot(ticks=("a", "b"))),
        GlobalEventSlot(4, event=api.EventSlot(ticks=("a", "b"), offers=("b_in",)))))
    seed = api.Stimulus(queues={"a_in": ((1,),), "b_in": ((0,),)},
                        slots=[slot.event for slot in schedule.slots], clock_schedule=schedule)
    built = jobs._build(_payload(IDENTITY.encode(), _declarations()))
    target = "monitor:cross:hit"
    selected = jobs.selected_trace(built, **_query(seed), targets=[target])
    assert target not in selected.replay.hits
    request = next(request for request in selected.requests.values()
                   if selected.occurrences[request.occurrence]["position"] == 2)
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    confirmed = jobs.selected_trace(built, **_query(api.Stimulus.from_dict(answer["candidate"])),
                                     targets=[target])
    assert target in confirmed.replay.hits


@pytest.mark.parametrize("depth", [2, 100])
def test_atomic_production_command_searches_both_directions_and_scores(tmp_path, record_property, depth):
    import json
    from time import monotonic

    from behave.sim.behavior.catalogue import source_frontend_catalogue
    from behave.sim.behavior.extract import source_model

    started = monotonic()
    path = tmp_path / "behavior.py"
    path.write_bytes(SOURCE)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {"behavior.py": SOURCE}, config={"class": "M"})
    base = _contract()
    contract = api.TaskContract([
        endpoint if endpoint.direction == api.Direction.IN else api.Endpoint(
            endpoint.name, endpoint.direction, endpoint.fields, endpoint.adapter,
            endpoint.clock, kind=endpoint.kind, answers_per_transaction=1)
        for endpoint in base.endpoints], base.clocks, protocol_instances=[
            api.ProtocolInstance("a", ("a_in", "a_out")),
            api.ProtocolInstance("b", ("b_in", "b_out"))])
    task = api.admit(contract, api.realization_of_behavior_source(
        SOURCE.decode(), contract, file=path.name), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"),
                                  api.GenerationSpec(3, 2, 0, 0), bounded_analysis=False)
    providers = handle.generation_providers(reference, workdir=tmp_path / "guidance")
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference, providers=providers,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=depth),
        search_policy=api.SearchPolicy(time_budget_s=60)))
    record_property("command_seconds", monotonic() - started)
    record_property("search", json.dumps([{
        "generated": len(search.generated),
        "probes": search.telemetry["probes"],
        "outcomes": dict(search.telemetry["outcomes"]),
        "termination": search.telemetry["termination"],
        "coverage": thaw_record_value(search.telemetry.get("coverage")),
        "baseline_coverage": thaw_record_value(search.telemetry.get("baseline_coverage")),
        "search_budget": thaw_record_value(search.telemetry.get("search_budget")),
    } for search in result.generated.searches]))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative, [failure.as_dict() for _, failure in result.evaluation.failures]
    assert result.decision.passed, result.as_dict()
    assert result.evaluation.complete
    assert result.analysis is None
    assert len(result.generated.searches) == 2
    assert len(result.generated.baseline) == 1
    assert len(result.generated.stimuli) == 2  # Identical discoveries share one replay input.
    source = SOURCE.decode()
    declared = source_frontend_catalogue(source_model(source), source)
    # Source-owned declarations retain field-read safety obligations even
    # when construction discharges them. Those failures must not become hits;
    # the separate exact-query test proves their bounded UNSAT result.
    safety = {goal for goal, declaration in declared.items()
              if declaration.cls == "safety"}
    assert len(safety) == 2
    assert all(declared[goal].family == "attributeerror" for goal in safety)
    for search in result.generated.searches:
        assert search.generated, json.dumps(thaw_record_value(search.telemetry["answers"]))
        summary = search.as_dict()["telemetry"]
        assert len(summary["baseline_coverage"]["hits"]) == 6
        assert len(summary["coverage"]["hits"]) == 8
        assert set(summary["coverage"]["remaining"]) == safety
