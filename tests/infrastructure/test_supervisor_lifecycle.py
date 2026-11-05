"""Supervision faults retain ownership until the launched command is settled."""

import errno
import json
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from behave.runtime import jobs


def control(tmp_path, *, cleanup_seconds=19):
    directory = tmp_path / "control"
    directory.mkdir()
    (directory / "launch.json").write_text(json.dumps({
        "argv": ["/fixture/command"], "cwd": str(tmp_path), "wall_seconds": None,
        "cleanup_seconds": cleanup_seconds, "heartbeat_seconds": 60, "metadata": {},
    }))
    return directory


@pytest.mark.parametrize("fault", ["heartbeat_write", "wait"])
def test_post_spawn_fault_cleans_owned_command_before_terminal_status(tmp_path, monkeypatch, fault):
    directory = control(tmp_path)
    signals, waits = [], []

    class Child:
        pid = 12345
        returncode = None

        def wait(self, *, timeout):
            waits.append(timeout)
            if not signals:
                raise OSError("wait failed")
            self.returncode = -signal.SIGTERM
            return self.returncode

        def poll(self):
            return self.returncode

    child = Child()
    write = jobs._write

    def failing_heartbeat(path, value):
        if fault == "heartbeat_write" and value.get("state") == "supervising":
            raise OSError("status write failed")
        if value.get("state") == "launch-error":
            assert child.poll() is not None, "must settle the child before a terminal receipt"
        return write(path, value)

    monkeypatch.setattr(jobs, "_write", failing_heartbeat)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(jobs.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    jobs.supervise(directory)
    record = jobs.status_of(directory)
    assert signals == [(child.pid, signal.SIGTERM)]
    assert record["state"] == "launch-error" and record["stage"] == "supervision"
    assert record["command_started"] is True and record["command_pid"] == child.pid
    assert record["cleanup_state"] == "complete" and record["cleanup_scope"] == "local-command"
    assert 0 <= waits[-1] <= 19
    assert ("status write failed" if fault == "heartbeat_write" else "wait failed") in record["error"]


@pytest.mark.parametrize("fault", ["supervisor_setup", "command_start"])
def test_pre_spawn_error_never_signals_a_process(tmp_path, monkeypatch, fault):
    directory = control(tmp_path)
    write = jobs._write
    spawned = []

    def failing_write(path, value):
        if fault == "supervisor_setup" and value.get("state") == "starting":
            raise OSError("initial status unavailable")
        return write(path, value)

    def failing_spawn(*args, **kwargs):
        spawned.append(args)
        raise FileNotFoundError("command unavailable")

    monkeypatch.setattr(jobs, "_write", failing_write)
    monkeypatch.setattr(jobs.subprocess, "Popen", failing_spawn)
    monkeypatch.setattr(jobs.os, "killpg", lambda *args: pytest.fail("no command to signal"))
    jobs.supervise(directory)
    record = jobs.status_of(directory)
    assert record["state"] == "launch-error" and record["stage"] == fault
    assert record["command_started"] is False and "command_pid" not in record
    assert "cleanup_state" not in record
    assert len(spawned) == (fault == "command_start")


@pytest.mark.parametrize("operation", ["write", "replace", "replace_then_fail"])
def test_transient_status_write_reopens_without_restarting_command(tmp_path, monkeypatch, operation):
    directory = control(tmp_path)
    failures, sleeps, spawns = [], [], []
    write_text, replace = Path.write_text, Path.replace

    def write(path, payload, *args, **kwargs):
        if (operation == "write" and path.name == "status.tmp"
                and '"state": "supervising"' in payload and len(failures) < 2):
            failures.append(path)
            raise OSError(errno.ESTALE, "stale status handle")
        return write_text(path, payload, *args, **kwargs)

    def rename(path, target):
        if (operation != "write" and path.name == "status.tmp"
                and '"state": "supervising"' in path.read_text() and len(failures) < 2):
            failures.append(path)
            if operation == "replace_then_fail":
                replace(path, target)
            raise OSError(errno.EIO, "metadata replace interrupted")
        return replace(path, target)

    class Child:
        pid = 12345

        def wait(self, *, timeout):
            return 0

    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(Path, "replace", rename)
    monkeypatch.setattr(jobs.time, "sleep", sleeps.append)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: spawns.append(a) or Child())
    monkeypatch.setattr(jobs.os, "killpg", lambda *args: pytest.fail("transient metadata error must not stop work"))
    jobs.supervise(directory)
    assert len(spawns) == 1 and len(failures) == 2 and sleeps == [.05, .1]
    record = jobs.status_of(directory)
    assert record["state"] == "completed" and record["returncode"] == 0
    assert record["command_started"] is True
    assert not (directory / "status.tmp").exists()


@pytest.mark.parametrize("error_code,attempts", [(errno.ESTALE, 8), (521, 8), (errno.EACCES, 1), (errno.ENOSPC, 1)])
def test_permanent_metadata_failure_is_bounded(tmp_path, monkeypatch, error_code, attempts):
    calls = []

    def failed(path, *args, **kwargs):
        calls.append(path)
        raise OSError(error_code, "control write failed")

    monkeypatch.setattr(Path, "write_text", failed)
    monkeypatch.setattr(jobs.time, "sleep", lambda _: None)
    with pytest.raises(OSError) as raised:
        jobs._write(tmp_path / "status.json", {"state": "supervising"})
    assert raised.value.errno == error_code and len(calls) == attempts


