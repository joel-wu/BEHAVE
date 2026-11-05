"""Unit tests for the task-bound sparse Behavior reference memory.

Locks the memory semantics the final-memory check depends on: lazy junk on
read (identical to the testbench backing), write tracking, and the deliberate
``.get`` override so a model that defaults to 0 still agrees with the simulated
RAM's junk-on-miss.
"""
import pytest

from behave.sim.behavior.reference_memory import ReferenceMemory
from behave.sim.core.memory import MemoryInterface, MemoryPort, MemoryRun, memory_junk_value


def _mk(depth=256, dw=8, init=None):
    assert depth >= 2 and depth & (depth - 1) == 0
    interface = MemoryInterface("dmem", depth.bit_length() - 1, dw, "clk", [MemoryPort("rw")])
    return ReferenceMemory(interface, MemoryRun("dmem", initial=init or {}))


def test_read_unwritten_is_deterministic_junk():
    m = _mk()
    assert m[5] == memory_junk_value("dmem", 5, 8)
    assert m[5] == m[5]                      # stable


def test_write_then_read_and_masks():
    m = _mk(dw=8)
    m[10] = 0x1FF                            # over 8 bits -> masked
    assert m[10] == 0xFF


def test_writes_are_tracked():
    m = _mk()
    m[3] = 7
    m[200] = 9
    assert m.writes == {3: 7, 200: 9}


def test_get_ignores_caller_default_returns_junk():
    m = _mk()
    # model wrote `.get(addr, 0)` — but un-stored reads must match the RAM junk,
    # NOT the caller's 0, or golden silently diverges from the mem_server.
    assert m.get(7, 0) == memory_junk_value("dmem", 7, 8)
    m[7] = 42
    assert m.get(7, 0) == 42                 # stored value wins


def test_mem_init_seeds_store_without_marking_writes():
    m = _mk(init={4: 0xAB})
    assert m[4] == 0xAB
    assert m.writes == {}                    # mem_init is not a model write


def test_len_and_contains():
    m = _mk(depth=64)
    assert len(m) == 64
    assert 0 in m and 63 in m and 64 not in m


def test_iter_covers_depth_with_junk():
    m = _mk(depth=4, init={1: 5})
    vals = list(m)
    assert len(vals) == 4
    assert vals[1] == 5
    assert vals[0] == memory_junk_value("dmem", 0, 8)


@pytest.mark.parametrize("address", [-1, 64])
def test_addresses_outside_task_memory_are_rejected(address):
    memory = _mk(depth=64)
    with pytest.raises(IndexError):
        _ = memory[address]
    with pytest.raises(IndexError):
        memory[address] = 1
    assert memory.writes == {}
