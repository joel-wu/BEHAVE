"""Entry/successor storage must not borrow the next step's reset controls."""


import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.stimulus_set import GenerationSpec


@pytest.mark.cosim
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("active_low", [False, True], ids=["high", "low"])
@pytest.mark.parametrize("edge", ["rising", "falling"])
def test_real_reset_pulse_before_any_clock_persists_after_release(
        tmp_path, asynchronous, active_low, edge):
    clock_edge = "posedge" if edge == "rising" else "negedge"
    reset_edge = "negedge" if active_low else "posedge"
    sensitivity = f" or {reset_edge} a_rst" if asynchronous else ""
    reset_level = int(not active_low)
    rtl = tmp_path / "pulse.sv"
    rtl.write_text(f"""module M(input a_clk, a_rst, b_clk, b_rst,
      input a_ready, b_ready, output a_valid, b_valid,
      output reg [7:0] q, seen, b_count);
      initial begin q = 5; seen = 1; b_count = 0; end
      assign a_valid = 0; assign b_valid = 0;
      always @({clock_edge} a_clk{sensitivity})
        case (a_rst)
          1'b{reset_level}: q <= 0;
          default: q <= q + 1;
        endcase
      always @({clock_edge} a_clk) seen <= q;
      always @({clock_edge} b_clk) b_count <= b_count + 1;
    endmodule""")
    contract = api.TaskContract([
        api.Endpoint(name, api.Direction.OUT,
            [api.Field(field, api.FiniteType(8), field)
             for field in (("q", "seen") if name == "a" else ("b_count",))],
            api.ready_valid(api.Direction.OUT, f"{name}_valid", f"{name}_ready"),
            clock=name, kind=api.Kind.TRANSACTION) for name in ("a", "b")],
        [api.ClockDomain(name, f"{name}_clk", f"{name}_rst",
                         reset_active_low=active_low, edge=edge) for name in ("a", "b")])
    schedule = GlobalSchedule([
        ClockTiming(name, period, phase=phase, edge=edge,
                    clock=f"{name}_clk", reset=f"{name}_rst", reset_active_low=active_low)
        for name, period, phase in (("a", 10, 10), ("b", 20, 4))], [
        GlobalEventSlot(time, event=api.EventSlot(
            ticks=ticks, resets=("a",) if reset else (),
            controls={"a_rst": int(bool(reset) != active_low), "b_rst": int(active_low),
                      "a_ready": 1, "b_ready": 1}))
        for time, ticks, reset in ((1, (), 1), (2, (), 1), (3, (), 0),
                                   (4, ("b",), 0), (10, ("a",), 0), (20, ("a",), 0))])
    stimulus = api.Stimulus({}, [slot.event for slot in schedule.slots], clock_schedule=schedule)
    assert not stimulus.faults_against(contract)
    artifact = RTLArtifact(rtl, "M", tmp_path / "run")
    manifest = artifact.frontend_catalogue(expected_identity=artifact.identity)
    assert type(manifest) is not api.Refusal, manifest
    context = RTLGenerationProvider.from_artifact(
        artifact, contract, GenerationSpec(6, 1, 0, 0), manifest,
        clock_schedule=schedule).exact_job_context()
    trace = context.source_trace(stimulus, reuse=False)

    # The async process runs once on assertion, not again while reset is held
    # or released. No clock of that process occurs in these three slots.
    # Structural goals are source control choices; arm_0 is the reset label.
    text = rtl.read_bytes()
    reset_goals = {
        goal_id for goal_id, goal in manifest.items()
        if goal.family == "control-choice" and goal.event == "arm_0"
        and text[dict(goal.provenance)["start"]:].startswith(b"case (a_rst)")
    }
    assert reset_goals
    for index, record in enumerate(trace.records):
        hits = {goal for goal, reached in record.hits if reached}
        assert bool(reset_goals & hits) == (asynchronous and index == 0)

    # Independent HDL expectations, not predictions copied out of a formula.
    # Async reset changes q without a clock; the clocked peer still holds 1.
    q = 0 if asynchronous else 5
    expected = [
        *[{"q": q, "seen": 1, "b_count": 0}] * 3,
        {"q": q, "seen": 1, "b_count": 1},
        {"q": q + 1, "seen": q, "b_count": 1},
        {"q": q + 2, "seen": q + 1, "b_count": 1}]
    assert [{name: dict(record.state)[name] for name in ("q", "seen", "b_count")}
            for record in trace.records] == expected
