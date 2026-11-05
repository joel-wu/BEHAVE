"""Startup recovery completes one pinned transaction, never a second launch."""

import multiprocessing
import os
from pathlib import Path

import pytest
from test_eval_checkpoint_fleet import retry_fixture

from behave.agent.attempt import AttemptInterrupted
from behave.eval import checkpoint as plan
from behave.eval import fleet as fleet_module
from behave.runtime.records import write_json
from behave.task_execution.run_store import RunStore


def interrupted(*args, **kwargs):
    raise OSError("controller interrupted")


@pytest.fixture
def retry(tmp_path, monkeypatch):
    lane, _config, directory, before, original, other = retry_fixture(tmp_path, monkeypatch)
    calls = []

    def submit(selected, index, **kwargs):
        assert selected == lane and index == 0
        control = directory / "control/retry"
        assert not control.exists()
        write_json(control / "launch.json", {"fixture": True})
        write_json(control / "status.json", {"state": "supervising"})
        calls.append(str(control))
        return {"state": "submitted"}

    monkeypatch.setattr(fleet_module, "submit_checkpoint", submit)
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda control:
        {"state": "failed", "returncode": 1} if Path(control) == original
        else plan.read(Path(control) / "status.json"))
    return {"root": tmp_path, "lane": lane, "directory": directory, "before": before,
            "original": original, "other": other, "calls": calls, "submit": submit}


def cut_after_intent(patch):
    publish = fleet_module.publish_json

    def commit(path, value):
        publish(path, value)
        if path.name == "intent.json":
            interrupted()

    patch.setattr(fleet_module, "publish_json", commit)


@pytest.mark.parametrize("boundary", ["intent", "move", "archive", "configuration", "settings",
                                     "manifest", "prepared", "owner", "submit", "aggregate", "final"])
def test_retry_resumes_without_second_submission(retry, monkeypatch, boundary):
    root, directory = retry["root"], retry["directory"]
    with monkeypatch.context() as patch:
        if boundary == "intent":
            cut_after_intent(patch)
        elif boundary == "move":
            rename = Path.rename

            def move(path, target):
                result = rename(path, target)
                if path == directory / "generation.json":
                    interrupted()
                return result

            patch.setattr(Path, "rename", move)
        elif boundary == "archive":
            publish = fleet_module.publish_json

            def commit(path, value):
                publish(path, value)
                if path.name == "archived.json":
                    interrupted()

            patch.setattr(fleet_module, "publish_json", commit)
        elif boundary == "owner":
            record = fleet_module.record_assignment

            def assign(*args, **kwargs):
                record(*args, **kwargs)
                interrupted()

            patch.setattr(fleet_module, "record_assignment", assign)
        elif boundary == "submit":
            def submit(*args, **kwargs):
                retry["submit"](*args, **kwargs)
                interrupted()

            patch.setattr(fleet_module, "submit_checkpoint", submit)
        else:
            targets = {"configuration": retry["lane"] / "configuration.json",
                "settings": directory / "rollout/settings.json", "manifest": directory / "manifest.json",
                "aggregate": root / "assignments.json"}

            def write(path, value, **kwargs):
                write_json(path, value, **kwargs)
                if path == targets.get(boundary) or (path.name == "recovery.json" and
                        value.get("state") == {"prepared": "prepared", "final": "submitted"}.get(boundary)):
                    interrupted()

            patch.setattr(fleet_module, "write_json", write)
        with pytest.raises(OSError, match="controller interrupted"):
            fleet_module.retry_startup(root, "task", "retry")
    # New defaults and another preflight callback cannot redefine a committed retry.
    monkeypatch.setattr(fleet_module, "measurement_config", interrupted)
    result = fleet_module.retry_startup(root, "task", "retry", preflight_check=interrupted)
    assert result["control"] == str(directory / "control/retry")
    assert len(retry["calls"]) == 1
    assert plan.read(root / "assignments.json")[0] == retry["other"]
    assert plan.read(retry["original"] / "launch.json") == {"original": True}
    archive = directory / "startup_failures/retry"
    assert plan.read(archive / "manifest.json") == retry["before"]
    assert plan.read(directory / "manifest.json") == {**retry["before"], "code": "new-code"}
    assert (archive / "generation.json").is_file() and (archive / "rollout/cleanup.json").is_file()
    assert not (directory / "generation.json").exists()
    # Once launched, a second call observes ownership, even if live files changed.
    write_json(directory / "rollout/progress.json", {"generation": "active"})
    assert fleet_module.retry_startup(root, "task", "retry")["launch_state"] == "supervising"
    assert len(retry["calls"]) == 1


