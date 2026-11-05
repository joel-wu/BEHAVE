"""PPA policy/projection/recovery, then opt-in real EDA and Sim tests."""

from dataclasses import replace
from pathlib import Path

import pytest

from behave.agent.attempt import AttemptSpec
from behave.agent.model import ModelReply, ToolCall
from behave.core.artifacts import BlobRef, FileData, FileRef, LoadedSource, SourceArtifact
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.data.published import JudgeInputs, PublicTaskInputs
from behave.ppa.config import Experiment, TimingPolicy, Workload
from behave.ppa.eda import library_description, mapped_area, timing_result
from behave.ppa.finalize import compare_searches
from behave.ppa.performance import completion, stimulus
from behave.ppa.workflow import _union, frontier, run_search


def file(name, data):
    return FileData(FileRef(name, BlobRef.of(data)), data)


def test_workload_uses_runtime_completion_without_a_cycle_ceiling():
    _, _, contract = fixture_task()
    request = stimulus(contract, Workload("long", ((1,),)))
    assert request.replay_policy is not None and not request.slots
    assert request.replay_policy.stall_every == 0


@pytest.mark.parametrize("answers", [None, 1, 2])
def test_workload_leaves_output_cardinality_to_the_public_contract(answers):
    from tests.sim.test_api_path import contract

    task = contract(answers=answers)
    before = task.as_dict()
    request = stimulus(task, Workload("stream", ((1,), (2,))))
    assert dict(request.queues) == {"in": ((1,), (2,))}
    assert task.as_dict() == before


def test_pilot_config_roundtrips_current_measurement_schema():
    import json

    config = Path(__file__).resolve().parents[2] / "configs/ppa/pilot.json"
    parsed = Experiment.from_dict(json.loads(config.read_text()))
    assert Experiment.from_dict(parsed.as_dict()) == parsed
    assert "answers_per_input" not in parsed.as_dict()["workloads"][0]


def test_old_workload_policy_is_not_silently_reinterpreted():
    row = experiment().as_dict()
    row["schema"] = "behave.ppa-experiment/7"
    with pytest.raises(ValueError):
        Experiment.from_dict(row)


def fixture_task():
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy
    from tests.sim.test_api_path import contract

    c = contract(answers=1, deadline=16, quiescent=True)
    ct = file("contract.json", canonical_json(c.as_dict()))
    behavior = file("behavior.py", b'class Identity:\n def reset(self): pass\n def process(self,in_a): return {"out_y":in_a}\n')
    source = LoadedSource(SourceArtifact((behavior.reference,), "behavior.py", "Identity"), (behavior,))
    revision, release = BlobRef.of(b"ppa-pilot"), BlobRef.of(b"pilot-release")
    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0,
                         baseline_cases=2, sequence_length=2)
    judge = JudgeInputs("identity", release, revision, ct, source, None,
        canonical_json(api.GenerationSpec(horizon=24, queue_bound=4, reset_hold_cycles=3, drain=4).as_dict()), policy.to_bytes())
    public = PublicTaskInputs("identity", release, revision, "spec.md", "contract.json", (
        file("spec.md", b"Implement an 8-bit identity stream. Ready/valid ports in_a/out_y; active-high rst and clk. "
             b"One output per accepted input, in order, within 16 cycles. Output remains stable when stalled."), ct))
    return public, judge, c


def experiment():
    return Experiment(TimingPolicy("clk", 20., .1, .2, .2, .1, .01),
        (Workload("stream", ((3,), (7,), (11,), (19,))),), (100., 200.), service_seconds=240)


RTL = b'''module M(input clk, rst, in_valid, out_ready, input [7:0] in_a,
output in_ready, out_valid, output [7:0] out_y);
assign in_ready = out_ready && !rst;
assign out_valid = in_valid && !rst;
assign out_y = in_a;
endmodule
'''

REGISTERED_RTL = b'''module M(input clk, rst, in_valid, out_ready, input [7:0] in_a,
output in_ready, out_valid, output [7:0] out_y);
reg full;
reg [7:0] data;
assign in_ready = (!full || out_ready) && !rst;
assign out_valid = full && !rst;
assign out_y = data;
always @(posedge clk) begin
  if (rst) begin full <= 0; data <= 0; end
  else if (in_ready) begin
    full <= in_valid;
    if (in_valid) data <= in_a;
  end
end
endmodule
'''


