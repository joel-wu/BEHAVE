"""Immutable environment requests and the traces observed when they execute.

`Stimulus` carries endpoint queues and a global sequence of event slots: offers,
controls, reset and clock events. `AuditedTrace` binds that request to its initial
state, per-slot transfers, observations, goals, successor states and completion.
An offered transaction is not necessarily accepted; the trace records that distinction.

Empty slots remain part of the schedule. Fingerprints distinguish transaction values,
backpressure, reset, clock events and memory policy; `source` is provenance and does
not affect stimulus identity.
"""

from __future__ import annotations

import enum
import hashlib
import json

from .frozen import Frozen
from .hit_vector import HIT_VECTOR_ENCODING, HitVector, HitVectorCodec, _reached_union
from .record_value import (
    FrozenValueMap,
    _digest_json_projection,
    canonical_json,
    freeze_record_value,
    record_pairs,
    same_record_value,
    thaw_record_value,
)
from .refusal import Refusal, Refused

__all__ = [
    "HIT_VECTOR_ENCODING",
    "AuditedTrace",
    "Completion",
    "EventSlot",
    "HitVector",
    "SlotRecord",
    "Stimulus",
    "TraceDomainFields",
    "canonical_json",
    "contract_of_domain",
    "from_legacy",
    "leading_reset_slots",
    "trace_domain",
    "uses_global_events",
]


def uses_global_events(contract, stimulus):
    """Explicit event timing selects the global product, even for one clock."""
    return (len(contract.clocks) > 1 or stimulus.clock_schedule is not None
            or (stimulus.replay_policy is not None
                and bool(stimulus.replay_policy.clock_timings)))


def _strings(values, path):
    """A finite set of exact identifiers, sorted without silently deduplicating."""
    if type(values) not in (tuple, list):
        raise TypeError(f"{path} must be a finite tuple or list of strings")
    got = tuple(values)
    if any(type(value) is not str for value in got):
        raise TypeError(f"{path} must contain only exact strings")
    if len(got) != len(set(got)):
        raise ValueError(f"{path} names one member more than once")
    return tuple(sorted(got))


def _record(value, path, allowed):
    if type(value) is not dict:
        raise TypeError(f"{path} must be an exact record")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{path} contains unknown fields {unknown}")
    return value


def _queue_pairs(queues):
    """Freeze endpoint queues without changing a payload value's type."""
    items = record_pairs(queues, "stimulus.queues")
    out = []
    for name, txns in items:
        if type(txns) is not tuple:
            raise TypeError(f"stimulus.queues.{name} must be a finite sequence")
        frozen_txns = []
        for index, txn in enumerate(txns):
            if type(txn) is not tuple:
                raise TypeError(
                    f"stimulus.queues.{name}[{index}] must be a transaction sequence")
            frozen_txns.append(tuple(
                freeze_record_value(
                    value, f"stimulus.queues.{name}[{index}][{field}]")
                for field, value in enumerate(txn)))
        out.append((name, tuple(frozen_txns)))
    return tuple(out)


def _accepted_pairs(accepted):
    items = record_pairs(accepted, "slot-record.accepted")
    out = []
    for name, txn in items:
        if txn is None:
            out.append((name, None))
            continue
        if type(txn) is not tuple:
            raise TypeError(
                f"slot-record.accepted.{name} must be a transaction or null")
        out.append((name, tuple(
            freeze_record_value(
                value, f"slot-record.accepted.{name}[{field}]")
            for field, value in enumerate(txn))))
    return tuple(out)


def _wire_pairs(pairs, path):
    """Validate a serialized mapping without losing duplicate-key evidence."""
    # ``record_pairs`` validates the exact pair shape and duplicate keys.  Returning the
    # original carrier lets the owning constructor apply its field-specific checks.
    record_pairs(pairs, path)
    return pairs


def _plain_pairs(pairs):
    return [[key, thaw_record_value(value)] for key, value in pairs]


class Completion(enum.Enum):
    """Whether the run reached the end of the horizon, and how.

    Three states and not a boolean, because "the artifact failed at slot t and the
    remaining slots were padded by terminal stuttering" is neither of the other two: the
    record has `N` slots, which a reader needs, **and** the concrete completion predicate
    is false, which the score needs. Collapsing them is how analysis padding turns a
    failed concrete run into a passing one.
    """

    #: Every slot ran, the artifact was invoked at each, nothing terminated early.
    NORMAL = "normal"
    #: The artifact failed at some slot; the rest are padding and it was not invoked
    #: again. `scores` is false regardless of what the padded suffix looks like.
    PADDED = "padded"
    #: The run stopped and the remaining slots have no record at all. Cannot enter an
    #: audited analysis pool.
    INCOMPLETE = "incomplete"
    #: A checked transaction boundary in an unfinished runtime request. Never
    #: full-run or exact-analysis evidence; requires explicit window policy.
    CHECKPOINT = "checkpoint"

    @property
    def scores(self):
        """Whether a score may be computed from a run that ended this way."""
        return self is Completion.NORMAL


class EventSlot(Frozen):
    """One moment of the global schedule.

    ``offers`` are the endpoints the environment **intends** to present on here.
    Whether it can is the queue's business at run time: a schedule is written before the
    run, and under stalling nobody knows in advance how many transfers will have happened
    by this slot, so "offer in every slot" is how *keep presenting until they are taken*
    is written down.
    **Several at once is one slot**, not several: two endpoints firing together is a
    coincidence the design sees, and serialising it as two slots describes a different
    run. The same goes for ``ticks``, which are the clock domains that advance.

    ``resets`` names asserted domains in an explicit global schedule;
    ``reset`` is the implicit single-clock shorthand. ``controls`` are
    the environment-owned signals and their values for this slot --
    back-pressure above all. They are part of the slot rather than of a policy object
    because a policy says how they are *chosen* and a slot says what they *were*, and a
    replay needs the second.
    """

    __slots__ = ("controls", "offers", "reset", "resets", "ticks")

    def __init__(self, offers=(), controls=None, reset=False, ticks=(),
                 resets=()):
        self.offers = _strings(offers, "event-slot.offers")
        self.controls = record_pairs(controls, "event-slot.controls")
        if type(reset) is not bool:
            raise TypeError("event-slot.reset must be an exact Boolean")
        self.reset = reset
        self.ticks = _strings(ticks, "event-slot.ticks")
        #: Per-domain levels for explicit schedules, including one-domain runs.
        #: ``reset`` retains the implicit single-clock spelling and identity.
        self.resets = _strings(resets, "event-slot.resets")
        if self.reset and self.resets:
            raise Refused(Refusal(
                "stimulus-two-reset-authorities",
                "a slot cannot use both the single-domain reset shorthand and "
                "the per-domain reset set"))
        self._freeze()

    def as_dict(self):
        out = {"offers": list(self.offers),
               "controls": _plain_pairs(self.controls),
               "reset": self.reset, "ticks": list(self.ticks)}
        # Preserve the byte identity of every existing single-clock stimulus.
        if self.resets:
            out["resets"] = list(self.resets)
        return out

    @classmethod
    def from_dict(cls, d):
        d = _record(d, "event-slot", {
            "offers", "controls", "reset", "ticks", "resets"})
        return cls(offers=d.get("offers", ()),
                   controls=_wire_pairs(
                       d.get("controls", ()), "event-slot.controls"),
                   reset=d.get("reset", False), ticks=d.get("ticks", ()),
                   resets=d.get("resets", ()))

    def __eq__(self, other):
        return (type(other) is EventSlot and self.offers == other.offers
                and same_record_value(self.controls, other.controls)
                and self.reset == other.reset
                and self.ticks == other.ticks and self.resets == other.resets)

    def __hash__(self):
        return hash((self.offers, self.controls, self.reset, self.ticks,
                     self.resets))

    def __repr__(self):
        parts = []
        if self.offers:
            parts.append("+".join(self.offers))
        if self.reset:
            parts.append("reset")
        if self.resets:
            parts.append("reset " + "+".join(self.resets))
        if self.ticks:
            parts.append("tick " + "+".join(self.ticks))
        return f"EventSlot({', '.join(parts) or 'bottom'})"


