"""Real CPU-only service/probe cancellation; no model or GPU acceptance claim."""

import socket
import sys
import time
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from test_serving import spec

from behave.serving import ServingFailure, ServingSession, ServingSpec, start_sessions


def wait_cancelled(cancelled):
    # Test watchdog only; production serving retains its saved startup budget.
    until = time.monotonic() + 3
    while not cancelled():
        if time.monotonic() >= until:
            pytest.fail("a failed peer did not cancel pending startup")
        Event().wait(.005)


def test_serving_starts_configured_engines_together():
    barrier = Barrier(2, timeout=3)
    released = Event()

    class Session:
        def __init__(self, index):
            self.index = index

        def start(self, *, cancelled):
            assert not cancelled()
            barrier.wait()
            if self.index == 0:
                assert released.wait(3)
            else:
                released.set()
            return self.index

    assert start_sessions([Session(0), Session(1)]) == (0, 1)


@pytest.mark.parametrize("failure_index", [0, 1])
def test_failed_serving_start_cancels_other_startup(failure_index):
    barrier, settled = Barrier(2, timeout=3), Event()
    original = RuntimeError("startup failed")

    class Failed:
        def start(self, *, cancelled):
            barrier.wait()
            raise original

    class Waiting:
        def start(self, *, cancelled):
            barrier.wait()
            wait_cancelled(cancelled)
            settled.set()
            raise ServingFailure("startup-cancelled")

    sessions = [Waiting(), Waiting()]
    sessions[failure_index] = Failed()
    with pytest.raises(RuntimeError, match="startup failed") as caught:
        start_sessions(sessions)
    assert caught.value is original and settled.is_set()


def test_caller_cancellation_reaches_every_startup():
    barrier, stopped, settled = Barrier(2, timeout=3), Event(), []

    class Session:
        def start(self, *, cancelled):
            barrier.wait()
            stopped.set()
            wait_cancelled(cancelled)
            settled.append(self)
            raise ServingFailure("startup-cancelled")

    sessions = [Session(), Session()]
    with pytest.raises(ServingFailure, match="startup-cancelled"):
        start_sessions(sessions, cancelled=stopped.is_set)
    assert set(settled) == set(sessions)


def test_coordinator_interruption_cancels_and_joins_startup(monkeypatch):
    from behave.serving import lifecycle

    entered, settled = Event(), Event()

    class Session:
        def start(self, *, cancelled):
            entered.set()
            wait_cancelled(cancelled)
            settled.set()
            raise ServingFailure("startup-cancelled")

    def interrupted(_):
        assert entered.wait(3)
        raise KeyboardInterrupt("coordinator interrupted")

    monkeypatch.setattr(lifecycle, "as_completed", interrupted)
    with pytest.raises(KeyboardInterrupt, match="coordinator interrupted"):
        start_sessions([Session()])
    assert settled.is_set()


def test_external_stop_after_ready_does_not_admit_an_evaluator():
    stopped = Event()

    def start(*, cancelled):
        stopped.set()
        return "ready-before-stop"

    with pytest.raises(ServingFailure, match="startup-cancelled"):
        start_sessions([SimpleNamespace(start=start)], cancelled=stopped.is_set)


@pytest.mark.parametrize("duplicate", [False, True])
def test_invalid_groups_do_not_start_any_engine(duplicate):
    session = SimpleNamespace(start=lambda **_: pytest.fail("invalid group must not launch"))
    with pytest.raises(ValueError, match="nonempty group of distinct sessions"):
        start_sessions([session, session] if duplicate else [])


def test_real_sessions_record_quiescent_cleanup_after_peer_probe_failure(tmp_path, monkeypatch):
    from behave.isolation.service import ServiceResult
    from behave.serving import session as control

    barrier, active = Barrier(2, timeout=3), set()
    monkeypatch.setattr(control, "check_port", lambda _: None)

    def serve(argv, **kwargs):
        active.add(kwargs["cwd"])
        try:
            wait_cancelled(kwargs["cancelled"])
            return ServiceResult("cancelled", -9, b"", b"", .01)
        finally:
            active.remove(kwargs["cwd"])

    def probe(selected, *, cancelled, **kwargs):
        barrier.wait()
        if selected.port == 31001:
            raise ServingFailure("fixture-probe-failed")
        wait_cancelled(cancelled)

    monkeypatch.setattr(control, "run_service", serve)
    monkeypatch.setattr(control, "probe", probe)
    sessions = [ServingSession(spec(port=31000 + index, startup_seconds=None,
        lifetime_seconds=None, cleanup_seconds=None), scratch=tmp_path / str(index),
        environment={}, records=tmp_path / str(index) / "records") for index in range(2)]
    with pytest.raises(ServingFailure, match="fixture-probe-failed"):
        start_sessions(sessions)
    assert not active
    receipts = [session.close() for session in sessions]
    assert all(row["cleanup_complete"] and not row["ready"] for row in receipts)
    assert {row["failure"] for row in receipts} == {"fixture-probe-failed", "startup-cancelled"}


@pytest.mark.isolation
def test_uncapped_startup_cancels_blocked_http_probe_and_server(tmp_path, monkeypatch):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    request_started = tmp_path / "request-started"
    program = """
import http.server, pathlib, sys, threading
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        pathlib.Path(sys.argv[2]).write_text('request received')
        threading.Event().wait()
http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
"""
    monkeypatch.setattr(ServingSpec, "command", lambda self:
        (sys.executable, "-I", "-B", "-u", "-c", program, str(port), str(request_started)))
    session = ServingSession(spec(port=port, startup_seconds=None, lifetime_seconds=None,
        cleanup_seconds=None, log_bytes=None), scratch=tmp_path, environment={}, records=tmp_path / "service")
    with pytest.raises(ServingFailure, match="startup-cancelled"):
        session.start(cancelled=request_started.exists)
    assert request_started.is_file()
    stopped = session.close()
    assert stopped["cleanup_complete"] and not stopped["ready"]
    assert stopped["service_status"] == "cancelled"
    # A completed receipt must correspond to the actual listener disappearing.
    with socket.socket() as released:
        released.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        released.bind(("127.0.0.1", port))
