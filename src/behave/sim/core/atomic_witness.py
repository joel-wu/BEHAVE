"""Versioned wire records for one atomic generation witness.

The record is shared by generation providers, canonical stimulus construction,
replay, and the Behavior atomic adapter.  It consequently belongs to none of
those backends.  This module validates only the wire identity and the scheduled
packed-port patterns; it does not decode task values, execute handlers or assign
generation policy.  The contract-aware stimulus boundary performs that decoding
with the shared physical-value layout.
"""

from __future__ import annotations

from .clock_schedule import GlobalEventSlot
from .frozen import Frozen
from .memory import MemoryEnvironment
from .refusal import Refusal, Refused

__all__ = [
    "ATOMIC_GENERATION_POLICY",
    "ATOMIC_WITNESS_VERSION",
    "WitnessInvocation",
    "validate_atomic_witness_identity",
    "witness_invocations",
]


ATOMIC_WITNESS_VERSION = "atomic-witness/2"
ATOMIC_GENERATION_POLICY = "atomic-generation/2"


class WitnessInvocation(Frozen):
    """One packed-port domain action decoded from the public wire record."""

    __slots__ = ("args", "domain")

    def __init__(self, domain, args=()):
        if type(domain) is not str or not domain:
            raise TypeError("a witness invocation needs a nonempty domain name")
        if type(args) not in (tuple, list) or any(
                type(value) is not int for value in args):
            raise TypeError(
                "witness invocation arguments are exact integer port patterns")
        self.domain = domain
        self.args = tuple(args)
        self._freeze()


def validate_atomic_witness_identity(value):
    """Require the exact witness, policy and memory-run image we decode.

    The environment is present even when the task declares no external memory.  That
    keeps the wire shape uniform and, more importantly, prevents a replay consumer
    from silently inventing a memory initialization, latency or ready schedule after
    generation.
    """

    if type(value) is not dict:
        raise Refused(Refusal(
            "atomic-witness-record-malformed",
            "an atomic witness is an exact mapping"))
    if value.get("version") != ATOMIC_WITNESS_VERSION:
        raise Refused(Refusal(
            "atomic-witness-version-unsupported",
            "an atomic witness must name the wire version this build decodes",
            got=value.get("version"), supported=ATOMIC_WITNESS_VERSION))
    if value.get("generation_policy") != ATOMIC_GENERATION_POLICY:
        raise Refused(Refusal(
            "atomic-generation-policy-unsupported",
            "an atomic witness must name the schedule-generation policy under "
            "which it was produced",
            got=value.get("generation_policy"),
            supported=ATOMIC_GENERATION_POLICY))
    try:
        MemoryEnvironment.from_dict(value.get("memory_environment"))
    except (TypeError, ValueError, Refused) as exc:
        raise Refused(Refusal(
            "atomic-memory-environment-malformed",
            "an atomic witness carries one canonical finite memory environment",
            error=f"{type(exc).__name__}: {exc}")) from None


def witness_invocations(slot, record):
    """Decode packed-port actions relative to their timestamped event slot."""

    if type(slot) is not GlobalEventSlot:
        raise TypeError("action decoding needs a timestamped EventSlot")
    if type(record) is not dict or type(record.get("actions")) is not list:
        raise Refused(Refusal(
            "multiclock-action-record-malformed",
            "a scheduled action record needs one explicit action list"))
    seen = set()
    out = []
    for index, action in enumerate(record["actions"]):
        if type(action) is not list or len(action) != 3:
            raise Refused(Refusal(
                "multiclock-action-record-malformed",
                "an action is [drive|tick, domain, values]",
                index=index))
        operation, domain, values = action
        if (operation not in ("drive", "tick") or type(domain) is not str
                or type(values) is not list):
            raise Refused(Refusal(
                "multiclock-action-record-malformed",
                "an action is [drive|tick, string-domain, list-of-values]",
                index=index))
        if any(type(value) is not int for value in values):
            raise Refused(Refusal(
                "multiclock-action-record-malformed",
                "serialized RTL pin values must be integers",
                index=index, domain=domain))
        if domain in seen:
            raise Refused(Refusal(
                "multiclock-two-invocations",
                "one domain has two actions in one event slot",
                domain=domain, time=slot.time))
        if domain not in slot.events or domain in slot.resets:
            raise Refused(Refusal(
                "multiclock-invocation-without-edge",
                "an action must belong to an active non-reset domain event",
                domain=domain, time=slot.time, events=list(slot.events),
                resets=list(slot.resets)))
        if operation == "tick" and values:
            raise Refused(Refusal(
                "multiclock-tick-has-input",
                "a clock-only tick cannot carry input values",
                domain=domain, time=slot.time))
        seen.add(domain)
        out.append(WitnessInvocation(domain, values))
    domains = [invocation.domain for invocation in out]
    if domains != sorted(domains):
        raise Refused(Refusal(
            "multiclock-action-record-malformed",
            "coincident actions use canonical domain order; the order carries no "
            "handler semantics",
            domains=domains))
    return tuple(out)
