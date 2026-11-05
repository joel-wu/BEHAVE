"""Final Eval and training settlement leaves raw Sim outcomes intact."""

from dataclasses import replace

import pytest

from behave.core.artifacts import BlobRef
from behave.eval.report import summarize_campaign as summarize
from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment
from behave.task_execution.result import TaskResult, final_score_from_record
from behave.train.slime_launch import preset
from behave.train.slime_rollout import candidate_credit
from behave.train.training_data import TrainingCandidate


def result_with(behavior, rtl):
    ref = BlobRef.of(b"fixture")
    judgment = SubmissionJudgment(ref, behavior, rtl, ref)
    return TaskResult("task", ref, ref, "attempt", ref, "final_action", ref,
                      judgment, None, None, ref)


@pytest.mark.parametrize("origin", ["reference", "verifier", "infrastructure", "resource-policy"])
@pytest.mark.parametrize("other", [None, 0, 1])
def test_unresolved_component_settles_zero_for_every_origin(origin, other):
    unresolved = ComponentJudgment(None, "failure", origin, retryable=True)
    behavior = None if other is None else ComponentJudgment(other, "decided")
    result = result_with(behavior, unresolved)
    before = result.to_bytes()
    expected = .5 if other == 1 else 0.
    assert result.score is None
    assert result.final_score == final_score_from_record(result.as_dict()) == expected
    assert TrainingCandidate(BlobRef.of(before), result).reward == expected
    assert result.to_bytes() == before
    assert result.judgment.rtl.retryable
    assert result.judgment.rtl.origin == origin


@pytest.mark.parametrize("stage", [None, "solver", "judge"])
def test_no_judgment_or_interruption_has_numeric_final_score(stage):
    result = result_with(None, ComponentJudgment(1, "passed"))
    changes = dict(judgment=None, failure_stage=stage,
                   failure_code=None if stage is None else "interrupted")
    if stage == "solver":
        changes.update(termination=None, submission=None)
    result = replace(result, **changes)
    assert result.score is None
    assert result.final_score == final_score_from_record(result.as_dict()) == 0.


def test_final_rates_include_all_requested_tasks_but_not_unrequested_artifacts():
    rows = [{"model": "fixture", "setting": "rtl_only", "dataset": "cvdp",
             "rtl": {"status": status, "score": score},
             "behavior": {"status": "not_requested", "score": None}, "coverage": None}
            for status, score in [("passed", 1), ("candidate_failed", 0), ("sim_no_conclusion", None)]]
    dataset = summarize(rows)["fixture/rtl_only"]["datasets"]["cvdp"]
    assert dataset["tasks"] == 3
    assert dataset["rtl"]["pass_rate"] == 1 / 3
    assert dataset["rtl"]["unknown"] == 1
    assert dataset["behavior"]["pass_rate"] is None


def test_eight_rollout_credit_keeps_unresolved_member_in_denominator():
    credit = candidate_credit([1.] * 7 + [None], ("task",) * 8, preset("grpo_no_std"))
    assert credit == [.125] * 7 + [-.875]
