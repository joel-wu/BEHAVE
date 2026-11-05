"""Loop/helper observation reaches the ordinary, unmodified public scorer."""

import json

import pytest

from behave.sim import api

from .test_baseline_public_sequence import REFERENCE, _contract

pytestmark = pytest.mark.cosim


@pytest.mark.parametrize("mutant", (False, True))
@pytest.mark.parametrize("construction", (False, True))
def test_loop_helper_full_baseline_and_candidate_mismatch(tmp_path, mutant, construction):
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    one, zero = (0, 1) if mutant else (1, 0)
    source = f"""module M(input clk,rst,in_valid,out_ready,
      input [7:0] a, output in_ready,out_valid, output reg [7:0] y);
      function automatic copy_bit(v);
        if(v) copy_bit={one}; else copy_bit={zero};
      endfunction
      assign in_ready=out_ready;
      assign out_valid=in_valid;
      integer i;
      always_comb for(i=0;i<8;i++) y[i]=copy_bit(a[i]);
    endmodule
    """
    if construction:
        source = source.replace("endmodule", """
          logic initial_value;
          initial initial_value=copy_bit(1'b1);
          function automatic unused(input v); unused=copy_bit(v); endfunction
        endmodule""")
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"),
                             api.GenerationSpec(220, 128, 2, 4))
    request = handle.command_request(
        run_directory=tmp_path / "command", baseline=api.BaselinePolicy(),
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance",
                                             enable_local_guidance=False),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False, reference=reference)
    assert request.baseline.policy.case_limit == 50
    assert all(stimulus.depth("in") == 100 for stimulus in request.baseline.stimuli)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    evaluation, decision = result.evaluation, result.decision
    assert decision.authoritative
    assert decision.passed is not mutant
    assert bool(evaluation.mismatch_count) is mutant
    assert not evaluation.safety_hits
    assert all(run.trace.source_evidence is not None for run in evaluation.results)
    if mutant:
        # Transaction checkpoints let a fully checked failing prefix decide;
        # only a pass requires the complete baseline.
        return
    assert evaluation.complete
    isolated = len(request.baseline.stimuli)
    assert len(evaluation.results) == isolated + 1
    assert all(run.trace.accepted_count(["in"]) == 100 for run in evaluation.results[:isolated])
    assert evaluation.results[-1].trace.accepted_count(["in"]) == 100 * isolated
