"""Pre-catalogue failures retain ownership without inventing an execution."""

import importlib.util
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.rtl import catalogue, elaborate, tool_isolation
from behave.sim.rtl.frontend_error import SourceRejected
from tests.sim.test_api_path import contract
from tests.sim.test_rtl_tool_isolation import _command_row, _completed


@pytest.mark.parametrize("model", ["*", "123M", "M()", "class", "M.Entry"])
@pytest.mark.parametrize("route", ["single-file", "multi-file", "realization"])
def test_invalid_behavior_entry_is_a_submission_failure(monkeypatch, model, route):
    source = "class M:\n def process(self, a): return {'y': a}\n"
    monkeypatch.setattr("behave.sim.worker.run_job_strict",
                        lambda *a, **kw: pytest.fail("invalid entry must not start a worker"))
    with pytest.raises(api.FailureRaised) as caught:
        if route == "realization":
            api.realization_of_behavior_source(source, contract(), model=model)
        else:
            files = {"model.py": source.encode()}
            if route == "multi-file":
                files["helper.py"] = b"K = 7\n"
            api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, "model.py", model))
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.CANDIDATE
    assert failure.determines_score
    assert failure.reason.kind == "behavior-entry-invalid"
    assert failure.reason.as_dict()["detail"]["model"] == model


@pytest.mark.parametrize("model", ["match", "case", "\u6a21\u578b"])
def test_valid_python_class_identifiers_remain_supported(model):
    source = f"class {model}:\n def process(self, a): return {{'y': a}}\n"
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {"model.py": source.encode()}, "model.py", model))
    assert prepared.source == source and prepared.model == model


@pytest.mark.parametrize("entry", [7, False, []])
def test_nonstring_entry_is_api_misuse_not_candidate_evidence(entry):
    with pytest.raises(TypeError):
        api.realization_of_behavior_source("class M: pass", contract(), model=entry)
    with pytest.raises(TypeError):
        api.realization_of_rtl_source("module m; endmodule", top=entry)


@pytest.mark.parametrize("top", ["*", "m()", "0m", "../m"])
def test_invalid_rtl_entry_is_not_a_frontend_crash(monkeypatch, top):
    monkeypatch.setattr(elaborate, "_verilator",
                        lambda: pytest.fail("entry validation precedes tool setup"))
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_rtl_source("module m; endmodule", top=top)
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.CANDIDATE
    assert failure.determines_score
    assert failure.reason.kind == "rtl-entry-invalid"
    assert failure.reason.as_dict()["detail"]["top"] == top


@pytest.mark.parametrize("top", [r"\escaped-name", r"\* ", "m$impl"])
def test_unsupported_legal_sv_top_names_are_not_invalid_submissions(monkeypatch, top):
    monkeypatch.setattr(elaborate, "_verilator",
                        lambda: pytest.fail("no incomplete name may reach the backend"))
    with pytest.raises(api.Refused) as caught:
        api.realization_of_rtl_source("module m; endmodule", top=top)
    assert caught.value.refusal.kind == "rtl-top-identifier-unsupported"


@pytest.mark.parametrize("model", ["*", "class"])
def test_invalid_reference_entry_cannot_score_the_candidate_zero(monkeypatch, model):
    monkeypatch.setattr("behave.sim.behavior.isolated._trusted_source_protocol_facts",
                        lambda *a, **kw: pytest.fail("invalid entry must not start a worker"))
    with pytest.raises(api.FailureRaised) as caught:
        api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
            {"behavior.py": b"class M: pass\n"}, config={"class": model})
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.REFERENCE
    assert failure.disposition is api.FailureDisposition.NO_RESULT
    assert failure.reason.kind == "behavior-entry-invalid"


@pytest.mark.parametrize("status,limited,origin", [
    (1, False, api.FailureOrigin.CANDIDATE),
    (-11, False, api.FailureOrigin.INFRASTRUCTURE),
    (126, False, api.FailureOrigin.INFRASTRUCTURE),
    (0, False, api.FailureOrigin.INFRASTRUCTURE),  # success, missing result
    (1, True, api.FailureOrigin.RESOURCE_POLICY),
])
def test_rtl_frontend_classifies_stage_evidence_not_diagnostics(
        tmp_path, monkeypatch, status, limited, origin):
    monkeypatch.setattr(elaborate, "_verilator", lambda: tmp_path / "verilator")
    row = _command_row("verilator-elaborate", returncode=status,
                       stderr=b"syntax error: the candidate is wrong")
    row["output_limited"] = limited
    execution = _completed(commands=(row,))
    monkeypatch.setattr(tool_isolation, "run_workspace", lambda *a, **k: execution)
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_rtl_source("module m; endmodule", top="m")
    failure = caught.value.failure
    assert failure.origin is origin
    assert failure.determines_score is (origin is api.FailureOrigin.CANDIDATE)
    assert api.RunFailure.from_dict(failure.as_dict()).as_dict() == failure.as_dict()


