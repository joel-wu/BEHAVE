"""Original source rejection is not an observer/compiler infrastructure fault."""

import base64
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

import pytest
from test_data_checks import comparison
from test_judge import RTL

from behave.data.checks import check_sources
from behave.isolation import IsolationStatus
from behave.sim import api
from behave.sim.rtl import source_check, tool_isolation, tool_protocol
from behave.task_execution.comparison import ComparisonRequest
from behave.task_execution.simulator_judge import _SimulatorService

# Unmodified candidate 31 from the 959340de2407 real 8-by-8 run.
REGISTERED_SOURCE = b"""module M (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    input  logic [7:0]  in_a,
    input  logic        out_ready,
    output logic        in_ready,
    output logic        out_valid,
    output logic [7:0]  out_y
);

    logic [7:0] y_comb;

    assign y_comb = in_a ^ 8'd71;

    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_y     <= 8'd0;
        end else begin
            if (out_valid && out_ready) begin
                out_valid <= in_valid;
                out_y     <= y_comb;
            end else if (!out_valid && in_valid) begin
                out_valid <= 1'b1;
                out_y     <= y_comb;
            end
        end
    end

    assign in_ready  = !out_valid || out_ready;
    assign out_valid = out_valid;

endmodule
"""


@pytest.mark.parametrize("status,limited,output,origin", [
    (1, False, False, api.FailureOrigin.CANDIDATE),
    (-11, False, False, api.FailureOrigin.INFRASTRUCTURE),
    (126, False, False, api.FailureOrigin.INFRASTRUCTURE),
    (2, False, False, api.FailureOrigin.INFRASTRUCTURE),
    (0, False, False, api.FailureOrigin.VERIFIER),
    (1, True, False, api.FailureOrigin.RESOURCE_POLICY),
    (0, False, True, None),
])
def test_source_build_check_uses_stage_status_not_message(monkeypatch, status, limited, output, origin):
    row = MappingProxyType({
        "id": "verilator-source-check", "returncode": status,
        "timed_out": False, "output_limited": limited, "stdout": "",
        "stderr": base64.b64encode(b"candidate syntax error").decode("ascii"),
    })
    execution = tool_protocol.ToolExecution(
        "rtl-source-check", IsolationStatus.COMPLETED, "1" * 64,
        "test-backend", "2" * 64, "3" * 64, (row,),
        MappingProxyType({"build/Vtop.mk": b"build"} if output else {}),
    )
    monkeypatch.setattr(tool_isolation, "run_workspace", lambda *a, **k: execution)
    failure, receipt = source_check.source_compile_failure("module M; endmodule", "M", timeout_s=10)
    assert receipt is execution
    if origin is None:
        assert failure is None
    else:
        assert failure.origin is origin
        assert failure.determines_score is (origin is api.FailureOrigin.CANDIDATE)


@pytest.mark.isolation
@pytest.mark.eda
@pytest.mark.parametrize("invalid", [False, True])
def test_real_original_source_codegen_without_observer(invalid):
    source = RTL.replace(b"assign out_valid = in_valid && !rst;", b"assign out_valid = out_valid;") if invalid else RTL
    failure, execution = source_check.source_compile_failure(source.decode(), "M", timeout_s=60)
    assert execution.role == "rtl-source-check"
    if invalid:
        assert failure is not None and failure.origin is api.FailureOrigin.CANDIDATE
        assert failure.reason.kind == "rtl-source-build-rejected"
    else:
        assert failure is None


@pytest.mark.isolation
@pytest.mark.eda
def test_original_rtl_build_rejection_scores_zero_and_cold_replays(tmp_path):
    inputs, journal = comparison(tmp_path, component="rtl", real=True, rtl=REGISTERED_SOURCE)
    result = check_sources(inputs, journal=journal)
    assert result.result.score == 0, result.as_dict()
    # Rejected source now stops during preparation: its first retained blob is
    # source, not a completed evaluation. Assert the public typed failure.
    failure = api.RunFailure.from_dict(result.result.as_dict()["detail"]["failure"])
    assert failure.origin is api.FailureOrigin.CANDIDATE
    assert failure.disposition is api.FailureDisposition.SCORE_ZERO
    assert failure.phase == "candidate-frontend"
    assert failure.reason.kind == "rtl-source-rule-violation"
    violations = failure.reason.as_dict()["detail"]["source_rules"]["violations"]
    assert {row["rule"] for row in violations} == {"MixedVarAssigns"}
    assert result.result.evidence
    assert check_sources(inputs, journal=journal) == result


@pytest.mark.isolation
@pytest.mark.eda
def test_observer_only_build_defect_is_still_no_result(tmp_path, monkeypatch):
    from behave.sim.rtl import observation_binding

    # This separate negative control removes the source defect, then introduces
    # a compiler error into the trusted observer. It must never become zero.
    source = REGISTERED_SOURCE.replace(b"    assign out_valid = out_valid;\n", b"")
    inputs, journal = comparison(tmp_path, component="rtl", real=True, rtl=source)
    original = observation_binding.prepare_scheduled_observer

    def broken_observer(*args, **kwargs):
        prepared = original(*args, **kwargs)
        return replace(prepared, collector="#error test observer defect\n" + prepared.collector)

    monkeypatch.setattr(observation_binding, "prepare_scheduled_observer", broken_observer)
    checked = []
    compile_source = source_check.source_compile_failure

    def check(*args, **kwargs):
        answer = compile_source(*args, **kwargs)
        checked.append(answer)
        return answer

    monkeypatch.setattr(source_check, "source_compile_failure", check)
    with TemporaryDirectory(prefix="behave-observer-defect-", dir="/tmp") as root:
        result = _SimulatorService().evaluate(ComparisonRequest(inputs, Path(root)), journal.objects)
    assert result.score is None and result.origin == "verifier", result.as_dict()
    assert result.code == "rtl-artifact-build-failed", result.as_dict()
    assert checked and all(failure is None for failure, _ in checked)