class Scripted:
    def __init__(self):
        self.index = 0

    def generate(self, request):
        i = self.index
        self.index += 1
        if i == 0:
            # No starter implementation or hidden model in public inputs/history.
            assert "class Identity" not in repr(request)
            name, args = "write_file", {"path": "top.sv", "content": RTL.decode()}
        elif i == 1:
            name, args = "compare_rtl", {"rtl": {"files": ["top.sv"], "entry_file": "top.sv", "entry_symbol": "M"}}
        elif i == 2:
            got = request.messages[-2]["content"]
            assert got["matched"] is True and got["report"].endswith("report.md")
            name, args = "measure_ppa", {"candidate": got["candidate"]}
        elif i == 3:
            assert "PRIVATE-EXPECTED" not in repr(request)
            measured = request.messages[-2]["content"]
            if "performance" in measured:
                assert measured["reference_source"] == "provided"
                assert measured["performance"]["source"] == "rtl"
                assert "mapped_score" not in measured
            name, args = "finish_search", {}
        else:
            raise AssertionError("cold replay must not generate")
        return ModelReply("", (ToolCall(str(i), name, args),))


class FakeNative:
    identity = "fake-ppa"

    @property
    def technology(self):
        return {"library": "fixture-not-a-physical-pdk"}

    def run_many(self, inputs, exp, modes, pool, objects):
        return {mode: (self.run(inputs, exp, mode, pool, objects), 0.) for mode in modes}

    def run(self, inputs, exp, mode, pool, objects):
        _, _, contract = fixture_task()
        if mode == "eda":
            return {"eda": {"stage": "synthesis", "status": "measured", "area_um2": 10.,
                             "simulation_source": inputs.source.artifact.as_dict(),
                             "timing": {"status": "met"}, "log": "yosys"}}
        result = {"score": 1, "code": "PRIVATE-EXPECTED",
                  "pool": list(_union(pool, [stimulus(contract, exp.workloads[0]).as_dict()]))}
        from behave.task_execution.judgment import ComponentJudgment
        result["judgment"] = ComponentJudgment(1, "simulator-concrete-score").as_dict()
        if mode in {"mapped", "performance", "final"}:
            result["performance"] = {"score": 1, "time_ns": 80.}
        return result


def test_search_uses_shared_loop_private_feedback_and_cold_replay(tmp_path):
    public, task, _ = fixture_task()
    kwargs = {"public": public, "task": task, "experiment": experiment(),
              "spec": AttemptSpec("p", "ppa", {"model": "fake"}),
              "backend": Scripted(), "native": FakeNative(), "directory": tmp_path, "prompt": "pilot draft"}
    result = run_search(**kwargs)
    assert len(result["candidates"]) == 1 and result["frontier"] == []
    assert result["evaluation_status"] == "awaiting_final"
    assert not any(item["final"] for item in result["candidates"][0]["history"])
    assert result["turns"] == 4
    assert result["billing"]["unpriced_dispatches"] == 4
    assert result["measurement_requests"] == 3  # baseline, EDA, source workload
    assert run_search(**kwargs) == result
    compared = compare_searches(searches={"method/replica": tmp_path}, task=task,
        experiment=experiment(), native=FakeNative(), directory=tmp_path / "common")
    assert len(compared["frontiers"]["method/replica"]) == 1
    assert compared["final_service_calls"] == 1
    assert compare_searches(searches={"method/replica": tmp_path}, task=task,
        experiment=experiment(), native=FakeNative(), directory=tmp_path / "common") == compared


def test_library_area_cannot_be_overridden_by_candidate():
    cell = {"attributes": {"area": "4.0"}, "ports": {}}
    library = canonical_json({"modules": {"cell": cell}})
    design = {"modules": {"M": {"cells": {"u0": {"type": "cell"}}}, "cell": cell}}
    stats = canonical_json({"modules": {"\\M": {"area": 4.0}}})
    assert mapped_area(canonical_json(design), stats, "M", library) == 4.
    design["modules"]["cell"] = {"attributes": {"area": "0.1"}, "ports": {}}
    with pytest.raises(ValueError, match="non-library"):
        mapped_area(canonical_json(design), stats, "M", library)


def test_pure_wires_have_zero_area_but_unmapped_cells_do_not():
    design = {"modules": {"M": {"cells": {}}}}
    stats = canonical_json({"modules": {"\\M": {}}})
    library = canonical_json({"modules": {}})
    assert mapped_area(canonical_json(design), stats, "M", library) == 0.
    design["modules"]["M"]["cells"]["u"] = {"type": "user_blackbox"}
    with pytest.raises(ValueError, match="non-library"):
        mapped_area(canonical_json(design), stats, "M", library)


