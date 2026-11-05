"""Driver ownership is source evidence, not a heuristic over tool stderr."""

import json
import subprocess

import pytest

from behave.sim import api
from behave.sim.rtl import catalogue, elaborate, shared_frontend, tool_isolation, tool_protocol
from behave.sim.rtl.frontend_error import SourceRejected
from behave.sim.rtl.source_rules import (
    REJECTION_EXIT,
    REJECTION_FILE,
    TYPED_DIAGNOSTICS_FILE,
    SourceRule,
    from_compiler,
    read_rejection,
)
from tests.sim.test_rtl_tool_isolation import _command_row, _completed, _needs_namespace


def _diagnostic(rule=SourceRule.EXCLUSIVE_PROCEDURAL_WRITER, **changes):
    return {"severity": "error", "code": rule.value,
            "symbolPath": "M.value", "location": "design.sv:3:8",
            "message": "not used for classification", **changes}


def _payload(rows):
    return json.dumps(rows).encode()


@pytest.mark.parametrize("rule", list(SourceRule))
def test_driver_rule_evidence_roundtrips_without_using_prose(rule):
    evidence = [_diagnostic(rule), {"severity": "note", "message": "other write"},
                {"severity": "warning", "optionName": "unused-variable"}]
    rejected = from_compiler(_payload(evidence))
    restored = read_rejection(_payload(rejected.as_dict()))
    assert restored.as_dict() == rejected.as_dict()
    assert restored.violations[0].rule is rule
    assert restored.violations[0].symbol == "M.value"


@pytest.mark.parametrize("rows", [
    [], [_diagnostic(severity="warning")],
    [_diagnostic(code="unknown-rule", message="multiple-always-assigns")],
    [{"severity": "error", "message": "multiple-cont-assigns M.value design.sv:3:8"}],
    [_diagnostic(), _diagnostic(code="other-error")],
    [_diagnostic(), {"severity": "fatal", "message": "analysis stopped"}],
])
def test_warning_text_and_incomplete_or_other_diagnostics_are_not_driver_proofs(rows):
    assert from_compiler(_payload(rows)) is None


@pytest.mark.parametrize("rows", [
    {}, [0], [_diagnostic(symbolPath=3)], [_diagnostic(location="")],
])
def test_malformed_driver_evidence_is_not_a_source_rejection(rows):
    with pytest.raises(ValueError):
        from_compiler(_payload(rows))


@pytest.mark.parametrize("returncode,expected", [
    (5, REJECTION_EXIT), (1, 1), (-11, 1), (126, 1),
])
def test_only_completed_slang_analysis_publishes_a_driver_rejection(
        tmp_path, monkeypatch, returncode, expected):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shared_frontend.sys, "argv", ["frontend", "M", "--declare-source"])
    (tmp_path / TYPED_DIAGNOSTICS_FILE).write_bytes(_payload([_diagnostic()]))
    monkeypatch.setattr(shared_frontend.subprocess, "run",
                        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode))
    monkeypatch.setattr(shared_frontend, "declare_catalogue",
                        lambda where, top, run: run(where, "/tools/slang", "design.sv"))
    with pytest.raises(SystemExit) as caught:
        shared_frontend.main()
    assert caught.value.code == expected
    assert (tmp_path / REJECTION_FILE).exists() == (expected == REJECTION_EXIT)


@pytest.mark.parametrize("source_only", [False, True])
def test_catalogue_retains_typed_rejection_without_an_alternate_compiler(
        tmp_path, monkeypatch, source_only):
    source = tmp_path / "candidate.sv"
    source.write_text("module M; endmodule")
    rejected = from_compiler(_payload([_diagnostic()]))
    role = "rtl-source-catalogue" if source_only else "yosys-rtlil"
    execution = _completed(commands=(_command_row(role, returncode=REJECTION_EXIT),),
                           outputs={REJECTION_FILE: _payload(rejected.as_dict())})
    monkeypatch.setattr(tool_isolation, "run_workspace", lambda *args, **kwargs: execution)
    monkeypatch.setattr(elaborate, "schema_of_module",
                        lambda *args, **kwargs: pytest.fail("driver proof needs no second compiler"))
    with pytest.raises(SourceRejected) as caught:
        catalogue._read_frontend(source, "M", 30, source_only=source_only)
    assert caught.value.reason.kind == "rtl-source-rule-violation"
    assert caught.value.reason.as_dict()["detail"]["source_rules"] == rejected.as_dict()


