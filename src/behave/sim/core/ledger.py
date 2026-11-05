"""Protocol facts shared by all trace monitors.

Registered adapter replay supplies offers, readiness, transfers and payloads for
each observed endpoint. Projected traces retain global scheduled-step indexes,
so a deadline is not shortened to the endpoint's active-edge count.

An offer is distinct from a transfer: stalled valid retains one offer, while
``started`` marks a new presentation. Readability is separate from payload value;
missing observations leave dependent checks unresolved, never equal by absence.
Terminal padding contributes no facts. Monitors judge these facts without
reconstructing a second protocol interpretation.
"""

from __future__ import annotations

from .frozen import Frozen, FrozenMap
from .protocol import adapter_program_for

__all__ = ["ChannelSlot", "Ledger", "SlotFact", "ledger_of"]


class ChannelSlot(Frozen):
    """One endpoint at one slot.

    Every field is three-valued where the signal may be missing: `True`, `False`, or
    `None` for "the run does not say". A check that cannot tell which of the three it has
    is a check reporting the absence of evidence as evidence.
    """

    __slots__ = (
        "endpoint",
        "offered",
        "payload",
        "payload_readable",
        "readable",
        "ready",
        "started",
        "transferred",
        "why_unreadable",
    )

    def __init__(self, endpoint, offered=None, ready=None, transferred=None,
                 payload=None, readable=True, payload_readable=True, started=False,
                 why_unreadable=""):
        self.endpoint = str(endpoint)
        #: Whether the sender asserted `valid` here.
        self.offered = offered
        #: Whether the receiver asserted `ready` here.
        self.ready = ready
        #: Whether the transaction crossed. Taken from the trace's accepted record,
        #: which is the driver's own answer, rather than recomputed from the two above.
        self.transferred = transferred
        #: The payload on the wire, whether or not it crossed. `None` where unreadable.
        self.payload = None if payload is None else tuple(payload)
        #: Whether the handshake signals **and** the payload were all probed here.
        self.readable = bool(readable)
        #: Whether the payload alone was. **Separate from the handshake**, because the
        #: two answer different questions and folding them lost one: a design offering
        #: an answer whose data lines are X has made an offer -- `valid` says so, and
        #: that is what a response deadline is measured to -- while what it is offering
        #: cannot be compared. Requiring both made the offer invisible, so the deadline
        #: went on firing on a transaction that had been answered.
        self.payload_readable = bool(payload_readable)
        #: Whether an offer **begins** here. See the module docstring.
        self.started = bool(started)
        self.why_unreadable = str(why_unreadable)
        self._freeze()

    def __repr__(self):
        return (f"ChannelSlot({self.endpoint}, offered={self.offered}, "
                f"ready={self.ready}, transferred={self.transferred})")


class SlotFact(Frozen):
    """One slot of the run, across every endpoint."""

    __slots__ = (
        "adapter_checks", "adapter_environment_violations",
        "adapter_violations", "channels", "index", "reset",
    )

    def __init__(self, index, reset, channels, adapter_checks=(),
                 adapter_violations=(), adapter_environment_violations=()):
        self.index = int(index)
        self.reset = bool(reset)
        self.channels = FrozenMap(channels)
        self.adapter_checks = tuple(adapter_checks)
        self.adapter_violations = tuple(adapter_violations)
        self.adapter_environment_violations = tuple(
            adapter_environment_violations)
        self._freeze()

    def at(self, endpoint):
        return self.channels.get(endpoint)


class Ledger(Frozen):
    """Every slot of one run, as protocol facts."""

    __slots__ = ("contract", "observed", "slots", "truncated")

    def __init__(self, contract, slots, truncated=False, observed=None):
        self.contract = contract
        self.slots = tuple(slots)
        #: Whether the run ended before its horizon, **or** was padded after a failure.
        #: Nothing about a terminal state follows from either.
        self.truncated = bool(truncated)
        #: How many slots were actually observed. Padding is not observation.
        self.observed = len(self.slots) if observed is None else int(observed)
        self._freeze()

    def __len__(self):
        return len(self.slots)

    def __iter__(self):
        return iter(self.slots)


def ledger_of(contract, stimulus, trace, *, slot_indexes=None):
    """The ledger for one run. Reads the trace; judges nothing."""
    from .stimulus import Completion

    # **Where the run stopped being a run.** A `PADDED` trace has `N` records because a
    # shorter one would describe a different stimulus, but the suffix after the artifact
    # failed is terminal stuttering: the driver writes `SlotRecord(monitor={"padding":
    # 1})` and never invokes the artifact again. Reading those as cycles had the monitor
    # accruing deadlines against slots in which nothing was running and reporting the
    # design's silence in them as an unresolved payload.
    observed = trace.observed_count
    truncated = (trace.completion in (Completion.INCOMPLETE, Completion.PADDED, Completion.CHECKPOINT)
                 or observed < len(trace.records))
    indexes = (tuple(range(len(stimulus.slots))) if slot_indexes is None
               else tuple(slot_indexes))
    replay = adapter_program_for(contract).replay(
        contract, stimulus, trace, observed=observed, slot_indexes=indexes)
    return ledger_from_replay(contract, stimulus.slots[:observed], replay,
                              slot_indexes=indexes[:observed], truncated=truncated)


def ledger_from_replay(contract, events, replay, *, slot_indexes=None, truncated=False):
    """The common projection for recovered transitions, without re-executing them."""
    endpoints = contract.endpoints
    slots = []
    indexes = tuple(range(len(events))) if slot_indexes is None else slot_indexes
    for index, (slot, recovered) in enumerate(
            zip(events, replay, strict=True)):
        channels = {}
        for endpoint in endpoints:
            image = recovered.channels[endpoint.name]
            missing = image.why_unreadable
            channels[endpoint.name] = ChannelSlot(
                endpoint.name, offered=image.offered, ready=image.ready,
                transferred=image.transferred, payload=image.payload,
                readable=(image.protocol_readable and image.payload_readable),
                payload_readable=image.payload_readable,
                started=image.started, why_unreadable=missing)
        slots.append(SlotFact(
            indexes[index], bool(slot.reset), channels,
            adapter_checks=recovered.checks,
            adapter_violations=recovered.violations,
            adapter_environment_violations=recovered.environment_violations))
    return Ledger(contract, slots, truncated=truncated, observed=len(events))
