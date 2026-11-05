"""Committed staging release survives interruption without another model call."""

import json
import multiprocessing
import os
from pathlib import Path

import pytest
import test_ppa_staging_cleanup as fixtures

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.core.records import RecordError
from behave.isolation import scratch
from behave.ppa import launch, staging
from behave.runtime import snapshots as snapshots_module
from behave.runtime.records import write_json

prepared = fixtures.prepared


def broken(*args, **kwargs):
    raise OSError("release interrupted")


@pytest.fixture
def ready(prepared, monkeypatch):
    root, local, state = prepared
    with monkeypatch.context() as patch:
        patch.setattr(scratch.ScratchDirectory, "cleanup", broken)
        with pytest.raises(OSError, match="release interrupted"):
            launch.run_staged(root)
    assert local.is_dir() and (root / "scratch-release.json").is_file()
    return root, local, state


@pytest.mark.parametrize("boundary", ["intent", "partial", "rmdir", "terminal"])
def test_release_resumes_each_commit_boundary(prepared, monkeypatch, boundary):
    root, local, state = prepared
    with monkeypatch.context() as patch:
        if boundary in {"intent", "terminal"}:
            def write(path, value, **kwargs):
                if value.get("scratch") == ("release-ready" if boundary == "intent" else "released"):
                    broken()
                write_json(path, value, **kwargs)
            patch.setattr(launch if boundary == "intent" else staging, "write_json", write)
        elif boundary == "partial":
            (local / "cache").mkdir()
            (local / "cache/file").write_bytes(b"disposable")
            original = scratch.shutil.rmtree

            def remove(path, *args, **kwargs):
                original(path, *args, **kwargs)
                broken()

            patch.setattr(scratch.shutil, "rmtree", remove)
        else:
            original = Path.rmdir

            def remove(path):
                if path == local:
                    broken()
                original(path)

            patch.setattr(Path, "rmdir", remove)
        with pytest.raises(OSError, match="release interrupted"):
            launch.run_staged(root)
    if boundary == "partial":
        assert (local / staging.MARKER).exists()
        assert len(list(local.iterdir())) == 2  # Marker and the remaining child.
    elif boundary == "rmdir":
        assert local.is_dir() and list(local.iterdir()) == []
    elif boundary == "terminal":
        assert not local.exists()
    else:
        assert json.loads((root / "runtime.json").read_text())["state"] == "running"
    assert launch.release_staged(root)["scratch"] == "released"
    assert not local.exists() and state["calls"] == 1
    assert (root / "execution/paid-record").read_bytes() == b"must survive"
    # Idempotence must not regress a terminal state or rewrite its status file.
    status = (root / "runtime.json").stat()
    monkeypatch.setattr(staging, "write_json", broken)
    assert launch.release_staged(root)["scratch"] == "released"
    after = (root / "runtime.json").stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_size) == (status.st_ino, status.st_mtime_ns, status.st_size)


def test_execution_holds_the_lock_through_trace_publication(prepared, monkeypatch):
    root, local, state = prepared
    original = launch.export_traces

    def export(path):
        with pytest.raises(AttemptInterrupted, match="run-already-owned"):
            launch.release_staged(root)
        return original(path)

    monkeypatch.setattr(launch, "export_traces", export)
    assert launch.run_staged(root) == 0
    assert not local.exists() and state["calls"] == 1


def test_recovery_takes_the_same_execution_lock(ready, monkeypatch):
    root, local, state = ready
    with JournalDirectory(root).owner():
        for operation in (launch.release_staged, launch.run_staged):
            with pytest.raises(AttemptInterrupted, match="run-already-owned"):
                operation(root)
    original = scratch.ScratchDirectory.cleanup

    def cleanup(owner):
        with pytest.raises(AttemptInterrupted, match="run-already-owned"):
            launch.release_staged(root)
        original(owner)

    monkeypatch.setattr(scratch.ScratchDirectory, "cleanup", cleanup)
    assert launch.release_staged(root)["scratch"] == "released"
    assert not local.exists() and state["calls"] == 1


