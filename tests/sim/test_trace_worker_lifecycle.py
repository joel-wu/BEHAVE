"""Worker teardown retains the cause and shares one runtime cleanup window."""

import gc
import io
import subprocess
import time
import weakref
from types import SimpleNamespace

import pytest

from behave.sim.rtl import trace_postprocessing, trace_process


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class Process:
    def __init__(self, clock, events, name, *, unreapable=False):
        self.clock, self.events, self.name = clock, events, name
        self.unreapable = unreapable
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.events.append((self.name, "terminate"))

    def kill(self):
        self.events.append((self.name, "kill"))
        self.killed = True

    def wait(self, timeout):
        self.events.append((self.name, "wait", timeout))
        if not self.killed or self.unreapable:
            self.clock.now += timeout
            raise subprocess.TimeoutExpired(self.name, timeout)
        self.returncode = -9
        return self.returncode


def worker(clock, events, name, **kwargs):
    result = object.__new__(trace_process._Worker)
    result.process = Process(clock, events, name, **kwargs)
    result.connection = SimpleNamespace(close=lambda: events.append((name, "close")))
    result.errors = io.BytesIO()
    return result


@pytest.fixture
def lifetime(monkeypatch):
    clock, events = Clock(), []
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(trace_process.defaults, "SIM_JOB_CLEANUP_SECONDS", 7)
    monkeypatch.setattr(trace_process, "cpu_cleanup_unconfirmed", lambda: events.append("quarantine"))
    return clock, events


def test_finished_worker_has_no_graceful_wait_ladder(lifetime):
    clock, events = lifetime
    owned = worker(clock, events, "one")
    owned.close()
    assert events == [("one", "close"), ("one", "kill"), ("one", "wait", 7)]
    assert clock.now == 0
    assert owned.errors.closed


def test_pool_stops_every_worker_before_reaping_with_one_deadline(lifetime):
    clock, events = lifetime
    pool = trace_process.TraceWorkerPool(None, None, 2)
    pool._workers = [worker(clock, events, "stuck", unreapable=True),
                     worker(clock, events, "finished")]
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        pool.close()
    assert events.index(("finished", "kill")) < events.index(("stuck", "wait", 7))
    assert ("finished", "wait", 0) in events
    assert clock.now == 7
    assert "quarantine" in events
    assert all(item.errors.closed for item in pool._workers)


@pytest.mark.parametrize("during_startup", [False, True])
@pytest.mark.parametrize("kind", ["budget", "crash"])
def test_interrupted_lane_retains_its_cause_for_queued_replays(monkeypatch, during_startup, kind):
    from behave.sim.scoring_policy import ScoringStopped

    cause = ScoringStopped() if kind == "budget" else EOFError("original worker failure")
    starts = []

    def interrupted(*args):
        raise cause

    def create(*args):
        starts.append(True)
        if during_startup:
            raise cause
        return SimpleNamespace(check=interrupted)

    monkeypatch.setattr(trace_process, "_Worker", create)
    pool = trace_process.TraceWorkerPool(None, None, 1)
    for _ in range(3):
        with pytest.raises(type(cause)) as caught:
            pool.check(None, None, None, 0)
        assert caught.value is cause
    assert len(starts) == 1  # An interrupted frame is never retried or reused.
    assert len(pool._workers) == (0 if during_startup else 1)


@pytest.mark.parametrize("during_startup", [False, True])
@pytest.mark.parametrize("kind", ["budget", "crash"])
def test_closed_interrupted_lane_releases_payload_without_cyclic_gc(monkeypatch, during_startup, kind):
    from behave.sim.scoring_policy import ScoringStopped

    class Payload:
        pass

    failure_type = ScoringStopped if kind == "budget" else EOFError

    def interrupted(*args):
        raise failure_type()

    def create(*args):
        if during_startup:
            interrupted()
        return SimpleNamespace(check=interrupted)

    monkeypatch.setattr(trace_process, "_Worker", create)
    monkeypatch.setattr(trace_process, "_close_workers", lambda workers: None)
    enabled = gc.isenabled()
    gc.disable()
    try:
        pool = trace_process.TraceWorkerPool(None, None, 1)
        payload = Payload()
        retained = weakref.ref(payload)
        try:
            pool.check(None, payload, None, 0)
        except failure_type:
            pass
        pool.close()
        del payload, pool
        assert retained() is None
    finally:
        if enabled:
            gc.enable()


