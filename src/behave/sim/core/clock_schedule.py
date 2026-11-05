"""Canonical global event schedules for several clock domains.

A clock period is not a sequence of per-domain actions.  It induces one ordered
sequence of *global* instants, and every domain whose active edge occurs at an
instant belongs to the same event slot.  Keeping that coincidence set intact is
what prevents a replay from inventing an ordering between simultaneous edges.

The records in this module contain only task/environment facts.  What a model or
RTL artifact did at a slot belongs in an audited trace, not in the schedule.
"""

from __future__ import annotations

import hashlib
import heapq
from itertools import islice

from .frozen import Frozen
from .refusal import Refusal, Refused
from .stimulus import EventSlot, canonical_json

__all__ = [
    "ClockTiming",
    "GlobalEventSlot",
    "GlobalSchedule",
    "unique_global_schedule",
    "validate_external_bindings",
]


def unique_global_schedule(schedules, *, horizon=None):
    """Return the one canonical schedule shared by ``schedules``.

    Mapping a backend's witness or stimulus to a schedule remains at that
    backend boundary.  The identity decision itself is common: an exact product
    is relative to one nonempty schedule, never a union of timing domains.
    """

    schedules = tuple(schedules)
    if any(type(schedule) is not GlobalSchedule for schedule in schedules):
        raise TypeError("an exact schedule pool contains GlobalSchedule records")
    if not schedules:
        raise Refused(Refusal(
            "exact-action-schedule-missing",
            "a schedule-bound exact product needs a nonempty schedule set"))
    first = schedules[0]
    if horizon is not None and len(first.slots) != horizon:
        raise Refused(Refusal(
            "exact-action-schedule-horizon-mismatch",
            "the concrete action schedule and bounded-analysis horizon differ",
            schedule_slots=len(first.slots), horizon=horizon))
    for schedule in schedules[1:]:
        if schedule.digest != first.digest or schedule.as_dict() != first.as_dict():
            raise Refused(Refusal(
                "exact-action-schedule-not-unique",
                "the concrete evaluation contains several environment schedules; "
                "one fixed-schedule exact instance cannot classify their union",
                schedules=sorted({item.digest for item in schedules})))
    return first


def validate_external_bindings(timings, primary_inputs):
    """Require every scheduled clock/reset to be an externally driven input.

    A generated clock needs a separately declared transition policy.  Treating
    it as an independent testbench input would let replay choose events the RTL
    itself was meant to produce, so the base event-slot adapter refuses it.
    Likewise, two domain names cannot silently share one clock binding; they are
    one event domain unless a higher-level adapter defines their joint handler.
    """
    primary_inputs = set(primary_inputs)
    by_clock = {}
    for timing in timings:
        if not isinstance(timing, ClockTiming):
            raise TypeError("clock bindings must be ClockTiming records")
        if timing.clock not in primary_inputs:
            raise Refused(Refusal(
                "schedule-clock-not-external",
                "a scheduled clock is not an externally driven primary input; "
                "generated clocks need an explicit event policy",
                domain=timing.name, clock=timing.clock))
        by_clock.setdefault(timing.clock, []).append(timing.name)
        if timing.reset is not None and timing.reset not in primary_inputs:
            raise Refused(Refusal(
                "schedule-reset-not-external",
                "a scheduled reset is not an externally driven primary input",
                domain=timing.name, reset=timing.reset))
    shared = {clock: domains for clock, domains in by_clock.items()
              if len(domains) > 1}
    if shared:
        raise Refused(Refusal(
            "schedule-clock-binding-shared",
            "several declared domains share one clock input; bind their "
            "endpoints to one domain or declare a joint event handler",
            shared=shared))


