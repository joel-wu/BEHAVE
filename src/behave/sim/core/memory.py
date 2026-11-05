"""Finite external-memory protocol owned by the task and the run.

There are deliberately three records here.

``MemoryInterface`` is a task fact: the physical request/response ports, widths,
clock domain and protocol version.  ``MemoryRun`` is an environment fact for one
stimulus: initial cells, read latency, and ready choices. A runtime request uses
a counter-indexed policy; its completed trace stores the *actual* finite bits.
``MemoryState`` is the complete mutable state of that
environment at one boundary.  Concrete replay, search, snapshots and an exact
product can therefore use one transition rather than independently implementing a
memory server.

The transition is read-first.  A request commits only on ``valid && ready``;
accepted reads snapshot their value before any write from the same slot; pending
reads are FIFO per port; and at most one response per port is presented per slot.
Unwritten cells use :func:`memory_junk_value`, a deterministic total function.
"""

from __future__ import annotations

import hashlib

from behave.core import runtime_defaults as defaults

from ..memory_driver import _protocol
from ..memory_driver._protocol import (
    MEMORY_JUNK_VERSION,
    _cell,
    _counter_prefix,
    memory_junk_parameters,
    memory_junk_reader,
    memory_junk_value,
    memory_port_prefix,
)
from .frozen import Frozen
from .record_value import _immutable_identity_value, canonical_json, record_pairs, thaw_record_value
from .refusal import Refusal, Refused

__all__ = [
    "EXTERNAL_MEMORY_PROTOCOL",
    "MEMORY_ENVIRONMENT_VERSION",
    "MEMORY_FULL_STATE_IMAGE",
    "MEMORY_FULL_STATE_MAX_CELLS",
    "MEMORY_JUNK_VERSION",
    "MEMORY_SPARSE_STATE_IMAGE",
    "MemoryEnvironment",
    "MemoryEnvironmentState",
    "MemoryInterface",
    "MemoryPort",
    "MemoryRealization",
    "MemoryRun",
    "MemoryState",
    "advance_memory_environment",
    "advance_memory_state",
    "drive_memory_environment",
    "drive_memory_state",
    "materialize_memory_environment_state",
    "memory_artifact_signal_set",
    "memory_junk_parameters",
    "memory_junk_reader",
    "memory_junk_value",
    "memory_port_prefix",
    "memory_port_signal_set",
]


EXTERNAL_MEMORY_PROTOCOL = (
    "external-memory/read-first-request-ready-valid-run-latency-one-slot-response/"
    "no-write-priority/index-mix-junk/cancel-on-domain-reset/3")
MEMORY_ENVIRONMENT_VERSION = "memory-environment/3"
# Full cell images are an audit/exact-relation representation, not the runtime
# protocol.  Runtime states stay sparse and therefore support every declared address
# width; callers needing a pointwise image receive a named finite capability boundary.
MEMORY_FULL_STATE_IMAGE = "memory-full-state-image/1"
MEMORY_FULL_STATE_MAX_CELLS = defaults.SIM_MEMORY_FULL_STATE_CELLS
MEMORY_SPARSE_STATE_IMAGE = "memory-sparse-implicit-junk/2"


def _exact_int(value, kind, why, *, minimum=None):
    if type(value) is not int or minimum is not None and value < minimum:
        raise Refused(Refusal(kind, why, got=repr(value)))
    return value


def _name(value, kind, what):
    if type(value) is not str or not value:
        raise Refused(Refusal(kind, f"{what} is a nonempty exact string"))
    return value


def memory_port_signal_set(memory, port, kind):
    """All physical signals owned by one declared memory port."""

    if kind not in MemoryPort.KINDS:
        raise ValueError(f"unknown external-memory port kind {kind!r}")
    prefix = memory_port_prefix(memory, port)
    names = {
        prefix + "_req_valid", prefix + "_req_addr", prefix + "_req_ready",
    }
    if kind in ("r", "rw"):
        names.update({prefix + "_resp_valid", prefix + "_resp_rdata"})
    if kind in ("w", "rw"):
        names.add(prefix + "_req_wdata")
    if kind == "rw":
        names.add(prefix + "_req_we")
    return names


def memory_artifact_signal_set(interface):
    """Artifact-driven request pins of one exact memory interface."""

    if type(interface) is not MemoryInterface:
        raise TypeError("memory artifact signals need an exact MemoryInterface")
    names = set()
    for port in interface.ports:
        prefix = memory_port_prefix(interface.name, port.id)
        names.update({prefix + "_req_valid", prefix + "_req_addr"})
        if port.writes:
            names.add(prefix + "_req_wdata")
        if port.kind == "rw":
            names.add(prefix + "_req_we")
    return names


