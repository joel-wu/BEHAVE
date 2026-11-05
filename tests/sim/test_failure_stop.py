"""Negative decisions retain a checked witness and honest unfinished membership."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.authority import CONCRETE, Authoritative
from behave.sim.core.failure import FailureDisposition, RunFailure
from behave.sim.core.outcome import ConcreteEvaluation
from behave.sim.core.run_record import VERIFIED_SAMPLE, RunResult
from behave.sim.evaluation_policy import verdict
from tests.support.device_boundary import prepare_device_command

from .test_api_path import Doubling, _evaluate, _ref, contract, realization, schedule
from .test_command_handler import _BaselineOnly


def evaluate(device, *, stop=True):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    stimuli = tuple(schedule([value], drain=2) for value in (3, 5, 7, 9))
    return _evaluate(task, stimuli, device, reference=reference, stop_on_failure=stop)


@pytest.mark.parametrize("bad", (False, True))
@pytest.mark.parametrize("stop", (False, True))
def test_completed_mismatch_stops_only_when_requested(bad, stop):
    got = evaluate(Doubling(gain=3 if bad else 2), stop=stop)
    expected = 1 if bad and stop else 4
    assert len(got.results) == expected
    assert len(got.stimuli) == 4
    assert len(got.unexecuted) == 4 - expected
    assert got.complete is (expected == 4)
    assert verdict(got).authoritative
    assert verdict(got).passed is not bad
    assert (got.failure_witness is not None) is bad


def test_unexecuted_members_without_negative_evidence_cannot_pass_or_fail():
    complete = evaluate(Doubling())
    partial = ConcreteEvaluation(complete.task, complete.results[:1],
        catalogue=complete.catalogue, requested_stimuli=complete.stimuli)
    assert not partial.complete and partial.failure_witness is None
    assert not verdict(partial).authoritative
    with pytest.raises(api.Refused, match="authority-evidence-not-whole"):
        Authoritative(CONCRETE, evaluation=partial)


def test_an_empty_run_does_not_discard_later_accepted_inputs():
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    stimuli = (schedule([], drain=2), schedule([3], drain=2))
    got = _evaluate(task, stimuli, Doubling(), reference=reference, stop_on_failure=True)
    assert len(got.results) == 2 and got.complete and got.passed
    assert not got.unexecuted and got.failure_witness is None


def test_sample_verification_cannot_turn_a_negative_subset_into_a_score():
    got = evaluate(Doubling(gain=3))
    sampled = ConcreteEvaluation(got.task, got.results, hits=got.hits,
        catalogue=got.catalogue, requested_stimuli=got.stimuli,
        telemetry={"batch": {"verified": VERIFIED_SAMPLE}})
    assert sampled.failure_witness is None
    assert not verdict(sampled).authoritative


@pytest.mark.parametrize("origin", ("reference", "verifier", "infrastructure", "resource"))
def test_no_result_is_not_itself_a_negative_witness(origin):
    got = evaluate(Doubling())
    reason = api.Refusal("test-unavailable", "no completed candidate judgment")
    failure = (RunFailure.resource("test", reason, policy="test/1",
                                  disposition=FailureDisposition.NO_RESULT)
               if origin == "resource" else getattr(RunFailure, origin)("test", reason))
    partial = ConcreteEvaluation(got.task,
        [RunResult.failed_to_run(got.stimuli[0], failure)], catalogue=got.catalogue,
        requested_stimuli=got.stimuli)
    assert partial.failure_witness is None
    assert not verdict(partial).authoritative


def test_independent_infrastructure_failure_does_not_erase_a_checked_counterexample():
    got = evaluate(Doubling(gain=3))
    failed = RunResult.failed_to_run(got.stimuli[1], RunFailure.infrastructure(
        "test", api.Refusal("test-unavailable", "another worker failed")))
    combined = ConcreteEvaluation(got.task, [got.results[0], failed], hits=got.hits,
        catalogue=got.catalogue, requested_stimuli=got.stimuli)
    assert not combined.complete
    assert verdict(combined).authoritative and not verdict(combined).passed
    assert combined.failure_witness == 0


@pytest.mark.parametrize("origin", ("reference", "verifier"))
def test_known_reference_or_verifier_defect_prevents_a_negative_decision(origin):
    got = evaluate(Doubling(gain=3))
    failure = getattr(RunFailure, origin)(
        "test", api.Refusal("test-invalid-judgment", "known evaluation defect"))
    combined = ConcreteEvaluation(got.task,
        [got.results[0], RunResult.failed_to_run(got.stimuli[1], failure)],
        hits=got.hits, catalogue=got.catalogue, requested_stimuli=got.stimuli)
    assert combined.failure_witness is None
    assert not verdict(combined).authoritative


@pytest.mark.parametrize("stop", (False, True))
def test_preflight_failure_is_retained_without_retry_or_more_scoring(monkeypatch, stop):
    from behave.sim import evaluation, runtime_execution

    from .test_execution_reuse import _execution

    execution, _ = _execution(monkeypatch)
    seeds = tuple(api.Stimulus(queues={"in": [(value,)]},
        replay_policy=api.ReplayPolicy(2, 4, 3)) for value in (1, 2, 3))
    reference_failure = RunFailure.reference(
        "completion", api.Refusal("test-reference", "reference unavailable"))
    planned = []

    def completion_plan(_task, seed, _reference):
        planned.append(seed)
        if seed == seeds[1]:
            raise api.FailureRaised(reference_failure)

    monkeypatch.setattr(runtime_execution, "completion_plan", completion_plan)
    replayed = []

    def negative(_task, seed, _artifact, **_kwargs):
        replayed.append(seed)
        return RunFailure.candidate("test", api.Refusal("test-candidate", "candidate fault"))

    monkeypatch.setattr(evaluation, "run", negative)
    first = execution.evaluate(seeds, stop_on_failure=stop)
    second = execution.evaluate(seeds, stop_on_failure=stop)
    # Planning already established a reference defect. Normal scoring stops
    # before dispatch; diagnostic replay may continue but cannot score zero.
    assert replayed == ([] if stop else [seeds[0], seeds[2]])
    assert planned == list(seeds)  # The retained failed attempt is not planned again.
    assert first.results == second.results
    assert len(first.results) == (1 if stop else 3)
    assert first.unexecuted == ((seeds[0], seeds[2]) if stop else ())
    assert next(run for run in first.results if run.stimulus == seeds[1]).run_failure \
        is reference_failure
    assert first.failure_witness is None
    assert not verdict(first).authoritative


def test_existing_explicit_resource_zero_policy_is_a_negative_witness():
    got = evaluate(Doubling())
    failure = RunFailure.resource("test", api.Refusal("budget", "execution exhausted"),
                                  policy="test/1", disposition=FailureDisposition.SCORE_ZERO)
    partial = ConcreteEvaluation(got.task,
        [RunResult.failed_to_run(got.stimuli[0], failure)], catalogue=got.catalogue,
        requested_stimuli=got.stimuli)
    assert verdict(partial).authoritative and not verdict(partial).passed


@pytest.mark.parametrize("kind,reached", [(api.SAFETY, True), (api.SAFETY, False),
                                          (api.SAFETY, None), (api.COVERAGE, True)])
@pytest.mark.parametrize("eligible", [False, True])
def test_interrupted_trace_needs_a_positive_eligible_safety_hit(kind, reached, eligible):
    got = evaluate(Doubling())
    original = got.results[0].trace
    goal = api.Goal("test.native-event", kind, family="test", provider="test",
                    event="native-event", policy="test/1")
    records = [api.SlotRecord(accepted=row.accepted, state=row.state,
        outputs=row.outputs, environment=row.environment, monitor=row.monitor,
        hits={**dict(row.hits), goal.id: reached if index == 0 else False})
        for index, row in enumerate(original.records)]
    trace = api.AuditedTrace(original.stimulus, initial_state=original.initial_state,
        records=records, completion=api.Completion.PADDED,
        artifact=original.artifact, domain=original.domain)
    result = (RunResult.compared(trace.stimulus, trace, api.compare_run(got.task, _ref(), trace))
              if eligible else RunResult.not_compared(trace.stimulus, trace,
                  RunFailure.infrastructure("test", api.Refusal("test-unchecked", "not validated")),
                  observations_eligible=False))
    partial = ConcreteEvaluation(got.task, [result],
        hits=(goal.id,) if eligible and reached else (),
        catalogue=api.CatalogueManifest((*got.catalogue.values(), goal)),
        requested_stimuli=got.stimuli)
    negative = eligible and kind == api.SAFETY and reached is True
    assert (partial.failure_witness is not None) is negative
    assert verdict(partial).authoritative is negative
    assert not partial.complete
    if negative:
        assert not verdict(partial).passed
        assert partial.failure_witness == 0
        # A known problem with the reference or verifier still blocks zero;
        # retaining a safety bit does not make those defects disappear.
        for origin in ("reference", "verifier"):
            failed = RunResult.failed_to_run(got.stimuli[1], getattr(RunFailure, origin)(
                "test", api.Refusal("test-invalid", "known defect")))
            invalid = ConcreteEvaluation(got.task, [result, failed], hits=partial.hits,
                catalogue=partial.catalogue, requested_stimuli=got.stimuli)
            assert invalid.failure_witness is None
            assert not verdict(invalid).authoritative


def test_requested_membership_cannot_be_swapped_or_reordered():
    got = evaluate(Doubling(gain=3))
    with pytest.raises(ValueError, match="membership and order"):
        ConcreteEvaluation(got.task, got.results, catalogue=got.catalogue,
                           requested_stimuli=got.stimuli[1:])


def test_public_command_persists_negative_without_search_or_continuous_replay(tmp_path):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    request = prepare_device_command(task, Doubling(gain=3),
        run_directory=tmp_path / "run", reference=reference,
        baseline=api.BaselinePolicy(case_limit=5, max_queue_depth=100),
        providers=(_BaselineOnly(),), generation_spec=None, search_policy=api.SearchPolicy(),
        run_analysis=False)
    result = api.handle_command(request)
    assert result.decision.authoritative and result.decision.passed is False, result.as_dict()
    assert not result.evaluation.complete
    assert result.evaluation.unexecuted
    assert len(result.evaluation.results) < len(result.evaluation.stimuli)
    assert result.generated.search_summary["stop_reason"] == "baseline-failed"
    assert result.evaluation.execution_cost["reuse"]["completed_runs"] == len(result.evaluation.results)
    saved = json.loads(Path(result.record_path).read_text())
    assert saved["terminal"] is True
    image = result.evaluation.as_dict()
    assert image["complete"] is False and image["passed"] is False
    assert image["unexecuted_stimuli"]
    assert image["failure_witness"] is not None


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
@pytest.mark.parametrize("workers", (1, 2))
def test_production_command_stops_after_a_real_checked_mismatch(tmp_path, monkeypatch, kind, workers):
    from .test_baseline_public_sequence import REFERENCE, RTL, _contract

    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", str(workers))
    c = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    if kind == "behavior":
        text = REFERENCE.replace('return {"y": a}', 'return {"y": (a + 1) & 255}')
        path = tmp_path / "candidate.py"
        path.write_text(text)
        task = api.admit(c, api.realization_of_behavior_source(text, c), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path))
    else:
        text = RTL.replace("VALUE", "a + 8'd1")
        path = tmp_path / "candidate.sv"
        path.write_text(text)
        task = api.admit(c, api.realization_of_rtl_source(text, top="M", contract=c), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "result", reference=reference,
        baseline=api.BaselinePolicy(case_limit=8, max_queue_depth=100),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False))
    assert result.decision.authoritative and result.decision.passed is False, result.as_dict()
    evaluated = result.evaluation
    assert not evaluated.complete and evaluated.unexecuted
    assert evaluated.mismatch_count > 0 and evaluated.failure_witness is not None
    assert 1 <= len(evaluated.results) <= workers
    assert len(evaluated.stimuli) > workers
    assert all(row["runs"] == 0 for row in evaluated.execution_cost["phases"]
               if row["phase"] == "continuous")
    assert evaluated.execution_cost["reuse"]["completed_runs"] == len(evaluated.results)
