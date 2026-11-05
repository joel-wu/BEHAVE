"""Executable, versioned transaction-protocol adapters.

An :class:`~behave.sim.core.contract.Adapter` in a task contract is the immutable
binding of logical endpoint roles to physical pins.  This module is the code behind
that identity.  A registered program supplies the finite protocol state and the two
halves of every slot: environment drive before the artifact settles, then the accepted
and emitted batches, next state, and monitor events after its pins are observed.

The registry is deliberately closed by ``(name, version)``.  An unknown version is a
typed capability refusal; it never inherits the semantics of the closest adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from .failure import CandidateFault
from .frozen import Frozen, FrozenMap, frozen_map
from .input_queue import InputQueueState
from .product import Component
from .record_value import freeze_record_value
from .refusal import Refusal, Refused

__all__ = [
    "APB_ACCESS",
    "ARTIFACT",
    "AXI_LITE_READ",
    "AXI_LITE_WRITE",
    "ENVIRONMENT",
    "READY_VALID",
    "SAMPLED_EDGE",
    "SETTLED",
    "START_DONE",
    "AdapterChannel",
    "AdapterDrive",
    "AdapterProgram",
    "AdapterReplaySlot",
    "AdapterTransition",
    "AdapterViolation",
    "adapter_program_for",
    "payload_signals",
    "receptive_controls",
    "registered_adapter",
    "supported_adapter_identities",
]


APB_ACCESS = "apb-access"
AXI_LITE_READ = "axi-lite-read"
AXI_LITE_WRITE = "axi-lite-write"
READY_VALID = "ready-valid"
SAMPLED_EDGE = "sampled-edge"
SETTLED = "combinational-settled"
START_DONE = "start-done"
ENVIRONMENT = "environment"
ARTIFACT = "artifact"


def receptive_controls(contract):
    """Adapter-declared active-high response acceptance pins, each listed once.

    Baseline scheduling, explicit framing and drain attribution consume this
    same authority. An unknown output action is never guessed to mean READY.
    """
    return tuple(sorted({signal for endpoint in contract.outputs()
                         for signal in registered_adapter(
                             endpoint.adapter.name, endpoint.adapter.version).receptive_controls(endpoint)}))


def _finite_value(value):
    """Whether ``value`` belongs to the adapter record's closed finite carrier."""
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return True
    if kind is tuple:
        return all(_finite_value(item) for item in value)
    return False


def _finite_mapping(value, name):
    if type(value) is not dict and not isinstance(value, FrozenMap):
        raise TypeError(f"an adapter transition's {name} must be a mapping")
    got = value if type(value) in (dict, FrozenMap) else dict(value.items())
    if any(type(key) is not str or not key for key in got):
        raise ValueError(f"an adapter transition's {name} needs string identifiers")
    if any(not _finite_value(item) for item in got.values()):
        raise TypeError(
            f"an adapter transition's {name} contains a value outside its finite "
            "carrier")
    return got


def _payload_record(value, name):
    """Freeze one logical transaction without narrowing its recursive fields."""

    if value is None:
        return None
    if type(value) is not tuple:
        raise TypeError(f"{name} must be None or an exact transaction tuple")
    # These exact scalar leaves are already the frozen record carrier. Reusing
    # their tuple neither aliases mutable state nor accepts subclasses. Recursive
    # fields and floats still take the full validating/detaching projection.
    if all(item is None or type(item) in (bool, int, str) for item in value):
        return value
    try:
        return tuple(
            freeze_record_value(item, f"{name}[{index}]")
            for index, item in enumerate(value))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} contains an invalid finite value: {exc}") from None


class AdapterViolation(Frozen):
    """One declared protocol event attributed to its endpoint and explanation."""

    __slots__ = ("endpoint", "goal", "why")

    def __init__(self, goal, endpoint, why):
        if type(goal) is not str or not goal:
            raise ValueError("an adapter violation needs a nonempty goal identifier")
        if type(endpoint) is not str or not endpoint:
            raise ValueError("an adapter violation needs a nonempty endpoint")
        if type(why) is not str or not why:
            raise ValueError("an adapter violation needs a nonempty explanation")
        self.goal = goal
        self.endpoint = endpoint
        self.why = why
        self._freeze()


class AdapterChannel(Frozen):
    """The protocol facts for one endpoint in one slot."""

    __slots__ = (
        "offered",
        "payload",
        "payload_readable",
        "protocol_readable",
        "ready",
        "started",
        "transferred",
        "why_unreadable",
    )

    def __init__(self, *, offered, ready, transferred, payload=None,
                 payload_readable=True, protocol_readable=True, started=False,
                 why_unreadable=""):
        for name, value in (("offered", offered), ("ready", ready),
                            ("transferred", transferred)):
            if value is not None and type(value) is not bool:
                raise TypeError(
                    f"an adapter channel's {name} must be bool or None")
        for name, value in (("payload_readable", payload_readable),
                            ("protocol_readable", protocol_readable),
                            ("started", started)):
            if type(value) is not bool:
                raise TypeError(f"an adapter channel's {name} must be bool")
        payload = _payload_record(payload, "an adapter channel payload")
        if type(why_unreadable) is not str:
            raise TypeError("an adapter unreadability explanation must be a string")
        if transferred is True and offered is False:
            raise ValueError("an adapter channel cannot transfer without an offer")
        if transferred is True and offered is None and protocol_readable:
            raise ValueError(
                "a readable adapter channel cannot transfer with an unknown offer")
        self.offered = offered
        self.ready = ready
        self.transferred = transferred
        self.payload = payload
        self.payload_readable = payload_readable
        self.protocol_readable = protocol_readable
        self.started = started
        self.why_unreadable = why_unreadable
        self._freeze()


class AdapterDrive(Frozen):
    """Everything the environment drives for one slot."""

    __slots__ = ("controls", "presentations")

    def __init__(self, controls, presentations=None):
        if type(controls) is not dict and not isinstance(controls, FrozenMap):
            raise TypeError("an adapter drive needs an exact control mapping")
        if type(controls) not in (dict, FrozenMap):
            controls = dict(controls.items())
        if any(type(name) is not str or not name for name in controls):
            raise ValueError("adapter drive controls need nonempty string names")
        if any(type(value) is not int or value not in (0, 1)
               for value in controls.values()):
            raise TypeError("adapter drive controls must be exact one-bit integers")
        presentations = {} if presentations is None else presentations
        if (type(presentations) is not dict
                and not isinstance(presentations, FrozenMap)):
            raise TypeError(
                "an adapter drive needs an exact endpoint presentation mapping")
        original_presentations = presentations
        presentations = dict(presentations.items())
        if any(type(endpoint) is not str or not endpoint
               for endpoint in presentations):
            raise ValueError(
                "adapter drive presentations need nonempty endpoint names")
        presentations = {
            endpoint: _payload_record(
                payload, f"adapter drive presentation {endpoint!r}")
            for endpoint, payload in presentations.items()
        }
        self.controls = frozen_map(controls)
        self.presentations = (
            original_presentations if type(original_presentations) is FrozenMap
            and all(payload is original_presentations[name]
                    for name, payload in presentations.items())
            else frozen_map(presentations))
        self._freeze()

    def presentation(self, endpoint):
        """The payload presented on ``endpoint``, or ``None`` when it is idle."""
        if type(endpoint) is not str or not endpoint:
            raise TypeError("an adapter presentation lookup needs an endpoint name")
        return self.presentations.get(endpoint)

    @property
    def payload(self):
        """The historical scalar presentation, for a unique input endpoint only."""
        if len(self.presentations) > 1:
            raise Refused(Refusal(
                "adapter-drive-payload-ambiguous",
                "this drive presents several independently named input endpoints; "
                "a scalar payload would have to select one without a declared rule",
                endpoints=list(self.presentations)))
        return next(iter(self.presentations.values()), None)

    @property
    def payloads(self):
        """Compatibility spelling for the canonical presentation mapping."""
        return self.presentations