class MemoryPort(Frozen):
    """One request/response port in a task-owned memory interface."""

    __slots__ = ("id", "kind")
    KINDS = ("r", "w", "rw")

    def __init__(self, kind, id=None):
        if kind not in self.KINDS or type(kind) is not str:
            raise Refused(Refusal(
                "memory-port-kind-invalid",
                "a memory port is exactly read-only, write-only, or read/write",
                got=repr(kind)))
        if id is not None and (type(id) is not str or not id):
            raise Refused(Refusal(
                "memory-port-id-invalid",
                "a multi-port memory identifier is absent or a nonempty exact string",
                got=repr(id)))
        self.kind = kind
        self.id = id
        self._freeze()

    @property
    def reads(self):
        return self.kind in ("r", "rw")

    @property
    def writes(self):
        return self.kind in ("w", "rw")

    def as_dict(self):
        return {"id": self.id, "kind": self.kind}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"id", "kind"}:
            raise TypeError("a memory-port image has exactly id and kind")
        return cls(value["kind"], value["id"])


class MemoryInterface(Frozen):
    """The task's finite external-memory declaration."""

    __slots__ = (
        "addr_width", "clock", "data_width", "name", "ports", "protocol", "sampling",
    )

    def __init__(self, name, addr_width, data_width, clock, ports, *,
                 protocol=EXTERNAL_MEMORY_PROTOCOL, sampling=None):
        self.name = _name(name, "memory-name-invalid", "a memory name")
        self.addr_width = _exact_int(
            addr_width, "memory-address-width-invalid",
            "a memory address width is a positive exact integer", minimum=1)
        self.data_width = _exact_int(
            data_width, "memory-data-width-invalid",
            "a memory data width is a positive exact integer", minimum=1)
        self.clock = _name(
            clock, "memory-clock-invalid", "a memory clock domain")
        if protocol != EXTERNAL_MEMORY_PROTOCOL:
            raise Refused(Refusal(
                "memory-protocol-unsupported",
                "this build only implements its versioned read-first request "
                "ready/valid protocol with an unbackpressured one-slot response",
                got=protocol, supported=EXTERNAL_MEMORY_PROTOCOL))
        self.protocol = protocol
        if type(ports) not in (tuple, list) or not ports \
                or any(type(port) is not MemoryPort for port in ports):
            raise Refused(Refusal(
                "memory-ports-invalid",
                "a memory declares a nonempty finite list of exact MemoryPort records"))
        ids = [port.id for port in ports]
        if len(set(ids)) != len(ids):
            raise Refused(Refusal(
                "memory-port-id-duplicate",
                "two ports of one memory share an identifier",
                memory=name, ports=ids))
        if len(ports) > 1 and any(port.id is None for port in ports):
            raise Refused(Refusal(
                "memory-multiport-id-missing",
                "every port of a multi-port memory needs an explicit identifier",
                memory=name))
        self.ports = tuple(sorted(
            ports, key=lambda port: (port.id is not None, port.id or "")))
        from .memory_sampling import MemorySampling

        if sampling is not None:
            if type(sampling) is not MemorySampling:
                raise TypeError("memory sampling is an exact MemorySampling record")
            sampling.validate_shape(self.depth, self.data_width)
        self.sampling = sampling
        self._freeze()

    @property
    def depth(self):
        return 1 << self.addr_width

    def port(self, id):
        for port in self.ports:
            if port.id == id:
                return port
        return None

    def as_dict(self):
        return {
            "name": self.name,
            "addr_width": self.addr_width,
            "data_width": self.data_width,
            "clock": self.clock,
            "ports": [port.as_dict() for port in self.ports],
            "protocol": self.protocol,
            **({"sampling": self.sampling.as_dict()} if self.sampling is not None else {}),
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"name", "addr_width", "data_width", "clock", "ports", "protocol"}
        if type(value) is not dict or set(value) not in (fields, fields | {"sampling"}):
            raise TypeError("a memory-interface image has its complete field set")
        if type(value["ports"]) is not list:
            raise TypeError("a memory-interface image carries a finite port list")
        from .memory_sampling import MemorySampling

        return cls(
            value["name"], value["addr_width"], value["data_width"],
            value["clock"], [MemoryPort.from_dict(p) for p in value["ports"]],
            protocol=value["protocol"],
            sampling=MemorySampling.from_dict(value["sampling"]) if "sampling" in value else None)


class MemoryRealization(Frozen):
    """Physical external-memory shape reported independently by a candidate frontend."""

    __slots__ = ("addr_width", "clock", "data_width", "name", "ports")

    def __init__(self, name, addr_width, data_width, clock, ports):
        declared = MemoryInterface(name, addr_width, data_width, clock, ports)
        self.name = declared.name
        self.addr_width = declared.addr_width
        self.data_width = declared.data_width
        self.clock = declared.clock
        self.ports = declared.ports
        self._freeze()

    def as_dict(self):
        return {
            "name": self.name,
            "addr_width": self.addr_width,
            "data_width": self.data_width,
            "clock": self.clock,
            "ports": [port.as_dict() for port in self.ports],
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"name", "addr_width", "data_width", "clock", "ports"}
        if type(value) is not dict or set(value) != fields:
            raise TypeError("a memory-realization image has its complete field set")
        if type(value["ports"]) is not list:
            raise TypeError("a memory-realization image carries a finite port list")
        return cls(
            value["name"], value["addr_width"], value["data_width"],
            value["clock"], [MemoryPort.from_dict(p) for p in value["ports"]])


