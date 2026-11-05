"""One actual run can feed both search directions and final scoring."""

from dataclasses import replace

import pytest

from behave.sim import api, evaluation
from behave.sim.core.failure import FailureRaised, RunFailure
from behave.sim.deferred_generation import deferred_provider
from behave.sim.evaluation import ReplayExecution
from behave.sim.search_policy import SearchStopped
from tests.sim.test_api_path import Doubling, FirstOnlyDoubling, _ref, contract, realization, schedule
from tests.sim.test_command_handler import _BaselineOnly
from tests.support.device_boundary import prepare_device_command


def _execution(monkeypatch, *, gain=2):
    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    calls = []
    original = evaluation.run

    def run(task, stimulus, artifact, **kwargs):
        calls.append(stimulus)
        return original(task, stimulus, artifact, **kwargs)

    monkeypatch.setattr(evaluation, "run", run)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    return ReplayExecution(task, Doubling(gain), reference, catalogue), calls


@pytest.mark.parametrize("gain", [2, 3])
def test_checked_seed_and_new_input_are_scored_once(monkeypatch, gain):
    execution, calls = _execution(monkeypatch, gain=gain)
    first, second = schedule([2, 3], drain=2), schedule([4, 5], drain=2)
    trace, = execution.traces((first,))
    assert execution.traces((first, first)) == (trace, trace)
    execution.traces((second,))
    compared = execution.evaluate((first, second))
    assert calls == [first, second]
    assert compared.complete and compared.passed is (gain == 2)
    assert compared.results[0].trace is trace
    assert compared.execution_cost["reuse"]["completed_runs"] == 2
    assert all(cost == 0 for cost in compared.execution_cost["work_seconds"].values())

    # Independent execution remains the differential oracle, not a cached self-comparison.
    fresh = evaluation.evaluate(execution.task, (first, second), Doubling(gain),
                                execution.reference, catalogue=execution.catalogue)
    assert [run.as_dict() for run in compared.results] == [run.as_dict() for run in fresh.results]
    assert compared.hits == fresh.hits
    assert len(calls) == 4


@pytest.mark.parametrize("gain", [2, 3])
def test_trace_read_does_not_reassemble_completed_evaluations(monkeypatch, gain):
    execution, calls = _execution(monkeypatch, gain=gain)
    first, second = schedule([2, 3], drain=2), schedule([4, 5], drain=2)
    baseline = execution.evaluate((first, second))
    expected = tuple(row.trace for row in baseline.results)

    def no_more_work(*_args, **_kwargs):
        pytest.fail("reading completed traces must not reassemble a score or replay")

    monkeypatch.setattr(execution, "evaluate", no_more_work)
    assert execution.traces((second, first, second)) == (expected[1], expected[0], expected[1])
    assert calls == [first, second]
    assert execution.has_failure_witness is (gain == 3)


@pytest.mark.parametrize("gain", [2, 3])
def test_trace_read_assesses_only_new_members(monkeypatch, gain):
    execution, calls = _execution(monkeypatch, gain=gain)
    first, second = schedule([2, 3], drain=2), schedule([4, 5], drain=2)
    baseline = execution.evaluate((first,))
    evaluate = execution.evaluate
    offered = []

    def assess_new(stimuli, **kwargs):
        offered.append(tuple(stimuli))
        return evaluate(stimuli, **kwargs)

    monkeypatch.setattr(execution, "evaluate", assess_new)
    traces = execution.traces((first, second, second, first))
    assert offered == [(second,)]
    assert traces[0] is traces[3] is baseline.results[0].trace
    assert traces[1] is traces[2]
    assert calls == [first, second]
    assert execution.has_failure_witness is (gain == 3)


@pytest.mark.parametrize("origin", ["candidate", "reference", "verifier", "infrastructure"])
def test_trace_read_keeps_completed_failures_without_retry(monkeypatch, origin):
    execution, _ = _execution(monkeypatch)
    seed = schedule([3], drain=2)
    failure = getattr(RunFailure, origin)("test", api.Refusal("unavailable", "test defect"))
    monkeypatch.setattr(evaluation, "run", lambda *_a, **_k: failure)
    result = execution.evaluate((seed,))

    def no_retry(*_args, **_kwargs):
        pytest.fail("reading a completed failure must not re-evaluate it")

    monkeypatch.setattr(execution, "evaluate", no_retry)
    assert execution.traces((seed, seed)) == (failure.reason, failure.reason)
    assert execution.has_failure_witness is (result.failure_witness is not None)


