"""Command execution and cleanup are separate evidence, without extra polling."""

import base64
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from behave.isolation import IsolationRefusal, IsolationResult, IsolationStatus
from behave.sim.rtl import _tool_job as guest
from behave.sim.rtl import tool_isolation as tools
from behave.sim.rtl import tool_protocol, tool_runtime
from tests.sim.test_rtl_tool_isolation import _command_row, _runtime


@pytest.fixture
def command_owner(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    events = []
    child = SimpleNamespace(returncode=None, killed=False, timeout=False, cleanup_fails=False)
    child.stdout = SimpleNamespace(fileno=lambda: 1, close=lambda: None)
    child.stderr = SimpleNamespace(fileno=lambda: 2, close=lambda: None)
    child.poll = lambda: child.returncode

    def wait(timeout):
        events.append(("wait", timeout))
        if child.returncode is not None:
            return child.returncode
        if (child.killed and child.cleanup_fails) or (not child.killed and child.timeout):
            clock.now += timeout
            raise subprocess.TimeoutExpired("tool", timeout)
        child.returncode = -9 if child.killed else 0
        return child.returncode

    def kill(_):
        child.killed = True

    child.wait = wait

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def register(self, *_):
            pass

        def get_map(self):
            return {}

        def select(self, timeout):
            events.append(("select", timeout))
            clock.now += timeout
            return []

    monkeypatch.setattr(guest, "Path", lambda path: tmp_path if path == "/work" else Path(path))
    monkeypatch.setattr(guest, "_LOG_ROOT", tmp_path)
    monkeypatch.setattr(guest.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(guest.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(guest.os, "set_blocking", lambda *_: None)
    monkeypatch.setattr(guest, "_kill_group", kill)
    monkeypatch.setattr(guest.selectors, "DefaultSelector", Selector)
    return child, clock, events


def command():
    return {"id": "compile", "argv": ["/tool"], "cwd": ".", "environment": {}, "timeout_s": 5}


def test_closed_output_pipes_use_command_deadline_without_private_polling(command_owner):
    _, clock, events = command_owner
    row = guest._command(command(), 1024, 7)
    assert row["returncode"] == 0
    assert not row["timed_out"]
    assert events == [("wait", 5), ("wait", 7)]
    assert clock.now == 0


def test_cleanup_timeout_retains_original_command_timeout(command_owner):
    child, clock, _ = command_owner
    child.timeout = child.cleanup_fails = True
    row = guest._command(command(), 1024, 7)
    assert row["timed_out"] is True
    assert row["returncode"] is None
    assert "TimeoutExpired" in row["cleanup_error"]
    assert clock.now == 12


def test_cleanup_timeout_does_not_replace_a_supervision_exception(command_owner, monkeypatch):
    child, _, _ = command_owner
    child.cleanup_fails = True

    def failed_poll(*_):
        raise OSError("original pipe failure")

    selector = guest.selectors.DefaultSelector
    monkeypatch.setattr(selector, "get_map", lambda _: {"pipe": True})
    monkeypatch.setattr(selector, "select", failed_poll)
    with pytest.raises(OSError, match="original pipe failure") as caught:
        guest._command(command(), 1024, 7)
    assert any("cleanup" in note.lower() for note in caught.value.__notes__)


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
@pytest.mark.parametrize("execution_error", [False, True])
def test_pipe_close_failure_preserves_command_cause(command_owner, monkeypatch, channel, execution_error):
    child, _clock, _events = command_owner

    def failed_close():
        raise OSError(f"{channel} close failed")

    monkeypatch.setattr(getattr(child, channel), "close", failed_close)
    if execution_error:
        def failed_poll(*_):
            raise OSError("original execution failure")

        selector = guest.selectors.DefaultSelector
        monkeypatch.setattr(selector, "get_map", lambda _: {"pipe": True})
        monkeypatch.setattr(selector, "select", failed_poll)
        with pytest.raises(OSError, match="original execution failure") as caught:
            guest._command(command(), 1024, 7)
        assert any(f"{channel} close failed" in note for note in caught.value.__notes__)
    else:
        child.timeout = True
        row = guest._command(command(), 1024, 7)
        assert row["timed_out"] is True
        assert f"{channel} close failed" in row["cleanup_error"]


def test_tool_stop_and_reap_share_the_cleanup_deadline(command_owner, monkeypatch):
    child, clock, events = command_owner

    def stop(_):
        clock.now += 3
        child.killed = True

    monkeypatch.setattr(guest, "_kill_group", stop)
    guest._command(command(), 1024, 7)
    assert events[-1] == ("wait", 4)


@pytest.mark.parametrize("parallel", [1, 2])
def test_group_does_not_start_queued_work_after_unconfirmed_cleanup(monkeypatch, parallel):
    called = []

    def execute(row, *_):
        called.append(row["id"])
        return {"id": row["id"], "cleanup_error": "could not reap"}

    # Deterministic executor order checks the common dispatch function in both
    # paths. Running work is joined by the actual executor's context manager.
    class Executor:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def map(self, function, values):
            return map(function, values)

    monkeypatch.setattr(guest, "_command", execute)
    monkeypatch.setattr(guest.concurrent.futures, "ThreadPoolExecutor", Executor)
    result = guest._group({"parallel": parallel, "commands": [{"id": "a"}, {"id": "b"}],
                           "when_executable_glob": None}, 1024, 7)
    assert called == ["a"]
    assert result[1] == {"id": "b", "skipped": "prior command cleanup unconfirmed"}


def test_guest_publishes_original_failure_and_skips_later_phases(tmp_path, monkeypatch, capsys):
    compile_row = _command_row("compile", timed_out=True, stderr=b"original timeout diagnostic")
    compile_row.update(returncode=None, cleanup_error="could not reap")
    job = {"version": guest._PROTOCOL, "groups": [
        {"commands": [{"id": "compile"}]}, {"commands": [{"id": "simulate"}]}],
        "outputs": ["result"], "output_trees": [], "preserve_outputs": [], "normalizers": [],
        "protocol_bytes": None, "cleanup_seconds": 7,
        "normalization_recursion_limit": 1000}
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job))
    monkeypatch.setattr(guest, "Path", lambda value: job_path if value == "/input/job.json"
                        else tmp_path / value.lstrip("/"))
    called = []

    def execute(row, *_):
        called.append(row)
        return [compile_row]

    monkeypatch.setattr(guest, "_group", execute)
    guest._main()
    assert called == [job["groups"][0]]
    result = json.loads(capsys.readouterr().out)
    assert result == {"version": guest._PROTOCOL, "cleanup_failed": True, "commands": [
        compile_row, {"id": "simulate", "skipped": "prior command cleanup unconfirmed"}]}


def test_cleanup_failure_keeps_command_evidence_but_never_publishes_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _: _runtime(tmp_path))
    stderr = b"the original compiler diagnostic\n" * 200
    row = _command_row("compile", timed_out=True, stderr=stderr)
    row.update(returncode=None, cleanup_error="TimeoutExpired: could not reap compiler")
    protocol = {"version": tool_protocol.JOB_VERSION, "commands": [row], "cleanup_failed": True}

    def execute(spec):
        return IsolationResult(IsolationStatus.COMPLETED, spec.digest, "test",
                               exit_code=0, stdout=json.dumps(protocol).encode())

    monkeypatch.setattr(tools, "run", execute)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result").write_bytes(b"untrusted prior output")
    result = tools.run_workspace(workspace, (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
        "compile", ("/runtime/python",)),)),), outputs=("result",), role="cleanup-test")
    assert result.status is IsolationStatus.REFUSED
    assert result.refusal.code == "rtl-tool-cleanup-unconfirmed"
    assert len(result.commands) == 1
    assert base64.b64decode(result.commands[0]["stderr"]) == stderr
    assert result.commands[0]["timed_out"] is True
    assert not result.outputs
    with pytest.raises(tool_protocol.ToolIsolationError, match="cleanup"):
        tool_protocol.require_completed(result)


