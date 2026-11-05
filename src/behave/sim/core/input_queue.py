"""The input-queue state shared by concrete and exact protocol products.

An event-slot offer releases the next queued transaction.  Acceptance advances a
different cursor.  Keeping the cursors separate is what makes a released transaction
remain presented through back-pressure and through the drain, where no new transaction
is released.
"""

from __future__ import annotations

from dataclasses import dataclass

from .frozen import FrozenMap
from .stimulus import uses_global_events

__all__ = [
    "InputCompletionEnvironment",
    "InputDrainEnvironment",
    "InputEnvironmentFault",
    "InputQueueState",
    "PreparedInputs",
    "endpoint_is_active",
    "input_completion_environment",
    "input_drain_environment",
    "input_is_present",
    "input_release_step",
    "released_by_schedule",
]


@dataclass(frozen=True)
class InputEnvironmentFault:
    """One environment premise that terminal input attribution needs."""

    kind: str
    slot: int
    endpoint: str
    why: str


@dataclass(frozen=True)
class InputCompletionEnvironment:
    """The schedule facts needed to judge drain-end input completion."""

    released: FrozenMap
    drain_slots: tuple
    faults: tuple


@dataclass(frozen=True)
class InputDrainEnvironment:
    """The receptive suffix following the schedule's final input offer."""

    drain_slots: tuple
    faults: tuple


def endpoint_is_active(endpoint, slot, atomic):
    """The endpoint advances outside reset; one-clock slots imply their edge."""
    if not atomic:
        return not slot.reset
    return endpoint.clock in slot.ticks and endpoint.clock not in slot.resets


def input_drain_environment(stimulus, contract, endpoints=None):
    """Derive the receptive suffix after the last relevant active input offer.

    ``endpoints`` names the input channels whose schedule is being considered. The
    concrete completion check passes the nonempty queues; a fixed symbolic schedule
    passes all input channels because queue length is a symbolic component.
    """
    from .protocol import registered_adapter

    inputs = {endpoint.name: endpoint for endpoint in contract.inputs()}
    relevant = set(inputs) if endpoints is None else set(endpoints)
    unknown = relevant - set(inputs)
    if unknown:
        raise ValueError(
            f"input drain names unknown endpoints: {sorted(unknown)}")
    atomic = uses_global_events(contract, stimulus)
    last_offer = -1
    for index, slot in enumerate(stimulus.slots):
        for name in slot.offers:
            endpoint = inputs.get(name)
            if name not in relevant or endpoint is None \
                    or not endpoint_is_active(endpoint, slot, atomic):
                continue
            last_offer = index
    drain_slots = tuple(
        index for index, slot in enumerate(stimulus.slots)
        if last_offer >= 0 and index > last_offer
        and any(endpoint_is_active(endpoint, slot, atomic)
                for name, endpoint in inputs.items() if name in relevant)
    )
    faults = []
    for index in drain_slots:
        slot = stimulus.slots[index]
        controls = dict(slot.controls)
        for endpoint in contract.outputs():
            if not endpoint_is_active(endpoint, slot, atomic):
                continue
            blocked = tuple(
                signal for signal in registered_adapter(
                    endpoint.adapter.name, endpoint.adapter.version).receptive_controls(endpoint)
                if controls.get(signal) != 1)
            if blocked:
                faults.append(InputEnvironmentFault(
                    "environment.drain-not-receptive", index, endpoint.name,
                    f"drain slot {index} did not assert {list(blocked)}"))
    return InputDrainEnvironment(drain_slots, tuple(faults))


def input_completion_environment(stimulus, contract):
    """Derive the terminal input premise shared by replay and exact products.

    Injection releases every queued transaction. After its final active offer, the
    schedule provides a receptive drain for work already in flight. Empty queues do
    not create a progress obligation.
    """

    inputs = {endpoint.name: endpoint for endpoint in contract.inputs()}
    atomic = uses_global_events(contract, stimulus)
    released = {name: 0 for name in inputs}
    for slot in stimulus.slots:
        for name in slot.offers:
            endpoint = inputs.get(name)
            if endpoint is None or not endpoint_is_active(endpoint, slot, atomic):
                continue
            if released[name] < stimulus.depth(name):
                released[name] += 1

    relevant = {name for name in inputs if stimulus.depth(name) > 0}
    drain = input_drain_environment(stimulus, contract, relevant)
    faults = list(drain.faults)
    for name, count in released.items():
        depth = stimulus.depth(name)
        if count != depth:
            faults.append(InputEnvironmentFault(
                "environment.input-queue-unreleased",
                len(stimulus.slots) - 1 if stimulus.slots else 0,
                name,
                f"the schedule released {count} of {depth} queued input "
                "transactions"))
    return InputCompletionEnvironment(
        FrozenMap(released), drain.drain_slots, tuple(faults))


