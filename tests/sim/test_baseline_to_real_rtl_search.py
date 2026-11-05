"""Baseline hits, real local solvers and shared replay through the public API."""

from collections import Counter
import json
import time

import pytest

from behave.sim import api
from behave.sim.rtl.artifact import RTLArtifact

pytestmark = pytest.mark.cosim

VALUE = 0x12345678
REFERENCE = """class M:
    def reset(self):
        pass
    def process(self, a):
        if a == 305419896:
            y = a ^ 1
        else:
            y = a
        return {"y": y}
"""
RTL = """module M(input clk,rst,in_valid,out_ready,input [31:0] a,
    output in_ready,out_valid,output reg [31:0] y);
    reg full;
    assign in_ready = !full || out_ready;
    assign out_valid = full;
    always @(posedge clk) begin
        if(rst) begin full<=0; y<=0; end
        else if(in_ready) begin
            full<=in_valid;
            if(in_valid) begin
                if(a==32'h12345678) y<=a^32'd1;
                else y<=a;
            end
        end
    end
endmodule
"""


def test_baseline_precedes_real_search_and_only_new_inputs_need_concrete_replay(
        tmp_path, monkeypatch):
    started = time.monotonic()
    integer = api.FiniteType(32)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", integer, "a")],
                     api.ready_valid("in", "in_valid", "in_ready"), clock="clk",
                     kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", integer, "y")],
                     api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
                     kind=api.Kind.TRANSACTION, answers_per_transaction=1)],
        [api.ClockDomain("clk", "clk", "rst")])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    baseline = api.build_baseline(contract)
    assert baseline.policy.case_limit == 50
    assert all(seed.depth("in") == 100 for seed in baseline.stimuli)
    assert not any(value == VALUE for seed in baseline.stimuli
                   for _, queue in seed.queues for value, in queue)

    trace_batches = []
    original = RTLArtifact._traces_per_process

    def record_execution(artifact, task_contract, stimuli, *args, **kwargs):
        seeds = tuple(stimuli)
        purpose = kwargs.get("_purpose")
        row = {
            "seconds": time.monotonic() - started,
            "purpose": "concrete" if purpose is None else purpose.value,
            "stimuli": [seed.fingerprint for seed in seeds],
            "state_names": list(kwargs.get("state_names", ())),
        }
        trace_batches.append(row)
        began = time.monotonic()
        try:
            return original(artifact, task_contract, seeds, *args, **kwargs)
        finally:
            row["elapsed_s"] = time.monotonic() - began

    # This spy counts executions but delegates every byte, trace and hit to the
    # real Driver. Neither Select nor Probe nor their answers are mocked.
    monkeypatch.setattr(RTLArtifact, "_traces_per_process", record_execution)
    path = tmp_path / "candidate.sv"
    path.write_text(RTL)
    task = api.admit(contract, api.realization_of_rtl_source(
        RTL, top="M", contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"),
                             api.GenerationSpec(220, 128, 2, 4))
    request = handle.command_request(
        run_directory=tmp_path / "command", baseline=baseline,
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance"),
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=8, debug=True),
        run_analysis=False, reference=reference)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps({
        "elapsed_s": time.monotonic() - started,
        "trace_batches": trace_batches, "result": result.as_dict()}, indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    assert result.evaluation.complete

    first = result.generated.searches[0]
    rare = "branch:behavior.py:5:11:0:T"
    assert rare in first.telemetry["baseline_coverage"]["remaining"]
    assert rare not in first.telemetry["baseline_coverage"]["hits"]
    assert rare in first.telemetry["coverage"]["hits"]
    assert first.telemetry["probes"] > 0
    rtl = result.generated.searches[1]
    assert rtl.provider == "rtl/exact-local"
    assert rtl.telemetry["probes"] > 0
    assert not rtl.telemetry["refusals"]
    remaining = set(rtl.telemetry["baseline_coverage"]["remaining"])
    assert all(set(rtl.telemetry["target_sets"][answer["target_set"]]) <= remaining
               for answer in rtl.as_dict()["telemetry"]["answers"])
    assert any(value == VALUE for seed in result.generated.stimuli
               for _, queue in seed.queues for value, in queue)

    initial = {seed.fingerprint for seed in baseline.stimuli}
    assert set(trace_batches[0]["stimuli"]) == initial
    executions = Counter(identity for batch in trace_batches if batch["purpose"] == "concrete"
                         for identity in batch["stimuli"])
    assert all(executions[identity] == 1 for identity in initial)
    assert all(count == 1 for count in executions.values())
    assert all(run.trace.source_evidence is not None for run in result.evaluation.results)
    # The baseline replays its own continuous workload before Search; the final
    # suite reuses it beside the workload that concatenates the expanded pool.
    suite = dict(dict(result.evaluation.telemetry)["replay_suite"])
    assert len(suite["continuous"]) == 2
    assert len(result.evaluation.results) == len(result.generated.stimuli) + 2
