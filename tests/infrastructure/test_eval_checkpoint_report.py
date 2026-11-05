"""Checkpoint reports retain requested denominators and separate component scores."""


import pytest

from tools.eval_checkpoint_report import comparison
from tools.eval_review_trial import write_json


def setup_experiment(root):
    write_json(root / "experiment.json", {"updates": 20, "arms": ["baseline", "trained"],
        "tasks": ["passes", "half", "not-submitted", "unavailable"]})
    write_json(root / "configuration.json", {"setting": "joint"})


def part(score):
    return {"score": score, "code": "test", "origin": "verifier" if score is None else None}


def record(root, arm, task, status, *, scores=None, failure=None, missing_result=False):
    directory = root / arm
    value = {"judgment": None if scores is None else {"rtl": part(scores[0]), "behavior": part(scores[1])},
             "submission": None if scores is None else {}, "failure_stage": "solver" if failure else None,
             "failure_code": failure}
    reference = None if missing_result else {"sha256": task}
    if reference is not None:
        write_json(directory / "run/candidates" / task / "objects" / task, value)
    write_json(directory / "run/report" / f"{task}.json", {"kind": "evaluation_case", "payload": {
        "row": {"task_id": task, "run_key": task, "status": status, "result": reference,
                "error": {"code": "worker-failed"} if missing_result else None}}})


@pytest.mark.parametrize("missing_result", [False, True])
def test_final_unresolved_and_unsubmitted_scores_are_zero_with_full_denominator(tmp_path, missing_result):
    setup_experiment(tmp_path)
    for arm in ("baseline", "trained"):
        record(tmp_path, arm, "passes", "scored", scores=(1, 1))
        record(tmp_path, arm, "half", "no_result", scores=(1, None))
        record(tmp_path, arm, "not-submitted", "no_submission")
        record(tmp_path, arm, "unavailable", "no_result", failure="chat-response-invalid",
               missing_result=missing_result)
        write_json(tmp_path / arm / "runtime.json", {"state": "completed"})
    report = comparison(tmp_path)
    for arm in report["arms"].values():
        assert arm["denominator"] == arm["settled"] == 4
        assert arm["rtl_accuracy"] == .5 and arm["joint_mean_reward"] == .375
    assert report["paired_rtl"] == {"both_pass": 2, "neither_pass": 2}
    assert report["complete"]


def test_pending_judgment_is_not_final_zero(tmp_path):
    setup_experiment(tmp_path)
    record(tmp_path, "baseline", "unavailable", "pending_judgment")
    report = comparison(tmp_path)
    row = report["records"]["baseline"][-1]
    assert not row["settled"] and row["joint_final_reward"] is None
    assert report["arms"]["baseline"]["pending"] == 4


def test_paired_outcomes_follow_task_ids_not_report_order(tmp_path):
    setup_experiment(tmp_path)
    for arm, passes in (("baseline", {"passes", "half"}), ("trained", {"passes", "not-submitted"})):
        for task in reversed(["passes", "half", "not-submitted", "unavailable"]):
            record(tmp_path, arm, task, "scored", scores=(int(task in passes), 1))
        write_json(tmp_path / arm / "runtime.json", {"state": "completed"})
    report = comparison(tmp_path)
    assert report["paired_rtl"] == {"both_pass": 1, "baseline_only": 1, "trained_only": 1, "neither_pass": 1}


def test_missing_recorded_outcome_is_not_silently_a_zero(tmp_path):
    setup_experiment(tmp_path)
    write_json(tmp_path / "baseline/run/report/case.json", {"kind": "evaluation_case", "payload": {
        "row": {"task_id": "passes", "run_key": "a", "status": "scored", "result": {"sha256": "missing"}}}})
    with pytest.raises(FileNotFoundError):
        comparison(tmp_path)