class MemoryRun(Frozen):
    """Initialization and either actual ready bits or their causal run policy.

    Runtime requests use a counter-indexed policy without allocating a horizon.
    Completed traces materialize precisely the prefix which actually executed.
    Both forms use the same read/write transition and the same ready-bit recipe.
    """

    __slots__ = ("_ready_prefixes", "initial", "name", "read_latency", "ready", "ready_policy")

    def __init__(self, name, *, initial=(), read_latency=1, ready=(), ready_policy=None):
        self.name = _name(name, "memory-run-name-invalid", "a memory-run name")
        self.read_latency = _exact_int(
            read_latency, "memory-read-latency-invalid",
            "a read latency is a positive exact slot count", minimum=1)
        try:
            initial = tuple(initial.items() if type(initial) is dict else initial)
        except TypeError:
            raise Refused(Refusal(
                "memory-initialization-invalid",
                "memory initialization is a finite address/value mapping")) from None
        cells = []
        for pair in initial:
            if type(pair) not in (tuple, list) or len(pair) != 2:
                raise Refused(Refusal(
                    "memory-initialization-invalid",
                    "every initialized cell is one address/value pair"))
            addr, value = pair
            _exact_int(addr, "memory-initial-address-invalid",
                       "an initialized address is a nonnegative exact integer", minimum=0)
            _exact_int(value, "memory-initial-value-invalid",
                       "an initialized value is a nonnegative exact integer", minimum=0)
            cells.append((addr, value))
        if len({addr for addr, _ in cells}) != len(cells):
            raise Refused(Refusal(
                "memory-initial-address-duplicate",
                "one memory initialization assigns an address more than once",
                memory=name))
        self.initial = tuple(sorted(cells))

        try:
            ready = tuple(ready.items() if type(ready) is dict else ready)
        except TypeError:
            raise Refused(Refusal(
                "memory-ready-schedule-invalid",
                "a ready schedule is a finite mapping from port ids to bit sequences")) \
                from None
        schedules = []
        for pair in ready:
            if type(pair) not in (tuple, list) or len(pair) != 2:
                raise Refused(Refusal(
                    "memory-ready-schedule-invalid",
                    "every ready schedule names one port and one finite bit sequence"))
            port, bits = pair
            if port is not None and (type(port) is not str or not port):
                raise Refused(Refusal(
                    "memory-ready-port-invalid",
                    "a ready schedule port id is absent or a nonempty exact string"))
            if type(bits) not in (tuple, list) or any(
                    type(bit) is not int or bit not in (0, 1) for bit in bits):
                raise Refused(Refusal(
                    "memory-ready-bits-invalid",
                    "a ready schedule is an exact finite sequence of zero/one bits",
                    memory=name, port=port))
            schedules.append((port, tuple(bits)))
        if len({port for port, _ in schedules}) != len(schedules):
            raise Refused(Refusal(
                "memory-ready-port-duplicate",
                "one memory run carries two ready schedules for the same port",
                memory=name))
        self.ready = tuple(sorted(
            schedules, key=lambda item: (item[0] is not None, item[0] or "")))
        if ready_policy is not None:
            if (type(ready_policy) not in (tuple, list) or len(ready_policy) != 2
                    or any(type(v) is not int for v in ready_policy)
                    or not 0 <= ready_policy[1] <= 1 << 256
                    or any(bits for _, bits in self.ready)):
                raise ValueError("runtime memory needs (seed, threshold) and empty per-port schedules")
            ready_policy = tuple(ready_policy)
        self.ready_policy = ready_policy
        # Bytes, not mutable hashlib objects: wire identity and process transfer
        # stay unchanged. Only the slot suffix varies during a run.
        self._ready_prefixes = (() if ready_policy is None else tuple(
            _counter_prefix("external-memory-ready/1", ready_policy[0], name, port, index)
            for index, (port, _) in enumerate(self.ready)))
        self._freeze()

    @classmethod
    def from_policy(cls, interface, horizon, *, initial=(), read_latency=1,
                    stall_rate=0.0, seed=0):
        """Choose a runtime policy, or expand it when an explicit horizon is given."""

        if type(interface) is not MemoryInterface:
            raise TypeError("a memory policy expands against one exact MemoryInterface")
        if horizon is not None:
            _exact_int(horizon, "memory-horizon-invalid",
                       "a memory ready schedule has a finite nonnegative horizon", minimum=0)
        if type(stall_rate) not in (int, float) or isinstance(stall_rate, bool) \
                or not 0.0 <= stall_rate <= 1.0:
            raise Refused(Refusal(
                "memory-stall-rate-invalid",
                "a memory stall rate lies in the closed interval from zero to one",
                got=repr(stall_rate)))
        _exact_int(seed, "memory-seed-invalid", "a memory seed is an exact integer")
        try:
            initial_rows = tuple(
                initial.items() if type(initial) is dict else initial)
        except TypeError:
            raise Refused(Refusal(
                "memory-initialization-invalid",
                "memory initialization is a finite address/value mapping")) from None
        for row in initial_rows:
            if type(row) not in (tuple, list) or len(row) != 2:
                raise Refused(Refusal(
                    "memory-initialization-invalid",
                    "every initialized cell is one address/value pair"))
            address, value = row
            if type(address) is not int or not 0 <= address < interface.depth:
                raise Refused(Refusal(
                    "memory-initial-address-out-of-range",
                    "an initialized address fits the task-owned memory address width",
                    memory=interface.name, address=repr(address),
                    address_width=interface.addr_width))
            if type(value) is not int or not 0 <= value < 1 << interface.data_width:
                raise Refused(Refusal(
                    "memory-initial-value-out-of-range",
                    "an initialized value fits the task-owned memory data width",
                    memory=interface.name, value=repr(value),
                    data_width=interface.data_width))
        threshold = int(stall_rate * (1 << 256))
        run = cls(interface.name, initial=initial_rows, read_latency=read_latency,
                  ready=[(port.id, ()) for port in interface.ports],
                  ready_policy=(seed, threshold))
        return run if horizon is None else run.materialize(horizon)

    def materialize(self, horizon):
        if self.ready_policy is None:
            return self
        return MemoryRun(self.name, initial=self.initial, read_latency=self.read_latency,
                         ready=[(port, tuple(self.ready_at(port, slot) for slot in range(horizon)))
                                for port, _ in self.ready])

    def ready_at(self, port, slot):
        if type(slot) is not int or slot < 0:
            raise Refused(Refusal(
                "memory-ready-slot-invalid",
                "a memory ready lookup names a nonnegative exact slot",
                memory=self.name, port=port, slot=repr(slot)))
        for port_index, (found, bits) in enumerate(self.ready):
            if found == port:
                if self.ready_policy is not None:
                    _, threshold = self.ready_policy
                    if threshold == 0:
                        return 1
                    if threshold == 1 << 256:
                        return 0
                    image = self._ready_prefixes[port_index] + _counter_prefix(slot)
                    return int(int.from_bytes(hashlib.sha256(image).digest(), "big") >= threshold)
                if slot >= len(bits):
                    raise Refused(Refusal(
                        "memory-ready-schedule-exhausted",
                        "a memory transition cannot read beyond its finite ready "
                        "schedule",
                        memory=self.name, port=port, slot=slot,
                        horizon=len(bits)))
                return bits[slot]
        raise Refused(Refusal(
            "memory-ready-port-missing",
            "a memory run has no ready schedule for a declared port",
            memory=self.name, port=port))

    def as_dict(self):
        return {
            "name": self.name,
            "initial": [[addr, value] for addr, value in self.initial],
            "read_latency": self.read_latency,
            "ready": [[port, list(bits)] for port, bits in self.ready],
            "ready_policy": None if self.ready_policy is None else list(self.ready_policy),
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"name", "initial", "read_latency", "ready", "ready_policy"}
        if type(value) is not dict or set(value) != fields:
            raise TypeError("a memory-run image has its complete field set")
        if type(value["initial"]) is not list or type(value["ready"]) is not list:
            raise TypeError("memory-run initialization and ready schedules are lists")
        return cls(
            value["name"], initial=value["initial"],
            read_latency=value["read_latency"], ready=value["ready"], ready_policy=value["ready_policy"])


