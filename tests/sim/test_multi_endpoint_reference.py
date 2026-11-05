"""The explicit slot boundary for concrete multi-endpoint references."""

from __future__ import annotations

import pytest

from behave.sim import api
from behave.sim.core.reference import SlotEvent
from behave.sim.core.refusal import Refused
from tests.support.reference import load

U8 = api.FiniteType(8)


def _contract():
    endpoints = [
        api.Endpoint(
            "left", api.Direction.IN,
            [api.Field("value", U8, "left_data")],
            api.ready_valid("in", "left_valid", "left_ready"),
            clock="clk", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "right", api.Direction.IN,
            [api.Field("value", U8, "right_data")],
            api.ready_valid("in", "right_valid", "right_ready"),
            clock="clk", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "sum", api.Direction.OUT,
            [api.Field("value", U8, "sum_data")],
            api.ready_valid("out", "sum_valid", "sum_ready"),
            clock="clk", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "difference", api.Direction.OUT,
            [api.Field("value", U8, "difference_data")],
            api.ready_valid("out", "difference_valid", "difference_ready"),
            clock="clk", kind=api.Kind.TRANSACTION),
    ]
    return api.TaskContract(
        endpoints, [api.ClockDomain("clk", "clk", "rst")],
        source="two independent ready/valid inputs and outputs")


def _slot_reference():
    return load({"behavior.py": b"""\
class Model:
    def reset(self):
        self.calls = 0

    def process_slot(self, slot):
        self.calls += 1
        inputs = slot["inputs"]
        if "left" not in inputs or "right" not in inputs:
            return None
        left = inputs["left"]["left_data"]
        right = inputs["right"]["right_data"]
        return {
            "sum": {"sum_data": (left + right) & 0xff},
            "difference": [
                {"difference_data": (left - right) & 0xff},
                {"difference_data": (right - left) & 0xff},
            ],
        }
"""}, config={"class": "Model"})


def test_a_slot_reference_preserves_simultaneous_inputs_and_output_endpoints():
    model = _slot_reference().instantiate(_contract())
    model.reset()
    got = model.advance_by_endpoint(SlotEvent(
        3, ticks=("clk",), controls=(("sum_ready", 1),),
        accepted_inputs=(("left", (7,)), ("right", (2,)))))

    assert got == {"sum": [(9,)], "difference": [(5,), (251,)]}


def test_a_transaction_reference_is_not_serialised_across_several_endpoints():
    ordinary = load({"behavior.py": b"""\
class Model:
    def reset(self):
        pass

    def process(self, value):
        return {"sum_data": value}
"""}, config={"class": "Model"})

    with pytest.raises(Refused, match="reference-slot-interface-missing"):
        ordinary.instantiate(_contract())


def test_the_isolated_protocol_judge_declares_process_slot_without_importing_it():
    from behave.sim.behavior.isolated import _source_protocol_image

    image = _source_protocol_image("""\
class Model:
    def reset(self):
        pass

    def process_slot(self, slot):
        return None
""", cls="Model")

    assert image["facts"]["cls_name"] == "Model"
    assert image["offers"] == ["reset", "process_slot"]
