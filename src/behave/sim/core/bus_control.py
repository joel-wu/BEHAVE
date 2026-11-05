"""Shared concrete/symbolic APB and AXI4-Lite control transitions.

Payloads stay in the input queue until logical acceptance. Only AXI's response
code enters a control predicate; it must never be replaced by a zero payload.
Wait counters are arithmetic, not an enumeration of the task's wait allowance.
"""

from dataclasses import dataclass


def _wait_step(waits, waiting, limit, algebra):
    a = algebra
    if limit is None:
        return waits, a.bool(False)
    hit = a.land(waiting, a.eq(waits, limit))
    increment = a.ite(a.gt(waits, limit), waits, a.add(waits, 1))
    return a.ite(waiting, increment, waits), hit


@dataclass(frozen=True)
class APBControl:
    waiting: object
    waits: object
    complete: object
    wait_hit: object


def apb_control(waiting, waits, selected, ready, limit, algebra, *, readable=True):
    a = algebra
    access = a.land(selected, waiting)
    complete = a.land(access, ready)
    next_waits, hit = _wait_step(waits, a.land(access, readable, a.lnot(ready)), limit, a)
    held = a.land(selected, a.lnot(complete))
    return APBControl(held, a.ite(held, next_waits, a.integer(0)), complete, hit)


@dataclass(frozen=True)
class AXILiteControl:
    seen: tuple
    pending: object
    held: object
    waits: object
    assembled: object
    emitted: object
    early_hit: object
    code_hit: object
    wait_hit: object


def axi_lite_control(seen, pending, waits, presented, request_ready,
                     offered, response_ready, response_code, limit, algebra):
    a = algebra
    accepted = tuple(a.lor(old, a.land(presented, ready))
                     for old, ready in zip(seen, request_ready))
    assembled = a.land(presented, *accepted)
    emitted = a.land(offered, response_ready)
    held = a.land(presented, a.lnot(assembled))
    next_waits, wait_hit = _wait_step(waits, held, limit, a)
    return AXILiteControl(
        tuple(a.land(held, value) for value in accepted),
        a.lor(assembled, a.land(pending, a.lnot(emitted))), held,
        a.ite(held, next_waits, a.integer(0)), assembled, emitted,
        # A request assembled on this edge cannot justify pre-edge VALID.
        a.land(offered, a.lnot(pending)),
        a.land(offered, a.eq(response_code, 1)), wait_hit)