class Stimulus(Frozen):
    """Endpoint queues and the global schedule that spends them.

    The queues hold the payloads; the slots say when each endpoint's head is offered.
    Keeping them apart is what lets a slot exist with nothing in it, and what makes
    "the same transactions under different back-pressure" two stimuli rather than one.
    """

    __slots__ = (
        "_fingerprint",
        "_identity_bytes",
        "clock_schedule",
        "initial_state",
        "memory_environment",
        "memory_policy",
        "queues",
        "replay_policy",
        "slots",
        "source",
    )

    def __init__(self, queues=None, slots=(), memory_policy=None,
                 initial_state=None, source="", clock_schedule=None,
                 memory_environment=None, replay_policy=None):
        if replay_policy is not None:
            from .runtime_schedule import ReplayPolicy

            if type(replay_policy) is not ReplayPolicy:
                raise TypeError("a runtime request needs an exact ReplayPolicy")
            if slots or clock_schedule is not None:
                raise ValueError("a runtime request cannot also fix its schedule")
        self.replay_policy = replay_policy
        self.queues = _queue_pairs(queues)
        if type(slots) not in (tuple, list):
            raise TypeError("stimulus.slots must be a finite tuple or list")
        self.slots = tuple(slots)
        if any(type(slot) is not EventSlot for slot in self.slots):
            raise TypeError("stimulus.slots must contain only exact EventSlot records")
        # A multi-clock stimulus needs the physical schedule that induced its event
        # slots.  Timestamps, period, phase, edge and reset polarity affect the run but
        # do not belong in ``EventSlot`` itself; carrying the exact ``GlobalSchedule``
        # here makes them part of the stimulus identity without changing any existing
        # single-clock record.
        if clock_schedule is not None:
            from .clock_schedule import GlobalSchedule

            if type(clock_schedule) is not GlobalSchedule:
                raise TypeError(
                    "stimulus.clock_schedule must be an exact GlobalSchedule or absent")
            scheduled = tuple(slot.event for slot in clock_schedule.slots)
            if scheduled != self.slots:
                raise Refused(Refusal(
                    "stimulus-schedule-slot-mismatch",
                    "the timestamped clock schedule and the canonical event-slot "
                    "sequence describe different runs",
                    schedule_slots=len(scheduled), slots=len(self.slots)))
        self.clock_schedule = clock_schedule
        if memory_environment is not None:
            from .memory import MemoryEnvironment

            if type(memory_environment) is not MemoryEnvironment:
                raise TypeError(
                    "stimulus.memory_environment must be an exact MemoryEnvironment")
        self.memory_environment = memory_environment
        #: How the memory environment answered. Part of the stimulus because a witness
        #: replayed under a different latency is a different run, and part of the
        #: fingerprint for the same reason.
        self.memory_policy = record_pairs(memory_policy, "stimulus.memory-policy")
        #: What the design started in, as a policy rather than as a state: "power-up",
        #: "after the recorded reset segment", and so on. The state itself is the
        #: `AuditedTrace`'s, since it is a fact about the run.
        self.initial_state = record_pairs(initial_state, "stimulus.initial-state")
        #: Provenance. Outside the fingerprint: where a stimulus came from is not what it
        #: does, and two searches finding one stimulus must deduplicate to one.
        if type(source) is not str:
            raise TypeError("stimulus.source must be an exact string")
        self.source = source
        self._check()
        # Eager, for the reason `TaskContract` takes its digest eagerly: a
        # fingerprint filled in on first read is a field written after
        # construction, and a record whose identity can be taken before a
        # change and kept after it is not an identity.
        body = (self._image(shared_slots=True) if type(self) is Stimulus
                else dict(self.as_dict()))
        body.pop("source")
        self._identity_bytes = canonical_json(body).encode("utf-8")
        self._fingerprint = hashlib.sha256(self._identity_bytes).hexdigest()
        self._freeze()

    def _check(self):
        """Refuse a stimulus that cannot be run, rather than discovering it mid-replay.

        **What this cannot check is the queue depth**, and getting that wrong the first
        time is what this comment is for. An earlier version counted one queue item per
        *offer* and refused when the offers outran the queue. That is the wrong model of
        ready/valid: while `valid && !ready` the driver holds the **same** transaction on
        the bus, cycle after cycle, so one transaction is offered in many slots and the
        offers legitimately outnumber the queue several times over. Under that rule a
        stalled handshake could not be written down at all.

        The pointer advances on **transfer**, not on offer, and whether a transfer
        happened is a fact about the run. So the depth check belongs to `AuditedTrace`,
        which knows what was accepted, and what is left here is the one thing a stimulus
        can answer alone: an offer names an endpoint that has a queue.
        """
        names = {name for name, _ in self.queues}
        for i, slot in enumerate(self.slots):
            for endpoint in slot.offers:
                if endpoint not in names:
                    raise Refused(Refusal(
                        "stimulus-unknown-endpoint",
                        "a slot offers from an endpoint this stimulus has no queue for, "
                        "so there is no transaction for it to present",
                        slot=i, endpoint=endpoint, endpoints=sorted(names)))

    def faults_against(self, contract, *, require_memory_environment=True):
        """Every way this stimulus does not describe a run of ``contract``.

        `_check` can only ask what a stimulus can answer alone -- that an offer names an
        endpoint it has a queue for. Everything else that makes a stimulus runnable is
        relative to the task: whether that endpoint is one the environment drives at all,
        whether the values fit the fields' declared types, whether the controls are the
        ones the contract calls the environment's, whether a tick names a clock domain
        that exists.

        Reported as a list rather than raised one at a time. A stimulus with four faults
        should say so once: fixing them one refusal per run is how a caller comes to
        believe the last one was the only one.
        """
        out = []
        declared = {e.name: e for e in contract.endpoints}
        inputs = {e.name for e in contract.inputs()}
        # The controls a schedule names, which excludes an input's own `valid`:
        # `offers` decides that, and two authorities over one pin is how a slot
        # came to record a value the run did not drive.
        controls = set(contract.scheduled_controls())
        clocks = {c.name for c in contract.clocks}
        global_events = uses_global_events(contract, self)

        timings = (self.clock_schedule.timings if self.clock_schedule is not None else
                   self.replay_policy.clock_timings if self.replay_policy is not None else ())
        if len(contract.clocks) > 1 and not timings:
            out.append(
                "a multi-clock stimulus carries no physical clock schedule, so its "
                "event slots have no declared timestamps, periods, phases or edges")
        if timings:
            timing_by_domain = {timing.name: timing
                                for timing in timings}
            contract_by_domain = {clock.name: clock for clock in contract.clocks}
            if set(timing_by_domain) != set(contract_by_domain):
                out.append(
                    "the clock schedule and contract name different domain sets")
            for name in sorted(set(timing_by_domain) & set(contract_by_domain)):
                timing, domain = timing_by_domain[name], contract_by_domain[name]
                if (timing.clock != domain.clock
                        or timing.reset != domain.reset
                        or timing.reset_active_low != domain.reset_active_low
                        or timing.edge != domain.edge):
                    out.append(
                        f"clock schedule domain {name!r} binds a different clock, "
                        "active edge, reset or reset polarity than the contract")
        if contract.memories:
            if self.memory_environment is None:
                if require_memory_environment:
                    out.append(
                        "the task declares external memory but this stimulus carries no "
                        "initialization or actual finite ready schedule")
            else:
                out.extend(self.memory_environment.faults_against(
                    contract.memories, None if self.replay_policy is not None else len(self.slots)))
        elif self.memory_environment is not None and self.memory_environment.runs:
            out.append(
                "the stimulus carries an external-memory environment for a task that "
                "declares no external memory")

        for name, txns in self.queues:
            endpoint = declared.get(name)
            if endpoint is None:
                out.append(f"queue {name!r} is not an endpoint this contract declares")
                continue
            if name not in inputs:
                out.append(f"queue {name!r} is on an endpoint the artifact drives, and "
                           f"the environment has nothing to offer there")
                continue
            for i, payload in enumerate(txns):
                bad = endpoint.admits(payload)
                if bad:
                    out.append(f"{name}[{i}]: {'; '.join(bad)}")

        for i, slot in enumerate(self.slots):
            if not clocks and slot.reset:
                out.append(f"slot {i} requests reset for a clockless settled interface")
            for endpoint in slot.offers:
                if endpoint not in inputs:
                    out.append(f"slot {i} offers on {endpoint!r}, which is not an input "
                               f"endpoint of this contract")
                declared_endpoint = declared.get(endpoint)
                # Timestamped slots name every active edge explicitly. Only an
                # implicit one-clock stimulus advances on every ordinary slot.
                if (global_events
                        and declared_endpoint is not None
                        and declared_endpoint.clock not in slot.ticks):
                    out.append(
                        f"slot {i} offers on {endpoint!r} without an active edge in "
                        f"its clock domain {declared_endpoint.clock!r}")
            named = {k for k, _ in slot.controls}
            missing = sorted(controls - named)
            if missing:
                out.append(f"slot {i} does not name {missing}, and a control nobody "
                           f"names is one the run chooses")
            extra = sorted(named - controls)
            if extra:
                presented = set(contract.presented_controls())
                for name in extra:
                    if name in presented:
                        out.append(
                            f"slot {i} names {name!r}, which an input endpoint's "
                            f"presentation decides: `offers` says whether a transaction "
                            f"is put on the bus and the driver drives it from that. A "
                            f"slot carrying it too is a second authority over one pin, "
                            f"and the two disagreed on every idle slot")
                    else:
                        out.append(
                            f"slot {i} names {name!r}, which the contract does not call "
                            f"the environment's")
            # **One bit, and it is a bit.** `int(...)` took whatever was there, so a
            # slot could carry `out_ready = 2` and the driver would put a 2 on a
            # one-bit pin -- and the fingerprint would be over a value no run drove.
            # Every control this build schedules is a handshake or a reset line.
            for name, value in slot.controls:
                if name in controls and (type(value) is not int
                                         or value not in (0, 1)):
                    out.append(f"slot {i} drives {name}={value!r}, and every control "
                               f"this build schedules is one bit. A value outside "
                               f"{{0, 1}} is not something the environment can put on "
                               f"the pin, so the record would be of a run nobody ran")
            # **The flag and the pin say the same thing, or the stimulus means two
            # things.** `slot.reset` is what a Python device reads to clear its state
            # and what `Driver.present` reads to hold the bus idle; the pin is what an
            # RTL testbench drives. A slot with `reset = False` and `rst` asserted --
            # which is what copying a previous slot's controls into an idle tail
            # produced -- makes one stimulus mean one thing in one backend and another
            # in the other.
            #
            # Implicit slots use one reset flag. Explicit schedules use the
            # asserted-domain set instead, independently of their clock count.
            if len(contract.clocks) == 1 and not global_events:
                domain = contract.clocks[0]
                asserted = 0 if domain.reset_active_low else 1
                driven = dict(slot.controls).get(domain.reset)
                if driven is not None and (driven == asserted) != bool(slot.reset):
                    out.append(
                        f"slot {i} is marked reset={slot.reset} and drives "
                        f"{domain.reset}={driven}, which for a reset "
                        f"{'active low' if domain.reset_active_low else 'active high'} "
                        f"is the other thing. A device reading the flag and a testbench "
                        f"reading the pin would run two different stimuli")
            for tick in slot.ticks:
                if tick not in clocks:
                    out.append(f"slot {i} ticks {tick!r}, which is not a clock domain "
                               f"this contract declares")
            for reset_domain in slot.resets:
                if reset_domain not in clocks:
                    out.append(
                        f"slot {i} resets {reset_domain!r}, which is not a clock "
                        f"domain this contract declares")
            if global_events:
                driven = dict(slot.controls)
                for domain in contract.clocks:
                    asserted = 0 if domain.reset_active_low else 1
                    value = driven.get(domain.reset)
                    if value is not None and ((value == asserted)
                                              != (domain.name in slot.resets)):
                        out.append(
                            f"slot {i} names reset domains {list(slot.resets)} but "
                            f"drives {domain.reset}={value}; for domain {domain.name!r} "
                            "those describe different reset levels")
        return out

    def depth(self, endpoint):
        """How many transactions this endpoint's queue holds."""
        for name, txns in self.queues:
            if name == endpoint:
                return len(txns)
        return 0

    def offered(self, endpoint=None):
        """Count scheduled queue-release actions, in total or on one endpoint.

        Holding a pending row through back-pressure does not add another action.
        This is neither queue depth nor accepted-transfer count: reset/inactive
        slots and exhausted queues can prevent a scheduled action from releasing
        work. An unscheduled runtime stimulus has no such actions yet. Accepted
        transfers are recorded by `AuditedTrace`, not inferred from this count.
        """
        return sum(len(s.offers) if endpoint is None else (endpoint in s.offers)
                   for s in self.slots)

    def head(self, endpoint, accepted_before):
        """The transaction this endpoint presents, given how many of its queue went.

        The driver's pointer. It takes the accepted count rather than the slot index
        because that is what moves it: under ready/valid the head stays on the bus until
        it is taken, so the same transaction is the answer for every slot of a stall.

        `None` when the queue is drained. `InputQueueState` and the protocol adapter
        determine whether this row has been released and may currently be presented.
        """
        for name, txns in self.queues:
            if name == endpoint:
                return txns[accepted_before] if accepted_before < len(txns) else None
        return None

    def as_dict(self):
        return self._image(shared_slots=False)

    def _image(self, *, shared_slots):
        # Public editable images keep independent rows. Internal encoding can
        # share a consecutive immutable EventSlot's projection without changing
        # the canonical bytes or the number/order of cycles.
        slots = []
        previous = None
        for slot in self.slots:
            slots.append(slots[-1] if shared_slots and slot is previous
                         else slot.as_dict())
            previous = slot
        out = {"queues": [[name, [[thaw_record_value(v) for v in t]
                                   for t in txns]]
                          for name, txns in self.queues],
               "slots": slots,
               "memory_policy": _plain_pairs(self.memory_policy),
               "initial_state": _plain_pairs(self.initial_state),
               "source": self.source}
        if self.replay_policy is not None:
            out["replay_policy"] = self.replay_policy.as_dict()
        if self.memory_environment is not None:
            out["memory_environment"] = self.memory_environment.as_dict()
        # Preserve every existing single-clock serialized byte.  The field is mandatory
        # only for a multi-clock contract, where ``faults_against`` enforces it.
        if self.clock_schedule is not None:
            out["clock_schedule"] = self.clock_schedule.as_dict()
        return out

    @classmethod
    def from_dict(cls, d):
        d = _record(d, "stimulus", {
            "queues", "slots", "memory_policy", "initial_state", "source",
            "clock_schedule", "memory_environment", "replay_policy"})
        replay_policy = d.get("replay_policy")
        if replay_policy is not None:
            from .runtime_schedule import ReplayPolicy

            replay_policy = ReplayPolicy.from_dict(replay_policy)
        slots = d.get("slots", ())
        if type(slots) not in (tuple, list):
            raise TypeError("stimulus.slots must be a finite tuple or list on the wire")
        schedule = d.get("clock_schedule")
        if schedule is not None:
            from .clock_schedule import GlobalSchedule

            schedule = GlobalSchedule.from_dict(schedule)
        memory_environment = d.get("memory_environment")
        if memory_environment is not None:
            from .memory import MemoryEnvironment

            memory_environment = MemoryEnvironment.from_dict(memory_environment)
        from .record_value import RecordRowDecoder

        # Trace rows may reference any earlier slot, not only the preceding one.
        # Every position survives; each distinct owned row takes the full parser.
        decode_slot = RecordRowDecoder(EventSlot.from_dict)
        parsed_slots = [decode_slot(row) for row in slots]
        return cls(queues=_wire_pairs(
                       d.get("queues", ()), "stimulus.queues"),
                   slots=parsed_slots,
                   memory_policy=_wire_pairs(
                       d.get("memory_policy", ()), "stimulus.memory-policy"),
                   initial_state=_wire_pairs(
                       d.get("initial_state", ()), "stimulus.initial-state"),
                   source=d.get("source", ""), clock_schedule=schedule,
                   memory_environment=memory_environment, replay_policy=replay_policy)

    @property
    def identity_bytes(self) -> bytes:
        """Canonical semantic bytes, excluding provenance, frozen at construction.

        Byte-keyed deduplication reuses the same representation that was hashed;
        it does not replace exact byte comparisons with digest comparisons.
        ``as_dict`` still returns a fresh, editable public record.
        """
        return self._identity_bytes

    @property
    def fingerprint(self):
        """Everything the run depends on, and nothing else.

        `source` is excluded deliberately: the same stimulus reached by a random draw and
        by a solver is one stimulus, and a fingerprint that separated them would replay it
        twice and count it twice.
        """
        return self._fingerprint

    def __eq__(self, other):
        return type(other) is Stimulus and self.fingerprint == other.fingerprint

    def __hash__(self):
        return hash(self.fingerprint)

    def __repr__(self):
        return (f"Stimulus({len(self.slots)} slots, {self.offered()} offered, "
                f"{self.fingerprint[:8]})")


