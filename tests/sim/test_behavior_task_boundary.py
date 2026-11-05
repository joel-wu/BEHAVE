"""One endpoint-preserving task boundary shared by Behavior consumers."""

from __future__ import annotations

import pytest

from behave.sim import api
from behave.sim.behavior.task_boundary import (
    TaskBoundaryError,
    accepted_input_valuation,
    accepted_output_streams,
    full_output_streams,
    lower_task_behavior,
    project_source_output_streams,
    task_procedure_boundary,
    task_signature_boundary,
)

U4 = api.ValueType.integer(api.FiniteType(4))
NESTED = api.ValueType.record({
    "mode": api.ValueType.enumeration(("idle", "run")),
    "rows": api.ValueType.array(2, api.ValueType.record({
        "flag": api.ValueType.boolean(),
        "x": U4,
    })),
})


def _endpoint(name, direction, field):
    return api.Endpoint(
        name, direction, [field],
        api.ready_valid(direction, f"{name}_valid", f"{name}_ready"),
        clock="clk", kind=api.Kind.TRANSACTION)


def _contract():
    return api.TaskContract(
        endpoints=[
            _endpoint("left", api.Direction.IN,
                      api.Field("payload", NESTED, "left")),
            _endpoint("right", api.Direction.IN,
                      api.Field("enable", api.ValueType.boolean(), "right")),
            _endpoint("result", api.Direction.OUT,
                      api.Field("data", api.FiniteType(5), "y")),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="two independent inputs form one complete Behavior call")


SOURCE = """\
class M:
    def reset(self):
        pass
    def process(self, left, right):
        return {"y": 0}
"""


def _value():
    return {
        "mode": "run",
        "rows": [
            {"flag": True, "x": 3},
            {"flag": False, "x": 7},
        ],
    }


def _two_output_contract():
    return api.TaskContract(
        endpoints=[
            _endpoint("left", api.Direction.IN,
                      api.Field("payload", NESTED, "left")),
            _endpoint("right", api.Direction.IN,
                      api.Field("enable", api.ValueType.boolean(), "right")),
            _endpoint("left_result", api.Direction.OUT,
                      api.Field("left_data", api.FiniteType(5), "y")),
            _endpoint("right_result", api.Direction.OUT,
                      api.Field("right_data", api.FiniteType(5), "z")),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="two endpoint-identified output streams")


TWO_OUTPUT_SOURCE = """\
class M:
    def reset(self):
        pass
    def process(self, left, right):
        return {"y": 5, "z": 9}
"""


def test_complete_simultaneous_endpoint_batch_preserves_recursive_values():
    boundary = task_procedure_boundary(SOURCE, "behavior.py", _contract())

    assert [(row.endpoint, row.fields) for row in boundary.inputs] == [
        ("left", (("payload", "left"),)),
        ("right", (("enable", "right"),)),
    ]
    got = accepted_input_valuation(
        boundary, {"right": (False,), "left": (_value(),)})

    assert got == {"left": _value(), "right": False}
    assert got["left"] is not _value()
    assert task_signature_boundary(_contract(), boundary.signature) == boundary


def test_silence_is_not_a_procedure_step():
    boundary = task_procedure_boundary(SOURCE, "behavior.py", _contract())
    assert accepted_input_valuation(boundary, {}) is None
    assert accepted_input_valuation(
        boundary, {"left": None, "right": None}) is None


def test_full_output_product_keeps_endpoint_identity_and_independent_timing():
    boundary = task_procedure_boundary(
        TWO_OUTPUT_SOURCE, "behavior.py", _two_output_contract())
    exact = full_output_streams(boundary, [{"y": 5, "z": 9}])
    coincident = accepted_output_streams(boundary, [{
        "left_result": (5,), "right_result": (9,),
    }])
    independently_delayed = accepted_output_streams(boundary, [
        {"right_result": (9,)}, {"left_result": (5,)},
    ])
    swapped = accepted_output_streams(boundary, [{
        "left_result": (9,), "right_result": (5,),
    }])

    assert coincident == independently_delayed == exact
    assert swapped != exact


def test_source_output_projection_decodes_signed_wire_patterns():
    contract = api.TaskContract(
        endpoints=[
            _endpoint("request", api.Direction.IN,
                      api.Field("a", api.FiniteType(1), "a")),
            _endpoint("response", api.Direction.OUT,
                      api.Field("y", api.FiniteType(4, signed=True), "y")),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="a signed source field may be returned as its wire pattern",
    )
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": 15 if a else -1}
"""
    boundary = task_procedure_boundary(source, "behavior.py", contract)

    projected = project_source_output_streams(boundary, [
        {"response": (15,)}, {"response": (-1,)},
    ])

    assert projected == {"response": [{"y": -1}, {"y": -1}]}
    assert projected["response"][:1] == full_output_streams(
        boundary, [{"y": -1}])["response"]


@pytest.mark.parametrize(
    ("accepted", "code"),
    [
        ({"left": (_value(),)}, "behavior-boundary-partial-input-batch"),
        ({"ghost": (1,)}, "behavior-boundary-input-endpoint-unknown"),
        ({"left": (_value(), 1), "right": (False,)},
         "behavior-boundary-input-payload-arity"),
    ],
)
def test_incomplete_or_unbound_endpoint_batches_are_typed(accepted, code):
    boundary = task_procedure_boundary(SOURCE, "behavior.py", _contract())
    with pytest.raises(TaskBoundaryError) as caught:
        accepted_input_valuation(boundary, accepted)
    assert caught.value.code == code


def _structured_output_contract():
    return api.TaskContract(
        endpoints=[
            _endpoint("request", api.Direction.IN,
                      api.Field("value", api.FiniteType(4), "a")),
            _endpoint("left_result", api.Direction.OUT,
                      api.Field("payload", NESTED, "left")),
            _endpoint("right_result", api.Direction.OUT,
                      api.Field("payload", NESTED, "right")),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="two recursive output products retain endpoint identity")


STRUCTURED_OUTPUT_SOURCE = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        return {
            "left": {
                "mode": "run",
                "rows": [
                    {"flag": True, "x": a},
                    {"flag": False, "x": 2},
                ],
            },
            "right": {
                "rows": [
                    {"x": 3, "flag": False},
                    {"x": 4, "flag": True},
                ],
                "mode": "idle",
            },
        }
"""


@pytest.mark.solver
def test_task_lowering_reconstructs_recursive_multiendpoint_outputs():
    instance, boundary = lower_task_behavior(
        STRUCTURED_OUTPUT_SOURCE, "behavior.py",
        _structured_output_contract(), 1)
    replay = instance.bind(1).replay([{"a": 7}])
    assert not replay.broken
    transaction = replay.outputs[0].transactions[0]
    assert transaction["left"] == {
        "mode": "run",
        "rows": [
            {"flag": True, "x": 7},
            {"flag": False, "x": 2},
        ],
    }
    assert transaction["right"] == {
        "mode": "idle",
        "rows": [
            {"flag": False, "x": 3},
            {"flag": True, "x": 4},
        ],
    }

    exact = full_output_streams(boundary, [transaction])
    observed = accepted_output_streams(boundary, [{
        "left_result": (transaction["left"],),
        "right_result": (transaction["right"],),
    }])
    swapped = accepted_output_streams(boundary, [{
        "left_result": (transaction["right"],),
        "right_result": (transaction["left"],),
    }])
    assert observed == exact
    assert swapped != exact


@pytest.mark.solver
def test_task_signature_represents_an_empty_bounded_output_batch():
    contract = api.TaskContract(
        endpoints=[
            _endpoint("request", api.Direction.IN,
                      api.Field("value", U4, "a")),
            _endpoint("response", api.Direction.OUT,
                      api.Field("y", U4, "y")),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="the declared output can occur only after this bounded domain",
    )
    source = """\
class M:
    def __init__(self):
        self.reset()
    def reset(self):
        self.state = 0
    def process(self, a):
        ret = None
        if self.state == 2:
            ret = {"y": a}
        self.state += 1
        return ret
"""

    instance, _boundary = lower_task_behavior(
        source, "behavior.py", contract, 1)
    replay = instance.bind(1).replay([{"a": 7}])

    assert not replay.broken
    assert replay.outputs[0].transactions == []


SWIZZLE_SOURCE = """\
class M:
    def __init__(self):
        self.width = 8
        self.reset()
    def reset(self):
        pass
    def process(self, data, selector):
        segment_count = 1 << selector
        segment_length = self.width // segment_count
        result = 0
        for segment in range(segment_count):
            base = segment * segment_length
            for index in range(segment_length):
                bit = (data >> (base + index)) & 1
                result |= bit << (base + segment_length - 1 - index)
        return {"result": result, "gray": result ^ (result >> 1)}
"""


def _swizzle_contract():
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "request", api.Direction.IN,
                [api.Field("data", api.FiniteType(8), "data"),
                 api.Field("selector", api.FiniteType(2), "selector")],
                api.ready_valid(api.Direction.IN, "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "response", api.Direction.OUT,
                [api.Field("result", api.FiniteType(8), "result"),
                 api.Field("gray", api.FiniteType(8), "gray")],
                api.ready_valid(api.Direction.OUT, "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="nested data-dependent loops at a typed transaction boundary")


@pytest.mark.solver
def test_task_lowering_uses_cfg_facts_inside_nested_data_dependent_loops():
    instance, boundary = lower_task_behavior(
        SWIZZLE_SOURCE, "behavior.py", _swizzle_contract(), 1)
    replay = instance.bind(1).replay([
        {"data": 0b11010010, "selector": selector}
        for selector in range(4)
    ])
    assert not replay.broken
    assert boundary.signature.input_names == ("data", "selector")
    assert [step.transactions[0] for step in replay.outputs] == [
        {"result": 0b01001011, "gray": 0b01101110},
        {"result": 0b10110100, "gray": 0b11101110},
        {"result": 0b11100001, "gray": 0b10010001},
        {"result": 0b11010010, "gray": 0b10111011},
    ]


@pytest.mark.solver
def test_semantic_batch_capacity_precedes_the_exact_representation_budget():
    from behave.sim.symbolic.encoder import EncodingError

    source = """\
class M:
    def reset(self):
        pass
    def process(self, n, m):
        return [{"y": i} for i in range(n) for _ in range(m)]
"""
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "request", api.Direction.IN,
                [api.Field("n", api.FiniteType(20), "n"),
                 api.Field("m", api.FiniteType(20), "m")],
                api.ready_valid(api.Direction.IN, "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "response", api.Direction.OUT,
                [api.Field("y", api.FiniteType(20), "y")],
                api.ready_valid(
                    api.Direction.OUT, "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="large finite batch regression",
    )

    instance, _boundary = lower_task_behavior(
        source, "behavior.py", contract, 1)

    assert instance.proc.output_capacity == ((1 << 20) - 1) ** 2
    assert instance.well_formed() == []
    with pytest.raises(EncodingError, match="representation-budget-exceeded") as caught:
        instance.bind(1)
    assert caught.value.kind == "capability"
