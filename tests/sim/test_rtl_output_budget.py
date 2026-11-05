"""Output collection limits are resource evidence, never candidate failures."""

import json
from dataclasses import replace
from types import MappingProxyType

import pytest

from behave.isolation import (
    IsolationRefusal,
    IsolationResult,
    IsolationStatus,
    binary,
    capabilities,
)
from behave.sim.core.failure import FailureDisposition, FailureOrigin
from behave.sim.rtl import tool_isolation as tools
from behave.sim.rtl import tool_protocol, tool_runtime
from behave.sim.rtl.frontend_error import tool_failure


@pytest.fixture(autouse=True)
def separate_runtime_for_tool_overrides(tmp_path, monkeypatch):
    # Fake tool declarations must never be published into another test's batch.
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime"))


@pytest.mark.isolation
@pytest.mark.parametrize("size", [64, 2048])
@pytest.mark.parametrize("tree", [False, True])
def test_output_budget_at_the_real_tool_boundary(tmp_path, size, tree):
    available = capabilities()
    if type(available) is IsolationRefusal:
        pytest.skip(f"namespace isolation unavailable: {available.code}")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / "result"
    early = workspace / "early"
    early.write_bytes(b"prior early output")
    if tree and size > 64:
        output.mkdir()
        (output / "old").write_bytes(b"prior")
    elif not tree:
        output.write_bytes(b"prior")
    # Deterministic incompressible bytes also exercise the directory archive path.
    program = (
        "from pathlib import Path; import random; "
        "Path('early').write_bytes(b'collected first'); "
        + ("Path('result').mkdir(); " if tree else "")
        + f"Path({'result/data' if tree else 'result'!r}).write_bytes("
          f"random.Random(17).randbytes({size}))"
    )
    command = tool_protocol.ToolCommand(
        "produce", ("/runtime/python/bin/python", "-c", program), timeout_s=10)
    execution = tools.run_workspace(
        workspace, (tool_protocol.ToolGroup((command,)),), role="output-budget",
        outputs=("early",) if tree else ("early", "result"),
        output_trees=("result",) if tree else (),
        limits=replace(tools.default_limits(wall_seconds=30), stdout_bytes=1024),
        runtime_profile="simulation",
    )
    if size == 64:
        tool_protocol.require_completed(execution)
        assert early.read_bytes() == b"collected first"
        assert (output / "data" if tree else output).stat().st_size == size
        return
    assert execution.status is IsolationStatus.PROTOCOL_LIMIT, execution.as_dict()
    assert execution.commands[0]["returncode"] == 0
    assert execution.output_limit.output == "result"
    assert execution.output_limit.limit_bytes == 768
    assert execution.output_limit.observed_bytes > 768
    assert tool_protocol.ToolExecution.validate_image(execution.as_dict()) == execution.as_dict()
    assert not execution.outputs and not execution.trees
    assert early.read_bytes() == b"prior early output"
    assert (output / "old" if tree else output).read_bytes() == b"prior"
    with pytest.raises(tool_protocol.ToolIsolationError) as caught:
        tool_protocol.require_completed(execution)
    failure = tool_failure(caught.value)
    assert failure.origin is FailureOrigin.RESOURCE_POLICY
    assert failure.disposition is FailureDisposition.NO_RESULT


@pytest.mark.parametrize("mutation", [
    None, "wrong-limit", "not-exceeded", "bool-count", "unknown-output",
    "extra-field", "no-limit", "wrong-ledger", "partial-output",
])
def test_output_limit_protocol_is_bound_to_the_declared_job(
    tmp_path, monkeypatch, mutation,
):
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: (
        (binary(str(executable), "/runtime/python"),), (), ()))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / "result"
    output.write_bytes(b"prior")
    command = tool_protocol.ToolCommand("produce", ("/runtime/python",))

    def completed(spec):
        payload = {
            "version": "rtl-tool-job/7",
            "commands": [{
                "id": "produce", "returncode": 0, "timed_out": False,
                "output_limited": False, "stdout": "", "stderr": "",
            }],
            "output_limit": {
                "output": "result", "limit_bytes": 768, "observed_bytes": 769,
            },
        }
        limit = payload["output_limit"]
        if mutation == "wrong-limit":
            limit["limit_bytes"] = 700
        elif mutation == "not-exceeded":
            limit["observed_bytes"] = 768
        elif mutation == "bool-count":
            limit["observed_bytes"] = True
        elif mutation == "unknown-output":
            limit["output"] = "unknown"
        elif mutation == "extra-field":
            limit["extra"] = 0
        elif mutation == "no-limit":
            payload["output_limit"] = None
        elif mutation == "wrong-ledger":
            payload["commands"][0]["id"] = "unknown"
        elif mutation == "partial-output":
            payload["outputs"] = {"result": "ZnJlc2g="}
        return IsolationResult(
            IsolationStatus.COMPLETED, spec.digest, "backend",
            stdout=json.dumps(payload).encode(), exit_code=0)

    monkeypatch.setattr(tools, "run", completed)
    execution = tools.run_workspace(
        workspace, (tool_protocol.ToolGroup((command,)),), role="output-budget",
        outputs=("result",),
        limits=replace(tools.default_limits(), stdout_bytes=1024))
    assert output.read_bytes() == b"prior"
    assert not execution.outputs and not execution.trees
    if mutation is not None:
        assert execution.status is IsolationStatus.REFUSED
        assert execution.refusal.code == "rtl-tool-protocol-invalid"
        return
    assert execution.status is IsolationStatus.PROTOCOL_LIMIT
    assert execution.output_limit == tool_protocol.ToolOutputLimit("result", 768, 769)
    image = execution.as_dict()
    assert tool_protocol.ToolExecution.validate_image(image) == image
    for forged in (
        dict(image, status=IsolationStatus.COMPLETED.value),
        dict(image, outputs={"result": "0" * 64}),
        dict(image, output_limit=dict(image["output_limit"], observed_bytes=False)),
    ):
        with pytest.raises(ValueError):
            tool_protocol.ToolExecution.validate_image(forged)
    with pytest.raises(ValueError):
        replace(execution, outputs=MappingProxyType({"result": b"partial"}))
    with pytest.raises(ValueError):
        replace(execution, status=IsolationStatus.COMPLETED)
