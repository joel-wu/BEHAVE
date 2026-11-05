"""Source query preparation cannot silently switch the admitted program."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from behave.sim.core.refusal import Refused
from behave.sim.rtl.elaboration import source_documents
from behave.sim.rtl.source_generation import SourceGenerationSession


def test_changed_constants_do_not_share_the_baseline_query_context(tmp_path, monkeypatch):
    original = "module M(input a,output y); assign y=a==0; endmodule"
    path = tmp_path / "design.sv"
    path.write_text(original.replace("a==0", "a==1"))
    artifact = SimpleNamespace(rtl_path=path, top_module="M")

    def no_compiler(*args, **kwargs):
        pytest.fail("source identity must be bound before launching the compiler")

    monkeypatch.setattr("behave.sim.rtl.tool_isolation.run_workspace", no_compiler)
    identity = hashlib.sha256(("M\0" + original).encode()).hexdigest()
    with pytest.raises(Refused, match="rtl-source-query-artifact-changed"):
        source_documents(artifact, expected_identity=identity)


def test_native_query_metadata_crosses_the_shared_json_transport_once():
    from behave.sim import api
    from behave.sim.rtl.source_boundary_capture import VERSION

    seed = api.Stimulus({"in": [(3,)]}, [api.EventSlot(offers=("in",))])
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(accepted={"in": (3,)})],
        query_boundaries={"version": VERSION, "fields": [["x", 8]], "samples": [[0, [19]]]})
    calls = []
    session = SimpleNamespace(_binding=None, _prepare_timeout=17, _query_timeout=1,
        _exchange=lambda op, payload, timeout: calls.append((op, json.loads(json.dumps(payload)), timeout)))
    SourceGenerationSession.bind(session, seed, trace)
    SourceGenerationSession.bind(session, seed, trace)
    assert len(calls) == 1
    assert calls[0][1]["query_boundaries"]["samples"] == [[0, [19]]]
    assert calls[0][2] == 17  # trace binding is preparation, not a solver quantum


def test_transition_preparation_uses_its_own_phase_once_without_a_new_wall_budget():
    calls = []
    session = SimpleNamespace(_prepared=False, _prepare_timeout=17,
        _exchange=lambda *args: calls.append(args))
    SourceGenerationSession.prepare(session)
    SourceGenerationSession.prepare(session)
    assert calls == [("prepare", {}, 17)]


def test_failed_transition_preparation_is_not_published():
    def failed_exchange(*_args):
        raise RuntimeError("cancelled preparation")

    session = SimpleNamespace(_prepared=False, _prepare_timeout=17,
                              _exchange=failed_exchange)
    with pytest.raises(RuntimeError, match="cancelled preparation"):
        SourceGenerationSession.prepare(session)
    assert not session._prepared


@pytest.mark.parametrize("kind", ["capability", "width", "range", "resource"])
def test_source_preparation_preserves_the_encoding_refusal(monkeypatch, kind):
    from behave.sim.symbolic.encoder import EncodingError
    from behave.sim.engines.rtl import source_session

    def unavailable(*_args, **_kwargs):
        raise EncodingError("the retained scalar exceeds its declared carrier", kind=kind)

    monkeypatch.setattr(source_session, "SourceIntervalTemplate", unavailable)
    worker = SimpleNamespace(machine=SimpleNamespace(procedure=object()), env={}, template=None,
                             _validation=None)
    with pytest.raises(Refused) as failed:
        source_session.PreparedSourceGeneration.prepare(worker)
    refusal = failed.value.refusal
    assert refusal.kind == "rtl-source-query-encoding-unsupported"
    assert refusal.detail["encoding_kind"] == kind
    assert refusal.detail["detail"] == "the retained scalar exceeds its declared carrier"
    assert worker.template is None


def test_split_preparation_keeps_the_session_wall_envelope(monkeypatch):
    from behave.sim.job_session import JobSession

    calls = []
    monkeypatch.setattr(JobSession, "__init__",
        lambda self, *args, **kwargs: calls.append(kwargs))
    SourceGenerationSession({}, prepare_timeout_s=17, query_timeout_s=3,
                            probe_budget=2, timings=[])
    assert calls[0]["wall_s"] == 17 * (2 + 2) + 3 * 2  # open, prepare, binds, probes
    assert calls[0]["exchanges"] == 2 * 2 + 2  # open, prepare, then bind/probe


def test_time_budgeted_source_sessions_do_not_restart_the_shared_budget(monkeypatch):
    from behave.sim import search_policy
    from behave.sim.job_session import JobSession
    from behave.sim.scoring_policy import ScoringPolicy, scoring_scope

    now, calls = [100.0], []
    monkeypatch.setattr(search_policy.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(JobSession, "__init__",
        lambda self, *args, **kwargs: calls.append(kwargs))
    with scoring_scope(ScoringPolicy(time_budget_s=10)):
        budget = search_policy.SearchBudget(search_policy.SearchPolicy(time_budget_s=12))
        with search_policy.search_scope(budget.direction(2)):
            SourceGenerationSession({}, prepare_timeout_s=17, query_timeout_s=3,
                                    probe_budget=None, timings=[])
            # Two directions share the tighter ten-second scoring lifetime.
            assert calls[-1]["wall_s"] == 5
            assert calls[-1]["exchanges"] is None
            now[0] += 4
            assert search_policy.remaining_timeout(17) == 1
        with search_policy.search_scope(budget.direction(1)):
            SourceGenerationSession({}, prepare_timeout_s=17, query_timeout_s=3,
                                    probe_budget=None, timings=[])
            # Eight seconds remain in search, but only six in the total score.
            assert calls[-1]["wall_s"] == 6


@pytest.mark.eda("slang")
@pytest.mark.solver
def test_validated_literal_facts_precede_transition_construction(tmp_path, monkeypatch):
    from behave.sim.symbolic import ranges
    from behave.sim.engines.rtl import source_session
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_goals import manifest_of
    from behave.sim.rtl.source_plan import plan_source

    from .test_api_path import contract
    from .test_rtl_observation_frontend import _installed

    source = """module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) if(1'b0) q<=7; else q<=a;
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast,
        sensitivity_document=sensitivity, enumerate_iterations=False,
        filename="design.sv")
    catalogue = manifest_of(plan.declarations, filename="design.sv")
    calls = []
    original = source_session.SourceIntervalTemplate

    def construct(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(source_session, "SourceIntervalTemplate", construct)

    def unrelated_invariant(*args, **kwargs):
        pytest.fail('a pinned RTL query re-proved declared storage carriers by fixpoint')

    monkeypatch.setattr(ranges, 'fixpoint', unrelated_invariant)
    worker = source_session.PreparedSourceGeneration({"source": source,
        "cst": cst, "ast": ast, "sensitivity": sensitivity,
        "contract": contract().as_dict(), "catalogue": catalogue.as_dict()})
    assert calls == [] and worker.template is None
    assert worker.env == {**worker.machine.procedure.state_ranges, **worker.machine.input_ranges}
    assert worker.constant_false_goals
    assert set(worker.constant_false_goals) < set(catalogue)
    worker.prepare()
    assert calls == [True] and worker.template is not None
    assert worker.template._validation is worker._validation
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    assert type(worker.template.direct) is NativeCoverSourceCompiler
    assert worker.catalogue == catalogue


def test_source_templates_reuse_only_their_owned_validation(monkeypatch):
    from behave.sim.symbolic import ir, step_template
    from behave.sim.symbolic.validation import _procedure_validation
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    proc = ir.Procedure((), (), (), (), ir.Mark('goal'))
    validation = _procedure_validation(proc, compact_arrays=True)
    def no_second_validation(procedure, **kwargs):
        if procedure is proc:
            pytest.fail('the same owned IR was fully validated again')
        return _procedure_validation(procedure, **kwargs)
    monkeypatch.setattr(step_template, '_procedure_validation', no_second_validation)
    owner = SourceIntervalTemplate(proc, {}, _validation=validation)
    sibling = owner.normalized()
    assert sibling._validation is validation
    assert sibling.observations(('goal',)).hit_names == ('goal',)
    with pytest.raises(TypeError, match='validation'):
        SourceIntervalTemplate(ir.Procedure((), (), (), (), ir.Mark('goal')), {},
                               _validation=validation)


@pytest.mark.parametrize('kind', ['rtl-direct-event-regions-unsupported', 'unexpected-defect'])
def test_source_compiler_fallback_is_capability_specific(monkeypatch, kind):
    from behave.sim.core.refusal import Refusal
    from behave.sim.engines.rtl import source_session
    from behave.sim.rtl import source_direct
    from behave.sim.symbolic import ir
    from behave.sim.symbolic.validation import _procedure_validation

    proc = ir.Procedure((), (), (), (), ir.Mark('goal'))
    worker = object.__new__(source_session.PreparedSourceGeneration)
    worker.machine, worker.env, worker.template = SimpleNamespace(procedure=proc), {}, None
    worker._validation = _procedure_validation(proc, compact_arrays=True)
    def unavailable(machine):
        raise Refused(Refusal(kind, 'test compiler boundary'))
    monkeypatch.setattr(source_direct, 'NativeCoverSourceCompiler', unavailable)
    if kind == 'unexpected-defect':
        with pytest.raises(Refused, match=kind):
            worker.prepare()
        assert worker.template is None
    else:
        worker.prepare()
        assert worker.template.direct is None
        assert worker.template._validation is worker._validation
        assert worker.template.observations(('goal',)).hit_names == ('goal',)


@pytest.mark.eda('slang')
@pytest.mark.solver
def test_source_query_context_does_not_share_compiler_permissions(tmp_path):
    import z3
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from .test_rtl_source_scheduler import _lower

    machine, _ = _lower(tmp_path, '''module M(input clk,input [2:0] a,output reg [2:0] q);
        always @(posedge clk) if(a==3) q<=a; else q<=0; endmodule''', defer_domains=True)
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    owner = SourceIntervalTemplate(machine.procedure, env, direct=NativeCoverSourceCompiler(machine))
    selected = owner.relation(machine, tuple({goal for _, _, goal in machine.occurrences}))
    selected.direct._prefix_scope = True
    selected.direct._context_effects['sentinel'] = object()
    first, second = selected.translate(z3.Context()), selected.translate(z3.Context())
    assert first.direct is not second.direct and first.direct is not selected.direct
    assert not first.direct._prefix_scope and not second.direct._prefix_scope
    assert first.direct._context_effects == second.direct._context_effects == {}
    assert selected.direct._prefix_scope and 'sentinel' in selected.direct._context_effects


def test_same_seed_with_changed_native_trace_rebinds_the_source_boundary():
    from behave.sim import api
    from behave.sim.rtl.source_boundary_capture import VERSION

    seed = api.Stimulus({"in": [(3,)]}, [api.EventSlot(offers=("in",))])
    traces = [api.AuditedTrace(seed, records=[api.SlotRecord(accepted={"in": (3,)})],
        query_boundaries={"version": VERSION, "fields": [["x", 8]], "samples": [[0, [value]]]})
        for value in (19, 23)]
    calls = []
    session = SimpleNamespace(_binding=None, _prepare_timeout=17, _query_timeout=1,
        _exchange=lambda op, payload, timeout: calls.append(payload))
    for trace in (traces[0], traces[0], traces[1], traces[0]):
        SourceGenerationSession.bind(session, seed, trace)
    assert [row["query_boundaries"]["samples"] for row in calls] == [
        [[0, [19]]], [[0, [23]]], [[0, [19]]]]


def test_failed_source_replacement_does_not_retain_the_old_binding():
    from behave.sim import api
    from behave.sim.engines.rtl.source_session import PreparedSourceGeneration

    seed = api.Stimulus({"in": [(3,)]}, [api.EventSlot(offers=("in",))])
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(accepted={"in": (3,)})])

    def failed_exchange(*_args):
        raise RuntimeError("cancelled binding")

    session = SimpleNamespace(_binding=(seed, trace), _prepare_timeout=17, _query_timeout=1,
                              _exchange=failed_exchange)
    replacement = api.AuditedTrace(seed, records=trace.records, initial_state={"q": 1})
    with pytest.raises(RuntimeError, match="cancelled binding"):
        SourceGenerationSession.bind(session, seed, replacement)
    assert session._binding is None

    worker = object.__new__(PreparedSourceGeneration)
    worker.binding = object()
    worker._interval_progress = object()
    with pytest.raises(KeyError, match="initial_state"):
        worker.bind({"seed": {}})
    assert worker.binding is None
    assert worker._interval_progress is None


