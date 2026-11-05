"""Failure origin is fixed before aggregation and has one scoring consequence."""

import pytest

from behave.sim import api
from behave.sim.authority import CONCRETE, PARTIAL
from behave.sim.evaluation_policy import verdict
from behave.sim.core.catalogue import CatalogueManifest, Goal, SAFETY
from behave.sim.core.failure import ObservedGoals
from behave.sim.core.run_record import RunResult
from behave.sim.run_assessment import check_run

from .test_api_path import Doubling, _evaluate, _ref, contract, realization, schedule


class _CrashingCandidate(Doubling):

    def settle(self, presented, controls):
        raise RuntimeError("candidate code failed")


class _OutOfRangeCandidate(Doubling):

    def settle(self, presented, controls):
        got = super().settle(presented, controls)
        if got["out_valid"]:
            got["out_y"] = 1000
        return got


def test_failure_record_is_closed_canonical_and_policy_explicit():
    reason = api.Refusal("budget-exhausted", "the published budget expired",
                         seconds=3)
    failure = api.RunFailure.resource(
        "candidate-run", reason, policy="test-budget/1",
        disposition=api.FailureDisposition.SCORE_ZERO)

    image = failure.as_dict()
    assert api.RunFailure.from_dict(image).as_dict() == image
    assert image == {
        "origin": "resource-policy",
        "phase": "candidate-run",
        "disposition": "score-zero",
        "reason": {
            "kind": "budget-exhausted",
            "why": "the published budget expired",
            "detail": {"seconds": 3},
        },
        "policy": "test-budget/1",
    }
    with pytest.raises(ValueError, match="must name the published policy"):
        api.RunFailure(
            api.FailureOrigin.RESOURCE_POLICY, "candidate-run", reason,
            disposition=api.FailureDisposition.SCORE_ZERO)
    with pytest.raises(ValueError, match="candidate failures have disposition"):
        api.RunFailure(
            api.FailureOrigin.CANDIDATE, "candidate-run", reason,
            disposition=api.FailureDisposition.NO_RESULT)
    with pytest.raises(ValueError, match="unknown fields"):
        api.RunFailure.from_dict(dict(image, invented=True))


@pytest.mark.parametrize("artifact", [_CrashingCandidate(), _OutOfRangeCandidate()])
def test_candidate_fault_is_a_concrete_zero_not_a_missing_result(artifact):
    task = api.admit(contract(), realization(), reference=_ref())
    got = _evaluate(task, [schedule([3])], artifact, reference=_ref())

    assert got.complete and not got.passed
    assert len(got.candidate_failures) == 1
    assert got.no_result_failures == ()
    index, failure = got.candidate_failures[0]
    assert index == 0
    assert failure.disposition is api.FailureDisposition.SCORE_ZERO
    assert verdict(got).kind == CONCRETE
    assert got.as_evidence_dict()["failures"] == [[0, failure.as_dict()]]


def test_reference_fault_is_no_result_and_cannot_be_relabelled_concrete():
    class BrokenReference:

        def reset(self):
            pass

        def process(self, in_a):
            raise RuntimeError("reference code failed")

    reference = _ref(BrokenReference)
    task = api.admit(contract(), realization(), reference=reference)
    got = _evaluate(task, [schedule([3])], Doubling(), reference=reference)

    assert not got.complete and not got.passed
    assert got.candidate_failures == ()
    assert len(got.no_result_failures) == 1
    _, failure = got.no_result_failures[0]
    assert failure.origin is api.FailureOrigin.REFERENCE
    assert failure.disposition is api.FailureDisposition.NO_RESULT
    assert verdict(got).kind == PARTIAL


@pytest.mark.parametrize("origin", [api.RunFailure.candidate, api.RunFailure.verifier])
def test_interrupted_observations_survive_aggregation_without_inventing_a_trace(origin):
    stimulus = schedule([3])
    task = api.admit(contract(), realization(), reference=_ref())
    catalogue = CatalogueManifest([Goal("assertion", SAFETY)])
    failure = origin("execution", api.Refusal("stopped", "execution stopped"),
                     observed=ObservedGoals(stimulus.fingerprint, catalogue))
    restored = api.RunFailure.from_dict(failure.as_dict())
    assert restored.as_dict() == failure.as_dict()
    assessment = check_run(stimulus, restored)
    evaluation = api.ConcreteEvaluation(task, [assessment.result],
                                       hits=assessment.hits, catalogue=catalogue)
    assert evaluation.safety_hits == ("assertion",)
    assert evaluation.records == () and evaluation.accepted == 0
    assert evaluation.mismatch_count == 0 and not evaluation.passed
    assert verdict(evaluation).kind == (CONCRETE if origin == api.RunFailure.candidate else PARTIAL)


def test_failure_observations_bind_stimulus_and_original_goal_declarations():
    stimulus = schedule([3])
    catalogue = CatalogueManifest([Goal("assertion", SAFETY)])
    failure = api.RunFailure.candidate("execution", api.Refusal("stopped", "stopped"),
        observed=ObservedGoals(stimulus.fingerprint, catalogue))
    with pytest.raises(ValueError, match="another stimulus"):
        RunResult.failed_to_run(schedule([4]), failure)
    task = api.admit(contract(), realization(), reference=_ref())
    assessment = check_run(stimulus, failure)
    changed = CatalogueManifest([Goal("assertion", api.COVERAGE)])
    with pytest.raises(ValueError, match="another catalogue"):
        api.ConcreteEvaluation(task, [assessment.result], hits=assessment.hits, catalogue=changed)
    with pytest.raises(api.Refused, match="recorded-but-not-reported"):
        api.ConcreteEvaluation(task, [assessment.result], catalogue=catalogue)


def test_error_details_alone_do_not_create_goal_hits():
    stimulus = schedule([3])
    failure = api.RunFailure.candidate("execution", api.Refusal(
        "stopped", "arbitrary diagnostics are not an observation", goals=["assertion"]))
    assert check_run(stimulus, failure).hits == frozenset()
