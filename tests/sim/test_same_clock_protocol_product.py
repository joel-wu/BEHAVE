"""Endpoint-product execution at the protocol/driver boundary."""

from __future__ import annotations

import pytest

from behave.sim import api
from behave.sim.core.atomic import AtomicDriver
from behave.sim.core.clock_schedule import (
    ClockTiming,
    GlobalEventSlot,
    GlobalSchedule,
)
from behave.sim.core.driver import Driver
from behave.sim.core.product import Component
from behave.sim.core.protocol import adapter_program_for
from behave.sim.stimulus_set import _with_back_pressure

U8 = api.FiniteType(8)


def _ready_valid_endpoint(name, direction, *, clock="clk", valid=None,
                          ready=None, port=None):
    valid = f"{name}_valid" if valid is None else valid
    ready = f"{name}_ready" if ready is None else ready
    port = f"{name}_data" if port is None else port
    return api.Endpoint(
        name, direction, [api.Field("data", U8, port)],
        api.ready_valid(direction, valid, ready),
        clock=clock, kind=api.Kind.TRANSACTION)


def _product_contract():
    return api.TaskContract(
        endpoints=[
            _ready_valid_endpoint("left_request", api.Direction.IN),
            _ready_valid_endpoint("right_request", api.Direction.IN),
            _ready_valid_endpoint("left_result", api.Direction.OUT),
            _ready_valid_endpoint("right_result", api.Direction.OUT),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="same-clock ready/valid product fixture")


def _product_stimulus():
    controls = {"rst": 0, "left_result_ready": 1,
                "right_result_ready": 1}
    return api.Stimulus(
        queues={"left_request": [(10,), (11,)],
                "right_request": [(20,)]},
        slots=[
            api.EventSlot(
                reset=True,
                controls={"rst": 1, "left_result_ready": 0,
                          "right_result_ready": 0}),
            api.EventSlot(
                offers=("left_request", "right_request"), controls=controls),
            api.EventSlot(
                offers=("left_request", "right_request"), controls=controls),
        ],
        source="two independent request streams")


class _ProductDevice:
    def reset(self):
        self.observed = []

    def settle(self, presentations, controls):
        assert type(presentations) is dict
        slot = len(self.observed)
        self.observed.append((dict(presentations), dict(controls)))
        if slot == 0:
            left_ready, right_ready = 0, 0
            left_valid, right_valid = 0, 0
        elif slot == 1:
            left_ready, right_ready = 1, 0
            left_valid, right_valid = 1, 1
        else:
            left_ready, right_ready = 1, 1
            left_valid, right_valid = 0, 0
        return {
            "left_request_ready": left_ready,
            "right_request_ready": right_ready,
            "left_result_valid": left_valid,
            "right_result_valid": right_valid,
            "left_result_data": 70,
            "right_result_data": 80,
        }

    def edge(self):
        pass


def test_ready_valid_is_an_atomic_product_of_every_same_clock_endpoint():
    contract = _product_contract()
    stimulus = _product_stimulus()
    driver = Driver(contract, stimulus)
    device = _ProductDevice()
    device.reset()

    assert dict(driver.presentations(1)) == {
        "left_request": (10,), "right_request": (20,)}
    assert driver.controls(1)["left_request_valid"] == 1
    assert driver.controls(1)["right_request_valid"] == 1
    with pytest.raises(api.Refused, match="driver-presentation-ambiguous"):
        driver.present(1)
    with pytest.raises(api.Refused, match="driver-pointer-ambiguous"):
        _ = driver.taken

    for index, slot in enumerate(stimulus.slots):
        presentations = driver.presentations(index)
        controls = driver.controls(index)
        if slot.reset:
            device.reset()
        signals = device.settle(dict(presentations), controls)
        driver.observe(index, presentations, signals)
        device.edge()

    trace = driver.trace()
    simultaneous = dict(trace.records[1].accepted)
    assert simultaneous == {
        "left_request": (10,),
        "left_result": (70,),
        "right_request": None,
        "right_result": (80,),
    }
    assert dict(trace.records[2].accepted) == {
        "left_request": (11,), "right_request": (20,)}
    assert dict(driver.pointers) == {"left_request": 2, "right_request": 1}
    assert dict(driver.last_state["driver.pointers"]) == dict(driver.pointers)
    assert "outstanding" not in dict(trace.records[-1].monitor)

    replay = adapter_program_for(contract).replay(contract, stimulus, trace)
    assert replay[1].channels["left_request"].transferred
    assert not replay[1].channels["right_request"].transferred
    assert replay[2].channels["right_request"].payload == (20,)


def test_single_ready_valid_pair_has_no_outstanding_without_correlation():
    contract = api.TaskContract(
        endpoints=[
            _ready_valid_endpoint("request", api.Direction.IN),
            _ready_valid_endpoint("result", api.Direction.OUT),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])
    stimulus = api.Stimulus(
        queues={"request": [(7,)]},
        slots=[api.EventSlot(
            offers=("request",),
            controls={"rst": 0, "result_ready": 1})])
    driver = Driver(contract, stimulus)

    presented = driver.present(0)
    driver.observe(0, presented, {
        "request_ready": 1,
        "result_valid": 0,
        "result_data": 0,
    })

    assert "outstanding" not in dict(driver.records[0].monitor)
    assert "monitor.outstanding" not in {
        component.name for component in driver.last_state.manifest}


def test_adapter_drive_scalar_payload_refuses_a_multi_input_selection():
    contract = _product_contract()
    stimulus = _product_stimulus()
    program = adapter_program_for(contract)
    drive = program.drive(
        contract, program.initial_state(contract), stimulus.slots[1],
        {"left_request": (10,), "right_request": (20,)},
        dict(stimulus.slots[1].controls))

    assert dict(drive.presentations) == {
        "left_request": (10,), "right_request": (20,)}
    with pytest.raises(api.Refused, match="adapter-drive-payload-ambiguous"):
        _ = drive.payload


def test_ready_valid_refuses_shared_input_presentation_authority():
    contract = api.TaskContract(
        endpoints=[
            _ready_valid_endpoint(
                "first", api.Direction.IN, valid="shared_valid",
                ready="first_ready"),
            _ready_valid_endpoint(
                "second", api.Direction.IN, valid="shared_valid",
                ready="second_ready"),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])

    with pytest.raises(
            api.Refused, match="adapter-presentation-control-collision"):
        adapter_program_for(contract)


def test_shared_output_controls_are_an_executable_coupled_broadcast():
    contract = api.TaskContract(
        endpoints=[
            _ready_valid_endpoint(
                "first", api.Direction.OUT, valid="shared_valid",
                ready="shared_ready"),
            _ready_valid_endpoint(
                "second", api.Direction.OUT, valid="shared_valid",
                ready="shared_ready"),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])
    slot = api.EventSlot(controls={"rst": 0, "shared_ready": 1})
    program = adapter_program_for(contract)
    drive = program.drive(
        contract, program.initial_state(contract), slot, {},
        dict(slot.controls))
    transition = program.step(
        contract, program.initial_state(contract), slot, drive,
        {"shared_valid": 1, "first_data": 3, "second_data": 8})

    assert dict(transition.emitted_outputs) == {
        "first": (3,), "second": (8,)}


def test_presentation_and_scheduled_control_cannot_share_one_pin():
    contract = api.TaskContract(
        endpoints=[
            _ready_valid_endpoint(
                "request", api.Direction.IN, valid="shared_control",
                ready="request_ready"),
            _ready_valid_endpoint(
                "result", api.Direction.OUT, valid="result_valid",
                ready="shared_control"),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])

    with pytest.raises(
            api.Refused, match="adapter-environment-control-role-collision"):
        adapter_program_for(contract)


@pytest.mark.parametrize("collision", ["payload", "reset"])
def test_protocol_pin_cannot_also_be_payload_or_reset(collision):
    port = "shared" if collision == "payload" else "request_data"
    reset = "rst" if collision == "payload" else "request_valid"

    with pytest.raises(api.Refused, match="contract-port-role-collision"):
        api.TaskContract(
            endpoints=[
                _ready_valid_endpoint(
                    "request", api.Direction.IN,
                    valid="shared" if collision == "payload" else "request_valid",
                    port=port),
            ],
            clocks=[api.ClockDomain("clk", "clk", reset)])


def test_clock_domain_names_identify_one_timing_policy():
    with pytest.raises(api.Refused, match="contract-duplicate-clock-domain"):
        api.TaskContract(
            endpoints=[],
            clocks=[
                api.ClockDomain("same", "clk_a", "rst_a"),
                api.ClockDomain("same", "clk_b", "rst_b"),
            ])


def test_one_physical_pin_cannot_be_both_a_clock_and_a_reset():
    with pytest.raises(api.Refused, match="contract-port-role-collision"):
        api.TaskContract(
            endpoints=[],
            clocks=[
                api.ClockDomain("a", "shared", "rst_a"),
                api.ClockDomain("b", "clk_b", "shared"),
            ])


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"name": ""}, "contract-clock-field-invalid"),
        ({"clock": 3}, "contract-clock-field-invalid"),
        ({"reset": None}, "contract-clock-field-invalid"),
        ({"edge": 1}, "contract-clock-field-invalid"),
        ({"reset_active_low": 1}, "contract-reset-polarity-invalid"),
        ({"reset_hold_cycles": True}, "contract-reset-hold-invalid"),
        ({"reset_hold_cycles": -1}, "contract-reset-hold-invalid"),
        ({"reset_restores": 1}, "contract-reset-restoration-invalid"),
    ],
)
def test_clock_domain_does_not_coerce_task_semantics(kwargs, reason):
    values = {
        "name": "clk", "clock": "clk", "reset": "rst",
        "reset_active_low": False, "reset_hold_cycles": 3,
        "reset_restores": None, "edge": "rising",
    }
    values.update(kwargs)

    with pytest.raises(api.Refused, match=reason):
        api.ClockDomain(**values)