@pytest.mark.parametrize('mode', ['ready', 'budget-exhausted', 'missing-goal'])
@pytest.mark.parametrize('answer', ['sat', 'local-unsat', 'unknown'])
def test_projection_and_boundary_binding_share_the_probe_deadline(monkeypatch, mode, answer):
    from behave.sim import api
    from behave.sim.engines import probing
    from behave.sim.engines.rtl import source_session

    digest, clock, calls, built = 'a' * 64, [0.0], [], []
    request = SimpleNamespace(catalogue_digest=digest, targets=('goal',), mutable_fields=('field',),
                              identity_bytes=b'question')
    monkeypatch.setattr(source_session.ProbeRequest, 'from_dict', lambda _: request)
    monkeypatch.setattr(source_session.time, 'perf_counter', lambda: clock[0])
    relation = SimpleNamespace(source=SimpleNamespace(state_names=('live',)))

    def select(machine, targets):
        assert targets == request.targets
        built.append('relation')
        clock[0] += 0.01
        if mode == 'missing-goal':
            raise ValueError('a requested source goal has no encoded occurrence')
        return relation

    def bind(fields, *, state_names):
        assert fields == request.mutable_fields and state_names == ('live',)
        built.append('boundary')
        clock[0] += 0.2 if mode == 'budget-exhausted' else 0.02
        return SimpleNamespace(state={}, phases=({},), mutable_inputs=(),
            proposal=lambda values: api.Stimulus({'in': [(values['a'],)]}))

    def solve(*args, **kwargs):
        assert kwargs['_relation'] is relation
        assert (kwargs['timeout_ms'] == 100 if mode == 'budget-exhausted'
                else 69 <= kwargs['timeout_ms'] <= 70)
        clock[0] += 0.04
        calls.append(True)
        return probing.ProbeResult(probing.Outcome(answer), probing.Scope.RTL_LOCAL_HIT,
            goal='goal' if answer == 'sat' else None,
            candidate={'a': 7} if answer == 'sat' else None)

    monkeypatch.setattr(source_session, 'query_source_interval', solve)
    worker = SimpleNamespace(machine=SimpleNamespace(event_bound=3), env={},
        catalogue=SimpleNamespace(digest=lambda: digest), template=SimpleNamespace(relation=select),
        binding=SimpleNamespace(at=bind), _interval_progress=None)
    result = source_session.PreparedSourceGeneration.probe(worker, {'request': {}, 'timeout_ms': 100})
    assert result['outcome'] == {'ready': answer, 'budget-exhausted': 'constructing',
                                  'missing-goal': 'unsupported'}[mode]
    assert result['solve_ms'] == pytest.approx(clock[0] * 1000)
    assert calls == ([True] if mode == 'ready' else [])
    if mode == 'budget-exhausted':
        assert worker._interval_progress is not None
        result = source_session.PreparedSourceGeneration.probe(worker, {'request': {}, 'timeout_ms': 100})
        assert result['outcome'] == answer  # Real UNKNOWN stays terminal, not CONSTRUCTING.
        assert calls == [True] and built == ['relation', 'boundary']
        assert result['solve_ms'] == pytest.approx(40)
    assert worker._interval_progress is None
    if answer == 'sat' and mode != 'missing-goal':
        assert api.Stimulus.from_dict(result['candidate']).queues == (('in', ((7,),)),)


