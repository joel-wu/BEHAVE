"""The executable protocol product for one explicit global event slot.

The single-clock :mod:`behave.sim.core.driver` owns one protocol instance.  A
timestamped task owns one instance per clock domain and advances all instances whose
active edge belongs to the same :class:`GlobalEventSlot` before publishing one
``SlotRecord``.  This module is that product; it does not serialize coincident edges or
invent a cross-domain ordering.

One clock can also carry a timestamped schedule. Clock count does not decide
whether an empty event advances time without executing a clock edge.

The product is intentionally over the registered protocol programs.  It does not copy
ready/valid in a second driver: each domain projection is driven and observed by the
same versioned adapter used by the single-clock path.  A ready/valid domain is itself the
stateless product of every endpoint it owns; a stateful adapter validates whatever
endpoint relation its exact version declares.
"""

from __future__ import annotations

from .contract import Direction, Kind
from .failure import CandidateFault, call_candidate
from .frozen import Frozen, FrozenMap
from .input_queue import InputQueueState
from .memory import (
    MemoryEnvironmentState,
    advance_memory_environment,
    drive_memory_environment,
)
from .protocol import AdapterDrive, adapter_program_for, payload_signals
from .refusal import Refusal, Refused
from .stimulus import AuditedTrace, Completion, EventSlot, SlotRecord, Stimulus, trace_domain, uses_global_events

__all__ = [
    "ATOMIC_ADAPTER_VERSION",
    "AtomicDrive",
    "AtomicDriver",
    "DomainContractView",
    "atomic_adapter_programs",
    "project_domain_trace",
    "run_atomic_in_memory",
]


ATOMIC_ADAPTER_VERSION = "atomic-protocol-product/6"
_FOUR_STATE_DIGITS = frozenset("01xzXZ-")


def _artifact_sample(value):
    """Whether one simulator sample is exact or explicitly unreadable."""

    return (type(value) is int or value is None
            or type(value) is str and bool(value)
            and set(value) <= _FOUR_STATE_DIGITS)


class DomainContractView(Frozen):
    """Read-only task declarations projected onto one clock domain.

    Adapter programs consume a small structural interface (``endpoints``, ``inputs``
    and ``outputs``); they do not require a second ``TaskContract`` or a second digest.
    This view retains the original endpoint, clock and protocol-instance records
    by reference. A clock domain and a protocol instance are not interchangeable.
    """

    __slots__ = ("clocks", "endpoints", "protocol_instances")

    def __init__(self, endpoints, clock, *, protocol_instances=()):
        self.endpoints = tuple(sorted(endpoints, key=lambda endpoint: endpoint.name))
        self.clocks = (clock,)
        self.protocol_instances = tuple(protocol_instances)
        self._freeze()

    def inputs(self):
        return tuple(endpoint for endpoint in self.endpoints
                     if endpoint.direction is Direction.IN)

    def outputs(self):
        return tuple(endpoint for endpoint in self.endpoints
                     if endpoint.direction is Direction.OUT)

    def presented_controls(self):
        return tuple(sorted({name for endpoint in self.inputs()
                             for name in endpoint.adapter.environment_controls}))

    def scheduled_controls(self):
        presented = set(self.presented_controls())
        names = {self.clocks[0].reset}
        names.update(name for endpoint in self.endpoints
                     for name in endpoint.adapter.environment_controls)
        return tuple(sorted(names - presented))


def atomic_adapter_programs(contract):
    """Validate and return the executable adapter program of each endpoint domain."""
    if not contract.clocks:
        # None is the declared clockless sample domain, not a generated clock.
        # The registered adapter still validates the plain-pin contract.
        return {None: (contract, adapter_program_for(contract))}
    endpoint_by_domain = {clock.name: [] for clock in contract.clocks}
    for endpoint in contract.endpoints:
        endpoint_by_domain[endpoint.clock].append(endpoint)
    out = {}
    for clock in contract.clocks:
        endpoints = endpoint_by_domain[clock.name]
        if not endpoints:
            continue
        view = DomainContractView(
            endpoints, clock,
            protocol_instances=contract.protocol_instances_for(clock.name))
        out[clock.name] = (view, adapter_program_for(view))
    return out


