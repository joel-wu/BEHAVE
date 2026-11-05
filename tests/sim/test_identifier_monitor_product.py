"""Finite ID correlation against the independent public concrete cursor."""

import random

import pytest

from behave.sim import api
from behave.sim.core.ledger import ChannelSlot, SlotFact
from behave.sim.core.monitor import Monitor, ProtocolMonitorCursor
from behave.sim.core.ready_valid_product import (
    ConcreteAlgebra, OrderedReadyValidEvent, ordered_ready_valid_initial,
    ordered_ready_valid_step,
)


def _monitor(answers=1, policy="clears"):
    endpoints = []
    for direction in ("in", "out"):
        options = dict(max_outstanding=4, response_deadline=2,
                       reset_policy=None if policy is None else api.ResetPolicy(policy)) if direction == "in" else dict(
                           answers_per_transaction=answers, matching=api.Matching.IDENTIFIER, identifier="id")
        endpoints.append(api.Endpoint(direction, direction,
            [api.Field("id", api.FiniteType(3), f"{direction}_id")],
            api.ready_valid(direction, f"{direction}_valid", f"{direction}_ready"), "clk",
            kind=api.Kind.TRANSACTION, **options))
    return Monitor(api.TaskContract(endpoints, [api.ClockDomain("clk", "clk", "rst")]))


def _event(slot, accepted=None, offered=None, ready=True, reset=False):
    return OrderedReadyValidEvent(reset, slot, accepted is not None, offered is not None,
                                 ready, (offered or 0,), (accepted or 0,))


def _advance(cursor, event):
    cursor.advance(SlotFact(event.slot, event.reset, {
        "in": ChannelSlot("in", offered=event.input_accepted, ready=True,
            transferred=event.input_accepted, payload=event.input_key, started=event.input_accepted),
        "out": ChannelSlot("out", offered=event.output_valid, ready=event.output_ready,
            transferred=event.output_valid and event.output_ready, payload=event.output_payload,
            started=event.output_valid and cursor.held["out"][0] is None)}))


@pytest.mark.parametrize("answers", (1, 2))
@pytest.mark.parametrize("policy", ("clears", "keeps"))
@pytest.mark.parametrize("seed", range(8))
def test_identifier_rule_matches_independent_cursor(answers, policy, seed):
    monitor = _monitor(answers, policy)
    config = monitor.finite_channel_config(64)
    assert monitor.ordered_channel_config(64) is None
    cursor, rng = ProtocolMonitorCursor(monitor), random.Random(seed)
    state = ordered_ready_valid_initial(config, ConcreteAlgebra)
    for slot in range(64):
        unused = sorted(set(range(8)) - {item["key"] for item in cursor.pending})
        accepted = rng.choice(unused) if unused and rng.randrange(2) else None
        offered = rng.randrange(8) if rng.randrange(2) else None
        event = _event(slot, accepted, offered, bool(rng.randrange(2)), rng.randrange(13) == 0)
        before = len(cursor.found)
        _advance(cursor, event)
        stepped = ordered_ready_valid_step(config, state, event, ConcreteAlgebra)
        state = stepped.state
        assert not cursor.outside
        assert not stepped.details["identifier_reused"]
        assert {goal for goal, hit in stepped.hits.items() if hit} == {
            item.goal for item in cursor.found[before:]}, (seed, slot, event)
        actual = [(key, owed, at, offered, reported)
                  for present, key, owed, at, offered, reported in zip(
                      state.pending, state.keys, state.owed, state.accepted_at, state.offered, state.reported)
                  if present]
        expected = [(item["key"], item["owed"], item["at"], item["offered_at"] is not None, item["reported"])
                    for item in cursor.pending]
        assert actual == expected, (seed, slot, event, actual, expected)


@pytest.mark.parametrize("simultaneous_retire", (False, True))
def test_reusing_inflight_id_is_environment_failure_not_an_artifact_goal(simultaneous_retire):
    monitor = _monitor()
    config, cursor = monitor.finite_channel_config(3), ProtocolMonitorCursor(monitor)
    state = ordered_ready_valid_initial(config, ConcreteAlgebra)
    for event in (_event(0, 2), _event(1, 2, 2 if simultaneous_retire else None)):
        _advance(cursor, event)
        stepped = ordered_ready_valid_step(config, state, event, ConcreteAlgebra)
        state = stepped.state
    assert stepped.details["identifier_reused"]
    assert {item.goal for item in cursor.outside} == {"environment.identifier-reused-while-outstanding"}
    assert not any(stepped.hits.values())


def test_out_of_order_response_does_not_retire_head_or_credit_its_deadline():
    config = _monitor().finite_channel_config(3)
    state = ordered_ready_valid_initial(config, ConcreteAlgebra)
    for event in (_event(0, 1), _event(1, 2), _event(2, offered=2), _event(3)):
        stepped = ordered_ready_valid_step(config, state, event, ConcreteAlgebra)
        state = stepped.state
    assert state.pending == (True, False, False)
    assert stepped.details["deadline_hits"] == (True, False, False)


def test_undeclared_reset_remains_a_definedness_fault_not_an_id_assumption():
    monitor = _monitor(policy=None)
    config, cursor = monitor.finite_channel_config(2), ProtocolMonitorCursor(monitor)
    state = ordered_ready_valid_initial(config, ConcreteAlgebra)
    for event in (_event(0, 1), _event(1, reset=True)):
        _advance(cursor, event)
        stepped = ordered_ready_valid_step(config, state, event, ConcreteAlgebra)
        state = stepped.state
    assert cursor.correlation_lost and cursor.unresolved
    assert stepped.details["reset_policy_unresolved"]
    assert not stepped.details["identifier_reused"]
    assert state.pending[0]


@pytest.mark.parametrize(("kind", "value"), (
    (api.FiniteType(3, signed=True), -1),
    (api.ValueType.boolean(), True),
    (api.ValueType.enumeration(("idle", "busy", "done")), "busy"),
))
def test_packed_key_projection_respects_field_order_and_idle_typed_keys(kind, value):
    from behave.sim.core.physical_value import pack_value
    from .test_rtl_source_monitors import _monitors

    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("data", api.FiniteType(2), "in_data"), api.Field("id", kind, "in_id")],
            api.ready_valid("in", "iv", "ir"), "clk", kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("id", kind, "out_id"), api.Field("data", api.FiniteType(2), "out_data")],
            api.ready_valid("out", "ov", "ordy"), "clk", kind=api.Kind.TRANSACTION,
            matching=api.Matching.IDENTIFIER, identifier="id", answers_per_transaction=1),
    ], [api.ClockDomain("clk", "clk", "rst")])
    product = _monitors(contract)
    state = product.initial(ConcreteAlgebra, lambda value, _: value)
    for slot, payload in enumerate(((2, value), None)):
        keys = product.request_keys({"in": payload}, pack_value)
        assumptions = {}
        state, hits = product.step(state, slot, api.EventSlot(ticks=("clk",), controls={"ordy": 1}),
            {"in": payload is not None}, {"ov": 1, "out_id": pack_value(kind, value), "out_data": 2}, {},
            algebra=ConcreteAlgebra, boolean=bool, word=lambda value, _: value,
            input_keys=keys, assumptions=assumptions)
        assert not any(assumptions.values())
        assert hits["monitor.output-without-transaction"] == (slot == 1)