def test_trace_read_still_obeys_scoring_cancellation(monkeypatch):
    from behave.sim.scoring_policy import ScoringPolicy, ScoringStopped, scoring_scope

    execution, calls = _execution(monkeypatch)
    seed = schedule([3], drain=2)
    execution.evaluate((seed,))

    with scoring_scope(ScoringPolicy(time_budget_s=0)), pytest.raises(ScoringStopped):
        execution.traces((seed,))
    assert calls == [seed]


def test_trace_read_does_not_skip_an_unfinished_assessment_aggregation(monkeypatch):
    execution, calls = _execution(monkeypatch, gain=3)
    seed = schedule([3], drain=2)
    assemble = evaluation._assemble

    def interrupted(*_args, **_kwargs):
        raise SearchStopped("time-budget")

    monkeypatch.setattr(evaluation, "_assemble", interrupted)
    with pytest.raises(SearchStopped):
        execution.evaluate((seed,))
    assert calls == [seed] and not execution.has_failure_witness
    # A committed per-run assessment is not yet a successfully assembled result.
    with pytest.raises(SearchStopped):
        execution.traces((seed,))
    monkeypatch.setattr(evaluation, "_assemble", assemble)
    trace, = execution.traces((seed,))
    assert trace is not None and execution.has_failure_witness
    assert calls == [seed]  # Finish the check; do not execute the seed again.


@pytest.mark.parametrize("runtime", (False, True))
@pytest.mark.parametrize("gain", (2, 3))
def test_only_unfinished_work_enters_cpu_dispatch(monkeypatch, runtime, gain):
    execution, calls = _execution(monkeypatch, gain=gain)
    if runtime:
        seeds = tuple(api.Stimulus(queues={"in": [(value,)]},
                                  replay_policy=api.ReplayPolicy(2, 4, 3))
                      for value in (2, 5))
    else:
        seeds = schedule([2], drain=2), schedule([5], drain=2)
    first = execution.evaluate(seeds[:1])
    dispatches = []
    original = evaluation.ordered_map

    def dispatch(function, values, workers, **kwargs):
        values = tuple(values)
        assert seeds[0] not in values, "completed evidence must not wait for a CPU permit"
        if values:
            dispatches.append(values)
        return original(function, values, workers, **kwargs)

    monkeypatch.setattr(evaluation, "ordered_map", dispatch)
    combined = execution.evaluate(seeds)
    assert dispatches == [(seeds[1],)] * (2 if runtime else 1)
    assert calls == list(seeds)
    assert combined.results[0] is first.results[0]
    assert combined.complete and combined.passed is (gain == 2)
    dispatches.clear()
    repeated = execution.evaluate(seeds)
    assert not dispatches and calls == list(seeds)
    assert repeated.results == combined.results and repeated.hits == combined.hits


@pytest.mark.parametrize("gain", [2, 3])
def test_search_stop_reads_completed_scoring_without_another_evaluation(monkeypatch, gain):
    execution, calls = _execution(monkeypatch, gain=gain)
    seed = schedule([3], drain=2)
    result = execution.evaluate((seed,))
    assert execution.has_failure_witness is (gain == 3)

    def no_more_work(*_args, **_kwargs):
        pytest.fail("reading the search stop must not re-evaluate or serialize records")

    monkeypatch.setattr(evaluation, "evaluate", no_more_work)
    monkeypatch.setattr(api.ConcreteEvaluation, "as_evidence_dict", no_more_work)
    for _ in range(20):
        assert execution.has_failure_witness is (result.failure_witness is not None)
    assert calls == [seed]
    execution.release()
    assert not execution.has_failure_witness


@pytest.mark.parametrize("origin", ["reference", "verifier", "infrastructure"])
@pytest.mark.parametrize("negative_first", [False, True])
def test_search_stop_does_not_forget_failures_from_other_subsets(monkeypatch, origin, negative_first):
    execution, calls = _execution(monkeypatch, gain=3)
    bad, unavailable = schedule([3], drain=2), schedule([4], drain=2)
    original = evaluation.run

    def replay(task, stimulus, artifact, **kwargs):
        if stimulus == unavailable:
            calls.append(stimulus)
            return getattr(RunFailure, origin)("test", api.Refusal("unavailable", "test defect"))
        return original(task, stimulus, artifact, **kwargs)

    monkeypatch.setattr(evaluation, "run", replay)
    for seed in ((bad, unavailable) if negative_first else (unavailable, bad)):
        execution.evaluate((seed,))
    assert execution.has_failure_witness is (origin == "infrastructure")
    combined = execution.evaluate((bad, unavailable))
    assert execution.has_failure_witness is (combined.failure_witness is not None)
    assert len(calls) == 2


