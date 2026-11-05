"""Input/result progress, not a preallocated horizon, determines the tail."""

from itertools import islice

import pytest

from behave.core import runtime_defaults as defaults
from behave.sim import api
from behave.sim.core.driver import Driver, run_in_memory
from behave.sim.core.runtime_schedule import ReplayPolicy


def _contract(adapter=None, *, ratio=1):
    return api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(8), "a")],
                     adapter or api.ready_valid("in", "iv", "ir"), clock="clk",
                     kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "y")],
                     api.sampled_edge("out") if adapter is not None else
                     api.ready_valid("out", "ov", "orr"), clock="clk",
                     kind=api.Kind.TRANSACTION, answers_per_transaction=ratio)],
        [api.ClockDomain("clk", "clk", "rst")])


class DelayedEcho:
    identity = "runtime-delayed-echo"

    def __init__(self, wait, latency):
        self.wait, self.latency = wait, latency

    def reset(self):
        self.time = 0
        self.pending = []
        self.incoming = None
        self.outgoing = False

    def settle(self, presented, controls):
        ready = self.time >= self.wait
        valid = bool(self.pending and self.pending[0][0] <= self.time)
        self.incoming = presented if ready and not controls["rst"] else None
        self.outgoing = valid and bool(controls["orr"])
        return {"ir": int(ready), "ov": int(valid),
                "y": self.pending[0][1] if valid else 0}

    def edge(self):
        if self.outgoing:
            self.pending.pop(0)
        if self.incoming is not None:
            self.pending.append((self.time + self.latency, self.incoming[0]))
        self.time += 1


@pytest.mark.parametrize("wait,latency", [(0, 0), (10, 4), (300, 17), (6000, 0), (0, 6000)])
def test_100_inputs_complete_without_a_total_horizon_and_replay_identically(wait, latency):
    contract = _contract()
    request = api.Stimulus(queues={"in": [(i,) for i in range(100)]})
    policy = ReplayPolicy(reset_hold_cycles=2, drain_slots=latency + 2, stall_every=3)
    trace = run_in_memory(contract, request, DelayedEcho(wait, latency), execution_policy=policy)
    assert request.slots == ()  # The request is never rewritten into its result.
    assert trace.accepted_count(["in"]) == 100
    outputs = [dict(row.accepted).get("out") for row in trace.records]
    assert [value for value in outputs if value is not None] == [(i,) for i in range(100)]
    last_input = max(i for i, row in enumerate(trace.records)
                     if dict(row.accepted).get("in") is not None)
    last_output = max(i for i, value in enumerate(outputs) if value is not None)
    assert len(trace.records) == max(last_input, last_output) + 1 + policy.drain_slots
    assert all(not slot.offers and dict(slot.controls)["orr"] == 1
               for slot in trace.stimulus.slots[last_input + 1:])
    if wait == 300:
        assert len(trace.records) > 400
    replay = run_in_memory(contract, trace.stimulus, DelayedEcho(wait, latency))
    assert replay.as_dict() == trace.as_dict()


def test_waiting_is_not_a_successful_empty_or_truncated_run():
    request = api.Stimulus(queues={"in": [(7,), (8,)]})
    driver = Driver(_contract(), request, execution_policy=ReplayPolicy(1, 3, 0))
    for index, _slot in islice(driver.iter_slots(), 600):
        driver.observe(index, driver.presentations(index), {"ir": 0, "ov": 0, "y": 0})
    assert driver.taken == 0
    assert driver.present(599) == (7,)
    with pytest.raises(api.Refused, match="driver-incomplete"):
        driver.trace()
    with pytest.raises(api.Refused, match="driver-incomplete"):
        driver.completed_stimulus()