def test_technology_metadata_is_public_but_units_cannot_silently_change():
    library = b'library ("example_tt") {\n time_unit : "1ns";\n capacitive_load_unit(1.000, "pf");\n}'
    assert library_description(library)["library"] == "example_tt"
    with pytest.raises(ValueError, match="units"):
        library_description(library.replace(b"1ns", b"1ps"))


def test_frontier_requires_correct_complete_timed_designs():
    def row(key, area, time, score=1, timing="met"):
        return {"candidate": key, "score": score, "mapped_score": 1, "performance": {"score": 1, "time_ns": time},
                "eda": {"status": "measured", "area_um2": area, "timing": {"status": timing}}}
    assert frontier([row("a", 10., 80.), row("b", 12., 90.), row("c", 9., 100.),
                     row("wrong", 1., 1., score=0), row("no-result", 1., 1., score=None),
                     row("slow-clock", 1., 1., timing="violated")]) == ["a", "c"]


@pytest.mark.parametrize('period_ns', [10., 12.5, 20.])
def test_completion_counts_stall_and_excludes_observation_tail(period_ns):
    from types import SimpleNamespace

    from behave.sim import api

    _, _, contract = fixture_task()
    work = Workload("w", ((1,), (2,)))
    trace = SimpleNamespace(records=(api.SlotRecord(), api.SlotRecord(), api.SlotRecord(), api.SlotRecord(),
        api.SlotRecord(accepted={"in": (1,)}), api.SlotRecord(accepted={"in": (2,), "out": (1,)}),
        api.SlotRecord(accepted={"out": (2,)}), api.SlotRecord(), api.SlotRecord()))
    request = stimulus(contract, work)
    trace.request = request
    trace.stimulus = api.Stimulus(queues=dict(request.queues), slots=(
        *(api.EventSlot(reset=True) for _ in range(3)),
        *(api.EventSlot(offers=("in",)) for _ in range(6))))
    row = completion(api.workload_window(contract, trace, expected_outputs={"out": 2}), work, period_ns)
    assert row["cycles"] == 4 and row["time_ns"] == 4 * period_ns


@pytest.mark.parametrize("accepted_inputs", [1, 2])
def test_partial_queue_or_missing_response_has_no_workload_completion_time(accepted_inputs):
    from types import SimpleNamespace

    from behave.sim import api

    _, _, contract = fixture_task()
    work = Workload("w", ((1,), (2,)))
    request = stimulus(contract, work)
    records = [api.SlotRecord(accepted={"in": (1,)}),
               api.SlotRecord(accepted={"out": (1,)})]
    if accepted_inputs == 2:
        records.append(api.SlotRecord(accepted={"in": (2,)}))
    trace = SimpleNamespace(request=request, records=tuple(records),
        stimulus=api.Stimulus(queues=dict(request.queues),
                             slots=tuple(api.EventSlot(offers=("in",)) for _ in records)))
    row = completion(api.workload_window(contract, trace, expected_outputs={"out": 2}), work, 12.5)
    assert row["cycles"] is None and row["time_ns"] is None
    assert row["status"] == "not-completed"


@pytest.mark.parametrize("output,status", [
    ("BEHAVE_PPA_SETUP_NS 2.00\nBEHAVE_PPA_HOLD_NS 0.10", "met"),
    ("BEHAVE_PPA_SETUP_NS -2.00\nBEHAVE_PPA_HOLD_NS 0.10", "violated"),
    ("Error: missing clock\nBEHAVE_PPA_SETUP_NS 2.00\nBEHAVE_PPA_HOLD_NS 0.10", "unresolved"),
    ("Error 123: missing clock\nBEHAVE_PPA_SETUP_NS 2.00\nBEHAVE_PPA_HOLD_NS 0.10", "unresolved"),
    ("Warning 160: invalid port\nBEHAVE_PPA_SETUP_NS 2.00\nBEHAVE_PPA_HOLD_NS 0.10", "unresolved"),
    ("BEHAVE_PPA_SETUP_NS inf", "unresolved"), ("", "unresolved")])
def test_timing_never_admits_missing_or_error_reports(output, status):
    assert timing_result(output)["status"] == status