class MemoryEnvironment(Frozen):
    """All memory-run choices carried by one stimulus, with a stable identity."""

    __slots__ = ("_digest", "runs", "version")

    def __init__(self, runs=(), *, version=MEMORY_ENVIRONMENT_VERSION):
        if version != MEMORY_ENVIRONMENT_VERSION:
            raise Refused(Refusal(
                "memory-environment-version-unsupported",
                "this build only decodes its current external-memory run image",
                got=version, supported=MEMORY_ENVIRONMENT_VERSION))
        if type(runs) not in (tuple, list) or any(
                type(run) is not MemoryRun for run in runs):
            raise Refused(Refusal(
                "memory-environment-runs-invalid",
                "a memory environment carries a finite list of exact MemoryRun records"))
        names = [run.name for run in runs]
        if len(set(names)) != len(names):
            raise Refused(Refusal(
                "memory-environment-name-duplicate",
                "one stimulus carries two runs for the same external memory",
                memories=names))
        self.runs = tuple(sorted(runs, key=lambda run: run.name))
        self.version = version
        self._digest = hashlib.sha256(
            canonical_json(self.as_dict()).encode()).hexdigest()
        self._freeze()

    def run(self, name):
        for run in self.runs:
            if run.name == name:
                return run
        return None

    def faults_against(self, memories, horizon):
        declared = {memory.name: memory for memory in memories}
        actual = {run.name: run for run in self.runs}
        faults = []
        if set(declared) != set(actual):
            faults.append(
                "the memory environment and task declaration name different memories")
        for name in sorted(set(declared) & set(actual)):
            memory, run = declared[name], actual[name]
            if {port.id for port in memory.ports} != {port for port, _ in run.ready}:
                faults.append(
                    f"memory {name!r} has no one ready schedule per declared port")
            for port, bits in run.ready:
                if horizon is None:
                    if run.ready_policy is None:
                        faults.append(f"memory {name!r} runtime request has no ready policy")
                elif run.ready_policy is not None or len(bits) != horizon:
                    faults.append(
                        f"memory {name!r} port {port!r} has {len(bits)} ready bits "
                        f"for a {horizon}-slot stimulus")
            for addr, value in run.initial:
                if addr >= memory.depth:
                    faults.append(
                        f"memory {name!r} initializes address {addr}, outside its "
                        f"{memory.addr_width}-bit address space")
                if value >= 1 << memory.data_width:
                    faults.append(
                        f"memory {name!r} initializes value {value}, outside its "
                        f"{memory.data_width}-bit data carrier")
        return tuple(faults)

    def materialize(self, horizon):
        return MemoryEnvironment([run.materialize(horizon) for run in self.runs])

    def as_dict(self):
        return {"version": self.version,
                "runs": [run.as_dict() for run in self.runs]}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"version", "runs"}:
            raise TypeError("a memory-environment image has exactly version and runs")
        if type(value["runs"]) is not list:
            raise TypeError("a memory-environment image carries a finite run list")
        got = cls([MemoryRun.from_dict(run) for run in value["runs"]],
                  version=value["version"])
        if got.as_dict() != value:
            raise ValueError("a memory-environment image is not canonical")
        return got

    @property
    def digest(self):
        return self._digest


