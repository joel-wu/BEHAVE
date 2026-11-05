"""Real baseline scores distinguish wrong values, unsafe timing and bad references.

No fabricated traces or observer bindings: each case enters through the public
RTL command, installed frontend, simulator, isolated reference and result store.
"""

import json
from pathlib import Path

import pytest

from behave.sim import api

from .test_baseline_public_sequence import REFERENCE, RTL, _contract


@pytest.mark.cosim
@pytest.mark.parametrize("fault", ["none", "value", "stability", "reference"])
def test_public_baseline_verdict_has_the_right_evidence_and_owner(tmp_path, fault):
    contract = _contract()
    expression = {
        "none": "a",
        "value": "a ^ (n >= 64 ? 8'd1 : 8'd0)",
        # Every accepted output is still correct. Only a stalled offer changes
        # before acceptance, so comparison alone cannot reject this design.
        "stability": "out_ready ? a : (a ^ 8'd1)",
        "reference": "a",
    }[fault]
    reference_source = REFERENCE
    if fault == "reference":
        reference_source = REFERENCE.replace(
            '        return {"y": a}',
            '        if self.n >= 65:\n'
            '            return {"y": a // (self.n - 65)}\n'
            '        return {"y": a}')
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()}, config={"class": "M"})
    source = RTL.replace("VALUE", expression)
    path = tmp_path / "candidate.sv"
    path.write_text(source)
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
    # Retain the full public decision even when a following assertion fails.
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    evaluation, decision = result.evaluation, result.decision
    assert evaluation is not None and decision is not None
    record = json.loads(Path(result.record_path).read_text())
    assert record["terminal"]
    assert decision.evaluation_fingerprint == evaluation.fingerprint

    if fault == "reference":
        assert not evaluation.complete
        assert not decision.authoritative and decision.passed is None
        assert "passed" not in record["authority"]
        assert decision.failures
        assert all(failure.origin is api.FailureOrigin.REFERENCE
                   and failure.disposition is api.FailureDisposition.NO_RESULT
                   for _, failure in decision.failures)
        return

    assert decision.authoritative
    assert decision.passed is (fault == "none")
    assert record["authority"]["passed"] is (fault == "none")
    assert not evaluation.no_result_failures
    isolated = len(request.baseline.stimuli)
    assert all(run.trace.source_evidence is not None for run in evaluation.results)
    if fault == "none":
        assert evaluation.complete and len(evaluation.results) == isolated + 1
        assert all(run.trace.accepted_count(["in"]) == 100
                   for run in evaluation.results[:isolated])
        assert evaluation.results[-1].trace.accepted_count(["in"]) == 100 * isolated
    else:
        # A failing baseline is scored by its independent witness and stops there.
        assert evaluation.failure_witness is not None
        assert result.generated.search_summary["stop_reason"] == "baseline-failed"
    if fault == "stability":
        assert evaluation.mismatch_count == evaluation.memory_mismatch_count == 0
        assert set(evaluation.safety_hits) == {"monitor.sink-stability"}
        assert decision.evidence["safety_hits"] == ("monitor.sink-stability",)
    else:
        assert not evaluation.safety_hits
        assert bool(evaluation.mismatch_count) is (fault == "value")
