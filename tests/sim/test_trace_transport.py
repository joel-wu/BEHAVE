"""Backpressure and partial frames must not hide parent cancellation."""

import pickle
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.reduction import ForkingPickler

import pytest

from behave.sim.rtl.trace_transport import TraceChannel, _CheckedReader


class Cancelled(Exception):
    pass


def stop_after(checks):
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls >= checks:
            raise Cancelled

    return check


@pytest.mark.parametrize("payload", [None, ("record", [True, 1], {}), b"x" * (2 << 20)],
                         ids=["none", "structured", "two-mib"])
def test_complete_records_cross_partial_reads_and_writes(payload):
    parent, child = socket.socketpair()
    parent.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    child.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    with (
        TraceChannel(parent, lambda: None) as tx,
        TraceChannel(child) as rx,
        ThreadPoolExecutor(max_workers=1) as workers,
    ):
        future = workers.submit(lambda: rx.send(rx.recv()))
        tx.send(payload)
        assert tx.recv() == payload
        future.result(timeout=10)


def test_nonreading_peer_does_not_hide_send_cancellation():
    parent, child = socket.socketpair()
    parent.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    # The peer never reads: a whole-message send would wait indefinitely.
    with child, TraceChannel(parent, stop_after(4)) as channel, pytest.raises(Cancelled):
        channel.send(b"x" * (2 << 20))


@pytest.mark.parametrize("prefix", [b"\0", struct.pack("!Q", 100)+b"partial"])
def test_partial_header_or_body_does_not_hide_receive_cancellation(prefix):
    parent, child = socket.socketpair()
    with child, TraceChannel(parent, stop_after(4)) as channel:
        child.sendall(prefix)
        with pytest.raises(Cancelled):
            channel.recv()


def test_truncated_record_is_eof_not_a_result():
    parent, child = socket.socketpair()
    child.sendall(struct.pack("!Q", 100)+b"partial")
    child.close()
    with TraceChannel(parent, lambda: None) as channel, pytest.raises(EOFError):
        channel.recv()


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_checked_reader_preserves_standard_pickle_values(protocol):
    from behave.sim.core.record_value import FrozenValueMap

    value = FrozenValueMap((("state", (True, 1, "a\nb")), ("empty", None)))
    payload = bytearray(pickle.dumps(value, protocol=protocol))
    reader = _CheckedReader(payload, lambda: None)
    assert reader.payload is payload  # no additional whole-trace buffer
    got = pickle.Unpickler(reader).load()
    assert got.pairs() == value.pairs()
    assert type(got["state"][0]) is bool and type(got["state"][1]) is int
    with pytest.raises(AttributeError):
        got._pairs = ()


_RESTORED = []


def _restore_item(value):
    _RESTORED.append(value)
    return value


class _Item:
    def __init__(self, value):
        self.value = value

    def __reduce__(self):
        return _restore_item, (self.value,)


@pytest.mark.parametrize("count", [1, 20_000], ids=["final-check", "mid-reconstruction"])
def test_complete_payload_does_not_hide_reconstruction_cancellation(monkeypatch, count):
    # All bytes have arrived before cancellation. A socket-only check would
    # reconstruct the entire object graph and return it despite the deadline.
    payload = bytes(ForkingPickler.dumps([_Item(i) for i in range(count)]))
    packets = iter((struct.pack("!Q", len(payload)), payload))
    _RESTORED.clear()

    def check():
        if _RESTORED:
            raise Cancelled

    parent, child = socket.socketpair()
    with child, TraceChannel(parent, check) as channel:
        monkeypatch.setattr(channel, "_receive", lambda _: next(packets))
        with pytest.raises(Cancelled):
            channel.recv()
    assert 0 < len(_RESTORED) <= count
    if count > 1:
        assert len(_RESTORED) < count
    _RESTORED.clear()