@pytest.mark.parametrize('change', ['goal', 'field', 'occurrence', 'segment', 'bind', 'prepare'])
def test_prepared_interval_cannot_cross_a_changed_question_or_binding(monkeypatch, change):
    from behave.sim import api
    from behave.sim.engines.rtl import source_session
    from behave.sim.engines.rtl.generation import RTLScheduleSegment
    from behave.sim.generation import ProbeRequest, StimulusField
    from behave.sim.core.catalogue import CatalogueManifest, Goal
    from behave.sim.rtl.source_goals import OWNER

    catalogue = CatalogueManifest(tuple(Goal('rtl-source:' + name, 'coverage',
        family='control-choice', provider=OWNER, event='T', policy='tests/source/1')
        for name in ('goal', 'other')))
    digest, clock, built = catalogue.digest(), [0.0], []
    field = StimulusField('in', 0, 0, 0)
    request = ProbeRequest(('rtl-source:goal',), 'first', (field,), catalogue_digest=digest)
    segment = RTLScheduleSegment('first', 'c' * 64, 0, 1, 0, (field,))

    def select(machine, targets):
        built.append(tuple(targets))
        clock[0] += 0.2
        return SimpleNamespace(source=SimpleNamespace(state_names=()))

    template = SimpleNamespace(relation=select)
    binding = SimpleNamespace(at=lambda *args, **kwargs:
        SimpleNamespace(state={}, phases=({},), mutable_inputs=()))
    worker = object.__new__(source_session.PreparedSourceGeneration)
    worker.machine, worker.env = SimpleNamespace(procedure=object(), event_bound=3), {}
    worker.catalogue = catalogue
    worker.contract, worker.template, worker.binding = SimpleNamespace(clocks=()), template, binding
    worker._interval_progress = None
    monkeypatch.setattr(source_session.time, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(source_session, 'query_source_interval',
        lambda *args, **kwargs: pytest.fail('a changed question must build its own boundary'))

    def probe():
        return worker.probe(dict(request=request.as_dict(), segment=segment.as_dict(), timeout_ms=100))

    assert probe()['outcome'] == 'constructing'
    if change in ('goal', 'field', 'occurrence'):
        request = ProbeRequest(('rtl-source:other',) if change == 'goal' else request.targets,
            'second' if change == 'occurrence' else request.occurrence,
            (StimulusField('in', 0, 1, 0),) if change == 'field' else request.mutable_fields,
            catalogue_digest=digest)
    elif change == 'segment':
        segment = RTLScheduleSegment('later', segment.seed, 1, 2, 0, (field,))
    elif change == 'bind':
        monkeypatch.setattr(source_session, 'SourceTraceBindings', lambda *args: binding)
        worker.bind(dict(seed=api.Stimulus({'in': [(0,)]}).as_dict(), initial_state={}, records=[]))
        assert worker._interval_progress is None
    else:
        from behave.sim.rtl import source_direct

        worker._validation = None
        template.normalized = lambda: template
        monkeypatch.setattr(source_session, 'SourceIntervalTemplate', lambda *args, **kwargs: template)
        monkeypatch.setattr(source_direct, 'NativeCoverSourceCompiler', lambda machine: None)
        worker.prepare()
        assert worker._interval_progress is None
    assert probe()['outcome'] == 'constructing'
    assert len(built) == 2


def test_literal_false_filter_requires_every_lowered_occurrence():
    from behave.sim.symbolic import ir
    from behave.sim.engines.rtl.source_session import _constant_false_source_goals

    proc = ir.Procedure(("s",), (), (), (), ir._sequence([
        ir.If(ir.Lit(False), ir.Mark("blocked"), ir.Skip()),
        ir.If(ir.Lit(False), ir.Mark("alias_blocked"), ir.Mark("alias_live")),
        ir.If(ir.Var("s"), ir.Mark("state_dependent"), ir.Skip()),
    ]), initial={"s": 0})
    machine = SimpleNamespace(procedure=proc, occurrences=(
        ("blocked", (), "dead"), ("alias_blocked", (), "aliased"),
        ("alias_live", (), "aliased"), ("state_dependent", (), "state")))
    assert _constant_false_source_goals(machine) == ("dead",)
    # Initial state and native/local-query outcomes are not proof premises.
    proc.initial["s"] = 1
    assert _constant_false_source_goals(machine) == ("dead",)


def test_static_goal_exclusions_survive_cleanup_but_not_the_next_search():
    from behave.sim.engines.rtl.generation import _IsolatedRTLGeneration

    closed = []
    service = object.__new__(_IsolatedRTLGeneration)
    service._source_session = SimpleNamespace(close=lambda: closed.append("source"))
    service._source_constant_false = frozenset({"dead-z", "dead-a"})
    service._session_timings = [{"op": "open"}]
    service._search_active = True
    service._source_traces = {"seed": object()}
    service._schedule_queries = {"seed": object()}
    released = []
    service._context = SimpleNamespace(replay=SimpleNamespace(clear=lambda: released.append(True)))
    service.end_search()
    # The common scheduler asks for diagnostics after leaving search_scope.
    receipt = service.execution_cost()
    assert closed == ["source"] and service._source_session is None
    assert released == [True] and not service._source_traces and not service._schedule_queries
    assert receipt["constant_false_goals"] == ["dead-a", "dead-z"]
    assert receipt["operations"] == [{"op": "open"}]
    service.begin_search(None)
    assert service.execution_cost()["constant_false_goals"] == []
    assert service.execution_cost()["operations"] == []
    assert receipt["constant_false_goals"] == ["dead-a", "dead-z"]
    service.end_search()


def test_literal_false_requests_skip_transport_but_local_unsat_is_not_remembered(monkeypatch):
    from behave.sim import api
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.generation import _IsolatedRTLGeneration
    from behave.sim.engines.rtl.generation_job import encode_probe_reply
    from behave.sim.generation import stimulus_bytes

    digest = "a" * 64
    seed = api.Stimulus({"in": [(3,)]}, [api.EventSlot(offers=("in",))])
    calls = []

    def probe(request, _timeout, *, segment):
        assert segment is None
        calls.append(request.targets)
        return encode_probe_reply(probing.ProbeResult(
            probing.Outcome.LOCAL_UNSAT, probing.Scope.RTL_LOCAL_HIT), digest)

    prepared = []
    session = SimpleNamespace(opened={"constant_false_goals": ["dead"]},
        prepare=lambda: prepared.append(True),
        bind=lambda *_args: None, probe=probe)
    monkeypatch.setattr("behave.sim.rtl.source_generation.SourceGenerationSession",
                        lambda *_args, **_kwargs: session)
    service = object.__new__(_IsolatedRTLGeneration)
    service._source_refusal = service._source_session = None
    service._source_payload = {}
    service._source_constant_false = frozenset()
    service._source_traces = {stimulus_bytes(seed): object()}
    service._context = SimpleNamespace(artifact=SimpleNamespace(timeout_s=1),
        complete_catalogue=SimpleNamespace(digest=lambda: digest))
    service._session_budget, service._session_timings, service._timeout_ms = None, [], 5000
    for _ in range(2):
        answer = service._probe_source(seed, SimpleNamespace(targets=("dead",)), None)
        assert answer.outcome is probing.Outcome.LOCAL_UNSAT
        assert answer.candidate is None and answer.goal is None
        assert "literal-false" in answer.detail
    assert calls == []
    assert prepared == []
    for _ in range(2):
        answer = service._probe_source(seed, SimpleNamespace(targets=("dynamic",)), None)
        assert answer.outcome is probing.Outcome.LOCAL_UNSAT
    assert calls == [("dynamic",), ("dynamic",)]
    assert prepared == [True, True]


@pytest.mark.parametrize("how", ["timeout", "crashed", "no-answer"])
def test_fatal_source_probe_is_closed_and_not_retried_as_solver_unknown(how):
    from behave.sim import api
    from behave.sim.engines.rtl.generation import _IsolatedRTLGeneration
    from behave.sim.generation import stimulus_bytes
    from behave.sim.job_isolation import IsolatedJobFailure

    seed = api.Stimulus({"in": [(3,)]}, [api.EventSlot(offers=("in",))])
    calls = []

    def fail(*_args, **_kwargs):
        calls.append("probe")
        raise IsolatedJobFailure(how, "original worker failure")

    service = object.__new__(_IsolatedRTLGeneration)
    service._source_refusal = None
    service._source_payload = {}
    service._source_constant_false = frozenset()
    service._source_traces = {stimulus_bytes(seed): object()}
    service._source_session = SimpleNamespace(prepare=lambda: None, bind=lambda *_: None,
        probe=fail, close=lambda: calls.append("close"))
    service._timeout_ms = 5000
    request = SimpleNamespace(targets=("dynamic",))
    for _ in range(2):
        with pytest.raises(Refused) as caught:
            service._probe_source(seed, request, None)
        suffix = "timeout" if how == "timeout" else "unavailable"
        assert caught.value.refusal.kind == f"rtl-generation-isolated-source-{suffix}"
        assert caught.value.refusal.detail["failure"] == how
    assert calls == ["probe", "close"]

