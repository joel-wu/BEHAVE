"""Bash previews bound model context without killing commands or losing output."""

from dataclasses import replace

import pytest
from test_agent_tools import shell_runner  # noqa: F401

from behave.agent import commands
from behave.agent.commands import CommandRunner, shell_commands
from behave.agent.local_workspace import LocalWorkspace
from behave.agent.workspace import Workspace
from behave.core import runtime_defaults as defaults
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.isolation import IsolationResult, IsolationStatus, ResourceLimits


@pytest.mark.parametrize("local", [False, True])
def test_single_line_output_is_bounded_and_complete_file_is_readable_next_call(
        tmp_path, monkeypatch, shell_runner, local):
    store = DirectoryStore(tmp_path / "objects")
    workspace = LocalWorkspace(store, tmp_path / "attempt") if local else Workspace(store)
    limit = defaults.SHELL_OUTPUT_PREVIEW_BYTES
    runner = CommandRunner(shell_runner.template, writable_workspace=True, output_preview_bytes=limit)
    stdout = b'{"value":"' + b"a" * (limit * 4) + b'"}'
    stderr = b"command failed normally\n"
    calls = []

    def execute(spec):
        calls.append(spec)
        assert spec.limits == shell_runner.template.limits
        return IsolationResult(IsolationStatus.EXITED, spec.digest, "fixture",
                               exit_code=7, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(commands, "run", execute)
    result = runner.execute(workspace, {"command": "cat one-line.json"})
    body = result.content
    assert body["exit_code"] == 7 and body["status"] == "exited"
    assert len(body["stdout"].encode()) + len(body["stderr"].encode()) <= limit
    assert body["stderr"] == stderr.decode()
    info = body["output_preview"]
    assert info["limit_bytes"] == limit
    assert info["stdout"]["bytes"] == len(stdout) and info["stdout"]["truncated"]
    assert not info["stderr"]["truncated"]
    path = info["stdout"]["path"].removeprefix("/workspace/")
    assert workspace.read(path) == stdout
    assert store.read(result.artifacts[0]) == stdout
    with workspace.materialize() as root:
        assert (root / path).read_bytes() == stdout
    if not local:
        snapshot, _ = workspace.checkpoint()
        restored = Workspace(store)
        restored.restore(snapshot)
        assert restored.read(path) == stdout
    assert len(calls) == 1  # Output truncation never reexecutes or kills the command.


def test_preview_policy_is_part_of_tool_identity_and_description(shell_runner):
    legacy = shell_runner.tool()
    limited = CommandRunner(shell_runner.template, writable_workspace=True, output_preview_bytes=32768).tool()
    assert legacy.revision != limited.revision
    assert legacy.parameters == limited.parameters
    assert "32768 UTF-8 bytes" in limited.description
    assert "does not stop the command" in limited.description
    assert "Command output is included in the conversation." in legacy.description


def test_syn_sim_shell_wrapper_preserves_preview_configuration(tmp_path, monkeypatch, shell_runner):
    from behave.data.author_check import behavior_check_shell
    from behave.task_execution.judge_policy import JudgePolicy

    runner = CommandRunner(shell_runner.template, writable_workspace=True, output_preview_bytes=24)
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    monkeypatch.setattr(commands, "run", lambda spec: IsolationResult(
        IsolationStatus.EXITED, spec.digest, "fixture", exit_code=0, stdout=b"z" * 1000))
    tool = behavior_check_shell(runner, JudgePolicy("fixture-command", "fixture-authority").to_bytes(),
                               executor=lambda *a, **k: pytest.fail("ordinary Bash must not run Sim"))
    content = tool.handler(workspace, {"command": "cat verbose.json"}).content
    assert len(content["stdout"].encode()) == 24
    assert content["output_preview"]["limit_bytes"] == 24


def test_solver_and_syn_policies_roundtrip_and_keep_old_packets_unlimited(tmp_path, publication):
    from test_solver_process import setup

    from behave.data.role import ReaderInputs, RoleJob
    from behave.data.role_policy import RolePolicy
    from behave.task_execution.solver import SolverJob

    job, journal, _ = setup(tmp_path, publication, "pass")
    for data in job.input_blobs():
        journal.objects.put(data)
    assert job.output_preview_bytes == defaults.SHELL_OUTPUT_PREVIEW_BYTES
    assert SolverJob.from_bytes(job.to_bytes(), journal.objects) == job
    old = parse_json(job.to_bytes())
    old.pop("output_preview_bytes")
    restored = SolverJob.from_bytes(canonical_json(old), journal.objects)
    assert restored.output_preview_bytes is None and restored.to_bytes() == canonical_json(old)
    policy = RolePolicy(job.model, shell_limits=ResourceLimits(), output_preview_bytes=99)
    assert RolePolicy.from_dict(parse_json(canonical_json(policy.as_dict()))) == policy
    old_policy = parse_json(canonical_json(policy.as_dict()))
    old_policy.pop("output_preview_bytes")
    assert RolePolicy.from_dict(old_policy).output_preview_bytes is None
    reader = RoleJob(ReaderInputs(job.task.revision), replace(job.attempt, role="spec_reader"), job.model,
                     shell_limits=policy.shell_limits, output_preview_bytes=99)
    assert RoleJob.from_bytes(reader.to_bytes()) == reader
    old_reader = parse_json(reader.to_bytes())
    old_reader.pop("output_preview_bytes")
    restored_reader = RoleJob.from_bytes(canonical_json(old_reader))
    assert restored_reader.output_preview_bytes is None
    assert restored_reader.to_bytes() == canonical_json(old_reader)


@pytest.mark.isolation
def test_real_bash_can_read_saved_overflow_without_repeating_whole_output(tmp_path):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    runner = shell_commands(ResourceLimits(processes=16, wall_seconds=30), output_preview_bytes=32)
    first = runner.execute(workspace, {"command": "python -c \"print('abcdef' * 1000)\"; exit 7"})
    assert first.content["exit_code"] == 7
    path = first.content["output_preview"]["stdout"]["path"]
    second = runner.execute(workspace, {"command": f"head -c 6 '{path}'"})
    assert second.content["exit_code"] == 0 and second.content["stdout"] == "abcdef"
    assert "output_preview" not in second.content
