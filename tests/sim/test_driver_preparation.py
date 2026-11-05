"""Slot preparation is shared; protocol observations and audits are not cached."""

import random

import pytest

from behave.sim import api
from behave.sim.core.driver import Driver
from behave.sim.core.frozen import FrozenMap
from behave.sim.core.input_queue import InputQueueState
from behave.sim.core.monitor import Monitor
from behave.sim.core.product import Component
from tests.sim import test_apb_protocol as apb
from tests.sim import test_axi_lite_protocol as axi
from tests.sim import test_interface_composition as composition
from tests.sim import test_protocol_adapters as protocols
from tests.sim import test_same_clock_protocol_product as product
from tests.sim import test_sampled_protocol as sampled
from tests.sim import test_settled_protocol as settled


class UncachedDriver(Driver):
    """Subclasses retain fresh preparation, including overridden drive views."""


def _case(kind, seed):
    rng = random.Random(seed)
    if kind == "product":
        return product._product_contract(), product._product_stimulus(), product._ProductDevice()
    if kind == "composition":
        return composition._contract(bool(seed % 2)), composition._stimulus(), composition._Device()
    if kind.startswith("axi-"):
        write = kind == "axi-write"
        contract = axi._contract(write)
        return (contract, axi._stimulus(contract, write, stall=bool(seed % 2)),
                axi._Device(write, delays=(seed % 3, (seed + 1) % 3)))
    if kind == "apb":
        contract = apb._contract()
        device = apb._Device(waits=seed % 3)
        drain = 24
    elif kind in ("ready-valid", "start-done"):
        contract = protocols._contract(kind)
        device = (protocols._ReadyValidDoubling() if kind == "ready-valid"
                  else protocols._StartDoneDoubling(latency=seed % 3))
        drain = 24
    elif kind == "settled":
        contract, device, drain = settled.contract(), sampled._Doubling(), 0
    else:
        input_valid, output_valid = map(int, kind.rsplit("-", 1)[1])
        contract = sampled._contract(input_valid=bool(input_valid), output_valid=bool(output_valid))
        device, drain = sampled._Doubling(), 0
    rows = [(rng.randrange(256),) for _ in range(5)]
    return contract, api.reset_then(contract, rows, drain=drain), device


def _run(driver_type, kind, seed, *, prepare_reads=1, share_slots=False):
    contract, stimulus, device = _case(kind, seed)
    if share_slots:
        slots = []
        for slot in stimulus.slots:
            slots.append(slots[-1] if slots and slots[-1] == slot else slot)
        stimulus = api.Stimulus(queues=stimulus.queues, slots=slots)
    driver = driver_type(contract, stimulus)
    device.reset()
    driven = []
    states = []
    for index, slot in enumerate(stimulus.slots):
        # Asking about another slot must not cause a release or retain stale views.
        if not share_slots and index + 1 < len(stimulus.slots):
            driver.presentations(index + 1)
        for _ in range(prepare_reads):
            controls = driver.controls(index)
            presentations = driver.presentations(index)
        driven.append((dict(controls), dict(presentations)))
        if slot.reset:
            device.reset()
        payload = (dict(presentations) if len(driver.inputs) > 1
                   else presentations[driver.inputs[0].name])
        signals = device.settle(payload, controls)
        driver.observe(index, presentations, signals)
        states.append(driver.last_state)
        device.edge()
    trace = driver.trace()
    report = Monitor(contract).check(stimulus, trace)
    audit = {
        name: [item.as_dict() for item in getattr(report, name)]
        for name in ("hits", "violations", "assumption_violations", "unresolved")
    }
    audit.update(opportunities=dict(report.opportunities), unchecked=report.unchecked)
    return driven, tuple(states), trace.as_dict(), audit


@pytest.mark.parametrize("kind", (
    "ready-valid", "start-done", "apb", "axi-write", "axi-read", "product",
    "composition", "settled", "sampled-00", "sampled-01", "sampled-10", "sampled-11",
))
@pytest.mark.parametrize("seed", (10, 11, 12))
@pytest.mark.parametrize("share_slots", (False, True))
def test_prepared_and_recomputed_views_have_identical_trace_and_audit(kind, seed, share_slots):
    assert _run(Driver, kind, seed, prepare_reads=3, share_slots=share_slots) == _run(
        UncachedDriver, kind, seed, share_slots=share_slots)