class SlotRecord(Frozen):
    """What one slot actually did.

    ``accepted`` is per endpoint and is **bottom for an endpoint that offered and was not
    taken**, which is the fact a row list cannot hold. ``monitor`` is the finite monitor's
    state after this slot, carried because a replay that does not reproduce it has not
    reproduced the run.  Against a bound goal catalogue, true and unreadable hit bits
    are explicit and every remaining goal is false.  Large structural catalogues use a
    catalogue-indexed
    :class:`HitVector`, so dense and sparse vectors have the same bounded wire size.
    """

    __slots__ = (
        "accepted", "environment", "hits", "monitor", "outputs", "state",
    )

    def __init__(self, accepted=None, state=None, outputs=None, hits=None,
                 monitor=None, environment=None):
        self.accepted = _accepted_pairs(accepted)
        self.state = record_pairs(state, "slot-record.state")
        # State owned by the closed-loop environment rather than by the artifact or a
        # goal monitor.  External-memory cells, pending reads and presented responses
        # belong here: omitting them makes two closed-loop positions with identical RTL
        # registers but different future responses look like one trace state. A tagged
        # memory successor encodes that state relative to the stimulus and preceding
        # records; core.memory_trace decodes the complete state without discarding cells.
        self.environment = record_pairs(environment, "slot-record.environment")
        self.outputs = record_pairs(outputs, "slot-record.outputs")
        self.hits = (hits if type(hits) is HitVector
                     else record_pairs(hits, "slot-record.hits"))
        self.monitor = record_pairs(monitor, "slot-record.monitor")
        self._freeze()

    @property
    def transfers(self):
        """Endpoints whose offer was taken here. The score's unit."""
        return tuple(name for name, txn in self.accepted if txn is not None)

    def _fields(self, *, compact_hits=False):
        """One wire schema, retaining immutable values until a view is needed."""
        if type(self.hits) is HitVector:
            hits = (self.hits.as_compact_dict() if compact_hits
                    else self.hits.as_dict())
        else:
            if compact_hits:
                raise ValueError(
                    "only a catalogue-bound hit vector has a compact wire image")
            hits = self.hits
        return {"accepted": self.accepted,
                "state": self.state,
                "environment": self.environment,
                "outputs": self.outputs,
                "hits": hits,
                "monitor": self.monitor}

    def as_dict(self, *, compact_hits=False):
        return {name: (value if name == "hits" and type(self.hits) is HitVector
                       else _plain_pairs(value))
                for name, value in self._fields(compact_hits=compact_hits).items()}

    @classmethod
    def from_dict(cls, d, *, hit_catalogue=None):
        d = _record(d, "slot-record", {
            "accepted", "state", "environment", "outputs", "hits", "monitor"})
        raw_hits = d.get("hits", ())
        if type(raw_hits) is dict:
            hits = (HitVector.from_dict(raw_hits) if hit_catalogue is None
                    else (hit_catalogue if type(hit_catalogue) is HitVectorCodec
                          else HitVectorCodec(hit_catalogue)).decode(raw_hits))
        else:
            if hit_catalogue is not None:
                raise TypeError(
                    "a catalogue-bound trace slot must carry a compact hit vector")
            hits = (raw_hits if cls is SlotRecord
                    else _wire_pairs(raw_hits, "slot-record.hits"))
        fields = {name: d.get(name, ()) for name in (
            "accepted", "state", "environment", "outputs", "monitor")}
        # The exact constructor consumes these original pair rows directly: its
        # record_pairs boundary rejects duplicates and detaches every value once.
        # No dict conversion may erase duplicates first. Keep the wire checks
        # for subclasses whose constructors need not implement that boundary.
        if cls is not SlotRecord:
            fields = {name: _wire_pairs(value, f"slot-record.{name}")
                      for name, value in fields.items()}
        return cls(hits=hits, **fields)

    def __eq__(self, other):
        if type(other) is not SlotRecord:
            return False
        pairs_equal = all(same_record_value(left, right) for left, right in (
                (self.accepted, other.accepted),
                (self.state, other.state),
                (self.environment, other.environment),
                (self.outputs, other.outputs),
                (self.monitor, other.monitor),
            ))
        if not pairs_equal:
            return False
        if type(self.hits) is HitVector and type(other.hits) is HitVector:
            return self.hits == other.hits
        if type(self.hits) is HitVector or type(other.hits) is HitVector:
            return same_record_value(tuple(self.hits), tuple(other.hits))
        return same_record_value(self.hits, other.hits)


