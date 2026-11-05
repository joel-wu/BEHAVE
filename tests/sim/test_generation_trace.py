"""Release, presentation and acceptance are distinct queue events."""

import pytest

from behave.sim import api
from behave.sim.generation_trace import QueueOrigin, queue_origins, queue_release_slots

from .test_api_path import contract


def _trace(seed, rows):
    return api.AuditedTrace(seed, records=[api.SlotRecord(accepted=row) for row in rows])


def test_pending_rows_first_present_during_drain_and_survive_reset():
    # Equal payloads deliberately cannot identify which queue row transferred.
    seed = api.Stimulus({"in": [(0,), (0,), (0,)]}, [
        api.EventSlot(reset=True, offers=("in",)),
        api.EventSlot(offers=("in",)),
        api.EventSlot(offers=("in",)),
        api.EventSlot(offers=("in",)),
        api.EventSlot(),
        api.EventSlot(reset=True),
        api.EventSlot(),
    ])
    rows = [{}, {}, {}, {"in": (0,)}, {"in": (0,)}, {}, {"in": (0,)}]
    trace = _trace(seed, rows)
    assert queue_origins(contract(), seed, trace) == {
        ("in", 0): QueueOrigin(1, 1),
        ("in", 1): QueueOrigin(2, 4),
        ("in", 2): QueueOrigin(3, 6),
    }
    from behave.sim.engines.rtl.generation import _segments_from_trace

    segments = _segments_from_trace(contract(), seed, trace)
    assert [(segment.mutable_fields[0].source_position, segment.start, segment.stop)
            for segment in segments] == [(1, 1, 4), (2, 4, 6), (3, 6, 7)]


def test_independent_domains_use_only_their_active_nonreset_slots():
    task = api.TaskContract([
        api.Endpoint(name, "in", [api.Field("v", api.FiniteType(8), name)],
                     api.ready_valid("in", name + "_valid", name + "_ready"),
                     clock=clock)
        for name, clock in (("a", "c1"), ("b", "c2"))],
        [api.ClockDomain("c1", "c1", "r1"), api.ClockDomain("c2", "c2", "r2")])
    seed = api.Stimulus({"a": [(0,)], "b": [(0,)]}, [
        api.EventSlot(offers=("a", "b"), ticks=("c1",)),
        api.EventSlot(offers=("b",), ticks=("c2",), resets=("c2",)),
        api.EventSlot(offers=("b",), ticks=("c2",)),
        api.EventSlot(ticks=("c1", "c2")),
    ])
    trace = _trace(seed, [{}, {}, {}, {"a": (0,), "b": (0,)}])
    assert queue_origins(task, seed, trace) == {
        ("a", 0): QueueOrigin(0, 0), ("b", 0): QueueOrigin(2, 2)}


def test_acceptance_without_released_work_is_not_an_origin():
    seed = api.Stimulus({"in": [(0,)]}, [api.EventSlot()])
    with pytest.raises(ValueError, match="no pending presentation"):
        queue_release_slots(contract(), seed, [{"in": (0,)}])


@pytest.mark.parametrize("with_payload", (False, True))
def test_payload_free_presentations_keep_observations_and_interval_boundaries(with_payload):
    from behave.sim.engines.rtl.generation import _segments_from_trace

    endpoints = [api.Endpoint("tick", "in", (), api.sampled_edge(
        "in", valid="tick_valid"), "clk")]
    queues = {"tick": [(), ()]}
    if with_payload:
        endpoints.append(api.Endpoint("data", "in", [api.Field(
            "v", api.FiniteType(8), "value")], api.sampled_edge(
                "in", valid="data_valid"), "clk"))
        queues["data"] = [(4,), (5,)]
    task = api.TaskContract(endpoints, [api.ClockDomain("clk", "clk", "rst")])
    seed = api.Stimulus(queues, [
        api.EventSlot(offers=("data",) if with_payload else ()),
        api.EventSlot(offers=("tick",)),
        api.EventSlot(offers=("data",) if with_payload else ()),
        api.EventSlot(offers=("tick",)),
    ])
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(
        accepted=accepted, hits={"tick-observed": index in (1, 3)})
        for index, accepted in enumerate([
            {"data": (4,)} if with_payload else {}, {"tick": ()},
            {"data": (5,)} if with_payload else {}, {"tick": ()}])])
    image = trace.as_dict()
    origins = queue_origins(task, seed, trace)
    assert origins["tick", 0] == QueueOrigin(1, 1)
    assert origins["tick", 1] == QueueOrigin(3, 3)
    segments = _segments_from_trace(task, seed, trace)
    assert [(segment.start, segment.stop) for segment in segments] == (
        [(0, 1), (2, 3)] if with_payload else [])
    assert trace.as_dict() == image