@pytest.mark.parametrize("outputs", [{}, {REJECTION_FILE: b"{}"}])
def test_rejection_exit_without_complete_evidence_is_a_tool_failure(tmp_path, monkeypatch, outputs):
    source = tmp_path / "candidate.sv"
    source.write_text("module M; endmodule")
    execution = _completed(commands=(_command_row("rtl-source-catalogue", returncode=REJECTION_EXIT),),
                           outputs=outputs)
    monkeypatch.setattr(tool_isolation, "run_workspace", lambda *args, **kwargs: execution)
    with pytest.raises(tool_protocol.ToolIsolationError) as caught:
        catalogue.source_catalogue_with_execution_of(source, "M")
    assert caught.value.code == "rtl-source-evidence-invalid"


@pytest.mark.isolation
@pytest.mark.eda("slang")
@pytest.mark.parametrize("body,rule", [
    (("integer i; always_comb for(i=0;i<4;i=i+1) y[i]=a[i]; "
      "always_ff @(posedge clk) for(i=0;i<4;i=i+1) q[i]<=a[i];"),
     SourceRule.EXCLUSIVE_PROCEDURAL_WRITER),
    (("function [3:0] f(input [3:0] a); reg [3:0] x; "
      "begin x=a+1; f=x; end endfunction "
      "always_comb y=f(a); always_ff @(posedge clk) q<=f(a);"),
     SourceRule.EXCLUSIVE_PROCEDURAL_WRITER),
    ("assign y=a; assign y=a; assign q=a;",
     SourceRule.SINGLE_CONTINUOUS_VARIABLE_DRIVER),
    ("assign y=a; always @(posedge clk) y<=a; assign q=a;",
     SourceRule.NO_MIXED_VARIABLE_DRIVERS),
])
def test_real_original_source_driver_proof_reaches_catalogue_boundary(tmp_path, body, rule):
    _needs_namespace()
    source = "module M(input clk,input [3:0] a,output logic [3:0] y,q);" + body + "endmodule"
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    with pytest.raises(SourceRejected) as caught:
        catalogue.source_catalogue_with_execution_of(path, "M")
    detail = caught.value.reason.as_dict()["detail"]
    assert {row["rule"] for row in detail["source_rules"]["violations"]} == {rule.value}
    assert detail["execution"]["commands"][0]["returncode"] == REJECTION_EXIT
    assert path.read_text() == source


@pytest.mark.isolation
@pytest.mark.eda("slang")
def test_many_driver_violations_do_not_hit_a_diagnostic_count_cap(tmp_path):
    _needs_namespace()
    source = "module M(input [31:0] a,output logic y[32]);\n" + "\n".join(
        f"assign y[{i}]=a[{i}]; assign y[{i}]=a[{i}];" for i in range(32)) + "\nendmodule"
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    with pytest.raises(SourceRejected) as caught:
        catalogue.source_catalogue_with_execution_of(path, "M")
    violations = caught.value.reason.as_dict()["detail"]["source_rules"]["violations"]
    assert len(violations) == 32
    assert {row["rule"] for row in violations} == {SourceRule.SINGLE_CONTINUOUS_VARIABLE_DRIVER.value}
    assert path.read_text() == source


@pytest.mark.isolation
@pytest.mark.eda("slang")
def test_resolved_net_drivers_are_not_variable_driver_violations(tmp_path):
    _needs_namespace()
    source = "module M(input a,b,output wire y); assign y=a; assign y=b; endmodule"
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    catalogue.source_catalogue_with_execution_of(path, "M")
    assert path.read_text() == source


