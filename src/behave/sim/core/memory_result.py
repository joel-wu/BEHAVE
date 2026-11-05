"""Final external-memory values, independent of transaction matching and timing.

Explicit cells override the task's indexed junk baseline. Comparing the union of
both sparse images is therefore extensionally equal to comparing the entire RAM;
write order, redundant writes and protocol queues are not result values.
"""

from __future__ import annotations

import hashlib

from .frozen import Frozen
from .memory import MemoryEnvironmentState, memory_junk_reader
from .record_value import canonical_json, record_pairs
from .refusal import Refusal, Refused
from .stimulus import thaw_record_value

MEMORY_RESULT_VERSION = "final-external-memory/1"


class MemoryResult(Frozen):
    """A canonical finite image of every declared external memory."""

    __slots__ = ("_digest", "memories")

    def __init__(self, memories=()):
        rows = []
        for name, cells in memories:
            cells = tuple(tuple(pair) for pair in cells)
            if (type(name) is not str or not name
                    or any(len(pair) != 2 or any(type(v) is not int or v < 0
                                                for v in pair) for pair in cells)
                    or len({a for a, _ in cells}) != len(cells)):
                raise ValueError("a memory result has named, nonnegative integer cells")
            rows.append((name, tuple(sorted(cells))))
        if len({name for name, _ in rows}) != len(rows):
            raise ValueError("a memory result repeats an interface")
        self.memories = tuple(sorted(rows))
        # This immutable image is referenced by every subsequent score/report.
        # Tuples have exactly the public lists' JSON bytes, without a detached
        # list per cell or another whole-memory walk each time a phase reads it.
        self._digest = hashlib.sha256(canonical_json({
            "version": MEMORY_RESULT_VERSION, "memories": self.memories,
        }).encode()).hexdigest()
        self._freeze()

    @property
    def digest(self):
        return self._digest

    def validate(self, interfaces):
        declared = {memory.name: memory for memory in interfaces}
        if {name for name, _ in self.memories} != set(declared):
            raise ValueError("a memory result must cover exactly the declared memories")
        for name, cells in self.memories:
            memory = declared[name]
            if any(a >= memory.depth or v >= 1 << memory.data_width for a, v in cells):
                raise ValueError("a memory result lies outside its declared cell type")
        return self

    def as_dict(self):
        return {"version": MEMORY_RESULT_VERSION,
                "memories": [[name, [list(pair) for pair in cells]]
                             for name, cells in self.memories]}

    @classmethod
    def from_dict(cls, image):
        if (type(image) is not dict or set(image) != {"version", "memories"}
                or image["version"] != MEMORY_RESULT_VERSION
                or type(image["memories"]) is not list):
            raise ValueError("a final memory image has its version and complete cells")
        result = cls(image["memories"])
        if result.as_dict() != image:
            raise ValueError("a final memory image is not canonical")
        return result


def require_memory_result_path(contract, stimulus):
    """Do not claim final-memory agreement on an unimplemented reference boundary."""
    if not contract.memories:
        return
    if len(contract.clocks) > 1:
        raise Refused(Refusal(
            "memory-result-atomic-reference-unavailable",
            "the atomic reference adapter does not yet bind and expose final external memory"))


def model_memory_result(model, contract):
    """Read only the trusted adapter's memory channel, never arbitrary model state."""
    if not contract.memories:
        return MemoryResult()
    reader = getattr(model, "_external_memory_result", None)
    if not callable(reader):
        raise Refused(Refusal(
            "memory-result-adapter-unavailable",
            "the trusted model adapter cannot expose final external-memory values"))
    result = reader(contract.memories)
    if type(result) is not MemoryResult:
        raise TypeError("a memory adapter returns an exact MemoryResult")
    return result.validate(contract.memories)


def trace_memory_result(contract, trace, *, behavior):
    """Project the last completed slot's product state; never use an earlier image."""
    if not contract.memories:
        return MemoryResult()
    if not trace.records:
        return MemoryResult((memory.name, trace.stimulus.memory_environment.run(
            memory.name).initial) for memory in contract.memories)
    environment = dict(trace.records[-1].environment)
    if behavior:
        result = MemoryResult.from_dict(thaw_record_value(environment["memory-result"]))
    else:
        from .memory_trace import final_memory_state

        image = dict(record_pairs(environment["external-memory"]))
        if set(image) == {"memories"}:
            state = MemoryEnvironmentState.from_record(image)
        else:
            # A compact successor is relative to its complete trace prefix, not
            # a final snapshot. Consume that prefix once, retaining one state.
            state = final_memory_state(contract, trace)
        faults = state.faults_against(contract.memories, trace.stimulus.memory_environment)
        if faults:
            raise ValueError(faults[0])
        result = MemoryResult((name, memory.cells) for name, memory in state.memories)
    return result.validate(contract.memories)


def memory_faults(interfaces, expected, observed):
    """One (memory, address, expected, observed) fault per unequal final cell."""
    expected.validate(interfaces)
    observed.validate(interfaces)
    want, got = dict(expected.memories), dict(observed.memories)
    faults = []
    for memory in interfaces:
        left, right = dict(want[memory.name]), dict(got[memory.name])
        default = None
        for address in sorted(left.keys() | right.keys()):
            if address not in left or address not in right:
                if default is None:
                    default = memory_junk_reader(memory.name, memory.data_width)
                value = default(address)
                a, b = left.get(address, value), right.get(address, value)
            else:
                a, b = left[address], right[address]
            if a != b:
                faults.append((memory.name, address, a, b))
    return tuple(faults)
