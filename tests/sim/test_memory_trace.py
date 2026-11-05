"""Complete memory state remains recoverable without cumulative cell snapshots."""

import copy
import json
from types import SimpleNamespace

import pytest

from behave.sim.core.memory import (
    MemoryEnvironment,
    MemoryEnvironmentState,
    MemoryInterface,
    MemoryPort,
    MemoryRun,
    MemoryState,
    advance_memory_environment_with_writes,
    drive_memory_environment,
)
from behave.sim.core.memory_result import trace_memory_result
from behave.sim.core.memory_trace import (
    MEMORY_SUCCESSOR_VERSION,
    memory_successor_record,
    read_memory_successor,
    trace_memory_drives,
    trace_memory_states,
)
from behave.sim.core.record_value import freeze_record_value
from behave.sim.core.stimulus import EventSlot, SlotRecord


def _setup(*, latency=1, slots=128):
    interface = MemoryInterface("ram", 8, 16, "clk", [MemoryPort("r", "read"),
                                                     MemoryPort("w", "write")])
    run = MemoryRun("ram", initial={3: 77}, read_latency=latency,
                    ready={"read": [int(i % 3 != 0) for i in range(slots)],
                           "write": [int(i % 4 != 0) for i in range(slots)]})
    environment = MemoryEnvironment([run])
    return interface, environment


@pytest.mark.parametrize("latency", [1, 3, 7])
@pytest.mark.parametrize("frozen", [False, True])
def test_successor_records_reconstruct_every_read_first_boundary(latency, frozen):
    interface, environment = _setup(latency=latency)
    current = MemoryEnvironmentState.initial([interface], environment)
    decoded = current
    records = []
    for slot in range(128):
        address = slot % 23
        signals = {"ram__read_req_valid": 1, "ram__read_req_addr": address,
                   "ram__write_req_valid": int(slot % 5 != 0),
                   "ram__write_req_addr": address, "ram__write_req_wdata": slot % 11}
        current, writes = advance_memory_environment_with_writes(
            [interface], environment, current, slot, signals,
            active_domains=set() if slot % 9 == 0 else {"clk"})
        record = memory_successor_record(current, writes)
        decoded = read_memory_successor(decoded, freeze_record_value(record) if frozen else record)
        assert decoded == current
        assert decoded.digest == current.digest
        records.append(SlotRecord(environment={"external-memory": record}))
    trace = SimpleNamespace(stimulus=SimpleNamespace(memory_environment=environment), records=records)
    contract = SimpleNamespace(memories=(interface,))
    assert list(trace_memory_states(contract, trace))[-1] == current
    result = trace_memory_result(contract, trace, behavior=False)
    assert result.memories == (("ram", current.state("ram").cells),)


def test_successor_size_depends_on_current_writes_not_historical_cells(monkeypatch):
    states = [MemoryEnvironmentState({"ram": MemoryState(
        cells={i: i for i in range(size)}, writes={i: i for i in range(size)},
        pending={None: [(0, 7, 2)]}, responses={None: (1, 9)})})
        for size in (1, 100, 10000)]
    monkeypatch.setattr(MemoryState, "as_dict", lambda _: pytest.fail(
        "recording a successor must not serialize historical cells"))
    documents = [memory_successor_record(state, (("ram", ((0, 0),)),)) for state in states]
    assert documents[0] == documents[1] == documents[2]
    assert documents[0]["memories"][0][1]["writes"] == [[0, 0]]


@pytest.mark.parametrize("frozen", [False, True])
def test_successor_read_does_not_roundtrip_through_json(frozen, monkeypatch):
    from behave.sim.core.memory_trace import MemoryTraceReader

    initial = MemoryEnvironmentState({"ram": MemoryState(cells={4: 9})})
    after = MemoryState(cells={4: 10}, writes={4: 10},
                        pending={None: [(4, 9, 2)]}, responses={None: (4, 9)})
    row = memory_successor_record(MemoryEnvironmentState({"ram": after}),
                                  (("ram", ((4, 10),)),))
    reader = MemoryTraceReader(initial)
    with monkeypatch.context() as patch:
        patch.setattr(MemoryState, "as_dict", lambda _: pytest.fail(
            "reading an immutable successor must not rebuild its JSON image"))
        reader.consume(freeze_record_value(row) if frozen else row)
    assert reader.snapshot().state("ram") == after


def test_dense_and_delta_records_can_alternate_and_preserve_zero_writes():
    initial = MemoryEnvironmentState({"ram": MemoryState(cells={1: 7})})
    first = MemoryEnvironmentState({"ram": MemoryState(cells={1: 0}, writes={1: 0})})
    compact = memory_successor_record(first, (("ram", ((1, 0),)),))
    assert read_memory_successor(initial, compact) == first
    assert read_memory_successor(first, first.as_dict()) == first
    no_change = memory_successor_record(first, (("ram", ()),))
    assert read_memory_successor(first, no_change) == first
    assert read_memory_successor(first, json.loads(json.dumps(no_change))) == first


