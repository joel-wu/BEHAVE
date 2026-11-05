"""Public pin binding and Sim's record API consume the same memory transition."""

from itertools import product
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.sim.core import memory as core
from behave.sim.core.refusal import Refused
from behave.sim.memory_driver import _protocol
from behave.sim.memory_driver._cocotb import _Memory
from behave.sim.rtl.runtime_dependencies import python_dependency_mounts


class Pin:
    def __init__(self, width):
        self.width, self.value = width, 0

    def __len__(self):
        return self.width


def pins(interface):
    result = {}
    for port in interface.ports:
        for name in core.memory_port_signal_set(interface.name, port.id, port.kind):
            width = (interface.addr_width if name.endswith("_req_addr") else
                     interface.data_width if name.endswith(("_req_wdata", "_resp_rdata")) else 1)
            result[name] = Pin(width)
    return result


@pytest.mark.parametrize("kinds", list(product(("r", "w", "rw"), repeat=2)))
@pytest.mark.parametrize("latency", [1, 2, 5])
def test_public_binding_matches_core_for_stalls_reads_writes_and_pending(kinds, latency):
    interface = core.MemoryInterface("ram", 4, 8, "clk", [
        core.MemoryPort(kind, port) for kind, port in zip(kinds, ("a", "b"))])
    dut = pins(interface)
    initial = {1: 23, 4: 56}
    ready = lambda c, p: int((c + (p == "b")) % 4 != 1)
    public = _Memory(dut, "ram", dict(zip(("a", "b"), kinds)), initial, latency, ready)
    run = core.MemoryRun("ram", initial=initial, read_latency=latency,
                         ready={p: [ready(c, p) for c in range(40)] for p in ("a", "b")})
    state = core.MemoryState.initial(interface, run)
    # Copy initial data, never share the reference model's mutable dictionary.
    initial[1] = 99
    assert public.read(1) == 23
    for cycle in range(40):
        reset = cycle in (10, 11, 24)
        public._drive(cycle, reset=reset)
        expected = core.drive_memory_state(interface, run, state, cycle, reset=reset)
        assert {name: dut[name].value for name in expected} == expected
        requests = {}
        for i, port in enumerate(interface.ports):
            prefix = core.memory_port_prefix("ram", port.id)
            requests[prefix + "_req_valid"] = int(cycle < 30 and cycle % 5 != i)
            requests[prefix + "_req_addr"] = (cycle + 3 * i) % 16
            if port.writes:
                requests[prefix + "_req_wdata"] = (17 * cycle + i) % 256
            if port.kind == "rw":
                requests[prefix + "_req_we"] = int(cycle % 3 == i)
        for name, value in requests.items():
            dut[name].value = value
        public._commit(public._prepare(cycle, reset=reset))
        state = core.advance_memory_state(interface, run, state, cycle, requests, reset=reset)
        assert core.MemoryState(**public._state.snapshot()._asdict()) == state
        cells = dict(state.cells)
        for address in range(16):
            assert public.read(address) == cells.get(address, core.memory_junk_value("ram", address, 8))


def test_collision_fails_without_applying_either_write_and_read_first_is_preserved():
    interface = core.MemoryInterface("ram", 4, 8, "clk", [core.MemoryPort("rw", p) for p in ("a", "b")])
    dut = pins(interface)
    public = _Memory(dut, "ram", {"a": "rw", "b": "rw"}, {1: 42}, 1, None)
    public._drive(0)
    for p in ("a", "b"):
        for suffix, value in {"req_valid": 1, "req_addr": 1, "req_we": 1, "req_wdata": 7}.items():
            dut[f"ram__{p}_{suffix}"].value = value
    with pytest.raises(_protocol.MemoryProtocolError, match="memory-simultaneous-write-conflict"):
        public._prepare(0)
    assert public.read(1) == 42
    run = core.MemoryRun("ram", initial={1: 42}, ready={"a": [1], "b": [1]})
    with pytest.raises(Refused, match="memory-simultaneous-write-conflict"):
        core.advance_memory_state(interface, run, core.MemoryState.initial(interface, run), 0,
                                  {k: v.value for k, v in public._requests.items()})
    dut["ram__a_req_we"].value = 0
    public._commit(public._prepare(0))
    public._drive(1)
    assert dut["ram__a_resp_valid"].value == 1
    assert dut["ram__a_resp_rdata"].value == 42
    assert public.read(1) == 7


def test_single_port_instances_have_independent_storage():
    interface = core.MemoryInterface("ram", 4, 8, "clk", [core.MemoryPort("w")])
    left, right = (_Memory(pins(interface), "ram", {None: "w"}, {}, 1, None) for _ in range(2))
    left._drive(0)
    for name, value in {"ram_req_valid": 1, "ram_req_addr": 2, "ram_req_wdata": 89}.items():
        left._requests[name].value = value
    left._commit(left._prepare(0))
    assert left.read(2) == 89
    assert right.read(2) == core.memory_junk_value("ram", 2, 8)
    with pytest.raises(ValueError, match="address"):
        left.read(16)


@pytest.mark.parametrize("suffix", ["req_valid", "req_addr", "req_we", "req_wdata"])
def test_memory_protocol_keeps_complete_invalid_pin_evidence(suffix):
    interface = core.MemoryInterface("ram", 4, 8, "clk", [core.MemoryPort("rw")])
    run = core.MemoryRun("ram", ready={None: [1]})
    state = core.MemoryState.initial(interface, run)
    request = {f"ram_{key}": value for key, value in
               {"req_valid": 1, "req_addr": 2, "req_we": 1, "req_wdata": 3}.items()}
    invalid = "invalid-pin-" * 40
    request[f"ram_{suffix}"] = invalid
    with pytest.raises(_protocol.MemoryProtocolError) as caught:
        _protocol.advance(interface, run.read_latency, state,
                          core.drive_memory_state(interface, run, state, 0), request)
    assert caught.value.facts["got"] == repr(invalid)


def test_public_memory_package_is_shared_and_the_core_uses_its_junk_function():
    mounts = [next(m for m in python_dependency_mounts(profile)
                   if m.target == "/runtime/site/memory_driver")
              for profile in ("development", "testbench-build", "testbench")]
    assert mounts[0] == mounts[1] == mounts[2]
    source = Path(_protocol.__file__)
    assert (Path(mounts[0].source) / "_protocol.py").read_bytes() == source.read_bytes()
    assert core.memory_junk_value is _protocol.memory_junk_value
    assert core.memory_junk_parameters is _protocol.memory_junk_parameters
    # The kernel itself is importable without cocotb, task records or evaluators.
    assert "from behave" not in source.read_text()
    assert _protocol.drive(SimpleNamespace(name="ram", ports=(), data_width=8), {},
                           _protocol.State()) == {}
