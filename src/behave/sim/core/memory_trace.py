"""Lossless external-memory successor records relative to a trace prefix.

The stimulus supplies the initial cells. Each step records accepted writes and
the complete pending/response queues; cells and the cumulative write set are
then uniquely determined. Replaying this encoding does not run the RTL or guess
unobserved values. Older complete snapshots remain readable at the same boundary.
"""

from ..memory_driver import _protocol
from .memory import MemoryEnvironmentState, MemoryState
from .record_value import record_pairs

MEMORY_SUCCESSOR_VERSION = "memory-successor-writes/1"


def memory_successor_record(state, writes):
    """Encode a trusted transition without copying every historical cell."""
    if type(state) is not MemoryEnvironmentState:
        raise TypeError("a memory successor record needs an exact environment state")
    updates = dict(writes)
    if len(updates) != len(writes) or set(updates) != {name for name, _ in state.memories}:
        raise ValueError("a memory successor records writes for every declared memory")
    return memory_updates_record((name, _protocol.Updates(updates[name], memory.pending,
                                                        memory.responses))
                                 for name, memory in state.memories)


def memory_updates_record(updates):
    """Encode trusted per-edge updates without materializing historical cells."""
    rows = []
    for name, memory in sorted(updates):
        rows.append([name, {
            "writes": [list(pair) for pair in memory.writes],
            "pending": [[port, [list(entry) for entry in queue]]
                        for port, queue in memory.pending],
            "responses": [[port, None if response is None else list(response)]
                          for port, response in memory.responses],
        }])
    return {"version": MEMORY_SUCCESSOR_VERSION, "memories": rows}


def _read_updates(top, names):
    if (set(top) != {"version", "memories"}
            or top["version"] != MEMORY_SUCCESSOR_VERSION
            or type(top["memories"]) is not tuple):
        raise ValueError("a memory successor has its exact version and memory rows")
    rows = top["memories"]
    if any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str
           for row in rows):
        raise ValueError("a memory successor row has a name and component record")
    if tuple(row[0] for row in rows) != names:
        raise ValueError("a memory successor must preserve the complete ordered memory set")
    decoded = []
    for name, raw in rows:
        body = dict(record_pairs(raw, f"memory.successor.{name}"))
        if set(body) != {"writes", "pending", "responses"}:
            raise ValueError("a memory successor has writes, pending reads and responses")
        if any(type(item) is not tuple for item in body.values()):
            raise TypeError("memory successor components are finite sequences")
        # The constructor already validates these immutable rows. Preserve the
        # same canonical-order check without thawing and rebuilding JSON each edge.
        changes = MemoryState(**body)
        if any(getattr(changes, key) != value for key, value in body.items()):
            raise ValueError("a memory successor is not canonical")
        decoded.append((name, _protocol.Updates(changes.writes, changes.pending,
                                               changes.responses)))
    return decoded


def memory_successor_quiescent(value, names):
    """Read the closed pending/response queues without copying prior RAM cells."""
    top = dict(record_pairs(value, "memory.successor"))
    if set(top) == {"memories"}:
        rows = MemoryEnvironmentState.from_record(value).memories
    else:
        rows = _read_updates(top, tuple(names))
    return (all(not queue for _, memory in rows for _, queue in memory.pending)
            and all(item is None for _, memory in rows for _, item in memory.responses))


class MemoryTraceReader:
    """Fold a recorded prefix in place, exporting full states only on request."""

    def __init__(self, initial):
        self._reset(initial)

    def _reset(self, state):
        if type(state) is not MemoryEnvironmentState:
            raise TypeError("a memory successor needs the preceding complete state")
        self._memories = {name: _protocol.RuntimeState(memory)
                          for name, memory in state.memories}

    def consume(self, value):
        top = dict(record_pairs(value, "memory.successor"))
        if set(top) == {"memories"}:
            self._reset(MemoryEnvironmentState.from_record(value))
            return
        # Decode all rows before changing any state. A malformed later memory
        # must not leave this reader with an accepted partial prefix.
        updates = _read_updates(top, tuple(self._memories))
        for name, changed in updates:
            self._memories[name].commit(changed)

    def snapshot(self):
        return MemoryEnvironmentState((name, MemoryState(**state.snapshot()._asdict()))
                                      for name, state in self._memories.items())

    def drive(self, interfaces, environment, slot, *, reset_domains=()):
        """Recover the current drive without copying sparse historical cells."""
        signals = {}
        for interface in interfaces:
            run = environment.run(interface.name)
            ready = {port.id: run.ready_at(port.id, slot) for port in interface.ports}
            signals.update(_protocol.drive(interface, ready, self._memories[interface.name],
                                           reset=interface.clock in reset_domains))
        return signals


def read_memory_successor(previous, value):
    """Decode one complete boundary state, in dense or prefix-relative form."""
    reader = MemoryTraceReader(previous)
    reader.consume(value)
    return reader.snapshot()


def trace_memory_states(contract, trace):
    """Yield complete prefix states without retaining a full image per slot."""
    reader = _trace_reader(contract, trace)
    for record in trace.records:
        reader.consume(dict(record.environment)["external-memory"])
        yield reader.snapshot()


def trace_memory_drives(contract, trace):
    """Pre-slot pins recovered from recorded successors, not future samples.

    The physical runner holds these pins through control settling, clock and
    clock return. The successor supplies only the *next* slot's drive.
    """
    reader = _trace_reader(contract, trace)
    seed = trace.stimulus
    atomic = len(contract.clocks) > 1 or seed.clock_schedule is not None
    for index, (slot, record) in enumerate(zip(seed.slots, trace.records, strict=True)):
        resets = slot.resets if atomic else (
            (contract.clocks[0].name,) if slot.reset else ())
        yield reader.drive(contract.memories, seed.memory_environment, index, reset_domains=resets)
        reader.consume(dict(record.environment)["external-memory"])


def final_memory_state(contract, trace):
    """Consume each delta once; do not build intermediate full snapshots."""
    reader = _trace_reader(contract, trace)
    for record in trace.records:
        reader.consume(dict(record.environment)["external-memory"])
    return reader.snapshot()


def _trace_reader(contract, trace):
    return MemoryTraceReader(MemoryEnvironmentState.initial(
        contract.memories, trace.stimulus.memory_environment))