@pytest.mark.parametrize("mutation", ["missing-flag", "no-failed-command", "false-flag"])
def test_cleanup_envelope_must_agree_with_the_command_ledger(tmp_path, monkeypatch, mutation):
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _: _runtime(tmp_path))
    row = _command_row("compile")
    row["cleanup_error"] = "could not reap"
    protocol = {"version": tool_protocol.JOB_VERSION, "commands": [row], "cleanup_failed": True}
    if mutation == "missing-flag":
        protocol.pop("cleanup_failed")
        protocol.update(outputs={}, trees={})
    elif mutation == "no-failed-command":
        row.pop("cleanup_error")
    else:
        protocol["cleanup_failed"] = False

    def execute(spec):
        return IsolationResult(IsolationStatus.COMPLETED, spec.digest, "test",
                               exit_code=0, stdout=json.dumps(protocol).encode())

    monkeypatch.setattr(tools, "run", execute)
    result = tools.run_workspace(tmp_path, (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
        "compile", ("/runtime/python",)),)),), role="cleanup-test")
    assert result.refusal.code == "rtl-tool-protocol-invalid"


def test_retired_tool_protocol_is_not_silently_reused():
    with pytest.raises(ValueError, match="version"):
        tool_protocol.decode_protocol(b'{"version":"rtl-tool-job/5","commands":[],"outputs":{},"trees":{}}')


@pytest.mark.parametrize("mutation", ["completed", "outputs", "trees"])
def test_live_and_saved_cleanup_failures_cannot_claim_completed_artifacts(mutation):
    row = _command_row("compile", timed_out=True)
    row.update(returncode=None, cleanup_error="could not reap")
    result = tool_protocol.ToolExecution(
        "test", IsolationStatus.REFUSED, "policy", "backend", "workspace", "job",
        commands=(MappingProxyType(row),), refusal=IsolationRefusal("rtl-tool-cleanup-unconfirmed", "cleanup"))
    image = result.as_dict()
    assert tool_protocol.ToolExecution.validate_image(image) == image
    if mutation == "completed":
        changes = {"status": IsolationStatus.COMPLETED, "refusal": None}
        image.update(status="completed", refusal=None)
    else:
        changes = {mutation: MappingProxyType({"result": b"data" if mutation == "outputs" else "0" * 64})}
        image[mutation] = {"result": "0" * 64}
    with pytest.raises(ValueError, match="cleanup"):
        replace(result, **changes)
    with pytest.raises(ValueError, match="cleanup"):
        tool_protocol.ToolExecution.validate_image(image)