def project_domain_trace(domain, contract, stimulus, trace):
    """An adapter's active-edge trace, retaining its global slot indexes.

    Monitor replay and search selection consume the same projection. Inactive
    edges do not run the adapter; indexes still retain elapsed global time.
    """
    endpoint_names = {endpoint.name for endpoint in contract.endpoints}
    control_names = set(contract.scheduled_controls())
    indexes, slots, records = [], [], []
    for index, slot in enumerate(stimulus.slots):
        if domain not in slot.ticks:
            continue
        indexes.append(index)
        slots.append(EventSlot(
            offers=tuple(name for name in slot.offers if name in endpoint_names),
            controls={name: value for name, value in slot.controls if name in control_names},
            reset=domain in slot.resets, ticks=(domain,)))
        if index >= len(trace.records):
            continue
        record = trace.records[index]
        records.append(SlotRecord(
            accepted={name: payload for name, payload in record.accepted if name in endpoint_names},
            state=dict(record.state), outputs=dict(record.outputs),
            hits=dict(record.hits), monitor=dict(record.monitor),
            environment=dict(record.environment)))
    projected = Stimulus(
        queues={name: transactions for name, transactions in stimulus.queues if name in endpoint_names},
        slots=slots, memory_policy=dict(stimulus.memory_policy),
        memory_environment=stimulus.memory_environment,
        initial_state=dict(stimulus.initial_state), source=stimulus.source)
    projected_trace = AuditedTrace(
        projected, initial_state=dict(trace.initial_state), records=records,
        completion=trace.completion, domain="", artifact=trace.artifact)
    return projected, projected_trace, tuple(indexes)


class AtomicDrive(Frozen):
    """The environment drive for one global slot.

    ``signals`` is the one physical pin valuation presented to the artifact.
    ``domains`` retains each adapter's typed drive so observation cannot reconstruct it
    from the candidate's response.
    """

    __slots__ = ("domains", "signals")

    def __init__(self, signals, domains):
        if type(signals) is not dict:
            raise TypeError("an atomic drive needs an exact signal mapping")
        if any(type(name) is not str or not name or type(value) is not int
               for name, value in signals.items()):
            raise TypeError("atomic drive signals need names and exact integer values")
        if type(domains) is not dict or any(
                type(name) is not str or not name or type(drive) is not AdapterDrive
                for name, drive in domains.items()):
            raise TypeError("atomic domain drives need exact AdapterDrive values")
        self.signals = FrozenMap(signals)
        self.domains = FrozenMap(domains)
        self._freeze()


