"""Contract-owned construction of reset, body, and drain slots.

Framing is shared by public stimulus helpers and canonical witness construction.
Keeping it in the core makes the trace shape independent of the public facade and of
either frontend.
"""

from __future__ import annotations

from .protocol import receptive_controls
from .refusal import Refusal, Refused
from .stimulus import EventSlot, Stimulus


def reset_then(contract, rows=(), endpoint=None, controls=None, source="",
               drain=None, ticks=0, reset_hold_cycles=None):
    """Reset according to ``contract``, offer ``rows``, then drain.

    ``offers`` is the sole authority for an input endpoint's valid signal.  Scheduled
    controls therefore contain reset and environment-owned controls, but not a second
    copy of source valid.
    """

    # The body slots and the endpoint queue are two views of the same finite
    # sequence.  Materialize it once so a generator cannot be consumed while
    # constructing the slots and then silently produce an empty queue.
    rows = tuple(tuple(row) for row in rows)
    inputs = contract.inputs()
    if endpoint is None:
        if len(inputs) > 1:
            raise Refused(Refusal(
                "stimulus-endpoint-ambiguous",
                "this contract has more than one input endpoint, so which one these "
                "rows are for has to be said",
                endpoints=[item.name for item in inputs]))
        endpoint = inputs[0].name if inputs else None
    if endpoint is None and rows:
        raise Refused(Refusal(
            "stimulus-no-input-endpoint",
            "this contract has no input endpoint, so there is nowhere to offer these "
            "rows; a task that only produces is driven by ticks",
            rows=len(rows)))

    source_ep = contract.endpoint(endpoint) if endpoint else None
    if not contract.clocks:
        if reset_hold_cycles not in (None, 0) or ticks or controls:
            raise Refused(Refusal(
                "stimulus-settled-timing", "settled input steps have no reset, clock ticks or controls"))
        return _framed(contract, [EventSlot(offers=[endpoint]) for _ in rows],
                       {endpoint: rows} if endpoint else {}, drain, {}, source=source)
    domain = contract.clock_domain(
        source_ep.clock if source_ep is not None else contract.clocks[0].name)
    names = contract.scheduled_controls()
    asserted = 0 if domain.reset_active_low else 1
    released = 1 - asserted
    held = {name: 0 for name in names}
    held[domain.reset] = asserted
    hold = domain.reset_hold_cycles if reset_hold_cycles is None \
        else int(reset_hold_cycles)

    slots = [EventSlot(reset=True, controls=held) for _ in range(hold)]
    driving = {name: 0 for name in names}
    driving[domain.reset] = released
    driving.update({key: value for key, value in (controls or {}).items()
                    if key in names})
    slots.extend(EventSlot(offers=[endpoint], controls=driving) for _ in rows)
    slots.extend(EventSlot(ticks=[domain.name], controls=driving)
                 for _ in range(ticks))
    queues = {endpoint: list(rows)} if endpoint else {}
    return _framed(
        contract, slots, queues, drain, driving,
        controls_given=controls, source=source)


def framed(contract, inner, queues=None, endpoint=None, controls=None, source="",
           drain=None, memory_policy=None, initial_state=None,
           reset_hold_cycles=None, memory_environment=None):
    """Frame existing body slots with the contract's reset and a drain suffix."""

    if not contract.clocks:
        if reset_hold_cycles not in (None, 0) or controls:
            raise Refused(Refusal(
                "stimulus-settled-timing", "settled input steps have no reset or controls"))
        return _framed(contract, tuple(inner), dict(queues or {}), drain, {},
                       source=source, memory_policy=memory_policy,
                       initial_state=initial_state, memory_environment=memory_environment)

    inputs = contract.inputs()
    if endpoint is None:
        endpoint = inputs[0].name if inputs else None
    source_ep = contract.endpoint(endpoint) if endpoint else None
    domain = contract.clock_domain(
        source_ep.clock if source_ep is not None else contract.clocks[0].name)
    names = contract.scheduled_controls()
    asserted = 0 if domain.reset_active_low else 1
    held = {name: 0 for name in names}
    held[domain.reset] = asserted
    driving = {name: 0 for name in names}
    driving[domain.reset] = 1 - asserted
    driving.update({key: value for key, value in (controls or {}).items()
                    if key in names})
    body = [
        EventSlot(
            offers=slot.offers,
            ticks=slot.ticks,
            reset=slot.reset,
            controls=held if slot.reset else driving,
        )
        for slot in inner
    ]
    hold = domain.reset_hold_cycles if reset_hold_cycles is None \
        else int(reset_hold_cycles)
    slots = [EventSlot(reset=True, controls=held) for _ in range(hold)] + body
    return _framed(
        contract, slots, dict(queues or {}), drain, driving,
        controls_given=controls, source=source,
        memory_policy=memory_policy, initial_state=initial_state,
        memory_environment=memory_environment)


def idle_tail(contract, slots, count, stall_every=None, controls=None):
    """Return a receptive drain with no new offers or explicit ticks.

    Back-pressure is an injection action.  During drain the environment accepts every
    ready/valid output so a full output buffer cannot prevent the design from accepting
    work that was already released.  ``stall_every`` remains accepted for source
    compatibility, but deliberately has no effect on a drain.
    """

    count = max(0, int(count or 0))
    if not count:
        return []
    if not contract.clocks:
        raise Refused(Refusal(
            "stimulus-settled-drain", "a settled step needs an input value; it has no idle drain"))
    names = set(contract.scheduled_controls())
    ready = receptive_controls(contract)
    base = ({key: value for key, value in dict(slots[-1].controls).items()
             if key in names}
            if slots else {name: 0 for name in names})
    for domain in contract.clocks:
        base[domain.reset] = 1 if domain.reset_active_low else 0
    base.update({key: value for key, value in dict(controls or {}).items()
                 if key in names})
    for signal in ready:
        base[signal] = 1

    tail = []
    for _index in range(len(slots), len(slots) + count):
        tail.append(EventSlot(controls=base))
    return tail


def _framed(contract, slots, queues, drain, driving,
            controls_given=None, source="", memory_policy=None,
            initial_state=None, memory_environment=None):
    """Append the drain and construct the immutable stimulus record."""

    drain = (1 if contract.clocks else 0) if drain is None else int(drain)
    tail = idle_tail(
        contract, list(slots), drain,
        controls={**dict(driving), **dict(controls_given or {})})
    return Stimulus(
        queues=queues,
        slots=list(slots) + tail,
        source=source,
        memory_policy=memory_policy,
        memory_environment=memory_environment,
        initial_state=initial_state,
    )


__all__ = ["framed", "idle_tail", "reset_then"]