class AdapterTransition(Frozen):
    """The total result of observing one driven slot."""

    __slots__ = (
        "accepted_inputs",
        "channels",
        "checks",
        "emitted_outputs",
        "monitor",
        "state",
        "violations",
    )

    def __init__(self, *, state, accepted_inputs=None, emitted_outputs=None,
                 channels=None, checks=(), violations=(), monitor=None):
        accepted_inputs = {} if accepted_inputs is None else accepted_inputs
        emitted_outputs = {} if emitted_outputs is None else emitted_outputs
        channels = {} if channels is None else channels
        monitor = {} if monitor is None else monitor
        state = _finite_mapping(state, "state")
        monitor = _finite_mapping(monitor, "monitor")
        for name, value in (("accepted_inputs", accepted_inputs),
                            ("emitted_outputs", emitted_outputs),
                            ("channels", channels)):
            if type(value) is not dict and not isinstance(value, FrozenMap):
                raise TypeError(f"an adapter transition's {name} must be a mapping")
        accepted_inputs = dict(accepted_inputs.items())
        emitted_outputs = dict(emitted_outputs.items())
        channels = dict(channels.items())
        for name, batch in (("accepted input", accepted_inputs),
                            ("emitted output", emitted_outputs)):
            if any(type(endpoint) is not str or not endpoint
                   for endpoint in batch):
                raise ValueError(f"an adapter {name} needs endpoint identifiers")
            frozen = {
                endpoint: _payload_record(
                    payload, f"an adapter {name} payload for {endpoint!r}")
                for endpoint, payload in batch.items()
            }
            if name == "accepted input":
                accepted_inputs = frozen
            else:
                emitted_outputs = frozen
        if any(type(endpoint) is not str or not endpoint
               or type(channel) is not AdapterChannel
               for endpoint, channel in channels.items()):
            raise TypeError(
                "adapter transition channels need identifiers and exact "
                "AdapterChannel values")
        if type(checks) is not tuple or any(
                type(goal) is not str or not goal for goal in checks):
            raise TypeError("adapter transition checks must be goal identifiers")
        if type(violations) is not tuple or any(
                type(item) is not AdapterViolation for item in violations):
            raise TypeError(
                "adapter transition violations must be exact AdapterViolation values")
        self.state = frozen_map(state)
        self.accepted_inputs = frozen_map(accepted_inputs)
        self.emitted_outputs = frozen_map(emitted_outputs)
        self.channels = frozen_map(channels)
        self.checks = checks
        self.violations = violations
        self.monitor = frozen_map(monitor)
        self._freeze()


@dataclass(frozen=True, slots=True)
class AdapterReplayBoundary:
    """Optional prefix feedback from the already executed adapter transition."""

    domain: str
    state: FrozenMap
    queues: InputQueueState
    successor: FrozenMap
    successor_queues: InputQueueState


@dataclass(frozen=True, slots=True)
class AdapterReplayStep:
    """One reconstructed transition, before any consumer builds its ledger."""

    index: int
    slot: object
    record: object
    state: FrozenMap
    queues: InputQueueState
    drive: AdapterDrive
    transition: AdapterTransition
    successor_queues: InputQueueState

    def releases(self):
        for name, count in self.successor_queues.released.items():
            if count > self.queues.released[name]:
                yield name, self.queues.released[name]

    def presentations(self):
        for name, payload in self.drive.presentations.items():
            if payload is not None:
                yield name, self.queues.accepted[name]


class AdapterReplaySlot(Frozen):
    """One adapter transition reconstructed from a recorded slot."""

    __slots__ = (
        "boundary", "channels", "checks", "drive", "environment_violations",
        "presented_inputs", "violations",
    )

    def __init__(self, channels, checks=(), violations=(),
                 environment_violations=(), *, presented_inputs=(), drive=None, boundary=None):
        if type(channels) is not dict and not isinstance(channels, FrozenMap):
            raise TypeError("an adapter replay slot needs a channel mapping")
        channels = dict(channels.items())
        if any(type(endpoint) is not str or not endpoint
               or type(channel) is not AdapterChannel
               for endpoint, channel in channels.items()):
            raise TypeError(
                "adapter replay channels need identifiers and exact AdapterChannel "
                "values")
        if type(checks) is not tuple or any(
                type(goal) is not str or not goal for goal in checks):
            raise TypeError("adapter replay checks must be goal identifiers")
        for name, values in (("violations", violations),
                             ("environment violations",
                              environment_violations)):
            if type(values) is not tuple or any(
                    type(item) is not AdapterViolation for item in values):
                raise TypeError(
                    f"adapter replay {name} must be exact AdapterViolation values")
        self.channels = FrozenMap(channels)
        self.checks = checks
        self.violations = violations
        self.environment_violations = environment_violations
        # Queue coordinates actually driven by this adapter, before transfers.
        # In a stateful protocol a released head can remain hidden while busy.
        self.presented_inputs = FrozenMap(presented_inputs)
        # Reuse the immutable drive already evaluated by the protocol program.
        # This is reconstructed feedback, not another durable per-slot record.
        self.drive = drive
        # Optional query feedback only: real pre-release queue/adapter state.
        # Runtime scoring does not allocate or persist a second state ledger.
        self.boundary = boundary
        self._freeze()


def _directions(contract):
    from .contract import TaskContract

    if type(contract) is TaskContract:
        return contract.inputs(), contract.outputs()
    inputs = tuple(e for e in contract.endpoints
                   if getattr(e.direction, "value", e.direction) == "in")
    outputs = tuple(e for e in contract.endpoints
                    if getattr(e.direction, "value", e.direction) == "out")
    return inputs, outputs