def test_sample_only_batch_cannot_supply_a_search_stop(monkeypatch):
    from behave.sim.core.run_record import VERIFIED_SAMPLE

    execution, _ = _execution(monkeypatch, gain=3)
    seed = schedule([3], drain=2)
    execution.evaluate((seed,), telemetry={"batch": {"verified": VERIFIED_SAMPLE}})
    assert not execution.has_failure_witness


def test_runtime_continuous_phase_is_a_new_execution(monkeypatch):
    execution, calls = _execution(monkeypatch)
    policy = api.ReplayPolicy(2, 4, 3)
    seeds = tuple(api.Stimulus(queues={"in": [(i,)] * 100}, replay_policy=policy)
                  for i in (1, 2))
    first = execution.evaluate(seeds)
    suite = api.build_replay_suite(execution.task.contract, seeds)
    final = execution.evaluate(suite)
    assert first.complete and final.complete and final.passed
    assert calls == list(suite.stimuli)
    assert all(final.results[i] is first.results[i] for i in range(2))
    assert final.results[-1].trace.accepted_count(["in"]) == 200
    assert final.execution_cost["reuse"]["completed_runs"] == 2

    changed = api.Stimulus(queues=seeds[0].queues,
                          replay_policy=replace(policy, drain_slots=5))
    execution.evaluate((changed,))
    assert calls[-1] == changed and len(calls) == 4


def test_failed_comparison_is_retained_without_an_implicit_retry(monkeypatch):
    execution, calls = _execution(monkeypatch)
    seed = schedule([3], drain=2)
    original = evaluation.compare_run
    comparisons = []

    def broken(*_args):
        comparisons.append(seed)
        raise FailureRaised(RunFailure.reference(
            "comparison", api.Refusal("test-reference", "reference unavailable")))

    monkeypatch.setattr(evaluation, "compare_run", broken)
    before = execution.evaluate((seed,))
    assert not before.complete and not before.passed
    monkeypatch.setattr(evaluation, "compare_run", original)
    after = execution.evaluate((seed,))
    assert not after.complete and not after.passed
    assert after.results[0] is before.results[0]
    assert calls == [seed]
    assert comparisons == [seed]
    assert after.results[0].trace is before.results[0].trace
    assert after.execution_cost["reuse"]["completed_runs"] == 1
    assert after.execution_cost["work_seconds"]["replay"] == 0
    assert after.execution_cost["work_seconds"]["monitor"] == 0
    assert after.execution_cost["work_seconds"]["comparison"] == 0


def test_feedback_reads_native_hits_without_replay_or_selection(monkeypatch):
    execution, calls = _execution(monkeypatch)
    seed = schedule([3], drain=2)
    result = execution.evaluate((seed,))
    feedback, = execution.feedback((seed,), execution.catalogue)
    assert feedback.hits == result.hits
    assert feedback.record == result.results[0].trace.fingerprint
    assert feedback.catalogue_digest == execution.catalogue.digest()
    assert calls == [seed]

    from .test_generation_scheduler import _frontend_catalogue
    with pytest.raises(api.Refused, match="catalogue-baseline-feedback-unknown-goal"):
        execution.feedback((seed,), _frontend_catalogue())


def test_no_result_execution_is_retained_without_an_implicit_retry(monkeypatch):
    execution, calls = _execution(monkeypatch)

    def broken(_task, stimulus, _artifact, **_kwargs):
        calls.append(stimulus)
        raise api.Refused(api.Refusal("native-events-unscheduled-trigger", "observer unavailable"))

    monkeypatch.setattr(evaluation, "run", broken)
    seed = schedule([3], drain=2)
    before = execution.evaluate((seed,))
    after = execution.evaluate((seed,))
    assert not before.complete and not after.complete
    assert after.results[0] is before.results[0]
    assert calls == [seed]


def test_a_completed_candidate_fault_is_not_retried(monkeypatch):
    execution, calls = _execution(monkeypatch)

    def broken(task, stimulus, artifact, **_kwargs):
        calls.append(stimulus)
        raise FailureRaised(RunFailure.candidate(
            "candidate", api.Refusal("test-candidate", "invalid candidate")))

    monkeypatch.setattr(evaluation, "run", broken)
    seed = schedule([3], drain=2)
    before = execution.evaluate((seed,))
    after = execution.evaluate((seed,))
    assert calls == [seed]
    assert not after.passed and after.results[0].determines_score
    assert after.results[0] is before.results[0]


def test_execution_records_cannot_cross_artifacts(monkeypatch):
    execution, calls = _execution(monkeypatch)
    seed = schedule([3], drain=2)
    execution.traces((seed,))
    with pytest.raises(ValueError, match="another invocation"):
        evaluation.evaluate(execution.task, (seed,), Doubling(3), execution.reference,
                            catalogue=execution.catalogue, _execution=execution)
    assert calls == [seed]