def test_drain_is_receptive_for_every_output_endpoint():
    contract = _product_contract()
    body = [api.EventSlot(controls={
        "rst": 0,
        "left_result_ready": 0,
        "right_result_ready": 0,
    })]

    tail = api.idle_tail(contract, body, 3, stall_every=2)

    assert [dict(slot.controls)["left_result_ready"] for slot in tail] == [1, 1, 1]
    assert [dict(slot.controls)["right_result_ready"] for slot in tail] == [1, 1, 1]


def test_generation_applies_back_pressure_to_every_output_endpoint():
    contract = _product_contract()
    stimulus = api.Stimulus(
        queues={},
        slots=[
            api.EventSlot(reset=True, controls={"rst": 1}),
            api.EventSlot(controls={"rst": 0}),
        ])

    got = _with_back_pressure(stimulus, contract, stall_every=2)

    assert [dict(slot.controls)["left_result_ready"] for slot in got.slots] == [0, 1]
    assert [dict(slot.controls)["right_result_ready"] for slot in got.slots] == [0, 1]


def test_reconciliation_refusal_does_not_partially_commit_a_slot():
    contract = _product_contract()
    stimulus = _product_stimulus()
    driver = Driver(contract, stimulus)
    driver.declare((Component("artifact.state", "artifact"),))
    device = _ProductDevice()
    device.reset()

    reset_presentations = driver.presentations(0)
    driver.observe(
        0, reset_presentations,
        device.settle(dict(reset_presentations), driver.controls(0)),
        device_state={"artifact.state": 0},
        device_after={"artifact.state": 0})
    before_pointers = dict(driver.pointers)
    before_adapter = driver.adapter_state
    before_state = driver.last_state

    presentations = driver.presentations(1)
    with pytest.raises(api.Refused, match="product-unclaimed-change"):
        driver.observe(
            1, presentations,
            device.settle(dict(presentations), driver.controls(1)),
            device_state={"artifact.state": 0},
            device_after={"artifact.state": 1})

    assert dict(driver.pointers) == before_pointers
    assert driver.adapter_state == before_adapter
    assert len(driver.records) == 1
    assert driver.last_state is before_state


