"""Runtime validation's facts replace a second walk, never the monitor checks."""

from dataclasses import replace

import pytest

from behave.sim import api
from behave.sim.core.atomic import run_atomic_in_memory
from behave.sim.core.driver import run_in_memory
from behave.sim.core.monitor import monitor_for
from behave.sim.core.protocol import (
    AdapterChannel,
    AdapterDrive,
    AdapterProgram,
    AdapterTransition,
    ProtocolFacts,
    adapter_program_for,
)
from behave.sim.core.runtime_schedule import validate_resolution
from behave.sim.runtime_execution import declared_completion_targets
from tests.sim.test_runtime_multiclock_schedule import ClockedQueue
from tests.sim.test_runtime_multiclock_schedule import contract as atomic_contract
from tests.sim.test_runtime_schedule import DelayedEcho, _contract
from tests.sim.test_sampled_runtime import Echo, sampled_contract


def report_image(report):
    return ({name: [item.as_dict() for item in getattr(report, name)] for name in (
        "hits", "violations", "assumption_violations", "unresolved")},
        report.opportunities, report.unchecked)


def compare_reports(task, request, trace, expected_outputs, monkeypatch):
    monitor = monitor_for(task)
    report = monitor.check(trace.stimulus, trace)
    oracle = report_image(report)
    stop = (report.violations[0].as_dict()
            if trace.completion is api.Completion.INCOMPLETE and report.violations else None)
    facts = validate_resolution(task, request, trace.stimulus, trace.records,
                                expected_outputs, collect_protocol=True, protocol_stop=stop)
    assert facts

    def duplicate(*args, **kwargs):
        raise AssertionError("a second protocol replay was requested")

    monkeypatch.setattr(AdapterProgram, "replay", duplicate)
    assert report_image(monitor.check(trace.stimulus, trace, _protocol_ledgers=facts)) == oracle


@pytest.mark.parametrize("wait,latency", [(0, 0), (11, 5), (300, 17)])
def test_single_clock_preserves_complete_monitor_report(wait, latency, monkeypatch):
    task = _contract()
    policy = api.ReplayPolicy(2, latency + 2, 3)
    request = api.Stimulus(queues={"in": [(n,) for n in range(100)]}, replay_policy=policy)
    trace = run_in_memory(task, request, DelayedEcho(wait, latency))
    compare_reports(task, request, trace, {"out": 100}, monkeypatch)


def test_recovered_facts_still_report_unstable_stalled_output(monkeypatch):
    class UnstableEcho(DelayedEcho):
        def settle(self, presented, controls):
            outputs = super().settle(presented, controls)
            if outputs["ov"]:
                outputs["y"] ^= self.time & 1
            return outputs

    task = _contract()
    request = api.Stimulus(queues={"in": [(n,) for n in range(20)]},
                           replay_policy=api.ReplayPolicy(2, 3, 3))
    trace = run_in_memory(task, request, UnstableEcho(0, 0))
    assert monitor_for(task).check(trace.stimulus, trace).violations
    compare_reports(task, request, trace, {"out": 20}, monkeypatch)


@pytest.mark.parametrize("sampled", [False, True])
def test_atomic_facts_keep_global_indexes_and_domain_reset_semantics(sampled, monkeypatch):
    task = sampled_contract() if sampled else atomic_contract()
    policy = api.ReplayPolicy.for_contract(task)
    if sampled:
        request = api.build_baseline(task, policy=api.BaselinePolicy(case_limit=1, max_queue_depth=12)).stimuli[0]
        device = Echo()
    else:
        request = api.Stimulus(queues={"in": [(n,) for n in range(15)]},
                              replay_policy=replace(policy, drain_slots=4))
        device = ClockedQueue(29)
    counts = declared_completion_targets(task, request)
    trace = run_atomic_in_memory(task, request, device, expected_outputs=counts)
    compare_reports(task, request, trace, counts, monkeypatch)


def test_recovery_still_rejects_a_forged_transfer_before_monitoring():
    task = _contract()
    request = api.Stimulus(queues={"in": [(5,)]}, replay_policy=api.ReplayPolicy(1, 2, 0))
    trace = run_in_memory(task, request, DelayedEcho(0, 0))
    records = list(trace.records)
    for index, record in enumerate(records):
        if dict(record.accepted).get("in") is not None:
            accepted = dict(record.accepted)
            accepted["in"] = (99,)
            records[index] = api.SlotRecord(accepted=accepted, outputs=dict(record.outputs))
            break
    with pytest.raises(api.Refused, match="runtime-schedule-transfer-mismatch"):
        validate_resolution(task, request, trace.stimulus, records, {"out": 1}, collect_protocol=True)


@pytest.mark.parametrize("offered,transferred,live,reused", (
    (False, False, False, True),
    (True, False, False, False),  # first stalled offer starts now
    (True, False, True, True),   # continuing offer does not start again
    (None, None, False, False),  # absent accepted record means no recorded transfer
))
def test_channel_reuse_preserves_offer_history_and_recorded_transfer(
        offered, transferred, live, reused):
    task = _contract()
    observed = []
    facts = ProtocolFacts(task, adapter_program_for(task), observer=observed.append)
    facts.live["out"] = live
    image = AdapterChannel(offered=offered, ready=False, transferred=transferred)
    transition = AdapterTransition(state={}, channels={
        "in": AdapterChannel(offered=False, ready=False, transferred=False),
        "out": image,
    })
    facts.record(0, api.EventSlot(), {}, AdapterDrive({}, {"in": None}),
                 transition, api.SlotRecord(accepted={"in": None, "out": None}),
                 {"in": 0})
    channel = facts.slots[0].channels["out"]
    assert (channel is image) is reused
    assert channel.started is (bool(offered) and not live)
    assert channel.transferred is False
    assert channel.offered is offered
    assert observed[0].channels["out"] is channel
    assert facts.live["out"] is bool(offered)
    with pytest.raises(AttributeError):
        channel.transferred = True


def test_channel_reuse_does_not_replace_recorded_input_payload():
    task = _contract()
    facts = ProtocolFacts(task, adapter_program_for(task))
    image = AdapterChannel(offered=True, ready=True, transferred=True,
                           payload=(1,), started=True)
    transition = AdapterTransition(state={}, channels={
        "in": image,
        "out": AdapterChannel(offered=False, ready=False, transferred=False),
    })
    facts.record(0, api.EventSlot(), {}, AdapterDrive({}, {"in": (1,)}),
                 transition, api.SlotRecord(accepted={"in": (7,), "out": None}),
                 {"in": 0})
    assert facts.slots[0].channels["in"] is not image
    assert facts.slots[0].channels["in"].payload == (7,)
    assert image.payload == (1,)