def test_config_rejects_nonfinite_and_roundtrips():
    exp = experiment()
    assert Experiment.from_dict(__import__("json").loads(canonical_json(exp.as_dict()))) == exp
    with pytest.raises(ValueError):
        replace(exp.timing, period_ns=float("nan"))


def test_workload_rejects_wrong_reset_and_out_of_range_values():
    _, _, contract = fixture_task()
    with pytest.raises(ValueError, match="reset"):
        stimulus(contract, replace(experiment().workloads[0], reset_cycles=1))
    with pytest.raises(ValueError, match="outside"):
        stimulus(contract, replace(experiment().workloads[0], inputs=((256,),)))


def test_unknown_measurement_never_retries_or_reuses_old_metrics(tmp_path):
    from behave.agent.attempt import AttemptInterrupted
    from behave.agent.run_store import JournalDirectory
    from behave.ppa.workflow import SearchArchive

    _, task, _ = fixture_task()
    run = JournalDirectory(tmp_path)
    archive = SearchArchive(run, task, experiment(), FakeNative())
    archive.append("requested", {"candidate": "unknown", "mode": "eda", "final": False})
    with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
        SearchArchive(run, task, experiment(), FakeNative())


def test_performance_uses_captured_rtl_not_the_mapped_implementation(tmp_path):
    from behave.agent.run_store import JournalDirectory
    from behave.ppa.workflow import SearchArchive

    _, task, _ = fixture_task()
    run = JournalDirectory(tmp_path)
    source = file("source.sv", RTL)
    mapped = file("mapped.v", REGISTERED_RTL)
    for f in (source, mapped):
        run.objects.put(f.data)
    mapped_artifact = SourceArtifact((mapped.reference,), "mapped.v", "M")

    class VerifySource(FakeNative):
        def run(self, inputs, exp, mode, pool, objects):
            assert mode == "performance"
            assert inputs.source.artifact == SourceArtifact((source.reference,), "source.sv", "M")
            return {"performance": {"score": 1, "time_ns": 100.}}

    archive = SearchArchive(run, task, experiment(), VerifySource())
    archive.candidates["c"] = {"source": SourceArtifact((source.reference,), "source.sv", "M").as_dict(),
                               "eda": {"simulation_source": mapped_artifact.as_dict()}}
    archive.invoke("c", "performance")
    assert archive.candidates["c"]["performance"]["time_ns"] == 100.


def test_reference_preparation_failure_cannot_be_candidate_zero(tmp_path, monkeypatch):
    from behave.task_execution.simulator_judge import _SimulatorService
    from behave.task_execution.comparison import ComparisonRequest
    from behave.sim import api
    from behave.task_execution.comparison import ComparisonInputs

    _, task, _ = fixture_task()
    def broken_reference(*args, **kwargs):
        raise api.FailureRaised(api.RunFailure.candidate("bad-reference-source", api.Refusal("bad-source", "test")))

    monkeypatch.setattr(api, "prepare_behavior_bundle", broken_reference)
    model = file("model.py", task.reference_behavior.files[0].data)
    reference = LoadedSource(SourceArtifact((model.reference,), "model.py", "Identity"), (model,))
    inputs = ComparisonInputs("rtl", task.reference_behavior, reference,
                               task.contract, task.run_policy, task.judge_policy)
    result = _SimulatorService().evaluate(ComparisonRequest(inputs, tmp_path), DirectoryStore(tmp_path / "objects"))
    assert result.score is None and result.origin == "reference"


