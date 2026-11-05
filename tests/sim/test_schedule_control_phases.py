"""Control changes are scheduled steps, not invented clock ticks."""

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.monitor import MonitorProduct
from behave.sim.core.stimulus import AuditedTrace, SlotRecord


@pytest.mark.parametrize("active_low", [False, True])
def test_reset_pulse_can_assert_hold_and_release_without_any_clock(active_low):
    timing = ClockTiming("a", 10, phase=10, reset="rst",
                         reset_active_low=active_low)
    slots = [GlobalEventSlot(time, event=api.EventSlot(
        controls={"rst": int(asserted != active_low)},
        resets=("a",) if asserted else (),
        ticks=("a",) if time == 10 else (),
    )) for time, asserted in ((1, True), (2, True), (3, False), (10, False))]
    schedule = GlobalSchedule([timing], slots)
    assert [schedule.reset_changes(i) for i in range(4)] == [
        (("a", True),), (), (("a", False),), ()]
    assert [slot.events for slot in schedule.slots] == [(), (), (), ("a",)]
    restored = GlobalSchedule.from_dict(schedule.as_dict())
    assert restored.digest == schedule.digest
    assert restored.reset_changes(2) == (("a", False),)
    # The prefix is semantic history: the same deasserted boundary is not an
    # assertion/release event when run without its asserted predecessor.
    suffix = GlobalSchedule([timing], slots[2:])
    assert suffix.reset_changes(0) == ()
    assert suffix.digest != schedule.digest
    for invalid in (-1, 4, True, "1"):
        with pytest.raises(IndexError):
            schedule.reset_changes(invalid)


def test_control_steps_do_not_make_a_missing_scheduled_clock_legal():
    with pytest.raises(api.Refused, match="schedule-event-set-not-induced"):
        GlobalSchedule([ClockTiming("a", 2, phase=2, reset="rst")], [
            GlobalEventSlot(1, resets=("a",)), GlobalEventSlot(3)])


@pytest.mark.parametrize("with_endpoint", [False, True])
def test_reset_restoration_counts_clock_edges_not_control_phases(with_endpoint):
    endpoints = [api.Endpoint(
        "out", api.Direction.OUT, [api.Field("y", api.FiniteType(1), "y")],
        api.ready_valid("out", "valid", "ready"),
        clock="a", kind=api.Kind.TRANSACTION)] if with_endpoint else []
    contract = api.TaskContract(endpoints, [
        api.ClockDomain("a", "a_clk", "a_rst", reset_hold_cycles=2,
                        reset_restores=True),
        api.ClockDomain("b", "b_clk", "b_rst"),
    ])
    slots, records = [], []
    state = 0
    for time in range(1, 41):
        asserted = time in (1, 2) or 4 <= time <= 8 or 13 <= time <= 20 \
            or 27 <= time <= 29 or 33 <= time <= 40
        ticks = tuple(domain for domain, period in (("a", 4), ("b", 5))
                      if time % period == 0)
        controls = {"a_rst": int(asserted), "b_rst": 0}
        if with_endpoint:
            controls["ready"] = 1
        slots.append(GlobalEventSlot(time, event=api.EventSlot(
            ticks=ticks, resets=("a",) if asserted else (), controls=controls)))
        if "a" in ticks:
            state = 0 if asserted else state + 1
        records.append(SlotRecord(state={"q": state},
                                  outputs={"y": 0, "valid": 0}))
    schedule = GlobalSchedule([
        ClockTiming("a", 4, phase=4, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 5, phase=5, clock="b_clk", reset="b_rst")], slots)
    stimulus = api.Stimulus({}, [slot.event for slot in slots],
                            clock_schedule=schedule)
    trace = AuditedTrace(stimulus, initial_state={"q": 0}, records=records)
    report = MonitorProduct(contract).check(
        stimulus, trace, reset_state_fields={"a": ("q",)})
    scenario = "monitor.reset-restoration.a.scenario"
    violation = "monitor.reset-restoration.a.violation"
    assert [(hit.goal, hit.slot, hit.endpoint) for hit in report.hits] == [
        (scenario, 19, "a"), (scenario, 39, "a")]
    assert report.opportunities[scenario] == report.opportunities[violation] == 2
    assert not report.violations
    assert not report.unresolved


@pytest.mark.parametrize("with_endpoint", [False, True])
@pytest.mark.parametrize("faulty", [False, True])
def test_reset_release_between_edges_breaks_the_hold(with_endpoint, faulty):
    """Two separate one-edge pulses are not one two-edge reset sequence."""
    endpoints = [api.Endpoint(
        "out", api.Direction.OUT, [api.Field("y", api.FiniteType(1), "y")],
        api.ready_valid("out", "valid", "ready"),
        clock="a", kind=api.Kind.TRANSACTION)] if with_endpoint else []
    contract = api.TaskContract(endpoints, [
        api.ClockDomain("a", "a_clk", "a_rst", reset_hold_cycles=2,
                        reset_restores=True),
        api.ClockDomain("b", "b_clk", "b_rst"),
    ])
    slots, records = [], []
    for time in range(21):
        # Baseline: edges 0/4. Work: edge 8. First pulse: edge 12.
        # Release at 13, reassert at 15: the next full hold ends at 20, not 16.
        asserted = time <= 4 or time == 12 or time >= 15
        ticks = tuple(domain for domain, period in (("a", 4), ("b", 5))
                      if time % period == 0)
        controls = {"a_rst": int(asserted), "b_rst": 0}
        if with_endpoint:
            controls["ready"] = 1
        slots.append(GlobalEventSlot(time, event=api.EventSlot(
            ticks=ticks, resets=("a",) if asserted else (), controls=controls)))
        records.append(SlotRecord(
            state={"q": int(faulty and time > 4)},
            outputs={"y": 0, "valid": 0}))
    schedule = GlobalSchedule([
        ClockTiming("a", 4, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 5, clock="b_clk", reset="b_rst")], slots)
    stimulus = api.Stimulus({}, [slot.event for slot in slots], clock_schedule=schedule)
    trace = AuditedTrace(stimulus, initial_state={"q": 0}, records=records)
    report = MonitorProduct(contract).check(
        stimulus, trace, reset_state_fields={"a": ("q",)})
    scenario = "monitor.reset-restoration.a.scenario"
    violation = "monitor.reset-restoration.a.violation"
    expected = [(scenario, 20)] + ([(violation, 20)] if faulty else [])
    assert [(hit.goal, hit.slot) for hit in report.hits] == expected
    assert report.opportunities[scenario] == report.opportunities[violation] == 1
    assert len(report.violations) == int(faulty)
    assert not report.unresolved
