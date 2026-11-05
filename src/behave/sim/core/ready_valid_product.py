"""One transition rule for the finite ready/valid correlation product.

The ordinary monitor consumes concrete ledger facts.  Bounded RTL analysis consumes
Z3 terms. Both need exactly the same state update: accepted requests enter a finite
pending queue, outputs retire its head or the matching ID, stalled offers retain their payload,
and the declared safety obligations emit hit bits.  This module owns that update once;
an algebra supplies either concrete values or symbolic terms.

The finite product supports ordered and identifier-correlated responses. Unreadable
observations still need the general concrete monitor. Reset preserves or
clears requests only as declared; an undeclared reset with outstanding requests
emits a definedness fault. Exact consumers must reject that domain, not use the
continuation after the fault to establish safety.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ConcreteAlgebra",
    "CountedReadyValidState",
    "OrderedReadyValidConfig",
    "OrderedReadyValidEvent",
    "OrderedReadyValidState",
    "OrderedReadyValidStep",
    "ordered_ready_valid_initial",
    "ordered_ready_valid_state_image",
    "ordered_ready_valid_step",
    "ready_valid_fires",
    "ready_valid_pointer_step",
]


@dataclass(frozen=True)
class OrderedReadyValidConfig:
    """Finite monitor declarations; an optional payload index selects ID matching."""

    capacity: int
    payload_fields: int
    answers_per_transaction: int | None
    max_outstanding: int | None = None
    response_deadline: int | None = None
    reset_policy: str | None = None
    identifier_index: int | None = None

    @property
    def counted(self):
        """Ordered one-to-one responses need no per-request identity or age."""
        return (self.answers_per_transaction == 1
                and self.identifier_index is None and self.response_deadline is None)

    @property
    def queue_capacity(self):
        """No request can enter the queue without a declared answer ratio.

        Its reset values then remain invariant. Omit those constant components
        in both concrete and symbolic states, rather than visiting horizon-sized
        all-zero tables at every slot. Stability still retains its own state.
        ``capacity`` remains the declared bound for counting configurations.
        """
        return 0 if self.answers_per_transaction is None else self.capacity

    def __post_init__(self):
        if self.identifier_index is not None and (
                type(self.identifier_index) is not int
                or not 0 <= self.identifier_index < self.payload_fields):
            raise ValueError("identifier_index must name an output payload field")
        if self.reset_policy not in (None, "clears", "keeps"):
            raise ValueError("reset_policy must be clears, keeps or None")
        if type(self.capacity) is not int or self.capacity < 1:
            raise ValueError("the ordered monitor needs a positive finite capacity")
        if type(self.payload_fields) is not int or self.payload_fields < 0:
            raise ValueError("the output payload arity must be nonnegative")
        for name in ("answers_per_transaction", "max_outstanding"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be a positive integer or None")
        if self.response_deadline is not None and (
                type(self.response_deadline) is not int
                or self.response_deadline < 0):
            raise ValueError("response_deadline must be nonnegative or None")


@dataclass(frozen=True)
class CountedReadyValidState:
    """Exact observable state for ordered one-to-one, deadline-free responses.

    Every pending request owes one output, so its identity and acceptance time
    cannot affect a transition or goal. Keep the total acceptance count as well
    as outstanding responses: the finite capacity is a lifetime acceptance
    bound, not a recyclable FIFO depth.
    """

    accepted_count: object
    outstanding: object
    stalled: object
    held: tuple


@dataclass(frozen=True)
class OrderedReadyValidState:
    """Finite monitor state; members may be concrete values or solver terms."""

    accepted_count: object
    pending: tuple
    owed: tuple
    accepted_at: tuple
    offered: tuple
    reported: tuple
    stalled: object
    held: tuple
    keys: tuple = ()


@dataclass(frozen=True)
class OrderedReadyValidEvent:
    """The protocol facts for one slot, concrete or in the supplied algebra."""

    reset: object
    slot: object
    input_accepted: object
    output_valid: object
    output_ready: object
    output_payload: tuple
    input_key: tuple = ()


@dataclass(frozen=True)
class OrderedReadyValidStep:
    """The next state and the goal/chance values emitted by one slot."""

    state: OrderedReadyValidState | CountedReadyValidState
    hits: dict
    opportunities: dict
    details: dict


class ConcreteAlgebra:
    """The rule's operations over ordinary Python values."""

    @staticmethod
    def bool(value):
        return bool(value)

    @staticmethod
    def integer(value):
        return int(value)

    @staticmethod
    def zero_like(value):
        return False if type(value) is bool else 0

    @staticmethod
    def land(*values):
        return all(values)

    @staticmethod
    def lor(*values):
        return any(values)

    @staticmethod
    def lnot(value):
        return not value

    @staticmethod
    def eq(left, right):
        return left == right

    @staticmethod
    def gt(left, right):
        return left > right

    @staticmethod
    def add(left, right):
        return left + right

    @staticmethod
    def sub(left, right):
        return left - right

    @staticmethod
    def div(left, right):
        return left // right

    @staticmethod
    def mod(left, right):
        return left % right

    @staticmethod
    def ite(condition, when_true, when_false):
        return when_true if condition else when_false

    @classmethod
    def count(cls, values):
        return sum(int(value) for value in values)