class AtomicDriver:
    """Drive one canonical clocked stimulus through the per-domain adapter product."""

    def __init__(self, contract, stimulus, artifact_identity="", *, expected_outputs=None):
        if not contract.clocks or not uses_global_events(contract, stimulus):
            raise Refused(Refusal(
                "atomic-driver-needs-global-events",
                "the atomic protocol product needs an explicit clock schedule; "
                "implicit one-clock slots use Driver",
                clocks=[clock.name for clock in contract.clocks]))
        faults = stimulus.faults_against(contract)
        if faults:
            raise Refused(Refusal(
                "atomic-driver-stimulus-not-of-this-contract",
                "this clocked stimulus does not describe a run of the admitted "
                "clocked contract",
                faults=faults, total=len(faults)))
        self.contract = contract
        from .input_framing import InputDomainCursor

        self._input_domain = InputDomainCursor(contract)
        self.stimulus = stimulus
        self.schedule = stimulus.clock_schedule
        self._runtime_schedule = None
        if stimulus.replay_policy is not None:
            from .runtime_schedule import TransactionSchedule

            self._runtime_schedule = TransactionSchedule(
                contract, stimulus, stimulus.replay_policy, expected_outputs=expected_outputs)
        self._emitted = dict.fromkeys((endpoint.name for endpoint in contract.outputs()), 0)
        self.artifact_identity = str(artifact_identity)
        self.records = []
        self._protocol_facts = None
        self._queue_state = InputQueueState.initial(contract.inputs())
        self.memory_state = (
            None if not contract.memories else
            MemoryEnvironmentState.initial(
                contract.memories, stimulus.memory_environment)
        )
        #: Compatibility name retained for callers of the first atomic driver.  It was
        #: already an endpoint mapping, unlike the old single-clock scalar.
        self.taken = self._queue_state.accepted

        self.views = {}
        self.programs = {}
        self.states = {}
        for domain, (view, program) in atomic_adapter_programs(contract).items():
            self.views[domain] = view
            self.programs[domain] = program
            self.states[domain] = program.initial_state(view)
        if not self.programs:
            raise Refused(Refusal(
                "atomic-driver-no-endpoint-protocol",
                "none of this multi-clock contract's domains owns a transaction "
                "endpoint, so there is no score denominator or protocol observation"))

        self._runtime_monitors = {}
        if self._runtime_schedule is not None:
            from .monitor import RuntimeProtocolMonitor, monitor_for

            owner = monitor_for(contract)
            components = (owner._components if len(contract.clocks) > 1 else
                          ((contract.clocks[0].name, contract, owner),))
            self._runtime_monitors = {
                domain: RuntimeProtocolMonitor(view, self.programs[domain])
                for domain, view, _monitor in components}
            self._protocol_facts = {domain: monitor.facts
                                    for domain, monitor in self._runtime_monitors.items()}

        for endpoint in contract.endpoints:
            if endpoint.kind is not Kind.TRANSACTION:
                raise Refused(Refusal(
                    "adapter-channel-kind" if endpoint.kind is Kind.SAMPLED
                    else "adapter-channel-kind-undeclared",
                    "the atomic adapter product describes transaction endpoints; this "
                    "endpoint is declared as something else or as nothing at all",
                    endpoint=endpoint.name, channel_kind=endpoint.kind.value))

    @property
    def protocol_stop(self):
        return next((monitor.stop for domain, monitor in sorted(self._runtime_monitors.items())
                     if monitor.stop is not None), None)

    @property
    def timings(self):
        return (self.stimulus.replay_policy.clock_timings if self._runtime_schedule is not None
                else self.schedule.timings)

    @property
    def scheduled(self):
        return (self._runtime_schedule.scheduled if self._runtime_schedule is not None
                else self.schedule.slots)

    def _slot(self, index):
        return (self._runtime_schedule.slots if self._runtime_schedule is not None
                else self.stimulus.slots)[index]

    def iter_slots(self):
        if self._runtime_schedule is None:
            yield from enumerate(self.stimulus.slots)
            return
        runtime = self._runtime_schedule
        while True:
            index = len(runtime.slots)
            if index != len(self.records):
                raise ValueError("observe the current global slot before requesting the next")
            pending = (sum(self._emitted.values()) < sum(self.pointers.values()) * runtime.response_ratio
                       if runtime.response_ratio is not None else False)
            if runtime.expected_outputs is not None:
                pending = any(self._emitted[name] < count
                              for name, count in runtime.expected_outputs.items())
            slot = runtime.slot(self.pointers, self.released, responses_pending=pending)
            if slot is None:
                return
            yield index, slot

    def _local_slot(self, domain, slot):
        view = self.views[domain]
        names = {endpoint.name for endpoint in view.endpoints}
        relevant_controls = set(view.scheduled_controls())
        return EventSlot(
            offers=tuple(name for name in slot.offers if name in names),
            controls={name: value for name, value in slot.controls
                      if name in relevant_controls},
            reset=domain in slot.resets,
            ticks=(domain,) if domain in slot.ticks else ())

    @property
    def pointers(self):
        return self._queue_state.accepted

    def progress_counts(self):
        """Observed transfers at committed global slots, not a score."""
        return {"slots": len(self.records), "accepted": dict(self.pointers),
                "emitted": dict(self._emitted)}

    @property
    def released(self):
        return self._queue_state.released

    def _prepared_inputs(self, slot):
        releasable = {
            endpoint.name for endpoint in self.contract.inputs()
            if endpoint.clock in slot.ticks and endpoint.clock not in slot.resets
        }
        presentable = set()
        for domain, view in self.views.items():
            if domain not in slot.ticks or domain in slot.resets:
                continue
            program = self.programs[domain]
            presentable.update(
                endpoint.name for endpoint in view.inputs()
                if program.presentation_allowed(
                    view, self.states[domain], endpoint)
            )
        return self._queue_state.prepare(
            self.stimulus, slot, releasable=releasable,
            presentable=presentable)

    def _presentations(self, domain, prepared):
        inputs = self.views[domain].inputs()
        out = {}
        for endpoint in inputs:
            payload = prepared.presentations[endpoint.name]
            if payload is not None:
                faults = endpoint.admits(payload)
                if faults:
                    raise Refused(Refusal(
                        "atomic-driver-payload-outside-contract",
                        "the stimulus presents a value outside this endpoint's "
                        "declared finite payload type",
                        endpoint=endpoint.name, faults=faults))
            out[endpoint.name] = payload
        return FrozenMap(out)

    def drive(self, index):
        """Compute all domain drives from the common pre-slot adapter state."""
        slot = self._slot(index)
        prepared = self._prepared_inputs(slot)
        signals = dict(slot.controls)
        if self.memory_state is not None:
            for name, value in drive_memory_environment(
                    self.contract.memories, self.stimulus.memory_environment,
                    self.memory_state, index, reset_domains=slot.resets).items():
                if name in signals and signals[name] != value:
                    raise Refused(Refusal(
                        "atomic-drive-pin-collision",
                        "the task schedule and external-memory environment drive one "
                        "physical input pin differently",
                        pin=name, first=signals[name], second=value, slot=index))
                signals[name] = value
        domain_drives = {}
        for domain in sorted(set(slot.ticks) & set(self.programs)):
            view, program = self.views[domain], self.programs[domain]
            local = self._local_slot(domain, slot)
            presentations = self._presentations(domain, prepared)
            scheduled = {name: dict(slot.controls)[name]
                         for name in view.scheduled_controls()}
            drive = program.drive(
                view, self.states[domain], local, presentations, scheduled)
            domain_drives[domain] = drive
            for name, value in drive.controls.items():
                if name in signals and signals[name] != value:
                    raise Refused(Refusal(
                        "atomic-drive-pin-collision",
                        "two domain adapters drive one environment pin differently",
                        pin=name, first=signals[name], second=value,
                        domain=domain, slot=index))
                signals[name] = value
            for endpoint in view.inputs():
                payload = drive.presentation(endpoint.name)
                signals.update(payload_signals(endpoint, payload))
        return AtomicDrive(signals, domain_drives)

    def observe(self, index, drive, signals, *, hits=(), state=None):
        """Apply every active domain adapter to one common pin observation."""
        if type(drive) is not AtomicDrive:
            raise TypeError("atomic observation needs the AtomicDrive for this slot")
        if type(signals) is not dict or any(
                type(name) is not str or not name
                or not _artifact_sample(value)
                for name, value in signals.items()):
            raise CandidateFault(Refusal(
                "atomic-artifact-observation-malformed",
                "an atomic artifact observation maps exact signal names to integer "
                "values or explicit unreadable four-state samples"))
        if type(hits) not in (tuple, list) or any(
                type(goal) is not str or not goal for goal in hits):
            raise CandidateFault(Refusal(
                "atomic-artifact-hit-record-malformed",
                "an atomic artifact's slot hits are goal identifiers"))

        slot = self._slot(index)
        accepted, monitor = {}, {}
        checks, violations = [], []
        next_states = {}
        recovered = [] if self._protocol_facts is not None else None
        for domain in sorted(self.programs):
            view, program = self.views[domain], self.programs[domain]
            if domain not in slot.ticks:
                next_states[domain] = program.elapse(view, self.states[domain])
                continue
            local = self._local_slot(domain, slot)
            transition = program.step(
                view, self.states[domain], local, drive.domains[domain], signals)
            if recovered is not None:
                recovered.append((domain, local, transition))
            for batch in (transition.accepted_inputs, transition.emitted_outputs):
                for endpoint, payload in batch.items():
                    if endpoint in accepted:
                        raise Refused(Refusal(
                            "atomic-record-endpoint-collision",
                            "two protocol instances reported one logical endpoint in "
                            "the same slot",
                            endpoint=endpoint, slot=index))
                    accepted[endpoint] = payload
            next_states[domain] = transition.state
            for name, value in transition.monitor.items():
                monitor[f"{domain}.{name}"] = value
            checks.extend(transition.checks)
            violations.extend(transition.violations)

        for goal in checks:
            monitor[f"adapter.checked:{goal}"] = 1
        for violation in violations:
            monitor[f"adapter.violation:{violation.goal}"] = violation.why
        next_memory_state = self.memory_state
        if self.memory_state is not None:
            next_memory_state = advance_memory_environment(
                self.contract.memories, self.stimulus.memory_environment,
                self.memory_state, index, signals,
                active_domains=slot.ticks, reset_domains=slot.resets)
        hit_set = set(hits)
        record = SlotRecord(
            accepted=accepted,
            state={} if state is None else state,
            # Unknown pre-edge values matter to the adapter above, but they are not
            # values and therefore do not enter the durable trace.  This is the same
            # boundary used by the single-clock driver.
            outputs={name: value for name, value in signals.items()
                     if type(value) is int},
            hits={goal: goal in hit_set for goal in sorted(hit_set)},
            monitor=monitor,
            environment=(
                {} if next_memory_state is None else
                {"external-memory": next_memory_state.as_dict()}
            ),
        )
        # Storage sharing retains every global slot, including its state/hits.
        if self.records and record == self.records[-1]:
            record = self.records[-1]
        # Commit only after every active domain observed the same artifact output and
        # the complete immutable record was constructed.  Domain iteration and a late
        # refusal therefore cannot leave a half-applied pointer/adapter successor.
        prepared = self._prepared_inputs(slot)
        next_queue_state = self._queue_state.commit(prepared, accepted)
        self._input_domain.advance(slot, accepted, index=index)
        if recovered is not None:
            for domain, local, transition in recovered:
                self._protocol_facts[domain].record(
                    index, local, self.states[domain], drive.domains[domain], transition,
                    record, self._queue_state.accepted)
        self._queue_state = next_queue_state
        self.taken = self._queue_state.accepted
        self.states.update(next_states)
        self.memory_state = next_memory_state
        for name in self._emitted:
            if name in accepted:
                self._emitted[name] += 1
        self.records.append(record)
        if self.protocol_stop is not None:
            self._runtime_schedule.complete = True
        return record

    def completed_stimulus(self):
        """The actual global schedule, without constructing a second trace."""
        stimulus = (self._runtime_schedule.stimulus() if self._runtime_schedule is not None
                    else self.stimulus)
        if len(self.records) != len(stimulus.slots):
            raise Refused(Refusal(
                "atomic-driver-incomplete",
                "the atomic driver has not observed every global event slot",
                slots=len(stimulus.slots), observed=len(self.records)))
        return stimulus

    def trace(self):
        stimulus = self.completed_stimulus()
        return AuditedTrace(
            stimulus,
            records=self.records,
            completion=Completion.INCOMPLETE if self.protocol_stop is not None else Completion.NORMAL,
            domain=trace_domain(
                self.contract.digest,
                horizon=len(stimulus.slots),
                adapter_version=ATOMIC_ADAPTER_VERSION),
            artifact=self.artifact_identity,
            request=self.stimulus if self._runtime_schedule is not None else None,
        )