def _payload(endpoint, signals):
    from .physical_value import (
        PhysicalValueError,
        unpack_signal_value,
    )

    values, missing = [], []
    for field in endpoint.fields:
        pattern = signals.get(field.port)
        if type(pattern) is not int:
            missing.append(field.port)
            values.append(None)
            continue
        try:
            values.append(unpack_signal_value(field.type, pattern))
        except PhysicalValueError as exc:
            missing.append(f"{field.port} ({exc.code})")
            values.append(None)
    if missing:
        return None, False, ", ".join(sorted(missing))
    return tuple(values), True, ""


def payload_signals(endpoint, payload):
    """Pack one logical transaction onto its declaration-owned physical pins."""

    from .physical_value import pack_value

    if payload is None:
        return {field.port: 0 for field in endpoint.fields}
    faults = endpoint.admits(payload)
    if faults:
        raise Refused(Refusal(
            "driver-payload-outside-contract",
            "the logical transaction does not inhabit its endpoint declaration",
            endpoint=endpoint.name, faults=faults))
    return {
        field.port: pack_value(field.type, value)
        for field, value in zip(endpoint.fields, payload)
    }


def _require_bits(endpoint, signals):
    for signal in endpoint.adapter.artifact_controls:
        value = signals.get(signal)
        if type(value) is not int or value not in (0, 1):
            raise CandidateFault(Refusal(
                "driver-handshake-unreadable",
                "the artifact's protocol signal did not come back as 0 or 1, so "
                "whether the protocol event happened is unknown",
                endpoint=endpoint.name, signal=signal,
                got=repr(value)))


def _presentation_mapping(contract, presented):
    """Return one explicit presentation entry for every input endpoint.

    A tuple (or ``None``) is the historical single-input boundary.  It remains
    accepted only where the endpoint is unique; a multi-input caller has to name which
    bus each payload belongs to.
    """
    inputs, _outputs = _directions(contract)
    names = {endpoint.name for endpoint in inputs}
    if type(presented) is dict or isinstance(presented, FrozenMap):
        got = presented if type(presented) is FrozenMap else dict(presented.items())
    elif presented is None or type(presented) is tuple:
        if len(inputs) > 1:
            raise Refused(Refusal(
                "adapter-presentation-ambiguous",
                "several input endpoints can be presented in this slot, so a scalar "
                "payload does not say which endpoint owns it",
                inputs=sorted(names)))
        if not inputs:
            if presented is not None:
                raise Refused(Refusal(
                    "adapter-presentation-without-input",
                    "a payload was presented to a contract with no input endpoint"))
            got = {}
        else:
            got = {inputs[0].name: presented}
    else:
        raise TypeError(
            "adapter presentations must be an exact mapping, integer tuple, or None")
    if any(type(endpoint) is not str or not endpoint for endpoint in got):
        raise TypeError(
            "adapter presentation mappings need nonempty exact string endpoints")
    unknown = sorted(set(got) - names)
    if unknown:
        raise Refused(Refusal(
            "adapter-presentation-endpoint-unknown",
            "a presentation names an endpoint this protocol instance does not drive",
            endpoints=unknown, inputs=sorted(names)))
    if type(got) is FrozenMap and len(got) == len(inputs):
        return got
    return {endpoint.name: got.get(endpoint.name) for endpoint in inputs}


class ProtocolFacts:
    """Facts from transitions the trusted driver has already evaluated.

    Standalone trace replay and runtime schedule validation use this same
    conversion. This object never accepts a simulator-supplied ledger.
    """

    def __init__(self, contract, program, *, retain_drives=False, retain=True, observer=None):
        self.contract, self.program = contract, program
        self.retain_drives = retain_drives
        self.retain, self.observer = retain, observer
        self.live = {endpoint.name: False for endpoint in contract.endpoints}
        self.slots, self.events, self.indexes = [], [], []

    def record(self, index, slot, before, drive, transition, record, pointers, *, boundary=None):
        checks, violations = self.program.artifact_events(self.contract, transition)
        environment_violations = self.program.environment_violations(
            self.contract, before, slot, drive)
        recorded = dict(record.accepted)
        channels = {}
        for endpoint in self.contract.endpoints:
            image = transition.channels[endpoint.name]
            transferred = recorded.get(endpoint.name) is not None
            payload = image.payload
            if getattr(endpoint.direction, "value", endpoint.direction) == "in" and transferred:
                payload = recorded[endpoint.name]
            started = bool(image.offered) and not self.live[endpoint.name]
            self.live[endpoint.name] = bool(image.offered) and not transferred
            # The transition already validated and froze this channel. Only
            # rebuild it when the recorded transfer or offer history changes it.
            if (transferred is image.transferred and started is image.started
                    and payload is image.payload):
                channels[endpoint.name] = image
            else:
                channels[endpoint.name] = AdapterChannel(
                    offered=image.offered, ready=image.ready,
                    transferred=transferred, payload=payload,
                    payload_readable=image.payload_readable,
                    protocol_readable=image.protocol_readable, started=started,
                    why_unreadable=image.why_unreadable)
        if self.observer is not None:
            from .ledger import SlotFact

            self.observer(SlotFact(index, slot.reset, channels, checks, violations,
                                   environment_violations))
        if self.retain:
            self.slots.append(AdapterReplaySlot(
                channels, checks, violations, environment_violations=environment_violations,
                presented_inputs={name: pointers[name] for name, payload in drive.presentations.items()
                                  if payload is not None},
                drive=drive if self.retain_drives else None, boundary=boundary))
            self.events.append(slot)
            self.indexes.append(index)

    def ledger(self, *, truncated=False):
        from .ledger import ledger_from_replay

        return ledger_from_replay(self.contract, self.events, self.slots,
                                  slot_indexes=self.indexes, truncated=truncated)


