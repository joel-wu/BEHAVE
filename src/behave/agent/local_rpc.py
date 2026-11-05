"""Bounded same-host RPC for trusted workers; no candidate-visible capability.

Transport owns framing, disconnect detection and listener lifetime. A service
owns request validation, admission and durable settlement. No payload chooses
an executable, Python callable or host endpoint.

Trusted services may use None for request/response budgets to avoid extra caps
on internal records. Explicit caller budgets, deadlines, cancellation and the
framing format are preserved.
"""

from __future__ import annotations

import math
import os
import select
import socket
import struct
import threading
import time
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.lifecycle import CleanupState

from .attempt import AttemptInterrupted


def _ready(stream, deadline, cancelled, *, writing):
    # A concurrent batch can own descriptors above FD_SETSIZE. select() rejects
    # those valid sockets; poll() has no descriptor-number ceiling.
    poller = select.poll()
    poller.register(stream, select.POLLOUT if writing else select.POLLIN)
    while True:
        if cancelled():
            raise InterruptedError("relay cancelled")
        remaining = math.inf if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("relay deadline")
        ready = poller.poll(math.ceil(1000 * min(defaults.LOCAL_RPC_POLL_SECONDS, remaining)))
        if ready:
            return


def send_frame(stream: socket.socket, data: bytes, *, deadline: float | None, cancelled=lambda: False):
    view = memoryview(struct.pack("!I", len(data)) + data)
    while view:
        _ready(stream, deadline, cancelled, writing=True)
        try:
            count = stream.send(view)
        except BlockingIOError:
            continue
        if count == 0:
            raise ConnectionError("relay closed")
        view = view[count:]


def receive_frame(stream: socket.socket, *, deadline: float | None, cancelled=lambda: False) -> bytes:
    def read(size):
        body = bytearray()
        while len(body) < size:
            _ready(stream, deadline, cancelled, writing=False)
            try:
                data = stream.recv(min(65536, size - len(body)))
            except BlockingIOError:
                continue
            if not data:
                raise ConnectionError("relay closed")
            body.extend(data)
        return bytes(body)
    size, = struct.unpack("!I", read(4))
    return read(size)


def peer_closed(stream: socket.socket) -> bool:
    try:
        stream.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        return True  # EOF or extra bytes: one connection carries exactly one RPC.
    except BlockingIOError:
        return False
    except OSError:
        return True


def exchange(endpoint: str, data: bytes, *, deadline: float | None,
             cancelled=lambda: False) -> bytes:
    if cancelled():
        raise InterruptedError("relay cancelled")
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        raise TimeoutError("relay deadline")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(remaining)
        stream.connect(endpoint)
        stream.setblocking(False)
        send_frame(stream, data, deadline=deadline, cancelled=cancelled)
        return receive_frame(stream, deadline=deadline, cancelled=cancelled)


class LocalRPCServer:
    """One sequential ingress, serviced by its owning coordinator thread."""

    def __init__(self, *, name: str, deadline: float | None,
                 cancelled=lambda: False):
        if name not in {"model-relay", "development-relay", "cpu-relay"}:
            raise ValueError("unknown local service")
        if (deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline))
                or not callable(cancelled)):
            raise ValueError("relay requires a finite deadline or None and a cancellation predicate")
        self.name, self.deadline, self.cancelled = name, deadline, cancelled
        self._stop = threading.Event()
        self._thread = self._error = self._scratch = None
        self.cleanup_state = CleanupState.COMPLETE
        self.child_cleanup = CleanupState.COMPLETE

    def __enter__(self):
        from behave.core.paths import local_rpc_root
        from behave.isolation.scratch import ScratchDirectory

        if self._scratch is not None:
            raise RuntimeError("a relay cannot be restarted")
        self._scratch = ScratchDirectory(prefix=f"behave-{self.name}-", cleanup=lambda: self.cleanup_state,
                                         parent=local_rpc_root())
        self.endpoint = str(Path(self._scratch.name) / "rpc.sock")
        self._listener = None
        self.cleanup_state = CleanupState.UNKNOWN
        try:
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.bind(self.endpoint)
            os.chmod(self.endpoint, 0o600)
            self._listener.listen(1)
            self._listener.settimeout(defaults.LOCAL_RPC_POLL_SECONDS)
            self._thread = threading.Thread(target=self._serve, name=f"behave-{self.name}", daemon=True)
            self._thread.start()
            return self
        except BaseException:
            self._stop.set()
            if self._listener is not None:
                self._listener.close()
            if self._thread is not None and self._thread.ident is not None:
                self._thread.join(timeout=defaults.LOCAL_RPC_CLEANUP_SECONDS)
            alive = self._thread is not None and self._thread.is_alive()
            self.cleanup_state = CleanupState.combine(
                CleanupState.INCOMPLETE if alive else CleanupState.COMPLETE, self.child_cleanup)
            self.cleanup_state.require_quiescent(self.name)
            self._scratch.cleanup()
            raise

    def __exit__(self, *_):
        self._stop.set()
        self.cleanup_state = CleanupState.UNKNOWN
        try:
            self._listener.close()
        finally:
            self._thread.join(timeout=defaults.LOCAL_RPC_CLEANUP_SECONDS)
            self.cleanup_state = CleanupState.combine(
                CleanupState.INCOMPLETE if self._thread.is_alive() else CleanupState.COMPLETE, self.child_cleanup)
            self.cleanup_state.require_quiescent(self.name)
            self._scratch.cleanup()
        if self._error is not None:
            raise AttemptInterrupted(self.name + "-" + type(self._error).__name__) from None

    def _cancelled(self):
        return self._stop.is_set() or self.cancelled()

    def respond(self, data: bytes, *, cancelled) -> bytes:
        raise NotImplementedError

    def _serve(self):
        try:
            while not self._cancelled():
                try:
                    stream, _ = self._listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        return
                    raise
                with stream:
                    stream.setblocking(False)
                    try:
                        data = receive_frame(stream,
                                             deadline=self.deadline,
                                             cancelled=self._cancelled)
                    except (OSError, ValueError):
                        continue
                    # Only transport failures may abandon a connection and keep
                    # serving. A service/ledger exception stops this owner; an
                    # uncertain durable write must not become a fresh retry.
                    reply = self.respond(data, cancelled=lambda stream=stream: self._cancelled() or peer_closed(stream))
                    try:
                        send_frame(stream, reply,
                                   deadline=self.deadline,
                                   cancelled=self._cancelled)
                    except (OSError, ValueError):
                        continue  # Abandoned delivery does not roll back durable outcomes.
        except BaseException as error:  # noqa: BLE001 -- propagate thread failure at owner boundary
            self._error = error