def test_candidate_preparation_owns_the_scoring_consequence(tmp_path, monkeypatch):
    from behave.sim.rtl.artifact import RTLArtifact
    from tests.sim.test_baseline_public_sequence import _contract
    from tests.sim.test_rtl_source_public import SOURCE

    path = tmp_path / "candidate.sv"
    path.write_text(SOURCE)
    artifact = RTLArtifact(path, "M", tmp_path / "build")
    contract = _contract()
    ports = [api.Port(name, width, driven_by=owner) for name, width, owner in (
        ("clk", 1, "environment"), ("rst", 1, "environment"),
        ("a", 8, "environment"), ("in_valid", 1, "environment"),
        ("in_ready", 1, "artifact"), ("out_valid", 1, "artifact"),
        ("out_ready", 1, "environment"), ("y", 8, "artifact"))]
    task = api.admit(contract, api.Realization("rtl", ports, identity=artifact.identity))
    rejected = from_compiler(_payload([_diagnostic()]))

    def fail(*args, **kwargs):
        raise SourceRejected(rejected.reason(_completed()))

    monkeypatch.setattr(RTLArtifact, "frontend_catalogue", fail)
    with pytest.raises(api.FailureRaised) as caught:
        api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.determines_score
    assert caught.value.failure.reason.kind == "rtl-source-rule-violation"


@pytest.mark.isolation
@pytest.mark.eda("slang")
@pytest.mark.parametrize("body,rule", [
    ("assign y=a[1:2];", SourceRule.REVERSED_RANGE),
    ("logic mem[2]; initial mem=0;", SourceRule.ASSIGNMENT_TYPE),
    ("always @(posedge clk) for(int i=0;i<2;i++) begin int j=i; y=j; end",
     SourceRule.AUTOMATIC_FROM_STATIC_INITIALIZER),
    ('import "DPI-C" function bit [31:0] external_value(input int a);',
     SourceRule.DPI_RETURN_TYPE),
    ("assign y=a[3:0][1:0];", SourceRule.SELECT_AFTER_RANGE),
])
def test_typed_language_rejection_has_a_compiler_location(tmp_path, body, rule):
    _needs_namespace()
    source = "module M(input clk,input [3:0] a,output logic [1:0] y);\n" + body + "\nendmodule"
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    with pytest.raises(SourceRejected) as caught:
        catalogue.source_catalogue_with_execution_of(path, "M")
    violations = caught.value.reason.as_dict()["detail"]["source_rules"]["violations"]
    assert rule.value in {row["rule"] for row in violations}
    assert all(row["location"].startswith("design.sv:") for row in violations)
    assert path.read_text() == source


@pytest.mark.isolation
@pytest.mark.eda("slang")
@pytest.mark.parametrize("declarations,body,rules", [
    ("output wire y", "always_comb y=a[0];", {SourceRule.PROCEDURAL_NET_ASSIGNMENT}),
    ("output wire [1:0] y", "always @(*) y[0]=a[0];", {SourceRule.PROCEDURAL_NET_ASSIGNMENT}),
    ("output wire y", "assign y=a[i];", {SourceRule.UNDECLARED_IDENTIFIER}),
    ("output wire y", "wire zero = &~a; assign y=zero;",
     {SourceRule.EXPECTED_EXPRESSION, SourceRule.EXPECTED_DECLARATOR}),
    ("output wire y", "assign y=(a < 's0);", {SourceRule.EXPECTED_SIGNED_INTEGER_BASE}),
])
def test_source_rules_distinguish_nets_names_and_expression_grammar(
        tmp_path, declarations, body, rules):
    _needs_namespace()
    source = f"module M(input [3:0] a, {declarations});\n{body}\nendmodule"
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    with pytest.raises(SourceRejected) as caught:
        catalogue.source_catalogue_with_execution_of(path, "M")
    violations = caught.value.reason.detail["source_rules"]["violations"]
    assert {row["rule"] for row in violations} == {rule.value for rule in rules}
    assert path.read_text() == source


@pytest.mark.isolation
@pytest.mark.eda("slang")
@pytest.mark.parametrize("declarations,body", [
    ("output logic y", "always_comb y=a[0];"),
    ("output wire y", "assign y=a[0];"),
    ("output wire y", "assign implicit_net=a[0]; assign y=implicit_net;"),
    ("output wire y", "wire zero = &(~a); assign y=zero;"),
    ("output wire y", "assign y=a[i]; localparam int i=0;"),
    ("output wire y", "assign y=(a < 'sd0);"),
    ("output wire y", "assign y=(a < '0);"),
])
def test_neighboring_legal_source_is_not_rejected_by_the_rule_policy(tmp_path, declarations, body):
    _needs_namespace()
    source = f"module M(input [3:0] a, {declarations});\n{body}\nendmodule"
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    catalogue.source_catalogue_with_execution_of(path, "M")
    assert path.read_text() == source
