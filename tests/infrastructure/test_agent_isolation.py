"""Explicit isolation lane: a selected test fails rather than using a host fallback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from shlex import quote

import pytest

from behave.agent.attempt import AttemptSpec
from behave.agent.commands import python_commands, shell_commands
from behave.agent.journal import AttemptJournal
from behave.agent.model import ModelReply, ToolCall
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef
from behave.core.records import parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.isolation import IsolationRefusal, ResourceLimits, capabilities
from behave.task_execution.capture import load_submission
from behave.task_execution.solver import run_solver
from behave.task_execution.submission import ArtifactSelection, SubmissionRequest

pytestmark = pytest.mark.isolation


@pytest.fixture(scope="module")
def runner():
    available = capabilities()
    if type(available) is IsolationRefusal:
        pytest.fail(f"selected isolation lane unavailable: {available.code}: {available.why}")
    return python_commands(ResourceLimits(cpu_seconds=5, wall_seconds=10, rss_bytes=256 << 20,
                                          file_bytes=8 << 20, stdout_bytes=16384, stderr_bytes=16384))


def test_real_edit_check_submit_uses_only_public_files_and_replays_cold(publication, tmp_path, runner, monkeypatch):
    runner = shell_commands(ResourceLimits(cpu_seconds=5, wall_seconds=10))
    task = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    journal = AttemptJournal(tmp_path / "attempt/events", DirectoryStore(tmp_path / "attempt/objects"))
    spec = AttemptSpec("real-edit-check-submit", "solver", {"model": "scripted"})
    monkeypatch.setenv("BEHAVE_TEST_PRIVATE_TOKEN", "must-never-cross")
    selection = SubmissionRequest(ArtifactSelection(("model.py",), "model.py", "M"),
                                  ArtifactSelection(("top.sv",), "top.sv", "top"))
    code = (
        "import os, pathlib, socket; from model import M; "
        "assert M().process(3) == 6; "
        "assert os.environ.get('BEHAVE_TEST_PRIVATE_TOKEN') is None; "
        f"assert not os.path.exists({str(publication.root)!r}); "
        "assert sorted(p.name for p in pathlib.Path('/workspace').iterdir()) == "
        "['include', 'model.py', 'spec.md', 'top.sv']; "
        "pathlib.Path('build-product').write_text('temporary'); print('checked')"
    )
    replies = [
        ModelReply("", (ToolCall("behavior", "run_command", {"command":
            "printf 'class M:\\n    def process(self, a): return a*2\\n' > model.py"}),)),
        ModelReply("", (ToolCall("rtl", "run_command", {"command":
            "printf 'module top; endmodule\\n' > top.sv"}),)),
        ModelReply("", (ToolCall("check", "run_command", {"command": "python -B -c " + quote(code)}),)),
        ModelReply("", (ToolCall("submit", "submit", selection.as_arguments()),)),
    ]

    class Broker:
        calls = 0

        def generate(self, request):
            assert "must-never-cross" not in repr(request)
            assert "PRIVATE_REFERENCE" not in repr(request)
            if self.calls == 3:
                result = request.messages[-2]["content"]
                assert result["status"] == "completed" and result["exit_code"] == 0, result
                assert result["stdout"] == "checked\n", result
            reply = replies[self.calls]
            self.calls += 1
            return reply

    backend = Broker()
    got = run_solver(task=task.public, spec=spec, backend=backend, journal=journal,
                     command_tool=runner.tool())
    assert backend.calls == 4 and got.status == "awaiting_judgment"
    captured = load_submission(journal.objects, got.submission)
    assert captured.behavior.artifact is not None and captured.rtl.artifact is not None
    workspace = Workspace(journal.objects)
    workspace.restore(captured.workspace)
    assert "build-product" not in {file.path for file in workspace.snapshot().files}
    # Fails with IndexError if either a paid model call or a tool is repeated.
    again = run_solver(task=task.public, spec=spec, backend=backend, journal=journal,
                       command_tool=runner.tool())
    assert again == got and backend.calls == 4


def test_commands_cannot_write_source_network_or_host_and_keep_exact_output_bytes(tmp_path, runner):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    workspace.write("a", b"source")
    code = """
import os, socket
for operation in (
    lambda: open('/workspace/a', 'w'),
    lambda: open('/home/host-user/escape', 'w'),
    lambda: socket.socket(),
):
    try:
        operation()
        print('escaped')
    except OSError:
        print('denied')
os.write(2, b'\\xff\\x00')
"""
    result = runner.execute(workspace, {"args": ["-c", code]})
    assert result.content["status"] == "completed"
    assert result.content["stdout"] == "denied\ndenied\ndenied\n"
    diagnostics = parse_json(workspace.store.read(result.artifacts[-1]))
    stderr = BlobRef.from_dict(diagnostics["stderr_bytes"])
    assert workspace.store.read(stderr) == b"\xff\x00"
    assert workspace.read("a") == b"source"


def test_concurrent_command_workspaces_and_scratch_do_not_leak(tmp_path, runner):
    store = DirectoryStore(tmp_path / "objects")

    def execute(index):
        workspace = Workspace(store)
        workspace.write("identity", str(index).encode())
        result = runner.execute(workspace, {"args": ["-c", (
            "from pathlib import Path; assert not Path('/work/prior').exists(); "
            "Path('/work/prior').write_text('private'); print(Path('/workspace/identity').read_text())"
        )]})
        assert result.content["status"] == "completed"
        return result.content["stdout"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(execute, range(4))) == [f"{i}\n" for i in range(4)]


def test_timeout_output_limit_and_nonzero_exit_are_tool_results_not_submission_scores(tmp_path, runner):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    failed = runner.execute(workspace, {"args": ["-c", "raise SystemExit(7)"]})
    assert failed.content["status"] == "exited" and failed.content["exit_code"] == 7
    flooded = runner.execute(workspace, {"args": ["-c", "import os; os.write(1, b'x' * 100000)"]})
    assert flooded.content["status"] == "output-limit"
    assert len(flooded.content["stdout"]) <= 16384
    short = python_commands(ResourceLimits(cpu_seconds=4, wall_seconds=1, rss_bytes=256 << 20,
                                           file_bytes=2 << 20, stdout_bytes=4096, stderr_bytes=4096))
    timed = short.execute(workspace, {"args": ["-c", "import time; time.sleep(30)"]})
    assert timed.content["status"] == "timed-out"
    assert all("score" not in result.content for result in (failed, flooded, timed))