def test_missing_frontend_is_not_a_bad_candidate(monkeypatch):
    monkeypatch.setattr(elaborate, "_verilator", lambda: None)
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_rtl_source("module m; endmodule", top="m")
    assert caught.value.failure.origin is api.FailureOrigin.INFRASTRUCTURE
    assert not caught.value.failure.determines_score


def test_a_typed_frontend_failure_keeps_its_origin(monkeypatch):
    failure = api.RunFailure.candidate(
        "candidate-frontend", api.Refusal("rtl-source-rejected", "source rejected"))

    def rejected(*args, **kwargs):
        raise api.FailureRaised(failure)

    monkeypatch.setattr("behave.sim.contract_of.realization_of_source", rejected)
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_rtl_source("module m; endmodule", top="m")
    assert caught.value.failure is failure


@pytest.mark.parametrize("closed", [False, True])
def test_public_task_interface_only_closes_the_declaration(monkeypatch, closed):
    source = "module m(input a, output y);\n" + ("endmodule\n" if closed else "")
    spec = "### RTL interface\n```sv\n" + source + "```\n"
    task_contract = contract()
    seen = []
    monkeypatch.setattr(api, "realization_of_rtl_source",
        lambda text, name, **kw: seen.append((text, name, kw)) or "realization")
    assert api.realization_of_task_interface(spec, contract=task_contract) == "realization"
    assert seen == [(source if closed else source + "\nendmodule\n",
                     "the task's RTL interface declaration", {"contract": task_contract})]


@pytest.mark.parametrize("origin", list(api.FailureOrigin))
def test_task_interface_reattributes_only_candidate_frontend_failures(monkeypatch, origin):
    reason = api.Refusal("fixture-interface", "Fixture refusal.")
    # Resource policy has its own constructor; preserve the actual record.
    failure = (api.RunFailure.resource("fixture", reason, policy="fixture",
                   disposition=api.FailureDisposition.NO_RESULT) if origin is api.FailureOrigin.RESOURCE_POLICY
               else getattr(api.RunFailure, origin.value)("fixture", reason))

    def reject(*args, **kwargs):
        raise api.FailureRaised(failure)

    monkeypatch.setattr(api, "realization_of_rtl_source", reject)
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_task_interface("### RTL interface\n```sv\nmodule m;\n```\n")
    actual = caught.value.failure
    if origin is api.FailureOrigin.CANDIDATE:
        assert actual.origin is api.FailureOrigin.REFERENCE
        assert actual.disposition is api.FailureDisposition.NO_RESULT
        assert actual.reason is reason
    else:
        assert actual is failure


def test_missing_task_interface_is_not_a_candidate_fault(monkeypatch):
    monkeypatch.setattr(api, "realization_of_rtl_source",
                        lambda *a, **kw: pytest.fail("No declaration to elaborate"))
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_task_interface("No interface")
    assert caught.value.failure.origin is api.FailureOrigin.REFERENCE
    assert caught.value.failure.reason.kind == "task-interface-invalid"


def test_structural_rejection_of_simulatable_rtl_is_not_zero(tmp_path, monkeypatch):
    source = tmp_path / "m.sv"
    source.write_text("module m; endmodule")
    execution = _completed(commands=(_command_row("yosys-rtlil", returncode=1),))
    monkeypatch.setattr(tool_isolation, "run_workspace", lambda *a, **k: execution)
    seen = []
    monkeypatch.setattr(elaborate, "schema_of_module",
                        lambda source, **kw: seen.append(source))
    with pytest.raises(api.Refused) as caught:
        catalogue.read_sites(source, "m")
    assert seen == [source.read_text()]
    assert caught.value.refusal.kind == "rtl-structural-frontend-unsupported"


def test_wrapper_launch_errors_cannot_masquerade_as_source_rejection(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "tool_wrapper_test", Path(tool_isolation.__file__).with_name("_tool_wrapper.py"))
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    monkeypatch.setattr(wrapper.sys, "argv", ["/tools/verilator"])
    monkeypatch.setattr(wrapper, "glob", lambda pattern: [])
    assert wrapper.main() == 126


@pytest.mark.isolation
def test_real_python_parser_returns_candidate_fault_not_worker_failure():
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_behavior_source("class M(\n", contract())
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.reason.kind == "behavior-source-syntax-error"
    assert caught.value.failure.determines_score


