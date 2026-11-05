"""Production-order tests for the concrete-to-bounded handoff."""

from __future__ import annotations

import json

import pytest

from behave.sim import api
from behave.sim.core import analysis as analysis_mod
from behave.sim.core import closure as closure_mod
from behave.sim.core.audit import (
    AuditCheck,
    AuditedWitness,
    AuditedWitnessLimitError,
)
from behave.sim.core.audit_plan import AuditPlan
from behave.sim.core.catalogue import (
    COVERAGE,
    SAFETY,
    CatalogueManifest,
    DomainManifest,
    Goal,
)
from behave.sim.core.conditional_evidence import TrustedAssumption
from behave.sim.core.domain import TraceDomain
from behave.sim.core.instance import BoundedInstance, Feasible, Replay, StepOutput
from behave.sim.core.instance import Verdict as QueryVerdict
from behave.sim.core.outcome import AnalysisOutcome, ConcreteEvaluation, Verdict
from behave.sim.core.query import QueryCall, SolverAnswer
from behave.sim.core.refusal import Refusal
from behave.sim.core.run_record import RunResult
from behave.sim.core.stimulus import AuditedTrace, Completion, EventSlot, SlotRecord, Stimulus, trace_domain

GOAL = Goal("g", COVERAGE, family="test", provider="test",
            policy="analysis-test/1")


class _Task:
    """The minimum immutable authority fields ConcreteEvaluation serialises."""

    class _Contract:
        digest = "contract"

        @staticmethod
        def inputs():
            return ()

    class _Realization:
        identity = "artifact"

        @staticmethod
        def as_dict():
            return {"kind": "test", "identity": "artifact", "ports": [],
                    "channel_ports": {}, "source": ""}

    contract = _Contract()
    realization = _Realization()
    reference = "reference"

    @staticmethod
    def binds(_artifact):
        return True


def _result(label="raw", *, hits=(), case=None, completion=Completion.NORMAL):
    stimulus = Stimulus(queues={}, slots=[EventSlot(
        controls={} if case is None else {'case': case})], source=label)
    trace = AuditedTrace(
        stimulus, records=[SlotRecord(hits=hits)],
        domain=trace_domain(_Task.contract.digest, horizon=1),
        artifact=_Task.realization.identity, completion=completion)
    return RunResult.not_compared(
        stimulus, trace,
        api.RunFailure.verifier(
            "test-audit-view",
            api.Refusal("test-uncompared", "comparison is irrelevant here")))


def _evaluation(*results):
    hits = set().union(*(result.trace.goal_summary[1] for result in results if result.trace))
    return ConcreteEvaluation(_Task(), results=results, catalogue=[GOAL], hits=hits)


def _task_for_behavior(behavior, input_width=1):
    adapter_in = api.ready_valid("in", "in_valid", "in_ready")
    adapter_out = api.ready_valid("out", "out_valid", "out_ready")
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("a", api.FiniteType(input_width), "a")],
                adapter_in, clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("y", api.FiniteType(1), "y")],
                adapter_out, clock="clk", kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])
    realization = api.Realization(
        "behavior",
        (
            api.Port(
                "a", input_width, driven_by="environment", clock="clk"),
            api.Port("y", 1, driven_by="artifact", clock="clk"),
            api.Port("in_valid", 1, driven_by="environment"),
            api.Port("in_ready", 1, driven_by="artifact"),
            api.Port("out_valid", 1, driven_by="artifact"),
            api.Port("out_ready", 1, driven_by="environment"),
            api.Port("clk", 1, driven_by="environment"),
            api.Port("rst", 1, driven_by="environment"),
        ),
        channel_ports={"in": ("a",), "out": ("y",)},
        identity=behavior.identity)
    return api.admit(contract, realization)


class _Spy:
    def __init__(self, feasible=(Feasible.YES, ({"x": 0},)), *,
                 pool_hits=None, verdict=None):
        self.events = []
        self.feasibility = feasible
        self.pool_hits = dict(pool_hits or {})
        self.verdict = verdict or QueryVerdict(
            {}, {GOAL.id}, {},
            queries=(QueryCall((GOAL.id,), SolverAnswer.UNSAT),))
        self.feasible_calls = 0
        self.query_calls = 0
        self.identity = _Task.realization.identity

    def analysis_identity(self):
        return self.identity

    def evidence_identity(self, _steps):
        return {
            "representation": "test-representation/1",
            "encoding": "test-encoding/1",
        }

    def task_audit(self, _task):
        return AuditCheck.agreed()

    def goals(self):
        return {GOAL.id: GOAL}

    def catalogue_manifest(self):
        return CatalogueManifest(tuple(self.goals().values()))

    def well_formed(self):
        self.events.append("well-formed")
        return []

    def audit_plan(self):
        return AuditPlan.all()

    def unaccounted_goals(self):
        self.events.append("goal-audit")
        return []

    def feasible(self, steps, timeout_ms=None):
        self.feasible_calls += 1
        self.events.append("feasible")
        return self.feasibility

    def replay(self, trace):
        self.events.append("replay")
        key = tuple(tuple(sorted(dict(row).items())) for row in trace)
        return Replay(
            self.pool_hits.get(key, ()),
            outputs=[StepOutput() for _ in trace], state={}, steps=len(trace))

    def _witness(self, trace, domain, replayed=None):
        replayed = self.replay(trace) if replayed is None else replayed
        if type(replayed) is not Replay:
            return AuditCheck.refused(
                "witness-replay-invalid",
                "semantic replay returned something other than a Replay record")
        if replayed.broken:
            return AuditCheck.refused(
                "witness-replay-failed", str(replayed.broken))
        return AuditedWitness(
            domain=domain,
            states=tuple({} for _ in range(len(trace) + 1)),
            inputs=trace,
            outputs=tuple(replayed.outputs[index].transactions
                          for index in range(len(trace))),
            hit_sets=(set(replayed.hits), *(() for _ in trace[1:])),
            terminal_error=str(replayed.error or ""))

    def audit_request(self, trace, domain):
        replayed = self.replay(trace)
        if type(replayed) is not Replay:
            return AuditCheck.refused(
                "witness-replay-invalid",
                "semantic replay returned something other than a Replay record")
        if replayed.broken:
            return AuditCheck.refused(
                "witness-replay-failed", str(replayed.broken))
        for check in (self.audit((trace,)), self.source_audit((trace,))):
            if check.status.value != "agreed":
                return check
        return self._witness(trace, domain, replayed)

    def audit(self, traces):
        self.events.append("step-audit")
        return AuditCheck.agreed()

    def source_audit(self, traces):
        self.events.append("source-audit")
        return AuditCheck.agreed()

    def query(self, goals, steps, timeout_ms=None):
        self.query_calls += 1
        self.events.append("goal-query")
        if type(self.verdict) is not QueryVerdict:
            return self.verdict
        if self.verdict.queries is not None:
            return self.verdict
        calls = [QueryCall(
            (goal,), SolverAnswer.SAT, witness=[dict(row) for row in trace])
                 for goal, trace in self.verdict.reachable.items()]
        calls.extend(QueryCall((goal,), SolverAnswer.UNSAT)
                     for goal in sorted(self.verdict.unreachable))
        calls.extend(QueryCall((goal,), SolverAnswer.UNKNOWN, reason=why)
                     for goal, why in sorted(self.verdict.undecided.items()))
        return QueryVerdict(
            self.verdict.reachable, self.verdict.unreachable,
            self.verdict.undecided, queries=calls)

    def ruled_out(self):
        return {}

    def trusted_assumptions(self):
        return (TrustedAssumption(
            "test-assumption", "the test provider is faithful", scope="test"),)


def _bound(spy):
    return BoundedInstance(
        spy, TraceDomain(bound=1, contract={"x": [0, 1]},
                         frontend="behavior-test/1", policy="test",
                         artifact=_Task.realization.identity,
                         task_contract=_Task.contract.digest))


def _witness_origin(got, goal):
    support = got.per_goal[goal]["support_ref"]
    return next(row["origin"] for row in got.evidence["witnesses"]
                if row["witness_ref"] == support)


def test_saved_subject_uses_the_same_closure_and_goal_query_path():
    from behave.sim import analysis_service
    from behave.sim.analysis_budget import analysis_scope
    from behave.sim.behavior.exact_service import _analysis_subject_image

    evaluation = _evaluation()
    subject = analysis_mod._analysis_subject_from_wire(
        evaluation.task, _analysis_subject_image(evaluation), evaluation.catalogue)
    original, restored = _Spy(), _Spy()
    with analysis_scope(10):
        first = analysis_service._analyse(evaluation, _bound(original))
        second = analysis_service._analyse(subject, _bound(restored))
    assert first.verdict is second.verdict is Verdict.CLASSIFIED
    assert first.per_goal == second.per_goal
    assert first.concrete == second.concrete == evaluation.fingerprint
    assert original.events == restored.events
    assert original.query_calls == restored.query_calls == 1


def test_bound_instance_cannot_change_artifact_or_domain_after_binding():
    bound = _bound(_Spy())

    with pytest.raises(AttributeError, match="cannot be reassigned"):
        bound.instance = _Spy()
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        bound.domain = TraceDomain(bound=2)


