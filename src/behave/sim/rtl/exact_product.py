"""Choose and bind the finite RTL task product without changing trace semantics.

The original single-clock product represents one request/response pair and leaves the
per-slot environment symbolic.  A contract with several endpoints needs the endpoint
product instead: it uses the explicit-clock relation over one action schedule.
Explicitly timestamped one-clock runs use that relation too. This module owns
the choice and retains the full ``GlobalSchedule`` when decoding witnesses.
Legacy implicit slots may form an edge-index schedule only for cycle semantics;
their indexes cannot supply a physical-time latency goal.
"""

from __future__ import annotations

from ..core.clock_schedule import (
    ClockTiming,
    GlobalEventSlot,
    GlobalSchedule,
    unique_global_schedule,
)
from ..core.refusal import Refusal, Refused
from ..core.stimulus import EventSlot, Stimulus

__all__ = [
    "global_schedule_for_stimuli",
    "needs_global_schedule_product",
    "schedule_matches_stimulus",
    "stimulus_for_global_schedule",
]


def needs_global_schedule_product(contract, clock_schedule=None):
    """Whether ``contract`` needs the fixed-schedule protocol product.

    The older one-pair relation is not selected merely from endpoint counts.  Its
    adapter must explicitly implement that symbolic-slot encoding.  Other registered
    finite adapters use the general protocol table over the concrete run's authenticated
    schedule, even when their topology happens to be one input and one output.
    """

    from ..core.protocol import adapter_program_for

    if (len(contract.clocks) != 1 or clock_schedule is not None
            or contract.clocks[0].edge != "rising"):
        # Each domain is bound independently by the atomic product. Do not flatten
        # explicit instances into one adapter merely to choose the schedule form.
        return True
    program = adapter_program_for(contract)
    return (len(contract.inputs()) != 1
            or len(contract.outputs()) != 1
            or not program.exact_symbolic_schedule_product)


def _single_clock_schedule(contract, stimulus):
    if len(contract.clocks) != 1:
        raise ValueError("a one-domain schedule needs exactly one clock")
    if type(stimulus) is not Stimulus:
        raise TypeError("an exact schedule is derived from an exact Stimulus")
    faults = stimulus.faults_against(contract)
    if faults:
        raise Refused(Refusal(
            "rtl-exact-schedule-stimulus-outside-contract",
            "the exact action schedule cannot be derived from a stimulus outside "
            "the admitted task",
            first=faults[0], count=len(faults)))
    clock = contract.clocks[0]
    timing = ClockTiming(
        clock.name, 1, clock=clock.clock, reset=clock.reset,
        reset_active_low=clock.reset_active_low, edge=clock.edge)
    slots = []
    for index, slot in enumerate(stimulus.slots):
        ticks = set(slot.ticks)
        if ticks and ticks != {clock.name}:
            raise Refused(Refusal(
                "rtl-exact-single-clock-tick-mismatch",
                "a one-clock stimulus can only name its sole active domain",
                slot=index, ticks=sorted(ticks), domain=clock.name))
        resets = set(slot.resets)
        if resets and resets != {clock.name}:
            raise Refused(Refusal(
                "rtl-exact-single-clock-reset-mismatch",
                "a one-clock stimulus can only reset its sole clock domain",
                slot=index, resets=sorted(resets), domain=clock.name))
        reset = bool(slot.reset or clock.name in resets)
        event = EventSlot(
            offers=slot.offers, controls=dict(slot.controls),
            ticks=(clock.name,), resets=(clock.name,) if reset else ())
        slots.append(GlobalEventSlot(index, event=event))
    return GlobalSchedule((timing,), slots)


def _schedule_of(contract, stimulus):
    if not stimulus.slots:
        raise Refused(Refusal(
            "rtl-exact-action-schedule-unbound",
            "an execution-driven replay request does not yet contain a finite "
            "action schedule for bounded analysis"))
    if stimulus.clock_schedule is not None:
        faults = stimulus.faults_against(contract)
        if faults:
            raise Refused(Refusal(
                "rtl-exact-schedule-stimulus-outside-contract",
                "the explicit action schedule is outside the admitted task",
                first=faults[0], count=len(faults)))
        return stimulus.clock_schedule
    if len(contract.clocks) == 1:
        if any(rule.timebase.kind == "global-time" for rule in contract.latency_goals):
            raise Refused(Refusal(
                "rtl-exact-global-time-unbound",
                "global-time latency needs the concrete run's timestamped schedule; "
                "slot indexes are not physical time"))
        return _single_clock_schedule(contract, stimulus)
    raise Refused(Refusal(
        "rtl-atomic-exact-schedule-unbound",
        "a multi-clock exact product needs each concrete stimulus's canonical "
        "GlobalSchedule"))


def global_schedule_for_stimuli(contract, stimuli, *, horizon=None):
    """Return the one action schedule shared by ``stimuli``, or refuse honestly."""

    stimuli = tuple(stimuli)
    if any(type(stimulus) is not Stimulus for stimulus in stimuli):
        raise TypeError("an exact schedule pool contains only Stimulus records")
    schedules = [_schedule_of(contract, stimulus) for stimulus in stimuli]
    return unique_global_schedule(schedules, horizon=horizon)


def schedule_matches_stimulus(contract, schedule, stimulus):
    """Whether ``stimulus`` has exactly the action semantics of ``schedule``."""

    if type(schedule) is not GlobalSchedule or type(stimulus) is not Stimulus:
        return False
    try:
        got = _schedule_of(contract, stimulus)
    except (Refused, TypeError, ValueError):
        return False
    return got.digest == schedule.digest and got.as_dict() == schedule.as_dict()


def stimulus_for_global_schedule(
        schedule, queues, *, source, memory_environment=None):
    """Carry schedule facts without dropping time or requiring unbound run inputs.

    Admission belongs to the caller: context construction binds memory later,
    while request decoding and replay require the complete environment now.
    """

    if type(schedule) is not GlobalSchedule:
        raise TypeError("an exact task stimulus needs an exact GlobalSchedule")
    return Stimulus(
        queues=queues, slots=[slot.event for slot in schedule.slots],
        source=source, clock_schedule=schedule,
        memory_environment=memory_environment)
