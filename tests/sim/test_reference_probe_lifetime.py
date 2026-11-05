"""Load-time freshness checks own and release their temporary model instances."""

import threading

import pytest

from behave.sim import api
from behave.sim.core import reference


def install_loader(monkeypatch, factory):
    def loader(sources, config):
        return factory

    monkeypatch.setitem(reference._LOADERS, "probe-lifetime-test/1", loader)


class Probe:
    def __init__(self, closed, name, fail=False):
        self.closed, self.name, self.fail = closed, name, fail

    def close(self):
        self.closed.append(self.name)
        if self.fail:
            raise RuntimeError("probe cleanup failed")


def test_successful_probes_close_but_future_instances_stay_open(monkeypatch):
    closed, made = [], []

    def factory():
        model = Probe(closed, len(made))
        made.append(model)
        return model

    install_loader(monkeypatch, factory)
    bundle = api.load_reference("probe-lifetime-test/1", {"model": b"fixed"})
    assert sorted(closed) == [0, 1]
    live = bundle.new_model()
    assert live is made[2] and 2 not in closed
    live.close()
    assert sorted(closed) == [0, 1, 2]


def test_second_constructor_failure_closes_first_probe(monkeypatch):
    closed = []
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second constructor failed")
        return Probe(closed, "first")

    install_loader(monkeypatch, factory)
    with pytest.raises(RuntimeError, match="second constructor failed"):
        api.load_reference("probe-lifetime-test/1", {"model": b"fixed"})
    assert closed == ["first"]


def test_reused_instance_is_closed_once_and_refused(monkeypatch):
    closed = []
    shared = Probe(closed, "shared")
    install_loader(monkeypatch, lambda: shared)
    with pytest.raises(api.Refused, match="reference-loader-returns-one-instance"):
        api.load_reference("probe-lifetime-test/1", {"model": b"fixed"})
    assert closed == ["shared"]


def test_cleanup_failure_does_not_leave_other_probe_open(monkeypatch):
    closed = []
    models = iter((Probe(closed, "first"), Probe(closed, "second", fail=True)))
    install_loader(monkeypatch, lambda: next(models))
    with pytest.raises(RuntimeError, match="probe cleanup failed"):
        api.load_reference("probe-lifetime-test/1", {"model": b"fixed"})
    assert sorted(closed) == ["first", "second"]


def test_repeated_isolated_loads_leave_no_probe_service_threads():
    source = b'class Model:\n    def reset(self):\n        pass\n    def process(self, value):\n        return {"value":value}\n'
    before = set(threading.enumerate())
    for _ in range(3):
        api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": source}, config={"class": "Model"})
    leaked = [thread.name for thread in threading.enumerate() if thread not in before
              and any(name in thread.name for name in ("_drain_stderr", "_finish_status"))]
    assert leaked == []
