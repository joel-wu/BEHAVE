"""Structural hits describe the event executing the source, not an idle clock."""

import pytest

pytest.importorskip("z3", reason="exact event sampling requires the solver lane")
pytestmark = [pytest.mark.solver, pytest.mark.cosim]

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.stimulus_set import GenerationSpec


def _context(tmp_path, edge, body, *, paired=False):
    path = tmp_path / "clock_read.sv"
    extra_ports = ", input other_clk, other_rst" if paired else ""
    path.write_text(f"""module M(input clk, rst, ready, output valid,
                               output reg [7:0] q = 0{extra_ports});
        assign valid = 0;
        {body}
    endmodule
    """)
    clocks = [api.ClockDomain("main", "clk", "rst", edge=edge)]
    timings = [ClockTiming("main", 1, clock="clk", reset="rst", edge=edge)]
    if paired:
        clocks.append(api.ClockDomain("other", "other_clk", "other_rst", edge="falling"))
        timings.append(ClockTiming("other", 1, clock="other_clk", reset="other_rst", edge="falling"))
    contract = api.TaskContract([
        api.Endpoint("out", api.Direction.OUT,
            [api.Field("q", api.FiniteType(8), "q")],
            api.ready_valid(api.Direction.OUT, "valid", "ready"),
            clock="main", kind=api.Kind.TRANSACTION)],
        clocks)
    schedule = GlobalSchedule(timings, [
        GlobalEventSlot(time, event=api.EventSlot(
            ticks=tuple(clock.name for clock in clocks),
            controls={**{clock.reset: 0 for clock in clocks}, "ready": 1}))
        for time in (0, 1)])
    stimulus = api.Stimulus({}, [slot.event for slot in schedule.slots],
                            clock_schedule=schedule)
    artifact = RTLArtifact(path, "M", tmp_path / "run")
    manifest = artifact.frontend_catalogue(expected_identity=artifact.identity)
    assert type(manifest) is not api.Refusal, manifest
    # Match public admission order. Native replay must not install an observer
    # after the exact context has already frozen a different observation identity.
    assert artifact.bind_observation(contract).native
    context = RTLGenerationProvider.from_artifact(
        artifact, contract, GenerationSpec(2, 1, 0, 0), manifest,
        clock_schedule=schedule).exact_job_context()
    return context, stimulus


def test_coincident_clock_used_as_data_keeps_native_replay_but_refuses_exact(tmp_path):
    context, stimulus = _context(tmp_path, "rising", """
        always @(posedge clk)
            case ({clk, other_clk})
                2'b10: q <= q + 5;
                default: q <= q + 9;
            endcase
    """, paired=True)
    trace = context.artifact.exact_trace(
        context.contract, stimulus, observe_names=("q",), state_names=("q",))
    assert [dict(trace.initial_state)["q"], *[dict(row.state)["q"] for row in trace.records]] == [0, 5, 10]


@pytest.mark.parametrize("body", [
    "always @(posedge clk) q <= q + other_clk;",
    "wire [7:0] operand = {7'b0, other_clk}; always @(posedge clk) q <= q + operand;",
])
def test_data_only_clock_read_is_not_a_certified_exact_transition(tmp_path, body):
    context, stimulus = _context(tmp_path, "rising", body, paired=True)
    trace = context.artifact.exact_trace(
        context.contract, stimulus, observe_names=("q",), state_names=("q",))
    assert [dict(trace.initial_state)["q"], *[dict(row.state)["q"] for row in trace.records]] == [0, 0, 0]