class AdapterProgram(Frozen):
    """A task-level executable protocol definition.

    Subclasses are stateless definitions.  Their returned ``state`` is the finite state
    of a particular run; the definition object itself is shared and immutable.
    """

    name = ""
    version = 0
    declared_monitor_state = ()
    monitor_goal_declarations = ()
    monitor_goals = ()
    request_response_correlation = False
    #: Whether the shared slot monitor's accept-before-retire ordering is this
    #: protocol's correlation semantics.  A protocol can correlate requests and
    #: responses while using a different within-slot order; its registered adapter
    #: transition then owns those obligations instead.
    generic_correlation_monitor = False
    stalled_offer_stability = False
    #: Whether the finite control state, transfer decisions and protocol goals depend
    #: only on state, reset, presentation presence and one-bit protocol controls.  An
    #: exact RTL product may compile such a program to a total transition table while
    #: carrying the actual payload terms through transfers unchanged.  The default is
    #: deliberately false: a new adapter gets no exact capability by inheritance.
    exact_control_product = False
    #: Versioned trusted declaration behind ``exact_control_product``.  The closed
    #: registry reviews built-in adapter code; a finite table compiled from one
    #: representative payload cannot itself prove that arbitrary payload values leave
    #: control unchanged.  Keeping that trust boundary in identity makes the claim
    #: explicit and prevents a new adapter from inheriting it accidentally.
    exact_control_product_policy = None
    # An algebra-parametric transition shared with concrete execution. Unlike
    # control tables this can include explicitly modelled payload predicates.
    exact_transition_policy = None
    #: The historical one-pair RTL product leaves slot actions symbolic.  Only an
    #: adapter which explicitly declares that encoding may use it; other finite
    #: adapters bind the concrete run's authenticated GlobalSchedule.
    exact_symbolic_schedule_product = False
    #: Logical mapping implemented by this program; unknown mappings never fall
    #: through to the direct one-payload-per-endpoint interpretation.
    transaction_mapping = "direct/1"
    physical_configuration = False

    __slots__ = ()

    def __init__(self):
        self._freeze()

    @property
    def identity(self):
        return self.name, self.version

    def validate(self, contract):  # pragma: no cover - abstract boundary
        raise NotImplementedError

    def initial_state(self, contract):  # pragma: no cover - abstract boundary
        raise NotImplementedError

    def state_space(self, contract):  # pragma: no cover - abstract boundary
        """The finite adapter states admitted for this contract."""
        raise NotImplementedError

    def environment_action_space(self, contract):
        """Every Boolean valuation of the adapter-owned environment pins."""
        import itertools

        names = tuple(sorted({name for endpoint in contract.endpoints
                              for name in endpoint.adapter.environment_controls}))
        return tuple(FrozenMap(zip(names, values))
                     for values in itertools.product((0, 1), repeat=len(names)))

    def components(self, contract):
        del contract
        return ()

    def monitor_goals_for(self, contract):
        """Adapter-owned goals applicable to this exact task declaration."""

        del contract
        return self.monitor_goals

    def monitor_goal_declarations_for(self, contract):
        """Ownership rows for :meth:`monitor_goals_for`."""

        del contract
        return self.monitor_goal_declarations

    def monitor_goal_owners_for(self, contract):
        """The closed ``goal -> (owner, role)`` declaration for this task."""

        return FrozenMap({
            goal: (owner, role)
            for goal, owner, role in self.monitor_goal_declarations_for(contract)
        })

    def monitor_goals_owned_by(self, contract, owner):
        """Declared protocol events owed by exactly ``owner``."""

        if owner not in (ENVIRONMENT, ARTIFACT):
            raise ValueError("an adapter goal owner is environment or artifact")
        return tuple(
            goal for goal, declared, _role
            in self.monitor_goal_declarations_for(contract)
            if declared == owner)

    def _events_owned_by(self, contract, events, owner, *, channel):
        """Validate and retain events from one side of the protocol boundary."""

        declarations = self.monitor_goal_owners_for(contract)
        out = []
        for event in events:
            goal = event if type(event) is str else event.goal
            declaration = declarations.get(goal)
            if declaration is None:
                raise Refused(Refusal(
                    "adapter-protocol-event-undeclared",
                    "the registered adapter emitted a protocol event outside its "
                    "task-specific declaration",
                    adapter=f"{self.name}/v{self.version}", goal=goal,
                    channel=channel))
            if declaration[0] != owner:
                raise Refused(Refusal(
                    "adapter-protocol-event-owner-mismatch",
                    "a protocol event crossed the environment/artifact ownership "
                    "boundary that determines trace admissibility",
                    adapter=f"{self.name}/v{self.version}", goal=goal,
                    declared_owner=declaration[0], emitted_by=owner,
                    channel=channel))
            out.append(event)
        return tuple(out)

    def environment_violations(self, contract, state, slot, drive):
        """Environment-owned assumption failures for one driven slot."""

        values = self.legal_environment_action(contract, state, slot, drive)
        if type(values) is not tuple or any(
                type(item) is not AdapterViolation for item in values):
            raise Refused(Refusal(
                "adapter-environment-event-shape-invalid",
                "the registered adapter returned an invalid environment-assumption "
                "event sequence",
                adapter=f"{self.name}/v{self.version}"))
        return self._events_owned_by(
            contract, values, ENVIRONMENT, channel="environment-action")

    def artifact_events(self, contract, transition):
        """Validate the artifact-owned checks and violations of one transition."""

        if type(transition) is not AdapterTransition:
            raise TypeError("adapter event validation needs an AdapterTransition")
        checks = self._events_owned_by(
            contract, transition.checks, ARTIFACT, channel="artifact-check")
        violations = self._events_owned_by(
            contract, transition.violations, ARTIFACT,
            channel="artifact-violation")
        return checks, violations

    def state_values(self, state):
        return dict(state)

    def outstanding_after_reset(self, contract, accepted, emitted):
        """The legacy trace counter after reset.

        Protocol state remains the authority.  This hook only keeps the historical
        ``monitor.outstanding`` projection coherent with that state without teaching
        :class:`Driver` a protocol name.
        """
        sources, _outputs = _directions(contract)
        if len(sources) == 1 and getattr(
                sources[0].reset_policy, "value", sources[0].reset_policy) == "keeps":
            return accepted, emitted
        return 0, 0

    def drive(self, contract, state, slot, presented, scheduled_controls):
        raise NotImplementedError  # pragma: no cover - abstract boundary

    def baseline_queue_range(self, endpoint, *, offers, samples):
        """Legal input depths without assuming a candidate's response latency.

        The baseline fixes the schedule and payloads before candidate execution.
        Adapters opt in with a minimum and maximum queue depth for that schedule.
        """
        raise Refused(Refusal(
            "generation-baseline-adapter-unsupported",
            "the adapter has no universally legal fixed baseline schedule",
            endpoint=endpoint.name, adapter=f"{self.name}/v{self.version}"))

    def requires_input_each_step(self, endpoint):
        """Whether an input cannot idle at its active sampling boundary."""
        return False

    def samples_every_active_edge(self, endpoint):
        """Whether every non-reset edge is an unconditional input/output record."""
        return False

    def receptive_controls(self, endpoint):
        """Scheduled active-high acceptance controls, if this adapter defines them."""
        if endpoint.adapter.environment_controls:
            raise Refused(Refusal(
                "adapter-receptive-control-unsupported",
                "this adapter has no declared receptive output action", endpoint=endpoint.name))
        return ()

    def presentation_allowed(self, contract, state, endpoint):
        """Whether this adapter may start or continue presenting ``endpoint``.

        Ready/valid can hold a pending input regardless of the receiver's ready bit.
        Stateful request protocols override this when their own finite state forbids a
        new presentation.
        """

        del contract, state, endpoint
        return True

    def legal_environment_action(self, contract, state, slot, drive):
        """Protocol violations caused by the environment action in this slot."""
        del contract, state, slot, drive
        return ()

    def step(self, contract, state, slot, drive, signals, *, strict=True):
        raise NotImplementedError  # pragma: no cover - abstract boundary

    def elapse(self, contract, state, slots=1):
        """Advance time without observing a protocol edge.

        ``slots`` counts scheduled macrosteps, not simulator phases or physical time.
        This transition produces no transfers, resets, or goal hits. Timed protocols
        retain an expired obligation until their next active-edge observation.
        Implementations must compose: elapse(a + b) = elapse(b) after elapse(a).
        """
        if type(slots) is not int or slots < 0:
            raise ValueError("elapsed slots must be an exact nonnegative integer")
        return self._elapse(contract, state, slots)

    def _elapse(self, contract, state, slots):
        return state

    def replay(self, contract, stimulus, trace, observed=None, *, slot_indexes=None,
               retain_drives=False, retain_boundaries=False):
        """Re-run this adapter over a trace using the same drive and transition rules.

        The trace's accepted batches remain the authority on which transfers actually
        crossed.  Re-execution recovers offer/ready facts, finite adapter state, and
        protocol events without maintaining a second interpretation in the monitor.
        """
        facts = ProtocolFacts(contract, self, retain_drives=retain_drives)
        for step in self.iter_replay(contract, stimulus, trace, observed,
                                     slot_indexes=slot_indexes):
            boundary = (AdapterReplayBoundary(contract.clocks[0].name if contract.clocks else '',
                frozen_map(step.state), step.queues, step.transition.state, step.successor_queues)
                if retain_boundaries else None)
            facts.record(step.index, step.slot, step.state, step.drive, step.transition,
                         step.record, step.queues.accepted, boundary=boundary)
        return tuple(facts.slots)

    def iter_replay(self, contract, stimulus, trace, observed=None, *, slot_indexes=None):
        """Stream the same transitions for scoring or query-boundary projection.

        Query selection needs queue positions and drives, not a second full
        channel/monitor ledger. Both consumers still execute these exact drive,
        transition, elapsed-time and recorded-acceptance rules.
        """
        inputs, _outputs = _directions(contract)
        state = self.initial_state(contract)
        queue_state = InputQueueState.initial(inputs)
        if observed is None:
            observed = len(trace.records)
        indexes = (tuple(range(len(stimulus.slots))) if slot_indexes is None
                   else tuple(slot_indexes))
        if len(indexes) != len(stimulus.slots) or any(
                type(index) is not int or index < 0 for index in indexes) or any(
                left >= right for left, right in pairwise(indexes)):
            raise ValueError(
                "a projected adapter needs one increasing global slot index per "
                "active local slot")
        previous = -1
        for index, slot, record in zip(
                indexes, stimulus.slots[:observed], trace.records[:observed]):
            state = self.elapse(contract, state, index - previous - 1)
            previous = index
            releasable = set() if slot.reset else {endpoint.name for endpoint in inputs}
            presentable = {
                endpoint.name for endpoint in inputs
                if not slot.reset and self.presentation_allowed(
                    contract, state, endpoint)
            }
            prepared = queue_state.prepare(
                stimulus, slot, releasable=releasable,
                presentable=presentable)
            presentations = dict(prepared.presentations)
            scheduled = dict(slot.controls)
            drive = self.drive(contract, state, slot, presentations, scheduled)
            transition = self.step(
                contract, state, slot, drive, dict(record.outputs), strict=False)
            recorded = dict(record.accepted)
            # Preserve held requests as well as control flags; a busy protocol's
            # queue release is not its first presentation or acceptance.
            after_queue = queue_state.commit(prepared, recorded)
            yield AdapterReplayStep(index, slot, record, state, queue_state,
                                    drive, transition, after_queue)
            queue_state = after_queue
            state = transition.state


