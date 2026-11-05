"""The shared Driver advances its accounting once per slot, without history scans."""

import weakref

import pytest

from behave.sim import api
from behave.sim.core.driver import Driver
from behave.sim.core.product import Component, ProductState
from tests.support.device_boundary import run_device

from .test_api_path import Doubling, contract, realization, schedule


def test_concrete_driver_retains_only_the_current_product_state(monkeypatch):
    from behave.sim.core import driver as driver_module

    live = weakref.WeakValueDictionary()

    class TrackedState(ProductState):
        __slots__ = ("__weakref__",)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            live[id(self)] = self

    monkeypatch.setattr(driver_module, "ProductState", TrackedState)
    c = contract(answers=1)
    driver = Driver(c, schedule(list(range(64)), c=c, drain=20))
    device = Doubling()
    device.reset()
    for index, slot in enumerate(driver.stimulus.slots):
        controls = driver.controls(index)
        presented = driver.presentations(index)
        if slot.reset:
            device.reset()
        driver.observe(index, presented, device.settle(driver.present(index), controls))
        device.edge()
        assert len(live) == 1
    assert driver.last_state["driver.slot"] == len(driver.records)
    assert driver.trace().accepted_count({"in"}) == 64


def test_manifest_cannot_change_after_the_first_slot_failed():
    c = contract(answers=1)
    driver = Driver(c, schedule([1], c=c))
    driver.fail(0, "device failed before producing a sample")

    with pytest.raises(api.Refused, match="driver-manifest-late"):
        driver.declare((Component("artifact.late", "artifact"),))


def _step(driver, device):
    device.reset()
    for index, slot in enumerate(driver.stimulus.slots):
        controls = driver.controls(index)
        presentations = driver.presentations(index)
        if slot.reset:
            device.reset()
        driver.observe(
            index, presentations,
            device.settle(driver.present(index), controls))
        device.edge()
        yield driver.last_state


def test_incremental_accounting_equals_the_history_oracle_at_every_slot():
    c = contract(answers=1)
    stimulus = schedule([1, 2, 3, 4, 5], c=c, drain=4)
    driver = Driver(c, stimulus)

    for incremental in _step(driver, Doubling()):
        history = driver.state(records=tuple(driver.records))
        assert incremental == history
        assert dict(driver.records[-1].monitor).get("outstanding") == \
            history["monitor.outstanding"]

    trace = driver.trace()
    assert trace.accepted_count({"in"}) == 5
    assert trace.accepted_count({"out"}) == 5


def test_production_hot_path_never_calls_the_history_oracle(monkeypatch):
    def history_scan_is_a_bug(*_args, **_kwargs):
        raise AssertionError("production Driver rescanned its record history")

    monkeypatch.setattr(Driver, "_in_flight", history_scan_is_a_bug)
    c = contract(answers=1)
    task = api.admit(c, realization(), reference=None)
    rows = [index & 0xFF for index in range(1024)]

    trace = run_device(task, schedule(rows, c=c, drain=2), Doubling())

    assert trace.accepted_count({"in"}) == len(rows)
    assert trace.accepted_count({"out"}) == len(rows)