def test_plain_payload_free_counter_selects_no_mutation_but_keeps_every_tick():
    from behave.sim.engines.rtl.generation import _segments_from_trace

    task = api.TaskContract([api.Endpoint(
        "tick", "in", (), api.sampled_edge("in"), "clk")],
        [api.ClockDomain("clk", "clk", "rst")])
    seed = api.Stimulus({"tick": [(), (), ()]},
                       [api.EventSlot(offers=("tick",)) for _ in range(3)])
    trace = _trace(seed, [{"tick": ()} for _ in range(3)])
    assert len(queue_origins(task, seed, trace)) == 3
    assert _segments_from_trace(task, seed, trace) == ()
    assert len(trace.records) == 3


@pytest.mark.parametrize("kind", (
    "ready-valid", "start-done", "apb", "axi-write", "axi-read", "product",
    "composition", "settled", "sampled-00", "sampled-01", "sampled-10", "sampled-11",
))
@pytest.mark.parametrize("seed", (10, 12))
def test_selection_origins_match_actual_driver_presentations(kind, seed, monkeypatch):
    from behave.sim.core.driver import Driver
    from .test_driver_preparation import _case, _run

    task, stimulus, _device = _case(kind, seed)
    driven, _states, trace_image, _audit = _run(Driver, kind, seed)
    trace = api.AuditedTrace.from_dict(trace_image)
    next_release = {endpoint.name: 0 for endpoint in task.inputs()}
    next_accept = dict(next_release)
    releases, expected = {}, {}
    for slot, ((_, presented), record) in enumerate(zip(driven, trace.records, strict=True)):
        scheduled = stimulus.slots[slot]
        if not scheduled.reset:
            for name in scheduled.offers:
                if next_release[name] < stimulus.depth(name):
                    releases[name, next_release[name]] = slot
                    next_release[name] += 1
        # Independent oracle: the payloads actually supplied to the device,
        # not a simulated ready/valid or busy test in this expectation.
        for name, payload in presented.items():
            if payload is not None:
                key = name, next_accept[name]
                expected.setdefault(key, QueueOrigin(releases[key], slot))
        for name, payload in record.accepted:
            if name in next_accept and payload is not None:
                next_accept[name] += 1
    from behave.sim.core.protocol import ProtocolFacts, adapter_program_for

    def no_score_ledger(*args, **kwargs):
        pytest.fail('query selection must not construct a duplicate scoring ledger')

    with monkeypatch.context() as patch:
        patch.setattr(ProtocolFacts, 'record', no_score_ledger)
        assert queue_origins(task, stimulus, trace) == expected

    reconstructed = adapter_program_for(task).replay(task, stimulus, trace)
    assert all(row.drive is None for row in reconstructed)
    reconstructed = adapter_program_for(task).replay(task, stimulus, trace, retain_drives=True)
    assert [(dict(row.drive.controls), dict(row.drive.presentations))
            for row in reconstructed] == driven


def test_protocol_transition_iteration_does_not_materialize_future_slots(monkeypatch):
    from behave.sim.core.protocol import adapter_program_for

    task = contract()
    seed = api.Stimulus({'in': [(3,), (4,)]}, [
        api.EventSlot(offers=('in',)), api.EventSlot(offers=('in',))])
    trace = _trace(seed, [{'in': (3,)}, {'in': (4,)}])
    program = adapter_program_for(task)
    observed = []
    step = type(program).step

    def counted(self, contract, state, slot, drive, signals, *, strict=True):
        observed.append(slot)
        return step(self, contract, state, slot, drive, signals, strict=strict)

    monkeypatch.setattr(type(program), 'step', counted)
    rows = program.iter_replay(task, seed, trace)
    assert observed == []
    first = next(rows)
    assert len(observed) == 1
    assert first.index == 0
    assert tuple(first.releases()) == (('in', 0),)
    assert tuple(first.presentations()) == (('in', 0),)
    second = next(rows)
    assert len(observed) == 2
    assert second.queues is first.successor_queues
    assert tuple(second.releases()) == (('in', 1),)
    assert tuple(second.presentations()) == (('in', 1),)
    with pytest.raises(StopIteration):
        next(rows)