class MemoryState(Frozen):
    """Complete state of one finite memory environment at a slot boundary."""

    __slots__ = ("cells", "pending", "responses", "writes")

    def __init__(self, *, cells=(), pending=(), responses=(), writes=()):
        self.cells = self._pairs(cells, "cells")
        self.writes = self._pairs(writes, "writes")
        pending_rows = self._rows(pending, "pending")
        parsed_pending = []
        for port, queue in pending_rows:
            if type(queue) not in (tuple, list):
                raise TypeError("a pending-read queue is a finite exact sequence")
            entries = []
            for entry in queue:
                if type(entry) not in (tuple, list) or len(entry) != 3:
                    raise TypeError(
                        "a pending read has exactly address, value and slots-left")
                address, value, left = entry
                if any(type(item) is not int for item in entry) or min(
                        address, value) < 0 or left < 1:
                    raise TypeError(
                        "a pending read carries nonnegative exact address/value and "
                        "a positive exact slots-left")
                entries.append((address, value, left))
            parsed_pending.append((port, tuple(entries)))
        self.pending = tuple(parsed_pending)

        response_rows = self._rows(responses, "responses")
        parsed_responses = []
        for port, response in response_rows:
            if response is not None:
                if type(response) not in (tuple, list) or len(response) != 2 \
                        or any(type(item) is not int for item in response) \
                        or min(response) < 0:
                    raise TypeError(
                        "a response is absent or an exact nonnegative address/value "
                        "pair")
                response = tuple(response)
            parsed_responses.append((port, response))
        self.responses = tuple(parsed_responses)
        self._freeze()

    @staticmethod
    def _pairs(rows, name):
        if type(rows) is dict:
            rows = rows.items()
        try:
            rows = tuple(rows)
        except TypeError:
            raise TypeError(f"memory-state {name} are a finite pair sequence") from None
        parsed = []
        for row in rows:
            if type(row) not in (tuple, list) or len(row) != 2 \
                    or any(type(item) is not int for item in row) or min(row) < 0:
                raise TypeError(
                    f"memory-state {name} contain exact nonnegative integer pairs")
            parsed.append(tuple(row))
        if len({address for address, _ in parsed}) != len(parsed):
            raise ValueError(f"memory-state {name} repeat an address")
        return tuple(sorted(parsed))

    @staticmethod
    def _rows(rows, name):
        if type(rows) is dict:
            rows = rows.items()
        try:
            rows = tuple(rows)
        except TypeError:
            raise TypeError(f"memory-state {name} are a finite named sequence") from None
        parsed = []
        for row in rows:
            if type(row) not in (tuple, list) or len(row) != 2:
                raise TypeError(f"memory-state {name} contain exact named rows")
            port = row[0]
            if port is not None and (type(port) is not str or not port):
                raise TypeError(f"memory-state {name} use exact memory-port ids")
            parsed.append((port, row[1]))
        if len({port for port, _ in parsed}) != len(parsed):
            raise ValueError(f"memory-state {name} repeat a memory-port id")
        return tuple(sorted(
            parsed, key=lambda item: (item[0] is not None, item[0] or "")))

    @classmethod
    def initial(cls, interface, run):
        if type(interface) is not MemoryInterface or type(run) is not MemoryRun:
            raise TypeError("memory initial state needs exact interface and run records")
        if run.name != interface.name:
            raise Refused(Refusal(
                "memory-run-interface-mismatch",
                "a memory state can only start from the run bearing its interface "
                "name",
                interface=interface.name, run=run.name))
        state = cls(
            cells=run.initial,
            pending=[(port.id, ()) for port in interface.ports if port.reads],
            responses=[(port.id, None) for port in interface.ports if port.reads])
        faults = state.faults_against(interface, run)
        if faults:
            raise Refused(Refusal(
                "memory-state-outside-interface",
                "the initial memory state is outside its declared finite interface",
                memory=interface.name, first=faults[0], count=len(faults)))
        return state

    def faults_against(self, interface, run=None):
        """All ways this state lies outside one declared finite memory.

        ``MemoryState`` is serializable without a live task object.  A consumer that
        gives the image semantic meaning closes it against the task-owned interface
        (and, when available, the run-owned latency) before using it.
        """

        if type(interface) is not MemoryInterface:
            raise TypeError("memory-state validation needs one MemoryInterface")
        if run is not None and type(run) is not MemoryRun:
            raise TypeError("memory-state validation needs one MemoryRun or None")
        faults = []
        data_limit = 1 << interface.data_width

        def check_pairs(rows, what):
            for address, value in rows:
                if address >= interface.depth:
                    faults.append(
                        f"{what} address {address} exceeds the "
                        f"{interface.addr_width}-bit address carrier")
                if value >= data_limit:
                    faults.append(
                        f"{what} value {value} exceeds the "
                        f"{interface.data_width}-bit data carrier")

        check_pairs(self.cells, "cell")
        check_pairs(self.writes, "write")
        cells = dict(self.cells)
        for address, value in self.writes:
            if cells.get(address) != value:
                faults.append(
                    f"write-set value {value} at address {address} differs from the "
                    "current cell")

        read_ports = {port.id for port in interface.ports if port.reads}
        pending = dict(self.pending)
        responses = dict(self.responses)
        if set(pending) != read_ports:
            faults.append("pending queues do not exactly name the read-capable ports")
        if set(responses) != read_ports:
            faults.append("responses do not exactly name the read-capable ports")
        for port in sorted(read_ports, key=lambda value: (value is not None,
                                                          value or "")):
            last_left = 0
            for address, value, left in pending.get(port, ()):
                if address >= interface.depth:
                    faults.append(
                        f"pending read on port {port!r} has out-of-range address "
                        f"{address}")
                if value >= data_limit:
                    faults.append(
                        f"pending read on port {port!r} has out-of-range value "
                        f"{value}")
                if left <= last_left:
                    faults.append(
                        f"pending reads on port {port!r} are not in FIFO latency "
                        "order")
                if run is not None and left >= run.read_latency:
                    faults.append(
                        f"pending read on port {port!r} has {left} slots left under "
                        f"latency {run.read_latency}")
                last_left = left
            response = responses.get(port)
            if response is not None:
                address, value = response
                if address >= interface.depth:
                    faults.append(
                        f"response on port {port!r} has out-of-range address "
                        f"{address}")
                if value >= data_limit:
                    faults.append(
                        f"response on port {port!r} has out-of-range value {value}")
        return tuple(faults)

    def as_dict(self):
        return {
            "cells": [[a, v] for a, v in self.cells],
            "pending": [[port, [[a, v, left] for a, v, left in queue]]
                        for port, queue in self.pending],
            "responses": [[port, None if response is None else list(response)]
                          for port, response in self.responses],
            "writes": [[a, v] for a, v in self.writes],
        }

    def __eq__(self, other):
        return type(other) is MemoryState and (
            self.cells, self.pending, self.responses, self.writes
        ) == (
            other.cells, other.pending, other.responses, other.writes
        )

    def __hash__(self):
        return hash((self.cells, self.pending, self.responses, self.writes))

    @classmethod
    def from_dict(cls, value):
        fields = {"cells", "pending", "responses", "writes"}
        if type(value) is not dict or set(value) != fields:
            raise TypeError("a memory-state image has its complete field set")
        if any(type(value[name]) is not list for name in fields):
            raise TypeError("memory-state components are finite lists")
        got = cls(**value)
        if got.as_dict() != value:
            raise ValueError("a memory-state image is not canonical")
        return got


