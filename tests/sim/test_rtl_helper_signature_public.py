"""Implicit helper formals through the default public baseline and scorer."""

import json
import time

import pytest

from behave.sim import api

from .test_baseline_public_sequence import REFERENCE, _contract

pytestmark = pytest.mark.cosim


@pytest.mark.parametrize("mutant", (False, True))
def test_public_baseline_preserves_implicit_helper_types(tmp_path, mutant):
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.replace('return {"y": a}',
                                          'return {"y": a & 1}').encode()},
        config={"class": "M"})
    # The first formal is one-bit logic, not the observer's integer context.
    # Wrong output must still fail even when the observer records real hits.
    value = "$bits(v) + 1" if mutant else "$bits(v)"
    source = f"""module M(input clk,rst,in_valid,out_ready,
      input [7:0] a, output in_ready,out_valid, output reg [7:0] y);
      function automatic [7:0] adjust(v);
        if(v) adjust={value}; else adjust=0;
      endfunction
      assign in_ready=out_ready;
      assign out_valid=in_valid;
      always_comb y=adjust(a);
    endmodule
    """
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    started = time.monotonic()
    task = api.admit(contract, api.realization_of_rtl_source(
        source, top="M", contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"),
                             api.GenerationSpec(220, 128, 2, 4))
    request = handle.command_request(
        run_directory=tmp_path / "command", baseline=api.BaselinePolicy(),
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance",
                                             enable_local_guidance=False),
        search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=False, reference=reference)
    assert request.baseline.policy.case_limit == 50
    assert all(stimulus.depth("in") == 100 for stimulus in request.baseline.stimuli)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps({
        "elapsed_s": time.monotonic() - started, "result": result.as_dict()}, indent=2))
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
    isolated_count = len(request.baseline.stimuli)
    assert len(evaluation.results) == isolated_count + 1
    assert all(run.trace.accepted_count(["in"]) == 100
               for run in evaluation.results[:isolated_count])
    assert evaluation.results[-1].trace.accepted_count(["in"]) == 100 * isolated_count
