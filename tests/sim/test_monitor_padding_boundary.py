"""A terminated run has no further protocol or reset-monitor transitions."""

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.monitor import Monitor, MonitorProduct
from behave.sim.core.stimulus import AuditedTrace, Completion, SlotRecord


@pytest.mark.parametrize("multiclock", [False, True])
@pytest.mark.parametrize("short", [False, True])
def test_padding_retains_observed_violation_but_cannot_complete_a_reset(multiclock, short):
    contract = api.TaskContract([api.Endpoint(
        "out", api.Direction.OUT, [api.Field("y", api.FiniteType(8), "y")],
        api.ready_valid("out", "valid", "ready"),
        clock="a", kind=api.Kind.TRANSACTION)], [
        api.ClockDomain("a", "a_clk", "a_rst", reset_hold_cycles=1,
                        reset_restores=True),
        *([api.ClockDomain("b", "b_clk", "b_rst")] if multiclock else []),
    ])
    slots, records = [], []
    count = 9 if multiclock else 5
    first, second, cutoff = (2, 4, 5) if multiclock else (1, 2, 3)
    for index in range(count):
        reset = index == 0 or index >= cutoff
        controls = {"a_rst": int(reset), "ready": 0}
        if multiclock:
            controls["b_rst"] = 0
        slots.append(api.EventSlot(
            controls=controls, reset=reset and not multiclock,
            ticks=(("a",) if index % 2 == 0 else ("b",)) if multiclock else (),
            resets=("a",) if reset and multiclock else ()))
        records.append(SlotRecord(
            outputs={"valid": int(index == first), "y": 7},
            state={"q": int(index >= first)}) if index < cutoff
            else SlotRecord(monitor={"padding": 1}))
    schedule = GlobalSchedule([
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 2, phase=1, clock="b_clk", reset="b_rst")],
        [GlobalEventSlot(i, event=slot) for i, slot in enumerate(slots)]
    ) if multiclock else None
    stimulus = api.Stimulus({}, slots, clock_schedule=schedule)
    trace = AuditedTrace(stimulus, initial_state={"q": 0},
                         records=records[:cutoff] if short else records,
                         completion=Completion.INCOMPLETE if short else Completion.PADDED)
    assert trace.observed_count == cutoff
    assert AuditedTrace.from_dict(trace.as_dict()).observed_count == cutoff
    monitor = MonitorProduct(contract) if multiclock else Monitor(contract)
    if multiclock:
        domain, projected_contract, _monitor = monitor._components[0]
        projected, projected_trace, indexes = monitor._project(
            domain, projected_contract, stimulus, trace)
        assert indexes == (0, 2, 4, 6, 8)
        assert len(projected.slots) == 5
        assert len(projected_trace.records) == (3 if short else 5)
    report = monitor.check(stimulus, trace,
                           reset_state_fields={"a": ("q",)} if multiclock else ("q",))
    assert [(hit.goal, hit.slot) for hit in report.hits] == [
        ("monitor.sink-stability", second)]
    assert not any("reset-restoration" in item.goal for item in report.unresolved)
    assert report.opportunities.get("monitor.reset-restoration.a.scenario", 0) == 0
    assert report.opportunities.get("monitor.reset-restoration.a.violation", 0) == 0
