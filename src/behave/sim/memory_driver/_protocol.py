"""External-memory pin transition shared by Sim and public cocotb tests.

This module has no evaluator, task, simulator or filesystem dependencies.
Sim owns validation/serialization of its records. The public binding supplies
the same interface fields and state to these functions.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple

MEMORY_JUNK_VERSION = "external-memory-junk/index-mix/2"


class MemoryProtocolError(ValueError):
    """An invalid request, including an undefined simultaneous-write collision."""

    def __init__(self, kind, why, **facts):
        self.kind = kind
        self.why = why
        self.facts = facts
        super().__init__(f"{kind}: {why} ({facts})")


class State(NamedTuple):
    cells: object = ()
    pending: object = ()
    responses: object = ()
    writes: object = ()


class Transition(NamedTuple):
    state: State
    writes: tuple


class Updates(NamedTuple):
    """Accepted writes and complete successor queues for one active edge."""

    writes: tuple
    pending: tuple
    responses: tuple


class RuntimeState:
    """Owned sparse storage; full immutable images are made only on request.

    Preparing an edge does not modify storage. Drivers can prepare every memory
    before committing any of them, including when a later request is invalid.
    """

    def __init__(self, state, interface=None):
        self._interface = interface
        self._default = (None if interface is None else
                         memory_junk_reader(interface.name, interface.data_width))
        self._cells = dict(state.cells)
        self._writes = dict(state.writes)
        self.pending = tuple((port, tuple(tuple(entry) for entry in queue))
                             for port, queue in state.pending)
        self.responses = tuple((port, None if value is None else tuple(value))
                               for port, value in state.responses)

    def read(self, interface, address):
        if address in self._cells:
            return self._cells[address]
        if interface is self._interface:
            return self._default(address)
        return memory_junk_value(interface.name, address, interface.data_width)

    def prepare(self, interface, read_latency, driven, artifact_outputs, *, slot=0,
                reset=False):
        return _plan(interface, read_latency, self._cells, self.pending,
                     driven, artifact_outputs, slot=slot, reset=reset,
                     default=self._default if interface is self._interface else None)

    def unchanged(self):
        """An inactive domain retains its queues without counting down reads."""
        return Updates((), self.pending, self.responses)

    def commit(self, updates):
        self._cells.update(updates.writes)
        self._writes.update(updates.writes)
        self.pending = updates.pending
        self.responses = updates.responses

    def snapshot(self):
        return State(tuple(self._cells.items()), self.pending, self.responses,
                     tuple(self._writes.items()))


def _counter_word(domain, *parts):
    """One implementation-independent 256-bit counter-PRF word.

    Semantic records must not inherit the private algorithm of ``random.Random``.
    Length-prefixing each UTF-8 component makes the byte image unambiguous and the
    domain tag keeps ready decisions and junk data in disjoint streams.
    """

    return int.from_bytes(hashlib.sha256(_counter_prefix(domain, *parts)).digest(), "big")


def _counter_prefix(*parts):
    """The canonical counter image, also used to bind a fixed ready prefix."""
    chunks = []
    for part in parts:
        encoded = str(part).encode("utf-8")
        chunks.extend((len(encoded).to_bytes(8, "big"), encoded))
    return b"".join(chunks)


def memory_port_prefix(memory, port):
    """Physical prefix for ``port`` of ``memory``."""

    return memory if port is None else f"{memory}__{port}"


def _junk_parameter(memory_name, data_width, label):
    """Return one stable ``data_width``-bit parameter.

    SHA is used only once to choose constants for a named memory.  The address is
    deliberately *not* hashed: keeping it as the varying operand gives exact
    analysis a compact bit-vector term instead of a table with one entry per cell.
    """

    words = []
    for counter in range((data_width + 255) // 256):
        words.append(_counter_word(MEMORY_JUNK_VERSION, memory_name, data_width, label, counter))
    value = 0
    for word in words:
        value = (value << 256) | word
    return value & ((1 << data_width) - 1)


def memory_junk_parameters(memory_name, data_width):
    """Constants of the versioned address-to-cell initialization function."""

    if type(memory_name) is not str or not memory_name:
        raise ValueError("a memory junk value needs a nonempty memory name")
    if type(data_width) is not int or isinstance(data_width, bool) or data_width < 1:
        raise ValueError("a memory junk value needs a positive data width")
    first = _junk_parameter(memory_name, data_width, "first-multiplier") | 1
    second = _junk_parameter(memory_name, data_width, "second-multiplier") | 1
    offset = _junk_parameter(memory_name, data_width, "offset")
    return first, second, offset, max(1, data_width // 3)


def memory_junk_value(memory_name, address, data_width):
    """Stable unwritten-cell value shared by concrete and exact execution.

    This is a finite-width address mixer.  Its constants remain name-specific and
    SHA-derived, while its varying part uses only bit-vector operations.  Hence the
    same definition is compactly representable for a symbolic address.
    """

    if type(address) is not int or isinstance(address, bool) or address < 0:
        raise ValueError("a memory junk address is a nonnegative exact integer")
    return memory_junk_reader(memory_name, data_width)(address)


def memory_junk_reader(memory_name, data_width):
    """Bind the same address mixer once per memory; do not retain cell values."""
    return _MemoryJunk(memory_junk_parameters(memory_name, data_width),
                       (1 << data_width) - 1)


class _MemoryJunk(NamedTuple):
    parameters: tuple
    mask: int

    def __call__(self, address):
        if type(address) is not int or address < 0:
            raise ValueError("a memory junk address is a nonnegative exact integer")
        first, second, offset, shift = self.parameters
        mask = self.mask
        value = ((address & mask) ^ offset) * first & mask
        value ^= value >> shift
        return (value * second + offset) & mask


def _cell(state, interface, address):
    cells = dict(state.cells)
    if address in cells:
        return cells[address]
    return memory_junk_value(interface.name, address, interface.data_width)


def drive(interface, ready, state, *, reset=False):
    """Environment pins for a memory slot."""

    driven = {}
    responses = dict(state.responses)
    mask = (1 << interface.data_width) - 1
    for port in interface.ports:
        prefix = memory_port_prefix(interface.name, port.id)
        driven[prefix + "_req_ready"] = 0 if reset else ready[port.id]
        if port.reads:
            response = None if reset else responses[port.id]
            driven[prefix + "_resp_valid"] = int(response is not None)
            driven[prefix + "_resp_rdata"] = 0 if response is None else response[1] & mask
    return driven


def advance(interface, read_latency, state, driven, artifact_outputs, *, slot=0,
            reset=False):
    """Compatibility projection of the shared transition's successor state."""
    return transition(interface, read_latency, state, driven, artifact_outputs,
                      slot=slot, reset=reset).state