class _IndependentEdgeChannels(AdapterProgram):
    """Stateless channel sampling shared by explicit handshake/sampling policies.

    Subclasses validate their control roles and declare capabilities independently.
    Empty sender/receiver controls mean an unconditional event/receptive receiver;
    they do not create physical handshake pins.
    """

    def initial_state(self, contract):
        del contract
        return frozen_map()

    def state_space(self, contract):
        del contract
        return (FrozenMap(),)

    def drive(self, contract, state, slot, presented, scheduled_controls):
        del state, slot
        controls = dict(scheduled_controls)
        presentations = _presentation_mapping(contract, presented)
        inputs, _outputs = _directions(contract)
        for endpoint in inputs:
            for signal in endpoint.adapter.environment_controls:
                controls[signal] = int(
                    presentations[endpoint.name] is not None)
        return AdapterDrive(controls, presentations)

    def step(self, contract, state, slot, drive, signals, *, strict=True):
        inputs, outputs = _directions(contract)
        controls = drive.controls
        accepted_inputs, emitted_outputs, channels = {}, {}, {}
        for source in inputs:
            if strict and not slot.reset:
                _require_bits(source, signals)
            presented = drive.presentation(source.name)
            offered = presented is not None and all(
                controls.get(name) == 1
                for name in source.adapter.environment_controls)
            ready_bits = [signals.get(name) for name
                          in source.adapter.artifact_controls]
            ready = (None if any(type(value) is not int or value not in (0, 1)
                                 for value in ready_bits)
                     else all(value == 1 for value in ready_bits))
            fired = offered and ready is True and not slot.reset
            if presented is not None and not slot.reset:
                accepted_inputs[source.name] = presented if fired else None
            channels[source.name] = AdapterChannel(
                offered=offered and not slot.reset, ready=ready,
                transferred=fired, payload=presented,
                protocol_readable=ready is not None or slot.reset)
        for sink in outputs:
            if strict and not slot.reset:
                _require_bits(sink, signals)
            valid_bits = [signals.get(name) for name
                          in sink.adapter.artifact_controls]
            offered = (None if any(type(value) is not int or value not in (0, 1)
                                   for value in valid_bits)
                       else all(value == 1 for value in valid_bits))
            ready = all(controls.get(name) == 1
                        for name in sink.adapter.environment_controls)
            fired = offered is True and ready and not slot.reset
            payload, readable, why = _payload(sink, signals)
            if fired and not readable:
                raise CandidateFault(Refusal(
                    "driver-output-unreadable",
                    "an output transferred and not every field came back as a "
                    "number, so the record would contain a transaction with a hole",
                    endpoint=sink.name,
                    fields=[f.port for f in sink.fields]))
            if fired:
                emitted_outputs[sink.name] = payload
            channels[sink.name] = AdapterChannel(
                offered=(False if slot.reset else offered), ready=ready,
                transferred=fired, payload=payload,
                payload_readable=readable,
                protocol_readable=offered is not None or slot.reset,
                why_unreadable=why)
        return AdapterTransition(
            state=state, accepted_inputs=accepted_inputs,
            emitted_outputs=emitted_outputs, channels=channels)


