"""Multi-clock run timing is explicit runtime policy, not a task-file horizon."""

from dataclasses import replace
from itertools import islice

import pytest

from behave.sim import api
from behave.sim.core.atomic import AtomicDriver, run_atomic_in_memory
from behave.sim.core.clock_schedule import ClockTiming, GlobalSchedule, periodic_slots
from behave.sim.core.runtime_schedule import validate_resolution
from behave.sim.runtime_execution import declared_completion_targets


def contract():
    return api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(8), "a")],
                     api.ready_valid("in", "iv", "ir"), clock="write",
                     kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "y")],
                     api.ready_valid("out", "ov", "orr"), clock="read",
                     kind=api.Kind.TRANSACTION, answers_per_transaction=1)],
        [api.ClockDomain("write", "wc", "wr"), api.ClockDomain("read", "rc", "rr")])


class ClockedQueue:
    identity = "runtime-two-clock-queue"

    def __init__(self, wait):
        self.wait = wait
        self.pending = []

    def atomic_step(self, event, drive):
        result = {}
        incoming = None
        if "write" in event.events:
            ready = event.time >= self.wait and "write" not in event.resets
            result["ir"] = int(ready)
            if ready and drive["iv"]:
                incoming = drive["a"]
        if "read" in event.events:
            valid = bool(self.pending) and "read" not in event.resets
            result.update(ov=int(valid), y=self.pending[0] if valid else 0)
            if valid and drive["orr"]:
                self.pending.pop(0)
        # Coincident edges both read the pre-slot queue.
        if incoming is not None:
            self.pending.append(incoming)
        return result


@pytest.mark.parametrize("wait", [0, 1500])
def test_runtime_atomic_progress_resolves_to_an_identical_fixed_replay(wait):
    task = contract()
    policy = replace(api.ReplayPolicy.for_contract(task), drain_slots=4)
    request = api.Stimulus(queues={"in": [(i,) for i in range(100)]}, replay_policy=policy)
    trace = run_atomic_in_memory(task, request, ClockedQueue(wait),
                                 expected_outputs=declared_completion_targets(task, request))
    assert api.transfers(trace, "in") == [(i,) for i in range(100)]
    assert api.transfers(trace, "out") == [(i,) for i in range(100)]
    assert len(trace.records) > 100
    if wait:
        assert len(trace.records) > 256
    assert request.slots == () and request.clock_schedule is None
    assert api.AuditedTrace.from_dict(trace.as_dict()).fingerprint == trace.fingerprint
    assert all(not slot.offers for slot in trace.stimulus.slots if slot.resets)
    validate_resolution(task, request, trace.stimulus, trace.records,
                        declared_completion_targets(task, request))
    fixed = run_atomic_in_memory(task, trace.stimulus, ClockedQueue(wait))
    assert [row.as_dict() for row in fixed.records] == [row.as_dict() for row in trace.records]


def test_unaccepted_atomic_inputs_do_not_complete_by_an_old_horizon():
    task = contract()
    request = api.Stimulus(queues={"in": [(7,)]}, replay_policy=api.ReplayPolicy.for_contract(task))
    driver = AtomicDriver(task, request, expected_outputs={"out": 1})
    device = ClockedQueue(10**9)
    for index, _ in islice(driver.iter_slots(), 300):
        drive = driver.drive(index)
        driver.observe(index, drive, device.atomic_step(driver.scheduled[index], drive.signals))
    assert driver.pointers["in"] == 0
    with pytest.raises(api.Refused, match="driver-incomplete"):
        driver.trace()
    with pytest.raises(api.Refused, match="driver-incomplete"):
        driver.completed_stimulus()


def _independent_interfaces(*, instances=True, missing_ratio=False, same_clock=False,
                            kind=api.Kind.TRANSACTION):
    endpoints = []
    for name, ratio in (("a", 2), ("b", 3)):
        endpoints.extend((
            api.Endpoint(name + "_in", "in", [api.Field("x", api.FiniteType(8), name + "_x")],
                         api.ready_valid("in", name + "_iv", name + "_ir"),
                         clock="clk" if same_clock else name, kind=kind),
            api.Endpoint(name + "_out", "out", [api.Field("y", api.FiniteType(8), name + "_y")],
                         api.ready_valid("out", name + "_ov", name + "_orr"),
                         clock="clk" if same_clock else name, kind=kind,
                         answers_per_transaction=None if missing_ratio and name == "b" else ratio)))
    return api.TaskContract(endpoints,
        ([api.ClockDomain("clk", "clk", "rst")] if same_clock else
         [api.ClockDomain(name, name + "_clk", name + "_rst") for name in ("a", "b")]),
        protocol_instances=([api.ProtocolInstance(name, (name + "_in", name + "_out"))
                             for name in ("a", "b")] if instances else ()))