def test_stop_retains_channel_failure_when_kill_also_fails(lifetime):
    clock, events = lifetime
    owned = worker(clock, events, "one")
    primary = OSError("channel close failed")

    def close():
        raise primary

    def kill():
        events.append(("one", "kill"))
        raise OSError("signal delivery failed")

    owned.connection.close = close
    owned.process.kill = kill
    with pytest.raises(OSError) as caught:
        owned.stop()
    assert caught.value is primary
    assert ("one", "kill") in events
    assert any("signal delivery failed" in note for note in primary.__notes__)


def test_reap_retains_wait_failure_when_diagnostic_close_also_fails(lifetime):
    clock, events = lifetime
    owned = worker(clock, events, "one", unreapable=True)

    def close():
        events.append(("one", "diagnostic-close"))
        raise OSError("diagnostic close failed")

    owned.errors = SimpleNamespace(close=close)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        owned.reap(7)
    assert clock.now == 7
    assert ("one", "diagnostic-close") in events
    assert any("diagnostic close failed" in note for note in caught.value.__notes__)


def test_failed_initialization_keeps_primary_exception(monkeypatch, lifetime):
    clock, events = lifetime
    process = Process(clock, events, "startup", unreapable=True)
    monkeypatch.setattr(trace_process.subprocess, "Popen", lambda *a, **k: process)

    def fail_send(*_):
        raise ValueError("the original plan transport error")

    monkeypatch.setattr(trace_process.TraceChannel, "send", fail_send)
    # The worker trims a real plan before sending it; this test's plan is None.
    monkeypatch.setattr(trace_process, "replace", lambda plan, **_: plan)
    with pytest.raises(ValueError, match="original plan transport") as caught:
        trace_process._Worker(None, None)
    assert any("cleanup" in note.lower() for note in caught.value.__notes__)
    assert clock.now == 7
    assert "quarantine" in events


def test_failed_initialization_still_reaps_when_child_socket_close_fails(monkeypatch, lifetime):
    clock, events = lifetime
    process = Process(clock, events, "startup")
    monkeypatch.setattr(trace_process.subprocess, "Popen", lambda *a, **k: process)
    socketpair = trace_process.socket.socketpair

    def pair():
        parent, child = socketpair()

        def close():
            child.close()
            raise OSError("child channel close failed")

        return parent, SimpleNamespace(fileno=child.fileno, close=close)

    monkeypatch.setattr(trace_process.socket, "socketpair", pair)
    with pytest.raises(OSError, match="child channel close failed"):
        trace_process._Worker(None, None)
    assert process.returncode == -9
    assert ("startup", "kill") in events
    assert ("startup", "wait", 7) in events


@pytest.mark.parametrize("primary", [ValueError("decode failed"), KeyboardInterrupt()])
def test_postprocessor_keeps_active_error_when_cleanup_also_fails(monkeypatch, primary):
    def cleanup():
        error = RuntimeError("cannot reap trace worker")
        error.add_note("diagnostic close also failed")
        raise error

    quarantine = []
    monkeypatch.setattr(trace_postprocessing, "cpu_cleanup_unconfirmed", lambda: quarantine.append(True))
    checker = object.__new__(trace_postprocessing.TracePostprocessor)
    checker.pool = SimpleNamespace(close=cleanup)
    with pytest.raises(type(primary)) as caught, checker:
        raise primary
    assert caught.value is primary
    assert any("cannot reap trace worker" in note for note in caught.value.__notes__)
    assert any("diagnostic close also failed" in note for note in caught.value.__notes__)
    assert quarantine == [True]


def test_cleanup_failure_without_an_earlier_error_still_fails(monkeypatch):
    def cleanup():
        raise RuntimeError("cannot reap trace worker")

    monkeypatch.setattr(trace_postprocessing, "cpu_cleanup_unconfirmed", lambda: None)
    checker = object.__new__(trace_postprocessing.TracePostprocessor)
    checker.pool = SimpleNamespace(close=cleanup)
    with pytest.raises(RuntimeError, match="cannot reap trace worker"), checker:
        pass
