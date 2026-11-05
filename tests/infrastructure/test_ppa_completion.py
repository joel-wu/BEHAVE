"""PPA consumes Sim completion for workloads without a fixed response ratio."""

from dataclasses import replace

import pytest

from test_ppa import experiment, file
from tests.sim.test_api_path import contract
from behave.core.artifacts import LoadedSource, SourceArtifact
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.ppa import worker
from behave.ppa.config import Workload
from behave.sim import api
from behave.task_execution.comparison import ComparisonInputs
from behave.task_execution.judge_policy import JudgePolicy


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("copies", [1, 2])
@pytest.mark.parametrize("values", [(1, 2, 3, 4), (2, 4)])
def test_reference_derived_variable_and_zero_output_workloads(tmp_path, values, copies):
    task = contract()
    behavior = file("behavior.py", f'''class Filter:
 def reset(self): pass
 def process(self,in_a):
  if not (in_a & 1): return []
  return [{{"out_y":(in_a + i) & 255}} for i in range({copies})]
'''.encode())
    rtl = file("top.sv", f'''module M(input clk,rst,in_valid,out_ready,
 input [7:0] in_a, output in_ready,out_valid, output [7:0] out_y);
 reg second = 0;
 reg [7:0] saved = 0;
 assign in_ready = !rst && !second && (!in_a[0] || out_ready);
 assign out_valid = !rst && (second || (in_valid && in_a[0]));
 assign out_y = second ? saved : in_a;
 always @(posedge clk) begin
  if (rst) begin second <= 0; saved <= 0; end
  else if (second && out_ready) second <= 0;
  else if (in_valid && in_ready && in_a[0] && {copies} == 2) begin
   second <= 1; saved <= in_a + 1'b1;
  end
 end
endmodule
'''.encode())
    def source(item, symbol):
        return LoadedSource(SourceArtifact((item.reference,), item.reference.path, symbol), (item,))

    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, baseline_cases=1,
                         sequence_length=2, search_seconds=0, analysis=None)
    inputs = ComparisonInputs("rtl", source(rtl, "M"), source(behavior, "Filter"),
        file("contract.json", canonical_json(task.as_dict())), b"null", policy.to_bytes())
    workload = Workload("filter", tuple((value,) for value in values))
    exp = replace(experiment(), workloads=(workload,))
    result = worker.execute(inputs, exp, "performance", (), tmp_path,
                            DirectoryStore(tmp_path / "objects"), liberty=None, sta=None)
    assert result["score"] == 1, result
    metric, = result["performance"]["workloads"]
    cycles = len(values) + (copies - 1) * sum(value & 1 for value in values)
    assert metric["cycles"] == cycles
    assert metric["time_ns"] == cycles * exp.timing.period_ns
    assert task.outputs()[0].answers_per_transaction is None