def run_atomic_in_memory(contract, stimulus, device, *, expected_outputs=None):
    """Run a signal-level Python device through the production atomic driver.

    The device exposes ``atomic_step(global_slot, driven_signals)`` and returns an exact
    signal mapping.  It may instead return ``(signals, hits, state)`` to expose the same
    optional structural observations an RTL replay records.  The protocol and score
    semantics remain entirely in :class:`AtomicDriver`.
    """
    driver = AtomicDriver(
        contract, stimulus,
        artifact_identity=str(getattr(device, "identity", "") or ""),
        expected_outputs=expected_outputs)
    reset = getattr(device, "reset", None)
    if callable(reset):
        call_candidate(reset)
    step = getattr(device, "atomic_step", None)
    if not callable(step):
        raise CandidateFault(Refusal(
            "atomic-device-step-missing",
            "a multi-clock signal-level device must implement "
            "atomic_step(slot, drive)"))
    for index, _slot in driver.iter_slots():
        scheduled = driver.scheduled[index]
        drive = driver.drive(index)
        got = call_candidate(step, scheduled, dict(drive.signals))
        hits, state = (), None
        if type(got) is tuple and len(got) == 3:
            got, hits, state = got
        driver.observe(index, drive, got, hits=hits, state=state)
    return driver.trace()
