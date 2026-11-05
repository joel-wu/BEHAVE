"""A failed query launch retains the existing tool evidence, not just its label."""

import hashlib
from types import SimpleNamespace

import pytest

from behave.isolation import IsolationRefusal, IsolationStatus
from behave.sim.engines.rtl.generation import _IsolatedRTLGeneration
from behave.sim.rtl.exact_job_context import RTLExactJobContext
from behave.sim.rtl.elaboration import source_documents
from behave.sim.rtl.tool_protocol import ToolExecution, ToolIsolationError


def failed_execution():
    return ToolExecution(
        role="rtl-source-query", status=IsolationStatus.REFUSED,
        policy_digest="1" * 64, backend_identity="test-backend",
        workspace_digest="2" * 64, job_digest="3" * 64,
        refusal=IsolationRefusal(
            "isolation-launch-failed", "the namespace could not be established",
            {"error": "BlockingIOError: [Errno 11] Resource temporarily unavailable"}),
    )


def test_failed_source_compiler_is_retained_in_artifact_lineage(tmp_path, monkeypatch):
    source = "module M(input a, output y); assign y=a; endmodule"
    path = tmp_path / "design.sv"
    path.write_text(source)
    executions = []
    artifact = SimpleNamespace(rtl_path=path, top_module="M", timeout_s=10,
                               _record_tool_execution=executions.append)
    execution = failed_execution()
    monkeypatch.setattr("behave.sim.rtl.elaboration.scratch_root", lambda: tmp_path)
    monkeypatch.setattr("behave.sim.rtl.tool_isolation.run_workspace",
                        lambda *args, **kwargs: execution)
    identity = hashlib.sha256(("M\0" + source).encode()).hexdigest()
    with pytest.raises(ToolIsolationError) as caught:
        source_documents(artifact, expected_identity=identity)
    assert caught.value.execution is execution
    assert executions == [execution]


@pytest.mark.parametrize("owner", [RTLExactJobContext.unavailable,
                                  _IsolatedRTLGeneration._unavailable])
def test_query_refusal_retains_structured_tool_cause(owner):
    execution = failed_execution()
    error = ToolIsolationError(execution.refusal.code, execution.refusal.why, execution)
    refusal = owner("source", error)
    assert refusal.kind.endswith("-isolated-source-unavailable")
    assert refusal.detail["failure"] == "ToolIsolationError"
    assert refusal.as_dict()["detail"]["tool_executions"] == [execution.as_dict()]


@pytest.mark.parametrize("owner", [RTLExactJobContext.unavailable,
                                  _IsolatedRTLGeneration._unavailable])
def test_non_tool_query_error_keeps_its_existing_diagnostic(owner):
    refusal = owner("source", ValueError("invalid source reply"))
    assert refusal.as_dict()["detail"] == {
        "failure": "ValueError", "detail": "invalid source reply"}
