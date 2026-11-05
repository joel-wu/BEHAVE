"""A failed monitor cannot erase the original reason or qualify raw hits."""

from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.analysis import _analysis_subject_from_wire
from behave.sim.core.catalogue import COVERAGE, CatalogueManifest, Goal
from behave.sim.core.monitor import MonitorReport, Violation
from behave.sim.evaluation import ReplayExecution
from behave.sim.generation_feedback import ReplayFeedback
from behave.sim.run_assessment import RunAssessment, assess_run

from .test_api_path import Doubling, _ref, contract, in_memory, realization, schedule


def _fixture():
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    stimulus = schedule([1, 2])
    original = in_memory(task, stimulus, Doubling())
    records = list(original.records)
    first = records[0]
    records[0] = api.SlotRecord(
        accepted=first.accepted, state=first.state, environment=first.environment,
        outputs=first.outputs, monitor=first.monitor, hits={"source-hit": True})
    trace = api.AuditedTrace(stimulus, records=records,
        initial_state=dict(original.initial_state), completion=original.completion,
        domain=original.domain, artifact=original.artifact)
    catalogue = CatalogueManifest((Goal("source-hit", COVERAGE, provider="behavior"),))
    return task, stimulus, trace, reference, catalogue


@pytest.mark.parametrize("mode", ["refusal", "raised", "exception", "outside"])
def test_unchecked_observations_preserve_failure_and_do_not_feed_search(mode, monkeypatch):
    task, stimulus, trace, reference, catalogue = _fixture()
    failure = api.RunFailure.verifier("trace-monitor", api.Refusal("original", "original cause"))

    class Monitor:
        def check(self, *args, **kwargs):
            if mode == "refusal":
                raise api.Refused(failure.reason)
            if mode == "raised":
                raise api.FailureRaised(failure)
            if mode == "exception":
                raise RuntimeError("original cause")
            return MonitorReport(assumption_violations=[
                Violation("environment.invalid-input", 0, "in", "outside declared domain")])

    def must_not_compare(*args, **kwargs):
        pytest.fail("an unchecked observation must not enter reference comparison")

    assessment = assess_run(task, stimulus, trace, reference,
                            monitor=Monitor(), compare=must_not_compare)
    result = assessment.result
    assert result.trace is trace and not result.observations_eligible
    assert result.as_dict()["observations_eligible"] is False
    assert not assessment.hits
    expected = {"refusal": "original", "raised": "original",
                "exception": "verifier-monitor-failed", "outside": "environment-assumption-violated"}
    assert result.refused.reason.kind == expected[mode]
    evaluation = api.ConcreteEvaluation(task, (result,), catalogue=catalogue)
    assert evaluation.no_result_failures == ((0, result.refused),)
    assert not evaluation.passed and not evaluation.hits
    assert evaluation.results[0].trace.records[0].hits == (("source-hit", True),)

    execution = ReplayExecution(task, None, reference, catalogue)
    execution._assessments[stimulus.identity_bytes] = assessment
    monkeypatch.setattr(execution, "evaluate", lambda _: evaluation)
    assert execution.traces((stimulus,)) == (result.refused.reason,)
    assert execution.feedback((stimulus,), catalogue) == (result.refused.reason,)

    # A diagnostic hit cannot be promoted by supplying it beside the result.
    with pytest.raises(api.Refused, match="evaluation-hit-no-run-recorded"):
        api.ConcreteEvaluation(task, (result,), hits=("source-hit",), catalogue=catalogue)
    # Raw trace/catalogue shape checks remain in force even outside the domain.
    with pytest.raises(api.Refused, match="evaluation-recorded-hit-outside-the-catalogue"):
        api.ConcreteEvaluation(task, (result,), catalogue=CatalogueManifest(()))

    image = {"concrete": evaluation.fingerprint, "evaluation": evaluation.as_evidence_dict(),
             "raw_records": [{"stimulus": stimulus.as_dict(), "trace": trace.as_dict()}]}
    restored = _analysis_subject_from_wire(task, image, catalogue)
    assert not restored.results[0].observations_eligible
    assert restored.results[0].trace.fingerprint == trace.fingerprint


def test_checked_trace_keeps_its_hits_when_reference_comparison_fails():
    task, stimulus, trace, reference, catalogue = _fixture()
    failure = api.RunFailure.reference("comparison", api.Refusal("reference-error", "reference failed"))

    def fail(*args, **kwargs):
        raise api.FailureRaised(failure)

    assessment = assess_run(task, stimulus, trace, reference,
                            monitor=SimpleNamespace(check=lambda *a, **k: MonitorReport()), compare=fail)
    assert assessment.result.observations_eligible and assessment.hits == {"source-hit"}
    evaluation = api.ConcreteEvaluation(task, (assessment.result,), hits=assessment.hits,
                                        catalogue=catalogue)
    assert evaluation.no_result_failures == ((0, failure),)
    execution = ReplayExecution(task, None, reference, catalogue)
    execution._assessments[stimulus.identity_bytes] = assessment
    feedback, = execution.feedback((stimulus,), catalogue)
    assert type(feedback) is ReplayFeedback and feedback.hits == ("source-hit",)
    with pytest.raises(api.Refused, match="evaluation-hit-recorded-but-not-reported"):
        api.ConcreteEvaluation(task, (assessment.result,), catalogue=catalogue)


def test_mixed_valid_and_unchecked_runs_do_not_promote_or_erase_valid_hits():
    task, stimulus, trace, reference, catalogue = _fixture()
    first = assess_run(task, stimulus, trace, reference)
    other = schedule([3])
    raw = in_memory(task, other, Doubling())
    refused = api.RunFailure.verifier("trace-monitor", api.Refusal("outside", "not admitted"))
    second = RunAssessment(api.RunResult.not_compared(other, raw, refused, observations_eligible=False))
    evaluation = api.ConcreteEvaluation(task, (first.result, second.result),
                                        hits=first.hits, catalogue=catalogue)
    assert evaluation.hits == ("source-hit",) and not evaluation.passed
    assert evaluation.no_result_failures == ((1, refused),)