def validate_resolved_request(stimulus, request):
    """A runtime resolution may add slots, never change inputs or environment."""
    if type(request) is not Stimulus or request.replay_policy is None:
        raise TypeError("a trace request needs an exact runtime stimulus")
    if type(stimulus) is not Stimulus or stimulus.replay_policy is not None:
        raise TypeError("a resolved request needs an executed fixed schedule")
    expected = request.as_dict()
    expected.pop("replay_policy")
    if request.replay_policy.clock_timings:
        if stimulus.clock_schedule is None or (
                [timing.as_dict() for timing in stimulus.clock_schedule.timings] !=
                [timing.as_dict() for timing in request.replay_policy.clock_timings]):
            raise ValueError("the resolved clock timings differ from the runtime request")
        expected["clock_schedule"] = stimulus.clock_schedule.as_dict()
    if request.memory_environment is not None:
        expected["memory_environment"] = request.memory_environment.materialize(
            len(stimulus.slots)).as_dict()
    actual = stimulus.as_dict()
    for row in (expected, actual):
        row.pop("slots")
        row.pop("source")
    if expected != actual:
        raise ValueError("the resolved schedule changed its request's inputs or initial environment")


class AuditedTrace(Frozen):
    """One stimulus, run, with a record for every slot of it.

    A record per slot and not per transfer. The horizon is the stimulus's length, and a
    trace that stops when the expected outputs first run dry has not observed the drain
    -- which is where a missing terminal response lives.
    """

    __slots__ = (
        "_accepted_counts",
        "_fingerprint",
        "_goal_summary",
        "artifact",
        "completion",
        "domain",
        "initial_state",
        "query_boundaries",
        "initialization",
        "records",
        "request",
        "source_evidence",
        "stimulus",
    )

    def __init__(self, stimulus, initial_state=None, records=(),
                 completion=Completion.NORMAL, domain="", artifact="",
                 source_evidence=None, request=None, query_boundaries=None,
                 initialization=None):
        if type(stimulus) is not Stimulus:
            raise TypeError("an audited trace needs an exact Stimulus record")
        if stimulus.replay_policy is not None:
            raise ValueError("a trace needs the actual schedule, not an unexecuted request")
        if request is not None:
            validate_resolved_request(stimulus, request)
        self.request = request
        self.stimulus = stimulus
        self.initial_state = record_pairs(initial_state, "audited-trace.initial-state")
        # Frontend-owned replay initialization, distinct from the observed state
        # after startup. Preserve its identity; the exact adapter checks its schema
        # and membership. Ordinary runs omit it and keep their existing identity.
        if initialization is not None and type(initialization) is not dict:
            raise TypeError("trace initialization must be an exact record or None")
        self.initialization = freeze_record_value(initialization)
        if type(records) not in (tuple, list):
            raise TypeError("audited-trace.records must be a finite tuple or list")
        self.records = tuple(records)
        if any(type(record) is not SlotRecord for record in self.records):
            raise TypeError(
                "audited-trace.records must contain only exact SlotRecord records")
        vectors = tuple(record.hits for record in self.records
                        if type(record.hits) is HitVector)
        if vectors and len(vectors) != len(self.records):
            raise Refused(Refusal(
                "trace-hit-vector-representation-mixed",
                "one trace cannot mix catalogue-bound and unbound slot hit records"))
        if vectors and any(vector.catalogue != vectors[0].catalogue
                           for vector in vectors[1:]):
            raise Refused(Refusal(
                "trace-hit-vector-catalogue-mismatch",
                "every slot of one trace must use the same frozen goal catalogue"))
        if type(completion) is not Completion:
            raise TypeError("audited-trace.completion must be an exact Completion")
        self.completion = completion
        #: The fingerprint of the domain this ran under. A record compared against one
        #: taken under another domain is two runs, and the fingerprint is what says so.
        if type(domain) is not str:
            raise TypeError("audited-trace.domain must be an exact string")
        self.domain = domain
        #: **Which artifact produced it.** A trace said what stimulus it was of and what
        #: contract it ran under, and nothing about the design. So admitting artifact A,
        #: running artifact B, and filing B's trace under A's task passed every check
        #: there was -- and every refusal admission had made was about something that was
        #: not under test. Empty where the runner cannot say, which the checks treat as
        #: unverifiable rather than as agreement.
        if type(artifact) is not str:
            raise TypeError("audited-trace.artifact must be an exact string")
        self.artifact = artifact
        # Opaque to the protocol product, but durable and fingerprinted. The
        # source adapter validates its closed observation schema and binding;
        # carrying a record alone is not an execution certificate.
        if source_evidence is not None and type(source_evidence) is not dict:
            raise TypeError("source evidence must be an exact record or None")
        self.source_evidence = freeze_record_value(source_evidence)
        # Ancillary native values for local query construction, separate from
        # score evidence and reset-owned state. The querying frontend binds them.
        self.query_boundaries = freeze_record_value(query_boundaries)
        if (self.completion is not Completion.INCOMPLETE
                and len(self.records) != len(stimulus.slots)):
            raise Refused(Refusal(
                "trace-slot-count",
                "a complete trace has one record per slot of its stimulus; anything "
                "shorter is a different run and cannot be compared with this one",
                slots=len(stimulus.slots), records=len(self.records),
                completion=self.completion.value))
        self._check_transfers()
        # Some selection traces never need an identity. Hash lazily, once per
        # immutable record, without retaining a second serialized trace body.
        self._fingerprint = None
        self._accepted_counts = None
        self._goal_summary = None
        self._freeze()

    @property
    def goal_summary(self):
        """Recorded names and reached names, derived once from immutable slots.

        Recorded names include false and unreadable goals for catalogue checks.
        This is raw evidence: the caller still determines observation eligibility.
        The summary is not serialized and cannot replace per-slot evidence.
        """
        if self._goal_summary is None:
            if self.records and type(self.records[0].hits) is HitVector:
                names = frozenset(self.records[0].hits.catalogue)
                reached = _reached_union(record.hits for record in self.records)
            else:
                names, reached = set(), set()
                for record in self.records:
                    for name, value in record.hits:
                        names.add(name)
                        if value:
                            reached.add(name)
                names, reached = frozenset(names), frozenset(reached)
            object.__setattr__(self, "_goal_summary", (names, reached))
        return self._goal_summary

    @property
    def observed_count(self) -> int:
        """Executed records before terminal padding, excluding padding itself.

        Padding preserves the declared horizon; it does not represent another
        invocation or advance a monitor. Short incomplete traces end at their
        last available record.
        """
        for index, record in enumerate(self.records):
            if any(name == "padding" and value for name, value in record.monitor):
                return index
        return len(self.records)

    def _check_transfers(self):
        """Every accepted payload is the oldest released, unaccepted queue entry.

        This is the depth check the `Stimulus` cannot make. The pointer advances on
        transfer, so how far into a queue a run got is a fact about the run, and only a
        record can be held against it. Three ways a record can fail to describe a run of
        its stimulus:

        * it accepts a payload that is not the head the pointer was on, which is a record
          of some other run;
        * it accepts more from an endpoint than its queue holds, which is a driver
          inventing transactions;
        * it accepts an entry the schedule has not released.  A transfer need not share
          a slot with its release: once released, ready/valid keeps that entry presented
          through stalls and through the drain.
        """
        # Only the endpoints the **environment** drives, which are the ones with queues.
        # An output endpoint's transfer is the artifact sending: there is no queue behind
        # it, no pointer to advance and no offer to have made, so holding it to the
        # driver's rules reports every well-formed output as a transfer with nothing on
        # the bus.
        from .input_queue import InputQueueState

        driven = {name for name, _ in self.stimulus.queues}
        queue_state = InputQueueState.initial(sorted(driven))
        for i, (slot, record) in enumerate(zip(self.stimulus.slots, self.records)):
            eligible = () if slot.reset else driven
            prepared = queue_state.prepare(
                self.stimulus, slot, releasable=eligible,
                presentable=eligible)
            for endpoint, payload in record.accepted:
                if payload is None or endpoint not in driven:
                    continue
                want = prepared.presentations[endpoint]
                if want is None:
                    exhausted = (
                        queue_state.accepted[endpoint]
                        >= self.stimulus.depth(endpoint)
                    )
                    raise Refused(Refusal(
                        ("trace-transfer-not-the-head" if exhausted else
                         "trace-transfer-without-released-input"),
                        ("this slot records a transfer after the driver's pointer "
                         "has passed the end of that endpoint's queue" if exhausted
                         else "this slot records a transfer but the schedule has "
                         "released no unaccepted queue entry for that endpoint"),
                        slot=i, endpoint=endpoint,
                        index=queue_state.accepted[endpoint]))
                if not same_record_value(payload, want):
                    raise Refused(Refusal(
                        "trace-transfer-not-the-head",
                        "this slot records a transfer of a payload that is not the one "
                        "the driver's pointer was on, so the record describes some "
                        "other run",
                        slot=i, endpoint=endpoint, accepted=list(payload),
                        head=list(want), index=queue_state.accepted[endpoint]))
            queue_state = queue_state.commit(prepared, dict(record.accepted))

    def accepted_count(self, endpoints=None):
        """Accepted transfers, optionally restricted to the input endpoints.

        The score's transaction count. Reset, ticks, all-bottom slots, output checks and
        produced outputs contribute nothing, which is what makes this different from the
        `n_drives + n_checks` the multi-clock path reports today.
        """
        if self._accepted_counts is None:
            counts = {}
            for record in self.records:
                for name, payload in record.accepted:
                    if payload is not None:
                        counts[name] = counts.get(name, 0) + 1
            object.__setattr__(self, "_accepted_counts", tuple(sorted(counts.items())))
        return sum(count for name, count in self._accepted_counts
                   if endpoints is None or name in endpoints)

    def _fields(self, *, shared_slots=False):
        """Shared public/identity schema; no copy of the complete slot history."""
        compact = bool(self.records) and type(self.records[0].hits) is HitVector
        out = {"stimulus": self.stimulus._image(shared_slots=shared_slots),
               "artifact": self.artifact,
               "initial_state": self.initial_state,
               "records": self.records,
               "completion": self.completion.value,
               "domain": self.domain}
        if self.request is not None:
            out["request"] = self.request._image(shared_slots=shared_slots)
        if compact:
            out["hit_encoding"] = HIT_VECTOR_ENCODING
            out["hit_catalogue"] = list(self.records[0].hits.catalogue)
        if self.source_evidence is not None:
            out["source_evidence"] = self.source_evidence
        if self.query_boundaries is not None:
            out["query_boundaries"] = self.query_boundaries
        if self.initialization is not None:
            out["initialization"] = self.initialization
        return out

    def as_dict(self):
        out = self._fields()
        compact = "hit_encoding" in out
        out["initial_state"] = _plain_pairs(self.initial_state)
        out["records"] = [r.as_dict(compact_hits=compact) for r in self.records]
        if self.source_evidence is not None:
            out["source_evidence"] = thaw_record_value(self.source_evidence)
        if self.query_boundaries is not None:
            out["query_boundaries"] = thaw_record_value(self.query_boundaries)
        if self.initialization is not None:
            out["initialization"] = thaw_record_value(self.initialization)
        return out

    @classmethod
    def from_dict(cls, d):
        d = _record(d, "audited-trace", {
            "stimulus", "artifact", "initial_state", "records", "completion",
            "domain", "hit_encoding", "hit_catalogue", "source_evidence", "request", "query_boundaries",
            "initialization"})
        completion = d.get("completion", Completion.NORMAL.value)
        if type(completion) is not str:
            raise TypeError("audited-trace.completion must be a string on the wire")
        records = d.get("records", ())
        if type(records) not in (tuple, list):
            raise TypeError(
                "audited-trace.records must be a finite tuple or list on the wire")
        has_encoding = "hit_encoding" in d or "hit_catalogue" in d
        if has_encoding and (
                d.get("hit_encoding") != HIT_VECTOR_ENCODING
                or "hit_catalogue" not in d):
            raise ValueError(
                "an audited trace has no complete supported hit-vector binding")
        hit_catalogue = (HitVectorCodec(d["hit_catalogue"])
                         if has_encoding else None)
        return cls(Stimulus.from_dict(d["stimulus"]),
                   initial_state=_wire_pairs(
                       d.get("initial_state", ()),
                       "audited-trace.initial-state"),
                   records=[SlotRecord.from_dict(
                       r, hit_catalogue=hit_catalogue) for r in records],
                   completion=Completion(completion),
                   domain=d.get("domain", ""), artifact=d.get("artifact", ""),
                   source_evidence=d.get("source_evidence"),
                   query_boundaries=d.get("query_boundaries"),
                   initialization=d.get("initialization"),
                   request=Stimulus.from_dict(d["request"]) if "request" in d else None)

    @property
    def fingerprint(self):
        # Only the exact record has the recursively frozen constructor contract.
        # A subclass may override its wire projection, so preserve its uncached
        # behavior. Concurrent first reads may compute twice but yield one value.
        if type(self) is not AuditedTrace:
            return hashlib.sha256(canonical_json(self.as_dict()).encode()).hexdigest()
        if self._fingerprint is None:
            # The exact Stimulus already validates its complete projection at
            # construction; its exact SlotRecords and other values are recursively
            # detached too. Encode these owned tuples directly instead of copying
            # every pair/value into a second, mutable graph just to discard it.
            # Public as_dict still returns detached lists/dicts, using the same schema.
            projection = self._fields(shared_slots=True)
            compact = "hit_encoding" in projection

            def encode_child(value):
                if type(value) is SlotRecord:
                    return value._fields(compact_hits=compact)
                if type(value) is FrozenValueMap:
                    return dict(value.pairs())
                raise TypeError(f"{type(value).__name__} is not an owned trace value")

            try:
                value = _digest_json_projection(projection, default=encode_child, record_paths=(
                    ("records",), ("source_evidence", "intervals"),
                    ("stimulus", "slots"), ("request", "slots")))
            except (TypeError, ValueError, OverflowError, RecursionError):
                # Preserve the established diagnostic for encoding limits.
                value = hashlib.sha256(canonical_json(self.as_dict()).encode()).hexdigest()
            object.__setattr__(self, "_fingerprint", value)
        return self._fingerprint

    def __repr__(self):
        return (f"AuditedTrace({len(self.records)} slots, "
                f"{self.accepted_count()} accepted, {self.completion.value})")


