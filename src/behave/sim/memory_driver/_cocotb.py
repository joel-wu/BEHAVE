"""Pin/clock binding for the shared external-memory transition."""

from __future__ import annotations

from asyncio import CancelledError
from dataclasses import dataclass

import cocotb
from cocotb.triggers import ReadWrite, Timer, ValueChange
from cocotb.utils import get_sim_steps

from . import _protocol


@dataclass(frozen=True)
class _Port:
    id: str | None
    kind: str

    @property
    def reads(self):
        return self.kind in ("r", "rw")

    @property
    def writes(self):
        return self.kind in ("w", "rw")


@dataclass(frozen=True)
class _Interface:
    name: str
    ports: tuple[_Port, ...]
    addr_width: int
    data_width: int

    @property
    def depth(self):
        return 1 << self.addr_width


class _Memory:
    """One pin-bound memory. Construct with MemoryDriver.add_memory()."""

    def __init__(self, dut, name, ports, initial, read_latency, ready):
        if not name or not isinstance(name, str):
            raise ValueError("memory name must be a nonempty string")
        if not ports or any(kind not in ("r", "w", "rw") for kind in ports.values()):
            raise ValueError("ports maps port IDs to 'r', 'w', or 'rw'")
        if any(port is not None and (not isinstance(port, str) or not port) for port in ports):
            raise ValueError("port IDs are nonempty strings, or None for a single port")
        if len(ports) > 1 and None in ports:
            raise ValueError("multiple ports require explicit port IDs")
        if type(read_latency) is not int or read_latency < 1:
            raise ValueError("read_latency must be a positive number of active edges")
        declarations = tuple(_Port(port, kind) for port, kind in ports.items())
        self._requests = {}
        self._outputs = {}
        address_widths, data_widths = set(), set()
        for port in declarations:
            prefix = _protocol.memory_port_prefix(name, port.id)
            request_names = ["req_valid", "req_addr"]
            output_names = ["req_ready"]
            if port.reads:
                output_names += ["resp_valid", "resp_rdata"]
            if port.writes:
                request_names.append("req_wdata")
            if port.kind == "rw":
                request_names.append("req_we")
            for names, handles in ((request_names, self._requests), (output_names, self._outputs)):
                for suffix in names:
                    signal = prefix + "_" + suffix
                    handle = dut[signal]
                    handles[signal] = handle
                    if suffix == "req_addr":
                        address_widths.add(len(handle))
                    elif suffix in ("req_wdata", "resp_rdata"):
                        data_widths.add(len(handle))
                    elif len(handle) != 1:
                        raise ValueError(f"{signal} must be one bit")
        if len(address_widths) != 1 or len(data_widths) != 1:
            raise ValueError("all ports of a memory must share address and data widths")
        self._interface = _Interface(name, declarations, address_widths.pop(), data_widths.pop())
        cells = {} if initial is None else dict(initial)
        for address, value in cells.items():
            self._check_address(address)
            if type(value) is not int or not 0 <= value < 1 << self._interface.data_width:
                raise ValueError("initial memory words must fit the data width")
        self._state = _protocol.RuntimeState(_protocol.State(
            cells=tuple(cells.items()),
            pending=tuple((p.id, ()) for p in declarations if p.reads),
            responses=tuple((p.id, None) for p in declarations if p.reads),
        ))
        self._latency = read_latency
        self._ready = ready

    def _check_address(self, address):
        if type(address) is not int or not 0 <= address < self._interface.depth:
            raise ValueError("memory word address must fit the address width")

    def read(self, address):
        """Read one current word, including the shared unwritten-cell value."""
        self._check_address(address)
        return self._state.read(self._interface, address)

    def _drive(self, cycle, *, reset=False):
        ready = {p.id: 1 if self._ready is None else self._ready(cycle, p.id) for p in self._interface.ports}
        if any(value not in (0, 1) for value in ready.values()):
            raise ValueError("ready(cycle, port) must return a bit")
        self._driven = _protocol.drive(self._interface, ready, self._state, reset=reset)
        for name, value in self._driven.items():
            self._outputs[name].value = value

    def _prepare(self, cycle, *, reset=False):
        if reset:
            return _protocol.reset_updates(self._interface)
        sampled = {name: int(handle.value) for name, handle in self._requests.items()}
        return self._state.prepare(self._interface, self._latency, self._driven,
                                   sampled, slot=cycle)

    def _commit(self, updates):
        self._state.commit(updates)


