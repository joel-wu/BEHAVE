"""Completed replies must not spend the worker's reap budget on a bye request."""

import io
import subprocess

import pytest

from behave.core import runtime_defaults as defaults
from behave.sim.behavior.isolated import IsolatedModel
from behave.sim.core.failure import FailureOrigin, FailureRaised


def test_completed_model_stops_before_spending_its_reap_allowance(monkeypatch):
    clock, waits, requests = [10.0], [], []

    class Child:
        def __init__(self):
            self.stdin, self.stdout, self.stderr = (io.BytesIO() for _ in range(3))
            self.stopped = self.reaped = False

        def poll(self):
            return 0 if self.reaped else None

        def kill(self):
            self.stopped = True

        def wait(self, timeout):
            waits.append(timeout)
            if not self.stopped or timeout <= 0:
                clock[0] += timeout
                raise subprocess.TimeoutExpired("child", timeout)
            self.reaped = True

    model = object.__new__(IsolatedModel)
    model._child = child = Child()
    monkeypatch.setattr(defaults, "SERVICE_CLEANUP_SECONDS", 7)
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(model, "_say", requests.append)
    try:
        model.close()
        assert requests == []
        assert waits == [7.0]
        assert child.reaped and model._child is None
        assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))
    finally:
        model._child = None  # Fake ownership, not a real process to reap at collection.


def test_model_cleanup_failure_belongs_to_infrastructure(monkeypatch):
    from types import SimpleNamespace

    def cannot_stop():
        raise OSError("namespace could not be stopped")

    model = object.__new__(IsolatedModel)
    model._child = child = SimpleNamespace(poll=lambda: None, kill=cannot_stop)
    try:
        with pytest.raises(FailureRaised) as caught:
            model.close()
        assert caught.value.failure.origin is FailureOrigin.INFRASTRUCTURE
        assert caught.value.failure.kind == "behavior-worker-cleanup-unconfirmed"
        assert model._child is child
        assert caught.value.__cause__.args == ("namespace could not be stopped",)
    finally:
        model._child = None


@pytest.mark.isolation
def test_completed_reply_does_not_wait_for_python_exit_handlers(monkeypatch):
    """A real worker has nothing left to publish after its checked response."""
    from behave.sim.behavior import isolated

    monkeypatch.setattr(isolated, "_CHILD", (
        "import atexit, time\natexit.register(time.sleep, 60)\n" + isolated._CHILD))
    source = """class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": a + 1}
"""
    with IsolatedModel(source, cls="M") as model:
        model.reset()
        assert model.process(a=7) == {"y": 8}
        child = model._child
    assert child.poll() is not None
    assert model._child is None