def input_release_step(released, length, release, algebra):
    """Release at most one queue entry, over a concrete or symbolic algebra."""

    can_release = algebra.land(release, algebra.gt(length, released))
    return algebra.ite(
        can_release, algebra.add(released, algebra.integer(1)), released)


def input_is_present(accepted, released, active, algebra):
    """Whether the oldest released, unaccepted entry is presented in this slot."""

    return algebra.land(active, algebra.gt(released, accepted))


def released_by_schedule(stimulus, contract):
    """Queue-prefix lengths released by a complete schedule.

    This is the schedule-only half of :meth:`InputQueueState.prepare`: acceptance and
    protocol busy state cannot change which transactions injection made available.
    Single-clock slots use the established implicit-edge shorthand; an atomic slot
    releases only endpoints whose domain has an active, non-reset edge.
    """

    return input_completion_environment(stimulus, contract).released


@dataclass(frozen=True)
class PreparedInputs:
    """The release state and payloads used to drive one slot."""

    released: FrozenMap
    presentations: FrozenMap


@dataclass(frozen=True)
class InputQueueState:
    """Accepted and released cursors for every input endpoint."""

    accepted: FrozenMap
    released: FrozenMap

    @classmethod
    def initial(cls, endpoints):
        names = tuple(
            endpoint if type(endpoint) is str else endpoint.name
            for endpoint in endpoints)
        return cls(
            FrozenMap({name: 0 for name in names}),
            FrozenMap({name: 0 for name in names}),
        )

    def _check_names(self, stimulus):
        names = set(self.accepted)
        if set(self.released) != names:
            raise ValueError("accepted and released cursors name different endpoints")
        queues = {name for name, _transactions in stimulus.queues}
        if not queues <= names:
            raise ValueError("the stimulus queues an unknown input endpoint")
        return names

    def prepare(self, stimulus, slot, *, releasable=None, presentable=None):
        """Apply this slot's releases and return the payloads it presents.

        ``releasable`` names endpoints whose schedule action is active in this slot;
        ``presentable`` may be narrower when a protocol such as start/done is busy.
        Neither operation deletes work released by an earlier slot.
        """

        names = self._check_names(stimulus)
        releasable = names if releasable is None else set(releasable)
        presentable = (releasable if presentable is None
                       else set(presentable))
        released = self.released
        for name in slot.offers:
            if name in releasable:
                count = min(stimulus.depth(name), released[name] + 1)
                if count != released[name]:
                    if released is self.released:
                        released = dict(released)
                    released[name] = count
        presentations = {}
        for name in names:
            presentations[name] = (
                stimulus.head(name, self.accepted[name])
                if name in presentable and self.accepted[name] < released[name]
                else None
            )
        return PreparedInputs(
            released if released is self.released else FrozenMap(released),
            FrozenMap(presentations))

    def commit(self, prepared, accepted):
        """Commit exactly the transfers reported for ``prepared``."""

        if type(prepared) is not PreparedInputs:
            raise TypeError("input-queue commit needs exact PreparedInputs")
        transfers = dict(accepted)
        cursors = self.accepted
        for name, payload in transfers.items():
            if name not in cursors or payload is None:
                continue
            if prepared.presentations[name] is None:
                raise ValueError(
                    f"endpoint {name!r} accepted with no pending presentation")
            if cursors is self.accepted:
                cursors = dict(cursors)
            cursors[name] += 1
            if cursors[name] > prepared.released[name]:
                raise ValueError(
                    f"endpoint {name!r} accepted beyond its released queue prefix")
        if cursors is self.accepted and prepared.released is self.released:
            return self
        return InputQueueState(
            cursors if cursors is self.accepted else FrozenMap(cursors),
            prepared.released)

    def remaining(self, stimulus):
        """Required queue entries not yet accepted, by endpoint."""

        self._check_names(stimulus)
        return FrozenMap({
            name: stimulus.depth(name) - self.accepted[name]
            for name in self.accepted
        })
