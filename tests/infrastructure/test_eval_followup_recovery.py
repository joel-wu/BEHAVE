"""Selected follow-ups transfer sealed evidence, not shared historical stores."""

import errno
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
import test_rejudge
from test_eval_followup import inventory
from test_evaluation import plan_for
from test_rejudge import Judge
from test_task_workflow import setup
from test_testbench_reports import POLICY, ReportBackend, measured

from behave.agent.journal import load_chain
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.records import RecordError, thaw_json
from behave.data.published import PublishedReleaseReader, TaskRevision, load_published_task
from behave.eval import followup as followup_module
from behave.eval.runner import run_evaluation
from behave.runtime.records import write_json
from behave.task_execution.accounting import load_accounting
from behave.task_execution.capture import load_submission
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.result import TaskResult
from behave.task_execution.run_store import RunStore

local_rollout = test_rejudge.local_rollout


class UnavailableJudge(Judge):
    def evaluate(self, request, objects):
        self.requests.append(request)
        return ComponentJudgment(None, "fixture-unavailable", "verifier")


def review_inputs(tmp_path, publication):
    """Two real saved trajectories sharing a batch CAS, with final TB evidence."""
    base, _ = setup(tmp_path / "fixture", publication)
    other = replace(publication.task, task_id="fixture_other")
    publication.release, publication.ref = publication.publish(tasks=(
        *publication.release.tasks, TaskRevision(other.task_id, publication.put(other.to_bytes()))))
    published = PublishedReleaseReader(publication.reader, publication.ref)
    jobs = tuple(replace(base, task=published.load(revision.task_id).public,
                         attempt=replace(base.attempt, attempt_id="with_tb" if index == 0 else "second"))
                 for index, revision in enumerate(publication.release.tasks))
    source = tmp_path / "source"
    root = RunStore(source / "run")
    # Exercise ancestor-owned stores explicitly; the ordinary batch fixture
    # otherwise creates flat per-candidate stores and exports their closures.
    for job in jobs:
        candidate = root.directory / "candidates" / BlobRef.of(job.to_bytes()).sha256
        JournalDirectory(candidate, objects=root.objects)
        assert RunStore(candidate).objects.directory == root.objects.directory
    report = run_evaluation(plan=plan_for(*jobs, testbench=POLICY), reader=publication.reader,
        directory=root.directory, judge=UnavailableJudge(), testbench=ReportBackend(measured, measured))
    write_json(source / "settings.json", {
        "tasks": [job.task.task_id for job in jobs], "setting": "joint", "release": publication.ref.as_dict(),
        "task_objects": str(publication.root), "judge_workers": 2, "score_on_submit": False,
        "generation_parameters": {"temperature": 0.7, "seed": 17},
        "attempt": {"max_turns": 60, "context_tokens": 131072}})
    return source, root, report


@pytest.fixture
def saved_review(tmp_path, publication, local_rollout, monkeypatch):
    source, root, report = review_inputs(tmp_path, publication)
    monkeypatch.setattr("behave.sim.api.prepare_runtime", lambda **kwargs: None)
    judge = Judge(unavailable=False)
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda: judge)
    return source, root, report, judge


def run(output):
    return followup_module.run_rejudgments(output, status_output=output, cancelled=lambda: False)


def assert_retained(item, old, original):
    target = RunStore(item["candidate_directory"])
    result = TaskResult.from_bytes(target.objects.read(BlobRef.from_dict(item["row"]["result"])))
    previous = TaskResult.from_bytes(original.objects.read(BlobRef.from_dict(old["result"])))
    assert result.submission == previous.submission and result.solver_head == previous.solver_head
    assert load_accounting(target.objects, result) == load_accounting(original.objects, previous)
    assert load_submission(target.objects, result.submission) == load_submission(original.objects, previous.submission)
    assert item["row"]["testbench"] == old["testbench"]
    tb_ref = BlobRef.from_dict(old["testbench"])
    tb = json.loads(target.objects.read(tb_ref))
    assert target.objects.read(tb_ref) == original.objects.read(tb_ref)
    load_chain(target.objects, BlobRef.from_dict(tb["head"]))
    return target, result


@pytest.mark.parametrize("cross_device", [False, True])
def test_only_declared_inputs_and_selected_evidence_are_transferred(
        tmp_path, publication, saved_review, local_rollout, monkeypatch, cross_device):
    source, original, report, judge = saved_review
    unrelated = original.objects.put(b"UNRELATED_SHARED_HISTORY")
    unrelated_input = publication.put(b"UNRELATED_INPUT_HISTORY")
    before = inventory(source)
    output = tmp_path / "followup"
    link = os.link

    def transfer(source, destination, **kwargs):
        if cross_device and Path(source).parent in {original.objects.directory, publication.root}:
            raise OSError(errno.EXDEV, "fixture cross-device link")
        return link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", transfer)
    selection = followup_module.prepare(output, [source])
    settings = json.loads((output / "settings.json").read_text())
    assert set(selection["rejudge"]) == {row["task_id"] for row in report.summary["candidates"]}
    assert not (Path(settings["task_objects"]) / unrelated_input.sha256).exists()
    for row in report.summary["candidates"]:
        load_published_task(DirectoryReader(settings["task_objects"]),
                            BlobRef.from_dict(settings["release"]), row["task_id"])

    def no_tree_copy(*args, **kwargs):
        pytest.fail("follow-up must not walk or copy a shared object tree")

    monkeypatch.setattr("shutil.copytree", no_tree_copy)
    results = run(output)
    assert len(judge.requests) == 4 and len(local_rollout) == 2
    for row in report.summary["candidates"]:
        old = thaw_json(row)
        target, result = assert_retained(results[row["task_id"]], old, original)
        assert result.score == 1 and results[row["task_id"]]["new_model_calls"] == 0
        assert not (target.objects.directory / unrelated.sha256).exists()
        same_inode = (target.objects.directory / old["result"]["sha256"]).stat().st_ino == (
            original.objects.directory / old["result"]["sha256"]).stat().st_ino
        assert same_inode is not cross_device
        other = next(item for item in report.summary["candidates"] if item["task_id"] != row["task_id"])
        assert not (target.objects.directory / other["result"]["sha256"]).exists()
    assert inventory(source) == before
    assert run(output) == results
    assert len(judge.requests) == 4 and len(local_rollout) == 2
    # Independent immutable copies still work when the original directory moves.
    source.rename(tmp_path / "archived-source")
    for item in results.values():
        target = RunStore(item["candidate_directory"])
        result = TaskResult.from_bytes(target.objects.read(BlobRef.from_dict(item["row"]["result"])))
        assert len(load_accounting(target.objects, result).trajectory.turns) == 2
        load_submission(target.objects, result.submission)


