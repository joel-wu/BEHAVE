"""Exact bus safety classification through the public, native-audited path."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.rtl.exact_product import global_schedule_for_stimuli
from tests.sim.test_apb_protocol import _contract as apb_contract
from tests.sim.test_axi_lite_protocol import _contract as axil_contract
from tests.sim.test_rtl_public_analysis_path import _BaselineOnly
from tests.support.reference import load

pytestmark = pytest.mark.cosim


def _fixture(kind, faulty):
    if kind == "apb":
        contract = apb_contract()
        ready = "paddr != 8'd37" if faulty else "1'b1"
        source = f"""module dut(input clk, rst, z_sel, a_enable,
          input [7:0] paddr, output pready, output [7:0] prdata, output pslverr);
          assign pready = {ready};
          assign prdata = paddr;
          assign pslverr = 0;
        endmodule"""
        reference = "def process(self, paddr): return {'prdata': paddr, 'pslverr': 0}"
        return contract, source, reference, "monitor.apb.wait-limit"

    write = kind == "write"
    contract = axil_contract(write)
    channel, address = ("b", "awaddr") if write else ("r", "araddr")
    ports = ("input awvalid, wvalid, input [31:0] awaddr, wdata, input [3:0] wstrb, "
             "output awready, wready" if write else
             "input arvalid, input [31:0] araddr, output arready, output [31:0] rdata")
    wires = ("assign awready = !pending; assign wready = !pending;" if write else
             "assign arready = !pending; assign rdata = held;")
    accepted = "awvalid && wvalid" if write else "arvalid"
    response = "held == 32'd37 ? 2'd1 : 2'd0" if faulty else "2'd0"
    source = f"""module dut(input clk, rst, {ports}, input {channel}ready,
      output {channel}valid, output [1:0] {channel}resp);
      reg pending;
      reg [31:0] held;
      always @(posedge clk) begin
        if (rst) begin pending <= 0; held <= 0; end
        else if (!pending && {accepted}) begin pending <= 1; held <= {address}; end
        else if (pending && {channel}ready) pending <= 0;
      end
      {wires}
      assign {channel}valid = pending;
      assign {channel}resp = {response};
    endmodule"""
    reference = ("def process(self, awaddr, wdata, wstrb): return {'bresp': 0}" if write else
                 "def process(self, araddr): return {'rdata': araddr, 'rresp': 0}")
    return contract, source, reference, "monitor.axil.response-code"


@pytest.mark.parametrize("kind", ["apb", "read", "write"])
@pytest.mark.parametrize("faulty", [False, True], ids=["correct", "hidden-safety-fault"])
def test_public_bus_analysis_classifies_safe_and_reachable_obligations(tmp_path, kind, faulty):
    contract, source, method, target = _fixture(kind, faulty)
    reference = load({"behavior.py": f"class Model:\n    def reset(self): pass\n    {method}\n".encode()},
                     config={"class": "Model"})
    rtl = tmp_path / "dut.sv"
    rtl.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="dut", contract=contract),
                     reference=reference)
    generation = api.GenerationSpec(horizon=8, queue_bound=1, reset_hold_cycles=1, drain=6,
                                    stall_every=0)
    baseline = api.build_baseline(contract, generation,
                                 policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    # Freeze scheduling before running: exact analysis varies every declared
    # transaction field, not just the values tested by the baseline.
    schedule = global_schedule_for_stimuli(contract, baseline.stimuli)
    handle = api.prepare_rtl(task, api.RTLConfig(rtl, "dut", tmp_path / "rtl", timeout_s=300),
                             generation, analysis_clock_schedule=schedule)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation", baseline=baseline, reference=reference,
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=True,
        # This test qualifies all goals, including independent native witnesses.
        # Its explicit budget is not a production default or a per-query cutoff.
        analysis_wall_timeout_seconds=600))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.required_complete and result.evaluation.passed, result.evaluation.as_dict()
    assert target not in result.evaluation.hits
    outcome = result.analysis
    assert outcome.verdict is api.Verdict.CLASSIFIED, outcome.as_dict()
    assert outcome.concrete == result.evaluation.fingerprint
    assert outcome.as_dict()["per_goal"][target]["status"] == (
        "reached" if faulty else "bounded-unreachable")
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted["authority"]["passed"] is True
    assert persisted["report"]["analysis"] == json.loads(json.dumps(outcome.as_dict()))