def test_search_cancellation_keeps_completed_checks_but_not_unfinished_execution(monkeypatch):
    execution, calls = _execution(monkeypatch)
    first, second = schedule([2], drain=2), schedule([3], drain=2)
    original = evaluation.run

    def interrupted(task, stimulus, artifact, **kwargs):
        if stimulus == second:
            raise SearchStopped("time-budget")
        return original(task, stimulus, artifact, **kwargs)

    monkeypatch.setattr(evaluation, "run", interrupted)
    with pytest.raises(SearchStopped):
        execution.evaluate((first, second))
    assert calls == [first]
    monkeypatch.setattr(evaluation, "run", original)
    final = execution.evaluate((first, second))
    assert final.complete and final.passed
    assert calls == [first, second]
    assert final.execution_cost["reuse"]["completed_runs"] == 1


@pytest.mark.isolation
def test_expired_reference_startup_does_not_cache_an_infrastructure_failure(monkeypatch):
    from behave.isolation import linux
    from behave.sim.search_policy import SearchBudget, SearchPolicy, active_search, search_scope
    import time

    source = b'''class Model:
    def reset(self):
        pass
    def process(self, in_a):
        return {"out_y": (in_a * 2) & 255}
'''
    c = contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': source}, config={'class': 'Model'})
    task = api.admit(c, realization(), reference=reference)
    execution = ReplayExecution(task, Doubling(), reference,
        api.evaluation_manifest(task, api.CatalogueManifest(())))
    first, second = schedule([2], drain=2), schedule([3], drain=2)
    baseline = execution.evaluate((first,))
    assert baseline.complete and baseline.passed
    read_line, interrupted = linux._StatusReader.read_line, []

    def expire_start(reader, timeout):
        lease = active_search()
        if lease is not None and not interrupted:
            interrupted.append(True)
            lease.deadline = time.monotonic() - 1
            raise TimeoutError('the namespace status did not arrive before the deadline')
        return read_line(reader, timeout)

    monkeypatch.setattr(linux._StatusReader, 'read_line', expire_start)
    lease = SearchBudget(SearchPolicy(time_budget_s=30)).direction(1)
    with search_scope(lease), pytest.raises(SearchStopped) as caught:
        execution.evaluate((second,))
    assert interrupted and caught.value.cleanup_confirmed
    assert second.identity_bytes not in execution._assessments
    assert second.identity_bytes not in execution._settled
    # Search ends, then the ordinary final check completes the same saved input.
    # Completed baseline evidence is reused; no failed record is overwritten.
    final = execution.evaluate((first, second))
    assert final.complete and final.passed and not final.no_result_failures
    assert final.results[0] is baseline.results[0]


def test_reuse_preserves_safety_failure_separately_from_matching_outputs(monkeypatch):
    execution, calls = _execution(monkeypatch)
    execution = ReplayExecution(execution.task, FirstOnlyDoubling(),
                                execution.reference, execution.catalogue)
    seed = schedule([3, 5], drain=3)
    trace, = execution.traces((seed,))
    final = execution.evaluate((seed,))
    assert calls == [seed]
    assert final.results[0].trace is trace
    assert final.mismatch_count == 0
    assert final.safety_hits == ("monitor.input-completion",)
    assert not final.passed


def test_public_command_shares_records_between_deferred_searches_and_final_score(tmp_path, monkeypatch):
    execution, calls = _execution(monkeypatch)
    spec = api.GenerationSpec(12, 2, 2, 2)
    baseline = api.build_baseline(execution.task.contract, spec,
                                 policy=api.BaselinePolicy(case_limit=2, max_queue_depth=2))
    observations, owners = [], []

    def recipe(name):
        def prepare(*, execution, baseline):
            owners.append(execution)
            observations.append(execution.traces(baseline))
            provider = _BaselineOnly()
            provider.name = name
            return provider
        return deferred_provider(name, _BaselineOnly.policy, {"source": name}, prepare)

    request = prepare_device_command(
        execution.task, execution.artifact, run_directory=tmp_path / "command",
        baseline=baseline, providers=(recipe("reference"), recipe("candidate")),
        generation_spec=spec, probe_budget=1, run_analysis=False, reference=execution.reference)
    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.complete and result.evaluation.passed
    assert observations[0] == observations[1]
    assert calls == list(baseline.stimuli)
    assert result.evaluation.execution_cost["reuse"]["completed_runs"] == len(calls)
    assert owners[0] is owners[1]
    assert not owners[0]._checked and not owners[0]._assessments and not owners[0]._completion
    assert result.evaluation.results[0].trace is observations[0][0]