def transition(interface, read_latency, state, driven, artifact_outputs, *, slot=0,
               reset=False):
    """Pure read-first transition for one memory and one active clock edge."""

    cells, writes = dict(state.cells), dict(state.writes)
    updates = _plan(interface, read_latency, cells, state.pending,
                    driven, artifact_outputs, slot=slot, reset=reset)
    cells.update(updates.writes)
    writes.update(updates.writes)
    after = State(cells=cells.items(), pending=updates.pending,
                  responses=updates.responses, writes=writes.items())
    return Transition(after, updates.writes)


def reset_updates(interface):
    """Cancel the read epoch, preserving cells and committed-write evidence."""
    return Updates((), tuple((p.id, ()) for p in interface.ports if p.reads),
                   tuple((p.id, None) for p in interface.ports if p.reads))


def _plan(interface, read_latency, cells, queues, driven, artifact_outputs, *, slot,
          default=None, reset=False):
    """One read-first rule shared by immutable analysis and mutable execution."""

    if reset:
        return reset_updates(interface)

    def observed(name):
        if name not in artifact_outputs:
            raise MemoryProtocolError(
                "memory-request-pin-missing",
                "an active external-memory slot records every artifact-owned request "
                "pin rather than assigning an implicit value to an omitted signal",
                memory=interface.name,
                signal=name,
                slot=slot,
            )
        return artifact_outputs[name]

    pending = {port: [list(entry) for entry in queue] for port, queue in queues}
    for queue in pending.values():
        for entry in queue:
            entry[2] -= 1
    snapshots = []
    actions = []
    addr_mask = interface.depth - 1
    data_mask = (1 << interface.data_width) - 1
    for port in interface.ports:
        prefix = memory_port_prefix(interface.name, port.id)
        valid = observed(prefix + "_req_valid")
        ready = driven[prefix + "_req_ready"]
        if type(valid) is not int or valid not in (0, 1):
            raise MemoryProtocolError(
                "memory-request-valid-not-bit",
                "an external-memory request valid signal is exactly one bit",
                memory=interface.name,
                port=port.id,
                got=repr(valid),
            )
        address = observed(prefix + "_req_addr")
        if type(address) is not int or not 0 <= address < 1 << interface.addr_width:
            raise MemoryProtocolError(
                "memory-request-address-out-of-range",
                "an external-memory request address fits its declared port",
                memory=interface.name,
                port=port.id,
                got=repr(address),
            )
        address &= addr_mask
        reading = port.kind == "r"
        if port.kind == "rw":
            write_enable = observed(prefix + "_req_we")
            if type(write_enable) is not int or write_enable not in (0, 1):
                raise MemoryProtocolError(
                    "memory-write-enable-not-bit",
                    "an external-memory write-enable signal is exactly one bit",
                    memory=interface.name,
                    port=port.id,
                    got=repr(write_enable),
                )
            reading = not write_enable
        value = None
        if port.writes:
            value = observed(prefix + "_req_wdata")
            if type(value) is not int or not 0 <= value < 1 << interface.data_width:
                raise MemoryProtocolError(
                    "memory-write-data-out-of-range",
                    "an external-memory write value fits its declared port",
                    memory=interface.name,
                    port=port.id,
                    got=repr(value),
                )
        if not (valid and ready):
            continue
        if reading:
            if address in cells:
                value = cells[address]
            else:
                if default is None:
                    default = memory_junk_reader(interface.name, interface.data_width)
                value = default(address)
            snapshots.append((port.id, address, value))
        else:
            actions.append((address, value & data_mask))
    by_address = {}
    for address, value in actions:
        if address in by_address:
            raise MemoryProtocolError(
                "memory-simultaneous-write-conflict",
                "two ports accepted writes to the same external-memory address in "
                "one slot, and the declared protocol assigns neither port priority",
                memory=interface.name,
                address=address,
            )
        by_address[address] = value
    for port, address, value in snapshots:
        pending[port].append([address, value, read_latency - 1])
    responses = {}
    for port, queue in pending.items():
        if queue and queue[0][2] <= 0:
            address, value, _left = queue.pop(0)
            responses[port] = (address, value)
        else:
            responses[port] = None
    return Updates(tuple(sorted(actions)),
                   tuple((port, tuple(tuple(entry) for entry in queue))
                         for port, queue in pending.items()),
                   tuple(responses.items()))
