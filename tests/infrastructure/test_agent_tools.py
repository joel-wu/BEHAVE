"""Tool boundaries without launching a model, simulator or isolation worker."""


import pytest

from behave.agent import commands
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.commands import CommandRunner
from behave.agent.journal import AttemptJournal, Event
from behave.agent.loop import run_attempt
from behave.agent.model import Charge, ModelReply, ToolCall
from behave.agent.tools import (
    current_invocation,
    file_tools,
    object_schema,
    tool_invocation,
)
from dataclasses import replace

from behave.agent.trajectory import trajectory
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json
from behave.core.storage import DirectoryStore
from behave.isolation import (
    Binary,
    IsolationRefusal,
    IsolationResult,
    IsolationSpec,
    IsolationStatus,
    ResourceLimits,
    WritableRoot,
)


@pytest.fixture
def runner():
    # Paths are deliberately unavailable: these tests must never launch them.
    return CommandRunner(IsolationSpec(
        argv=("/bin/python", "-B"),
        binaries=(Binary("/unavailable/python", "/bin/python", "0" * 64),),
        writable=(WritableRoot("/work", 4096),),
        environment=(("FIXTURE", "fixed"),),
        limits=ResourceLimits(file_bytes=8192),
    ))


@pytest.fixture
def shell_runner():
    return CommandRunner(IsolationSpec(
        argv=("/bin/bash", "--noprofile", "--norc"),
        binaries=(Binary("/unavailable/bash", "/bin/bash", "0" * 64),),
        writable=(WritableRoot("/workspace", 4096),),
        cwd="/workspace",
    ), writable_workspace=True)


@pytest.mark.parametrize("command", [
    "make -j1",
    "printf '%s\\n' 'a b' '$literal' | sed 's/a/A/' > result.txt",
    "python - <<'PY'\nprint('hello \"world\"')\nPY\n",
])
def test_shell_accepts_command_text_and_supplies_bash_arguments(tmp_path, monkeypatch, shell_runner, command):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    seen = []

    def execute(spec):
        seen.append(spec.argv)
        return IsolationResult(IsolationStatus.EXITED, spec.digest, "fixture",
                               exit_code=7, stdout=b"output\n", stderr=b"error\n")

    monkeypatch.setattr(commands, "run", execute)
    result = shell_runner.execute(workspace, {"command": command})
    assert seen == [("/bin/bash", "--noprofile", "--norc", "-c", command)]
    assert result.content["exit_code"] == 7
    assert result.content["stdout"] == "output\n"
    assert result.content["stderr"] == "error\n"
    declaration = shell_runner.tool().declaration()
    assert declaration["parameters"] == object_schema({"command": {"type": "string"}})
    assert '{"command": "ls"}' in declaration["description"]