@pytest.mark.eda
@pytest.mark.isolation
def test_native_ppa_yosys_sta_and_sim(tmp_path, pytestconfig):
    import os
    import shutil

    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs

    root = Path(os.environ.get("BEHAVE_PPA_ROOT", Path(__file__).resolve().parents[2] / "external/ppa-pilot"))
    if not (root / "sky130.lib").is_file():
        pytest.skip("explicit SKY130 pilot installation required")
    public, task, _ = fixture_task()
    f = file("top.sv", RTL)
    source = LoadedSource(SourceArtifact((f.reference,), "top.sv", "M"), (f,))
    inputs = ComparisonInputs("rtl", source, task.reference_behavior, task.contract, task.run_policy, task.judge_policy)
    native = NativePPA(liberty=root / "sky130.lib", sta=root / "opensta/usr/bin/sta")
    store = DirectoryStore(tmp_path / "objects")
    eda = native.run(inputs, experiment(), "eda", (), store)
    if eda.get("eda", {}).get("status") != "measured":
        print(eda.get("eda", {}).get("log"), flush=True)
        print([store.read(BlobRef.from_dict(ref)).decode(errors="replace")
               for ref in eda.get("evidence", ()) if ref["size_bytes"] < 2000], flush=True)
    assert eda.get("eda", {}).get("status") == "measured", (eda, [
        store.read(BlobRef.from_dict(ref)).decode(errors="replace")
        for ref in eda.get("evidence", ())])
    if eda["eda"]["timing"]["status"] != "met":
        pytest.fail(eda["eda"]["log"].split("OpenSTA")[-1])
    assert eda["eda"]["power"]["status"] == "estimated", eda["eda"]["log"]
    assert eda["eda"]["power"]["total_w"] > 0
    perf = native.run(inputs, experiment(), "performance", (), store)
    if perf.get("score") is None:
        pytest.fail(canonical_json({"authority": perf.get("authority"), "failures": perf.get("failures")}).decode())
    assert perf.get("score") == 1, perf
    assert perf["performance"]["time_ns"] == 80., perf
    kwargs = {"public": public, "task": task, "experiment": experiment(),
        "spec": AttemptSpec("p", "ppa", {"model": "fake"}), "backend": Scripted(),
        "native": native, "directory": tmp_path / "search", "prompt": "pilot draft"}
    result = run_search(**kwargs)
    assert result["frontier"] == [] and result["evaluation_status"] == "awaiting_final", result
    assert result["measurement_requests"] == 3
    assert [h["mode"] for h in result["candidates"][0]["history"]] == [
        "check", "eda", "performance"]
    assert run_search(**kwargs) == result  # no further provider or service calls
    comparison = compare_searches(searches={"scripted-pilot": tmp_path / "search"}, task=task,
        experiment=experiment(), native=native, directory=tmp_path / "final")
    assert len(comparison["frontiers"]["scripted-pilot"]) == 1, comparison
    assert comparison["final_service_calls"] == 1
    # The detached lane retains the actual journals/CAS beside its JUnit record,
    # not just an assertion that an ephemeral end-to-end test once passed.
    report = pytestconfig.getoption("xmlpath")
    if report:
        evidence = Path(report).resolve().parent / "ppa-native"
        evidence.mkdir(exist_ok=True)
        for name in ("search", "final"):
            shutil.copytree(tmp_path / name, evidence / name, dirs_exist_ok=False)
        (evidence / "summary.json").write_bytes(canonical_json({
            "backend": "scripted-four-turn-fixture-not-live-llm", "turns": result["turns"],
            "candidates": len(result["candidates"]), "frontier": result["frontier"],
            "area_um2": result["candidates"][0]["eda"]["area_um2"],
            "timing": result["candidates"][0]["eda"]["timing"],
            "power": result["candidates"][0]["eda"]["power"],
            "performance": result["candidates"][0]["performance"],
            "common_final_service_calls": comparison["final_service_calls"]}))


@pytest.mark.eda
@pytest.mark.isolation
def test_native_ppa_combinational_bit_aliases_are_readable_by_sta(tmp_path):
    import os

    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs

    root = Path(os.environ.get("BEHAVE_PPA_ROOT", Path(__file__).resolve().parents[2] / "external/ppa-pilot"))
    if not (root / "sky130.lib").is_file():
        pytest.skip("explicit SKY130 pilot installation required")
    _, task, _ = fixture_task()
    f = file("top.sv", RTL.replace(b"assign out_y = in_a;", b"assign out_y = in_a ^ 8'd37;"))
    source = LoadedSource(SourceArtifact((f.reference,), "top.sv", "M"), (f,))
    inputs = ComparisonInputs("rtl", source, task.reference_behavior, task.contract, task.run_policy, task.judge_policy)
    native = NativePPA(liberty=root / "sky130.lib", sta=root / "opensta/usr/bin/sta")
    result = native.run(inputs, experiment(), "eda", (), DirectoryStore(tmp_path / "objects"))
    assert result.get("eda", {}).get("status") == "measured", result
    assert result["eda"]["timing"]["status"] == "met", result["eda"]["log"]


