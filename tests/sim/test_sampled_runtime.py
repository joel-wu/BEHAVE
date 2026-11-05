"""One periodic window drives independently sampled clocks and continuous replay."""

from dataclasses import replace

import pytest

from behave.sim import api
from behave.sim.baseline import BaselinePolicy, build_baseline
from behave.sim.core.atomic import run_atomic_in_memory
from behave.sim.core.runtime_schedule import validate_resolution
from behave.sim.core.sampled_runtime import SampledClockWindow
from behave.sim.replay_suite import build_replay_suite
from behave.sim.runtime_execution import declared_completion_targets


def sampled_contract():
    return api.TaskContract([
        api.Endpoint(f"{domain}_in", "in", [api.Field("x", api.FiniteType(8), f"{domain}_x")],
                     api.sampled_edge("in"), domain, kind=api.Kind.TRANSACTION)
        for domain in ("a", "b")
    ] + [
        api.Endpoint(f"{domain}_out", "out", [api.Field("y", api.FiniteType(8), f"{domain}_y")],
                     api.sampled_edge("out"), domain, kind=api.Kind.TRANSACTION)
        for domain in ("a", "b")
    ], [api.ClockDomain(domain, f"{domain}_clk", f"{domain}_rst") for domain in ("a", "b")])


class Echo:
    identity = "sampled-two-clock-echo"

    def atomic_step(self, event, drive):
        return {f"{domain}_y": drive[f"{domain}_x"] for domain in event.events}


@pytest.mark.parametrize("hold", [0, 1, 2, 5])
def test_unequal_sample_counts_and_continuous_runs_complete_without_idle_values(hold):
    task = sampled_contract()
    policy = replace(api.ReplayPolicy.for_contract(task), reset_hold_cycles=hold)
    baseline = build_baseline(task, policy=BaselinePolicy(case_limit=3, max_queue_depth=100),
                              execution_policy=policy)
    suite = build_replay_suite(task, baseline.stimuli)
    assert len(suite.continuous) == 1
    for request in suite.stimuli:
        expected = declared_completion_targets(task, request)
        trace = run_atomic_in_memory(task, request, Echo(), expected_outputs=expected)
        for domain in ("a", "b"):
            assert api.transfers(trace, f"{domain}_in") == list(dict(request.queues)[f"{domain}_in"])
            assert api.transfers(trace, f"{domain}_out") == api.transfers(trace, f"{domain}_in")
            assert trace.accepted_count([f"{domain}_out"]) == expected[f"{domain}_out"]
        validate_resolution(task, request, trace.stimulus, trace.records, expected)
    assert {name: len(rows) for name, rows in baseline.stimuli[0].queues} == {"a_in": 99, "b_in": 66}


def test_sampled_counts_are_not_an_inferred_response_ratio():
    task = sampled_contract()
    policy = api.ReplayPolicy.for_contract(task)
    window = SampledClockWindow.for_contract(task, policy)
    bad = api.Stimulus(queues={"a_in": [(0,)] * 100, "b_in": [(0,)] * 100}, replay_policy=policy)
    with pytest.raises(api.Refused, match="runtime-sampled-queue-window"):
        window.outputs_for(bad)


def test_native_independent_clocks_use_the_same_sampling_window(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact

    rtl = tmp_path / "echo.sv"
    rtl.write_text('''module echo(input a_clk, a_rst, b_clk, b_rst,
        input [7:0] a_x, b_x, output [7:0] a_y, b_y);
        assign a_y = a_x;
        assign b_y = b_x;
    endmodule''')
    task = sampled_contract()
    request = build_baseline(task, policy=BaselinePolicy(case_limit=1, max_queue_depth=6)).stimuli[0]
    artifact = RTLArtifact(rtl, "echo", tmp_path / "replay")
    artifact.bind_observation(task)
    expected = declared_completion_targets(task, request)
    trace = artifact.trace(task, request, expected_outputs=expected)
    for domain in ("a", "b"):
        assert api.transfers(trace, f"{domain}_out") == list(dict(request.queues)[f"{domain}_in"])
    validate_resolution(task, request, trace.stimulus, trace.records, expected)
