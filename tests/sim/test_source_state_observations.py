"""State closure may omit layout-only snapshots, never transfer facts."""

from contextlib import contextmanager
import ast

import pytest

from behave.sim.symbolic import ranges
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.source_sequences import (
    _FunctionContracts, _ScalarBindings, _SourceQueryCache, _Spec, plan_sequences,
)
from behave.sim.behavior.source_values import SCALAR, contract_of_value


SOURCE = '''class M:
    def reset(self):
        self.count = 0
        self.q = []

    def put(self, a):
        self.count = self.count + 1
        self.q.append((a, self.count))
        return self.count

    def process(self, a):
        value = self.put(a)
        if a & 1:
            self.count = self.count + (a & 3)
        if len(self.q) < 3:
            return None
        out = []
        for x, n in self.q:
            out.append({"y": x + n + value})
        self.q = []
        return out
'''


@contextmanager
def _record_all_layers(monkeypatch):
    """Reference behavior: retain binding snapshots in preliminary layers too."""
    with monkeypatch.context() as patch:
        for name in ("_remember_fixed", "_replay_contract_query"):
            original = getattr(_FunctionContracts, name)

            def recorded(self, *args, _original=original):
                state_only = self.state_only
                self.state_only = False
                try:
                    return _original(self, *args)
                finally:
                    self.state_only = state_only

            patch.setattr(_FunctionContracts, name, recorded)
        yield


def _plan(model, bound):
    return plan_sequences(
        model, ScopeResolver(model), ("y",),
        declaration_contracts={"count": SCALAR, "q": contract_of_value([])},
        input_contracts={"a": SCALAR},
        input_ranges={"a": ranges.Range(0, 7), "count": ranges.Range(0, 0)},
        state_bound=bound)


def _specs(values):
    return {key: tuple(getattr(value, field) for field in _Spec.__slots__)
            for key, value in values.items()}


@pytest.mark.parametrize("bound", [0, 1, 3, 8])
def test_every_final_plan_field_matches_recording_all_state_layers(monkeypatch, bound):
    model = source_model(SOURCE)
    actual = _plan(model, bound)
    with _record_all_layers(monkeypatch):
        expected = _plan(model, bound)
    for field in actual.__slots__:
        left, right = getattr(actual, field), getattr(expected, field)
        if field == "_specs":
            left, right = _specs(left), _specs(right)
        assert left == right, field
    assert actual._expression_contracts
    assert actual._statement_contexts
    assert actual._branch_outcomes


def test_state_prepass_does_not_retain_discarded_binding_snapshots(monkeypatch):
    original = _FunctionContracts.summarize
    preliminary, final = [], []

    def checked(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if self.state_only:
            preliminary.append(self)
            assert not self._expression_fixed
            assert not self._expression_bounds
            # Numeric/shape facts are still needed by later statements.
            assert self._expression_contexts
        else:
            final.append(self)
        return result

    monkeypatch.setattr(_FunctionContracts, "summarize", checked)
    _plan(source_model(SOURCE), 8)
    assert preliminary and final
    assert final[-1]._expression_fixed
    assert final[-1]._expression_bounds


def test_final_layout_cannot_reuse_a_state_only_observation_summary():
    source = '''class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": a + 1 if a < 4 else a - 1}
'''
    model = source_model(source)
    node = model.step.body[0].value.values[0]
    resolver, shared = ScopeResolver(model), _SourceQueryCache()
    preliminary = _FunctionContracts(model, resolver, query_cache=shared, state_only=True)
    final = _FunctionContracts(model, resolver, query_cache=shared)
    cold = _FunctionContracts(model, resolver)
    for planner in (preliminary, final, cold):
        planner._context[:] = [("activation",)]
        planner._expr(model.step, node, {"a": SCALAR},
                      _ScalarBindings(scalar_bounds={"a": ranges.Bounds(0, 7)}))
    for child in ast.walk(node):
        if isinstance(child, ast.expr):
            assert final.fixed_bindings(child, ("activation",)) == (
                cold.fixed_bindings(child, ("activation",)))
            assert final.numeric_bounds(child, ("activation",)) == (
                cold.numeric_bounds(child, ("activation",)))
    assert final.numeric_bounds(node.body.left, ("activation",))["a"] == ranges.Bounds(0, 3)
