"""Incremental concrete execution must equal the complete finite transition."""

import random

import pytest

from behave.sim.core.ordered_monitor import OrderedConcreteMonitor
from behave.sim.core.ready_valid_product import OrderedReadyValidConfig, OrderedReadyValidEvent
from tests.sim.ordered_monitor_oracle import DenseOrderedOracle


@pytest.mark.parametrize("answers", (None, 1, 3))
@pytest.mark.parametrize("deadline", (None, 0, 2))
@pytest.mark.parametrize("capacity", (1, 8))
@pytest.mark.parametrize("outstanding", (None, 1))
@pytest.mark.parametrize("reset_policy", (None, "clears", "keeps"))
def test_every_state_component_and_observation_matches_finite_rule(
        answers, deadline, capacity, outstanding, reset_policy):
    config = OrderedReadyValidConfig(capacity, 2, answers, outstanding, deadline, reset_policy)
    for seed in range(4):
        rng = random.Random(seed)
        incremental, dense = OrderedConcreteMonitor(config), DenseOrderedOracle(config)
        for index in range(128):
            event = OrderedReadyValidEvent(
                reset=rng.randrange(17) == 0,
                # The executor does not assume consecutive or increasing slots.
                slot=index if seed % 2 else rng.randrange(160),
                input_accepted=bool(rng.randrange(2)),
                output_valid=bool(rng.randrange(2)),
                output_ready=bool(rng.randrange(2)),
                output_payload=(rng.randrange(3), (rng.randrange(3), rng.randrange(3))),
            )
            assert incremental.step(event) == dense.step(event), (seed, index, event)
            assert incremental.state_image() == dense.state_image(), (seed, index, event)
            assert incremental.pending_requests() == dense.pending_requests()


def test_deadline_precedes_offer_and_retirement_and_is_reported_once():
    config = OrderedReadyValidConfig(4, 1, 2, 1, 1)
    machine = OrderedConcreteMonitor(config)
    events = ((True, False, True), (True, False, True),
              (False, True, False), (False, True, True),
              (False, True, True), (False, True, True))
    got = [machine.step(OrderedReadyValidEvent(False, i, accepted, valid, ready, (5,)))
           for i, (accepted, valid, ready) in enumerate(events)]
    assert got[2].deadline_hits == ((0, 0),)
    assert got[3].deadline_hits == ((1, 1),)
    assert not got[4].deadline_hits and not got[5].deadline_hits
    assert machine.pending_requests() == [{"at": 1, "owed": 1, "reported": True}]


def test_retired_history_is_not_scanned_on_later_steps():
    class NoScan(list):
        def __iter__(self):
            raise AssertionError("per-cycle execution scanned retired requests")

    config = OrderedReadyValidConfig(1000000, 1, 1, 1, 1000)
    machine = OrderedConcreteMonitor(config)
    for index in range(100):
        machine.step(OrderedReadyValidEvent(False, index, True, True, True, (1,)))
    machine._history = NoScan(machine._history)
    for index in range(100, 1100):
        got = machine.step(OrderedReadyValidEvent(False, index, False, False, True, (0,)))
        assert not any(got.hits.values())
        assert got.outstanding == 0
        assert got.opportunities["monitor.response-deadline"] == 0


def test_payload_arity_still_rejects_before_a_reset():
    machine = OrderedConcreteMonitor(OrderedReadyValidConfig(1, 1, 1))
    with pytest.raises(ValueError, match="arity"):
        machine.step(OrderedReadyValidEvent(True, 0, False, False, False, ()))


def test_transfer_credits_first_offer_even_if_request_arrives_during_a_stall():
    from behave.sim.core.ledger import ChannelSlot, SlotFact
    from behave.sim.core.monitor import Monitor, ProtocolMonitorCursor
    from .test_monitor_obligations import _watched

    monitor = Monitor(_watched(answers=2, response_deadline=1))
    config = monitor.ordered_channel_config(1)
    dense, incremental = DenseOrderedOracle(config), OrderedConcreteMonitor(config)
    cursor = ProtocolMonitorCursor(monitor)
    for slot, (accepted, valid, ready) in enumerate(((False, True, False),
            (True, True, True), (False, False, True), (False, False, True))):
        event = OrderedReadyValidEvent(False, slot, accepted, valid, ready, (7,))
        cursor.advance(SlotFact(slot, False, {
            'in': ChannelSlot('in', offered=accepted, ready=True, transferred=accepted,
                              payload=(7,), started=accepted),
            'out': ChannelSlot('out', offered=valid, ready=ready, transferred=valid and ready,
                               payload=(7,), started=valid and slot == 0)}))
        actual = incremental.step(event)
        assert actual == dense.step(event)
        assert not any(actual.hits.values())
    assert not cursor.found
    assert cursor.pending[0]['offered_at'] == 1
    assert incremental.state_image()['offered'] == (True,)


@pytest.mark.parametrize("policy", (None, "clears", "keeps"))
def test_mid_traffic_reset_matches_independent_protocol_cursor(policy):
    from behave.sim import api
    from behave.sim.core.ledger import ChannelSlot, SlotFact
    from behave.sim.core.monitor import Monitor, ProtocolMonitorCursor
    from .test_monitor_obligations import _watched

    monitor = Monitor(_watched(answers=1, response_deadline=1,
        reset_policy=None if policy is None else api.ResetPolicy(policy)))
    config = monitor.ordered_channel_config(1)
    assert config.reset_policy == policy
    dense, incremental = DenseOrderedOracle(config), OrderedConcreteMonitor(config)
    cursor = ProtocolMonitorCursor(monitor)
    observations = []
    for slot in range(4):
        reset, accepted = slot == 1, slot == 0
        cursor.advance(SlotFact(slot, reset, {
            "in": ChannelSlot("in", offered=accepted, ready=True,
                transferred=accepted, payload=(0,), started=accepted),
            "out": ChannelSlot("out", offered=False, ready=True,
                transferred=False, payload=(0,))}))
        event = OrderedReadyValidEvent(reset, slot, accepted, False, True, (0,))
        got = incremental.step(event)
        assert got == dense.step(event)
        observations.append(got)
    if policy is None:
        assert [(row.slot, row.goal) for row in cursor.unresolved] == [(1, "monitor.correlation")]
        assert [i for i, row in enumerate(observations) if row.reset_policy_unresolved] == [1]
        # The exact continuation cannot prove anything once the domain is undefined.
    else:
        exact_hits = [(i, goal) for i, row in enumerate(observations)
                      for goal, hit in row.hits.items() if hit]
        assert exact_hits == [(row.slot, row.goal) for row in cursor.found]
        assert exact_hits == ([(2, "monitor.response-deadline")] if policy == "keeps" else [])