@pytest.mark.eda
@pytest.mark.isolation
def test_native_ppa_synthesizes_the_same_conditional_branch_as_source_simulation(tmp_path):
    import os

    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs

    root = Path(os.environ.get("BEHAVE_PPA_ROOT", Path(__file__).resolve().parents[2] / "external/ppa-pilot"))
    if not (root / "sky130.lib").is_file():
        pytest.skip("explicit SKY130 pilot installation required")
    _, task, contract = fixture_task()
    rtl = RTL.replace(b"assign out_y = in_a;", b"`ifdef SYNTHESIS\nassign out_y = 0;\n`else\nassign out_y = in_a;\n`endif")
    f = file("top.sv", rtl)
    source = LoadedSource(SourceArtifact((f.reference,), "top.sv", "M"), (f,))
    inputs = ComparisonInputs("rtl", source, task.reference_behavior, task.contract, task.run_policy, task.judge_policy)
    native = NativePPA(liberty=root / "sky130.lib", sta=root / "opensta/usr/bin/sta")
    store = DirectoryStore(tmp_path / "objects")
    eda = native.run(inputs, experiment(), "eda", (), store)
    assert eda.get("eda", {}).get("status") == "measured", eda
    artifact = SourceArtifact.from_dict(eda["eda"]["simulation_source"])
    mapped = LoadedSource(artifact, tuple(FileData(f, store.read(f.content)) for f in artifact.files))
    pool = (stimulus(contract, experiment().workloads[0]).as_dict(),)
    result = native.run(replace(inputs, source=mapped), experiment(), "mapped", pool, store)
    if result.get("score") is None:
        pytest.fail(canonical_json({"code": result.get("code"), "authority": result.get("authority"),
                                   "failures": result.get("failures")}).decode())
    assert result.get("score") == 1, result
    assert result["performance"]["time_ns"] == 80.


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("sat_resource_sharing", [False, True])
@pytest.mark.parametrize("signed", [False, True])
def test_native_ppa_registers_have_area_and_real_completion_latency(tmp_path, sat_resource_sharing, signed):
    import os

    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs

    root = Path(os.environ.get("BEHAVE_PPA_ROOT", Path(__file__).resolve().parents[2] / "external/ppa-pilot"))
    if not (root / "sky130.lib").is_file():
        pytest.skip("explicit SKY130 pilot installation required")
    _, task, _ = fixture_task()
    rtl = REGISTERED_RTL
    if signed:
        rtl = rtl.replace(b"[7:0]", b"signed [7:0]")
    f = file("top.sv", b'`include "width.svh"\n' + rtl.replace(b"[7:0]", b"[`PPA_WIDTH-1:0]"))
    header = file("headers with spaces/width.svh", b"`define PPA_WIDTH 8\n")
    order = file("rtl.f", b"top.sv\n")
    captured = (f, header, order)
    exp = replace(experiment(), sat_resource_sharing=sat_resource_sharing)
    source = LoadedSource(SourceArtifact(tuple(row.reference for row in captured), "top.sv", "M"), captured)
    inputs = ComparisonInputs("rtl", source, task.reference_behavior, task.contract, task.run_policy, task.judge_policy)
    native = NativePPA(liberty=root / "sky130.lib", sta=root / "opensta/usr/bin/sta")
    store = DirectoryStore(tmp_path / "objects")
    eda = native.run(inputs, exp, "eda", (), store)
    if eda.get("eda", {}).get("status") != "measured":
        pytest.fail(eda.get("eda", {}).get("log", repr(eda)))
    assert eda.get("eda", {}).get("status") == "measured", eda
    assert eda["eda"]["area_um2"] > 12.512  # the combinational two-gate fixture
    assert eda["eda"]["power"]["status"] == "estimated", eda["eda"]["log"]
    assert eda["eda"]["power"]["internal_w"] > 0
    artifact = SourceArtifact.from_dict(eda["eda"]["simulation_source"])
    mapped = LoadedSource(artifact, tuple(FileData(f, store.read(f.content)) for f in artifact.files))
    result = native.run(replace(inputs, source=mapped), exp, "mapped", (), store)
    assert result.get("score") == 1, result
    perf = native.run(inputs, exp, "performance", (), store)
    assert perf["performance"]["source"] == "rtl"
    assert result["performance"]["source"] == "mapped"
    assert result["performance"]["time_ns"] == 100.
    if signed:
        # This fixture's contract is unsigned. Readable STA/netlist output
        # must not make the mismatched source interface admissible.
        assert perf["score"] == 0 and perf["code"] == "admission-signedness"
        assert perf["performance"]["time_ns"] is None
    else:
        assert perf["score"] == 1 and perf["performance"]["time_ns"] == 100., perf
        assert result["performance"]["workloads"] == perf["performance"]["workloads"]