#: The domain name a single-clock design uses. `RTLMeta` gives its one domain an empty
#: name, so the two agree without a translation table.
ANON = ""


class TraceDomainFields(Frozen):
    """The backend-neutral fields fixed before a concrete replay starts.

    A generation policy also contains construction choices such as drain and stall.
    Drivers must not inspect that larger policy: only the horizon, reset prefix and
    queue bound define the finite trace set.  This exact record is the carry-only seam
    from the public generation boundary to any replay backend.  The backend adds its
    own adapter version when it names the resulting domain.
    """

    __slots__ = ("horizon", "queue_bound", "reset_hold_cycles")

    def __init__(self, horizon, reset_hold_cycles, queue_bound):
        if type(horizon) is not int or horizon < 1:
            raise ValueError(
                "trace-domain fields need a positive exact integer horizon")
        if type(reset_hold_cycles) is not int or reset_hold_cycles < 0:
            raise ValueError(
                "trace-domain fields need a nonnegative exact reset hold")
        if type(queue_bound) is not int or queue_bound < 1:
            raise ValueError(
                "trace-domain fields need a positive exact queue bound")
        self.horizon = horizon
        self.reset_hold_cycles = reset_hold_cycles
        self.queue_bound = queue_bound
        self._freeze()

    def identity(self, contract_digest, *, adapter_version=""):
        """Name this trace set after a backend supplies its adapter version."""

        return trace_domain(
            contract_digest,
            horizon=self.horizon,
            reset_hold_cycles=self.reset_hold_cycles,
            adapter_version=adapter_version,
            queue_bound=self.queue_bound,
        )


