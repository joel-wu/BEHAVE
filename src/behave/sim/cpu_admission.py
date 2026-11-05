"""Host-local CPU admission shared by trusted Sim callers, including processes.

A coordinator owns one pool for its allocation and explicitly distributes its
endpoint. Permits reserve *whole replay waves*, not individual futures. No worker
is started while waiting, and nested maps reuse the parent permit serially.

The socket is a control channel, not a worker transport: only bounded JSON is
accepted. An active client disappearing quarantines the pool: its descendants
may still be using CPUs. Only external cleanup can justify creating a new pool.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import selectors
import socket
import tempfile
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from .core.failure import FailureRaised, RunFailure
from .core.refusal import Refusal
from .core.resources import job_cpu_limit
from .execution_control import check_execution
from .search_policy import SearchStopped

_WIRE_LIMIT = defaults.SIM_CPU_ADMISSION_WIRE_BYTES
_SCOPE = ContextVar("sim_cpu_scope", default=None)
_HELD = ContextVar("sim_cpu_permit", default=False)
_CLIENT_SOCKETS: weakref.WeakSet[socket.socket] = weakref.WeakSet()


def _close_inherited_clients():
    # Otherwise a forked descendant can hide its parent's disconnect even
    # though that descendant is forbidden to use the inherited permit/scope.
    for sock in tuple(_CLIENT_SOCKETS):
        sock.close()
    _CLIENT_SOCKETS.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_close_inherited_clients)


@dataclass
class _Permit:
    pid: int = field(default_factory=os.getpid)
    cleanup_confirmed: bool = True


def _failure(kind: str, why: str) -> FailureRaised:
    return FailureRaised(RunFailure.infrastructure("cpu-admission", Refusal(kind, why)))


def _encode(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"


def _read(sock: socket.socket, checkpoint: Callable[[], None], data: bytearray) -> dict:
    while True:
        checkpoint()
        if b"\n" in data:
            line, _, rest = data.partition(b"\n")
            data[:] = rest
            value = json.loads(line)
            if type(value) is not dict:
                raise ValueError("CPU control message must be an object")
            return value
        try:
            block = sock.recv(_WIRE_LIMIT + 1 - len(data))
        except TimeoutError:
            continue
        if not block:
            raise OSError("CPU coordinator disconnected")
        data.extend(block)
        if len(data) > _WIRE_LIMIT:
            raise ValueError("CPU control message exceeds its bound")


@dataclass(frozen=True)
class CpuPoolEndpoint:
    """Explicit trusted-worker handoff; do not expose this capability to candidates."""

    path: str
    host: str
    capacity: int
    token: str = field(repr=False)

    def __post_init__(self):
        if type(self.capacity) is not int or self.capacity < 1:
            raise ValueError("CPU capacity must be a positive exact integer")
        if any(type(x) is not str or not x for x in (self.path, self.host, self.token)):
            raise ValueError("CPU endpoint needs a path, host and identity")

    def as_dict(self) -> dict:
        return {"version": "sim-cpu-endpoint/1", "path": self.path,
                "host": self.host, "capacity": self.capacity, "token": self.token}

    @classmethod
    def from_dict(cls, image: dict) -> CpuPoolEndpoint:
        if type(image) is not dict or set(image) != {"version", "path", "host", "capacity", "token"} \
                or image["version"] != "sim-cpu-endpoint/1":
            raise ValueError("invalid CPU endpoint record")
        return cls(**{key: image[key] for key in ("path", "host", "capacity", "token")})


@dataclass(eq=False)
class _Client:
    sock: socket.socket
    data: bytearray = field(default_factory=bytearray)
    phase: str = "hello"
    workers: int = 0
    since: float = field(default_factory=time.monotonic)


class CpuPool:
    """One allocation's FIFO, all-or-nothing permit broker; no ambient global pool.

    ``capacity`` is an orchestrator reservation, not the machine's core count.
    Close only after joining callers and their descendants. A new owner must not
    replace a quarantined pool until those descendants have been fenced/cleaned.
    """

    def __init__(self, capacity: int):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("CPU capacity must be a positive exact integer")
        self._pid = os.getpid()
        # No inherited finalizer may delete the coordinator's socket in a child.
        self._directory = Path(tempfile.mkdtemp(prefix="behave-cpu-", dir=scratch_root()))
        path = str(self._directory / "control.sock")
        self.endpoint = CpuPoolEndpoint(path, socket.gethostname(), capacity, secrets.token_hex(16))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._clients: dict[socket.socket, _Client] = {}
        self._queue: deque[_Client] = deque()
        self._used = 0
        self._peak = 0
        self._quarantined = False
        self._selector = selectors.DefaultSelector()
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(path)
        self._listener.listen(defaults.SIM_CPU_ADMISSION_MAX_CLIENTS)
        self._listener.setblocking(False)
        self._selector.register(self._listener, selectors.EVENT_READ, None)
        if hasattr(os, "register_at_fork"):
            owner = weakref.ref(self)

            def after_fork():
                pool = owner()
                if pool is not None:
                    pool._close_inherited_descriptors()

            os.register_at_fork(after_in_child=after_fork)
        self._thread = threading.Thread(target=self._serve, name="behave-cpu-admission", daemon=True)
        self._thread.start()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_):
        self.close()

    def _owner(self):
        if os.getpid() != self._pid:
            raise RuntimeError("CPU pool belongs to its coordinator; pass its endpoint, not the owner")

    def snapshot(self) -> dict:
        self._owner()
        with self._lock:
            return {"capacity": self.endpoint.capacity, "reserved": self._used,
                    "peak_reserved": self._peak, "queued": len(self._queue),
                    "quarantined": self._quarantined, "closed": self._stop.is_set()}

    def close(self):
        self._owner()
        self._stop.set()
        self._thread.join()
        (self._directory / "control.sock").unlink(missing_ok=True)
        if self._directory.exists():
            self._directory.rmdir()

    def _close_inherited_descriptors(self):
        # Never acquire a lock copied from another thread during fork. Closing
        # the child's descriptor copies does not unregister the parent's peers.
        for client in self._clients.values():
            client.sock.close()
        self._listener.close()
        self._selector.close()

    def _drop(self, client: _Client):
        self._selector.unregister(client.sock)
        self._clients.pop(client.sock)
        client.sock.close()
        if client.phase == "queued":
            self._queue.remove(client)
        elif client.phase in {"offered", "active"}:
            self._used -= client.workers
        if client.phase == "active":
            self._quarantined = True

    def _send(self, client: _Client, value: dict):
        # Tiny, bounded, nonblocking messages. An unresponsive control peer must
        # never block the coordinator or hold its global state lock indefinitely.
        try:
            client.sock.sendall(_encode(value))
        except OSError:
            self._drop(client)

    def _message(self, client: _Client, value: dict):
        if client.phase == "hello":
            if set(value) != {"token", "workers"} or value["token"] != self.endpoint.token \
                    or type(value["workers"]) is not int or not 1 <= value["workers"] <= self.endpoint.capacity:
                raise ValueError("invalid permit request")
            client.workers = value["workers"]
            client.phase = "queued"
            self._queue.append(client)
        elif value == {"op": "release"} and client.phase in {"offered", "active"}:
            self._used -= client.workers
            client.phase = "released"
            self._send(client, {"status": "released"})
        elif value == {"op": "start"} and client.phase == "offered":
            # The client cannot start work before the acknowledgement. A lost
            # active connection is conservative even if the ACK itself was lost.
            client.phase = "active"
            self._send(client, {"status": "go"})
        else:
            raise ValueError("invalid permit transition")

    def _dispatch(self):
        while self._queue:
            client = self._queue[0]
            if self._quarantined:
                self._queue.popleft()
                client.phase = "refused"
                self._send(client, {"status": "quarantined"})
            elif self._used + client.workers <= self.endpoint.capacity:
                self._queue.popleft()
                self._used += client.workers
                self._peak = max(self._peak, self._used)
                client.phase = "offered"
                client.since = time.monotonic()
                self._send(client, {"status": "offer"})
            else:
                break  # Strict FIFO; smaller newcomers cannot starve this wave.

    def _serve(self):
        try:
            while not self._stop.is_set():
                events = self._selector.select(defaults.SIM_CPU_ADMISSION_POLL_SECONDS)
                with self._lock:
                    for key, _ in events:
                        client = key.data
                        if client is None:
                            sock, _ = self._listener.accept()
                            sock.setblocking(False)
                            if len(self._clients) >= defaults.SIM_CPU_ADMISSION_MAX_CLIENTS:
                                sock.close()
                                continue
                            client = _Client(sock)
                            self._clients[sock] = client
                            self._selector.register(sock, selectors.EVENT_READ, client)
                            continue
                        try:
                            block = client.sock.recv(_WIRE_LIMIT + 1)
                            if not block:
                                self._drop(client)
                                continue
                            client.data.extend(block)
                            if len(client.data) > _WIRE_LIMIT:
                                raise ValueError("oversized control message")
                            while b"\n" in client.data:
                                line, rest = client.data.split(b"\n", 1)
                                client.data = bytearray(rest)
                                value = json.loads(line)
                                if type(value) is not dict:
                                    raise ValueError("control message must be an object")
                                self._message(client, value)
                                if client.sock not in self._clients:
                                    break
                        except (OSError, ValueError, TypeError):
                            if client.sock in self._clients:
                                self._drop(client)
                    for client in tuple(self._clients.values()):
                        if client.phase in {"hello", "offered", "released", "refused"} \
                                and time.monotonic() - client.since > defaults.SIM_CPU_ADMISSION_CONTROL_SECONDS:
                            self._drop(client)
                    self._dispatch()
        finally:
            with self._lock:
                self._quarantined = True
                for client in tuple(self._clients.values()):
                    self._drop(client)
            self._listener.close()
            self._selector.close()


class CpuUsage:
    """Queue receipts, separate from candidate evidence and solver outcomes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._receipts: list[dict] = []

    def _add(self, receipt: dict):
        with self._lock:
            self._receipts.append(dict(receipt))

    def as_dict(self) -> dict:
        with self._lock:
            return {"version": "sim-cpu-usage/1", "reservations": [dict(x) for x in self._receipts]}


