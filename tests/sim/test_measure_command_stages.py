"""Measurement wrappers must not change stage order, calls or failures."""

import pytest

from behave.sim import command
from tools.measure_search_score import command_stage_timings


def test_stage_timer_forwards_calls_and_restores_owners(monkeypatch):
    calls = []
    sentinel = object()

    def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(command.generation_service, "generate_stimuli", generate)
    rows, images = [], []
    with command_stage_timings(rows, lambda: images.append([dict(row) for row in rows])):
        assert command.generation_service.generate_stimuli(1, seed=2) is sentinel
    assert command.generation_service.generate_stimuli is generate
    assert calls == [((1,), {"seed": 2})]
    assert [(row["name"], row["status"]) for row in rows] == [("generation", "returned")]
    assert rows[0]["elapsed_s"] >= 0
    assert images[0] == [{"name": "generation", "status": "running"}]
    # Unentered score/analysis stages must not be reported as zero-cost passes.
    assert len(rows) == 1


def test_stage_timer_preserves_the_original_failure_and_does_not_retry(monkeypatch):
    failure = RuntimeError("actual score failure")
    calls = []

    def score(*_args, **_kwargs):
        calls.append(1)
        raise failure

    monkeypatch.setattr(command.evaluation_service, "evaluate_generated", score)
    rows = []
    with pytest.raises(RuntimeError) as caught, command_stage_timings(rows, lambda: None):
        command.evaluation_service.evaluate_generated()
    assert caught.value is failure
    assert command.evaluation_service.evaluate_generated is score
    assert calls == [1]
    assert rows[0]["name"] == "concrete_score"
    assert rows[0]["status"] == "raised"


def test_stage_timer_includes_baseline_replay_before_generation(monkeypatch):
    owner = command.evaluation_service.ReplayExecution
    calls, rows = [], []

    def evaluate(self, stimuli, **kwargs):
        calls.append((self, stimuli, kwargs))
        return "checked"

    monkeypatch.setattr(owner, "evaluate", evaluate)
    receiver = object()
    with command_stage_timings(rows, lambda: None):
        assert owner.evaluate(receiver, (1, 2), telemetry={"test": True}) == "checked"
    assert owner.evaluate is evaluate
    assert calls == [(receiver, (1, 2), {"telemetry": {"test": True}})]
    assert [(row["name"], row["status"]) for row in rows] == [("replay_assessment", "returned")]


def test_analysis_operation_timing_keeps_refusal_and_failure_distinct_from_completion(monkeypatch):
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance
    from behave.sim.core.refusal import Refusal

    calls, rows = [], []
    refusal = Refusal('test-unresolved', 'no goal was proved')
    failure = InterruptedError('shared analysis deadline')
    def operation(self, name, **extra):
        calls.append((self, name, extra))
        if name == 'decode':
            raise failure
        return refusal
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', operation)
    receiver = object()
    with command_stage_timings(rows, lambda: None):
        assert RTLIsolatedAnalysisInstance._raw_call(receiver, 'record-audit', rows=[1]) is refusal
        with pytest.raises(InterruptedError) as caught:
            RTLIsolatedAnalysisInstance._raw_call(receiver, 'decode', rows=[1])
    assert caught.value is failure
    assert RTLIsolatedAnalysisInstance._raw_call is operation
    assert calls == [(receiver, 'record-audit', {'rows': [1]}), (receiver, 'decode', {'rows': [1]})]
    assert [(row['name'], row['status']) for row in rows] == [
        ('rtl_analysis:record-audit', 'returned'), ('rtl_analysis:decode', 'raised')]
    assert all(row['elapsed_s'] >= 0 for row in rows)


def test_native_timing_does_not_change_independent_execution_requests(monkeypatch):
    from behave.sim.rtl.exact_job_context import RTLExactJobContext

    calls, rows = [], []
    sentinel = object()
    def source_trace(self, stimulus, **options):
        calls.append((self, stimulus, options))
        return sentinel
    monkeypatch.setattr(RTLExactJobContext, 'source_trace', source_trace)
    receiver, stimulus = object(), object()
    with command_stage_timings(rows, lambda: None):
        for _ in range(2):
            assert RTLExactJobContext.source_trace(receiver, stimulus, reuse=False) is sentinel
    assert RTLExactJobContext.source_trace is source_trace
    assert calls == [(receiver, stimulus, {'reuse': False})] * 2
    assert [(row['name'], row['status']) for row in rows] == [('rtl_source_trace', 'returned')] * 2


@pytest.mark.parametrize('failed', [False, True])
def test_worker_phase_tail_survives_success_and_budget_exit(monkeypatch, failed):
    from behave.sim.job_session import JobSession
    from behave.sim.rtl.analysis_transport import RTLAnalysisSession

    session = RTLAnalysisSession.__new__(RTLAnalysisSession)
    session.timings = []
    diagnostic = {'bytes': 100, 'truncated': False,
                  'tail': 'ANALYSIS_STAGE {"stage":["proof"],"event":"started"}\n'}
    monkeypatch.setattr(session, '_stderr_diagnostic',
        lambda: pytest.fail('diagnostics already captured by the transport owner'))
    failure = TimeoutError('owning Analysis budget')

    def exchange(self, operation, payload, timeout_s):
        self.timings.append({'op': operation, 'stderr': diagnostic})
        if failed:
            raise failure
        return 'original-answer'

    monkeypatch.setattr(JobSession, '_exchange', exchange)
    rows = []
    with command_stage_timings(rows, lambda: None):
        if failed:
            with pytest.raises(TimeoutError) as caught:
                session._exchange('query-start', {}, 600)
            assert caught.value is failure
        else:
            assert session._exchange('query-start', {}, 600) == 'original-answer'
    assert rows[0]['worker_diagnostic'] == diagnostic
    assert rows[0]['status'] == ('raised' if failed else 'returned')
    assert rows[0]['name'] == 'rtl_worker:query-start'
