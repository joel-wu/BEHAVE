"""Full saved-submission passes reuse indexed staging without rerunning models."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from test_evaluation import plan_for
from test_rejudge import Judge, local_rollout  # noqa: F401
from test_task_workflow import setup

from behave.core.artifacts import BlobRef
from behave.eval import scoring as scoring_module
from behave.eval.runner import run_evaluation
from behave.eval.scoring import select_completed_run, select_score_recovery
from behave.task_execution import batch as batch_module
from behave.task_execution.rejudge import judgment_run, rejudge_batch
from behave.task_execution.run_store import RunStore


def saved_run(tmp_path, publication, monkeypatch):
    base, _ = setup(tmp_path / "fixture", publication)
    jobs = tuple(replace(base, attempt=replace(base.attempt, attempt_id=name))
                 for name in ("submitted", "no_submission", "missing"))
    execute = batch_module.execute_task

    def run(*, job, **kwargs):
        if job.attempt.attempt_id == "missing":
            raise RuntimeError("fixture unavailable worker")
        return execute(job=job, **kwargs)

    monkeypatch.setattr(batch_module, "execute_task", run)
    output = tmp_path / "saved"
    shutil.copytree(publication.root, output / "inputs")
    root = RunStore(output / "run")
    report = run_evaluation(plan=replace(plan_for(*jobs), score_on_submit=False),
                            reader=publication.reader, directory=root.directory, judge=Judge())
    return output, root, report


def score(output, name, **kwargs):
    return scoring_module.score_saved(output, name, workers=2, previous=None, judge_policy=None,
                               cpu_pool=None, stage_from=None, all_submitted=True,
                               retry_testbench=False, **kwargs)


def test_full_pass_is_lazy_complete_and_resumable(tmp_path, publication, request, monkeypatch):
    launches = request.getfixturevalue("local_rollout")
    output, root, original = saved_run(tmp_path, publication, monkeypatch)
    before = list(launches)
    judge = Judge(unavailable=False)
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda **kw: judge)
    expected = rejudge_batch(directory=root.directory, name="strict", reader=publication.reader,
                             judge=Judge(unavailable=False), workers=2, all_submitted=True,
                             retry_testbench=False)
    monkeypatch.setattr(scoring_module, "_stage_score_objects", lambda *a, **kw: pytest.fail("full CAS copy"))
    history_refs = {BlobRef.of(event.to_bytes()).sha256 for candidate in (root.directory / "candidates").iterdir()
                    for phase in ("solver", "provider", "development")
                    for event in RunStore(candidate, objects=root.objects).journal(phase).load()}
    # Trajectories remain in the archive, but no score input needs to reopen them.
    from behave.core.artifacts import DirectoryReader
    read = DirectoryReader.read

    def guarded(self, ref):
        assert ref.sha256 not in history_refs
        return read(self, ref)

    monkeypatch.setattr(DirectoryReader, "read", guarded)
    report = score(output, "lazy")
    assert report.summary["requested"] == 3
    assert report.summary["no_submission"] == 1
    assert report.summary["no_result"] == 1
    for key in ("mean", "scored", "billing"):
        assert report.summary[key] == expected.summary[key]
    assert [row["run_key"] for row in report.summary["candidates"]] == [
        row["run_key"] for row in original.summary["candidates"]]
    target = judgment_run(root, "lazy")
    config = target.journal("task").load()[0].payload
    assert len(config["selection"]) == 3
    runtime = json.loads((target.directory / "runtime.json").read_bytes())
    assert runtime["state"] == "archived"
    assert not Path(runtime["local_directory"]).exists()
    count = len(judge.requests)
    assert score(output, "lazy").summary == report.summary
    assert len(judge.requests) == count
    assert launches == before


@pytest.mark.usefixtures("local_rollout")
def test_started_legacy_pass_keeps_its_replay_configuration(tmp_path, publication, monkeypatch):
    output, root, _ = saved_run(tmp_path, publication, monkeypatch)
    judge = Judge(unavailable=False)
    expected = rejudge_batch(directory=root.directory, name="legacy", reader=publication.reader,
                             judge=judge, workers=2, all_submitted=True, retry_testbench=False)
    assert "selection" not in judgment_run(root, "legacy").journal("task").load()[0].payload
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda **kw: judge)
    monkeypatch.setattr("behave.eval.scoring.select_completed_run",
                        lambda *a, **kw: pytest.fail("must not rebind a legacy attempt"))
    count = len(judge.requests)
    assert score(output, "legacy").summary == expected.summary
    assert len(judge.requests) == count


@pytest.mark.usefixtures("local_rollout")
def test_interrupted_full_pass_resumes_without_losing_candidates(tmp_path, publication, monkeypatch):
    output, root, _ = saved_run(tmp_path, publication, monkeypatch)
    judge = Judge(unavailable=False)
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda **kw: judge)
    monkeypatch.setattr(scoring_module, "_stage_score_objects", lambda *a, **kw: pytest.fail("full CAS copy"))

    def interrupted(_):
        raise RuntimeError("coordinator interrupted after a committed case")

    with pytest.raises(RuntimeError, match="coordinator interrupted"):
        score(output, "resume", on_case=interrupted)
    target = judgment_run(root, "resume")
    runtime = json.loads((target.directory / "runtime.json").read_bytes())
    assert runtime["state"] == "failed-retained"
    assert Path(runtime["local_directory"]).is_dir()
    report = score(output, "resume")
    assert report.summary["requested"] == 3
    assert report.summary["no_submission"] == report.summary["no_result"] == 1
    assert report.summary["mean"] == pytest.approx(1 / 3)


@pytest.mark.usefixtures("local_rollout")
def test_selection_requires_completed_report_and_pins_previous_results(tmp_path, publication, monkeypatch):
    with pytest.raises(ValueError, match="completed"):
        select_completed_run(tmp_path / "absent", RunStore(tmp_path / "absent").objects)
    _, root, original = saved_run(tmp_path, publication, monkeypatch)
    before = select_completed_run(root.directory, root.objects)
    expected = rejudge_batch(directory=root.directory, name="earlier", reader=publication.reader,
                             judge=Judge(unavailable=False), workers=2, all_submitted=True,
                             retry_testbench=False)
    after = select_completed_run(root.directory, root.objects, previous="earlier")
    assert set(before) == set(after)
    assert all(row["judgment"] == "earlier" for row in after.values())
    assert [row["result"] for row in after.values()] == [
        dict(row["result"]) if row["result"] is not None else None for row in expected.summary["candidates"]]
    assert root.seal("report")[0] == original.head
    assert select_completed_run(root.directory, root.objects) == before


@pytest.mark.usefixtures("local_rollout")
def test_recovery_keeps_partial_scores_and_unfinished_generation_cases(tmp_path, publication, monkeypatch):
    output, root, original = saved_run(tmp_path, publication, monkeypatch)
    judge = Judge(unavailable=False)
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda **kw: judge)

    def interrupted(row):
        if row["status"] == "scored":
            raise RuntimeError("stop after commit")

    with pytest.raises(RuntimeError, match="stop after commit"):
        score(output, "interrupted", on_case=interrupted)
    selected = select_score_recovery(root.directory, root.objects, judgment="interrupted")
    before = len(judge.requests)
    report = scoring_module.score_saved(output, "recovery", workers=3, previous=None,
        judge_policy=None, cpu_pool=None, stage_from=None, selection=selected,
        retry_testbench=False)
    assert report.summary["requested"] == original.summary["requested"] == 3
    assert report.summary["scored"] == 2  # Includes the no-submission zero.
    assert report.summary["no_submission"] == report.summary["no_result"] == 1
    assert len(judge.requests) == before  # Already settled submission is retained.
    assert root.seal("report")[0] == original.head
    assert set(selected) == set(select_completed_run(root.directory, root.objects))


@pytest.mark.parametrize("name", ["", "..", "../baseline", "/baseline"])
def test_recovery_rejects_invalid_pass_name(tmp_path, name):
    with pytest.raises(ValueError, match="directory name"):
        select_score_recovery(tmp_path, RunStore(tmp_path).objects, judgment=name)