@dataclass(frozen=True)
class _Scope:
    endpoint: CpuPoolEndpoint
    deadline: float | None
    cancelled: Callable[[], bool] | None
    usage: CpuUsage
    pid: int = field(default_factory=os.getpid)

    def check(self):
        if self.pid != os.getpid():
            raise _failure("cpu-scope-inherited", "pass the endpoint to a fresh worker scope; do not fork an active scope")
        check_execution()
        if self.cancelled is not None and self.cancelled():
            raise _failure("cpu-wait-cancelled", "CPU admission was cancelled by its caller")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise _failure("cpu-wait-deadline", "the caller deadline expired while waiting for CPUs")


@contextmanager
def cpu_scope(endpoint: CpuPoolEndpoint, *, deadline: float | None = None,
              cancelled: Callable[[], bool] | None = None):
    """Attach a shared endpoint, absolute monotonic deadline and cancellation.

    This does not reserve CPUs yet. Wrap a whole public Sim call. Across trusted
    processes pass ``endpoint.as_dict()`` explicitly and reconstruct it there;
    a sanitized worker environment is deliberately not an implicit transport.
    """
    if type(endpoint) is not CpuPoolEndpoint:
        raise TypeError("CPU scope requires an exact CpuPoolEndpoint")
    if endpoint.host != socket.gethostname():
        raise ValueError("CPU endpoints are local to the coordinator's host")
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise ValueError("CPU deadline must be an absolute finite monotonic time")
    if cancelled is not None and not callable(cancelled):
        raise TypeError("CPU cancellation must be callable")
    if _SCOPE.get() is not None or _HELD.get():
        raise ValueError("CPU scopes must not replace an enclosing resource scope")
    usage = CpuUsage()
    token = _SCOPE.set(_Scope(endpoint, deadline, cancelled, usage))
    try:
        yield usage
    finally:
        _SCOPE.reset(token)