def test_start_done_still_requires_exactly_one_request_and_completion():
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "first", api.Direction.IN, [api.Field("data", U8, "first_data")],
                api.start_done(api.Direction.IN, "first_start", "done"),
                clock="clk", kind=api.Kind.TRANSACTION,
                reset_policy=api.ResetPolicy.CLEARS),
            api.Endpoint(
                "second", api.Direction.IN,
                [api.Field("data", U8, "second_data")],
                api.start_done(api.Direction.IN, "second_start", "done"),
                clock="clk", kind=api.Kind.TRANSACTION,
                reset_policy=api.ResetPolicy.CLEARS),
            api.Endpoint(
                "result", api.Direction.OUT,
                [api.Field("data", U8, "result_data")],
                api.start_done(api.Direction.OUT, "first_start", "done"),
                clock="clk", kind=api.Kind.TRANSACTION,
                reset_policy=api.ResetPolicy.CLEARS),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])

    with pytest.raises(api.Refused, match="adapter-start-done-endpoints"):
        adapter_program_for(contract)


def test_atomic_driver_passes_a_full_same_domain_presentation_product():
    contract = api.TaskContract(
        endpoints=[
            _ready_valid_endpoint("left", api.Direction.IN, clock="a"),
            _ready_valid_endpoint("right", api.Direction.IN, clock="a"),
            _ready_valid_endpoint("result", api.Direction.OUT, clock="b"),
        ],
        clocks=[
            api.ClockDomain("a", "a_clk", "a_rst"),
            api.ClockDomain("b", "b_clk", "b_rst"),
        ])
    event = api.EventSlot(
        offers=("left", "right"),
        controls={"a_rst": 0, "b_rst": 0, "result_ready": 1},
        ticks=("a", "b"))
    timings = (
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 3, clock="b_clk", reset="b_rst"),
    )
    schedule = GlobalSchedule(timings, [GlobalEventSlot(0, event=event)])
    stimulus = api.Stimulus(
        queues={"left": [(4,)], "right": [(9,)]}, slots=[event],
        clock_schedule=schedule)
    driver = AtomicDriver(contract, stimulus)

    drive = driver.drive(0)
    assert dict(drive.domains["a"].presentations) == {
        "left": (4,), "right": (9,)}
    driven = dict(drive.signals)
    assert {name: driven[name] for name in (
        "left_data", "right_data", "left_valid", "right_valid")} == {
        "left_data": 4, "right_data": 9,
        "left_valid": 1, "right_valid": 1,
    }
    record = driver.observe(0, drive, {
        "left_ready": 1,
        "right_ready": 1,
        "result_valid": 1,
        "result_data": 13,
    })

    assert dict(record.accepted) == {
        "left": (4,), "result": (13,), "right": (9,)}
    assert driver.pointers == {"left": 1, "right": 1}