def test_equal_last_deltas_do_not_hide_different_earlier_memory_writes():
    interface, environment = _setup(slots=2)
    contract = SimpleNamespace(memories=(interface,))
    results = []
    last_records = []
    for value in (0, 91):
        after = MemoryEnvironmentState({"ram": MemoryState(
            cells={3: 77, 5: value}, writes={5: value},
            pending={"read": ()}, responses={"read": None})})
        records = [SlotRecord(environment={"external-memory": memory_successor_record(
            after, (("ram", writes),))}) for writes in (((5, value),), ())]
        trace = SimpleNamespace(stimulus=SimpleNamespace(memory_environment=environment),
                                records=records)
        results.append(trace_memory_result(contract, trace, behavior=False))
        last_records.append(records[-1])
    assert last_records[0] == last_records[1]
    assert results[0].memories != results[1].memories


def test_delta_carries_all_memories_including_inactive_domains():
    interfaces = [MemoryInterface(name, 4, 8, clock, [MemoryPort("w")])
                  for name, clock in (("b", "slow"), ("a", "fast"))]
    environment = MemoryEnvironment([MemoryRun(memory.name, initial={0: 7},
        ready={None: [1]}) for memory in interfaces])
    before = MemoryEnvironmentState.initial(interfaces, environment)
    after, writes = advance_memory_environment_with_writes(
        interfaces, environment, before, 0,
        {"a_req_valid": 1, "a_req_addr": 0, "a_req_wdata": 0},
        active_domains={"fast"})
    record = memory_successor_record(after, writes)
    assert [name for name, _body in record["memories"]] == ["a", "b"]
    assert record["memories"][1][1]["writes"] == []
    assert read_memory_successor(before, record) == after
    assert after.state("b").cells == before.state("b").cells


@pytest.mark.parametrize("scheduled", [False, True])
def test_pre_slot_drives_fold_mixed_records_without_copying_all_cells(monkeypatch, scheduled):
    from behave.sim.core.memory_trace import MemoryTraceReader

    interface, environment = _setup(latency=3, slots=10)
    state = MemoryEnvironmentState.initial((interface,), environment)
    slots, records, expected = [], [], []
    for i in range(10):
        resetting = i == 4
        slots.append(EventSlot(reset=resetting and not scheduled,
            ticks=("clk",) if scheduled else (), resets=("clk",) if resetting and scheduled else ()))
        reset_domains = ("clk",) if resetting else ()
        expected.append(drive_memory_environment((interface,), environment, state, i,
                                                 reset_domains=reset_domains))
        state, writes = advance_memory_environment_with_writes((interface,), environment, state, i,
            {"ram__read_req_valid": 1, "ram__read_req_addr": 3,
             "ram__write_req_valid": 1, "ram__write_req_addr": 3, "ram__write_req_wdata": i},
            reset_domains=reset_domains)
        encoded = state.as_dict() if i % 2 else memory_successor_record(state, writes)
        records.append(SlotRecord(environment={"external-memory": encoded}))
    trace = SimpleNamespace(records=records, stimulus=SimpleNamespace(slots=slots,
        clock_schedule=object() if scheduled else None, memory_environment=environment))
    contract = SimpleNamespace(memories=(interface,), clocks=(SimpleNamespace(name="clk"),))
    monkeypatch.setattr(MemoryTraceReader, "snapshot", lambda _: pytest.fail("drive recovery must stay incremental"))
    assert list(trace_memory_drives(contract, trace)) == expected


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(version="unknown"),
    lambda row: row.update(extra=1),
    lambda row: row.update(memories=[]),
    lambda row: row["memories"].append(copy.deepcopy(row["memories"][0])),
    lambda row: row["memories"][0].__setitem__(0, "other"),
    lambda row: row["memories"][0][1].update(cells=[]),
    lambda row: row["memories"][0][1].update(writes=[[True, 1]]),
    lambda row: row["memories"][0][1].update(writes=[[1, -1]]),
    lambda row: row["memories"][0][1].update(writes=[[1, 2], [1, 3]]),
    lambda row: row["memories"][0][1].update(writes=[[2, 1], [1, 1]]),
    lambda row: row["memories"][0][1].update(pending=[[None, [[1, 2, 0]]]]),
    lambda row: row["memories"][0][1].update(writes={"1": 2}),
    lambda row: row["memories"][0][1].update(responses=[["z", None], ["a", None]]),
    lambda row: row["memories"][0][1].update(pending=[["z", []], ["a", []]]),
])
def test_malformed_delta_never_becomes_a_complete_state(mutation):
    initial = MemoryEnvironmentState({"ram": MemoryState()})
    row = memory_successor_record(initial, (("ram", ()),))
    assert row["version"] == MEMORY_SUCCESSOR_VERSION
    mutation(row)
    with pytest.raises((TypeError, ValueError)):
        read_memory_successor(initial, row)
