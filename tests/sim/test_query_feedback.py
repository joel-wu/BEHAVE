"""Replay scheduling feedback, with unchanged final independent audits."""

import pytest

from behave.sim.behavior.instance import lower
from behave.sim.core.audit import AuditCheck, WitnessInconsistent
from behave.sim.core.catalogue import catalogue_of
from behave.sim.core.closure import _audit_query_transcript, close_over
from behave.sim.core.instance import BoundedInstance
from behave.sim.core.query import QueryCall, SolverAnswer
from behave.sim.core.query_cache import clear_analysis_answer_cache
from behave.sim.core.query_feedback import QueryFeedback
from behave.sim.core.query_telemetry import AnalysisCacheBinding, CacheDisposition
from behave.sim.core.refusal import Refused
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

pytestmark = [pytest.mark.solver, pytest.mark.isolation]

SOURCE = '''
class M:
    def reset(self):
        pass
    def process(self, a, b):
        y = 0
        if a == 0:
            y = 1
        if b == 0:
            y = 2
        return {"y": y}
'''


def _bound(source=SOURCE, steps=1):
    schema = TransactionSchema(
        [Endpoint("a", "input", 2), Endpoint("b", "input", 2),
         Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    return lower(source, schema, bound=steps).bind(steps)


def _summary(result):
    return {goal: result.of(goal).status for goal in result.catalogue}


@pytest.mark.parametrize("steps", [1, 4])
def test_nonempty_pool_cross_cone_hits_avoid_redundant_physical_queries(steps):
    instance = _bound(steps=steps)
    pool = [[{"a": 1, "b": 1} for _ in range(steps)]]
    assert len(instance.replay(pool[0]).hits) == 6
    result = close_over(instance, pool=pool, max_queries=1, workers=1)
    assert sum(call.solver_calls for call in result.queries) == 1
    assert all(result.of(goal).status.value == "reached" for goal in instance.goals())
    assert all(len(call.decoded_request) == steps for call in result.queries)

    legacy = _bound(steps=steps)
    legacy.instance.query_with_feedback = None
    before = close_over(legacy, pool=pool, max_queries=32, workers=1)
    # SAT model choice may alter the old schedule. Test the redundant work,
    # not a particular solver's choice of unconstrained input values.
    assert sum(call.solver_calls for call in before.queries) > 1
    credit = set(legacy.replay(pool[0]).hits)
    redundant = set()
    for call in before.queries:
        redundant.update(set(call.goals) & credit)
        if call.answer is SolverAnswer.SAT:
            credit.update(legacy.replay(call.decoded_request).hits)
    assert redundant
    assert _summary(result) == _summary(before)


def test_stateful_queries_keep_the_full_horizon():
    source = SOURCE.replace("pass", "self.n = 0").replace(
        "y = 0", "self.n += 1\n        y = 0").replace(
        "a == 0", "self.n == 2 and a == 0")
    instance = _bound(source, steps=2)
    result = close_over(instance, pool=[[{"a": 1, "b": 1}] * 2], workers=1)
    for call in result.queries:
        if call.answer is SolverAnswer.SAT:
            assert len(call.decoded_request) == 2
            assert set(call.goals) & instance.replay(call.decoded_request).hits
    assert result.query_transcript_complete


def test_partial_failure_hits_survive_stateless_witness_lifting():
    source = SOURCE.replace('return {"y": y}', 'return {"y": 1 // a}')
    instance = _bound(source, steps=4)
    result = close_over(instance, pool=[[{"a": 1, "b": 1}] * 4], workers=1)
    safety = [g for g in instance.goals() if g.startswith("safety:")]
    assert safety
    assert any(result.of(g).status.value == "reached" for g in safety)
    for call in result.queries:
        if call.answer is SolverAnswer.SAT:
            assert len(call.decoded_request) == 4
            assert not instance.replay(call.decoded_request).broken


def _feedback(instance):
    catalogue = catalogue_of(instance.goals(), allow_legacy=False)
    return QueryFeedback(instance, tuple(catalogue), catalogue, instance.audit_plan())


def test_duplicate_replays_are_provisional_and_final_audits_remain_batched(monkeypatch):
    instance = _bound()
    feedback = _feedback(instance)
    trace = [{"a": 0, "b": 0}]
    goals = sorted(instance.replay(trace).hits)[:2]
    counts = {"audit": 0, "source_audit": 0}
    for name in counts:
        original = getattr(instance.instance, name)

        def counted(traces, _name=name, _original=original):
            counts[_name] += 1
            return _original(traces)

        monkeypatch.setattr(instance.instance, name, counted)
    calls = [QueryCall((goal,), SolverAnswer.SAT, witness=trace) for goal in goals]
    for call in calls:
        assert feedback.checkpoint(call).hits >= set(goals)
    assert counts == {"audit": 0, "source_audit": 0}
    feedback.finish(calls)
    _records, sat, _, _ = _audit_query_transcript(
        instance, calls, instance.goals(), set(instance.goals()), instance.audit_plan())
    feedback.check_audits(sat)
    # One provider reading plus one independent core reading, not per call.
    assert counts == {"audit": 2, "source_audit": 2}
    feedback.close()
    with pytest.raises(ValueError, match="closed"):
        feedback.checkpoint(calls[0])


@pytest.mark.parametrize("refused", [False, True])
def test_final_audit_fault_withholds_the_replay_pruned_result(monkeypatch, refused):
    instance = _bound()
    check = (AuditCheck.refused("test-source-unavailable", "unavailable") if refused
             else AuditCheck.inconsistent("deliberate source disagreement"))
    monkeypatch.setattr(instance.instance, "source_audit", lambda traces: check)
    calls = []
    original = QueryFeedback.checkpoint

    def observed(self, call):
        calls.append(call)
        return original(self, call)

    monkeypatch.setattr(QueryFeedback, "checkpoint", observed)
    with pytest.raises(Refused if refused else WitnessInconsistent):
        close_over(instance, pool=[[{"a": 1, "b": 1}]], workers=1)
    assert len(calls) == 1


def test_feedback_cannot_drop_a_call_or_change_bound_identity(monkeypatch):
    instance = _bound()
    feedback = _feedback(instance)
    trace = [{"a": 0, "b": 0}]
    goal = min(instance.replay(trace).hits)
    call = QueryCall((goal,), SolverAnswer.SAT, witness=trace)
    feedback.checkpoint(call)
    with pytest.raises(WitnessInconsistent, match="omitted or changed"):
        feedback.finish(())
    with pytest.raises(ValueError, match="another bound query"):
        feedback.check(_bound(), instance.goals())
    monkeypatch.setattr(instance.instance, "analysis_identity", lambda: "changed")
    with pytest.raises(WitnessInconsistent, match="changed during"):
        feedback.checkpoint(call)


def test_feedback_never_accepts_an_unasked_goal():
    instance = _bound()
    catalogue = catalogue_of(instance.goals(), allow_legacy=False)
    hits = sorted(instance.replay([{"a": 0, "b": 0}]).hits)
    feedback = QueryFeedback(instance, hits[:1], catalogue, instance.audit_plan())
    call = QueryCall(hits[1:2], SolverAnswer.SAT, witness=[{"a": 0, "b": 0}])
    with pytest.raises(Refused, match="outside"):
        feedback.checkpoint(call)


def test_cold_cache_decoration_preserves_feedback_and_warm_hit_reaudits(monkeypatch):
    clear_analysis_answer_cache()
    raw = _bound()
    binding = AnalysisCacheBinding(
        semantic_instance="feedback-cache-test", backend="test-z3",
        backend_version="test/1", tool="z3", tool_version="test")
    instance = BoundedInstance(raw.instance, raw.domain, analysis_cache=binding)
    cold = close_over(instance, pool=[[{"a": 1, "b": 1}]], workers=1)
    assert len(cold.queries) == 1
    assert cold.queries[0].telemetry.cache is CacheDisposition.MISS
    assert cold.queries[0].solver_calls == 1

    def no_solver(*args, **kwargs):
        pytest.fail("a warm decisive answer should not invoke the solver")

    monkeypatch.setattr(instance.instance, "query", no_solver)
    warm = close_over(instance, pool=[[{"a": 1, "b": 1}]], workers=1)
    assert _summary(cold) == _summary(warm)
    assert warm.queries[0].telemetry.cache is CacheDisposition.HIT
    assert warm.queries[0].solver_calls == 0
    monkeypatch.setattr(instance.instance, "source_audit", lambda traces:
                        AuditCheck.inconsistent("changed source reading"))
    with pytest.raises(WitnessInconsistent):
        close_over(instance, pool=[[{"a": 1, "b": 1}]], workers=1)
    clear_analysis_answer_cache()


def test_feedback_cannot_relabel_a_trace_or_change_source(monkeypatch):
    instance = _bound()
    feedback = _feedback(instance)
    trace = [{"a": 0, "b": 0}]
    goal = min(instance.replay(trace).hits)
    call = QueryCall((goal,), SolverAnswer.SAT, witness=trace)
    feedback.checkpoint(call)
    with pytest.raises(WitnessInconsistent, match="omitted or changed"):
        feedback.finish((QueryCall((goal,), SolverAnswer.SAT,
                                   witness=[{"a": 1, "b": 1}]),))
    with pytest.raises(WitnessInconsistent, match="omitted or changed"):
        feedback.finish((QueryCall((goal,), SolverAnswer.SAT, witness=trace,
                                   solver_calls=0),))
    monkeypatch.setattr(instance.instance, "source", SOURCE + "\n# changed\n")
    with pytest.raises(WitnessInconsistent, match="changed during"):
        feedback.checkpoint(call)


def test_no_provisional_reading_can_escape_its_final_audit():
    from behave.sim.core.audit import AuditedWitness

    instance = _bound()
    feedback = _feedback(instance)
    trace = [{"a": 0, "b": 0}]
    goal = min(instance.replay(trace).hits)
    call = QueryCall((goal,), SolverAnswer.SAT, witness=trace)
    feedback.checkpoint(call)
    with pytest.raises(WitnessInconsistent, match="no final audited witness"):
        feedback.check_audits(())
    honest = instance.audit_request(trace)
    wrong = AuditedWitness(
        domain=honest.domain, states=honest.states, inputs=honest.inputs,
        outputs=honest.outputs, hit_sets=[set()], terminal_error=honest.terminal_error)
    with pytest.raises(WitnessInconsistent, match="final independent audit disagree"):
        feedback.check_audits(((call, wrong, wrong.hits),))
    feedback.check_audits(((call, honest, honest.hits),))


@pytest.mark.parametrize("stateful", [False, True])
def test_occurrence_worklist_feedback_matches_legacy_closure(monkeypatch, stateful):
    from behave.sim.behavior import residual

    source = SOURCE
    if stateful:
        source = source.replace("pass", "self.n = 0").replace(
            "y = 0", "self.n += 1\n        y = 0").replace(
            "a == 0", "self.n == 2 and a == 0")
    # Exercise the same exact occurrence/group machinery used by large bodies,
    # without inflating a regression's solver problem just to pass a size gate.
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    observed = []
    original = residual._decide_occurrence_goals

    def checked(*args, **kwargs):
        observed.append(kwargs.get("_progress") is not None)
        return original(*args, **kwargs)

    monkeypatch.setattr(residual, "_decide_occurrence_goals", checked)
    pool = [[{"a": 1, "b": 1}] * 2]
    current = _bound(source, steps=2)
    got = close_over(current, pool=pool, workers=1)
    legacy = _bound(source, steps=2)
    legacy.instance.query_with_feedback = None
    before = close_over(legacy, pool=pool, workers=1)
    assert observed == [True, False]
    assert _summary(got) == _summary(before)
    assert got.query_transcript_complete


def test_feedback_budget_exhaustion_cannot_manufacture_unsat():
    instance = _bound()
    result = close_over(instance, artifact_ms=0, workers=1)
    assert {result.of(goal).status.value for goal in instance.goals()} == {"unresolved"}
    assert all(call.answer is SolverAnswer.UNKNOWN and call.solver_calls == 0
               for call in result.queries)
    assert {call.goals[0] for call in result.queries} == set(instance.goals())


def test_cross_cone_credit_cannot_hide_an_unsat_contradiction():
    from behave.sim.behavior.query_progress import QueryProgress

    instance = _bound()
    trace = [{"a": 0, "b": 0}]
    goals = sorted(instance.replay(trace).hits)
    progress = QueryProgress(_feedback(instance))
    progress.observe(QueryCall(goals[:1], SolverAnswer.SAT, witness=trace))
    with pytest.raises(WitnessInconsistent, match="contradicts"):
        progress.merge({}, {goals[-1]}, {}, goals)