def _check_presentation_ownership(contract):
    presentations, scheduled = {}, {}
    for endpoint in contract.endpoints:
        owners = (presentations if getattr(endpoint.direction, "value", endpoint.direction) == "in"
                  else scheduled)
        for signal in endpoint.adapter.environment_controls:
            owners.setdefault(signal, []).append(endpoint.name)
    collisions = {signal: names for signal, names in presentations.items()
                  if len(names) > 1}
    if collisions:
        raise Refused(Refusal(
            "adapter-presentation-control-collision",
            "independent input endpoints cannot share a presentation-control pin",
            collisions=collisions))
    conflicts = sorted(presentations.keys() & scheduled.keys())
    if conflicts:
        raise Refused(Refusal(
            "adapter-environment-control-role-collision",
            "one pin cannot be controlled by both input presentation and output scheduling",
            signals=conflicts,
            presentations={name: presentations[name] for name in conflicts},
            scheduled={name: scheduled[name] for name in conflicts}))


class _ReadyValidV1(_IndependentEdgeChannels):
    name = READY_VALID
    version = 1
    declared_monitor_state = ("outstanding", "stall_cycles")
    request_response_correlation = True
    generic_correlation_monitor = True
    stalled_offer_stability = True
    exact_control_product = True
    exact_control_product_policy = "payload-presence-control/1"
    exact_symbolic_schedule_product = True

    def receptive_controls(self, endpoint):
        return endpoint.adapter.environment_controls

    def baseline_queue_range(self, endpoint, *, offers, samples):
        return 1, offers

    def validate(self, contract):
        for endpoint in contract.endpoints:
            if (len(endpoint.adapter.environment_controls) != 1
                    or len(endpoint.adapter.artifact_controls) != 1):
                raise Refused(Refusal(
                    "adapter-ready-valid-controls",
                    "ready-valid/v1 has exactly one sender-valid and one "
                    "receiver-ready pin per endpoint",
                    endpoint=endpoint.name,
                    environment=list(endpoint.adapter.environment_controls),
                    artifact=list(endpoint.adapter.artifact_controls)))
        _check_presentation_ownership(contract)


class _SampledEdgeV1(_IndependentEdgeChannels):
    """One sample at every non-reset declared active-clock boundary.

    With a sender-valid pin, only asserted samples are transactions. Without it,
    every active boundary is a transaction, including repeated equal values.
    A plain input therefore requires a supplied queue value at every such boundary.
    Sampling uses the common settled-before-clock boundary, never post-edge data.
    """

    name = SAMPLED_EDGE
    version = 1
    request_response_correlation = True
    generic_correlation_monitor = True
    exact_control_product = True
    exact_control_product_policy = "sampled-presence-control/1"

    def requires_input_each_step(self, endpoint):
        return not endpoint.adapter.environment_controls

    def samples_every_active_edge(self, endpoint):
        sender = (endpoint.adapter.environment_controls
                  if getattr(endpoint.direction, "value", endpoint.direction) == "in"
                  else endpoint.adapter.artifact_controls)
        return not sender

    def baseline_queue_range(self, endpoint, *, offers, samples):
        if endpoint.adapter.environment_controls:
            return 1, offers
        if offers != samples:
            raise Refused(Refusal(
                "generation-baseline-sampled-idle",
                "a plain input needs an offered payload at every non-reset edge; "
                "the fixed baseline schedule contains idle or drain edges",
                endpoint=endpoint.name, samples=samples, offers=offers))
        return samples, samples

    def validate(self, contract):
        for endpoint in contract.endpoints:
            adapter = endpoint.adapter
            is_input = getattr(endpoint.direction, "value", endpoint.direction) == "in"
            sender = adapter.environment_controls if is_input else adapter.artifact_controls
            receiver = adapter.artifact_controls if is_input else adapter.environment_controls
            if receiver or len(sender) > 1:
                raise Refused(Refusal(
                    "adapter-sampled-edge-controls",
                    "sampled-edge/v1 has zero or one sender-valid pin and no receiver controls",
                    endpoint=endpoint.name))
        _check_presentation_ownership(contract)

    def drive(self, contract, state, slot, presented, scheduled_controls):
        drive = super().drive(contract, state, slot, presented, scheduled_controls)
        if not slot.reset:
            for endpoint in contract.inputs():
                if not endpoint.adapter.environment_controls and drive.presentation(endpoint.name) is None:
                    raise Refused(Refusal(
                        "stimulus-sampled-input-missing",
                        "a plain input is sampled at every active edge; an idle slot "
                        "or exhausted queue cannot suppress that physical input event",
                        endpoint=endpoint.name))
        return drive


class _SettledV1(_SampledEdgeV1):
    """One plain-pin transaction per explicitly driven, settled test step."""

    name = SETTLED
    # The finite table samples plain pins without a clock or reset coordinate.
    exact_control_product = True
    exact_control_product_policy = "settled-payload-presence-control/1"

    def validate(self, contract):
        if contract.clocks or any(
                endpoint.clock is not None or endpoint.adapter.environment_controls
                or endpoint.adapter.artifact_controls for endpoint in contract.endpoints):
            raise Refused(Refusal(
                "adapter-settled-clock-or-control",
                "combinational-settled/v1 has plain pins and no DUT clock or handshake"))
        super().validate(contract)