@pytest.mark.parametrize("cleanup_seconds", [None, 19])
@pytest.mark.parametrize("outcome", ["term", "kill", "missing_group", "unknown"])
def test_supervision_fault_uses_existing_cleanup_budget(tmp_path, monkeypatch, cleanup_seconds, outcome):
    directory = control(tmp_path, cleanup_seconds=cleanup_seconds)
    signals, waits = [], []
    write = jobs._write
    monkeypatch.setattr(jobs.time, "monotonic", lambda: 100.0)

    class Child:
        pid = 12345
        returncode = None

        def wait(self, *, timeout):
            waits.append(timeout)
            if outcome == "unknown":
                raise OSError("cannot reap child")
            if outcome == "kill" and signals[-1][1] == signal.SIGTERM:
                raise subprocess.TimeoutExpired("fixture child", timeout)
            self.returncode = -int(signals[-1][1])
            return self.returncode

        def poll(self):
            if outcome == "unknown":
                raise OSError("cannot observe child")
            return self.returncode

    def failed(path, value):
        if value.get("state") == "supervising":
            raise RuntimeError("fixture supervision error")
        return write(path, value)

    def signal_owned(pid, sig):
        signals.append((pid, sig))
        if outcome == "missing_group":
            raise ProcessLookupError
        if outcome == "unknown":
            raise PermissionError("cannot signal owned group")

    monkeypatch.setattr(jobs, "_write", failed)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: Child())
    monkeypatch.setattr(jobs.os, "killpg", signal_owned)
    jobs.supervise(directory)
    record = jobs.status_of(directory)
    assert record["state"] == "launch-error" and record["stage"] == "supervision"
    assert record["error"] == "RuntimeError: fixture supervision error"
    assert record["cleanup_scope"] == "local-command"
    expected_signals = [signal.SIGTERM, signal.SIGKILL] if outcome in {"kill", "unknown"} else [signal.SIGTERM]
    assert signals == [(Child.pid, sig) for sig in expected_signals]
    assert waits == [cleanup_seconds] * len(expected_signals)
    assert record["cleanup_state"] == ("unconfirmed" if outcome == "unknown" else "complete")
    if outcome == "unknown":
        assert record["returncode"] is None and len(record["cleanup_errors"]) == 5


def test_failed_final_receipt_does_not_abandon_child_or_claim_success(tmp_path, monkeypatch, capsys):
    directory = control(tmp_path)
    write_text = Path.write_text
    signals, failures = [], []

    class Child:
        pid = 12345
        returncode = None

        def wait(self, *, timeout):
            assert signals
            self.returncode = -signal.SIGTERM
            return self.returncode

        def poll(self):
            return self.returncode

    child = Child()

    def failed(path, payload, *args, **kwargs):
        if path.name == "status.tmp" and '"state": "starting"' not in payload:
            failures.append(path)
            raise OSError(errno.EIO, "control storage unavailable")
        return write_text(path, payload, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failed)
    monkeypatch.setattr(jobs.time, "sleep", lambda _: None)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(jobs.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    with pytest.raises(OSError, match="control storage unavailable"):
        jobs.supervise(directory)
    assert len(failures) == 16 and child.poll() == -signal.SIGTERM
    assert signals == [(child.pid, signal.SIGTERM)]
    log = capsys.readouterr().err
    assert "state=launch-error" in log and "cleanup=complete" in log and "command_started=True" in log
    assert "/fixture/command" not in log
    # Missing terminal evidence remains unknown to readers, not an invented PASS.
    assert jobs.status_of(directory)["state"] == "starting"


def test_copied_stdlib_supervisor_reaps_real_child_after_fault(tmp_path):
    directory = control(tmp_path, cleanup_seconds=2)
    plan = json.loads((directory / "launch.json").read_text())
    plan["argv"] = [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"]
    (directory / "launch.json").write_text(json.dumps(plan))
    runner = tmp_path / "copied-supervisor.py"
    shutil.copyfile(jobs.__file__, runner)
    code = '''
import importlib.abc, os, pathlib, runpy, signal, subprocess, sys
class NoCheckout(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'behave', 'tools'}:
            raise AssertionError('copied supervisor must remain stdlib-only')
sys.meta_path.insert(0, NoCheckout())
supervise = runpy.run_path(sys.argv[1])['supervise']
scope = supervise.__globals__
write, spawn = scope['_write'], subprocess.Popen
children = []
def capture(*args, **kwargs):
    assert kwargs['start_new_session'] is True
    child = spawn(*args, **kwargs)
    children.append(child)
    return child
def failed(path, value):
    if value.get('state') == 'supervising':
        raise OSError('fixture persistent heartbeat failure')
    return write(path, value)
subprocess.Popen, scope['_write'] = capture, failed
try:
    supervise(pathlib.Path(sys.argv[2]))
    assert len(children) == 1 and children[0].poll() is not None
finally:
    for child in children:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=5)
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(runner), str(directory)],
                            cwd=tmp_path, capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    record = jobs.status_of(directory)
    assert record["state"] == "launch-error" and record["stage"] == "supervision"
    assert record["command_started"] is True and record["cleanup_state"] == "complete"
