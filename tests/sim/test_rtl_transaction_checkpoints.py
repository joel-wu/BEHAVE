"""Real public scoring: settled prefixes are not shortened/reset test cases."""

import json

import pytest

from behave.sim import api, evaluation, runtime_execution
from behave.sim.scoring_policy import active_scoring

from .test_runtime_schedule import _contract


@pytest.mark.cosim
@pytest.mark.parametrize("mode", ["budget", "wrong-second", "complete"])
@pytest.mark.parametrize("ratio,declared", [(1, True), (2, True), (2, False)])
def test_public_checkpoint_keeps_one_process_and_original_request(tmp_path, monkeypatch, mode, ratio, declared):
    contract = _contract(ratio=ratio if declared else None)
    response = '{"y": a}' if ratio == 1 else '[{"y": a}, {"y": a}]'
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": ('class M:\n    def reset(self): pass\n'
                         '    def process(self, a): return ' + response + '\n').encode()},
        config={"class": "M"})
    source = '''module M(input clk,rst,iv,input [7:0] a,output ir,
                       output reg ov,input orr,output reg [7:0] y);
      integer seen, remaining;
      assign ir=!ov;
      always @(posedge clk) begin
        if(rst) begin ov<=0; y<=0; seen<=0; remaining<=0; end
        else begin
          if(ov && orr) begin remaining<=remaining-1; if(remaining==1) ov<=0; end
          if(iv && ir) begin ov<=1; y<=VALUE; seen<=seen+1; remaining<=RATIO; end
        end
      end
    endmodule'''.replace("VALUE", "a+(seen!=0)" if mode == "wrong-second" else "a").replace("RATIO", str(ratio))
    path = tmp_path / "rtl.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "artifact"))
    observed = []
    planned = []
    original_plan = runtime_execution.completion_plan
    def completion_plan(*args):
        planned.append(args[1].fingerprint)
        return original_plan(*args)
    monkeypatch.setattr(runtime_execution, "completion_plan", completion_plan)
    original = evaluation.assess_checked

    def assess(*args, **kwargs):
        result = original(*args, **kwargs)
        trace = result.result.trace
        if trace is not None and trace.completion is api.Completion.CHECKPOINT:
            observed.append(trace.accepted_count(["in"]))
            if mode == "budget":
                active_scoring().deadline = 0
        return result

    monkeypatch.setattr(evaluation, "assess_checked", assess)
    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "1")
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3 if mode == "budget" else 1, max_queue_depth=100),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False,
        scoring_policy=api.ScoringPolicy(time_budget_s=120, transaction_checkpoints=True)))
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert observed, result.decision.as_dict() if result.decision else result.as_dict()
    assert result.decision is not None, result.as_dict()
    assert result.decision.authoritative is (mode != "budget"), result.as_dict()
    assert result.evaluation.passed is (mode == "complete"), result.as_dict()
    if mode == "budget":
        assert len(result.evaluation.requested_stimuli) > 1
        assert len(planned) == 1  # No full-pool reference barrier before useful replay.
        assert not result.evaluation.required_complete and not result.evaluation.complete
        run = result.evaluation.results[0]
        assert run.trace.completion is api.Completion.CHECKPOINT
        assert run.trace.accepted_count(["in"]) == 1
        assert run.trace.accepted_count(["out"]) == ratio
        assert run.stimulus.depth("in") == 100
        assert run.trace.stimulus.queues == run.stimulus.queues
    elif mode == "wrong-second":
        assert result.evaluation.failure_witness is not None
        assert result.evaluation.mismatch_count > 0
    else:
        assert result.evaluation.complete
        assert all(row.trace.completion is api.Completion.NORMAL for row in result.evaluation.results)