class _StartDoneV1(AdapterProgram):
    name = START_DONE
    version = 1
    declared_monitor_state = ("age", "busy")
    request_response_correlation = True
    exact_control_product = True
    exact_control_product_policy = "payload-presence-control/1"
    START_WHILE_BUSY = "monitor.start-done.start-while-busy"
    DONE_WITHOUT_REQUEST = "monitor.start-done.done-without-outstanding"
    RESPONSE_DEADLINE = "monitor.response-deadline"
    monitor_goals = (START_WHILE_BUSY, DONE_WITHOUT_REQUEST)
    monitor_goal_declarations = (
        (START_WHILE_BUSY, ENVIRONMENT, "in"),
        (DONE_WITHOUT_REQUEST, ARTIFACT, "out"),
    )

    def baseline_queue_range(self, endpoint, *, offers, samples):
        # A second fixed request could overlap an arbitrarily slow first response.
        return 1, min(1, offers)

    def monitor_goals_for(self, contract):
        source = _directions(contract)[0][0]
        return self.monitor_goals + (
            (self.RESPONSE_DEADLINE,)
            if source.response_deadline is not None else ())

    def monitor_goal_declarations_for(self, contract):
        source = _directions(contract)[0][0]
        return self.monitor_goal_declarations + (
            ((self.RESPONSE_DEADLINE, ARTIFACT, "out"),)
            if source.response_deadline is not None else ())

    def validate(self, contract):
        inputs, outputs = _directions(contract)
        if len(inputs) != 1 or len(outputs) != 1:
            raise Refused(Refusal(
                "adapter-start-done-endpoints",
                "start-done/v1 is one request endpoint paired with one completion "
                "endpoint",
                inputs=len(inputs), outputs=len(outputs)))
        source, sink = inputs[0], outputs[0]
        if (len(source.adapter.environment_controls) != 1
                or source.adapter.artifact_controls
                or sink.adapter.environment_controls
                or len(sink.adapter.artifact_controls) != 1):
            raise Refused(Refusal(
                "adapter-start-done-controls",
                "start-done/v1 has one environment-owned start pin on the input and "
                "one artifact-owned done pin on the output; it has no ready pin and "
                "therefore invents no back-pressure",
                input_environment=list(source.adapter.environment_controls),
                input_artifact=list(source.adapter.artifact_controls),
                output_environment=list(sink.adapter.environment_controls),
                output_artifact=list(sink.adapter.artifact_controls)))
        if source.reset_policy is not sink.reset_policy:
            raise Refused(Refusal(
                "adapter-start-done-reset-policy",
                "start-done/v1 has one busy state for the request/response pair, so "
                "its two endpoints must declare the same reset rule",
                input=(None if source.reset_policy is None
                       else source.reset_policy.value),
                output=(None if sink.reset_policy is None
                        else sink.reset_policy.value)))
        if source.reset_policy is None:
            raise Refused(Refusal(
                "adapter-start-done-reset-policy-undeclared",
                "start-done/v1 must know whether reset clears or preserves its busy "
                "request; choosing either one here would add a run policy the task "
                "did not declare"))

    def initial_state(self, contract):
        del contract
        return FrozenMap({"adapter.age": 0, "adapter.busy": False})

    def state_space(self, contract):
        source = _directions(contract)[0][0]
        deadline = source.response_deadline
        ages = (0,) if deadline is None else tuple(range(deadline + 2))
        return (FrozenMap({"adapter.age": 0, "adapter.busy": False}),
                *(FrozenMap({"adapter.age": age, "adapter.busy": True})
                  for age in ages))

    def components(self, contract):
        del contract
        return (
            Component(
                "adapter.busy", "adapter",
                why="whether a start has been accepted and not completed by done"),
            Component(
                "adapter.age", "adapter",
                why="saturated scheduled-step age; deadline + 1 means reported"),
        )

    def _elapse(self, contract, state, slots):
        deadline = _directions(contract)[0][0].response_deadline
        age = state["adapter.age"]
        if deadline is None or not state["adapter.busy"] or age > deadline:
            return state
        # Inactive steps can expire an obligation but cannot observe/report it.
        # Reserve deadline + 1 for the active step that actually reports lateness.
        return FrozenMap({"adapter.age": min(age + slots, deadline),
                          "adapter.busy": True})

    def drive(self, contract, state, slot, presented, scheduled_controls):
        del state, slot
        controls = dict(scheduled_controls)
        source = _directions(contract)[0][0]
        presentations = _presentation_mapping(contract, presented)
        controls[source.adapter.environment_controls[0]] = int(
            presentations[source.name] is not None)
        return AdapterDrive(controls, presentations)

    def presentation_allowed(self, contract, state, endpoint):
        del contract, endpoint
        return not bool(state["adapter.busy"])

    def legal_environment_action(self, contract, state, slot, drive):
        source = _directions(contract)[0][0]
        start = dict(drive.controls).get(
            source.adapter.environment_controls[0]) == 1
        if start and bool(state["adapter.busy"]) and not slot.reset:
            return (AdapterViolation(
                self.START_WHILE_BUSY, source.name,
                "start was asserted while an earlier request was still busy"),)
        return ()

    def step(self, contract, state, slot, drive, signals, *, strict=True):
        inputs, outputs = _directions(contract)
        source, sink = inputs[0], outputs[0]
        # Before the reset edge, a sequential ``done`` register may still be X.  It is
        # not a protocol observation in a reset slot, so requiring a bit there would
        # reject an otherwise well-defined reset rather than execute it.
        if strict and not slot.reset:
            _require_bits(sink, signals)
        controls = dict(drive.controls)
        busy = bool(state["adapter.busy"])
        age = int(state["adapter.age"])
        start = controls.get(source.adapter.environment_controls[0]) == 1
        done_value = signals.get(sink.adapter.artifact_controls[0])
        done = (None if type(done_value) is not int or done_value not in (0, 1)
                else done_value == 1)
        checks, violations = [], []
        accepted_inputs, emitted_outputs = {}, {}

        if slot.reset:
            policy = getattr(source.reset_policy, "value", source.reset_policy)
            next_busy = busy if policy != "clears" else False
            next_age = (self.elapse(contract, state)["adapter.age"]
                        if policy != "clears" else 0)
            start = done = False
        else:
            if start:
                accepted_inputs[source.name] = (
                    drive.presentation(source.name) if not busy else None)
            if done is True:
                checks.append(self.DONE_WITHOUT_REQUEST)
                if not busy:
                    violations.append(AdapterViolation(
                        self.DONE_WITHOUT_REQUEST, sink.name,
                        "done was asserted with no previously accepted request "
                        "outstanding"))
            deadline = source.response_deadline
            if deadline is not None and busy:
                checks.append(self.RESPONSE_DEADLINE)
                # Age saturates at the deadline while awaiting observation; one past
                # it records that lateness was already reported. Check before done,
                # so a late completion cannot erase its violation.
                if age == deadline:
                    violations.append(AdapterViolation(
                        self.RESPONSE_DEADLINE, sink.name,
                        f"the outstanding start has gone at least {deadline + 1} slots "
                        f"without done, where the task declares {deadline}"))
            # Both events are interpreted against the state at slot entry.  A done
            # retires only an older request; a simultaneous start accepted from idle is
            # therefore the request left busy for the following slot.
            next_busy = ((busy and done is not True) or (start and not busy))
            next_age = (
                0 if not next_busy or not busy or deadline is None
                else min(age + 1, deadline + 1))

        payload, readable, why = _payload(sink, signals)
        if done is True and not readable:
            raise CandidateFault(Refusal(
                "driver-output-unreadable",
                "done emitted an output transaction whose payload was not fully "
                "readable",
                endpoint=sink.name, fields=[f.port for f in sink.fields]))
        if done is True:
            emitted_outputs[sink.name] = payload
        channels = {
            source.name: AdapterChannel(
                # Busy is the adapter's acceptance state, not a ready pin.  Leaving
                # ``ready`` unstated prevents downstream code from treating start/done
                # as ready/valid with an invented back-pressure signal.
                offered=start, ready=None, transferred=bool(start and not busy),
                payload=drive.presentation(source.name), protocol_readable=True),
            sink.name: AdapterChannel(
                offered=done, ready=None,
                transferred=(done is True), payload=payload,
                payload_readable=readable,
                protocol_readable=(done is not None or slot.reset),
                why_unreadable=why),
        }
        monitor = {"adapter.age": next_age,
                   "adapter.busy": int(next_busy),
                   "adapter.reset": int(slot.reset)}
        for goal in checks:
            monitor[f"adapter.checked:{goal}"] = 1
        for violation in violations:
            monitor[f"adapter.violation:{violation.goal}"] = violation.why
        return AdapterTransition(
            state={"adapter.age": next_age, "adapter.busy": next_busy},
            accepted_inputs=accepted_inputs, emitted_outputs=emitted_outputs,
            channels=channels, checks=tuple(checks), violations=tuple(violations),
            monitor=monitor)


