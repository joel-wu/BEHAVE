"""List-like external memory used by an isolated Behavior reference.

The RTL memory product and the transaction-level reference must read and write the
same initial store.  This adapter gives ordinary Behavior sources their familiar
``self.mem[address]`` interface while using the task-owned memory declaration and the
stimulus-owned :class:`~behave.sim.core.memory.MemoryRun` as its authority.
"""

from __future__ import annotations

import operator
from contextlib import contextmanager

from ..core.memory import MemoryInterface, MemoryRun, memory_junk_reader

__all__ = ["ReferenceMemory"]

_AUDIT_IMAGE_VERSION = "external-memory-audit-image/1"


def reset_state(initial, state, external_fields=(), *, preserved_fields=()):
    """Reset control state, retaining external stores and untouched attributes."""
    return {**initial, **{name: state[name]
                          for name in (*external_fields, *preserved_fields)}}


@contextmanager
def preserve_external_memory(fields, names):
    """Reset control state without resetting run-owned stores or write evidence.

    Source reset may rebind a memory or mutate it in place. Restore both the
    original binding and its contents, also on failure. Sparse stores copy only
    explicit cells; the dense representation remains a differential oracle.
    """
    saved = []
    for name in names:
        memory = fields[name]
        if type(memory) is ReferenceMemory:
            image = memory._store.copy(), memory.writes.copy()
        elif type(memory) is list:
            image = memory.copy()
        else:
            raise TypeError("a bound external memory is a reference store or dense array")
        saved.append((name, memory, image))
    try:
        yield
    finally:
        for name, memory, image in saved:
            if type(memory) is ReferenceMemory:
                memory._store, memory.writes = image
            else:
                memory[:] = image
            fields[name] = memory


class ReferenceMemory:
    """A finite mutable memory with deterministic values for unwritten cells."""

    __slots__ = ("_data_width", "_default", "_depth", "_mask", "_name", "_store", "writes")

    def __init__(self, interface, run):
        if type(interface) is not MemoryInterface or type(run) is not MemoryRun:
            raise TypeError(
                "a reference memory needs exact MemoryInterface and MemoryRun records")
        if run.name != interface.name:
            raise ValueError("the memory interface and run name different memories")
        self._name = interface.name
        self._depth = interface.depth
        self._data_width = interface.data_width
        self._mask = (1 << interface.data_width) - 1
        self._default = memory_junk_reader(interface.name, interface.data_width)
        self._store = dict(run.initial)
        self.writes = {}

    def _address(self, address):
        try:
            got = operator.index(address)
        except TypeError:
            raise TypeError("memory indices are integers") from None
        if not 0 <= got < self._depth:
            raise IndexError("memory index out of range")
        return got

    def __getitem__(self, address):
        if type(address) is slice:
            return [self[index] for index in range(*address.indices(self._depth))]
        address = self._address(address)
        if address in self._store:
            return self._store[address]
        return self._default(address)

    def __setitem__(self, address, value):
        if type(address) is slice:
            indices = range(*address.indices(self._depth))
            values = list(value)
            if len(values) != len(indices):
                raise ValueError(
                    "attempt to assign sequence of size "
                    f"{len(values)} to extended slice of size {len(indices)}")
            for index, item in zip(indices, values):
                self[index] = item
            return
        address = self._address(address)
        try:
            stored = operator.index(value) & self._mask
        except TypeError:
            raise TypeError("memory values are integers") from None
        self._store[address] = stored
        self.writes[address] = stored

    def __len__(self):
        return self._depth

    def __contains__(self, address):
        try:
            address = operator.index(address)
        except TypeError:
            return False
        return 0 <= address < self._depth

    def __iter__(self):
        return (self[address] for address in range(self._depth))

    def get(self, address, default=None):
        del default
        return self[address]

    def keys(self):
        return self._store.keys()

    def values(self):
        return self._store.values()

    def items(self):
        return self._store.items()


def _audit_image(memory):
    """Return the exact sparse state carried by the isolation protocol.

    This is deliberately not a method dispatched through the model.  The trusted
    child calls it only after an exact-type check, so candidate values cannot forge
    the protocol-owned external-memory channel.
    """

    if type(memory) is not ReferenceMemory:
        raise TypeError("an external-memory audit image needs ReferenceMemory")
    return {
        "version": _AUDIT_IMAGE_VERSION,
        "name": memory._name,
        "length": memory._depth,
        "data_width": memory._data_width,
        "cells": [[address, value]
                  for address, value in sorted(memory._store.items())],
    }


def _from_audit_image(image, interface):
    """Validate a child image and reconstruct its compact SolverIR value."""

    from ..core.memory import MemoryInterface
    from ..symbolic.uniform_array import UniformArray

    fields = {"version", "name", "length", "data_width", "cells"}
    if type(interface) is not MemoryInterface:
        raise TypeError("an external-memory audit image needs MemoryInterface")
    if type(image) is not dict or set(image) != fields:
        raise ValueError("an external-memory audit image has its complete fields")
    if (image["version"] != _AUDIT_IMAGE_VERSION
            or image["name"] != interface.name
            or image["length"] != interface.depth
            or image["data_width"] != interface.data_width
            or type(image["cells"]) is not list):
        raise ValueError("an external-memory audit image disagrees with its interface")
    cells = []
    for row in image["cells"]:
        if (type(row) is not list or len(row) != 2
                or any(type(value) is not int or isinstance(value, bool)
                       for value in row)):
            raise ValueError("an external-memory audit cell is not an integer pair")
        address, value = row
        if not 0 <= address < interface.depth \
                or not 0 <= value < (1 << interface.data_width):
            raise ValueError("an external-memory audit cell lies outside its interface")
        cells.append((address, value))
    if cells != sorted(cells) \
            or len({address for address, _value in cells}) != len(cells):
        raise ValueError("external-memory audit cells are not canonical")
    return UniformArray.memory(
        interface.depth, interface.name, interface.data_width, cells)