@pytest.mark.parametrize("change", ["sampling", "snapshot", "configuration", "checkpoint", "control",
                                    "linked-rollout", "new-rollout", "changed-cleanup", "changed-generation"])
def test_changed_retry_inputs_fail_before_moving_old_evidence(retry, monkeypatch, change):
    root, lane, directory = retry["root"], retry["lane"], retry["directory"]
    with monkeypatch.context() as patch:
        cut_after_intent(patch)
        with pytest.raises(OSError):
            fleet_module.retry_startup(root, "task", "retry")
    if change == "sampling":
        write_json(lane / "sampling.json", {"temperature": 0})
    elif change == "snapshot":
        write_json(root / "archive/code-new/snapshot.json", {"sha256": "changed"})
    elif change == "configuration":
        write_json(lane / "configuration.json", {**plan.read(lane / "configuration.json"), "model": "changed"})
    elif change == "checkpoint":
        write_json(directory.parent / "checkpoint.json", {"model_path": "/another"})
    elif change == "control":
        (directory / "control/stranger").mkdir()
    elif change == "new-rollout":
        (directory / "rollout/run").mkdir()
    elif change == "changed-cleanup":
        (directory / "rollout/cleanup.json").write_text('{"cleanup_complete": false}')
    elif change == "changed-generation":
        (directory / "generation.json").write_text('{"state": "running"}')
    else:
        (directory / "rollout").rename(root / "retained-rollout")
        (directory / "rollout").symlink_to(root / "retained-rollout", target_is_directory=True)
    with pytest.raises(ValueError):
        fleet_module.retry_startup(root, "task", "retry")
    assert retry["calls"] == []
    assert (directory / "manifest.json").is_file()
    assert (directory / "generation.json").is_file()


def test_uncertain_control_is_never_submitted_again(retry, monkeypatch):
    root, directory = retry["root"], retry["directory"]

    def uncertain(*args, **kwargs):
        (directory / "control/retry").mkdir()
        interrupted()

    monkeypatch.setattr(fleet_module, "submit_checkpoint", uncertain)
    with pytest.raises(OSError):
        fleet_module.retry_startup(root, "task", "retry")
    monkeypatch.setattr(fleet_module, "submit_checkpoint", lambda *a, **kw: pytest.fail("must not resubmit"))
    with pytest.raises(ValueError, match="reconciliation"):
        fleet_module.retry_startup(root, "task", "retry")


def test_submitted_retry_with_missing_control_is_not_unstarted(retry, monkeypatch):
    root, directory = retry["root"], retry["directory"]
    fleet_module.retry_startup(root, "task", "retry")
    (directory / "control/retry").rename(root / "retained-retry-control")
    with pytest.raises(ValueError, match="lost its control"):
        fleet_module.retry_startup(root, "task", "retry")
    assert len(retry["calls"]) == 1


def test_dispatch_intent_without_control_is_not_replayed(retry, monkeypatch):
    publish = fleet_module.publish_json

    def commit(path, value):
        publish(path, value)
        if path.name == "dispatch.json":
            interrupted()

    with monkeypatch.context() as patch:
        patch.setattr(fleet_module, "publish_json", commit)
        with pytest.raises(OSError):
            fleet_module.retry_startup(retry["root"], "task", "retry")
    assert retry["calls"] == []
    with pytest.raises(ValueError, match="reconciliation"):
        fleet_module.retry_startup(retry["root"], "task", "retry")
    assert retry["calls"] == []