_REGISTRY = {}


def _register(program):
    if not isinstance(program, AdapterProgram):
        raise TypeError("a registered adapter must implement AdapterProgram")
    if type(program.name) is not str or not program.name:
        raise ValueError("a registered adapter needs a nonempty name")
    if type(program.version) is not int or program.version < 1:
        raise ValueError("a registered adapter needs a positive integer version")
    if (type(program.declared_monitor_state) is not tuple
            or any(type(name) is not str or not name
                   for name in program.declared_monitor_state)
            or len(program.declared_monitor_state)
            != len(set(program.declared_monitor_state))):
        raise ValueError("a registered adapter has an invalid monitor-state schema")
    if (type(program.monitor_goals) is not tuple
            or any(type(goal) is not str or not goal
                   for goal in program.monitor_goals)
            or len(program.monitor_goals) != len(set(program.monitor_goals))):
        raise ValueError("a registered adapter has an invalid monitor-goal schema")
    if type(program.generic_correlation_monitor) is not bool:
        raise ValueError(
            "a registered adapter has an invalid generic-correlation capability")
    if type(program.exact_control_product) is not bool:
        raise ValueError(
            "a registered adapter has an invalid exact-control capability")
    policy = program.exact_control_product_policy
    if program.exact_control_product and (
            type(policy) is not str or not policy or "/" not in policy):
        raise ValueError(
            "an exact-control adapter needs a versioned payload-independence policy")
    if not program.exact_control_product and policy is not None:
        raise ValueError(
            "an adapter without exact control cannot name an exact-control policy")
    declarations = program.monitor_goal_declarations
    if (type(declarations) is not tuple
            or any(type(row) is not tuple or len(row) != 3
                   or any(type(item) is not str or not item for item in row)
                   for row in declarations)
            or tuple(row[0] for row in declarations) != program.monitor_goals
            or any(owner not in (ENVIRONMENT, ARTIFACT)
                   or role not in ("in", "out")
                   for _goal, owner, role in declarations)):
        raise ValueError(
            "a registered adapter's monitor goals need ordered ownership declarations")
    key = program.identity
    if key in _REGISTRY:
        raise RuntimeError(f"protocol adapter {key[0]}/v{key[1]} already registered")
    _REGISTRY[key] = program


def registered_adapter(name, version):
    """Return the exact registered adapter, or a typed capability refusal."""
    if type(name) is not str or not name:
        raise TypeError("an adapter lookup needs a nonempty string name")
    if type(version) is not int or version < 1:
        raise TypeError("an adapter lookup needs a positive integer version")
    key = name, version
    got = _REGISTRY.get(key)
    if got is None:
        raise Refused(Refusal(
            "admission-adapter-unsupported",
            "this endpoint names a protocol adapter version this build does not "
            "implement",
            adapter=key[0], version=key[1],
            supported=[f"{n}/v{v}" for n, v in sorted(_REGISTRY)]))
    return got


def supported_adapter_identities():
    """The immutable set of adapter identities implemented by this build."""
    return frozenset(_REGISTRY)


def adapter_program_for(contract):
    """Bind the one executable adapter identity used by ``contract``."""
    instances = getattr(contract, "protocol_instances", ())
    if len(instances) > 1:
        from .interface_product import InterfaceProduct

        if len(contract.clocks) != 1:
            raise Refused(Refusal(
                "admission-interface-clock-projection-required",
                "project a global event onto each active clock before composing interfaces"))
        product = InterfaceProduct(contract)
        product.monitor_goal_declarations_for(contract)
        return product
    identities = {(e.adapter.name, e.adapter.version) for e in contract.endpoints}
    if not identities:
        raise Refused(Refusal(
            "admission-adapter-combination-unsupported",
            "a transaction-protocol adapter needs at least one endpoint",
            adapters=[]))
    if len(identities) != 1:
        raise Refused(Refusal(
            "admission-adapter-combination-unsupported",
            "one request/response channel must use one protocol adapter identity on "
            "both endpoints; composing different endpoint protocols is not defined",
            adapters=[f"{n}/v{v}" for n, v in sorted(identities)]))
    name, version = next(iter(identities))
    program = registered_adapter(name, version)
    if instances and not program.physical_configuration and (
            instances[0].bindings or instances[0].max_wait_cycles is not None):
        raise Refused(Refusal(
            "admission-interface-physical-unsupported",
            "this adapter does not consume physical role or wait declarations",
            interface=instances[0].name, adapter=name))
    if instances and instances[0].mapping != program.transaction_mapping:
        raise Refused(Refusal(
            "admission-interface-mapping-unsupported",
            "the registered adapter does not implement this logical mapping",
            interface=instances[0].name, mapping=instances[0].mapping,
            supported=program.transaction_mapping))
    for endpoint in contract.endpoints:
        if endpoint.adapter.monitor_state != program.declared_monitor_state:
            raise Refused(Refusal(
                "admission-adapter-monitor-state-mismatch",
                "the contract's adapter metadata names a monitor state different "
                "from the executable state of this exact protocol version",
                endpoint=endpoint.name,
                declared=list(endpoint.adapter.monitor_state),
                executable=list(program.declared_monitor_state)))
    program.validate(contract)
    goals = program.monitor_goals_for(contract)
    declarations = program.monitor_goal_declarations_for(contract)
    if (type(goals) is not tuple
            or any(type(goal) is not str or not goal for goal in goals)
            or len(goals) != len(set(goals))
            or type(declarations) is not tuple
            or any(type(row) is not tuple or len(row) != 3
                   or any(type(item) is not str or not item for item in row)
                   for row in declarations)
            or tuple(row[0] for row in declarations) != goals
            or any(owner not in (ENVIRONMENT, ARTIFACT)
                   or role not in ("in", "out")
                   for _goal, owner, role in declarations)):
        raise Refused(Refusal(
            "admission-adapter-goal-schema-invalid",
            "the registered adapter produced an invalid task-specific goal schema",
            adapter=f"{program.name}/v{program.version}"))
    return program


_register(_ReadyValidV1())
_register(_SampledEdgeV1())
_register(_SettledV1())
_register(_StartDoneV1())

# Protocol implementations depend on the shared records above, not on Driver.
from .apb import APBAccessV1
from .axi_lite import AXILiteReadV1, AXILiteWriteV1

_register(APBAccessV1())
_register(AXILiteReadV1())
_register(AXILiteWriteV1())