class MemoryEnvironmentState(Frozen):
    """Complete product state for all external memories."""

    __slots__ = ("_digest", "memories")

    def __init__(self, memories=()):
        if type(memories) is dict:
            memories = memories.items()
        # Detach pair wrappers as well as the outer sequence. A caller-supplied
        # list pair must not be able to rename or replace a sealed memory state.
        rows = tuple((name, state) for name, state in memories)
        if any(type(name) is not str or type(state) is not MemoryState
               for name, state in rows):
            raise TypeError("memory environment state maps names to exact MemoryState")
        if len({name for name, _ in rows}) != len(rows):
            raise ValueError("memory environment state repeats a memory name")
        self.memories = tuple(sorted(rows))
        # Ordinary replay reads the state, not this identity. Reuse the product
        # record's deferral rule; other carriers retain constructor-time hashing
        # and encoding errors. No cells, pending reads or writes are omitted.
        lazy = (type(self) is MemoryEnvironmentState
                and _immutable_identity_value(tuple(
                    (name, state.cells, state.pending, state.responses, state.writes)
                    for name, state in self.memories)))
        self._digest = None if lazy else hashlib.sha256(
            canonical_json(self.as_dict()).encode()).hexdigest()
        self._freeze()

    @classmethod
    def initial(cls, interfaces, environment):
        interfaces = tuple(interfaces)
        if not interfaces and environment is None:
            # The empty task-memory product has one state and no run inputs.
            environment = MemoryEnvironment()
        if type(environment) is not MemoryEnvironment:
            raise TypeError("memory initial state needs an exact MemoryEnvironment")
        if any(type(interface) is not MemoryInterface for interface in interfaces):
            raise TypeError(
                "memory initial state needs exact MemoryInterface records")
        if len({interface.name for interface in interfaces}) != len(interfaces):
            raise ValueError("memory initial state repeats an interface name")
        if {interface.name for interface in interfaces} != {
                run.name for run in environment.runs}:
            raise Refused(Refusal(
                "memory-environment-interface-mismatch",
                "the memory state and run environment must name exactly the task's "
                "declared memories"))
        rows = []
        for interface in interfaces:
            if type(interface) is not MemoryInterface:
                raise TypeError(
                    "memory initial state needs exact MemoryInterface records")
            run = environment.run(interface.name)
            if run is None:
                raise Refused(Refusal(
                    "memory-environment-run-missing",
                    "the memory environment has no run for a declared external "
                    "memory",
                    memory=interface.name))
            rows.append((interface.name, MemoryState.initial(interface, run)))
        return cls(rows)

    def faults_against(self, interfaces, environment=None):
        """Validate the complete product image against task and optional run facts."""

        interfaces = tuple(interfaces)
        if any(type(interface) is not MemoryInterface for interface in interfaces):
            raise TypeError(
                "memory-environment state validation needs MemoryInterface records")
        if environment is not None and type(environment) is not MemoryEnvironment:
            raise TypeError(
                "memory-environment state validation needs MemoryEnvironment or None")
        declared = {interface.name: interface for interface in interfaces}
        actual = {name: state for name, state in self.memories}
        faults = []
        if set(declared) != set(actual):
            faults.append(
                "the memory state and task declaration name different memories")
        for name in sorted(set(declared) & set(actual)):
            run = None if environment is None else environment.run(name)
            if environment is not None and run is None:
                faults.append(f"the memory environment omits run {name!r}")
                continue
            faults.extend(
                f"memory {name!r}: {fault}"
                for fault in actual[name].faults_against(declared[name], run))
        return tuple(faults)

    def state(self, name):
        for found, state in self.memories:
            if found == name:
                return state
        raise KeyError(name)

    def as_dict(self):
        return {"memories": [[name, state.as_dict()]
                             for name, state in self.memories]}

    def __eq__(self, other):
        return (type(other) is MemoryEnvironmentState
                and self.memories == other.memories)

    def __hash__(self):
        return hash(self.memories)

    @classmethod
    def from_record(cls, value: object) -> MemoryEnvironmentState:
        """Decode JSON or the older frozen product-state representation.

        Only the two declared record positions are mappings. Cell pairs, port
        queues and responses remain ordered sequences; no shape guessing or
        dense memory materialization is involved. Task/run validation remains
        with the consumer that supplies those declarations.
        """
        top = dict(record_pairs(value, "memory.environment"))
        if set(top) != {"memories"} or type(top["memories"]) is not tuple:
            raise ValueError("the memory boundary has no complete memory list")
        rows = []
        for row in top["memories"]:
            if type(row) is not tuple or len(row) != 2 or type(row[0]) is not str:
                raise ValueError("a memory boundary row has a name and state record")
            body = {name: thaw_record_value(child) for name, child in
                    record_pairs(row[1], f"memory.environment.{row[0]}")}
            rows.append([row[0], body])
        return cls.from_dict({"memories": rows})

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"memories"} \
                or type(value["memories"]) is not list:
            raise TypeError(
                "a memory-environment state image has one exact memory-state list")
        rows = []
        for row in value["memories"]:
            if type(row) is not list or len(row) != 2 or type(row[0]) is not str:
                raise TypeError(
                    "each memory-environment state row is a name and state image")
            rows.append((row[0], MemoryState.from_dict(row[1])))
        got = cls(rows)
        if got.as_dict() != value:
            raise ValueError("a memory-environment state image is not canonical")
        return got

    @property
    def digest(self):
        if self._digest is None:
            object.__setattr__(self, "_digest", hashlib.sha256(
                canonical_json(self.as_dict()).encode()).hexdigest())
        return self._digest