def test_repeated_slots_share_storage_but_keep_every_observation():
    from behave.sim.core.record_value import canonical_json

    request = api.Stimulus(queues={"in": [(7,)]})
    driver = Driver(_contract(), request, execution_policy=ReplayPolicy(0, 2, 0))
    slots = driver.iter_slots()
    recorded_slots = []
    signals = [{"ir": 0, "ov": 0, "y": value} for value in (0, 0, False, 1, 1)]
    for sample in signals:
        index, slot = next(slots)
        recorded_slots.append(slot)
        driver.observe(index, driver.presentations(index), sample)
    assert len(driver.records) == len(recorded_slots) == 5
    assert recorded_slots[0].offers == ("in",)
    assert not recorded_slots[1].offers  # Released once, still pending at the pins.
    assert all(slot is recorded_slots[1] for slot in recorded_slots[1:])
    assert driver.records[0] is driver.records[1]
    assert driver.records[1] is not driver.records[2]  # bool is not int
    assert driver.records[3] is driver.records[4]
    assert [dict(row.outputs)["y"] for row in driver.records] == [0, 0, False, 1, 1]
    index, slot = next(slots)
    driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 1, "y": 7})
    assert driver.records[-1] is not driver.records[-2]
    for index, slot in slots:
        driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 0, "y": 7})
    trace = driver.trace()
    assert trace.accepted_count(["in"]) == trace.accepted_count(["out"]) == 1
    assert len(trace.records) == len(trace.stimulus.slots) == 8
    assert not trace.stimulus.slots[-1].offers
    assert canonical_json(trace.as_dict()) == canonical_json(api.AuditedTrace.from_dict(
        trace.as_dict()).as_dict())


def test_terminal_failure_does_not_invent_future_execution():
    request = api.Stimulus(queues={"in": [(7,)]})
    driver = Driver(_contract(), request, execution_policy=ReplayPolicy(0, 3, 0))
    index, _slot = next(driver.iter_slots())
    driver.fail(index, "candidate failed")
    trace = driver.trace()
    assert driver.completed_stimulus() == trace.stimulus
    assert not trace.completion.scores
    assert len(trace.records) == len(trace.stimulus.slots) == 1
    assert list(driver.iter_slots()) == []


def test_declared_multiple_responses_must_all_arrive_before_the_tail():
    request = api.Stimulus(queues={"in": [(7,)]})
    driver = Driver(_contract(ratio=2), request, execution_policy=ReplayPolicy(0, 1, 0))
    slots = driver.iter_slots()
    index, _slot = next(slots)
    driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 0, "y": 0})
    for index, _slot in islice(slots, 300):
        driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 0, "y": 0})
    for value in (7, 8):
        index, _slot = next(slots)
        driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 1, "y": value})
    index, _slot = next(slots)
    driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 0, "y": 0})
    assert list(slots) == []
    assert driver.trace().accepted_count(["out"]) == 2


def test_variable_output_count_is_not_silently_assumed_to_be_one():
    request = api.Stimulus(queues={"in": [(7,)]})
    with pytest.raises(api.Refused, match="runtime-schedule-completion-undeclared"):
        Driver(_contract(ratio=None), request, execution_policy=ReplayPolicy())


def test_plain_sampling_does_not_gain_undeclared_idle_transactions():
    contract = _contract(api.sampled_edge("in"))
    request = api.Stimulus(queues={"in": [(7,), (8,)]})
    with pytest.raises(api.Refused, match="runtime-schedule-continuous-input"):
        Driver(contract, request, execution_policy=ReplayPolicy(1, 3, 0))
    driver = Driver(contract, request, execution_policy=ReplayPolicy(1, 0, 0))
    for index, _slot in driver.iter_slots():
        driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 0, "y": 0})
    assert driver.trace().accepted_count(["in"]) == 2


def test_runtime_defaults_and_fixed_schedule_have_distinct_owners():
    policy = ReplayPolicy()
    assert policy.reset_hold_cycles == defaults.SIM_RESET_HOLD_CYCLES
    assert policy.drain_slots == defaults.SIM_DRAIN_SLOTS
    assert policy.stall_every == defaults.SIM_STALL_EVERY
    assert "horizon" not in policy.as_dict()
    request = api.Stimulus(queues={"in": [(7,)]}, slots=[api.EventSlot(
        offers=["in"], controls={"rst": 0, "orr": 1})])
    with pytest.raises(ValueError, match="also fix its schedule"):
        Driver(_contract(), request, execution_policy=policy)
