"""Request coordinates have distinct owners and immutable finite bounds."""

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.rtl.atomic_request import AtomicRequestCodec
from behave.sim.rtl.initial_request import InitialStateCoordinates
from behave.sim.rtl.task_request import ReadyValidRequestCodec


def _codec(atomic, ready):
    contract = api.TaskContract(
        clocks=(api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=1),),
        endpoints=(
            api.Endpoint("in", api.Direction.IN,
                (api.Field("a", api.FiniteType(1), "a"),),
                api.ready_valid(api.Direction.IN, "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint("out", api.Direction.OUT,
                (api.Field("y", api.FiniteType(1), "y"),),
                api.ready_valid(api.Direction.OUT, "out_valid", ready),
                clock="clk", kind=api.Kind.TRANSACTION),
        ))
    initial = InitialStateCoordinates("a" * 64, {"q": 3})
    if not atomic:
        return ReadyValidRequestCodec(contract, 2, 1, initial=initial)
    schedule = GlobalSchedule(
        (ClockTiming("clk", 1, clock="clk", reset="rst"),),
        (GlobalEventSlot(0, event=api.EventSlot(
            controls={"rst": 1, ready: 0}, ticks=("clk",), resets=("clk",))),))
    return AtomicRequestCodec(contract, 2, schedule, initial=initial)


@pytest.mark.parametrize("atomic", (False, True))
@pytest.mark.parametrize("ready", (
    "initial.rtl.q", "queue.in.length", "schedule.offer.in"))
def test_distinct_request_roles_cannot_overwrite_one_coordinate(atomic, ready):
    with pytest.raises(api.Refused) as caught:
        _codec(atomic, ready)
    assert caught.value.refusal.kind == "rtl-request-coordinate-collision"


@pytest.mark.parametrize("atomic", (False, True))
def test_request_bounds_cannot_be_mutated_through_an_exposed_interval(atomic):
    codec = _codec(atomic, "out_ready")
    assert tuple(codec.bounds["queue.in.length"]) == (0, 2)
    assert tuple(codec.bounds["initial.rtl.q"]) == (0, 7)
    with pytest.raises(TypeError):
        codec.bounds["queue.in.length"][1] = 999


def _request(codec, atomic):
    controls = {"rst": 1, "out_ready": 0}
    if atomic:
        from behave.sim.rtl.exact_product import stimulus_for_global_schedule

        stimulus = stimulus_for_global_schedule(
            codec.schedule, {}, source="request validation")
    else:
        stimulus = api.Stimulus(queues={}, slots=[api.EventSlot(
            reset=True, controls=controls)])
    return codec.encode(stimulus, initial_state={"q": 0})


@pytest.mark.parametrize("atomic", (False, True))
@pytest.mark.parametrize("coordinate,value", (
    ("out_ready", False),
    ("out_ready", 0.0),
    ("queue.in.0.a", False),
    ("queue.in.0.a", 0.0),
    ("queue.in.1.a", -1),
    ("queue.in.1.a", 2),
))
def test_every_coordinate_is_an_exact_in_range_integer_even_in_unused_cells(
        atomic, coordinate, value):
    codec = _codec(atomic, "out_ready")
    rows = _request(codec, atomic)
    assert codec.decode(rows).depth("in") == 0
    rows[0][coordinate] = value
    with pytest.raises(api.Refused):
        codec.decode(rows)


@pytest.mark.parametrize("atomic", (False, True))
def test_a_later_request_row_cannot_replace_a_fixed_integer_with_a_float(atomic):
    codec = _codec(atomic, "out_ready")
    if atomic:
        codec = AtomicRequestCodec(codec.contract, 2, GlobalSchedule(
            codec.schedule.timings, (*codec.schedule.slots,
                GlobalEventSlot(1, event=api.EventSlot(
                    controls={"rst": 0, "out_ready": 0}, ticks=("clk",))))),
            initial=codec.initial)
        rows = _request(codec, atomic)
    else:
        rows = _request(codec, atomic)
        rows.append({**rows[0], "rst": 0})
    assert codec.decode(rows).depth("in") == 0
    rows[-1]["queue.in.length"] = 0.0
    with pytest.raises(api.Refused):
        codec.decode(rows)


@pytest.mark.parametrize("atomic", (False, True))
@pytest.mark.parametrize("change", ("fractional-control", "extra-control", "reset", "queue"))
def test_encoding_checks_the_whole_stimulus_before_projecting_coordinates(atomic, change):
    codec = _codec(atomic, "out_ready")
    controls = {"rst": 1, "out_ready": 0}
    queues = {}
    if change == "fractional-control":
        controls["out_ready"] = 0.5
    elif change == "extra-control":
        controls["undeclared"] = 1
    elif change == "queue":
        queues["undeclared"] = [(1,)]
    stimulus = api.Stimulus(queues=queues, slots=[api.EventSlot(
        reset=change != "reset", controls=controls)])
    assert stimulus.faults_against(codec.contract)
    with pytest.raises(api.Refused):
        codec.encode(stimulus, initial_state={"q": 0})
