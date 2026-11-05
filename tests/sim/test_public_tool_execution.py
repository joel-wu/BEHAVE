"""One host-facing tool API, complete file-backed logs, no log execution quota."""

import json
from pathlib import Path

import pytest

from behave.isolation import IsolationResult, IsolationStatus, binary, read_only
from behave.sim import api
from behave.sim.rtl import _tool_job as guest
from behave.sim.rtl import tool_isolation as tools
from behave.sim.rtl import tool_outputs, tool_protocol, tool_runtime


def test_public_environment_extensions_do_not_mutate_the_shared_profile(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    runner.write_bytes(b"installed runner")
    runner.chmod(0o755)
    resource = tmp_path / "cells.lib"
    resource.write_bytes(b"library")
    base = tool_runtime.FrozenToolRuntime("eda", (binary(str(runner), "/python"),), (), ())
    monkeypatch.setattr(tool_runtime, "freeze_runtime", lambda profile: base)
    extra = binary(str(runner), "/sta")
    mount = read_only(str(resource), "/cells.lib")
    environment = api.prepare_tool_environment(binaries=(extra,), mounts=(mount,))
    assert base.mounts == () and len(base.binaries) == 1
    seen = []

    def run(workspace, groups, **options):
        seen.append(options)
        return "receipt"

    monkeypatch.setattr(tools, "run_workspace", run)
    group = api.ToolGroup((api.ToolCommand("sta", ("/sta",)),))
    assert environment.execute(tmp_path, (group,), role="timing") == "receipt"
    supplied = seen[0]["frozen_runtime"]
    assert supplied.binaries == (*base.binaries, extra)
    assert supplied.mounts == (mount,)
    assert environment.identity == supplied.identity != base.identity


def test_log_files_are_retained_outside_the_next_input_workspace(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    runner.write_bytes(b"runner")
    runner.chmod(0o755)
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _: ((binary(str(runner), "/python"),), (), ()))
    payload = b"start\n" + b"x" * (9 << 20) + b"\nRESULT=42\n"

    def execute(spec):
        logs = Path(next(root.source for root in spec.writable if root.target == "/logs"))
        job = json.loads(Path(next(m.source for m in spec.read_only if m.target == "/input/job.json")).read_text())
        assert "command_output_bytes" not in job
        (logs / "0-0.stdout").write_bytes(payload)
        (logs / "0-0.stderr").write_bytes(b"warning\n")
        return IsolationResult(IsolationStatus.COMPLETED, spec.digest, "test", exit_code=0,
            stdout=json.dumps({"version": tool_protocol.JOB_VERSION, "commands": [{
                "id": "measure", "returncode": 0, "timed_out": False,
                "stdout_file": "0-0.stdout", "stderr_file": "0-0.stderr"}],
                "outputs": {}, "trees": {}}).encode())

    monkeypatch.setattr(tools, "run", execute)
    workspace = tmp_path / "work"
    workspace.mkdir()
    result = api.prepare_tool_environment().execute(workspace,
        (api.ToolGroup((api.ToolCommand("measure", ("/python",)),)),), role="measure")
    assert result.status is IsolationStatus.COMPLETED
    assert api.command_stream(result, "measure", "stdout") == payload
    assert api.command_log(result, "measure", "stdout").read_bytes() == payload
    assert api.command_stream(result, "measure", "stderr") == b"warning\n"
    assert not Path(result.log_directory).is_relative_to(workspace)
    assert len(json.dumps(result.as_dict())) < 4096
    assert tool_protocol.ToolExecution.validate_image(result.as_dict()) == result.as_dict()


@pytest.mark.parametrize("same_bytes", [True, False])
def test_environment_merges_only_byte_identical_dependency_mounts(tmp_path, monkeypatch, same_bytes):
    tree = tmp_path / "lib"
    tree.mkdir()
    (tree / "dep.so").write_bytes(b"original")
    private = tmp_path / "private.so"
    private.write_bytes(b"original" if same_bytes else b"different")
    broad = read_only(str(tree), "/lib")
    leaf = read_only(str(private), "/lib/dep.so")
    runner = tmp_path / "runner"
    runner.write_bytes(b"installed runner")
    runner.chmod(0o755)
    base = tool_runtime.FrozenToolRuntime("eda", (binary(str(runner), "/python"),), (broad,), ())
    monkeypatch.setattr(tool_runtime, "freeze_runtime", lambda profile: base)
    seen = []
    monkeypatch.setattr(tools, "run_workspace",
                        lambda *a, **kw: seen.append(kw["frozen_runtime"]))

    environment = api.prepare_tool_environment(mounts=(leaf, broad))
    environment.execute(tmp_path, ())
    # A different library must reach isolation's conflict validation, never be
    # silently replaced by whatever the base profile has at the same path.
    assert seen[0].mounts == ((broad,) if same_bytes else (broad, leaf))
    assert base.mounts == (broad,)


def test_shared_isolation_layer_does_not_prepare_sim_profiles():
    import importlib
    import inspect

    shared = importlib.import_module("behave.isolation.runtime_environment")
    assert "behave.sim" not in inspect.getsource(shared)
    assert api.prepare_runtime.__module__ == "behave.sim.tool_execution"


@pytest.mark.parametrize("part_bytes", [1, 8, 64, None])
@pytest.mark.parametrize("size", [0, 1, 8, 9, 128, 257])
def test_guest_log_parts_retain_every_byte_and_merge_in_numeric_order(
    tmp_path, monkeypatch, part_bytes, size,
):
    limit = guest.resource.RLIM_INFINITY if part_bytes is None else part_bytes
    monkeypatch.setattr(guest.resource, "getrlimit", lambda _: (limit, limit))
    logs = tmp_path / "logs"
    logs.mkdir()
    payload = bytes(index % 256 for index in range(size))
    writer = guest._CommandLog(logs / "0-0.stdout")
    for start in range(0, len(payload), 13):
        writer.write(payload[start:start + 13])
    writer.close()
    if part_bytes is not None:
        assert all(path.stat().st_size <= part_bytes for path in logs.iterdir())
    groups = (tool_protocol.ToolGroup((tool_protocol.ToolCommand("test", ("/python",)),)),)
    directory = tool_outputs.retain_command_logs(logs, tmp_path / "work", groups)
    assert (Path(directory) / "0-0.stdout").read_bytes() == payload
    assert len(tuple(Path(directory).iterdir())) == 1


@pytest.mark.parametrize("names", [("0-0.stdout.part-1",),
                                  ("0-0.stdout", "0-0.stdout.part-2")])
def test_log_retention_does_not_silently_drop_missing_parts(tmp_path, names):
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in names:
        (logs / name).write_bytes(b"part")
    groups = (tool_protocol.ToolGroup((tool_protocol.ToolCommand("test", ("/python",)),)),)
    with pytest.raises(ValueError, match="missing parts"):
        tool_outputs.retain_command_logs(logs, tmp_path / "work", groups)


def test_rotated_log_cannot_export_an_undeclared_symlink(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "0-0.stdout").write_bytes(b"first")
    hidden = tmp_path / "hidden"
    hidden.write_bytes(b"private")
    (logs / "0-0.stdout.part-1").symlink_to(hidden)
    groups = (tool_protocol.ToolGroup((tool_protocol.ToolCommand("test", ("/python",)),)),)
    with pytest.raises(OSError):
        tool_outputs.retain_command_logs(logs, tmp_path / "work", groups)