def materialize_memory_environment_state(interfaces, state, environment=None):
    """Canonical full-cell image used at exact/concrete audit boundaries.

    Runtime state remains sparse because deterministic junk is part of the protocol.
    An exact array, however, denotes every address.  Materializing both sides here
    gives the audit one representation without changing the runtime transition.
    """

    if type(state) is not MemoryEnvironmentState:
        raise TypeError("materializing memory needs an exact environment state")
    interfaces = tuple(interfaces)
    oversized = tuple(interface for interface in interfaces
                      if interface.depth > MEMORY_FULL_STATE_MAX_CELLS)
    if oversized:
        first = oversized[0]
        raise Refused(Refusal(
            "memory-full-state-image-too-large",
            "the sparse external-memory state is valid, but this versioned exact/"
            "audit representation cannot enumerate its complete address space",
            representation=MEMORY_FULL_STATE_IMAGE,
            maximum_cells=MEMORY_FULL_STATE_MAX_CELLS,
            memory=first.name, depth=first.depth,
            count=len(oversized)))
    faults = state.faults_against(interfaces, environment)
    if faults:
        raise Refused(Refusal(
            "memory-state-outside-interface",
            "an external-memory state is outside its task/run declaration",
            first=faults[0], count=len(faults)))
    rows = []
    for interface in interfaces:
        if type(interface) is not MemoryInterface:
            raise TypeError("materializing memory needs exact interface records")
        found = state.state(interface.name)
        rows.append((interface.name, MemoryState(
            cells=[(address, _cell(found, interface, address))
                   for address in range(interface.depth)],
            pending=found.pending, responses=found.responses,
            writes=found.writes)))
    return MemoryEnvironmentState(rows)


