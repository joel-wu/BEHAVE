"""Incremental storage preserves the pure transition and complete trace image."""

import itertools
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
from behave.sim.core.memory_runtime import MemoryRuntime
from behave.sim.core.memory_trace import (
    MemoryTraceReader,
    final_memory_state,
    memory_successor_record,
    memory_updates_record,
)
from behave.sim.core.refusal import Refused
from behave.sim.core.stimulus import SlotRecord
from behave.sim.memory_driver import _protocol


@pytest.mark.parametrize("latency", (1, 2, 7))
def test_quiescence_waits_for_pending_read_and_response(latency):
    interface = MemoryInterface("ram", 4, 8, "clk", [MemoryPort("r")])
    environment = MemoryEnvironment([MemoryRun(
        "ram", initial={1: 42}, read_latency=latency,
        ready={None: [1] * (latency + 2)})])
    runtime = MemoryRuntime((interface,), environment)
    assert runtime.quiescent
    request = {"ram_req_valid": 1, "ram_req_addr": 1}
    updates = runtime.prepare(0, request)
    assert runtime.quiescent  # Only committed state defines the boundary.
    runtime.commit(updates)
    assert not runtime.quiescent
    idle = {"ram_req_valid": 0, "ram_req_addr": 0}
    for slot in range(1, latency):
        runtime.commit(runtime.prepare(slot, idle))
        assert not runtime.quiescent
    assert runtime.drive(latency)["ram_resp_valid"] == 1
    assert runtime.drive(latency)["ram_resp_rdata"] == 42
    runtime.commit(runtime.prepare(latency, idle))
    assert runtime.quiescent


def test_quiescence_does_not_claim_memory_value_agreement():
    interface = MemoryInterface("ram", 4, 8, "clk", [MemoryPort("w")])
    environment = MemoryEnvironment([MemoryRun("ram", initial={1: 42}, ready={None: [1]})])
    runtime = MemoryRuntime((interface,), environment)
    runtime.commit(runtime.prepare(0, {
        "ram_req_valid": 1, "ram_req_addr": 1, "ram_req_wdata": 99}))
    assert runtime.quiescent  # A committed write has no delayed response.
    # The ordinary memory comparator must still check these effects.
    assert runtime.snapshot().state("ram").cells == ((1, 99),)
    assert runtime.snapshot().state("ram").writes
    assert MemoryRuntime((), MemoryEnvironment([])).quiescent


@pytest.mark.parametrize("kinds", list(itertools.product(("r", "w", "rw"), repeat=2)))
@pytest.mark.parametrize("latency", (1, 2, 7))
def test_runtime_matches_pure_state_drive_and_record(kinds, latency):
    interface = MemoryInterface("ram", 8, 16, "clk", [MemoryPort(kind, port)
                                for kind, port in zip(kinds, ("a", "b"))])
    run = MemoryRun("ram", initial={3: 77}, read_latency=latency,
                    ready={port: [int((slot + i) % 3 != 0) for slot in range(64)]
                           for i, port in enumerate(("a", "b"))})
    environment = MemoryEnvironment([run])
    runtime = MemoryRuntime((interface,), environment)
    state = MemoryEnvironmentState.initial((interface,), environment)
    reader = MemoryTraceReader(state)
    initial = state
    for slot in range(64):
        resets = {"clk"} if slot in (10, 18, 23) else set()
        assert runtime.drive(slot, reset_domains=resets) == drive_memory_environment(
            (interface,), environment, state, slot, reset_domains=resets)
        signals = {}
        for i, port in enumerate(interface.ports):
            prefix = f"ram__{port.id}"
            signals[prefix + "_req_valid"] = int(slot % 5 != 0)
            signals[prefix + "_req_addr"] = (slot + i) % 19
            if port.writes:
                signals[prefix + "_req_wdata"] = slot % 13  # repeat and zero writes
            if port.kind == "rw":
                signals[prefix + "_req_we"] = slot % 2
        active = set() if slot % 9 == 0 else {"clk"}
        updates = runtime.prepare(slot, signals, active_domains=active, reset_domains=resets)
        assert runtime.snapshot() == state  # prepare is not a commit
        after, writes = advance_memory_environment_with_writes(
            (interface,), environment, state, slot, signals, active_domains=active,
            reset_domains=resets)
        runtime.commit(updates)
        assert runtime.snapshot() == after
        encoded = memory_updates_record(updates)
        assert encoded == memory_successor_record(after, writes)
        reader.consume(encoded)
        assert reader.snapshot() == after
        assert reader.snapshot().digest == after.digest
        state = after
    assert initial.state("ram").cells == ((3, 77),)
    assert initial.state("ram").writes == ()