def ordered_ready_valid_state_image(state, read=lambda value: value):
    """Return the complete finite monitor state in one shared field layout.

    Concrete replay and the symbolic product use different scalar carriers, but the
    state being compared is the same record.  Keeping this layout beside the shared
    transition prevents an audit from silently comparing only ``pending`` while a
    deadline, held payload, or already-reported bit differs.
    """

    if type(state) not in (OrderedReadyValidState, CountedReadyValidState):
        raise TypeError("the ordered monitor image needs its exact state type")
    if not callable(read):
        raise TypeError("the ordered monitor image reader must be callable")
    if type(state) is CountedReadyValidState:
        return {"accepted_count": read(state.accepted_count),
                "outstanding": read(state.outstanding),
                "stalled": read(state.stalled),
                "held": tuple(read(value) for value in state.held)}
    image = {
        "accepted_count": read(state.accepted_count),
        "pending": tuple(read(value) for value in state.pending),
        "owed": tuple(read(value) for value in state.owed),
        "accepted_at": tuple(read(value) for value in state.accepted_at),
        "offered": tuple(read(value) for value in state.offered),
        "reported": tuple(read(value) for value in state.reported),
        "stalled": read(state.stalled),
        "held": tuple(read(value) for value in state.held),
    }
    if state.keys:
        image["keys"] = tuple(read(value) for value in state.keys)
    return image


def ready_valid_fires(sender_valid, receiver_ready, algebra):
    """The adapter's single handshake predicate, over either supported algebra."""
    return algebra.land(sender_valid, receiver_ready)


def ready_valid_pointer_step(pointer, sender_valid, receiver_ready, algebra):
    """Advance the accepted-input pointer exactly on the shared handshake."""
    fired = ready_valid_fires(sender_valid, receiver_ready, algebra)
    return fired, algebra.ite(
        fired, algebra.add(pointer, algebra.integer(1)), pointer)


def ordered_ready_valid_initial(config, algebra, zero_payload=None):
    """The reset state of ``config`` in ``algebra``."""
    return _initial(config, algebra, zero_payload,
                    counted=getattr(config, 'counted', False))


def _initial(config, algebra, zero_payload, *, counted):
    false = algebra.bool(False)
    zero = algebra.integer(0)
    held = ((zero,) * config.payload_fields if zero_payload is None
            else tuple(zero_payload))
    if len(held) != config.payload_fields:
        raise ValueError("the ordered monitor reset payload has the wrong arity")
    if counted:
        return CountedReadyValidState(zero, zero, false, held)
    return OrderedReadyValidState(
        accepted_count=zero,
        pending=(false,) * config.queue_capacity,
        owed=(zero,) * config.queue_capacity,
        accepted_at=(zero,) * config.queue_capacity,
        offered=(false,) * config.queue_capacity,
        reported=(false,) * config.queue_capacity,
        stalled=false,
        held=held,
        keys=(() if config.identifier_index is None else
              (algebra.zero_like(held[config.identifier_index]),) * config.queue_capacity),
    )


