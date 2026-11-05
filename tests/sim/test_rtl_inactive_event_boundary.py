"""An inactive clock transition is not the following input-drive interval."""

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.rtl.native_observation import read_native_evidence

from .test_rtl_native_boundaries import _interval_meanings, _meaning, _prepare_observer, _run_driver


pytestmark = [pytest.mark.cosim, pytest.mark.eda("slang")]


@pytest.mark.parametrize("atomic,edge,count", [
    (False, "rising", 3), (True, "rising", 3),
    (False, "falling", 1), (True, "falling", 1),
])
def test_inactive_clock_read_is_not_reassigned_to_next_control(tmp_path, atomic, edge, count):
    clocks = [api.ClockDomain("a", "clk", "rst", edge=edge)]
    if atomic:
        clocks.append(api.ClockDomain("b", "bclk", "brst", edge=edge))
    contract = api.TaskContract([
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "out_y")],
                     api.sampled_edge("out"), "a", kind=api.Kind.TRANSACTION)], clocks)
    source = f"""module M(input clk, rst{', bclk, brst' if atomic else ''},
        output reg [7:0] out_y);
        always_comb if(clk) out_y=1; else out_y=0;
    endmodule"""
    slots = [api.EventSlot(controls={clock.reset: 0 for clock in clocks},
                          ticks=tuple(clock.name for clock in clocks) if atomic else ())] * count
    schedule = None
    if atomic:
        schedule = GlobalSchedule(
            tuple(ClockTiming(clock.name, 10, clock=clock.clock, reset=clock.reset, edge=edge)
                  for clock in clocks),
            tuple(GlobalEventSlot(i*10, event=slot) for i, slot in enumerate(slots)))
    stimulus = api.Stimulus(slots=slots, clock_schedule=schedule)
    prepared, catalogue = _prepare_observer(tmp_path, source)
    plain = _run_driver(tmp_path / "plain", source, contract, stimulus, prepared, native=False)
    assert [dict(row["outputs"])["out_y"] for row in plain["records"]] == [int(edge == "falling")] * count

    # The returned edge is part of the observed cycle: its real events stay in
    # that slot's advance interval and never move into the next drive.
    native = _run_driver(tmp_path / "native", source, contract, stimulus, prepared, native=True)
    assert [row["outputs"] for row in native["records"]] == [row["outputs"] for row in plain["records"]]
    evidence = native["source_evidence"]
    intervals = evidence["intervals"]
    assert [row["phase"] for row in intervals] == ["startup", *["control", "clock", "advance"] * count]
    outer = "if(clk) out_y=1; else out_y=0;"
    def branch(taken):
        return {_meaning("location", outer), _meaning("control-choice", outer, "true" if taken else "false"),
                _meaning("location", "out_y=1;" if taken else "out_y=0;")}
    inactive, active = branch(edge != "rising"), branch(edge == "rising")
    # The two-state backend starts clk at zero. For a falling-edge domain,
    # the Driver's inertial startup write then sets it high: both evaluations
    # belong to startup, neither is the first scheduled step.
    startup = branch(False) if edge == "rising" else branch(False) | branch(True)
    assert _interval_meanings(source, prepared, catalogue, intervals) == [
        startup, *[set(), active, inactive] * count]
    # Each activation enters the process, the if statement, its selected arm,
    # and the selected assignment; falling-edge startup activates it twice.
    assert intervals[0]["callbacks"] == (4 if edge == "rising" else 8)
    steps = read_native_evidence(evidence, stimulus, catalogue, expected_plan=prepared.plan)
    assert len(steps) == count
