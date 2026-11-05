"""Private trusted-worker records with interruptible parent I/O.

Both peers are Sim-owned processes. Pickle is solely their transient record
carrier; simulator JSON is carried as bytes, never interpreted as pickle.
Control is checked throughout partial sends, receives and record reconstruction,
not only before a blocking whole-message operation. Child I/O can block; its
parent owns cleanup.
"""

from __future__ import annotations

import pickle
import socket
import struct
from collections.abc import Callable
from multiprocessing.reduction import ForkingPickler

from behave.core import runtime_defaults as defaults

_SIZE = struct.Struct("!Q")


class _CheckedReader:
    """Check standard pickle frame reads without copying the entire payload."""

    def __init__(self, payload, check):
        self.payload, self.check = payload, check
        self.position = 0

    def read(self, size=-1):
        self.check()
        end = len(self.payload) if size < 0 else min(len(self.payload), self.position + size)
        result = bytes(memoryview(self.payload)[self.position:end])
        self.position = end
        return result

    def readline(self, size=-1):
        end = len(self.payload) if size < 0 else min(len(self.payload), self.position + size)
        newline = self.payload.find(b"\n", self.position, end)
        return self.read((newline + 1 if newline >= 0 else end) - self.position)


class TraceChannel:
    def __init__(self, sock: socket.socket, check: Callable[[], None] | None = None):
        self.socket, self.check = sock, check
        sock.settimeout(defaults.SIM_CPU_ADMISSION_POLL_SECONDS if check else None)

    def send(self, value):
        payload = memoryview(ForkingPickler.dumps(value))
        for part in (memoryview(_SIZE.pack(len(payload))), payload):
            while part:
                if self.check is not None:
                    self.check()
                try:
                    sent = self.socket.send(part)
                except TimeoutError:
                    continue
                if sent == 0:
                    raise EOFError("trace worker channel closed during send")
                part = part[sent:]

    def _receive(self, size):
        data = bytearray(size)
        remaining = memoryview(data)
        while remaining:
            if self.check is not None:
                self.check()
            try:
                count = self.socket.recv_into(remaining)
            except TimeoutError:
                continue
            if count == 0:
                raise EOFError("trace worker channel closed during receive")
            remaining = remaining[count:]
        return data

    def recv(self):
        size, = _SIZE.unpack(self._receive(_SIZE.size))
        payload = self._receive(size)
        if self.check is None:
            return ForkingPickler.loads(payload)
        # Receiving the bytes is not the end of the work: reconstructing a long
        # trace can otherwise keep the coordinator busy past its work deadline.
        # Use the same standard unpickler, with checks as it reads each frame.
        result = pickle.Unpickler(_CheckedReader(payload, self.check)).load()
        self.check()
        return result

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
