"""Production serial/parallel scores retain the same independent run evidence."""

import pytest

from behave.sim import api
from tests.sim.test_baseline_public_sequence import _contract

REFERENCE = """COUNT = 0
class M:
    def reset(self):
        pass
    def process(self, a):
        global COUNT
        COUNT += 1
        return {"y": (a + COUNT) & 255}
"""

BEHAVIOR = """class M:
    def reset(self):
        self.n = 0
    def process(self, a):
        self.n += 1
        return {"y": (a + self.n + ERROR) & 255}
"""

RTL = """module M(input wire clk, rst, in_valid, out_ready,
    input wire [7:0] a, output wire in_ready, out_valid, output wire [7:0] y);
    reg [7:0] n;
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign y = a + n + 8'd1 + ERROR;
    always @(posedge clk) begin
        if (rst) n <= 0;
        else if (in_valid && in_ready) n <= n + 1;
    end
endmodule
"""


def _semantic(evaluation):
    image = evaluation.as_dict()
    # Timing/tool lineage are telemetry. Full per-run trace identities,
    # comparison/failure records and hit/opportunity evidence remain compared.
    telemetry = dict(image.pop("telemetry", ()))
    for key in ("concrete_execution", "rtl_toolchain"):
        telemetry.pop(key, None)
    return image, telemetry


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
@pytest.mark.parametrize("mutant", (False, True))
def test_parallel_public_replay_matches_serial_and_resets_global_reference(
        tmp_path, monkeypatch, kind, mutant):
    c = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    spec = api.GenerationSpec(16, 8, 2, 2)
    if kind == "behavior":
        source = BEHAVIOR.replace("ERROR", "1" if mutant else "0")
        path = tmp_path / "candidate.py"
        path.write_text(source)
        realization = api.realization_of_behavior_source(source, c, file="candidate.py")
        task = api.admit(c, realization, reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(
            path, file="candidate.py", timeout_s=60), spec, bounded_analysis=False)
    else:
        source = RTL.replace("ERROR", "8'd1" if mutant else "8'd0")
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        realization = api.realization_of_rtl_source(source, "parallel replay", top="M", contract=c)
        task = api.admit(c, realization, reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(
            path, "M", tmp_path / "rtl", timeout_s=120), spec)
    stimuli = api.build_baseline(c, spec, policy=api.BaselinePolicy(
        case_limit=4, max_queue_depth=3), realization=realization).stimuli
    evaluations = []
    for workers in (1, 3):
        monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", str(workers))
        authority = handle.replay(stimuli, reference=reference)
        assert authority.authoritative, authority.as_summary_dict()
        result = authority.evaluation
        assert result.complete and authority.passed is not mutant, result.as_dict()
        assert [run.stimulus.fingerprint for run in result.results] == [s.fingerprint for s in stimuli]
        execution = result.execution_cost
        assert execution["workers"] == workers
        assert len(execution["run_costs"]) == len(stimuli)
        assert all(row["comparison"] > 0 for row in execution["run_costs"])
        assert execution["wall_seconds"] > 0
        execution["run_costs"][0]["comparison"] = -1
        assert result.execution_cost["run_costs"][0]["comparison"] > 0
        evaluations.append(result)
    assert _semantic(evaluations[0]) == _semantic(evaluations[1])


@pytest.mark.isolation
@pytest.mark.parametrize("failure_side", ("candidate", "reference", "reference-with-mismatch"))
def test_parallel_public_replay_preserves_partial_hits_and_failure_origin(
        tmp_path, monkeypatch, failure_side):
    c = _contract()
    straight = """class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": a}
"""
    # Keep a pure reference failure separate from a reference failure plus a
    # mismatch in another run. A known reference defect prevents a decision in
    # either case; do not silently replace no-result with candidate zero.
    expression = '1 // a' if failure_side == "reference-with-mismatch" else 'a + (0 // a)'
    partial = straight.replace('"y": a', f'"y": {expression}')
    source = partial if failure_side == "candidate" else straight
    reference_source = partial if failure_side != "candidate" else straight
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()}, config={"class": "M"})
    path = tmp_path / "candidate.py"
    path.write_text(source)
    realization = api.realization_of_behavior_source(source, c, file="candidate.py")
    task = api.admit(c, realization, reference=reference)
    spec = api.GenerationSpec(8, 2, 0, 1)
    handle = api.prepare_behavior(task, api.BehaviorConfig(
        path, file="candidate.py", timeout_s=60), spec, bounded_analysis=False)
    # Some runs fail at the first input, others complete; every member remains.
    stimuli = api.build_baseline(c, spec, policy=api.BaselinePolicy(
        case_limit=4, max_queue_depth=1), realization=realization).stimuli
    evaluations = []
    for workers in (1, 3):
        monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", str(workers))
        authority = handle.replay(stimuli, reference=reference)
        result = authority.evaluation
        assert result is not None and len(result.results) == len(stimuli)
        assert all(run.trace is not None for run in result.results)
        if failure_side == "candidate":
            assert authority.authoritative and not authority.passed
            assert result.safety_hits
            assert any(run.trace.completion is api.Completion.PADDED for run in result.results)
        else:
            assert not authority.authoritative, authority.as_summary_dict()
            assert result.failure_witness is None
            if failure_side == "reference-with-mismatch":
                assert result.mismatches
            else:
                assert not result.mismatches
            failures = [run.comparison_failure for run in result.results
                        if run.comparison_failure is not None]
            assert failures and all(failure.origin is api.FailureOrigin.REFERENCE
                                    for failure in failures)
        evaluations.append(result)
    assert _semantic(evaluations[0]) == _semantic(evaluations[1])


@pytest.mark.isolation
@pytest.mark.parametrize("missing_write", (False, True))
def test_parallel_public_replay_compares_each_runs_memory_result(
        tmp_path, monkeypatch, missing_write):
    from tests.sim.test_memory_result_score import SOURCE, setup, trace_for

    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    template, _ = setup(reference)
    source = SOURCE.replace("self.ram[1] = in_a", "pass") if missing_write else SOURCE
    path = tmp_path / "candidate.py"
    path.write_text(source)
    realization = api.realization_of_behavior_source(source, template.contract, file="candidate.py")
    task = api.admit(template.contract, realization, reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(
        path, file="candidate.py", timeout_s=60), api.GenerationSpec(2, 1, 0, 1),
        bounded_analysis=False)
    stimuli = tuple(trace_for(template, {}, input_value=value).stimulus for value in (5, 9, 42))
    evaluations = []
    for workers in (1, 3):
        monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", str(workers))
        authority = handle.replay(stimuli, reference=reference)
        result = authority.evaluation
        assert authority.authoritative and result.complete, authority.as_summary_dict()
        assert authority.passed is not missing_write
        assert result.mismatch_count == 0
        assert result.memory_mismatch_count == (len(stimuli) if missing_write else 0)
        assert all("memory-result" in dict(run.trace.records[-1].environment)
                   for run in result.results)
        evaluations.append(result)
    assert _semantic(evaluations[0]) == _semantic(evaluations[1])
