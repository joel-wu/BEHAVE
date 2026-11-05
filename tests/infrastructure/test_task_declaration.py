import pytest

from test_judge import real_task

from behave.core.lifecycle import CleanupState
from behave.core.records import parse_json
from behave.core.storage import DirectoryStore
from behave.sim import api
from behave.task_execution.task_declaration import check_task_declaration


HEADER = """module M(input clk, rst, in_valid, input [7:0] in_a,
output in_ready, out_valid, input out_ready, output [7:0] out_y);
"""


def spec(header=HEADER):
    return ("# Identity\n\n### RTL interface\n```verilog\n" + header + "```\n").encode()


@pytest.mark.parametrize("origin", ["candidate", "infrastructure", "verifier"])
def test_task_declaration_retains_failure_but_does_not_score_solver(tmp_path, monkeypatch, origin):
    task, _, _ = real_task(tmp_path / "task", rtl=None)
    reason = api.Refusal("fixture-interface", "Fixture refusal.")
    seen = []

    def realize(source, name, *, contract):
        seen.append(source)
        failure = getattr(api.RunFailure, origin)("fixture", reason)
        raise api.FailureRaised(failure)

    monkeypatch.setattr(api, "realization_of_rtl_source", realize)
    objects = DirectoryStore(tmp_path / "objects")
    result = check_task_declaration(spec(), task.contract.data, objects=objects)
    assert result.score is None
    assert result.origin == ("reference" if origin == "candidate" else origin)
    assert result.code == "fixture-interface"
    assert result.cleanup is CleanupState.COMPLETE
    assert seen == [HEADER + "\nendmodule\n"]
    diagnostic = parse_json(objects.read(result.diagnostics[0].content))
    assert diagnostic["reason"]["kind"] == "fixture-interface"
    assert all(objects.read(ref) is not None for ref in result.closure)


def test_missing_declaration_is_actionable_task_feedback(tmp_path, monkeypatch):
    task, _, _ = real_task(tmp_path / "task", rtl=None)
    monkeypatch.setattr(api, "realization_of_rtl_source",
                        lambda *a, **kw: pytest.fail("No declaration to elaborate"))
    result = check_task_declaration(b"No RTL interface here.", task.contract.data,
                                    objects=DirectoryStore(tmp_path / "objects"))
    assert result.score is None and result.origin == "reference"
    assert result.code == "task-interface-invalid"


def test_cancelled_queue_needs_no_release_or_rtl_execution(tmp_path, monkeypatch):
    from behave.sim.cpu_admission import reserve_cpus

    task, _, _ = real_task(tmp_path / "task", rtl=None)

    def realize(*args, **kwargs):
        with reserve_cpus(1):
            pytest.fail("Cancelled work must not execute")

    monkeypatch.setattr(api, "realization_of_rtl_source", realize)
    result = check_task_declaration(spec(), task.contract.data,
        objects=DirectoryStore(tmp_path / "objects"), cancelled=lambda: True)
    assert result.score is None and result.origin == "infrastructure"
    assert result.code == "cpu-wait-cancelled"
    assert result.cleanup is CleanupState.COMPLETE


def test_unconfirmed_tool_cleanup_is_not_released_as_complete(tmp_path, monkeypatch):
    from behave.sim.cpu_admission import cpu_cleanup_unconfirmed, reserve_cpus

    task, _, _ = real_task(tmp_path / "task", rtl=None)

    def realize(*args, **kwargs):
        with reserve_cpus(1):
            cpu_cleanup_unconfirmed()

    monkeypatch.setattr(api, "realization_of_rtl_source", realize)
    result = check_task_declaration(spec(), task.contract.data,
        objects=DirectoryStore(tmp_path / "objects"))
    assert result.score is None and result.origin == "infrastructure"
    assert result.code == "cpu-cleanup-unconfirmed"
    assert result.cleanup is CleanupState.UNKNOWN


def test_declaration_reuses_enclosing_worker_cpu_scope(tmp_path, monkeypatch):
    from behave.sim.cpu_admission import cpu_capacity

    task, _, _ = real_task(tmp_path / "task", rtl=None)
    capacities = []
    monkeypatch.setattr(api, "realization_of_rtl_source",
                        lambda *a, **kw: capacities.append(cpu_capacity()))
    monkeypatch.setattr(api, "admit", lambda *a, **kw: None)
    with api.CpuPool(capacity=2) as pool, api.cpu_scope(pool.endpoint):
        result = check_task_declaration(spec(), task.contract.data,
                                       objects=DirectoryStore(tmp_path / "objects"))
        assert cpu_capacity() == 2
    assert result.score == 1 and capacities == [2]


@pytest.mark.eda("verilator")
@pytest.mark.isolation
@pytest.mark.parametrize("fault", [None, "control", "signedness"])
def test_real_declared_interface_uses_common_admission_not_functional_rtl(tmp_path, fault):
    task, _, _ = real_task(tmp_path / "task", rtl=None)
    header = HEADER
    if fault == "control":
        header = header.replace("in_valid", "input_valid")
    elif fault == "signedness":
        header = header.replace("input [7:0]", "input signed [7:0]")
    objects = DirectoryStore(tmp_path / "objects")
    with api.CpuPool(capacity=2) as pool:
        result = check_task_declaration(spec(header), task.contract.data,
                                        objects=objects, cpu_pool=pool.endpoint.as_dict())
    assert result.cleanup is CleanupState.COMPLETE
    if fault is None:
        assert result.score == 1 and result.code == "task-interface-consistent"
    else:
        assert result.score is None and result.origin == "reference"
        assert result.code == ("realization-port-outside-contract" if fault == "control"
                               else "admission-signedness")
    evidence = parse_json(objects.read(result.evidence[-1]))
    assert evidence["functional_correctness_checked"] is False
    assert evidence["resources"]["reservations"]
    assert all(row["released"] for row in evidence["resources"]["reservations"])
