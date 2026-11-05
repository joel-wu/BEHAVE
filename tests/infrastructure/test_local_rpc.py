"""Shared relay budgets and lifecycle; native socket tests run on the node lane."""

import struct
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from behave.agent import local_rpc
from behave.core import runtime_defaults as defaults
from behave.core.lifecycle import CleanupState


def relay(**changes):
    values = {"name": "model-relay", "deadline": time.monotonic() + 20}
    return local_rpc.LocalRPCServer(**(values | changes))


def test_attempt_relay_needs_no_total_deadline():
    assert relay(deadline=None).deadline is None


@pytest.mark.isolation
@pytest.mark.parametrize("name", ["model-relay", "development-relay", "cpu-relay"])
def test_nested_execution_scratch_does_not_lengthen_socket(tmp_path, monkeypatch, name):
    execution = tmp_path / ("nested-" * 20)
    execution.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(execution))
    monkeypatch.setenv("TMPDIR", str(execution))
    with relay(name=name, deadline=None) as owner:
        endpoint = Path(owner.endpoint)
        assert len(str(endpoint).encode()) < 108
        assert endpoint.parent.parent == Path("/tmp")
        assert endpoint.is_socket()
    assert not endpoint.parent.exists()


def test_exchange_without_deadline_preserves_unbounded_delivery(monkeypatch):
    timeouts, deadlines = [], []
    stream = SimpleNamespace(settimeout=timeouts.append, connect=lambda _: None,
                             setblocking=lambda _: None)
    monkeypatch.setattr(local_rpc.socket, "socket", lambda *a: nullcontext(stream))
    monkeypatch.setattr(local_rpc, "send_frame", lambda *a, **kw: deadlines.append(kw["deadline"]))

    def receive(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        return b"complete response"

    monkeypatch.setattr(local_rpc, "receive_frame", receive)
    assert local_rpc.exchange("fixture", b"request", deadline=None) == b"complete response"
    assert timeouts == [None]
    assert deadlines == [None, None]


@pytest.mark.parametrize("deadline", ["1024", float("nan"), float("inf")])
def test_deadline_must_still_be_finite(deadline):
    with pytest.raises(ValueError, match="finite deadline"):
        relay(deadline=deadline)


@pytest.mark.parametrize("cancelled,remaining,error", [
    (True, 10, InterruptedError), (False, 0, TimeoutError), (False, -10, TimeoutError),
])
def test_stopped_exchange_never_opens_a_socket(monkeypatch, cancelled, remaining, error):
    monkeypatch.setattr(local_rpc.time, "monotonic", lambda: 100.)
    monkeypatch.setattr(local_rpc.socket, "socket", lambda *a: pytest.fail("must not open a socket"))
    with pytest.raises(error):
        local_rpc.exchange("unused", b"request", deadline=100 + remaining,
                           cancelled=lambda: cancelled)


@pytest.mark.parametrize("remaining", [10., .5, .00001])
def test_connect_uses_shared_setting_without_extending_the_deadline(monkeypatch, remaining):
    timeouts, frames, connections = [], [], []
    stream = SimpleNamespace(settimeout=timeouts.append, connect=connections.append, setblocking=lambda _: None)
    monkeypatch.setattr(local_rpc.socket, "socket", lambda *a: nullcontext(stream))
    monkeypatch.setattr(local_rpc.time, "monotonic", lambda: 100.)
    monkeypatch.setattr(local_rpc, "send_frame", lambda *a, **kw: frames.append(kw["deadline"]))

    def receive(*args, **kwargs):
        frames.append(kwargs["deadline"])
        return b"reply"

    monkeypatch.setattr(local_rpc, "receive_frame", receive)
    deadline = 100 + remaining
    assert local_rpc.exchange("fixture", b"request", deadline=deadline) == b"reply"
    assert connections == ["fixture"]
    assert timeouts == pytest.approx([remaining])
    assert frames == [deadline, deadline]


@pytest.mark.parametrize("writing", [False, True])
@pytest.mark.parametrize("remaining", [1., .01])
def test_read_and_write_poll_share_the_setting_and_remaining_time(monkeypatch, writing, remaining):
    waited, registered = [], []
    monkeypatch.setattr(local_rpc.time, "monotonic", lambda: 100.)
    monkeypatch.setattr(defaults, "LOCAL_RPC_POLL_SECONDS", .25)

    def poll(timeout):
        waited.append(timeout)
        return [(2048, local_rpc.select.POLLOUT if writing else local_rpc.select.POLLIN)]

    monkeypatch.setattr(local_rpc.select, "poll", lambda: SimpleNamespace(
        register=lambda *args: registered.append(args), poll=poll))
    stream = object()
    local_rpc._ready(stream, 100 + remaining, lambda: False, writing=writing)
    assert registered == [(stream, local_rpc.select.POLLOUT if writing else local_rpc.select.POLLIN)]
    # poll uses integer milliseconds and must not turn a small remainder into a
    # busy zero-timeout loop.
    assert waited == [local_rpc.math.ceil(1000 * min(.25, (100 + remaining) - 100))]


@pytest.mark.isolation
def test_valid_high_numbered_sockets_support_both_frame_directions():
    import fcntl
    import resource
    import socket

    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 2048:
        pytest.skip("test requires an allowed descriptor above FD_SETSIZE")
    left, right = socket.socketpair()
    with left, right:
        fd = fcntl.fcntl(left.fileno(), fcntl.F_DUPFD_CLOEXEC, 2048)
        with socket.socket(fileno=fd) as high:
            high.setblocking(False)
            right.setblocking(False)
            deadline = time.monotonic() + 5
            local_rpc.send_frame(right, b"request", deadline=deadline)
            assert local_rpc.receive_frame(high, deadline=deadline) == b"request"
            local_rpc.send_frame(high, b"reply", deadline=deadline)
            assert local_rpc.receive_frame(right, deadline=deadline) == b"reply"


def test_frame_reads_the_whole_declared_body(monkeypatch):
    requested = []
    body = b"x" * 1025

    def recv(size):
        requested.append(size)
        return struct.pack("!I", len(body)) if len(requested) == 1 else body

    monkeypatch.setattr(local_rpc, "_ready", lambda *a, **kw: None)
    assert local_rpc.receive_frame(SimpleNamespace(recv=recv), deadline=time.monotonic() + 10) == body
    assert requested == [4, 1025]


@pytest.mark.parametrize("deadline", [None, 102., 120.])
def test_server_frames_share_transport_setting_but_not_a_fresh_execution_budget(monkeypatch, deadline):
    owner = relay(deadline=deadline)
    frames = []
    stream = MagicMock(spec=local_rpc.socket.socket)
    owner._listener = SimpleNamespace(accept=lambda: (stream, None))
    monkeypatch.setattr(local_rpc.time, "monotonic", lambda: 100.)

    def receive(stream, **kwargs):
        frames.append(("read", kwargs["deadline"]))
        return b"request"

    def respond(data, **kwargs):
        assert data == b"request" and owner.deadline == deadline
        return b"reply"

    def send(stream, data, **kwargs):
        assert data == b"reply"
        frames.append(("write", kwargs["deadline"]))
        owner._stop.set()

    monkeypatch.setattr(local_rpc, "receive_frame", receive)
    monkeypatch.setattr(owner, "respond", respond)
    monkeypatch.setattr(local_rpc, "send_frame", send)
    owner._serve()
    assert owner._error is None
    assert frames == [("read", deadline), ("write", deadline)]


@pytest.mark.parametrize("startup_failure", [False, True])
def test_listener_and_both_cleanup_paths_use_shared_settings(tmp_path, monkeypatch, startup_failure):
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr(defaults, "LOCAL_RPC_POLL_SECONDS", .2)
    monkeypatch.setattr(defaults, "LOCAL_RPC_CLEANUP_SECONDS", 3.)
    polls, joins = [], []
    listener = SimpleNamespace(bind=lambda path: Path(path).touch(), listen=lambda _: None,
                               settimeout=polls.append, close=lambda: None)

    def start():
        if startup_failure:
            raise RuntimeError("fixture-start-failed")

    thread = SimpleNamespace(ident=1, start=start, join=lambda **kw: joins.append(kw["timeout"]),
                             is_alive=lambda: False)
    monkeypatch.setattr(local_rpc.socket, "socket", lambda *a: listener)
    monkeypatch.setattr(local_rpc.threading, "Thread", lambda **kw: thread)
    owner = relay()
    if startup_failure:
        with pytest.raises(RuntimeError, match="fixture-start-failed"):
            owner.__enter__()
    else:
        with owner:
            assert Path(owner.endpoint).exists()
    assert polls == [.2] and joins == [3.]
    assert owner.cleanup_state is CleanupState.COMPLETE
    assert not Path(owner.endpoint).parent.exists()