def drive_memory_state(interface, run, state, slot, *, reset=False):
    """Environment-owned input pins presented during ``slot``."""

    if not (type(interface) is MemoryInterface and type(run) is MemoryRun
            and type(state) is MemoryState):
        raise TypeError("memory drive needs exact interface, run and state records")
    faults = state.faults_against(interface, run)
    if faults:
        raise Refused(Refusal(
            "memory-state-outside-interface",
            "an external-memory state is outside its task/run declaration",
            memory=interface.name, first=faults[0], count=len(faults)))
    ready = {port.id: run.ready_at(port.id, slot) for port in interface.ports}
    return _protocol.drive(interface, ready, state, reset=reset)


def advance_memory_state(interface, run, state, slot, artifact_outputs, *, reset=False):
    """Pure read-first transition for one memory and one active clock edge."""
    return _advance_memory_state(interface, run, state, slot, artifact_outputs,
                                 reset=reset)[0]


def _advance_memory_state(interface, run, state, slot, artifact_outputs, *, reset=False):

    driven = drive_memory_state(interface, run, state, slot, reset=reset)
    try:
        transition = _protocol.transition(
            interface, run.read_latency, state, driven, artifact_outputs,
            slot=slot, reset=reset)
    except _protocol.MemoryProtocolError as exc:
        raise Refused(Refusal(exc.kind, exc.why, **exc.facts)) from exc
    after = transition.state
    return MemoryState(
        cells=after.cells, pending=after.pending,
        responses=after.responses, writes=after.writes), transition.writes


def drive_memory_environment(interfaces, environment, state, slot, *, reset_domains=()):
    driven = {}
    for interface in interfaces:
        driven.update(drive_memory_state(
            interface, environment.run(interface.name),
            state.state(interface.name), slot, reset=interface.clock in reset_domains))
    return driven


def advance_memory_environment(interfaces, environment, state, slot,
                               artifact_outputs, *, active_domains=None,
                               reset_domains=()):
    return advance_memory_environment_with_writes(
        interfaces, environment, state, slot, artifact_outputs,
        active_domains=active_domains, reset_domains=reset_domains)[0]


def advance_memory_environment_with_writes(interfaces, environment, state, slot,
                                         artifact_outputs, *, active_domains=None,
                                         reset_domains=()):
    """The same successor plus accepted writes, without diffing whole cell maps."""
    active = None if active_domains is None else set(active_domains)
    next_states = []
    writes = []
    for interface in interfaces:
        before = state.state(interface.name)
        after = before
        changed = ()
        reset = interface.clock in reset_domains
        if reset or active is None or interface.clock in active:
            after, changed = _advance_memory_state(
                interface, environment.run(interface.name), before, slot,
                artifact_outputs, reset=reset)
        next_states.append((interface.name, after))
        writes.append((interface.name, changed))
    return MemoryEnvironmentState(next_states), tuple(writes)