class ClockTiming(Frozen):
    """The externally declared timing and reset convention of one domain."""

    __slots__ = (
        "clock",
        "edge",
        "name",
        "period",
        "phase",
        "reset",
        "reset_active_low",
    )

    def __init__(self, name, period, *, phase=0, edge="rising", clock=None,
                 reset=None, reset_active_low=False):
        if type(name) is not str or not name:
            raise ValueError("a clock domain needs a nonempty string name")
        if type(period) is not int or period < 1:
            raise ValueError("a clock period must be a positive integer")
        if type(phase) is not int or phase < 0:
            raise ValueError("a clock phase/first-edge offset must be nonnegative")
        if edge not in ("rising", "falling"):
            raise ValueError("a clock edge is either 'rising' or 'falling'")
        if clock is not None and (type(clock) is not str or not clock):
            raise ValueError("a clock signal is a nonempty string or absent")
        if reset is not None and (type(reset) is not str or not reset):
            raise ValueError("a reset signal is a nonempty string or absent")
        if type(reset_active_low) is not bool:
            raise ValueError("reset_active_low must be a Boolean")
        self.name = name
        self.period = period
        self.phase = phase
        self.edge = edge
        self.clock = name + "_clk" if clock is None else clock
        self.reset = reset
        self.reset_active_low = reset_active_low
        self._freeze()

    def as_dict(self):
        return {
            "name": self.name,
            "period": self.period,
            "phase": self.phase,
            "edge": self.edge,
            "clock": self.clock,
            "reset": self.reset,
            "reset_active_low": self.reset_active_low,
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise Refused(Refusal(
                "schedule-clock-record-malformed",
                "a clock timing record must be a mapping"))
        required = {"name", "period", "phase", "edge", "clock", "reset",
                    "reset_active_low"}
        if set(value) != required:
            raise Refused(Refusal(
                "schedule-clock-record-malformed",
                "a clock timing record must contain exactly the versioned fields",
                missing=sorted(required - set(value)),
                unknown=sorted(set(value) - required)))
        try:
            return cls(
                value["name"], value["period"], phase=value["phase"],
                edge=value["edge"], clock=value["clock"],
                reset=value["reset"],
                reset_active_low=value["reset_active_low"],
            )
        except (TypeError, ValueError) as exc:
            raise Refused(Refusal(
                "schedule-clock-record-malformed",
                "a clock timing record contains an invalid field value",
                error=str(exc))) from None


class GlobalEventSlot(Frozen):
    """A timestamped view of T3-01's canonical ``EventSlot``.

    The event itself is not a second slot format: ``event`` is the same slot
    carried by ``Stimulus`` (offers, environment controls, ticks and per-domain
    resets).  This wrapper adds only the physical timestamp fixed by the clock
    schedule.  ``events`` remains a readable alias for ``event.ticks``.
    Control-only steps need no clock tick; ``resets`` records asserted levels,
    whose changes are recovered by ``GlobalSchedule.reset_changes``.
    """

    __slots__ = ("event", "time")

    def __init__(self, time, events=(), resets=(), *, event=None):
        if type(time) is not int or time < 0:
            raise ValueError("a global event time must be a nonnegative integer")
        if event is not None and (events or resets):
            raise ValueError(
                "pass either a canonical event slot or ticks/resets, not both")
        if event is None:
            event = EventSlot(ticks=events, resets=resets)
        if not isinstance(event, EventSlot):
            raise TypeError("a scheduled event must be a core EventSlot")
        if event.reset:
            raise Refused(Refusal(
                "schedule-reset-domain-ambiguous",
                "a multi-clock schedule must name each asserted reset domain; "
                "the single-domain reset shorthand has no unambiguous target"))
        self.time = time
        self.event = event
        # A scheduled step can contain only a control change.  In particular,
        # releasing the last reset has no asserted reset and no clock edge.
        # Requiring either here would force callers to invent a clock event.
        self._freeze()

    @property
    def events(self):
        return self.event.ticks

    @property
    def resets(self):
        """Asserted reset levels, not the resets that changed in this step."""
        return self.event.resets

    def as_dict(self):
        return {"time": self.time, **self.event.as_dict()}

    @classmethod
    def from_dict(cls, value, *, allow_actions=False):
        if type(value) is not dict:
            raise Refused(Refusal(
                "schedule-slot-record-malformed",
                "a timestamped event slot must be a mapping"))
        required = {"time", "offers", "controls", "reset", "ticks"}
        optional = {"resets"}
        if allow_actions:
            optional.add("actions")
        missing = required - set(value)
        unknown = set(value) - required - optional
        if missing or unknown:
            raise Refused(Refusal(
                "schedule-slot-record-malformed",
                "a timestamped event slot must contain exactly the versioned "
                "canonical EventSlot fields",
                missing=sorted(missing), unknown=sorted(unknown)))
        for field in ("offers", "ticks", "resets"):
            items = value.get(field, [])
            if (type(items) is not list
                    or any(type(item) is not str or not item for item in items)
                    or items != sorted(set(items))):
                raise Refused(Refusal(
                    "schedule-slot-record-malformed",
                    "slot endpoint, tick and reset sets use sorted unique string "
                    "lists",
                    field=field))
        controls = value["controls"]
        if (type(controls) is not list
                or any(type(pair) is not list or len(pair) != 2
                       or type(pair[0]) is not str or not pair[0]
                       for pair in controls)
                or [pair[0] for pair in controls]
                   != sorted({pair[0] for pair in controls})):
            raise Refused(Refusal(
                "schedule-slot-record-malformed",
                "slot controls use sorted unique [name, value] pairs"))
        if type(value["time"]) is not int or value["time"] < 0:
            raise Refused(Refusal(
                "schedule-slot-record-malformed",
                "a slot timestamp must be a nonnegative integer"))
        if type(value["reset"]) is not bool:
            raise Refused(Refusal(
                "schedule-slot-record-malformed",
                "the single-domain reset shorthand must be Boolean"))
        try:
            event_record = {
                field: value[field]
                for field in ("offers", "controls", "reset", "ticks", "resets")
                if field in value
            }
            return cls(value["time"], event=EventSlot.from_dict(event_record))
        except Refused:
            raise
        except (TypeError, ValueError) as exc:
            raise Refused(Refusal(
                "schedule-slot-record-malformed",
                "a timestamped event slot contains an invalid field value",
                error=str(exc))) from None


def periodic_slots(timings, *, reset_edges=None):
    """Yield global active-edge instants, grouping coincident edges atomically.

    Reset holds count each domain's own edges. The final reset edge still sees
    reset; the following global instant sees that domain released.
    """
    timings = tuple(timings)
    names = {timing.name for timing in timings}
    if not timings or len(names) != len(timings):
        raise ValueError("clock timings need nonempty, distinct domains")
    reset_edges = dict(reset_edges or {})
    if set(reset_edges) - names:
        raise Refused(Refusal("schedule-reset-unknown-domain",
                              "a reset prefix names an absent clock domain"))
    if any(type(count) is not int or count < 0 for count in reset_edges.values()):
        raise ValueError("reset edge counts must be nonnegative integers")
    heap = [(timing.phase, timing.name, timing.period) for timing in timings]
    heapq.heapify(heap)
    fired = dict.fromkeys(names, 0)
    while True:
        now = heap[0][0]
        events = []
        resets = [name for name in names if fired[name] < reset_edges.get(name, 0)]
        while heap[0][0] == now:
            _, name, period = heapq.heappop(heap)
            events.append(name)
            fired[name] += 1
            heapq.heappush(heap, (now + period, name, period))
        yield GlobalEventSlot(now, events, resets)


class GlobalSchedule(Frozen):
    """An immutable clock schedule with a full content identity.

    The digest covers periods, phases, active-edge polarities, clock/reset
    bindings, reset polarities, timestamps, coincident event sets and asserted
    reset sets.  Generation and replay can therefore compare the exact schedule
    they consumed instead of comparing two independently reconstructed clocks.
    """

    VERSION = "global-event-slots/1"
    __slots__ = ("_canonical", "_digest", "slots", "timings")

    def __init__(self, timings, slots):
        timings = tuple(timings)
        if any(not isinstance(timing, ClockTiming) for timing in timings):
            raise TypeError("schedule timings must be ClockTiming records")
        timings = tuple(sorted(timings, key=lambda timing: timing.name))
        slots = tuple(slots)
        if not timings:
            raise ValueError("a global schedule needs at least one clock domain")
        names = [timing.name for timing in timings]
        if len(names) != len(set(names)):
            raise Refused(Refusal(
                "schedule-duplicate-domain",
                "two clock descriptions name one domain, so an event does not say "
                "which period and polarity produced it",
                domains=names))
        if not slots:
            raise ValueError("a global schedule needs at least one event slot")
        known = set(names)
        timing_by_name = {timing.name: timing for timing in timings}
        previous = -1
        next_edge = {timing.name: timing.phase for timing in timings}
        periods = {timing.name: timing.period for timing in timings}
        for index, slot in enumerate(slots):
            if not isinstance(slot, GlobalEventSlot):
                raise TypeError("schedule slots must be GlobalEventSlot records")
            if slot.time <= previous:
                raise Refused(Refusal(
                    "schedule-time-not-increasing",
                    "global event slots must be strictly ordered by time; equal "
                    "timestamps belong in one slot with a coincident event set",
                    slot=index, time=slot.time, previous=previous))
            previous = slot.time
            unknown = sorted((set(slot.events) | set(slot.resets)) - known)
            if unknown:
                raise Refused(Refusal(
                    "schedule-unknown-domain",
                    "an event or reset names a domain absent from the clock schedule",
                    slot=index, unknown=unknown, domains=sorted(known)))
            unbound_resets = sorted(
                name for name in slot.resets
                if timing_by_name[name].reset is None)
            if unbound_resets:
                raise Refused(Refusal(
                    "schedule-reset-binding-missing",
                    "a slot asserts reset for a domain with no declared reset "
                    "binding",
                    slot=index, domains=unbound_resets))
            missed = sorted(name for name, when in next_edge.items()
                            if when < slot.time)
            due = sorted(name for name, when in next_edge.items()
                         if when == slot.time)
            if missed or tuple(due) != slot.events:
                raise Refused(Refusal(
                    "schedule-event-set-not-induced",
                    "the recorded event slots must contain every active edge "
                    "induced by the declared periods and phases, with equal-time "
                    "edges kept in one set",
                    slot=index, time=slot.time, recorded=list(slot.events),
                    due=due, missed=missed))
            for name in due:
                next_edge[name] += periods[name]
        self.timings = timings
        self.slots = slots
        body = self.as_dict()
        self._canonical = canonical_json(body)
        self._digest = hashlib.sha256(self._canonical.encode()).hexdigest()
        self._freeze()

    def reset_changes(self, index: int) -> tuple[tuple[str, bool], ...]:
        """Reset assertions/releases at the control phase of a scheduled step.

        The fixed profile starts with resets deasserted.  Later steps inherit
        the preceding levels, including across steps without a clock edge.
        This is a control-event view, not a clock tick or a reset-hold count.
        """
        if type(index) is not int or not 0 <= index < len(self.slots):
            raise IndexError("a reset transition needs an existing schedule index")
        before = set(self.slots[index - 1].resets) if index else set()
        after = set(self.slots[index].resets)
        return tuple((domain, domain in after)
                     for domain in sorted(before ^ after))

    @classmethod
    def periodic(cls, timings, n_slots, *, reset_edges=None):
        """Freeze a finite prefix of the same event stream used at runtime."""
        if type(n_slots) is not int or n_slots < 1:
            raise ValueError("n_slots must be a positive integer")
        timings = tuple(timings)
        return cls(timings, islice(periodic_slots(timings, reset_edges=reset_edges), n_slots))

    @classmethod
    def with_reset_prefix(cls, timings, body_slots, *, reset_edges):
        """A periodic schedule with a simultaneous reset prefix.

        Reset remains asserted in every domain until each domain has observed
        its declared number of active edges.  The following ``body_slots`` slots
        have reset released.  Independent mid-run resets are represented by the
        ordinary ``GlobalEventSlot.resets`` field; this constructor is only the
        common power-up/reset prefix used by generated witnesses.
        """
        timings = tuple(timings)
        if type(body_slots) is not int or body_slots < 1:
            raise ValueError("body_slots must be a positive integer")
        counts = {timing.name: reset_edges.get(timing.name, 0)
                  for timing in timings}
        if set(reset_edges) - set(counts):
            raise Refused(Refusal(
                "schedule-reset-unknown-domain",
                "a reset prefix names a domain absent from the clock schedule",
                unknown=sorted(set(reset_edges) - set(counts)),
                domains=sorted(counts)))
        if any(type(count) is not int or count < 0 for count in counts.values()):
            raise ValueError("every reset edge count must be a nonnegative integer")
        heap = [(timing.phase, timing.name, timing.period) for timing in timings]
        heapq.heapify(heap)
        fired = dict.fromkeys(counts, 0)
        slots = []
        body_seen = 0
        while body_seen < body_slots:
            now = heap[0][0]
            events = []
            # Reset ownership is per domain.  Capture the levels at this global
            # instant before advancing the edge counters: the edge that reaches a
            # domain's requested count is still a reset edge, but a faster domain is
            # not held for extra edges while a slower one finishes its own prefix.
            resets = tuple(sorted(name for name, count in counts.items()
                                  if fired[name] < count))
            while heap and heap[0][0] == now:
                _time, name, period = heapq.heappop(heap)
                events.append(name)
                fired[name] += 1
                heapq.heappush(heap, (now + period, name, period))
            if not resets:
                resets = ()
                body_seen += 1
            slots.append(GlobalEventSlot(now, events, resets))
        return cls(timings, slots)

    def as_dict(self):
        return {
            "version": self.VERSION,
            "timings": [timing.as_dict() for timing in self.timings],
            "slots": [slot.as_dict() for slot in self.slots],
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise Refused(Refusal(
                "schedule-record-malformed",
                "a global schedule record must be a mapping"))
        required = {"version", "timings", "slots"}
        if set(value) != required:
            raise Refused(Refusal(
                "schedule-record-malformed",
                "a global schedule record must contain exactly the versioned fields",
                missing=sorted(required - set(value)),
                unknown=sorted(set(value) - required)))
        if value.get("version") != cls.VERSION:
            raise Refused(Refusal(
                "schedule-version-unsupported",
                "the recorded schedule uses a different event-slot semantics",
                got=value.get("version"), supported=cls.VERSION))
        if type(value["timings"]) is not list or type(value["slots"]) is not list:
            raise Refused(Refusal(
                "schedule-record-malformed",
                "schedule timings and slots must be explicit lists"))
        try:
            return cls([ClockTiming.from_dict(item)
                        for item in value["timings"]],
                       [GlobalEventSlot.from_dict(item)
                        for item in value["slots"]])
        except Refused:
            raise
        except (TypeError, ValueError) as exc:
            raise Refused(Refusal(
                "schedule-record-malformed",
                "the global schedule contains an invalid field value",
                error=str(exc))) from None

    @property
    def digest(self):
        return self._digest

    def timing(self, domain):
        for timing in self.timings:
            if timing.name == domain:
                return timing
        return None

    def assert_digest(self, digest, *, consumer):
        if type(digest) is not str or digest != self.digest:
            raise Refused(Refusal(
                "schedule-digest-mismatch",
                "generation and replay did not consume the same global event "
                "schedule, so their traces are about different timing",
                consumer=str(consumer), recorded=digest, actual=self.digest))