def leading_reset_slots(slots):
    """How many slots this stimulus holds reset for before it drives anything.

    Counted from the front and stopping at the first slot that is not a reset, because
    that is the segment whose length a run's `reset_hold_cycles` is about: a reset
    asserted again in the middle of a run is a different event and does not add to the
    prefix. Three callers -- the reset audit, the recorded-schedule check, the domain
    membership test -- and a second copy of the loop is a second chance to count it
    differently. It lives here because the prefix is a property of a stimulus, and a
    core module reaching into `rtl/` for it was the domain layer depending on one
    backend.
    """
    held = 0
    for slot in slots:
        if not getattr(slot, "reset", False):
            break
        held += 1
    return held


def trace_domain(contract_digest, horizon=None, reset_hold_cycles=None,
                 adapter_version="", queue_bound=None):
    """The identity of the trace domain a record was taken in.

    A trace domain is a set of traces one admissibility predicate accepts, so its
    identity is what that predicate is over:

    * the **interface** -- `contract_digest`, kept as the string's prefix because
      `RunResult.check_task` refuses a trace whose domain is not this task's contract and
      needs the contract still recoverable from it;
    * the **horizon** `N`, since a trace of 40 slots and one of 200 are not members of
      one set;
    * the **reset policy** every schedule begins with;
    * the **adapter version**, since changing how a handshake is driven changes what a
      schedule means;
    * the **queue bound** per input endpoint: queue lengths vary within it and remain
      one domain, and a bound is what makes the domain a finite set of traces.

    Deliberately **not** the back-pressure pattern, and not the drain. `stall_every` is
    the rule that *constructs* a stimulus for a candidate that carries no schedule of its
    own; a provider's recorded schedule does not obey it and is still an admissible trace
    of the same domain. Putting it in the identity said the opposite of what the code did
    -- every trace carried a digest naming a stall pattern that half of them never ran
    under -- and the choice is between that and calling every recorded stimulus a
    separate domain. It is a construction policy; the telemetry records which environment
    each stimulus ran under, per stimulus, in `schedule_provenance`.

    Fields that are `None` are written as `None` rather than dropped: a run that did not
    state its horizon and one whose horizon happened to equal the default are different
    documents.
    """
    if type(contract_digest) is not str or not contract_digest:
        raise ValueError("a trace domain needs a nonempty contract digest")
    if horizon is not None and (type(horizon) is not int or horizon < 1):
        raise ValueError("a trace-domain horizon must be a positive integer or absent")
    if reset_hold_cycles is not None and (
            type(reset_hold_cycles) is not int or reset_hold_cycles < 0):
        raise ValueError(
            "trace-domain reset-hold cycles must be a nonnegative integer or absent")
    if type(adapter_version) is not str:
        raise TypeError("a trace-domain adapter version must be an exact string")
    if queue_bound is not None and (
            type(queue_bound) is not int or queue_bound < 1):
        raise ValueError(
            "a trace-domain queue bound must be a positive integer or absent")

    policy_body = thaw_record_value(freeze_record_value({
        "horizon": horizon,
        "reset_hold_cycles": reset_hold_cycles,
        "adapter": adapter_version,
        # The finite queue bound per input endpoint (todo3 T3-01): queue lengths vary
        # within it and stay one domain; changing it changes this fingerprint, and an
        # over-bound queue is refused before anything runs.
        "queue_bound": queue_bound,
    }, "trace-domain"))
    # This digest is a semantic identity, not a display label.  Keep the complete
    # SHA-256 so an evidence boundary never relies on a truncated prefix.
    policy = hashlib.sha256(
        json.dumps(policy_body, sort_keys=True).encode()).hexdigest()
    return f"{contract_digest}:{policy}"