def test_process_death_releases_lock_but_not_model_history(prepared, monkeypatch):
    root, local, _ = prepared

    def worker():
        def write(path, value, **kwargs):
            write_json(path, value, **kwargs)
            if value.get("scratch") == "release-ready":
                os._exit(75)
        launch.write_json = write
        launch.run_staged(root)

    child = multiprocessing.get_context("fork").Process(target=worker)
    child.start()
    try:
        child.join(20)
        assert child.exitcode == 75
    finally:
        if child.is_alive():
            child.kill()
            child.join(5)
    assert local.is_dir() and (root / "execution/paid-record").exists()
    monkeypatch.setattr(launch, "run", broken)
    monkeypatch.setattr(launch, "run_group", broken)
    monkeypatch.setattr(snapshots_module, "stage", broken)
    assert launch.release_staged(root)["scratch"] == "released"
    assert not local.exists()
    assert (root / "execution/paid-record").read_bytes() == b"must survive"


@pytest.mark.parametrize("defect", [
    "boot", "replacement", "marker", "missing-marker", "symlink-marker", "owner",
    "legacy-intent", "plans", "snapshot", "child", "malformed-intent", "symlink-intent",
])
def test_changed_or_unverifiable_release_retains_private_data(ready, monkeypatch, defect):
    root, local, state = ready
    if defect == "boot":
        monkeypatch.setattr(staging, "_transport_host", lambda: "another-host:another-boot")
    elif defect == "replacement":
        local.rename(local.with_name("original"))
        local.mkdir()
    elif defect == "marker":
        write_json(local / staging.MARKER, {"another": "owner"})
    elif defect == "missing-marker":
        (local / staging.MARKER).unlink()
    elif defect == "symlink-marker":
        (local / staging.MARKER).unlink()
        (local / staging.MARKER).symlink_to(root / "scratch-owner.json")
    elif defect == "owner":
        value = json.loads((root / "scratch-owner.json").read_text())
        value["directory"] = str(root.parent)
        write_json(root / "scratch-owner.json", value)
    elif defect == "legacy-intent":
        value = json.loads((root / "scratch-release.json").read_text())
        value["schema"] = "behave.ppa-scratch-release/1"
        write_json(root / "scratch-release.json", value)
    elif defect == "plans":
        value = json.loads((root / "plan.json").read_text())
        value["turns"] += 1
        write_json(root / "plan.json", value)
    elif defect == "snapshot":
        monkeypatch.setattr(snapshots_module, "verify", lambda _: {"sha256": "changed"})
    elif defect == "child":
        write_json(state["services"][0] / "service.json", {"cleanup": "unknown"})
    elif defect == "malformed-intent":
        write_json(root / "scratch-release.json", [])
    else:
        intent = root / "scratch-release.json"
        intent.rename(root / "other-intent.json")
        intent.symlink_to(root / "other-intent.json")
    sentinel = local / "must-survive"
    sentinel.write_bytes(b"not authorized")
    with pytest.raises((ValueError, RecordError)):
        launch.release_staged(root)
    assert sentinel.read_bytes() == b"not authorized"
    assert state["calls"] == 1
    assert (root / "execution/paid-record").read_bytes() == b"must survive"


@pytest.mark.parametrize("failure", ["unknown", "nonzero", "exception"])
def test_uncommitted_runs_are_not_implicitly_adopted(prepared, failure):
    root, local, state = prepared
    if failure == "unknown":
        state["cleanup"] = "unknown"
    elif failure == "nonzero":
        state["code"] = 1
    else:
        state["error"] = True
    if failure == "exception":
        with pytest.raises(RuntimeError, match="interrupted worker"):
            launch.run_staged(root)
    else:
        launch.run_staged(root)
    with pytest.raises(FileNotFoundError):
        launch.release_staged(root)
    assert local.is_dir() and state["calls"] == 1


def test_released_path_cannot_be_adopted_again(prepared):
    root, local, state = prepared
    assert launch.run_staged(root) == 0
    local.mkdir()
    (local / "new-owner").write_bytes(b"keep")
    with pytest.raises(ValueError, match="reappeared"):
        launch.release_staged(root)
    assert (local / "new-owner").read_bytes() == b"keep" and state["calls"] == 1


@pytest.mark.parametrize("marker", ["..", ".", "nested/file", "", 3])
def test_owner_marker_is_validated_before_creating_scratch(tmp_path, marker):
    with pytest.raises(ValueError, match="simple filename"):
        scratch.ScratchDirectory(prefix="owned-", parent=tmp_path, owner_marker=marker, cleanup=lambda: None)
    assert list(tmp_path.iterdir()) == []