def ordered_ready_valid_step(config, state, event, algebra):
    """Apply the ordered ready/valid monitor transition exactly once.

    The ordering matches :class:`~behave.sim.core.monitor.Monitor`: accept an input,
    check response deadlines, attribute a newly-started output offer, retire an output
    transfer, then check the end-of-slot outstanding count.  Stability is checked
    against the offer retained from the preceding slot.
    """
    if len(event.output_payload) != config.payload_fields:
        raise ValueError("the output payload does not match the configured arity")
    if type(state) is CountedReadyValidState:
        if not getattr(config, 'counted', False) or event.input_key:
            raise ValueError("the counted state requires ordered one-to-one responses without deadlines")
        return _counted_step(config, state, event, algebra)
    identified = config.identifier_index is not None
    if len(event.input_key) != int(identified) or len(state.keys) != (
            config.queue_capacity if identified else 0):
        raise ValueError("identifier matching requires the declared request key layout")
    if any(len(values) != config.queue_capacity for values in (
            state.pending, state.owed, state.accepted_at, state.offered,
            state.reported)):
        raise ValueError("the ordered monitor state has a different queue layout")

    false = algebra.bool(False)
    true = algebra.bool(True)
    zero = algebra.integer(0)
    one = algebra.integer(1)
    goals = {
        "monitor.sink-stability": false,
        "monitor.outstanding-exceeded": false,
        "monitor.output-without-transaction": false,
        "monitor.response-deadline": false,
    }
    chances = dict(goals)
    # Without a declared answer ratio the concrete monitor deliberately does not
    # invent a pending-transaction semantics.  Stability remains meaningful.
    accept = (event.input_accepted if config.answers_per_transaction is not None
              else false)
    accepted_count = algebra.add(
        state.accepted_count, algebra.ite(accept, one, zero))
    pending, owed, accepted_at, offered, reported, keys = [], [], [], [], [], []
    # Acceptance precedes retirement, even when both occur in the same slot.
    # Duplicate IDs are an environment violation, never an artifact goal.
    reused = (algebra.land(accept, algebra.lor(*(
        algebra.land(present, algebra.eq(key, event.input_key[0]))
        for present, key in zip(state.pending, state.keys)))) if identified else false)
    for index in range(config.queue_capacity):
        selected = algebra.land(
            accept, algebra.eq(state.accepted_count, algebra.integer(index)))
        pending.append(algebra.lor(state.pending[index], selected))
        owed.append(algebra.ite(
            selected,
            algebra.integer(config.answers_per_transaction or 0),
            state.owed[index]))
        accepted_at.append(algebra.ite(
            selected, algebra.integer(event.slot), state.accepted_at[index]))
        offered.append(algebra.ite(selected, false, state.offered[index]))
        reported.append(algebra.ite(selected, false, state.reported[index]))
        if identified:
            keys.append(algebra.ite(selected, event.input_key[0], state.keys[index]))

    # An identified output may retire any pending request, not the queue head.
    target = []
    older = false
    for index, present in enumerate(pending):
        match = (algebra.eq(keys[index], event.output_payload[config.identifier_index])
                 if identified else algebra.lnot(older))
        target.append(algebra.land(present, match))
        older = algebra.lor(older, present)
    any_target = algebra.lor(*target)

    deadline_hits = []
    if config.response_deadline is not None:
        deadline_chances = []
        for index in range(config.queue_capacity):
            chance = algebra.land(
                pending[index], algebra.lnot(offered[index]),
                algebra.lnot(reported[index]))
            hit = algebra.land(
                chance,
                algebra.gt(
                    algebra.sub(algebra.integer(event.slot), accepted_at[index]),
                    algebra.integer(config.response_deadline)))
            deadline_chances.append(chance)
            deadline_hits.append(hit)
            reported[index] = algebra.lor(reported[index], hit)
        goals["monitor.response-deadline"] = algebra.lor(*deadline_hits)
        chances["monitor.response-deadline"] = algebra.count(deadline_chances)

    output_transfer = ready_valid_fires(
        event.output_valid, event.output_ready, algebra)
    output_started = algebra.land(event.output_valid, algebra.lnot(state.stalled))
    for index in range(config.queue_capacity):
        offered[index] = algebra.lor(
            offered[index], algebra.land(algebra.lor(output_started, output_transfer), target[index]))

    if config.answers_per_transaction is not None:
        goals["monitor.output-without-transaction"] = algebra.land(
            output_transfer, algebra.lnot(any_target))
        chances["monitor.output-without-transaction"] = output_transfer
        for index in range(config.queue_capacity):
            retires = algebra.land(output_transfer, target[index])
            final = algebra.land(retires, algebra.eq(owed[index], one))
            pending[index] = algebra.land(pending[index], algebra.lnot(final))
            owed[index] = algebra.ite(
                retires, algebra.sub(owed[index], one), owed[index])

    if config.max_outstanding is not None:
        count = algebra.count(pending)
        goals["monitor.outstanding-exceeded"] = algebra.gt(
            count, algebra.integer(config.max_outstanding))
        chances["monitor.outstanding-exceeded"] = true

    stability_dropped, stability_changed, stalled, held = _stability(state, event, algebra)
    goals["monitor.sink-stability"] = algebra.lor(
        stability_dropped, stability_changed)
    chances["monitor.sink-stability"] = state.stalled

    ordinary = OrderedReadyValidState(
            accepted_count=accepted_count,
            pending=tuple(pending),
            owed=tuple(owed),
            accepted_at=tuple(accepted_at),
            offered=tuple(offered),
            reported=tuple(reported),
            stalled=stalled,
            held=held,
            keys=tuple(keys),
        )
    reset = _initial(
        config, algebra,
        tuple(algebra.zero_like(value) for value in state.held), counted=False)
    # Stability starts anew on reset, independently of request ownership. A
    # missing policy is not permission to erase obligations: retain the image
    # and expose the ambiguity to the consumer's whole-domain admission proof.
    requests_on_reset = reset if config.reset_policy == "clears" else state
    reset_policy_unresolved = (
        algebra.land(event.reset, algebra.lor(*state.pending))
        if config.reset_policy is None else false)

    def choose(reset_value, ordinary_value):
        return algebra.ite(event.reset, reset_value, ordinary_value)

    next_state = OrderedReadyValidState(
        accepted_count=choose(requests_on_reset.accepted_count, ordinary.accepted_count),
        pending=tuple(map(choose, requests_on_reset.pending, ordinary.pending)),
        owed=tuple(map(choose, requests_on_reset.owed, ordinary.owed)),
        accepted_at=tuple(map(choose, requests_on_reset.accepted_at, ordinary.accepted_at)),
        offered=tuple(map(choose, requests_on_reset.offered, ordinary.offered)),
        reported=tuple(map(choose, requests_on_reset.reported, ordinary.reported)),
        stalled=choose(reset.stalled, ordinary.stalled),
        held=tuple(map(choose, reset.held, ordinary.held)),
        keys=tuple(map(choose, requests_on_reset.keys, ordinary.keys)),
    )
    goals = {goal: choose(false, value) for goal, value in goals.items()}
    chances = {goal: choose(false, value) for goal, value in chances.items()}
    return OrderedReadyValidStep(
        next_state,
        goals,
        chances,
        {
            "reset_policy_unresolved": reset_policy_unresolved,
            "identifier_reused": choose(false, reused),
            "deadline_hits": tuple(choose(false, value)
                                    for value in deadline_hits),
            "stability_dropped": choose(false, stability_dropped),
            "stability_changed": choose(false, stability_changed),
        },
    )