def test_unchanged_drive_is_reused_but_every_observation_still_executes(monkeypatch):
    c = protocols._contract("ready-valid")
    slot = api.EventSlot(controls={"rst": 0, "out_ready": 1})
    stimulus = api.Stimulus(slots=[slot] * 128)
    driver = Driver(c, stimulus)
    prepare, step = InputQueueState.prepare, type(driver.protocol).step
    calls = {"prepare": 0, "step": 0}

    def preparing(*args, **kwargs):
        calls["prepare"] += 1
        return prepare(*args, **kwargs)

    def stepping(*args, **kwargs):
        calls["step"] += 1
        return step(*args, **kwargs)

    monkeypatch.setattr(InputQueueState, "prepare", preparing)
    monkeypatch.setattr(type(driver.protocol), "step", stepping)
    for index in range(len(stimulus.slots)):
        presented = driver.presentations(index)
        driver.observe(index, presented,
                       {"in_ready": index % 2, "out_valid": 0, "out_y": index})
    assert calls == {"prepare": 1, "step": 128}
    assert len(driver.records) == 128
    assert [dict(record.outputs)["out_y"] for record in driver.records] == list(range(128))


def test_one_queue_preparation_per_slot_and_an_independent_audit(monkeypatch):
    contract, stimulus, device = _case("ready-valid", 10)
    driver = Driver(contract, stimulus)
    original = InputQueueState.prepare
    calls = []

    def counted(state, *args, **kwargs):
        calls.append(state)
        return original(state, *args, **kwargs)

    monkeypatch.setattr(InputQueueState, "prepare", counted)
    device.reset()
    for index, slot in enumerate(stimulus.slots):
        presentations = driver.presentations(index)
        controls = driver.controls(index)
        assert driver.presentations(index) is presentations
        controls["in_valid"] ^= 1
        assert driver.controls(index)["in_valid"] != controls["in_valid"]
        if slot.reset:
            device.reset()
        driver.observe(index, presentations, device.settle(driver.present(index), driver.controls(index)))
        device.edge()
        assert driver._prepared_slot is not None
    assert len(calls) == len(stimulus.slots)
    trace = driver.trace()
    assert len(calls) == 2 * len(stimulus.slots)
    Monitor(contract).check(stimulus, trace)
    assert len(calls) == 3 * len(stimulus.slots)


def test_native_presentation_builds_one_drive_per_slot(monkeypatch):
    contract, stimulus, device = _case("ready-valid", 10)
    driver = Driver(contract, stimulus)
    program = type(driver.protocol)
    original, calls = program.drive, []

    def counted(self, *args, **kwargs):
        calls.append(None)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(program, "drive", counted)
    device.reset()
    for index, slot in enumerate(stimulus.slots):
        controls = driver.controls(index)
        presented = driver.presentations(index)
        if slot.reset:
            device.reset()
        signals = device.settle(driver.present(index), controls)
        driver.observe(index, presented, signals)
        device.edge()
    assert len(calls) == len(stimulus.slots)


def test_observe_consumes_prepared_drive_without_remaking_public_controls(monkeypatch):
    contract, stimulus, device = _case("ready-valid", 10)
    driver = Driver(contract, stimulus)
    original = Driver.controls
    calls = []

    def counted(self, index):
        calls.append(index)
        return original(self, index)

    monkeypatch.setattr(Driver, "controls", counted)
    device.reset()
    for index, slot in enumerate(stimulus.slots):
        controls = driver.controls(index)
        presented = driver.presentations(index)
        if slot.reset:
            device.reset()
        driver.observe(index, presented, device.settle(driver.present(index), controls))
        device.edge()
    assert calls == list(range(len(stimulus.slots)))


def test_observe_without_prior_controls_still_checks_reset(monkeypatch):
    from behave.sim.core.protocol import AdapterDrive

    contract = protocols._contract("ready-valid")
    stimulus = api.Stimulus(slots=[api.EventSlot(
        reset=True, controls={"rst": 1, "out_ready": 1})])
    driver = Driver(contract, stimulus)
    program = type(driver.protocol)
    original = program.drive

    def bad_reset(self, *args):
        drive = original(self, *args)
        return AdapterDrive({**dict(drive.controls), "rst": 0}, drive.presentations)

    monkeypatch.setattr(program, "drive", bad_reset)
    with pytest.raises(api.Refused, match="driver-reset-slot-not-asserted"):
        driver.observe(0, driver.presentations(0),
                       {"in_ready": 1, "out_valid": 0, "out_y": 0})
    assert not driver.records


def test_same_slot_rechecks_changed_queue_and_adapter_state():
    contract = apb._contract()
    stimulus = api.Stimulus({"in": [(3,), (9,)]}, [
        api.EventSlot(offers=("in",), controls={"rst": 0})])
    driver = Driver(contract, stimulus)
    assert driver.controls(0)["a_enable"] == 0
    first = driver._prepared_slot
    drive = driver.protocol.drive(contract, driver.adapter_state, stimulus.slots[0],
                                  driver.presentations(0), driver.controls(0))
    driver.adapter_state = driver.protocol.step(
        contract, driver.adapter_state, stimulus.slots[0], drive,
        {"pready": 0, "prdata": 0, "pslverr": 0}).state
    assert driver.controls(0)["a_enable"] == 1
    assert driver._prepared_slot is not first
    driver._queue_state = InputQueueState(FrozenMap({"in": 1}), FrozenMap({"in": 1}))
    assert driver.present(0) == (9,)


