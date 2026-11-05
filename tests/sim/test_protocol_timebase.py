"""A deadline's unit must survive clock-domain projection and reset retention."""

import itertools

import pytest

from behave.sim import api
from behave.sim.core.atomic import AtomicDriver
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.frozen import FrozenMap
from behave.sim.core.monitor import MonitorProduct, monitor_for
from behave.sim.core.protocol import adapter_program_for
from behave.sim.core.protocol_exact import AdapterExactTable

GOAL = "monitor.response-deadline"


def _run(kind, deadline, *, times=range(9), reset_at=(), keeps=False, respond=8):
    source, sink = ((api.ready_valid("in", "in_valid", "in_ready"),
                     api.ready_valid("out", "out_valid", "out_ready"))
                    if kind == "ready-valid" else
                    (api.start_done("in", "start", "done"),
                     api.start_done("out", "start", "done")))
    policy = api.ResetPolicy.KEEPS if keeps else api.ResetPolicy.CLEARS
    contract = api.TaskContract([
        api.Endpoint("in", api.Direction.IN,
                     [api.Field("a", api.FiniteType(8), "in_a")], source,
                     clock="a", kind=api.Kind.TRANSACTION,
                     reset_policy=policy, response_deadline=deadline),
        api.Endpoint("out", api.Direction.OUT,
                     [api.Field("y", api.FiniteType(8), "out_y")], sink,
                     clock="a", kind=api.Kind.TRANSACTION,
                     reset_policy=policy, answers_per_transaction=1),
    ], [api.ClockDomain("a", "a_clk", "a_rst"),
        api.ClockDomain("b", "b_clk", "b_rst")])
    slots = []
    for time in times:
        ticks = (("a",) if time % 4 == 0 else ()) + (("b",) if time % 2 else ())
        controls = {"a_rst": int(time in reset_at), "b_rst": 0}
        if kind == "ready-valid":
            controls["out_ready"] = 1
        slots.append(GlobalEventSlot(time, event=api.EventSlot(
            offers=("in",) if time == 0 else (), controls=controls,
            resets=("a",) if time in reset_at else (), ticks=ticks)))
    schedule = GlobalSchedule([
        ClockTiming("a", 4, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 2, phase=1, clock="b_clk", reset="b_rst"),
    ], slots)
    stimulus = api.Stimulus({"in": [(7,)]},
                            [slot.event for slot in slots], clock_schedule=schedule)
    driver = AtomicDriver(contract, stimulus)
    states = []
    for index, slot in enumerate(slots):
        offered = int(slot.time == respond)
        signals = {"out_y": 7, **({"in_ready": 1, "out_valid": offered}
                                  if kind == "ready-valid" else {"done": offered})}
        driver.observe(index, driver.drive(index), signals)
        states.append(dict(driver.states["a"]))
    trace = driver.trace()
    report = monitor_for(contract).check(stimulus, trace)
    return contract, stimulus, trace, report, states


@pytest.mark.parametrize("kind", ["ready-valid", "start-done"])
@pytest.mark.parametrize("deadline,expected", [(0, [4]), (3, [4]), (4, []), (8, [])])
def test_deadline_counts_scheduled_steps_not_just_domain_edges(kind, deadline, expected):
    _, _, trace, report, _ = _run(kind, deadline, respond=4)
    assert [item.slot for item in report.violations if item.goal == GOAL] == expected
    assert dict(trace.records[0].accepted)["in"] == (7,)
    assert dict(trace.records[4].accepted)["out"] == (7,)
    assert all(not record.accepted for record in trace.records[1:4])
    assert not report.assumption_violations


@pytest.mark.parametrize("kind", ["ready-valid", "start-done"])
def test_physical_time_is_not_the_deadline_unit(kind):
    # Omit control-only slots 2 and 6, retaining every induced edge. The response
    # occurs at physical time 4 but global slot 3 and is on time for deadline 3.
    _, _, _, report, _ = _run(kind, 3, times=(0, 1, 3, 4, 5, 7, 8), respond=4)
    assert not [item for item in report.violations if item.goal == GOAL]