def _stability(state, event, algebra):
    changed = algebra.lor(*(
        algebra.lnot(algebra.eq(old, new))
        for old, new in zip(state.held, event.output_payload, strict=True)))
    dropped = algebra.land(state.stalled, algebra.lnot(event.output_valid))
    changed = algebra.land(state.stalled, event.output_valid, changed)
    stalled = algebra.land(event.output_valid, algebra.lnot(event.output_ready))
    held = tuple(algebra.ite(stalled, value, old)
                 for old, value in zip(state.held, event.output_payload, strict=True))
    return dropped, changed, stalled, held


def _counted_step(config, state, event, algebra):
    """Quotient of the finite table; acceptance still precedes retirement."""
    false, true = algebra.bool(False), algebra.bool(True)
    zero, one = algebra.integer(0), algebra.integer(1)
    accepted = algebra.add(state.accepted_count, algebra.ite(event.input_accepted, one, zero))
    enters = algebra.land(event.input_accepted,
                          algebra.gt(algebra.integer(config.capacity), state.accepted_count))
    pending = algebra.add(state.outstanding, algebra.ite(enters, one, zero))
    transfer = ready_valid_fires(event.output_valid, event.output_ready, algebra)
    has_target = algebra.gt(pending, zero)
    pending = algebra.sub(pending, algebra.ite(algebra.land(transfer, has_target), one, zero))
    dropped, changed, stalled, held = _stability(state, event, algebra)
    hits = {"monitor.sink-stability": algebra.lor(dropped, changed),
            "monitor.output-without-transaction": algebra.land(transfer, algebra.lnot(has_target)),
            "monitor.outstanding-exceeded": (false if config.max_outstanding is None else
                algebra.gt(pending, algebra.integer(config.max_outstanding))),
            "monitor.response-deadline": false}
    chances = {"monitor.sink-stability": state.stalled,
               "monitor.output-without-transaction": transfer,
               "monitor.outstanding-exceeded": false if config.max_outstanding is None else true,
               "monitor.response-deadline": false}
    clears = config.reset_policy == 'clears'

    def choose(on_reset, ordinary):
        return algebra.ite(event.reset, on_reset, ordinary)

    return OrderedReadyValidStep(
        CountedReadyValidState(
            choose(zero if clears else state.accepted_count, accepted),
            choose(zero if clears else state.outstanding, pending),
            choose(false, stalled),
            tuple(choose(algebra.zero_like(value), value) for value in held)),
        {goal: choose(false, value) for goal, value in hits.items()},
        {goal: choose(false, value) for goal, value in chances.items()},
        {"reset_policy_unresolved": (algebra.land(event.reset, algebra.gt(state.outstanding, zero))
                                     if config.reset_policy is None else false),
         "identifier_reused": false, "deadline_hits": (),
         "stability_dropped": choose(false, dropped),
         "stability_changed": choose(false, changed)})
