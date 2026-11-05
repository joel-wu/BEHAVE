"""Transaction-driven scheduling, separate from a recorded finite schedule.

The driver owns acceptance and protocol state. This controller only chooses
environment actions: reset, release queued inputs, wait for results, drain.
It has no total-slot limit. The execution worker owns resource cancellation;
running out of time does not produce a completed trace.
"""

from __future__ import annotations

from dataclasses import dataclass

from behave.core import runtime_defaults as defaults

from .clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule, periodic_slots
from .protocol import receptive_controls, registered_adapter
from .refusal import Refusal, Refused
from .stimulus import EventSlot, Stimulus


@dataclass(frozen=True, slots=True)
class ReplayPolicy:
    """Reset and post-completion observation policy; never an analysis bound.

    Drain slots remain observable, including output and memory activity. Ending
    this window does not establish correctness: comparison and monitors still
    decide whether all required results were produced.
    """

    reset_hold_cycles: int = defaults.SIM_RESET_HOLD_CYCLES
    drain_slots: int = defaults.SIM_DRAIN_SLOTS
    stall_every: int = defaults.SIM_STALL_EVERY
    clock_timings: tuple[ClockTiming, ...] = ()

    def __post_init__(self):
        for name in ("reset_hold_cycles", "drain_slots", "stall_every"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative exact integer")
        if type(self.clock_timings) is not tuple or any(
                type(timing) is not ClockTiming for timing in self.clock_timings):
            raise TypeError("runtime clock timings must be a tuple of ClockTiming records")
        if len({timing.name for timing in self.clock_timings}) != len(self.clock_timings):
            raise ValueError("runtime clock domains must be distinct")
        object.__setattr__(self, "clock_timings", tuple(sorted(
            self.clock_timings, key=lambda timing: timing.name)))

    def as_dict(self):
        return {"version": "transaction-replay/3",
                "reset_hold_cycles": self.reset_hold_cycles,
                "drain_slots": self.drain_slots,
                "stall_every": self.stall_every,
                "clock_timings": [timing.as_dict() for timing in self.clock_timings]}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {
                "version", "reset_hold_cycles", "drain_slots", "stall_every", "clock_timings"} \
                or value["version"] != "transaction-replay/3":
            raise ValueError("unsupported transaction replay policy")
        return cls(**{key: item for key, item in value.items()
                      if key not in ("version", "clock_timings")},
                   clock_timings=tuple(ClockTiming.from_dict(row) for row in value["clock_timings"]))

    @classmethod
    def for_contract(cls, contract):
        continuous = any(registered_adapter(endpoint.adapter.name, endpoint.adapter.version)
                         .requires_input_each_step(endpoint) for endpoint in contract.inputs())
        timings = ()
        if len(contract.clocks) > 1:
            periods = defaults.SIM_BASELINE_CLOCK_PERIODS
            timings = tuple(ClockTiming(
                domain.name, periods[index % len(periods)], phase=periods[index % len(periods)],
                edge=domain.edge, clock=domain.clock, reset=domain.reset,
                reset_active_low=domain.reset_active_low)
                for index, domain in enumerate(contract.clocks))
        return cls(reset_hold_cycles=defaults.SIM_RESET_HOLD_CYCLES if contract.clocks else 0,
                   drain_slots=defaults.SIM_DRAIN_SLOTS if contract.clocks and not continuous else 0,
                   clock_timings=timings)


def environment_controls(contract, index, reset_domains, stall_every):
    """One assignment of the task's declared environment-owned controls."""

    values = {}
    for clock in contract.clocks:
        asserted = int(not clock.reset_active_low)
        values[clock.reset] = (asserted if clock.name in reset_domains
                               else 1 - asserted)
    for name in receptive_controls(contract):
        values[name] = int(not (stall_every and index % stall_every == 0))
    scheduled = tuple(contract.scheduled_controls())
    missing = sorted(set(scheduled) - set(values))
    if missing:
        raise Refused(Refusal(
            "generation-baseline-control-policy-incomplete",
            "the environment policy does not assign every scheduled control",
            controls=missing))
    return {name: values[name] for name in scheduled}


class TransactionSchedule:
    """A runtime execution driven by the protocol driver's queue cursors.

    ``slot`` is called only for the next unobserved slot. Issuing an offer never
    advances acceptance here. The returned finite schedule is published only
    after the inputs and declared responses have drained and the tail has executed.
    """

    def __init__(self, contract, stimulus, policy, *, expected_outputs=None):
        if type(policy) is not ReplayPolicy:
            raise TypeError("transaction scheduling needs a ReplayPolicy")
        if stimulus.slots or stimulus.clock_schedule is not None:
            raise ValueError("a transaction request cannot also fix its schedule")
        from .sampled_runtime import SampledClockWindow

        sampled_window = SampledClockWindow.for_contract(contract, policy)
        reset_edges = (sampled_window.reset_edges if sampled_window is not None else
                       {clock.name: policy.reset_hold_cycles for clock in contract.clocks})
        self.events = (periodic_slots(policy.clock_timings, reset_edges=reset_edges)
            if len(contract.clocks) > 1 else None)
        if (len(contract.clocks) > 1) != bool(policy.clock_timings):
            raise ValueError("a multi-clock runtime request must specify its clock timings")
        self.scheduled = []
        if not contract.clocks and (policy.reset_hold_cycles or policy.drain_slots):
            raise ValueError("settled sampling has no reset or clock drain slots")
        self.contract, self.request, self.policy = contract, stimulus, policy
        self.depths = {endpoint.name: stimulus.depth(endpoint.name)
                       for endpoint in contract.inputs()}
        continuous = [endpoint.name for endpoint in contract.inputs()
                      if registered_adapter(
                          endpoint.adapter.name, endpoint.adapter.version
                      ).requires_input_each_step(endpoint)]
        if continuous and (policy.drain_slots or set(continuous) != set(self.depths)
                           or (sampled_window is None and len(set(self.depths.values())) > 1)):
            raise Refused(Refusal(
                "runtime-schedule-continuous-input",
                "a continuously sampled input needs a queued value on every "
                "active edge; it cannot idle while another input waits or drains"))
        # Completion comes from reference output counts or a declared correlation,
        # never from endpoint count or an idle interval.
        from .monitor import Monitor

        correlation = Monitor(contract).correlation()
        if sampled_window is not None:
            sampled_outputs = sampled_window.outputs_for(stimulus)
            if expected_outputs is not None and dict(expected_outputs) != sampled_outputs:
                raise ValueError("sampled output counts disagree with the physical sampling window")
            expected_outputs = sampled_outputs
        if expected_outputs is not None:
            expected_outputs = dict(expected_outputs)
            if set(expected_outputs) != {endpoint.name for endpoint in contract.outputs()} \
                    or any(type(count) is not int or count < 0
                           for count in expected_outputs.values()):
                raise ValueError("completion needs one nonnegative count per output endpoint")
        self.expected_outputs = expected_outputs
        if contract.outputs() and correlation is None and expected_outputs is None:
            raise Refused(Refusal(
                "runtime-schedule-completion-undeclared",
                "this execution profile needs a declared response count; "
                "variable-output completion needs the reference result stream"))
        self.response_ratio = correlation[1] if correlation is not None else None
        self.slots = []
        self.drain_at = None
        self.complete = False

    def slot(self, accepted, released, *, responses_pending):
        if self.complete:
            return None
        index = len(self.slots)
        scheduled = next(self.events) if self.events is not None else None
        resets = (scheduled.resets if scheduled is not None else
                  tuple(clock.name for clock in self.contract.clocks)
                  if index < self.policy.reset_hold_cycles else ())
        in_reset = bool(resets) if scheduled is not None else index < self.policy.reset_hold_cycles
        if not in_reset and not responses_pending and self.drain_at is None and all(
                accepted[name] == depth for name, depth in self.depths.items()):
            self.drain_at = index
        if self.drain_at is not None and (
                index - self.drain_at >= self.policy.drain_slots):
            self.complete = True
            return None
        active = set(scheduled.events) - set(resets) if scheduled is not None else None
        offers = tuple(endpoint.name for endpoint in self.contract.inputs()
                       if released[endpoint.name] < self.depths[endpoint.name]
                       and not in_reset
                       and (endpoint.clock in active if active is not None else True))
        # Once all inputs have been released, receiver backpressure is removed
        # even if the DUT still owes input acceptances. Pending valid stays driven
        # by InputQueueState, not by these release events.
        injecting = bool(offers)
        controls = environment_controls(
            self.contract, index, resets, self.policy.stall_every if injecting else 0)
        reset = in_reset if scheduled is None else False
        ticks = () if scheduled is None else scheduled.events
        reset_domains = () if scheduled is None else resets
        previous = self.slots[-1] if self.slots else None
        if (previous is not None and previous.offers == offers
                and previous.reset == reset and previous.ticks == ticks
                and previous.resets == reset_domains and dict(previous.controls) == controls):
            # All values above are owned, typed schedule outputs. An EventSlot
            # has no index or time field and is immutable. Physical timestamps
            # still get a distinct GlobalEventSlot below, even when controls repeat.
            slot = previous
        else:
            slot = EventSlot(offers=offers, reset=reset, ticks=ticks,
                             resets=reset_domains, controls=controls)
        self.slots.append(slot)
        if scheduled is not None:
            self.scheduled.append(GlobalEventSlot(scheduled.time, event=slot))
        return slot

    def stimulus(self):
        """Materialize what ran, without modifying the original request."""

        if not self.complete:
            raise Refused(Refusal(
                "driver-incomplete", "transaction execution has not finished"))
        return self.observed_stimulus()

    def observed_stimulus(self):
        """Snapshot the issued schedule; this does not declare run completion.

        The owner must first observe every issued slot. Keep the original
        queues, including pending inputs: a checkpoint is not a shorter request.
        """
        return Stimulus(
            queues=self.request.queues, slots=self.slots,
            memory_policy=self.request.memory_policy,
            memory_environment=(None if self.request.memory_environment is None else
                                self.request.memory_environment.materialize(len(self.slots))),
            initial_state=self.request.initial_state, source=self.request.source,
            clock_schedule=(GlobalSchedule(self.policy.clock_timings, self.scheduled)
                            if self.events is not None else None))


def validate_resolution(contract, request, resolved, records, expected_outputs, *, collect_protocol=False,
                        protocol_stop=None, observed_prefix=False):
    """Check the recorded schedule against the causal runtime policy.

    An explicitly requested prefix validates only the observed transitions,
    without generating one extra slot or claiming completion. It is not score
    evidence; transaction obligations and comparison remain separate checks.
    """
    from .atomic import AtomicDriver
    from .driver import Driver

    atomic = len(contract.clocks) > 1
    driver = (AtomicDriver if atomic else Driver)(
        contract, request, expected_outputs=expected_outputs)
    facts = ()
    if collect_protocol:
        facts = (tuple(driver._protocol_facts.values()) if atomic else (driver._protocol_facts,))
        for fact in facts:
            fact.retain = True
    iterator = driver.iter_slots()
    for actual, record in zip(resolved.slots, records, strict=True):
        try:
            index, expected = next(iterator)
        except StopIteration:
            raise Refused(Refusal("runtime-schedule-extra-slot",
                                 "the trace continued after its completion policy")) from None
        if actual != expected:
            raise Refused(Refusal("runtime-schedule-policy-mismatch",
                                 "the recorded environment does not follow the runtime policy",
                                 slot=index))
        drive = driver.drive(index) if atomic else driver.presentations(index)
        replayed = driver.observe(index, drive, dict(record.outputs))
        if replayed.accepted != record.accepted:
            raise Refused(Refusal("runtime-schedule-transfer-mismatch",
                                 "the recorded transfers disagree with the protocol samples", slot=index))
    if not observed_prefix and next(iterator, None) is not None:
        raise Refused(Refusal("runtime-schedule-incomplete",
                             "input/result completion and the receptive tail have not finished"))
    actual_stop = None if driver.protocol_stop is None else driver.protocol_stop.as_dict()
    if actual_stop != protocol_stop:
        raise Refused(Refusal("runtime-schedule-stop-mismatch",
                             "the recorded protocol stop does not match the observed violation"))
    actual_stimulus = (driver._runtime_schedule.observed_stimulus() if observed_prefix
                       else driver.completed_stimulus())
    if actual_stimulus.fingerprint != resolved.fingerprint:
        raise Refused(Refusal("runtime-schedule-request-mismatch",
                             "the resolved schedule changed the runtime request"))
    return {tuple(endpoint.name for endpoint in fact.contract.endpoints):
            fact.ledger(truncated=observed_prefix or driver.protocol_stop is not None)
            for fact in facts}