@pytest.mark.eda
@pytest.mark.isolation
def test_real_rtl_parser_returns_candidate_fault_not_tool_failure():
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_rtl_source("module m(input wire a); assign = ; endmodule", top="m")
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.reason.kind == "rtl-source-rejected"
    assert caught.value.failure.determines_score


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("direction", ("input", "output"))
def test_elaborated_undeclared_port_is_a_candidate_contract_violation(direction):
    source = f"""module m(input clk, rst, in_valid, out_ready,
        input [7:0] in_a, output in_ready, out_valid, output [7:0] out_y,
        {direction} extra);
        assign in_ready=1'b1;
        assign out_valid=in_valid;
        assign out_y=in_a;
        {"assign extra=1'b0;" if direction == "output" else ""}
    endmodule"""
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_rtl_source(source, top="m", contract=contract())
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.CANDIDATE
    assert failure.determines_score
    assert failure.reason.kind == "realization-port-outside-contract"
    assert failure.reason.as_dict()["detail"]["ports"] == ["extra"]


def test_unbound_task_field_is_not_an_undeclared_candidate_port(monkeypatch):
    task = api.TaskContract(endpoints=[api.Endpoint(
        "in", api.Direction.IN, [api.Field("a", api.FiniteType(8))],
        api.ready_valid("in", "in_valid", "in_ready"), clock="clk")],
        clocks=[api.ClockDomain("clk", "clk", "rst")])

    def should_not_compile(*args, **kwargs):
        pytest.fail("an incomplete task cannot establish a candidate port mismatch")

    monkeypatch.setattr(elaborate, "schema_of_module", should_not_compile)
    with pytest.raises(api.Refused, match="admission-field-unbound"):
        api.realization_of_rtl_source("module m; endmodule", top="m", contract=task)


def test_rtl_failed_catalogue_still_checks_the_admitted_source(tmp_path, monkeypatch):
    from behave.sim.rtl.artifact import RTLArtifact

    source = tmp_path / "m.sv"
    source.write_text("module m; endmodule")
    artifact = RTLArtifact(source, "m", tmp_path / "work")
    original = artifact.identity

    def reject_changed(*args, **kwargs):
        source.write_text("module another; endmodule")
        raise SourceRejected(api.Refusal("rtl-source-rejected", "reject"))

    monkeypatch.setattr(catalogue, "source_catalogue_with_execution_of", reject_changed)
    with pytest.raises(api.Refused) as caught:
        artifact.frontend_catalogue(expected_identity=original)
    assert caught.value.refusal.kind == "rtl-catalogue-source-changed-during-elaboration"


@pytest.mark.parametrize("fault", ["crashed", "bad-shape", "bad-binding", "bad-failure", "service-bug"])
def test_behavior_realization_service_faults_never_become_candidate_faults(monkeypatch, fault):
    from behave.sim.behavior.realization import BEHAVIOR_REALIZATION_VERSION
    from behave.sim.worker import WorkerFailed

    task_contract = contract()

    def answer(*args, **kwargs):
        if fault == "crashed":
            raise WorkerFailed("crashed", "service process lost")
        if fault == "service-bug":
            raise RuntimeError("the service itself failed")
        if fault == "bad-shape":
            return []
        return {
            "version": BEHAVIOR_REALIZATION_VERSION,
            "contract": "wrong" if fault == "bad-binding" else task_contract.digest,
            "status": "source-failed", "failure": {},
        }

    monkeypatch.setattr("behave.sim.worker.run_job_strict", answer)
    with pytest.raises(api.FailureRaised) as caught:
        api.realization_of_behavior_source("class M: pass", task_contract)
    assert caught.value.failure.origin is (
        api.FailureOrigin.INFRASTRUCTURE if fault == "crashed"
        else api.FailureOrigin.VERIFIER)
    assert caught.value.failure.disposition is api.FailureDisposition.NO_RESULT


@pytest.mark.parametrize("fault", ["crashed", "bad-shape", "bad-catalogue", "service-bug"])
def test_direct_behavior_preparation_preserves_service_failure_origin(tmp_path, monkeypatch, fault):
    from behave.sim.behavior.isolated import Broken
    from tests.sim.test_behavior_public_exact_construction import SOURCE, _generation, _task

    source = tmp_path / "candidate.py"
    source.write_text(SOURCE)
    task = _task("test-front-end-service")

    def facts(*args, **kwargs):
        if fault == "crashed":
            raise Broken("source service lost")
        if fault == "service-bug":
            raise RuntimeError("source service bug")
        if fault == "bad-catalogue":
            return {"cls_name": "M", "domains": {}, "process_inputs": ["a"]}, (), None
        return []

    monkeypatch.setattr("behave.sim.behavior.isolated._trusted_source_protocol_facts", facts)
    with pytest.raises(api.FailureRaised) as caught:
        api.prepare_behavior(task, api.BehaviorConfig(source), _generation(task.contract))
    assert caught.value.failure.origin is (
        api.FailureOrigin.INFRASTRUCTURE if fault == "crashed"
        else api.FailureOrigin.VERIFIER)
    assert caught.value.failure.disposition is api.FailureDisposition.NO_RESULT
