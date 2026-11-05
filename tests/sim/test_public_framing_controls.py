"""Consumers get receptive pins from the declared protocol, not pin spelling."""

import pytest

from behave.sim import api
from behave.sim.core.protocol import receptive_controls


@pytest.mark.parametrize("form", ("ready-valid", "valid-only", "plain", "settled"))
def test_public_receptive_controls_do_not_invent_handshakes(form):
    clockless = form == "settled"
    if clockless:
        left, right = api.settled("in"), api.settled("out")
    elif form == "ready-valid":
        left = api.ready_valid("in", "request_present", "request_accept")
        right = api.ready_valid("out", "response_present", "response_accept")
    else:
        left = api.sampled_edge("in", valid="request_present" if form == "valid-only" else None)
        right = api.sampled_edge("out", valid="response_present" if form == "valid-only" else None)
    clock = None if clockless else "clk"
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(8), "a")],
                     left, clock, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "y")],
                     right, clock, kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1),
    ], [] if clockless else [api.ClockDomain("clk", "clk", "rst")])

    assert "receptive_controls" in api.__all__
    assert api.receptive_controls is receptive_controls
    assert api.receptive_controls(contract) == (
        ("response_accept",) if form == "ready-valid" else ())
    controls = {pin: 1 for pin in api.receptive_controls(contract)}
    stimulus = api.framed(contract, [api.EventSlot(offers=("in",))],
                          queues={"in": [(7,)]}, controls=controls, drain=0,
                          reset_hold_cycles=0 if clockless else 1)
    assert dict(stimulus.queues) == {"in": ((7,),)}
    assert all(dict(slot.controls).get(pin) == 1
               for slot in stimulus.slots if not slot.reset for pin in controls)