def test_invalid_later_memory_does_not_commit_an_earlier_write():
    interfaces = [MemoryInterface(name, 4, 8, "clk", [MemoryPort("w")])
                  for name in ("a", "b")]
    environment = MemoryEnvironment([MemoryRun(memory.name, initial={1: 42},
                                                ready={None: [1]}) for memory in interfaces])
    runtime = MemoryRuntime(interfaces, environment)
    before = runtime.snapshot()
    signals = {name + suffix: value for name in ("a", "b") for suffix, value in
               (("_req_valid", 1), ("_req_addr", 1), ("_req_wdata", 0))}
    del signals["b_req_addr"]
    with pytest.raises(Refused, match="memory-request-pin-missing"):
        runtime.prepare(0, signals)
    assert runtime.snapshot() == before
    signals["b_req_addr"] = 1
    updates = runtime.prepare(0, signals)
    assert runtime.snapshot() == before
    runtime.commit(updates)
    assert runtime.snapshot().state("a").cells == ((1, 0),)
    assert runtime.snapshot().state("b").cells == ((1, 0),)
    assert before.state("a").cells == ((1, 42),)


@pytest.mark.parametrize("threshold", [0, 1, 1 << 255, (1 << 256) - 1, 1 << 256])
def test_ready_prefix_preserves_independent_counter_and_process_roundtrip(threshold):
    import hashlib
    import pickle
    from concurrent.futures import ThreadPoolExecutor

    run = MemoryRun("ram_测试", ready={"a": (), "b": ()}, ready_policy=(-17, threshold))
    clone = pickle.loads(pickle.dumps(run))
    assert clone.as_dict() == run.as_dict()
    slots = [0, 1, 1024, 2**64 + 5, 7, 0]
    for index, port in enumerate(("a", "b")):
        expected = []
        for slot in slots:
            digest = hashlib.sha256()
            for part in ("external-memory-ready/1", -17, "ram_测试", port, index, slot):
                encoded = str(part).encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
            expected.append(int(int.from_bytes(digest.digest(), "big") >= threshold))
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda s, p=port: clone.ready_at(p, s), slots)) == expected


def test_ready_reuse_does_not_reuse_responses_or_accept_boolean_slot(monkeypatch):
    interface = MemoryInterface("ram", 4, 8, "clk", [MemoryPort("rw")])
    run = MemoryRun.from_policy(interface, None, initial={1: 42}, seed=19, stall_rate=0.25)
    environment = MemoryEnvironment([run])
    runtime = MemoryRuntime((interface,), environment)
    original, calls = MemoryRun.ready_at, []
    def counted(self, port, slot):
        calls.append(slot)
        return original(self, port, slot)
    monkeypatch.setattr(MemoryRun, "ready_at", counted)
    slot = next(i for i in range(100) if original(run, None, i))
    driven = runtime.drive(slot)
    updates = runtime.prepare(slot, {"ram_req_valid": 1, "ram_req_addr": 1,
                                    "ram_req_wdata": 5, "ram_req_we": 0})
    assert calls == [slot]
    runtime.commit(updates)
    # Same slot, new state: response data cannot come from the drive cache.
    assert runtime.drive(slot) != driven
    expected = drive_memory_environment((interface,), environment, runtime.snapshot(), slot)
    assert runtime.drive(slot) == expected
    assert runtime.drive(slot, reset_domains={"clk"}) == drive_memory_environment(
        (interface,), environment, runtime.snapshot(), slot, reset_domains={"clk"})
    runtime.drive(0)
    with pytest.raises(Refused, match="memory-ready-slot-invalid"):
        runtime.drive(False)
    runtime.drive(1)
    runtime.drive(0)  # non-monotone diagnostics regenerate, never use a stale slot