@pytest.mark.parametrize("task_contract", [None, "another-task"])
def test_exact_domain_must_name_the_concrete_task_before_queries(task_contract):
    spy = _Spy()
    exact = BoundedInstance(
        spy, TraceDomain(bound=1, contract={"x": [0, 1]},
                         frontend="behavior-test/1", policy="test",
                         artifact=_Task.realization.identity,
                         task_contract=task_contract))

    got = api.analyse(_evaluation(), exact,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-task-contract-mismatch"
    assert spy.events == []


@pytest.mark.parametrize("artifact", [None, "another-artifact"])
def test_exact_domain_must_name_the_concrete_artifact_before_queries(artifact):
    spy = _Spy()
    exact = BoundedInstance(
        spy, TraceDomain(bound=1, contract={"x": [0, 1]},
                         frontend="behavior-test/1", policy="test",
                         artifact=artifact,
                         task_contract=_Task.contract.digest))

    got = api.analyse(_evaluation(), exact,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] in {
        "analysis-domain-artifact-unbound", "analysis-artifact-mismatch"}
    assert spy.events == []


def test_bound_provider_must_still_be_the_artifact_named_by_its_domain():
    spy = _Spy()
    exact = _bound(spy)
    spy.identity = "provider-was-replaced"

    got = api.analyse(_evaluation(), exact,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-provider-domain-mismatch"
    assert spy.events == []


def _audit_to(value, events=None):
    def audit(_record, _exact):
        if events is not None:
            events.append("record-audit")
        trace = ({"x": value},)
        return api.RecordAudit.admitted(
            _exact.instance._witness(trace, _exact.domain.digest()))
    return audit


def test_nested_analysis_keeps_each_evidence_pool_and_policy_owned():
    """A frontend callback may enter another analysis without sharing state."""
    outer_spy = _Spy(pool_hits={((('x', 0),),): {GOAL.id}})
    inner_spy = _Spy()
    outer_evaluation = _evaluation(_result())
    inner_evaluation = _evaluation()
    before = outer_evaluation.as_dict(), inner_evaluation.as_dict()
    nested = []

    def audit(record, exact):
        nested.append(api.analyse(
            inner_evaluation, _bound(inner_spy), batch=2,
            wall_timeout_seconds=60))
        return _audit_to(0)(record, exact)

    outer = api.analyse(
        outer_evaluation, _bound(outer_spy), audit, batch=1,
        wall_timeout_seconds=60)

    assert outer.verdict is Verdict.CLASSIFIED
    assert outer.per_goal[GOAL.id]['status'] == 'reached'
    assert outer.evidence['feasibility']['established_by'] == 'audited-pool'
    assert outer.evidence['analysis_policy']['batch'] == 1
    assert outer_spy.feasible_calls == outer_spy.query_calls == 0
    inner, = nested
    assert inner.verdict is Verdict.CLASSIFIED
    assert inner.per_goal[GOAL.id]['status'] == 'bounded-unreachable'
    assert inner.evidence['feasibility']['established_by'] == 'exact-query'
    assert inner.evidence['analysis_policy']['batch'] == 2
    assert inner_spy.feasible_calls == inner_spy.query_calls == 1
    assert (outer_evaluation.as_dict(), inner_evaluation.as_dict()) == before


def test_refused_invocation_cannot_leave_evidence_for_the_next_one():
    first = _Spy()
    refused = api.analyse(
        _evaluation(_result()), _bound(first),
        lambda *_: api.RecordAudit.refused('audit-unavailable', 'no evidence'),
        wall_timeout_seconds=60)
    assert refused.verdict is Verdict.REFUSED
    assert refused.evidence['refusal']['kind'] == 'audit-unavailable'
    assert first.feasible_calls == first.query_calls == 0

    second = _Spy()
    classified = api.analyse(_evaluation(), _bound(second), wall_timeout_seconds=60)
    assert classified.verdict is Verdict.CLASSIFIED
    assert classified.per_goal[GOAL.id]['status'] == 'bounded-unreachable'
    assert not classified.evidence['record_audits']
    assert second.feasible_calls == second.query_calls == 1


@pytest.mark.parametrize('stop_at', ['first-query', 'query', 'between-rounds'])
def test_query_budget_stop_preserves_completed_evidence_not_a_partition(
        monkeypatch, stop_at):
    from behave.sim import analysis_budget

    now = [0.0]
    monkeypatch.setattr(analysis_budget.time, 'monotonic', lambda: now[0])
    goals = {name: Goal(name, COVERAGE, family='test', provider='test',
                       policy='analysis-test/1') for name in 'abcd'}
    spy = _Spy(pool_hits={((('x', 0),),): {'a'}})
    spy.goals = lambda: goals
    calls = []

    def query(asking, steps, timeout_ms=None):
        calls.append(tuple(asking))
        if stop_at == 'first-query':
            now[0] = 60
            analysis_budget.check_analysis()
        if asking == ['b']:
            return QueryVerdict({}, {'b'}, {}, queries=(
                QueryCall(('b',), SolverAnswer.UNSAT),))
        if asking == ['c']:
            if stop_at == 'between-rounds':
                now[0] = 60
            return QueryVerdict({}, set(), {'c': 'solver unknown'}, queries=(
                QueryCall(('c',), SolverAnswer.UNKNOWN, reason='solver unknown'),))
        assert asking == ['d']
        now[0] = 60
        analysis_budget.check_analysis()

    spy.query = query
    evaluation = ConcreteEvaluation(
        _Task(), results=(_result(),), catalogue=tuple(goals.values()))
    before = evaluation.as_dict()
    got = api.analyse(evaluation, _bound(spy), _audit_to(0), batch=1,
                      wall_timeout_seconds=60)
    assert got.verdict is Verdict.REFUSED
    assert not got.per_goal  # Interrupted work is not a complete classification.
    issue = got.evidence['refusal']
    assert issue['kind'] == 'analysis-budget-exhausted'
    assert issue['detail']['completed_reached'] == ['a']
    first = stop_at == 'first-query'
    assert issue['detail']['completed_unreachable'] == ([] if first else ['b'])
    assert issue['detail']['completed_unresolved'] == ([] if first else ['c'])
    assert issue['detail']['pending'] == (['b', 'c', 'd'] if first else ['d'])
    assert len(got.evidence.get('queries', ())) == (0 if first else 2)
    assert got.evidence['record_audits'][0]['disposition'] == 'admitted'
    assert got.evidence['feasibility']['established_by'] == 'audited-pool'
    assert got.evidence['passed_checks']
    assert calls == ([('b',)] if first else
                     [('b',), ('c',), ('d',)] if stop_at == 'query'
                     else [('b',), ('c',)])
    assert AnalysisOutcome.from_dict(got.as_dict()).as_dict() == got.as_dict()
    assert evaluation.as_dict() == before


def test_small_provider_answer_survives_a_later_provider_budget_stop(monkeypatch):
    from behave.sim import analysis_budget

    now = [0.0]
    monkeypatch.setattr(analysis_budget.time, 'monotonic', lambda: now[0])
    # Public IDs deliberately sort in the opposite order to provider size.
    goals = {name: Goal(name, kind, family='test', provider=provider,
                       policy='analysis-test/1') for name, kind, provider in (
        ('a', SAFETY, 'monitor'), ('b', SAFETY, 'monitor'),
        ('y', COVERAGE, 'source'), ('z', COVERAGE, 'source'))}
    spy = _Spy(pool_hits={((('x', 0),),): {'y'}})
    spy.goals = lambda: goals
    calls = []

    def query(asking, steps, timeout_ms=None):
        calls.append(tuple(asking))
        if asking == ['z']:
            return QueryVerdict({}, {'z'}, {}, queries=(
                QueryCall(('z',), SolverAnswer.UNSAT),))
        assert asking == ['a', 'b']
        now[0] = 60
        analysis_budget.check_analysis()

    spy.query = query
    evaluation = ConcreteEvaluation(
        _Task(), results=(_result(),), catalogue=tuple(goals.values()))
    before = evaluation.as_dict()
    got = api.analyse(evaluation, _bound(spy), _audit_to(0), wall_timeout_seconds=60)
    assert calls == [('z',), ('a', 'b')]
    assert got.verdict is Verdict.REFUSED
    assert not got.per_goal
    detail = got.evidence['refusal']['detail']
    assert detail['completed_reached'] == ['y']
    assert detail['completed_unreachable'] == ['z']
    assert detail['pending'] == ['a', 'b']
    assert len(got.evidence['queries']) == 1
    assert AnalysisOutcome.from_dict(got.as_dict()).as_dict() == got.as_dict()
    assert evaluation.as_dict() == before


@pytest.mark.parametrize('stop_at', ['first-record', 'second-record', 'between-records'])
@pytest.mark.parametrize('verified', [False, True])
def test_record_budget_stop_retains_completed_receipts_not_a_goal_partition(
        monkeypatch, stop_at, verified):
    from behave.sim import analysis_budget
    from behave.sim.core.audit_plan import AuditKind

    now = [0.0]
    monkeypatch.setattr(analysis_budget.time, 'monotonic', lambda: now[0])
    spy = _Spy(pool_hits={((('x', 0),),): {GOAL.id}})
    evaluation = _evaluation(_result('first', case=0), _result('second', case=1))
    before = evaluation.as_dict()
    calls = []

    def ordered(subject, *, horizon):
        assert horizon == 1
        yield 0, subject.results[0]
        if stop_at == 'between-records':
            now[0] = 60
        yield 1, subject.results[1]

    monkeypatch.setattr(analysis_mod, '_record_audit_order', ordered)

    def audit(record, exact):
        calls.append(record)
        if len(calls) == (1 if stop_at == 'first-record' else 2):
            now[0] = 60
            try:
                analysis_budget.check_analysis()
            except analysis_budget.AnalysisStopped as exc:
                exc.add_note('test record boundary')
                raise
        got = _audit_to(0)(record, exact)
        return (api.RecordAudit.admitted(got.witness, audits=AuditPlan(
            (AuditKind.STEP, AuditKind.SOURCE))) if verified else got)

    got = api.analyse(evaluation, _bound(spy), audit, wall_timeout_seconds=60)
    first = stop_at == 'first-record'
    assert got.verdict is Verdict.REFUSED and not got.per_goal
    detail = got.evidence['refusal']['detail']
    assert got.evidence['refusal']['kind'] == 'analysis-budget-exhausted'
    assert detail['phase'] == 'record-audit'
    assert detail['interrupted_record'] == (0 if first else 1)
    assert detail['completed_records'] == (0 if first else 1)
    assert detail['completed_record_audits'] == detail['admitted_witnesses'] == (0 if first else 1)
    assert detail['admitted_record_hits'] == ([] if first else [GOAL.id])
    assert detail['notes'] == ([] if stop_at == 'between-records' else ['test record boundary'])
    rows = got.evidence.get('record_audits', ())
    assert len(rows) == (0 if first else 1)
    if rows:
        assert rows[0]['record'] == 0 and rows[0]['disposition'] == 'admitted'
    assert spy.query_calls == spy.feasible_calls == 0
    assert AnalysisOutcome.from_dict(got.as_dict()).as_dict() == got.as_dict()
    assert evaluation.as_dict() == before


def test_complete_record_checks_feed_closure_without_another_execution():
    from behave.sim.core.audit_plan import AuditKind

    spy = _Spy(pool_hits={((('x', 0),),): {GOAL.id}})

    def checked(record, exact):
        got = _audit_to(0, spy.events)(record, exact)
        return api.RecordAudit.admitted(got.witness, audits=AuditPlan(
            (AuditKind.STEP, AuditKind.SOURCE)))

    evaluation = _evaluation(_result())
    before = evaluation.as_dict()
    got = api.analyse(evaluation, _bound(spy), checked, wall_timeout_seconds=60)
    assert got.per_goal[GOAL.id]['status'] == 'reached'
    assert spy.events.count('replay') == 1  # The record producer only.
    assert 'step-audit' not in spy.events and 'source-audit' not in spy.events
    assert spy.query_calls == 0
    assert evaluation.as_dict() == before


def test_native_records_only_add_new_audited_goal_support():
    spy = _Spy(pool_hits={((('x', 1),),): {GOAL.id}})
    calls = []

    def audit(record, exact):
        value = dict(record.stimulus.slots[0].controls)['case']
        calls.append(record)
        return _audit_to(value)(record, exact)

    evaluation = _evaluation(
        _result('first', hits=((GOAL.id, False),), case=0),
        _result('new-hit', hits=((GOAL.id, True),), case=1),
        _result('same-hit', hits=((GOAL.id, True),), case=2),
        _result('no-new-hit', hits=((GOAL.id, False),), case=3))
    before = evaluation.as_dict()
    got = api.analyse(evaluation, _bound(spy), audit, wall_timeout_seconds=60)
    assert got.verdict is Verdict.CLASSIFIED
    assert len(calls) == 1
    assert calls[0] is evaluation.results[1]
    assert got.per_goal[GOAL.id]['status'] == 'reached'
    assert spy.query_calls == 0
    assert evaluation.as_dict() == before
    rows = got.evidence['record_audits']
    assert [row['disposition'] for row in rows] == [
        'not-selected', 'admitted', 'not-selected', 'not-selected']
    assert rows[0]['covered_by'] == [rows[1]['witness_ref']]
    assert rows[2]['covered_by'] == [rows[1]['witness_ref']]
    assert AnalysisOutcome.from_dict(got.as_dict()).as_dict() == got.as_dict()
    # Nonselection is not permission to fabricate support or a passed check.
    for patch in ({'covered_by': ['witness:missing']},
                  {'covered_by': ['record:2']},
                  {'observed_hits': ['unknown-goal']}):
        image = got.as_dict()
        image['evidence']['record_audits'][2].update(patch)
        with pytest.raises(ValueError, match='audited goal support'):
            AnalysisOutcome.from_dict(image)
    # Removing the admitted anchor must not turn cross-references into proof.
    image = got.as_dict()
    image['evidence']['record_audits'][1] = {**rows[2], 'record': 1}
    with pytest.raises(ValueError, match='audited goal support'):
        AnalysisOutcome.from_dict(image)


def test_raw_hit_hints_cannot_replace_audited_goal_support():
    spy = _Spy()
    calls = []

    def audit(record, exact):
        calls.append(record)
        return _audit_to(0)(record, exact)

    got = api.analyse(_evaluation(*(
        _result(hits=((GOAL.id, True),), case=i) for i in range(3))),
        _bound(spy), audit, wall_timeout_seconds=60)
    assert got.verdict is Verdict.CLASSIFIED
    assert len(calls) == 3
    assert spy.query_calls == 1  # Raw hints never establish a reached goal.
    assert got.per_goal[GOAL.id]['status'] == 'bounded-unreachable'


@pytest.mark.parametrize('change', ['artifact', 'domain', 'completion', 'eligible', 'unknown'])
def test_invalid_or_unrecognized_record_cannot_be_hidden_by_selection(change):
    from behave.sim.core.stimulus import Completion

    record = _result(hits=((GOAL.id, True),))
    image = record.trace.as_dict()
    if change in {'artifact', 'domain'}:
        image[change] = 'another'
    elif change == 'completion':
        image['completion'] = Completion.INCOMPLETE.value
    elif change == 'unknown':
        image['records'][0]['hits'] = [['unknown-goal', True]]
    trace = AuditedTrace.from_dict(image)
    candidate = RunResult.not_compared(record.stimulus, trace,
        record.comparison_failure, observations_eligible=change != 'eligible')
    exact = _bound(_Spy())
    witness = exact.instance._witness(({'x': 0},), exact.domain.digest())
    assert analysis_mod._redundant_record_support(candidate, _evaluation(record),
        [witness], {GOAL.id: 'witness:' + witness.fingerprint}) is None
    # Exercise this helper even for records rejected by the outer concrete
    # constructor (unknown catalogue entries or duplicate stimuli).
    from types import SimpleNamespace
    evaluation = SimpleNamespace(task=_Task(), catalogue={GOAL.id: GOAL},
                                 results=(record, candidate))
    assert analysis_mod._record_audit_order(evaluation)[0] == (1, candidate)


def test_record_priority_is_stable_for_equal_hits_and_preserves_empty_pool():
    records = [_result(hits=((GOAL.id, True),), case=i) for i in range(3)]
    assert analysis_mod._record_audit_order(_evaluation(*records)) == list(enumerate(records))
    assert analysis_mod._record_audit_order(_evaluation()) == []


def test_record_priority_prefers_new_support_over_repeated_rich_records():
    from types import SimpleNamespace

    groups = ('abcd', 'abce', 'efg', 'gh')
    records = [_result(hits=tuple((name, True) for name in group), case=i)
               for i, group in enumerate(groups)]
    evaluation = SimpleNamespace(task=_Task(), results=records,
        catalogue={name: Goal(name, COVERAGE, family='test', provider='test',
                              policy='analysis-test/1') for name in 'abcdefgh'})
    before = [record.as_dict() for record in records]
    order = analysis_mod._record_audit_order(evaluation, horizon=1)
    assert [index for index, _ in order] == [0, 2, 3, 1]
    assert [record.as_dict() for record in records] == before
    assert all(record is records[index] for index, record in order)


def test_record_priority_walks_hit_edges_linearly(monkeypatch):
    from types import SimpleNamespace

    visited = []
    class Hints:
        def __init__(self, names):
            self.names = names
        def __len__(self):
            return len(self.names)
        def __iter__(self):
            for name in self.names:
                visited.append(name)
                yield name

    records = [Hints(('common', str(i))) for i in range(1000)]
    calls = []
    def hint(record, _evaluation, *, horizon):
        calls.append(record)
        assert horizon == 7
        return record
    monkeypatch.setattr(analysis_mod, '_record_hit_hint', hint)
    result = analysis_mod._record_audit_order(SimpleNamespace(results=records), horizon=7)
    assert result == list(enumerate(records))
    assert calls == records  # Inspect each trace once, not once per selection.
    assert len(visited) == 2 * sum(map(len, records))


@pytest.mark.parametrize('compact', [False, True])
def test_record_selection_uses_only_the_declared_window_without_promoting_hints(compact):
    from behave.sim.core.hit_vector import HitVector

    stimulus = Stimulus({}, [EventSlot(), EventSlot()], source='long-record')
    hits = [((GOAL.id, False),), ((GOAL.id, True),)]
    if compact:
        hits = [HitVector.from_names((GOAL.id,), reached=() if i == 0 else (GOAL.id,))
                for i in range(2)]
    trace = AuditedTrace(stimulus,
        records=[SlotRecord(hits=value) for value in hits],
        domain=trace_domain(_Task.contract.digest, horizon=2), artifact=_Task.realization.identity)
    long_record = RunResult.not_compared(stimulus, trace, _result().comparison_failure)
    early = _result(hits=((GOAL.id, True),), case=1)
    evaluation = _evaluation(long_record, early)
    assert analysis_mod._record_hit_hint(long_record, evaluation) == {GOAL.id}
    assert analysis_mod._record_hit_hint(long_record, evaluation, horizon=1) == set()
    assert analysis_mod._record_audit_order(evaluation, horizon=1)[0] == (1, early)
    before = evaluation.as_dict()
    spy = _Spy()  # Audited witnesses do not confirm the untrusted raw hints.
    calls = []
    def audit(record, exact):
        calls.append(record)
        return _audit_to(0)(record, exact)
    outcome = api.analyse(evaluation, _bound(spy), audit, wall_timeout_seconds=60)
    assert calls == [early]  # The long record adds no in-window goal support.
    assert spy.query_calls == 1
    assert outcome.per_goal[GOAL.id]['status'] == 'bounded-unreachable'
    assert evaluation.as_dict() == before


@pytest.mark.parametrize('completed', ['step_audit', 'source_audit'])
def test_partial_record_checks_do_not_skip_the_missing_check(completed):
    spy = _Spy()

    def checked(record, exact):
        got = _audit_to(0, spy.events)(record, exact)
        return api.RecordAudit.admitted(got.witness, audits=AuditPlan.from_names([completed]))

    got = api.analyse(_evaluation(_result()), _bound(spy), checked, wall_timeout_seconds=60)
    assert got.per_goal[GOAL.id]['status'] == 'bounded-unreachable'
    missing = 'source-audit' if completed == 'step_audit' else 'step-audit'
    assert missing in spy.events
    assert completed.replace('_', '-') not in spy.events
    assert spy.events.count('replay') == 2  # Producer + receiving check; no closure replay.


def test_record_checks_are_typed_and_preserved_by_pickle():
    import pickle
    from behave.sim.core.audit_plan import AuditKind

    witness = _Spy()._witness(({'x': 0},), _bound(_Spy()).domain.digest())
    with pytest.raises(TypeError, match='completed step/source'):
        api.RecordAudit.admitted(witness, audits=True)
    with pytest.raises(TypeError, match='completed step/source'):
        api.RecordAudit.admitted(witness, audits=AuditPlan.all())
    receipt = api.RecordAudit.admitted(witness, audits=AuditPlan((AuditKind.STEP, AuditKind.SOURCE)))
    assert pickle.loads(pickle.dumps(receipt)).as_dict() == receipt.as_dict()


def test_missing_exact_instance_is_a_typed_refusal_and_changes_no_concrete_byte():
    evaluation = _evaluation(_result())
    before = json.dumps(evaluation.as_dict(), sort_keys=True)

    got = api.analyse(evaluation,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == "analysis-exact-instance-missing"
    assert json.dumps(evaluation.as_dict(), sort_keys=True) == before
    assert got.concrete == evaluation.fingerprint


def test_failed_exact_construction_keeps_its_typed_reason_beside_the_score():
    evaluation = _evaluation(_result())
    before = json.dumps(evaluation.as_dict(), sort_keys=True)
    unavailable = Refusal(
        "rtl-task-exact-capability-unsupported",
        "the admitted task is outside the exact ready-valid subset",
        capability="ordered-ready-valid")

    got = api.analyse(evaluation, exact_refusal=unavailable,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"] == unavailable.as_dict()
    assert got.per_goal == {}
    assert got.concrete == evaluation.fingerprint
    assert json.dumps(evaluation.as_dict(), sort_keys=True) == before


def test_exact_instance_and_exact_refusal_cannot_both_claim_the_analysis():
    spy = _Spy()
    got = api.analyse(
        _evaluation(), _bound(spy),
        exact_refusal=Refusal("test-unavailable", "the test says unavailable"),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-exact-capability-ambiguous"
    assert spy.events == []


def test_an_untyped_exact_capability_failure_is_not_stringified():
    class Printable:
        def __str__(self):
            return "looks like a refusal"

    got = api.analyse(_evaluation(), exact_refusal=Printable(),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-exact-capability-refusal-invalid"


def test_record_audit_refusal_keeps_its_stable_kind_in_the_outcome():
    evaluation = _evaluation(_result())
    got = api.analyse(
        evaluation, _bound(_Spy()),
        lambda _result, _exact: api.RecordAudit.refused(
            "test-record-audit-refused", "the test decoder could not run"),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == "test-record-audit-refused"
    assert got.evidence["record_audits"][0]["kind"] == \
        "test-record-audit-refused"


@pytest.mark.parametrize('raised', [False, True])
def test_record_audit_preserves_structured_refusal_through_public_outcome(raised):
    from behave.sim.core.refusal import Refused

    evaluation = _evaluation(_result())
    detail = {'field': ['state', 2], 'physical_width': 16, 'encoded_width': 8}

    def unavailable(_result, _exact):
        if raised:
            raise Refused(Refusal('native-width', 'width unavailable', **detail))
        return api.RecordAudit.refused('native-width', 'width unavailable', **detail)

    got = api.analyse(evaluation, _bound(_Spy()), unavailable, wall_timeout_seconds=60)
    image = got.as_dict()
    assert got.verdict is Verdict.REFUSED and got.concrete == evaluation.fingerprint
    assert image['evidence']['refusal']['detail'] == detail
    assert image['evidence']['record_audits'][0]['detail'] == detail
    assert AnalysisOutcome.from_dict(image).as_dict() == image


@pytest.mark.parametrize('provider', ['rtl', 'behavior'])
def test_record_refusal_detail_roundtrip_is_detached_and_strict(provider):
    import importlib
    import pickle

    module = 'rtl.analysis' if provider == 'rtl' else 'behavior.exact_service'
    decoder = importlib.import_module('behave.sim.' + module)._decode_record_audit
    path = ['saved', 3]
    receipt = api.RecordAudit.refused('native-width', 'width unavailable', field=path)
    path.append('mutated')
    image = receipt.as_dict()
    assert image['detail'] == {'field': ['saved', 3]}
    assert decoder(image).as_dict() == pickle.loads(pickle.dumps(receipt)).as_dict() == image
    image['detail']['field'].append('changed output')
    assert receipt.as_dict()['detail'] == {'field': ['saved', 3]}
    old = {'disposition': 'refused', 'kind': 'old', 'why': 'legacy diagnostic'}
    assert decoder(old).as_dict() == old
    for invalid in (None, [], False, {'field': object()}):
        with pytest.raises((TypeError, ValueError)):
            decoder({**old, 'detail': invalid})
    with pytest.raises(ValueError):
        decoder({**old, 'passed': True})
    with pytest.raises(ValueError, match='only a refused record'):
        api.RecordAudit(api.AuditDisposition.OUTSIDE, why='outside', detail={'field': 'x'})


def test_oversized_complete_witness_is_a_typed_analysis_refusal():
    evaluation = _evaluation(_result())

    def oversized(_result, _exact):
        raise AuditedWitnessLimitError(123)

    got = api.analyse(evaluation, _bound(_Spy()), oversized,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-audited-witness-evidence-too-large"
    assert got.evidence["record_audits"][0]["kind"] == \
        "analysis-audited-witness-evidence-too-large"
    assert got.concrete == evaluation.fingerprint


def test_record_audit_cannot_construct_an_untyped_refusal():
    with pytest.raises(ValueError, match="stable refusal kind"):
        api.RecordAudit(api.AuditDisposition.REFUSED, why="prose only")


def test_record_audit_does_not_stringify_untyped_fields():
    class Printable:
        def __str__(self):
            return "looks-typed"

    with pytest.raises(TypeError, match="kind.*exact string"):
        api.RecordAudit.refused(Printable(), "why")
    with pytest.raises(TypeError, match="explanation.*exact string"):
        api.RecordAudit.outside(Printable())


def test_record_audit_is_a_closed_non_boolean_algebra():
    with pytest.raises(TypeError, match="exact AuditDisposition"):
        api.RecordAudit("outside", why="not in this domain")
    with pytest.raises(TypeError, match="cannot be subclassed"):
        class Forged(api.RecordAudit):
            pass
    with pytest.raises(TypeError, match="no truth value"):
        bool(api.AuditDisposition.OUTSIDE)


@pytest.mark.parametrize(("keyword", "value"), [
    ("feasibility_timeout_ms", 0),
    ("feasibility_timeout_ms", True),
    ("query_timeout_ms", -1),
    ("batch", 0),
])
def test_invalid_analysis_policy_refuses_before_any_provider_call(keyword, value):
    spy = _Spy()
    got = api.analyse(_evaluation(), _bound(spy), **{keyword: value},
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == "analysis-policy-value-invalid"
    assert got.evidence["refusal"]["detail"]["field"] == keyword
    assert got.evidence["refusal"]["detail"]["got"] == repr(value)
    assert spy.events == []


def test_something_other_than_the_exact_concrete_record_is_typed_refused():
    got = api.analyse(object(), _bound(_Spy()),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-concrete-record-invalid"


def test_analysis_outcome_is_closed_and_deeply_immutable():
    got = api.analyse(_evaluation(), _bound(_Spy()),
        wall_timeout_seconds=60)
    before = got.as_dict()

    with pytest.raises(AttributeError, match="cannot be reassigned"):
        got.why = "rewritten"
    view = got.evidence
    view["analysis_policy"]["batch"] = 99

    assert got.as_dict() == before
    with pytest.raises(ValueError, match="derived from the typed evidence record"):
        AnalysisOutcome(
            Verdict.REFUSED, why="no provider",
            per_goal={GOAL.id: {"status": "unreachable"}})
    with pytest.raises(TypeError, match="exact Verdict"):
        AnalysisOutcome(
            "refused", why=got.why, evidence=got.evidence_record,
            concrete=got.concrete)
    with pytest.raises(TypeError, match="no truth value"):
        bool(got.verdict)


def test_analysis_outcome_does_not_coerce_evidence_identity_fields():
    """A printable object is not the exact explanation or digest it impersonates."""
    got = api.analyse(_evaluation(), None,
        wall_timeout_seconds=60)

    class Printable:
        def __init__(self, value):
            self.value = value

        def __str__(self):
            return self.value

    with pytest.raises(TypeError, match="explanation must be an exact string"):
        AnalysisOutcome(
            got.verdict, why=Printable(got.why), evidence=got.evidence_record,
            concrete=got.concrete)
    with pytest.raises(TypeError, match="fingerprint must be an exact string"):
        AnalysisOutcome(
            got.verdict, why=got.why, evidence=got.evidence_record,
            concrete=Printable(got.concrete))


def test_a_frontend_callback_cannot_mutate_the_concrete_record_under_analysis():
    evaluation = _evaluation(_result())
    original = evaluation.task.reference

    def mutating(_record, _exact):
        evaluation.task.reference = "a different reference"
        trace = ({"x": 1},)
        return api.RecordAudit.admitted(
            _exact.instance._witness(trace, _exact.domain.digest()))

    try:
        got = api.analyse(evaluation, _bound(_Spy()), mutating,
            wall_timeout_seconds=60)
    finally:
        evaluation.task.reference = original

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert got.evidence["inconsistency"]["kind"] == \
        "analysis-mutated-concrete-evaluation"


def test_a_nonempty_audited_pool_makes_no_feasibility_query():
    key = ((("x", 1),),)
    spy = _Spy(pool_hits={key: {GOAL.id}})
    evaluation = _evaluation(_result())
    before = json.dumps(evaluation.as_dict(), sort_keys=True)

    got = api.analyse(evaluation, _bound(spy), _audit_to(1, spy.events),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED, got.as_dict()
    assert got.per_goal[GOAL.id]["status"] == "reached"
    assert got.evidence["feasibility"]["solver_calls"] == 0
    assert spy.feasible_calls == 0
    assert json.dumps(evaluation.as_dict(), sort_keys=True) == before


def test_public_analysis_serializes_one_domain_manifest_through_classification():
    spy = _Spy()
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    image = got.evidence
    manifest = DomainManifest.from_dict(image["domain_manifest"])
    assert manifest.digest() == image["domain_manifest_digest"]
    assert manifest.domain.digest() == image["domain_digest"]
    assert manifest.catalogue.digest() == image["catalogue_digest"]
    assert image["binding"]["domain_manifest"] == manifest.digest()
    assert any(
        row["name"] == "domain_manifest_binding"
        and "domain-manifest:" + manifest.digest() in row["subjects"]
        for row in image["passed_checks"])


def test_a_nonempty_pool_is_audited_before_its_first_goal_query():
    spy = _Spy()
    got = api.analyse(
        _evaluation(_result()), _bound(spy), _audit_to(1, spy.events),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert spy.feasible_calls == 0 and spy.query_calls == 1
    record = spy.events.index("record-audit")
    step = spy.events.index("step-audit", record)
    source = spy.events.index("source-audit", step)
    query = spy.events.index("goal-query", source)
    assert record < step < source < query


def test_an_empty_pool_asks_feasibility_once_and_audits_sat_before_goals():
    spy = _Spy(feasible=(Feasible.YES, ({"x": 0},)))
    evaluation = _evaluation(_result())

    def outside(_record, _exact):
        spy.events.append("record-audit")
        return api.RecordAudit.outside("this run is outside the semantic horizon")

    got = api.analyse(evaluation, _bound(spy), outside,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert spy.feasible_calls == 1
    assert spy.query_calls == 1
    assert got.evidence["feasibility"]["solver_calls"] == 1
    # The SAT witness is replayed, then both required audits run, before the closure is
    # allowed to issue its first goal query.  The second replay is close_over retaining
    # the audited witness as pool evidence.
    feasible = spy.events.index("feasible")
    first_replay = spy.events.index("replay", feasible)
    step_audit = spy.events.index("step-audit", first_replay)
    source_audit = spy.events.index("source-audit", step_audit)
    goal_query = spy.events.index("goal-query", source_audit)
    assert feasible < first_replay < step_audit < source_audit < goal_query


@pytest.mark.parametrize('complete_record', [False, True])
def test_rtl_checkpoint_neither_blocks_analysis_nor_supplies_a_goal_hit(complete_record):
    from behave.sim.rtl.analysis import _record_identity_audit

    checkpoint = _result(hits={GOAL.id: True}, completion=Completion.CHECKPOINT)
    records = (checkpoint, _result('complete', case=1)) if complete_record else (checkpoint,)
    evaluation = _evaluation(*records)
    before, fingerprint = evaluation.as_dict(), evaluation.fingerprint
    spy = _Spy()
    admit = _audit_to(1, spy.events)

    def audit(record, exact):
        identity = _record_identity_audit(record, evaluation.task, exact.domain)
        return identity if identity is not None else admit(record, exact)

    got = api.analyse(evaluation, _bound(spy), audit, wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert got.per_goal[GOAL.id]['status'] == 'bounded-unreachable'
    assert spy.query_calls == 1
    assert spy.feasible_calls == (0 if complete_record else 1)
    assert any(row['disposition'] == 'outside' for row in got.evidence['record_audits'])
    assert got.concrete == evaluation.fingerprint == fingerprint
    assert evaluation.as_dict() == before


def test_an_empty_goal_catalogue_is_a_complete_empty_partition():
    class NoGoals(_Spy):
        def goals(self):
            return {}

        def query(self, goals, steps, timeout_ms=None):
            raise AssertionError("an empty catalogue issues no goal query")

    spy = NoGoals()
    evaluation = ConcreteEvaluation(_Task(), catalogue=[])

    got = api.analyse(evaluation, _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert got.per_goal == {}
    assert got.evidence["closure"]["n_goals"] == 0
    assert got.evidence["closure"]["accounted_for"] is True
    assert spy.feasible_calls == 1 and spy.query_calls == 0


def test_exact_analysis_requires_the_provider_catalogue_manifest():
    class GoalsOnly(_Spy):
        catalogue_manifest = None

    got = api.analyse(_evaluation(), _bound(GoalsOnly()),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == "analysis-catalogue-unavailable"


def test_domain_catalogue_mismatch_is_a_manifest_stop_before_any_query():
    other = Goal(
        "other", COVERAGE, family="test", provider="test",
        policy="analysis-test/1")

    class DifferentCatalogue(_Spy):
        def catalogue_manifest(self):
            return CatalogueManifest((other,))

    spy = DifferentCatalogue()
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-domain-manifest-mismatch"
    exact_manifest = DomainManifest.from_dict(got.evidence["domain_manifest"])
    assert exact_manifest.catalogue == spy.catalogue_manifest()
    assert got.evidence["domain_manifest_digest"] == exact_manifest.digest()
    assert spy.feasible_calls == 0 and spy.query_calls == 0


def test_a_prebound_foreign_manifest_cannot_override_the_provider_authority():
    spy = _Spy()
    exact = _bound(spy)
    foreign = CatalogueManifest((Goal("foreign", COVERAGE),))
    forged = BoundedInstance(
        exact.instance, exact.domain, DomainManifest(exact.domain, foreign))

    got = api.analyse(_evaluation(), forged,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-domain-manifest-mismatch"
    authoritative = DomainManifest.from_dict(got.evidence["domain_manifest"])
    assert authoritative.catalogue == spy.catalogue_manifest()
    assert spy.feasible_calls == 0 and spy.query_calls == 0


def test_malformed_final_evidence_refuses_without_erasing_the_score(monkeypatch):
    key = ((("x", 1),),)
    spy = _Spy(pool_hits={key: {GOAL.id}})
    evaluation = _evaluation(_result())
    before = json.dumps(evaluation.as_dict(), sort_keys=True)

    def malformed_evidence(**_kwargs):
        raise ValueError("the provider supplied a contradictory evidence ledger")

    monkeypatch.setattr(analysis_mod, "ConditionalEvidence", malformed_evidence)
    got = api.analyse(
        evaluation, _bound(spy), _audit_to(1, spy.events),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED and got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == \
        "analysis-evidence-assembly-failed"
    assert got.evidence["refusal"]["detail"] == {
        "error_type": "ValueError", "intended_verdict": "classified"}
    assert got.concrete == evaluation.fingerprint
    assert json.dumps(evaluation.as_dict(), sort_keys=True) == before


def test_an_untyped_audit_plan_refuses_before_feasibility_or_queries():
    class UntypedAuditPlan(_Spy):
        def audit_plan(self):
            return ("step_audit", "step_audit")

    spy = UntypedAuditPlan()
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "analysis-audit-plan-invalid"
    assert spy.feasible_calls == 0 and spy.query_calls == 0


def test_unsat_feasibility_issues_no_goal_query():
    spy = _Spy(feasible=(Feasible.NO, "no trace"))
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INFEASIBLE_DOMAIN
    assert spy.feasible_calls == 1 and spy.query_calls == 0
    assert "goal-query" not in spy.events
    manifest = DomainManifest.from_dict(got.evidence["domain_manifest"])
    assert got.evidence["domain_manifest_digest"] == manifest.digest()
    assert got.evidence["binding"]["domain_manifest"] == manifest.digest()


def test_unresolved_feasibility_issues_no_goal_query():
    spy = _Spy(feasible=(Feasible.UNRESOLVED, "timeout"))
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.FEASIBILITY_UNRESOLVED
    assert spy.feasible_calls == 1 and spy.query_calls == 0
    assert "goal-query" not in spy.events


def test_a_sat_witness_must_be_a_materialized_finite_trace():
    class RaisesWhileIterating:
        def __iter__(self):
            raise RuntimeError("decoder escaped")

    spy = _Spy(feasible=(Feasible.YES, RaisesWhileIterating()))

    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT
    assert got.evidence["inconsistency"]["kind"] == \
        "analysis-feasibility-witness-undecodable"
    assert spy.feasible_calls == 1 and spy.query_calls == 0


def test_a_cyclic_feasibility_request_is_a_typed_inconsistency():
    cyclic = []
    cyclic.append(cyclic)
    spy = _Spy(feasible=(Feasible.YES, cyclic))

    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT
    assert got.evidence["inconsistency"]["kind"] == \
        "analysis-feasibility-witness-undecodable"
    assert spy.query_calls == 0


def test_a_feasibility_decoder_cannot_mutate_its_request_after_it_is_recorded():
    class Mutating(_Spy):
        def audit_request(self, trace, domain):
            trace[0]["x"] = 1
            return self._witness(trace, domain)

    spy = Mutating(feasible=(Feasible.YES, ({"x": 0},)))

    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == "witness-audit-failed"
    assert spy.query_calls == 0


def test_raw_record_inconsistency_stops_before_feasibility_or_goals():
    spy = _Spy()

    def inconsistent(_record, _exact):
        return api.RecordAudit.inconsistent("the raw and semantic outputs differ")

    got = api.analyse(_evaluation(_result()), _bound(spy), inconsistent,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert spy.feasible_calls == 0 and spy.query_calls == 0


def test_a_sat_witness_that_fails_exact_audit_never_reaches_goal_query():
    class Wrong(_Spy):
        def audit(self, traces):
            self.events.append("step-audit")
            return AuditCheck.inconsistent(["formula differs from replay"])

    spy = Wrong()
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert spy.feasible_calls == 1 and spy.query_calls == 0


@pytest.mark.parametrize("phase,pool", [
    ("feasible", False), ("audit", False), ("audit", True),
    ("source_audit", False), ("source_audit", True),
    ("query", False), ("query", True),
    ("record_audit", True), ("replay", False), ("replay", True),
])
def test_a_reported_exact_contradiction_is_global_not_a_refusal(monkeypatch, phase, pool):
    from behave.sim.core.audit import WitnessInconsistent

    spy = _Spy()

    def disagree(*_args, **_kwargs):
        raise WitnessInconsistent("source-disagrees", "two completed readings differ")

    if phase != "record_audit":
        monkeypatch.setattr(spy, phase, disagree)
    if phase == "replay":
        # Keep the witness producer independent of the replay being checked.
        # Otherwise the first audit_request would hide this receiving boundary.
        monkeypatch.setattr(spy, "audit_request", lambda trace, domain: AuditedWitness(
            domain=domain, states=({}, {}), inputs=trace,
            outputs=((),), hit_sets=((),)))
    evaluation = _evaluation(*((_result(),) if pool else ()))
    before = evaluation.as_dict()
    record_audit = (disagree if phase == "record_audit" else
                    _audit_to(0, spy.events) if pool else None)
    if phase == "replay" and pool:
        record_audit = lambda _record, exact: api.RecordAudit.admitted(
            spy.audit_request(({"x": 0},), exact.domain.digest()))
    got = api.analyse(evaluation, _bound(spy), record_audit,
        wall_timeout_seconds=60)
    assert got.verdict is Verdict.INCONSISTENT
    assert got.per_goal == {}
    assert "inconsistency" in got.evidence and "refusal" not in got.evidence
    assert evaluation.as_dict() == before
    assert spy.query_calls == 0


@pytest.mark.parametrize("phase", ["validate", "replay"])
def test_query_witness_receivers_preserve_completed_disagreements(monkeypatch, phase):
    from behave.sim.core.audit import WitnessInconsistent

    exact = _bound(_Spy())
    contradiction = WitnessInconsistent("source-disagrees", "two completed readings differ")

    def disagree(*_args, **_kwargs):
        raise contradiction

    monkeypatch.setattr(BoundedInstance, phase, disagree)
    outcomes = closure_mod._query_witnesses(
        exact, [((GOAL.id,), ({"x": 1},))], AuditPlan.all())
    assert outcomes == (contradiction,)


def test_a_sat_feasibility_witness_requires_a_typed_replay_record():
    class Malformed(_Spy):
        def replay(self, trace):
            self.events.append("replay")
            return object()

    spy = Malformed()
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED and got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == \
        "witness-replay-invalid"
    assert spy.feasible_calls == 1 and spy.query_calls == 0


def test_a_feasibility_witness_cannot_disagree_with_independent_replay():
    class ForgedFeasibility(_Spy):
        def audit_request(self, trace, domain):
            return AuditedWitness(
                domain=domain, states=({}, {}), inputs=trace,
                outputs=(({"forged": 1},),), hit_sets=((),))

    spy = ForgedFeasibility()
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert got.evidence["inconsistency"]["kind"] == \
        "analysis-witness-replay-disagrees"
    assert got.evidence["inconsistency"]["detail"]["context"] == \
        "feasibility-query"
    assert spy.feasible_calls == 1 and spy.query_calls == 0


def test_a_feasibility_audit_cannot_substitute_another_decoded_request():
    class SubstitutesRequest(_Spy):
        def audit_request(self, _trace, domain):
            return self._witness(({"x": 1},), domain)

    spy = SubstitutesRequest(feasible=(Feasible.YES, ({"x": 0},)))
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert got.evidence["inconsistency"]["kind"] == \
        "analysis-feasibility-witness-inconsistent"
    assert spy.feasible_calls == 1 and spy.query_calls == 0


def test_an_evaluated_record_witness_cannot_self_certify_its_outputs():
    spy = _Spy()
    evaluation = _evaluation(_result())

    def forged_record(_record, exact):
        trace = ({"x": 1},)
        return api.RecordAudit.admitted(AuditedWitness(
            domain=exact.domain.digest(), states=({}, {}), inputs=trace,
            outputs=(({"forged": 1},),), hit_sets=((),)))

    got = api.analyse(evaluation, _bound(spy), forged_record,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert got.evidence["inconsistency"]["kind"] == \
        "analysis-witness-replay-disagrees"
    assert got.evidence["inconsistency"]["detail"]["context"] == \
        "evaluated-record:0"
    assert spy.feasible_calls == 0 and spy.query_calls == 0


def test_query_results_are_serialized_by_the_existing_closure_algorithm():
    reached = ({GOAL.id: ({"x": 1},)}, set(), {})
    spy = _Spy(verdict=QueryVerdict(*reached),
               pool_hits={((("x", 1),),): {GOAL.id}})
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert got.per_goal[GOAL.id]["status"] == "reached"
    assert _witness_origin(got, GOAL.id) == "bounded-query"
    assert got.evidence["closure"]["accounted_for"] is True


def test_an_actual_unsat_call_cannot_be_hidden_by_a_replayed_sat_summary():
    witness = [{"x": 1}]
    spy = _Spy(
        pool_hits={((('x', 1),),): {GOAL.id}},
        verdict=QueryVerdict(
            {GOAL.id: witness}, set(), {}, queries=(
                QueryCall((GOAL.id,), SolverAnswer.UNSAT),
                QueryCall((GOAL.id,), SolverAnswer.SAT, witness=witness),
            )))

    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT
    assert got.evidence["inconsistency"]["kind"] == \
        "closure-query-transcript-unsat-refuted"


def test_an_actual_unsat_call_cannot_be_omitted_from_the_summary():
    spy = _Spy(verdict=QueryVerdict(
        {}, set(), {GOAL.id: "a later query timed out"}, queries=(
            QueryCall((GOAL.id,), SolverAnswer.UNSAT),
            QueryCall(
                (GOAL.id,), SolverAnswer.UNKNOWN,
                reason="a later query timed out",
            ),
        )))

    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.evidence["refusal"]["kind"] == \
        "closure-query-summary-omits-unsat"


def test_classification_is_not_blocked_by_the_old_all_conjunct_certificate_gate():
    coverage = Goal("coverage", COVERAGE, family="test", provider="test",
                    policy="analysis-test/1")
    safety = Goal("safety", SAFETY, family="test", provider="test",
                  policy="analysis-test/1")

    class Split(_Spy):
        def goals(self):
            return {coverage.id: coverage, safety.id: safety}

    evaluation = ConcreteEvaluation(_Task(), catalogue=[coverage, safety])
    first = Split(verdict=QueryVerdict(
        {}, {coverage.id}, {safety.id: "safety query timed out"}))
    got = api.analyse(evaluation, _bound(first),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert got.per_goal[coverage.id]["status"] == "bounded-unreachable"
    assert got.per_goal[safety.id]["status"] == "unresolved"

    second = Split(verdict=QueryVerdict(
        {}, {safety.id}, {coverage.id: "coverage query timed out"}))
    got = api.analyse(evaluation, _bound(second),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert got.per_goal[safety.id]["status"] == "bounded-unreachable"
    assert got.per_goal[coverage.id]["status"] == "unresolved"


def test_a_reached_safety_goal_is_classified_not_turned_into_analysis_failure():
    safety = Goal("safety", SAFETY, family="test", provider="test",
                  policy="analysis-test/1")
    witness = ({"x": 1},)

    class Safety(_Spy):
        def goals(self):
            return {safety.id: safety}

    spy = Safety(
        verdict=QueryVerdict({safety.id: witness}, set(), {}),
        pool_hits={((('x', 1),),): {safety.id}})
    evaluation = ConcreteEvaluation(_Task(), catalogue=[safety])

    got = api.analyse(evaluation, _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert got.per_goal[safety.id]["status"] == "reached"
    assert _witness_origin(got, safety.id) == "bounded-query"


def test_a_query_witness_outside_the_bound_domain_stops_the_whole_analysis():
    witness = ({"x": 2},)
    spy = _Spy(verdict=QueryVerdict({GOAL.id: witness}, set(), {}))

    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert got.evidence["inconsistency"]["kind"] == \
        "closure-query-witness-audit-disagrees"
    assert got.evidence["queries"] == [{
        "query_id": got.evidence["queries"][0]["query_id"],
        "order": 0,
            "goals": [GOAL.id],
            "answer": "sat",
            "solver_calls": 1,
            "passed_audits": [],
        "decoded_run_request": [{"x": 2}],
    }]
    assert spy.query_calls == 1


def test_a_query_witness_that_replay_cannot_run_is_a_run_level_refusal():
    witness = ({"x": 1},)

    class Broken(_Spy):
        def replay(self, trace):
            if dict(trace[0])["x"] == 1:
                self.events.append("replay")
                return Replay((), broken="the replay worker died", steps=1)
            return super().replay(trace)

    spy = Broken(verdict=QueryVerdict({GOAL.id: witness}, set(), {}))
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED and got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == \
        "witness-replay-failed"
    assert got.evidence["queries"][0]["answer"] == "sat"
    assert got.evidence["queries"][0]["decoded_run_request"] == [{"x": 1}]
    assert "audited_witness" not in got.evidence["queries"][0]
    assert "unresolved" not in got.evidence.get("closure", {})


def test_a_query_witness_missing_its_queried_hit_is_globally_inconsistent():
    witness = ({"x": 1},)
    spy = _Spy(verdict=QueryVerdict({GOAL.id: witness}, set(), {}))

    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert got.evidence["inconsistency"]["kind"] == \
        "closure-query-witness-misses-goal"


def test_a_query_witness_cannot_disagree_with_the_independent_replay():
    witness = ({"x": 1},)

    class ForgedOutput(_Spy):
        def audit_request(self, trace, domain):
            return AuditedWitness(
                domain=domain, states=({}, {}), inputs=trace,
                outputs=(({"forged": 1},),), hit_sets=({GOAL.id},))

    spy = ForgedOutput(
        verdict=QueryVerdict({GOAL.id: witness}, set(), {}),
        pool_hits={((('x', 1),),): {GOAL.id}})
    # A consistent evaluated record avoids making the forged query witness the
    # feasibility witness first.
    got = api.analyse(
        _evaluation(_result()), _bound(spy), _audit_to(0, spy.events),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT
    assert got.evidence["inconsistency"]["kind"] == \
        "closure-query-witness-replay-disagrees"


def test_catalogue_and_audit_plan_are_stable_through_the_query_transcript():
    changed_goal = Goal(
        GOAL.id, SAFETY, family="changed", provider="test",
        policy="analysis-test/1")

    class ChangesCatalogue(_Spy):
        changed = False

        def goals(self):
            return {GOAL.id: changed_goal if self.changed else GOAL}

        def query(self, goals, steps, timeout_ms=None):
            answer = super().query(goals, steps, timeout_ms=timeout_ms)
            self.changed = True
            return answer

    catalogue = ChangesCatalogue()
    got = api.analyse(_evaluation(), _bound(catalogue),
        wall_timeout_seconds=60)
    assert got.verdict is Verdict.INCONSISTENT
    assert got.evidence["inconsistency"]["kind"] == \
        "analysis-domain-manifest-changed"

    class ChangesAudits(_Spy):
        changed = False

        def audit_plan(self):
            return AuditPlan() if self.changed else AuditPlan.all()

        def query(self, goals, steps, timeout_ms=None):
            answer = super().query(goals, steps, timeout_ms=timeout_ms)
            self.changed = True
            return answer

    audits = ChangesAudits()
    got = api.analyse(_evaluation(), _bound(audits),
        wall_timeout_seconds=60)
    assert got.verdict is Verdict.INCONSISTENT
    assert got.evidence["inconsistency"]["kind"] == \
        "closure-audit-plan-changed"


def test_an_unavailable_query_witness_audit_refuses_instead_of_disagreeing():
    witness = ({"x": 1},)

    class Unavailable(_Spy):
        def audit(self, traces):
            self.events.append("step-audit")
            if dict(traces[0][0])["x"] == 1:
                return AuditCheck.refused(
                    "test-step-audit-unavailable",
                    "the independent interpreter did not start")
            return AuditCheck.agreed()

    spy = Unavailable(
        verdict=QueryVerdict({GOAL.id: witness}, set(), {}),
        pool_hits={((('x', 1),),): {GOAL.id}})
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED and got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == \
        "test-step-audit-unavailable"


def test_a_query_witness_source_disagreement_is_global_and_precedes_credit():
    witness = ({"x": 1},)

    class SourceWrong(_Spy):
        def source_audit(self, traces):
            self.events.append("source-audit")
            if dict(traces[0][0])["x"] == 1:
                return AuditCheck.inconsistent(
                    ["the source produces y=0 and the IR produces y=1"])
            return AuditCheck.agreed()

    spy = SourceWrong(
        verdict=QueryVerdict({GOAL.id: witness}, set(), {}),
        pool_hits={((('x', 1),),): {GOAL.id}})
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.INCONSISTENT and got.per_goal == {}
    assert got.evidence["inconsistency"]["kind"] == \
        "closure-query-witness-audit-disagrees"
    query = spy.events.index("goal-query")
    replay = spy.events.index("replay", query)
    step = spy.events.index("step-audit", replay)
    source = spy.events.index("source-audit", step)
    assert query < replay < step < source


def test_identical_sat_requests_are_audited_once_per_transcript():
    """Query decomposition may rediscover one request for several goal sets."""

    trace = [{"x": 1}]
    spy = _Spy(pool_hits={((('x', 1),),): {GOAL.id}})
    exact = _bound(spy)
    calls = (
        QueryCall((GOAL.id,), SolverAnswer.SAT, witness=trace),
        QueryCall((GOAL.id,), SolverAnswer.SAT, witness=trace),
    )

    records, sat, _unsat, _unknown = closure_mod._audit_query_transcript(
        exact, calls, (GOAL.id,), {GOAL.id}, AuditPlan.all())

    assert len(records) == len(sat) == 2
    # One audit_request and its independent replay each invoke these seams.
    # The second byte-identical QueryCall reuses that complete audited value.
    assert spy.events.count("replay") == 2
    assert spy.events.count("step-audit") == 2
    assert spy.events.count("source-audit") == 2
    assert records[0].witness == records[1].witness


def test_distinct_sat_requests_share_the_frontend_batch_audits():
    """Evidence closure batches work, but independently rechecks every witness."""

    class BatchSpy(_Spy):
        def audit_requests(self, traces, domain):
            self.events.append("witness-batch")
            replayed = [self.replay(trace) for trace in traces]
            checks = (self.audit(traces), self.source_audit(traces))
            if any(check.status.value != "agreed" for check in checks):
                failed = next(check for check in checks
                              if check.status.value != "agreed")
                return tuple(failed for _ in traces)
            return tuple(self._witness(trace, domain, got)
                         for trace, got in zip(traces, replayed))

    traces = ([{"x": 0}], [{"x": 1}])
    hits = {((('x', value),),): {GOAL.id} for value in (0, 1)}
    spy = BatchSpy(pool_hits=hits)
    calls = tuple(QueryCall(
        (GOAL.id,), SolverAnswer.SAT, witness=trace) for trace in traces)

    records, sat, _unsat, _unknown = closure_mod._audit_query_transcript(
        _bound(spy), calls, (GOAL.id,), {GOAL.id}, AuditPlan.all())

    assert len(records) == len(sat) == 2
    assert spy.events.count("witness-batch") == 1
    # Candidate construction and independent replay remain two distinct readings;
    # each reading audits the finite request batch once.
    assert spy.events.count("step-audit") == 2
    assert spy.events.count("source-audit") == 2


def test_query_result_statuses_are_pairwise_disjoint():
    with pytest.raises(ValueError, match="two statuses"):
        QueryVerdict({}, {GOAL.id}, {GOAL.id: "timeout"})


def test_a_duck_typed_query_result_is_a_run_level_refusal():
    class LooksLikeVerdict:
        def __init__(self):
            self.reachable = {}
            self.unreachable = {GOAL.id}
            self.undecided = {}

    spy = _Spy(verdict=LooksLikeVerdict())
    got = api.analyse(_evaluation(), _bound(spy),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED and got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == "closure-query-result-invalid"


def test_behavior_exact_instance_runs_through_the_public_coordinator_end_to_end():
    """Real lowering, exact feasibility, audits and closure -- no fake query arm."""
    from behave.sim.behavior.instance import lower
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    source = """
class M:
    def reset(self):
        pass

    def process(self, a):
        if a:
            return {"y": 1}
        return {"y": 0}
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 1), Endpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    behavior = lower(source, schema, bound=1)
    task = _task_for_behavior(behavior)
    evaluation = ConcreteEvaluation(task, catalogue=behavior.goals())

    got = api.analyse(
        evaluation,
        behavior.bind(1, task_contract=task.contract.digest),
        goal_policy=api.evaluation_policy(
            task, behavior.frontend_catalogue_manifest()),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert {row["status"] for row in got.per_goal.values()} == {"reached"}
    assert {_witness_origin(got, goal) for goal in got.per_goal} == {
        "feasibility-query", "bounded-query"}
    assert got.evidence["feasibility"]["solver_calls"] == 1
    assert got.evidence["closure"]["accounted_for"] is True
    manifest = DomainManifest.from_dict(got.evidence["domain_manifest"])
    assert manifest.catalogue == behavior.frontend_catalogue_manifest()
    assert got.evidence["domain_manifest_digest"] == manifest.digest()
    assert got.evidence["binding"]["domain_manifest"] == manifest.digest()


@pytest.mark.parametrize("unknown_at", [1, 2])
def test_real_behavior_audit_unknown_withholds_classification_and_can_retry(
        monkeypatch, unknown_at):
    import z3

    from behave.sim.behavior import audit as behavior_audit
    from behave.sim.behavior.instance import lower
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    source = """
class M:
    def reset(self):
        pass
    def process(self, a):
        if a:
            return {"y": 1}
        return {"y": 0}
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 1), Endpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    behavior = lower(source, schema, bound=1)
    task = _task_for_behavior(behavior)
    evaluation = ConcreteEvaluation(task, catalogue=behavior.goals())
    bound = behavior.bind(1, task_contract=task.contract.digest)
    policy = api.evaluation_policy(task, behavior.frontend_catalogue_manifest())
    original, calls = behavior_audit._audit_model, []

    def unresolved_model(solver):
        calls.append(None)
        if len(calls) == unknown_at:
            # Inject only the encoding-audit query; feasibility, lowering and
            # goal queries still use the real solver and production coordinator.
            solver.check = lambda: z3.unknown
            solver.reason_unknown = lambda: "coordinator-audit-budget"
        return original(solver)

    monkeypatch.setattr(behavior_audit, "_audit_model", unresolved_model)
    got = api.analyse(evaluation, bound, goal_policy=policy,
        wall_timeout_seconds=60)
    assert len(calls) == unknown_at
    assert got.verdict is Verdict.REFUSED and got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == "behavior-step-audit-unresolved"
    assert "coordinator-audit-budget" in got.evidence["refusal"]["why"]

    retried = api.analyse(evaluation, bound, goal_policy=policy,
        wall_timeout_seconds=60)
    assert retried.verdict is Verdict.CLASSIFIED
    assert {row["status"] for row in retried.per_goal.values()} == {"reached"}


def test_behavior_exact_boundary_cannot_narrow_the_task_input_domain():
    """A copied task digest is not proof that the represented input set is the task's."""
    from behave.sim.behavior.instance import lower
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    source = """
class M:
    def reset(self):
        pass

    def process(self, a):
        if a == 3:
            return {"y": 1}
        return {"y": 0}
"""
    narrow = TransactionSchema(
        [Endpoint("a", "input", 1), Endpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    behavior = lower(source, narrow, bound=1)
    task = _task_for_behavior(behavior, input_width=2)
    evaluation = ConcreteEvaluation(task, catalogue=behavior.goals())

    got = api.analyse(
        evaluation, behavior.bind(1, task_contract=task.contract.digest),
        goal_policy=api.evaluation_policy(
            task, behavior.frontend_catalogue_manifest()),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED and got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == \
        "analysis-task-binding-disagrees"


def test_behavior_source_audit_failure_is_typed_refusal(monkeypatch):
    """An unavailable host run is not a source/IR disagreement."""
    from behave.sim.behavior import isolated
    from behave.sim.behavior.instance import lower
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    source = """
class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": a}
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 1), Endpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    behavior = lower(source, schema, bound=1)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("the isolation process did not start")

    monkeypatch.setattr(isolated, "isolated_model", unavailable)
    got = behavior.source_audit(([{"a": 0}],))

    assert got.status is AuditCheckStatus.REFUSED
    assert got.kind == "behavior-source-audit-load-failed"
    assert "did not start" in got.why


def test_behavior_default_record_audit_reuses_the_concrete_run_as_the_pool():
    """A real RunResult reaches the fixed Behavior audit without a caller seam."""
    from behave.sim.behavior.instance import lower
    from behave.sim.core.schema import Authority, Reading, TransactionSchema
    from behave.sim.core.schema import Endpoint as SchemaEndpoint

    source = """
class M:
    def reset(self):
        pass

    def process(self, a):
        if a:
            return {"y": 1}
        return {"y": 0}
"""
    schema = TransactionSchema(
        [SchemaEndpoint("a", "input", 1),
         SchemaEndpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    behavior = lower(source, schema, bound=1)
    goal = next(g for g in behavior.goals() if g.endswith(":T"))
    concrete = behavior.replay([{"a": 1}])
    assert not concrete.error and not concrete.broken
    goal_bits = {
        name: name in concrete.hits for name in behavior.goals()
    }

    task = _task_for_behavior(behavior)
    contract = task.contract
    realization = task.realization
    stimulus = Stimulus(queues={"in": [(1,)]},
                        slots=[EventSlot(offers=["in"])], source="behavior-run")
    trace = AuditedTrace(
        stimulus,
        records=[SlotRecord(accepted={"in": (1,)},
                            outputs={"out": ((1,),)},
                            hits=goal_bits)],
        domain=trace_domain(contract.digest, horizon=1),
        artifact=realization.identity)
    result = RunResult.not_compared(
        stimulus, trace,
        api.RunFailure.verifier(
            "test-audit-view",
            api.Refusal(
                "test-uncompared", "the output score is independent here")))
    evaluation = ConcreteEvaluation(
        task, results=[result], hits=concrete.hits,
        catalogue=behavior.goals())

    got = api.analyse(
        evaluation,
        behavior.bind(1, task_contract=task.contract.digest),
        goal_policy=api.evaluation_policy(
            task, behavior.frontend_catalogue_manifest()),
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert got.evidence["feasibility"]["solver_calls"] == 0
    assert got.per_goal[goal]["status"] == "reached"
    assert _witness_origin(got, goal) == "evaluated-record"