@pytest.mark.parametrize("status,exit_code,signal", [
    (IsolationStatus.COMPLETED, 0, None),
    (IsolationStatus.EXITED, 7, None),
    (IsolationStatus.TIMED_OUT, None, None),
    (IsolationStatus.SIGNALED, None, 15),
])
def test_command_feedback_keeps_outputs_and_timing_but_not_internal_metadata(
        tmp_path, monkeypatch, shell_runner, status, exit_code, signal):
    from test_attempts import Scripted, call

    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("command-feedback", "fixture", {})
    stdout, stderr = b"output\n" * 600 + b"\xff", b"diagnostic\x00\xff"
    executions = []

    def execute(policy):
        executions.append(policy)
        return IsolationResult(status, policy.digest, "fixture-backend", stdout=stdout,
                               stderr=stderr, exit_code=exit_code, signal=signal)

    ticks = iter((100., 102.5))
    monkeypatch.setattr(commands, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(commands, "run", execute)
    backend = Scripted(call("run_command", {"command": "make"}), ModelReply("done"))
    result = run_attempt(journal=journal, backend=backend, spec=spec,
                         initial_messages=(), tools=(shell_runner.tool(),))
    feedback = backend.requests[1].messages[-2]["content"]
    assert dict(feedback) == {
        "status": status.value, "exit_code": exit_code, "signal": signal,
        "elapsed_seconds": 2.5,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }
    settled = next(event for event in journal.load() if event.kind == "tool_settled")
    payloads = [journal.objects.read(ref) for ref in settled.artifacts]
    assert stdout in payloads and stderr in payloads
    reports = [parse_json(data) for data in payloads if data.startswith(b'{"backend"')]
    assert len(reports) == 1
    report = reports[0]
    assert report["backend"] == "fixture-backend"
    assert report["isolation_policy"] == executions[0].digest
    assert report["elapsed_seconds"] == 2.5 and report["status"] == status.value
    assert journal.objects.read(BlobRef.from_dict(report["stdout_bytes"])) == stdout
    assert journal.objects.read(BlobRef.from_dict(report["stderr_bytes"])) == stderr
    assert "stdout" not in report and "stderr" not in report
    # Cold replay reuses both the feedback and measured time, without executing
    # the command or consulting the clock again.
    assert run_attempt(journal=journal, backend=Scripted(), spec=spec,
                       initial_messages=(), tools=(shell_runner.tool(),)) == result
    assert len(executions) == 1


@pytest.mark.parametrize("arguments", [
    {}, {"command": None}, {"command": ["ls"]}, {"command": "a\x00b"},
    {"args": ["-c", "ls"]}, {"command": "ls", "timeout": 10},
])
def test_shell_arguments_match_the_published_interface(tmp_path, monkeypatch, shell_runner, arguments):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    monkeypatch.setattr(commands, "run", lambda *_: pytest.fail("invalid shell arguments must not execute"))
    result = shell_runner.execute(workspace, arguments)
    assert result.content["error"] in {"record-fields-invalid", "command-args-invalid"}
    assert result.content["message"]
    assert result.content["executed"] is False
    assert not workspace.snapshot().files


@pytest.mark.parametrize("code", [
    "isolation-namespace-unavailable", "isolation-supervisor-timeout",
    "isolation-output-capture-failed",
])
def test_command_refusal_preserves_reason_output_and_cold_replay(tmp_path, monkeypatch, runner, code):
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("command-refusal", "fixture", {})
    refusal = IsolationRefusal(code, "fixture explanation", {"phase": "fixture"})
    executions = []

    def execute(policy):
        executions.append(policy)
        return IsolationResult(IsolationStatus.REFUSED, policy.digest, "fixture-backend",
                               stdout=b"partial output\xff", stderr=b"diagnostic\x00", refusal=refusal)

    class Backend:
        calls = 0

        def generate(self, request):
            self.calls += 1
            if self.calls > 1:
                pytest.fail("refusal must interrupt, not request another model reply")
            return ModelReply("", (ToolCall("cmd", "run_command", {"args": ["-c", "pass"]}),),
                              charge=Charge("0.1", "USD", "fixture"))

    monkeypatch.setattr(commands, "run", execute)
    backend = Backend()
    for _ in range(2):
        with pytest.raises(AttemptInterrupted, match="^" + code + "$"):
            run_attempt(journal=journal, backend=backend, spec=spec,
                        initial_messages=(), tools=(runner.tool(),))
    assert backend.calls == len(executions) == 1
    settled = journal.load()[-1]
    assert settled.kind == "tool_settled" and settled.payload["result"] is None
    payloads = [journal.objects.read(ref) for ref in settled.artifacts]
    assert b"partial output\xff" in payloads and b"diagnostic\x00" in payloads
    reports = [parse_json(data) for data in payloads if data.startswith(b'{"backend"')]
    assert len(reports) == 1
    report = reports[0]
    assert report["refusal"] == refusal.as_dict()
    assert report["isolation_policy"] == executions[0].digest
    assert report["backend"] == "fixture-backend" and report["status"] == "refused"
    assert "score" not in report
    assert trajectory(journal).cost_by_currency() == {"USD": "0.1"}


@pytest.mark.parametrize("arguments", [{}, {"args": "-c"}, {"args": [1]}, {"args": ["a\x00b"]}])
def test_invalid_arguments_do_not_materialize_or_launch(tmp_path, monkeypatch, runner, arguments):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))

    def forbidden(*args, **kwargs):
        pytest.fail("invalid tool arguments must fail before materialization or execution")

    monkeypatch.setattr(workspace, "materialize", forbidden)
    monkeypatch.setattr(commands, "run", forbidden)
    result = runner.execute(workspace, arguments)
    assert result.content["error"] in {"record-fields-invalid", "command-args-invalid"}
    assert result.content["message"]
    assert result.content["executed"] is False


def test_invocation_context_restores_outer_owner_even_after_failure():
    first = Event(0, None, "tool_requested", {}, ())
    second = Event(1, BlobRef.of(first.to_bytes()), "tool_requested", {}, ())
    assert current_invocation() is None
    with tool_invocation(first):
        with pytest.raises(RuntimeError), tool_invocation(second):
            assert current_invocation() is second
            raise RuntimeError("fixture")
        assert current_invocation() is first
    assert current_invocation() is None


def test_file_tools_only_change_managed_files_and_validate_arguments(tmp_path):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    tools = {tool.name: tool for tool in file_tools()}
    tools["write_file"].handler(workspace, {"path": "nested/a.py", "content": "μ\n"})
    assert tools["read_file"].handler(workspace, {"path": "nested/a.py"}).content["content"] == "μ\n"
    listing = tools["list_files"].handler(workspace, {}).content["files"]
    assert [file["path"] for file in listing] == ["nested/a.py"]
    assert not (tmp_path / "nested").exists()
    with pytest.raises(RecordError):
        tools["list_files"].handler(workspace, {"unexpected": True})
    with pytest.raises(RecordError):
        tools["write_file"].handler(workspace, {"path": "bad", "content": 7})
    workspace.write("binary", b"\xff")
    with pytest.raises(RecordError, match="UTF-8"):
        tools["read_file"].handler(workspace, {"path": "binary"})
    tools["delete_file"].handler(workspace, {"path": "nested/a.py"})
    with pytest.raises(RecordError):
        tools["read_file"].handler(workspace, {"path": "nested/a.py"})