def test_read_first_and_conflicting_writes_have_independent_expected_values():
    interface = MemoryInterface("ram", 4, 8, "clk", [MemoryPort("rw", p) for p in ("a", "b")])
    environment = MemoryEnvironment([MemoryRun("ram", initial={1: 42},
                                               ready={"a": [1, 1], "b": [1, 1]})])
    runtime = MemoryRuntime((interface,), environment)
    signals = {f"ram__{p}_{suffix}": value for p in ("a", "b") for suffix, value in
               (("req_valid", 1), ("req_addr", 1), ("req_we", 1), ("req_wdata", 7))}
    before = runtime.snapshot()
    with pytest.raises(Refused, match="memory-simultaneous-write-conflict"):
        runtime.prepare(0, signals)
    assert runtime.snapshot() == before
    signals["ram__a_req_we"] = 0
    runtime.commit(runtime.prepare(0, signals))
    assert runtime.snapshot().state("ram").cells == ((1, 7),)
    assert runtime.drive(1)["ram__a_resp_valid"] == 1
    assert runtime.drive(1)["ram__a_resp_rdata"] == 42


class _NoScan(dict):
    def __iter__(self):
        pytest.fail("a memory step must not iterate historical storage")

    def items(self):
        pytest.fail("a memory step must not copy historical storage")

    def keys(self):
        pytest.fail("a memory step must not copy historical storage")


def test_protocol_runtime_hot_path_never_scans_or_snapshots_storage(monkeypatch):
    interface = MemoryInterface("ram", 16, 16, "clk", [MemoryPort("rw")])
    cells = {i: i for i in range(10000)}
    state = _protocol.RuntimeState(_protocol.State(cells=cells.items(), writes=cells.items(),
                                                 pending=((None, ()),), responses=((None, None),)))
    state._cells, state._writes = _NoScan(state._cells), _NoScan(state._writes)
    monkeypatch.setattr(_protocol.RuntimeState, "snapshot", lambda _: pytest.fail(
        "an execution step does not request a complete snapshot"))
    cells[0] = 99
    assert state.read(interface, 0) == 0
    for slot in range(256):
        driven = _protocol.drive(interface, {None: 1}, state)
        signals = {"ram_req_valid": 1, "ram_req_addr": slot, "ram_req_wdata": slot + 1,
                   "ram_req_we": slot % 2}
        state.commit(state.prepare(interface, 1, driven, signals, slot=slot))
        assert state.read(interface, slot) == slot + slot % 2


def test_environment_step_and_record_do_not_construct_full_states(monkeypatch):
    interface = MemoryInterface("ram", 16, 16, "clk", [MemoryPort("w")])
    environment = MemoryEnvironment([MemoryRun("ram", initial={i: i for i in range(10000)},
                                               ready={None: [1]})])
    runtime = MemoryRuntime((interface,), environment)
    monkeypatch.setattr(MemoryState, "__init__", lambda *a, **kw: pytest.fail(
        "per-step preparation and encoding must not construct full memory states"))
    runtime.drive(0)
    updates = runtime.prepare(0, {"ram_req_valid": 1, "ram_req_addr": 9999, "ram_req_wdata": 0})
    runtime.commit(updates)
    assert memory_updates_record(updates)["memories"] == [["ram", {
        "writes": [[9999, 0]], "pending": [], "responses": [],
    }]]


def test_final_prefix_fold_snapshots_once_and_keeps_earlier_writes(monkeypatch):
    interface = MemoryInterface("ram", 16, 16, "clk", [MemoryPort("w")])
    environment = MemoryEnvironment([MemoryRun("ram", ready={None: [1] * 128})])
    records = [SlotRecord(environment={"external-memory": memory_updates_record([
        ("ram", _protocol.Updates(((slot, slot + 1),) if slot < 127 else (), (), ()))])})
        for slot in range(128)]
    trace = SimpleNamespace(stimulus=SimpleNamespace(memory_environment=environment), records=records)
    original = _protocol.RuntimeState.snapshot
    calls = []

    def snapshot(state):
        calls.append(state)
        return original(state)

    monkeypatch.setattr(_protocol.RuntimeState, "snapshot", snapshot)
    state = final_memory_state(SimpleNamespace(memories=(interface,)), trace)
    assert len(calls) == 1
    assert state.state("ram").cells == tuple((slot, slot + 1) for slot in range(127))
    assert state.state("ram").writes == state.state("ram").cells


def test_trace_reader_validates_all_rows_before_mutation():
    initial = MemoryEnvironmentState((name, MemoryState(cells={0: 1})) for name in ("a", "b"))
    reader = MemoryTraceReader(initial)
    record = memory_updates_record((name, _protocol.Updates(((0, 0),), (), ())) for name in ("a", "b"))
    record["memories"][1][1]["writes"] = [[0, -1]]
    with pytest.raises((ValueError, TypeError)):
        reader.consume(record)
    assert reader.snapshot() == initial