def test_chained_followup_keeps_original_solver_and_release(
        tmp_path, publication, saved_review, local_rollout, monkeypatch):
    source, original, report, _ = saved_review
    pending = UnavailableJudge()
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda: pending)
    first = tmp_path / "first"
    followup_module.prepare(first, [source])
    assert len(run(first)) == 2
    second = tmp_path / "second"
    followup_module.prepare(second, [first])
    judge = Judge(unavailable=False)
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda: judge)
    results = run(second)
    for row in report.summary["candidates"]:
        _, result = assert_retained(results[row["task_id"]], thaw_json(row), original)
        assert result.release == publication.ref and result.score == 1
    assert len(judge.requests) == 4 and len(local_rollout) == 2


def test_partial_failure_preserves_other_completed_results(tmp_path, saved_review, local_rollout, monkeypatch):
    source, _, _, judge = saved_review
    output = tmp_path / "followup"
    selection = followup_module.prepare(output, [source])
    broken, completed = selection["rejudge"]
    real = followup_module._candidate_artifacts

    def fail_one(objects, previous, row):
        if row["task_id"] == broken:
            raise OSError("fixture transfer interrupted")
        return real(objects, previous, row)

    with monkeypatch.context() as faults:
        faults.setattr(followup_module, "_candidate_artifacts", fail_one)
        with pytest.raises(OSError, match="fixture transfer interrupted"):
            run(output)
    saved = json.loads((output / "rejudged.json").read_text())
    assert set(saved) == {completed} and len(judge.requests) == 2
    assert set(run(output)) == {broken, completed}
    assert len(judge.requests) == 4 and len(local_rollout) == 2
    assert json.loads((output / "rejudged.json").read_text())[completed] == saved[completed]


def test_committed_judgment_replays_after_summary_write_failure(tmp_path, saved_review, monkeypatch):
    source, _, _, judge = saved_review
    output = tmp_path / "followup"
    selection = followup_module.prepare(output, [source])
    real = followup_module.write_json

    def fail_summary(path, value):
        if path.name == "rejudged.json":
            raise OSError("fixture summary interrupted")
        return real(path, value)

    with monkeypatch.context() as faults:
        faults.setattr(followup_module, "write_json", fail_summary)
        with pytest.raises(OSError, match="fixture summary interrupted"):
            run(output)
    assert len(judge.requests) == 4
    assert set(run(output)) == set(selection["rejudge"])
    assert len(judge.requests) == 4


def test_unsettled_target_remains_fenced(tmp_path, saved_review):
    source, _, _, judge = saved_review
    output = tmp_path / "followup"
    selection = followup_module.prepare(output, [source])
    task = selection["rejudge"][0]
    target = RunStore(output / "rejudgments" / task)
    journal = target.journal("judge")
    event = journal.append((), "judge_started", {})
    journal.append((event,), "judge_requested", {})
    with pytest.raises(RecordError, match="requires external fencing"):
        run(output)
    assert len(judge.requests) == 2  # Only the other, quiescent task is scored.
    assert task not in json.loads((output / "rejudged.json").read_text())


def test_inventory_does_not_read_model_payloads(tmp_path, saved_review, monkeypatch):
    _, original, report, _ = saved_review
    row = thaw_json(report.summary["candidates"][0])
    previous = BlobRef.from_dict(row["result"])
    result = TaskResult.from_bytes(original.objects.read(previous))
    accounting = json.loads(original.objects.read(result.accounting))
    payloads = {BlobRef.from_dict(turn[key]) for turn in accounting["turns"]
                for key in ("request", "response", "raw_response")}
    read = original.objects.read

    def metadata_only(ref):
        assert ref not in payloads, "inventory must not reread conversations"
        return read(ref)

    monkeypatch.setattr(original.objects, "read", metadata_only)
    refs = followup_module._candidate_artifacts(original.objects, previous, row)
    assert payloads.issubset(refs)


def test_another_coordinator_cannot_write_the_same_followup(tmp_path, saved_review):
    from behave.agent.attempt import AttemptInterrupted

    source, _, _, judge = saved_review
    output = tmp_path / "followup"
    followup_module.prepare(output, [source])
    with RunStore(output / "rejudgments").owner(), pytest.raises(AttemptInterrupted, match="run-already-owned"):
        run(output)
    assert not judge.requests and not (output / "rejudged.json").exists()
