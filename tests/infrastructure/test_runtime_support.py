"""Shared runtime ownership and compatibility; no Slurm or GPU required."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.runtime import jobs
from behave.runtime import snapshots as snapshots_module
from behave.runtime.records import write_json
from tools import infra_validation_job as validation


def repository(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for name in ("src/behave/code.py", "tests/infrastructure/test_code.py",
                 "tests/infrastructure/syn_runtime_fixture.py",
                 "tools/launch.py", "external/manifest.toml", "external/patches/native.patch"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# frozen bytes\n")
    (root / "external/oss-cad-suite/bin").mkdir(parents=True)
    split = root / "data/splits/tiny"
    split.mkdir(parents=True)
    (split.parent / "current.json").write_text('{"authored":"tiny"}')
    for name in ("eval.txt", "external_eval.txt"):
        (split / name).write_text("evaluation-task\n")
    files = ("tools/launch.py", "data/splits/current.json", "external/manifest.toml",
             "external/patches/native.patch")
    monkeypatch.setattr(snapshots_module, "FILES", files)
    return root, files


def test_production_snapshot_excludes_tests_and_freezes_native_pins(tmp_path, monkeypatch):
    root, _ = repository(tmp_path, monkeypatch)
    target = tmp_path / "frozen/checkout"
    record = snapshots_module.freeze(root, target)
    assert not any(name.startswith("tests/") for name in record["files"])
    assert "external/manifest.toml" in record["files"]
    assert not (target / "external").is_symlink()
    assert (target / "external/oss-cad-suite").resolve() == root / "external/oss-cad-suite"
    (root / "external/patches/native.patch").write_text("# subsequent checkout edit\n")
    local = tmp_path / "node"
    assert snapshots_module.stage(target, local) == record
    assert (local / "external/patches/native.patch").read_text() == "# frozen bytes\n"
    assert snapshots_module.verify(target) == record


@pytest.mark.parametrize('fail_commit', [False, True])
def test_snapshot_receipt_is_committed_after_files_via_shared_atomic_writer(tmp_path, monkeypatch, fail_commit):
    root, _ = repository(tmp_path, monkeypatch)
    target = tmp_path / 'frozen/checkout'
    writes = []
    original_replace = Path.replace

    def replace(source, destination):
        assert destination == target.parent / 'snapshot.json'
        assert source != destination and not destination.exists()
        record = json.loads(source.read_text())
        assert all((target / name).is_file() for name in record['files'])
        assert all((target / name).stat().st_mode & 0o777 == 0o444 for name in record['files'])
        writes.append(destination)
        if fail_commit:
            raise OSError('receipt publication failed')
        return original_replace(source, destination)

    monkeypatch.setattr(Path, 'replace', replace)
    if fail_commit:
        with pytest.raises(OSError, match='receipt publication failed'):
            snapshots_module.freeze(root, target)
        assert not (target.parent / 'snapshot.json').exists()
        assert not list(target.parent.glob('.snapshot.json.*.tmp'))
        with pytest.raises(FileNotFoundError):
            snapshots_module.verify(target)
        return
    record = snapshots_module.freeze(root, target)
    assert writes == [target.parent / 'snapshot.json']
    assert snapshots_module.verify(target) == record


def test_legacy_validation_inventory_survives_new_production_defaults(tmp_path, monkeypatch):
    root, files = repository(tmp_path, monkeypatch)

    monkeypatch.setattr(validation, "FILES", files)
    frozen = tmp_path / "validation/checkout"
    previous = validation.freeze(root, frozen)
    assert "tests/infrastructure/test_code.py" in previous["files"]
    assert "tests/infrastructure/syn_runtime_fixture.py" in previous["files"]
    monkeypatch.setattr(snapshots_module, "FILES", ("does-not-exist-in-old-run.py",))
    child = tmp_path / "continuation/checkout"
    continued = snapshots_module.freeze(frozen, child)
    assert continued["files"] == previous["files"]
    assert continued["sha256"] == previous["sha256"]
    assert snapshots_module.stage(child, tmp_path / "node")["sha256"] == previous["sha256"]
    assert (tmp_path / "node/tests/infrastructure/syn_runtime_fixture.py").read_bytes() == (
        root / "tests/infrastructure/syn_runtime_fixture.py").read_bytes()


def test_validation_discovers_available_operators_without_optional_probes(tmp_path):
    required = (*snapshots_module.FILES, *validation.SHARED_TESTS, *validation.TEST_SUPPORT)
    for name in (*required, "tools/nested/check.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    selected = validation._validation_files(tmp_path)
    assert set(selected) == set(required) | {"tools/nested/check.py"}
    assert all((tmp_path / name).is_file() for name in selected)


def test_validation_rejects_symlinked_operator_directory(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "tools").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError, match="must not be a symlink"):
        validation._validation_files(tmp_path)


def test_copied_supervisor_runs_without_importing_the_checkout(tmp_path, monkeypatch):
    control = tmp_path / "control"
    calls = []
    with monkeypatch.context() as m:
        m.setattr(jobs.subprocess, "Popen", lambda *a, **kw:
                  calls.append((a, kw)) or SimpleNamespace(pid=123))
        jobs.submit(control, ["/bin/true"], cwd=tmp_path, wall_seconds=None,
                    metadata={"purpose": "standalone smoke"}, cleanup_seconds=None)
    record = json.loads((control / "launch.json").read_bytes())
    assert record["cwd"] == str(tmp_path)
    assert record["wall_seconds"] is None and record["cleanup_seconds"] is None
    assert calls[0][1]["start_new_session"] is True
    result = subprocess.run([sys.executable, "-I", "-B", str(control / "runner.py"),
                             "_supervise", str(control)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads((control / "status.json").read_bytes())["state"] == "completed"


def test_legacy_submit_preserves_working_directory_and_signature(tmp_path, monkeypatch):
    from tools import dataset_validation_job as legacy

    calls = []
    monkeypatch.setattr(legacy, "REPO", tmp_path)
    monkeypatch.setattr(legacy, "submit_job", lambda *a, **kw: calls.append((a, kw)) or {})
    legacy.submit(tmp_path / "control", ["/bin/true"], wall_seconds=None,
                  metadata={}, cleanup_seconds=None)
    assert calls[0][1] == {"cwd": tmp_path, "wall_seconds": None, "metadata": {},
                          "cleanup_seconds": None}


def test_receipt_helper_keeps_legacy_import_and_atomic_output(tmp_path):
    from tools.eval_review_trial import write_json as legacy

    assert legacy is write_json
    target = tmp_path / "records/result.json"
    legacy(target, {"phase": "训练"})
    write_json(target, {"phase": "completed"})
    assert json.loads(target.read_text()) == {"phase": "completed"}
    assert list(target.parent.iterdir()) == [target]


def test_durable_receipt_syncs_payload_before_commit_and_directory_after(tmp_path, monkeypatch):
    import os
    import stat

    from behave.runtime import records as records_module

    events, replace = [], Path.replace
    monkeypatch.setattr(records_module.os, "fsync", lambda fd:
                        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"))

    def rename(path, target):
        events.append("replace")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", rename)
    target = tmp_path / "receipt.json"
    write_json(target, {"state": "releasing"}, durable=True)
    assert events == ["file", "replace", "directory"]
    events.clear()
    write_json(target, {"state": "legacy-default"})
    assert events == ["replace"]  # Existing high-frequency status writes are unchanged.


def test_failed_durable_receipt_keeps_the_previous_commit(tmp_path, monkeypatch):
    from behave.runtime import records as records_module

    target = tmp_path / "receipt.json"
    write_json(target, {"state": "verified"})

    def fail(fd):
        raise OSError("fixture fsync failure")

    monkeypatch.setattr(records_module.os, "fsync", fail)
    with pytest.raises(OSError, match="fsync failure"):
        write_json(target, {"state": "releasing"}, durable=True)
    assert json.loads(target.read_text()) == {"state": "verified"}
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("payload", (b"", b"\x00\xff\n", "训练\n".encode()))
def test_atomic_bytes_preserve_exact_payload(tmp_path, payload):
    from behave.runtime.records import write_bytes

    target = tmp_path / "derived/raw.json"
    write_bytes(target, payload)
    assert target.read_bytes() == payload
    assert list(target.parent.iterdir()) == [target]


def test_partial_temporary_write_never_replaces_previous_file(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from behave.runtime import records as records_module

    target = tmp_path / "raw.json"
    target.write_bytes(b"previous complete file")
    temporary_file = records_module.NamedTemporaryFile

    @contextmanager
    def interrupted_file(**options):
        with temporary_file(**options) as stream:
            def partial(data):
                stream.write(data[:3])
                stream.flush()
                raise OSError("fixture partial write")

            yield SimpleNamespace(name=stream.name, write=partial)

    with monkeypatch.context() as patch:
        patch.setattr(records_module, "NamedTemporaryFile", interrupted_file)
        with pytest.raises(OSError, match="partial write"):
            records_module.write_bytes(target, b"replacement complete file")
    assert target.read_bytes() == b"previous complete file"
    assert list(tmp_path.iterdir()) == [target]
    records_module.write_bytes(target, b"replacement complete file")
    assert target.read_bytes() == b"replacement complete file"


def test_concurrent_publications_use_distinct_complete_temporary_files(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from behave.runtime.records import write_bytes

    target = tmp_path / "raw.json"
    target.write_bytes(b"previous")
    payloads = (b"first complete file", b"second complete file")
    barrier, staged, replace = Barrier(2), [], Path.replace

    def publish(path, other):
        assert other == target and path.parent == target.parent
        assert target.read_bytes() == b"previous" and path.read_bytes() in payloads
        staged.append(path)
        barrier.wait(timeout=10)
        return replace(path, other)

    monkeypatch.setattr(Path, "replace", publish)
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda data: write_bytes(target, data), payloads))
    assert len(set(staged)) == 2
    assert target.read_bytes() in payloads
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("failure", ["copy", "spawn"])
def test_failed_supervisor_start_is_terminal_not_forever_submitted(tmp_path, monkeypatch, failure):
    control = tmp_path / "control"
    error = OSError("injected supervisor launch failure")
    launches = []

    def fail(*args, **kwargs):
        raise error

    def spawn(*args, **kwargs):
        launches.append(args)
        return fail()

    if failure == "copy":
        monkeypatch.setattr(jobs.shutil, "copyfile", fail)
    monkeypatch.setattr(jobs.subprocess, "Popen", spawn)
    with pytest.raises(OSError) as raised:
        jobs.submit(control, ["/bin/true"], cwd=tmp_path, wall_seconds=None, metadata={})
    assert raised.value is error
    assert len(launches) == (failure == "spawn")
    record = jobs.status_of(control)
    assert record["state"] == "launch-error"
    assert record["stage"] == "supervisor_start"
    assert record["supervisor_started"] is False and record["finished_at"]
    assert "injected supervisor launch failure" in record["error"]
    assert not (control / "supervisor.json").exists()
    monkeypatch.setattr(jobs, "Event", lambda: SimpleNamespace(
        wait=lambda _: pytest.fail("known launch failure must not wait")))
    assert jobs.wait_control(control, allow_failure=True)["state"] == "launch-error"
    with pytest.raises(RuntimeError, match="launch-error"):
        jobs.wait_control(control)
    before = (control / "status.json").read_bytes()
    with pytest.raises(FileExistsError):
        jobs.submit(control, ["/bin/true"], cwd=tmp_path, wall_seconds=None, metadata={})
    assert (control / "status.json").read_bytes() == before
    assert len(launches) == (failure == "spawn")  # A failure is not retry permission.


def test_supervisor_receipt_failure_does_not_claim_no_process_started(tmp_path, monkeypatch):
    control = tmp_path / "control"
    write = jobs._write

    def failing_receipt(path, value):
        if path.name == "supervisor.json":
            raise OSError("injected PID receipt failure")
        write(path, value)

    def spawned(*args, **kwargs):
        write(control / "status.json", {"state": "supervising", "command_pid": 123})
        return SimpleNamespace(pid=122)

    monkeypatch.setattr(jobs, "_write", failing_receipt)
    monkeypatch.setattr(jobs.subprocess, "Popen", spawned)
    with pytest.raises(OSError, match="PID receipt"):
        jobs.submit(control, ["/bin/true"], cwd=tmp_path, wall_seconds=None, metadata={})
    assert json.loads((control / "status.json").read_bytes()) == {
        "state": "supervising", "command_pid": 123}


def test_launch_reporting_failure_preserves_original_error(tmp_path, monkeypatch, capsys):
    write = jobs._write
    launch_error = OSError("original spawn error")

    def failing_receipt(path, value):
        if path.name == "status.json":
            raise OSError("status filesystem unavailable")
        write(path, value)

    def fail(*args, **kwargs):
        raise launch_error

    monkeypatch.setattr(jobs, "_write", failing_receipt)
    monkeypatch.setattr(jobs.subprocess, "Popen", fail)
    with pytest.raises(OSError) as raised:
        jobs.submit(tmp_path / "control", ["/bin/true"], cwd=tmp_path,
                    wall_seconds=None, metadata={})
    assert raised.value is launch_error
    assert "status filesystem unavailable" in capsys.readouterr().err


def test_wait_control_only_observes_unknown_and_overdue_owners(tmp_path, monkeypatch):
    from tools import eval_campaign

    assert eval_campaign.wait_control is jobs.wait_control
    terminal = {'state': 'completed', 'returncode': 0}
    states = iter(({'state': 'unknown'}, {'state': 'submitted'},
                   {'state': 'supervising', 'heartbeat_overdue': True}, terminal))
    pauses = []
    monkeypatch.setattr(jobs, 'status_of', lambda _: next(states))
    monkeypatch.setattr(jobs, 'Event', lambda: SimpleNamespace(wait=pauses.append))
    monkeypatch.setattr(jobs, 'submit', lambda *a, **kw: pytest.fail('observation cannot relaunch'))
    assert jobs.wait_control(str(tmp_path)) is terminal
    assert pauses == [30, 30, 30]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('state', ['failed', 'timed-out', 'launch-error'])
@pytest.mark.parametrize('allow_failure', [False, True])
def test_wait_control_retains_terminal_failure_without_mutation(tmp_path, monkeypatch, state, allow_failure):
    record = {'state': state, 'returncode': 7}
    monkeypatch.setattr(jobs, 'status_of', lambda _: record)
    monkeypatch.setattr(jobs, 'submit', lambda *a, **kw: pytest.fail('terminal is not retry permission'))
    if allow_failure:
        assert jobs.wait_control(tmp_path, allow_failure=True) is record
    else:
        with pytest.raises(RuntimeError, match=state):
            jobs.wait_control(tmp_path)
    assert record == {'state': state, 'returncode': 7} and not list(tmp_path.iterdir())
