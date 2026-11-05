"""A late state-dependent mismatch must reach both public artifact scores."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from tests.sim.test_command_handler import _BaselineOnly

REFERENCE = """class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        self.n += 1
        return {"y": a}
"""

RTL = """module M(input wire clk, rst, in_valid, out_ready,
    input wire [7:0] a, output wire in_ready, out_valid,
    output wire [7:0] y);
    reg [7:0] n;
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign y = VALUE;
    always @(posedge clk) begin
        if (rst) n <= 0;
        else if (in_valid && in_ready) n <= n + 1;
    end
endmodule
"""


def _contract():
    return api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(8), "a")],
                     api.ready_valid("in", "in_valid", "in_ready"), clock="clk",
                     kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "y")],
                     api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
                     kind=api.Kind.TRANSACTION, answers_per_transaction=1)],
        [api.ClockDomain("clk", "clk", "rst")])


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
@pytest.mark.parametrize("mutant", (False, True))
@pytest.mark.parametrize("runtime", (False, True))
def test_default_baseline_replays_long_sequences_and_detects_late_mismatch(tmp_path, kind, mutant, runtime):
    c = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    spec = api.GenerationSpec(220, 128, 2, 4, stall_every=3)
    if kind == "behavior":
        source = REFERENCE.replace('return {"y": a}',
            'return {"y": a ^ (1 if self.n >= 65 else 0)}') if mutant else REFERENCE
        path = tmp_path / "candidate.py"
        path.write_text(source)
        task = api.admit(c, api.realization_of_behavior_source(source, c, file="candidate.py"),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(
            path, file="candidate.py", timeout_s=60), spec, bounded_analysis=False)
    else:
        source = RTL.replace("VALUE", "a ^ (n >= 64 ? 8'd1 : 8'd0)" if mutant else "a")
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        task = api.admit(c, api.realization_of_rtl_source(
            source, "late mismatch", top="M", contract=c), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(
            path, "M", tmp_path / "rtl", timeout_s=120), spec)
    baseline = api.build_baseline(c) if runtime else api.BaselinePolicy()
    request = handle.command_request(
        run_directory=tmp_path / "command", baseline=baseline,
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=False, reference=reference)
    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative
    assert result.decision.passed is not mutant
    if mutant:
        assert not result.evaluation.complete and result.evaluation.unexecuted
        assert result.evaluation.failure_witness is not None
        witness = result.evaluation.results[result.evaluation.failure_witness]
        assert witness.trace.accepted_count(["in"]) >= 65
        assert all(row["runs"] == 0 for row in result.evaluation.execution_cost["phases"]
                   if row["phase"] == "continuous")
    else:
        assert result.evaluation.complete, result.evaluation.as_dict()
    assert result.evaluation.passed is not mutant, result.evaluation.as_dict()
    assert bool(result.evaluation.mismatch_count) is mutant
    assert result.evaluation.results
    isolated_count = len(result.generated.stimuli)
    for run in result.evaluation.results[:isolated_count]:
        assert run.stimulus.depth("in") == 100
        if run.trace.completion is api.Completion.CHECKPOINT:
            # Other parallel workers may stop before the failing run's 65th
            # input; their retained prefix is progress, not the negative witness.
            assert mutant and 0 < run.trace.accepted_count(["in"]) <= 100
        else:
            assert run.trace.accepted_count(["in"]) == 100
        if runtime:
            assert run.stimulus.slots == ()
            assert run.trace.request.fingerprint == run.stimulus.fingerprint
            assert len(run.trace.records) == len(run.trace.stimulus.slots)
    if runtime and not mutant:
        assert len(result.evaluation.results) == isolated_count + 1
        stream = result.evaluation.results[-1]
        assert stream.stimulus.depth("in") == 100 * isolated_count
        assert stream.trace.accepted_count(["in"]) == 100 * isolated_count
        assert stream.stimulus.replay_policy.drain_slots == 200
        assert stream.stimulus.replay_policy.stall_every == 0
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["passed"] is not mutant


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
def test_public_score_includes_fault_seen_only_between_sequences(tmp_path, kind):
    c = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    spec = api.GenerationSpec(220, 128, 2, 4)
    if kind == "behavior":
        source = REFERENCE.replace('return {"y": a}',
                                   'return {"y": a ^ (1 if self.n > 100 else 0)}')
        path = tmp_path / "candidate.py"
        path.write_text(source)
        task = api.admit(c, api.realization_of_behavior_source(source, c, file="candidate.py"),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(
            path, file="candidate.py", timeout_s=60), spec, bounded_analysis=False)
    else:
        source = RTL.replace("VALUE", "a ^ (n >= 100 ? 8'd1 : 8'd0)")
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        task = api.admit(c, api.realization_of_rtl_source(
            source, "cross-sequence fault", top="M", contract=c), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(
            path, "M", tmp_path / "rtl", timeout_s=120), spec)
    # Three recipes -> two distinct sequences. Both isolated 100-input runs
    # pass; only the uninterrupted 200-input workload can expose this mutant.
    baseline = api.build_baseline(c, policy=api.BaselinePolicy(case_limit=3))
    assert len(baseline.stimuli) == 2
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", baseline=baseline,
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=False, reference=reference))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and not result.decision.passed
    assert result.evaluation.failure_witness == 2
    assert len(result.evaluation.results) == 3
    assert all(not run.faults for run in result.evaluation.results[:2])
    assert result.evaluation.results[-1].faults
    stream = result.evaluation.results[-1]
    assert stream.stimulus.depth("in") == 200
    assert 100 < stream.trace.accepted_count(["in"]) <= 200
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["passed"] is False

    # Explicit replay uses the same suite and authority; no generation/analysis
    # or second scoring rule is needed by PPA's frozen-workload caller.
    authority = handle.replay(api.build_replay_suite(c, baseline.stimuli), reference=reference)
    assert authority.evaluation.complete and not authority.evaluation.passed