def cpu_capacity() -> int | None:
    scope = _SCOPE.get()
    return None if scope is None else scope.endpoint.capacity


def cpu_work_width(requested: int) -> int:
    """Size independent work without acquiring or duplicating a parent permit."""
    if _HELD.get():
        check_cpu_work()
        return 1
    return min(job_cpu_limit(requested), cpu_capacity() or requested)


def cpu_usage() -> dict | None:
    scope = _SCOPE.get()
    return None if scope is None else scope.usage.as_dict()


def check_cpu_work():
    permit = _HELD.get()
    if permit and not permit.cleanup_confirmed:
        raise _failure("cpu-cleanup-unconfirmed", "worker cleanup could not be confirmed; no further work may start")
    scope = _SCOPE.get()
    if scope is not None:
        scope.check()


def cpu_cleanup_unconfirmed():
    """A failed worker join must not turn socket close into a permit release."""
    from .scoring_policy import active_scoring

    scoring = active_scoring()
    if scoring is not None:
        scoring.cleanup_confirmed = False
    permit = _HELD.get()
    if permit:
        permit.cleanup_confirmed = False


@contextmanager
def reserve_cpus(workers: int):
    """Yield the admitted map width; a nested map always runs serially."""
    if _HELD.get():
        if _HELD.get().pid != os.getpid():
            raise _failure("cpu-scope-inherited", "a CPU permit cannot be inherited by a forked worker")
        check_cpu_work()
        yield 1
        return
    scope = _SCOPE.get()
    permit = _Permit()
    if scope is None:
        # Standalone calls retain their existing budget. Mark the wave held so
        # nested maps cannot multiply even a standalone thread budget.
        token = _HELD.set(permit)
        try:
            yield job_cpu_limit(workers)
            if not permit.cleanup_confirmed:
                raise _failure("cpu-cleanup-unconfirmed", "worker cleanup could not be confirmed")
        finally:
            _HELD.reset(token)
        return
    if workers > scope.endpoint.capacity:
        raise _failure("cpu-request-too-large", "replay width exceeds the shared CPU reservation")
    started = time.monotonic()
    receipt = {"workers": workers, "capacity": scope.endpoint.capacity, "status": "waiting"}
    offered = unwinding = running = False
    data = bytearray()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    _CLIENT_SOCKETS.add(sock)
    sock.settimeout(defaults.SIM_CPU_ADMISSION_POLL_SECONDS)
    try:
        scope.check()
        sock.connect(scope.endpoint.path)
        sock.sendall(_encode({"token": scope.endpoint.token, "workers": workers}))
        reply = _read(sock, scope.check, data)
        if reply == {"status": "quarantined"}:
            raise _failure("cpu-pool-quarantined", "an active client lost its cleanup receipt")
        if reply != {"status": "offer"}:
            raise ValueError("invalid CPU admission reply")
        offered = True
        scope.check()
        start_deadline = time.monotonic() + defaults.SIM_CPU_ADMISSION_CONTROL_SECONDS

        def start_check():
            scope.check()
            if time.monotonic() >= start_deadline:
                raise OSError("CPU start acknowledgement timed out")

        sock.sendall(_encode({"op": "start"}))
        if _read(sock, start_check, data) != {"status": "go"}:
            raise ValueError("invalid CPU start acknowledgement")
        receipt["wait_seconds"] = time.monotonic() - started
        receipt["status"] = "admitted"
        scope.check()
        token = _HELD.set(permit)
        try:
            running = True
            yield workers
            if not permit.cleanup_confirmed:
                raise _failure("cpu-cleanup-unconfirmed", "worker cleanup could not be confirmed")
        finally:
            _HELD.reset(token)
    except (OSError, ValueError) as exc:
        unwinding = True
        if running:
            receipt["status"] = "work-interrupted"
            raise  # A callback error is not a CPU admission error.
        receipt["status"] = "cpu-admission-unavailable"
        raise _failure(receipt["status"], str(exc)) from exc
    except BaseException as exc:
        unwinding = True
        receipt["status"] = (exc.failure.kind if isinstance(exc, FailureRaised) else
                             exc.reason if isinstance(exc, SearchStopped) else "work-interrupted")
        raise
    finally:
        receipt.setdefault("wait_seconds", time.monotonic() - started)
        try:
            if offered and not permit.cleanup_confirmed:
                receipt["released"] = False
                receipt["status"] = "cpu-cleanup-unconfirmed"
                # Close the active control channel without a release. The broker
                # will quarantine instead of scheduling over unjoined workers.
            elif offered:
                until = time.monotonic() + defaults.SIM_CPU_ADMISSION_CONTROL_SECONDS

                def cleanup_check():
                    if time.monotonic() >= until:
                        raise OSError("CPU release acknowledgement timed out")

                # Run only after the map has joined all of its owned workers.
                # If cancellation raced the start ACK, consume that ACK first.
                sock.sendall(_encode({"op": "release"}))
                reply = _read(sock, cleanup_check, data)
                if reply == {"status": "go"}:
                    reply = _read(sock, cleanup_check, data)
                if reply != {"status": "released"}:
                    raise ValueError("invalid CPU release acknowledgement")
                receipt["released"] = True
        except (OSError, ValueError) as exc:
            receipt["released"] = False
            if not unwinding:
                raise _failure("cpu-release-unconfirmed", str(exc)) from exc
        finally:
            sock.close()
            _CLIENT_SOCKETS.discard(sock)
            scope.usage._add(receipt)