@pytest.mark.parametrize("kind", ["ready-valid", "start-done"])
@pytest.mark.parametrize("keeps,expected", [(False, []), (True, [8])])
@pytest.mark.parametrize("deadline", [3, 7])
def test_reset_retention_does_not_pause_or_erase_the_deadline(
        kind, keeps, expected, deadline):
    _, _, _, report, _ = _run(kind, deadline, reset_at=(4,), keeps=keeps)
    assert [item.slot for item in report.violations if item.goal == GOAL] == expected


def test_projection_and_driver_use_the_same_elapsed_state_without_padding_hits():
    contract, stimulus, trace, report, states = _run("start-done", 1)
    assert states[0]["adapter.age"] == 0
    assert states[1]["adapter.age"] == 1  # elapsed, not yet reported
    assert states[4]["adapter.age"] == 2  # reported once on an active edge
    assert [item.slot for item in report.violations if item.goal == GOAL] == [4]
    assert not dict(trace.records[1].monitor).get(f"adapter.violation:{GOAL}")
    assert dict(trace.records[4].monitor)[f"adapter.violation:{GOAL}"]

    padded = api.AuditedTrace(stimulus, records=[
        *trace.records[:4], *[api.SlotRecord(monitor={"padding": 1}) for _ in range(5)]
    ], completion=api.Completion.PADDED)
    assert not [item for item in monitor_for(contract).check(
        stimulus, padded).violations if item.goal == GOAL]


@pytest.mark.parametrize("kind", ["ready-valid", "start-done"])
def test_deadline_matches_an_independent_finite_schedule_oracle(kind):
    """Expectation uses only acceptance/offer indices, not adapter state or age."""
    for times, bound, resets, keeps, response in itertools.product(
            (tuple(range(9)), (0, 1, 3, 4, 5, 7, 8)), range(9),
            ((), (4,), (4, 8)), (False, True), (None, 4, 8)):
        expected, pending, reported = [], True, False  # acceptance at slot zero
        for index, time in enumerate(times):
            if time % 4:
                continue
            if time in resets:
                if not keeps:
                    pending = False
                continue
            if pending and not reported and index > bound:
                expected.append(index)
                reported = True
            if time == response:
                pending = False
        _, _, _, report, _ = _run(
            kind, bound, times=times, reset_at=resets, keeps=keeps, respond=response)
        got = [item.slot for item in report.violations if item.goal == GOAL]
        assert got == expected, (times, bound, resets, keeps, response, got, expected)


class _Algebra:
    literal = staticmethod(lambda value: value)
    equal = staticmethod(lambda a, b: a == b)
    lor = staticmethod(lambda *values: any(values))
    ite = staticmethod(lambda condition, yes, no: yes if condition else no)


def test_elapsed_transition_is_closed_compositional_and_exact_for_all_states():
    contract, _, _, _, _ = _run("start-done", 3)
    view = MonitorProduct(contract)._components[0][1]
    program = adapter_program_for(view)
    table = AdapterExactTable(program, view)
    assert table.VERSION == "adapter-exact-control-table/7"
    assert table.as_dict()["elapsed_states"] == list(table.elapsed_states)
    for index, state in enumerate(table.states):
        valid, target = table.symbolic_elapse(index, _Algebra())
        assert valid
        assert table.state_at(target) == program.elapse(view, state)
        assert program.elapse(view, state, 0) == state
        for left, right in itertools.product((0, 1, 3, 20), repeat=2):
            assert program.elapse(view, state, left + right) == program.elapse(
                view, program.elapse(view, state, left), right)
    assert table.symbolic_elapse(-1, _Algebra()) == (False, -1)
    assert table.symbolic_elapse(len(table.states), _Algebra()) == (False, -1)
    for value in (-1, True, 0.5):
        with pytest.raises(ValueError):
            program.elapse(view, FrozenMap({"adapter.age": 0, "adapter.busy": True}), value)
