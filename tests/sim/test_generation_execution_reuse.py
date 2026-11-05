"""Real production providers hand checked executions to the public scorer."""

import pytest

from behave.sim import api
from tests.sim.test_baseline_public_sequence import REFERENCE, RTL, _contract

VARIABLE_LATENCY_RTL = """module M(input wire clk, rst, in_valid, out_ready,
    input wire [7:0] a, output wire in_ready, out_valid,
    output wire [7:0] y);
    reg valid, pending;
    reg [3:0] remaining;
    reg [7:0] payload;
    assign in_ready = !pending && !valid;
    assign out_valid = valid;
    assign y = payload;
    always @(posedge clk) begin
        if (rst) begin
            valid <= 0; pending <= 0; remaining <= 0; payload <= 0;
        end else begin
            if (valid && out_ready) valid <= 0;
            if (in_valid && in_ready) begin
                payload <= a == 8'd137 ? (a ^ 8'd1) : a;
                remaining <= a == 8'd137 ? 4 : 0;
                pending <= 1;
            end else if (pending) begin
                if (remaining == 0) begin valid <= 1; pending <= 0; end
                else remaining <= remaining - 1;
            end
        end
    end
endmodule
"""


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
@pytest.mark.parametrize("runtime", (False, True))
def test_public_search_reuses_complete_baseline_results(tmp_path, kind, runtime):
    contract = _contract()
    source_behavior = REFERENCE.replace('return {"y": a}',
        'if a == 137:\n            return {"y": a ^ 1}\n        return {"y": a}')
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source_behavior.encode()}, config={"class": "M"})
    spec = api.GenerationSpec(12, 2, 2, 2, stall_every=3)
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(source_behavior)
        task = api.admit(contract, api.realization_of_behavior_source(
            source_behavior, contract, file="candidate.py"), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(
            path, file="candidate.py"), spec, bounded_analysis=False)
    else:
        source = (VARIABLE_LATENCY_RTL if runtime else
                  RTL.replace("VALUE", "a == 8'd137 ? (a ^ 8'd1) : a"))
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_rtl_source(
            source, top="M", contract=contract), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), spec)
    # Five distinct seeds exceed the old implicit one-seed session accounting.
    policy = api.BaselinePolicy(case_limit=6, max_queue_depth=2)
    baseline = api.build_baseline(contract, None if runtime else spec, policy=policy)
    providers = handle.generation_providers(reference, workdir=tmp_path / "generation")
    request = handle.command_request(
        run_directory=tmp_path / "command", baseline=policy if runtime else baseline,
        providers=providers,
        probe_budget=1, run_analysis=False, reference=reference)
    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.complete and result.evaluation.passed
    assert request.baseline.stimuli == baseline.stimuli
    assert result.generated.generation_spec == spec
    assert result.generated.searches[0].telemetry["probes"] == 1
    assert result.generated.searches[0].generated, result.generated.searches[0].as_dict()
    first_search = result.generated.searches[0].as_dict()["telemetry"]
    assert not first_search["selection_refusals"], first_search
    assert len(first_search["coverage"]["hits"]) > len(first_search["baseline_coverage"]["hits"])
    if runtime and kind == "rtl":
        lengths = {run.stimulus.fingerprint: len(run.trace.records)
                   for run in result.evaluation.results}
        baseline_max = max(lengths[seed.fingerprint] for seed in baseline.stimuli)
        assert any(lengths[seed.fingerprint] > baseline_max
                   for seed in result.generated.searches[0].generated)
    reuse = result.evaluation.execution_cost["reuse"]
    assert reuse["completed_runs"] >= len(baseline.stimuli), result.as_dict()
    assert reuse["preceding_calls"]
    for search in result.generated.searches:
        assert dict(search.telemetry)["provider_refusal"] is None, search.as_dict()
    # A fresh replay is an independent differential oracle, not a saved score.
    suite = (api.build_replay_suite(contract, result.generated.stimuli, baseline=baseline.stimuli)
             if runtime else None)
    fresh = handle.replay(suite.stimuli if suite is not None else result.generated.stimuli,
                          reference=reference).evaluation
    assert fresh.complete and fresh.passed
    assert fresh.hits == result.evaluation.hits
    for reused, replayed in zip(result.evaluation.results, fresh.results, strict=True):
        original, repeated = reused.trace.as_dict(), replayed.trace.as_dict()
        if runtime and kind == "rtl" and reused.stimulus in suite.continuous:
            # Final scoring intentionally skips query-only state capture. A
            # standalone replay retains it, so these two trace identities need
            # not match. Every execution/observation field still must match.
            assert reused.trace.query_boundaries is None
            assert repeated.pop("query_boundaries") is not None
        assert original == repeated
        reports = []
        for run in (reused, replayed):
            report = run.as_dict()
            for bound in (report, report["comparison"]["of"],
                          report["comparison"]["reference_source"]):
                assert bound["trace"] == run.trace.fingerprint
                bound["trace"] = "this-run"
            reports.append(report)
        assert reports[0] == reports[1]
