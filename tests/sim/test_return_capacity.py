"""A return's backing capacity covers every visit, not only its last visit."""

import ast

import pytest

from behave.sim.symbolic import interp
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_sequences import plan_sequences
from behave.sim.behavior.source_values import SCALAR


def source(count):
    return f"""class M:
    def reset(self):
        pass
    def process(self, a):
        for index in range(3):
            if a == index:
                return [{{'y': index * 10 + offset}} for offset in range({count})]
        return []
"""


def partitioned_source(first):
    return f"""class M:
    def reset(self):
        pass
    def process(self, a):
        outputs = [{{'y': 0}}]
        if {first}:
            outputs.append({{'y': 1}})
        if a:
            outputs.append({{'y': 2}})
        return outputs
"""


@pytest.mark.parametrize("first", ("a", "not a"))
def test_shared_return_covers_all_retained_control_paths(first):
    text = partitioned_source(first)
    model = source_model(text)
    plan = plan_sequences(model, ScopeResolver(model), ("y",),
                          input_contracts={"a": SCALAR})
    node = next(node for node in ast.walk(model.step) if isinstance(node, ast.Return))
    assert plan.capacity_of_return(node) == plan.return_plan(node).contract.maximum
    procedure = lower_with_state_ranges(text, input_widths=[1], outputs=["y"], bound=1)
    namespace = {}
    exec(compile(text, "behavior.py", "exec"), namespace)  # noqa: S102
    for value in (0, 1):
        native = namespace["M"]()
        native.reset()
        _state, output, _hits, error = interp.run_step(procedure, {"a": value})
        assert not error
        assert output.transactions == native.process(value)


@pytest.mark.parametrize("count", ("3 - index", "index + 1"))
def test_return_capacity_uses_the_joined_occurrence_plan(count):
    model = source_model(source(count))
    plan = plan_sequences(model, ScopeResolver(model), ("y",),
                          input_contracts={"a": SCALAR})
    returns = [node for node in ast.walk(model.step) if isinstance(node, ast.Return)]
    for node in returns:
        joined = plan.return_plan(node)
        assert joined is not None
        assert plan.capacity_of_return(node) == joined.contract.maximum
        assert plan.output_capacity >= joined.contract.maximum
    assert plan.output_capacity == 3


@pytest.mark.parametrize("lowerer", (_Lowerer, GraphLowerer), ids=("term", "graph"))
@pytest.mark.parametrize("count", ("3 - index", "index + 1"))
def test_complete_lowering_preserves_each_return_batch(count, lowerer):
    text = source(count)
    procedure = lower_with_state_ranges(text, input_widths=[2], outputs=["y"],
                                         bound=1, lowerer=lowerer)
    namespace = {}
    exec(compile(text, "behavior.py", "exec"), namespace)  # noqa: S102
    for value in range(4):
        native = namespace["M"]()
        native.reset()
        expected = native.process(value)
        _state, output, _hits, error = interp.run_step(procedure, {"a": value})
        assert not error
        assert output.transactions == expected