@pytest.mark.parametrize("same_clock", (False, True))
def test_declared_interface_completion_uses_each_queue_and_response_ratio(same_clock):
    task = _independent_interfaces(same_clock=same_clock)
    request = api.Stimulus(queues={"a_in": [(1,)] * 7, "b_in": [(2,)] * 4},
                          replay_policy=api.ReplayPolicy.for_contract(task))
    assert declared_completion_targets(task, request) == {"a_out": 14, "b_out": 12}
    empty = api.Stimulus(queues={"a_in": [], "b_in": [(2,)] * 4},
                        replay_policy=api.ReplayPolicy.for_contract(task))
    assert declared_completion_targets(task, empty) == {"a_out": 0, "b_out": 12}


@pytest.mark.parametrize("instances,missing_ratio", [(False, False), (True, True)])
@pytest.mark.parametrize("same_clock", (False, True))
def test_completion_does_not_infer_missing_membership_or_cardinality(instances, missing_ratio, same_clock):
    task = _independent_interfaces(instances=instances, missing_ratio=missing_ratio, same_clock=same_clock)
    request = api.Stimulus(queues={"a_in": [(1,)], "b_in": [(2,)]},
                          replay_policy=api.ReplayPolicy.for_contract(task))
    with pytest.raises(api.Refused, match="runtime-completion-product-undeclared"):
        declared_completion_targets(task, request)


@pytest.mark.parametrize("same_clock", (False, True))
def test_answer_ratio_does_not_turn_undeclared_channel_transfers_into_transactions(same_clock):
    task = _independent_interfaces(same_clock=same_clock, kind=api.Kind.UNDECLARED)
    request = api.Stimulus(queues={"a_in": [(1,)], "b_in": [(2,)]},
                          replay_policy=api.ReplayPolicy.for_contract(task))
    with pytest.raises(api.Refused, match="runtime-completion-product-undeclared"):
        declared_completion_targets(task, request)


def test_periodic_runtime_prefix_preserves_coincident_edges_and_identity():
    timings = (ClockTiming("b", 3, reset="rb"), ClockTiming("a", 2, reset="ra"))
    policy = api.ReplayPolicy(clock_timings=timings)
    restored = api.ReplayPolicy.from_dict(policy.as_dict())
    assert restored.as_dict() == policy.as_dict()
    assert policy.as_dict() != replace(policy, clock_timings=(ClockTiming("a", 4), ClockTiming("b", 3))).as_dict()
    fixed = GlobalSchedule.periodic(timings, 20, reset_edges={"a": 3, "b": 2})
    assert [row.as_dict() for row in islice(periodic_slots(timings, reset_edges={"a": 3, "b": 2}), 20)] == [row.as_dict() for row in fixed.slots]


@pytest.mark.cosim
def test_runtime_atomic_rtl_observer_replays_without_a_fixed_schedule(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact

    rtl = tmp_path / "queue.sv"
    rtl.write_text("""module clocked_queue(
      input logic wc, wr, rc, rr,
      input logic iv, output logic ir, input logic [7:0] a,
      output logic ov, input logic orr, output logic [7:0] y);
      logic [7:0] cells [0:127];
      logic [7:0] wi, ri;
      assign ir = wi < 128;
      assign ov = ri < wi;
      assign y = cells[ri[6:0]];
      always @(posedge wc) begin
        if (wr) wi <= 0;
        else if (iv && ir) begin cells[wi[6:0]] <= a; wi <= wi + 1; end
      end
      always @(posedge rc) begin
        if (rr) ri <= 0;
        else if (ov && orr) ri <= ri + 1;
      end
    endmodule
    """)
    task = contract()
    request = api.Stimulus(queues={"in": [(i,) for i in range(100)]},
                          replay_policy=api.ReplayPolicy.for_contract(task))
    artifact = RTLArtifact(rtl, "clocked_queue", tmp_path / "replay")
    artifact.bind_observation(task)
    trace = artifact.trace(task, request, expected_outputs={"out": 100})
    assert api.transfers(trace, "in") == api.transfers(trace, "out") == [(i,) for i in range(100)]
    assert trace.request.fingerprint == request.fingerprint
    assert trace.stimulus.clock_schedule is not None
    validate_resolution(task, request, trace.stimulus, trace.records, {"out": 100})