@pytest.mark.parametrize("boundary", ["move", "submit"])
def test_process_death_releases_lock_without_repeating_submission(retry, monkeypatch, boundary):
    directory = retry["directory"]

    def worker():
        if boundary == "move":
            rename = Path.rename

            def move(path, target):
                result = rename(path, target)
                if path == directory / "generation.json":
                    os._exit(75)
                return result

            monkeypatch.setattr(Path, "rename", move)
        else:
            def submit(*args, **kwargs):
                retry["submit"](*args, **kwargs)
                os._exit(75)

            monkeypatch.setattr(fleet_module, "submit_checkpoint", submit)
        fleet_module.retry_startup(retry["root"], "task", "retry")

    child = multiprocessing.get_context("fork").Process(target=worker)
    child.start()
    child.join(timeout=15)
    if child.is_alive():
        child.terminate()
        child.join(timeout=5)
        pytest.fail("recovery child did not reach its injected crash")
    assert child.exitcode == 75
    if boundary == "submit":
        monkeypatch.setattr(fleet_module, "submit_checkpoint", lambda *a, **kw: pytest.fail("must not resubmit"))
    result = fleet_module.retry_startup(retry["root"], "task", "retry")
    assert result["control"] == str(directory / "control/retry")
    assert len(retry["calls"]) == (1 if boundary == "move" else 0)
    assert plan.read(directory / "startup_failures/retry/manifest.json") == retry["before"]


def test_linked_intent_is_not_adopted(retry, monkeypatch):
    with monkeypatch.context() as patch:
        cut_after_intent(patch)
        with pytest.raises(OSError):
            fleet_module.retry_startup(retry["root"], "task", "retry")
    intent = retry["directory"] / "startup_failures/retry/intent.json"
    retained = intent.rename(retry["root"] / "retained-intent.json")
    intent.symlink_to(retained)
    with pytest.raises(ValueError, match="cannot be linked"):
        fleet_module.retry_startup(retry["root"], "task", "retry")
    assert retry["calls"] == [] and (retry["directory"] / "generation.json").is_file()


@pytest.mark.parametrize("change", ["control-link", "launch-link", "dispatch"])
def test_submitted_retry_retains_its_ownership_proof(retry, change):
    root, directory = retry["root"], retry["directory"]
    fleet_module.retry_startup(root, "task", "retry")
    control = directory / "control/retry"
    if change == "dispatch":
        write_json(directory / "startup_failures/retry/dispatch.json", {"control": "unrelated"})
    else:
        path = control if change == "control-link" else control / "launch.json"
        retained = path.rename(root / "retained-submission")
        path.symlink_to(retained, target_is_directory=change == "control-link")
    message = "ownership directory" if change == "control-link" else "reconciliation"
    with pytest.raises(ValueError, match=message):
        fleet_module.retry_startup(root, "task", "retry")
    assert len(retry["calls"]) == 1


def test_retry_uses_existing_fleet_lock(retry):
    root = retry["root"]
    with RunStore(root / "owner").owner(), pytest.raises(AttemptInterrupted):
        fleet_module.retry_startup(root, "task", "retry")
    assert not (retry["directory"] / "startup_failures").exists()


def test_unowned_legacy_retry_is_retained(retry):
    directory = retry["directory"]
    write_json(directory / "startup_failures/retry/recovery.json", {"state": "archiving"})
    with pytest.raises(ValueError, match="reconciliation"):
        fleet_module.retry_startup(retry["root"], "task", "retry")
    assert (directory / "generation.json").is_file() and retry["calls"] == []


def test_planner_preview_is_read_only_and_matches_publication(tmp_path):
    from test_eval_checkpoint_study import fixture

    fixture(tmp_path)
    entry = {"model_line": "main", "update": 5}
    handoff = {"model_path": "/weights", "model_revision": "fixture"}
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    files = plan.measurement_files(tmp_path, entry, 17, handoff)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before
    output = plan.prepare_measurement(tmp_path, entry, 17, handoff).parent
    assert files == {name: plan.read(output / name) for name in files}