class MemoryDriver:
    """Own one clock domain and serve its external memories.

    Add memories before entering the async context. Do not also start a Clock
    on this signal. Requests are sampled after settling, before generating the
    active edge, including under Verilator. Responses change on the inactive
    edge and are stable at the next active edge, except that an asserted reset
    cancels pending/visible reads immediately. Bind the domain's reset explicitly;
    storage and previously committed writes survive it.
    """

    def __init__(self, clock, *, period, unit="ns", edge="rising", reset=None,
                 reset_active_low=False):
        if edge not in ("rising", "falling"):
            raise ValueError("edge must be 'rising' or 'falling'")
        ticks = get_sim_steps(period, unit)
        if ticks < 2:
            raise ValueError("clock period must contain at least two simulator precision ticks")
        self._clock = clock
        if type(reset_active_low) is not bool or (reset is not None and len(reset) != 1):
            raise ValueError("reset needs a one-bit handle and Boolean polarity")
        self._reset = reset
        self._reset_active_low = reset_active_low
        self._active = int(edge == "rising")
        self._inactive_ticks = ticks // 2
        self._active_ticks = ticks - self._inactive_ticks
        self._memories = []
        self._task = None
        self._reset_task = None
        self.cycle = 0

    def add_memory(self, dut, name, *, ports, initial=None, read_latency=1, ready=None):
        """Bind a memory and return its read(address) inspection handle.

        ports maps None (single port) or named IDs to r/w/rw. Physical names are
        <name>_<signal> or <name>__<port>_<signal>. Widths come from DUT handles.
        ready(cycle, port_id), if supplied, controls request acceptance per edge.
        """
        if self._task is not None:
            raise RuntimeError("add memories before starting the driver")
        memory = _Memory(dut, name, ports, initial, read_latency, ready)
        pins = set(memory._outputs)
        if any(pins.intersection(other._outputs) for other in self._memories):
            raise ValueError("a memory pin is already driven by this clock domain")
        self._memories.append(memory)
        return memory

    async def __aenter__(self):
        if self._task is not None:
            raise RuntimeError("a memory driver context runs once")
        self._clock.value = 1 - self._active
        for memory in self._memories:
            memory._drive(self.cycle, reset=self._in_reset())
        self._task = cocotb.start_soon(self._run())
        if self._reset is not None:
            self._reset_task = cocotb.start_soon(self._watch_reset())
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        tasks = [task for task in (self._task, self._reset_task) if task is not None]
        for task in tasks:
            task.cancel()
        error = None
        for task in tasks:
            try:
                await task
            except CancelledError:
                pass
            except Exception as failure:  # noqa: BLE001 -- await both tasks, then propagate
                error = error or failure
        if error is not None:
            raise error

    def _in_reset(self):
        return self._reset is not None and int(self._reset.value) == int(not self._reset_active_low)

    async def _watch_reset(self):
        while True:
            await ValueChange(self._reset)
            await ReadWrite()
            reset = self._in_reset()
            for memory in self._memories:
                if reset:
                    memory._commit(_protocol.reset_updates(memory._interface))
                memory._drive(self.cycle, reset=reset)

    async def _run(self):
        while True:
            await Timer(self._inactive_ticks, unit="step")
            # Unlike an edge callback, this samples BEFORE sequential RTL runs.
            # ReadWrite settles combinational requests before the clock write.
            await ReadWrite()
            after = [memory._prepare(self.cycle, reset=self._in_reset())
                     for memory in self._memories]
            self._clock.value = self._active
            for memory, updates in zip(self._memories, after):
                memory._commit(updates)
            self.cycle += 1
            await Timer(self._active_ticks, unit="step")
            self._clock.value = 1 - self._active
            for memory in self._memories:
                memory._drive(self.cycle, reset=self._in_reset())