def contract_of_domain(domain):
    """The contract digest a domain identity names. See `trace_domain`."""
    if type(domain) is not str:
        raise TypeError("a trace-domain identity must be an exact string")
    return domain.split(":", 1)[0]


def from_legacy(record, endpoint=None, controls=None, source=None):
    """Decode a legacy witness into the public stimulus representation.

    Mixed nonempty forms are refused: a tick or reset in an action list cannot
    be represented by an input row. ``controls`` supplies the per-slot environment
    values that the legacy format does not record, including backpressure.
    """
    if type(record) is not dict:
        raise Refused(Refusal(
            "legacy-witness-not-a-record",
            "a witness is a mapping; anything else cannot say which form it is in",
            got=type(record).__name__))
    forms = []
    for name in ("inputs", "interleave", "actions"):
        value = record.get(name)
        if value is None:
            continue
        if type(value) not in (tuple, list):
            raise TypeError(f"legacy {name} must be a finite tuple or list")
        if value:
            forms.append(name)
    if len(forms) > 1:
        raise Refused(Refusal(
            "legacy-witness-two-forms",
            "this record carries more than one stimulus form and they are not "
            "interchangeable: a tick or a reset in the action list has no row to appear "
            "as, so choosing one silently chooses a different run",
            forms=forms))
    if not forms:
        raise Refused(Refusal(
            "legacy-witness-empty",
            "no stimulus form here at all, so there is nothing to run; an empty "
            "stimulus is a thing a caller may build deliberately, not something to "
            "infer from a record that forgot to carry one",
            keys=sorted(record)))

    if endpoint is not None and type(endpoint) is not str:
        raise TypeError("a legacy endpoint name must be an exact string or absent")
    if source is not None and type(source) is not str:
        raise TypeError("legacy stimulus provenance must be an exact string or absent")

    queues, slots = {}, []

    def _offer(name, values):
        if type(name) is not str:
            raise TypeError("a legacy drive endpoint must be an exact string")
        if type(values) not in (tuple, list):
            raise TypeError("a legacy drive payload must be a finite tuple or list")
        queues.setdefault(name, []).append(tuple(values))
        slots.append(EventSlot(offers=[name], controls=controls))

    form = forms[0]
    if form == "inputs":
        name = endpoint if endpoint is not None else ANON
        rows = record["inputs"]
        for row in rows:
            _offer(name, row)
    else:
        actions = record[form]
        for index, action in enumerate(actions):
            if type(action) not in (tuple, list) or len(action) != 3:
                raise TypeError(
                    f"legacy action {index} must be a kind/domain/payload triple")
            kind, domain, values = action[0], action[1], action[2]
            if type(kind) is not str or type(domain) is not str:
                raise TypeError("a legacy action kind and domain must be exact strings")
            name = endpoint if endpoint is not None else domain
            if kind == "drive":
                _offer(name, values)
            elif kind == "tick":
                if type(values) not in (tuple, list):
                    raise TypeError(
                        "a legacy tick payload must be a finite tuple or list")
                # Preserve ticks as slots with no offer; they still advance time.
                slots.append(EventSlot(ticks=[domain], controls=controls))
            elif kind == "reset":
                if type(values) not in (tuple, list):
                    raise TypeError(
                        "a legacy reset payload must be a finite tuple or list")
                slots.append(EventSlot(reset=True, controls=controls))
            else:
                raise Refused(Refusal(
                    "legacy-witness-unknown-action",
                    "this action is not one the boundary knows how to place in a slot, "
                    "and guessing would put a run in the record that never happened",
                    action=kind, known=["drive", "tick", "reset"]))

    recorded_source = source if source is not None else record.get("source", "")
    return Stimulus(queues=queues, slots=slots,
                    memory_policy=record.get("mem_policy"),
                    initial_state=record.get("initial_state"),
                    source=recorded_source)