def test_atomic_reset_ignores_unknown_pre_edge_protocol_values_only_on_reset():
    contract = api.TaskContract(
        endpoints=[
            _ready_valid_endpoint("request", api.Direction.IN, clock="a"),
            _ready_valid_endpoint("result", api.Direction.OUT, clock="b"),
        ],
        clocks=[
            api.ClockDomain("a", "a_clk", "a_rst"),
            api.ClockDomain("b", "b_clk", "b_rst"),
        ])
    reset = api.EventSlot(
        controls={"a_rst": 1, "b_rst": 1, "result_ready": 0},
        ticks=("a", "b"), resets=("a", "b"))
    active = api.EventSlot(
        controls={"a_rst": 0, "b_rst": 0, "result_ready": 1},
        ticks=("a", "b"))
    timings = (
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 2, clock="b_clk", reset="b_rst"),
    )
    schedule = GlobalSchedule(timings, [
        GlobalEventSlot(0, event=reset),
        GlobalEventSlot(2, event=active),
    ])
    stimulus = api.Stimulus(
        queues={"request": []}, slots=[reset, active],
        clock_schedule=schedule)
    driver = AtomicDriver(contract, stimulus)

    reset_drive = driver.drive(0)
    with pytest.raises(api.Refused, match="atomic-artifact-observation-malformed"):
        driver.observe(0, reset_drive, {"request_ready": object()})
    record = driver.observe(0, reset_drive, {
        "request_ready": "x",
        "result_valid": None,
        "result_data": "xxxxxxxx",
    })
    assert dict(record.accepted) == {}
    assert dict(record.outputs) == {}

    active_drive = driver.drive(1)
    with pytest.raises(api.Refused, match="driver-handshake-unreadable"):
        driver.observe(1, active_drive, {
            "request_ready": "x",
            "result_valid": 0,
            "result_data": 0,
        })