def test_observe_does_not_substitute_cached_presentations_for_its_argument():
    contract = protocols._contract("ready-valid")
    stimulus = api.Stimulus({"in": [(3,)]}, [
        api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1})])
    driver = Driver(contract, stimulus)
    assert driver.present(0) == (3,)
    driver.observe(0, None, {"in_ready": 1, "out_valid": 0, "out_y": 0})
    assert driver.taken == 0
    assert dict(driver.records[0].accepted).get("in") is None


def test_refused_transition_can_retry_without_releasing_twice():
    contract = protocols._contract("ready-valid")
    stimulus = api.Stimulus({"in": [(3,), (9,)]}, [
        api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1})])
    driver = Driver(contract, stimulus)
    driver.declare((Component("artifact.x", "artifact"),))
    presentations = driver.presentations(0)
    signals = {"in_ready": 1, "out_valid": 0, "out_y": 0}
    with pytest.raises(api.Refused, match="product-unclaimed-change"):
        driver.observe(0, presentations, signals,
                       device_state={"artifact.x": 0}, device_after={"artifact.x": 1})
    assert driver.taken == 0 and not driver.records
    assert driver.presentations(0) is presentations
    driver.observe(0, presentations, signals,
                   device_state={"artifact.x": 0}, device_after={"artifact.x": 1},
                   device_changed={"artifact": ["artifact.x"]})
    assert driver.taken == driver.released["in"] == 1


def test_overridden_presentations_are_not_replaced_by_prepared_inputs():
    class MutablePresentation(Driver):
        value = None

        def presentations(self, _index):
            return FrozenMap({"in": self.value})

    contract = protocols._contract("ready-valid")
    stimulus = protocols._rv_stimulus()
    driver = MutablePresentation(contract, stimulus)
    assert driver.controls(1)["in_valid"] == 0
    driver.value = (3,)
    assert driver.controls(1)["in_valid"] == 1


def test_repeated_observations_do_not_reconstruct_slot_records(monkeypatch):
    contract = protocols._contract("ready-valid")
    slot = api.EventSlot(controls={"rst": 0, "out_ready": 1})
    driver = Driver(contract, api.Stimulus(slots=[slot] * 128))
    original = api.SlotRecord.__init__
    calls = []

    def counted(self, *args, **kwargs):
        calls.append(None)
        original(self, *args, **kwargs)

    monkeypatch.setattr(api.SlotRecord, "__init__", counted)
    for index in range(128):
        driver.observe(index, driver.presentations(index),
                       {"in_ready": 1, "out_valid": 0, "out_y": 0})
    assert len(calls) == 1
    assert len(driver.records) == 128
    assert all(record is driver.records[0] for record in driver.records)
    assert driver.last_state["driver.slot"] == 128


def test_record_reuse_keeps_scalar_types_and_rejects_subclasses():
    class ForeignInt(int):
        pass

    contract = protocols._contract("ready-valid")
    slot = api.EventSlot(controls={"rst": 0, "out_ready": 1})
    driver = Driver(contract, api.Stimulus(slots=[slot] * 3))
    signals = {"in_ready": 1, "out_valid": 0, "out_y": 0, "extra": True}
    driver.observe(0, driver.presentations(0), signals)
    signals["extra"] = 1
    driver.observe(1, driver.presentations(1), signals)
    assert driver.records[0] is not driver.records[1]
    assert dict(driver.records[0].outputs)["extra"] is True
    assert type(dict(driver.records[1].outputs)["extra"]) is int
    signals["extra"] = ForeignInt(1)
    with pytest.raises(TypeError):
        driver.observe(2, driver.presentations(2), signals)
    assert len(driver.records) == 2


def test_record_reuse_does_not_coalesce_identical_transfers():
    contract = protocols._contract("ready-valid")
    slot = api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1})
    driver = Driver(contract, api.Stimulus({"in": [(7,)] * 8}, [slot] * 8))
    for index in range(8):
        driver.observe(index, driver.presentations(index),
                       {"in_ready": 1, "out_valid": 1, "out_y": 14})
    assert all(record is driver.records[0] for record in driver.records)
    assert driver.taken == 8
    trace = driver.trace()
    assert trace.accepted_count(["in"]) == trace.accepted_count(["out"]) == 8


@pytest.mark.parametrize("kind", (
    "ready-valid", "start-done", "apb", "axi-write", "axi-read", "product",
    "composition", "settled", "sampled-00", "sampled-01", "sampled-10", "sampled-11",
))
@pytest.mark.parametrize("seed", (10, 11))
def test_record_reuse_preserves_every_state_record_and_monitor(kind, seed, monkeypatch):
    reused = _run(Driver, kind, seed, share_slots=True)
    monkeypatch.setattr("behave.sim.core.driver._same_pairs", lambda *_: False)
    assert _run(Driver, kind, seed, share_slots=True) == reused
