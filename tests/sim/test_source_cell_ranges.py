"""Selected storage facts must survive siblings, never unknown writes."""

import ast

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp, ranges
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_sequences import _FunctionContracts, _ScalarBindings
from behave.sim.behavior.source_values import SCALAR, ValueContract


SOURCE = '''class M:
    def reset(self): self.counters = [0, 0]
    def adjust(self, which, value):
        self.counters[which] = value
        if value: return value
        return 0
    def process(self, a):
        self.counters[0] = 0
        self.counters[1] = a
        self.adjust(0, a + 1)
        if a & 1:
            self.adjust(1, 2)
        else:
            self.adjust(1, 3)
        result = self.counters[0] * 16
        while self.counters[1] > 0:
            self.counters[1] -= 1
            result += 1
        return {'y': result}
'''


def planner():
    model = source_model(SOURCE)
    return model.step, _FunctionContracts(model, ScopeResolver(model))


@pytest.mark.parametrize('array, root', [
    ('self.counters', 'self.counters'), ('items', '#element:items'),
    ('self.counters[0]', 'self.counters'),
])
@pytest.mark.parametrize('expression', [
    '{a}[i + 1] >= {a}[i]', '{a}[i + 1] < {a}[i]',
    '{a}[i + 1] == {a}[i]', '{a}[i + 1] != {a}[i]',
    '{a}[i + 1] - {a}[i] < 0', '{a}[i + 1] * {a}[i] < 0',
])
def test_array_envelope_does_not_equate_distinct_selections(array, root, expression):
    from behave.sim.behavior.source_sequences import _refine_scalar_ranges
    from behave.sim.behavior.source_values import SourceValueBudget

    fn, plan = planner()
    predicate = ast.parse(expression.format(a=array), mode='eval').body
    for index in (0, 1, 0):
        bindings = _ScalarBindings({'i': index}, scalar_bounds={
            root: ranges.Bounds(-5, 5), 'i': ranges.Bounds(index, index)})
        for truth in (False, True):
            # Both outcomes have concrete witnesses within the leaf hull.
            for result in (
                plan._refine_scalar_condition(fn, predicate, bindings, truth),
                _refine_scalar_ranges(predicate, plan.resolver.owner_of(fn),
                    bindings, truth, budget=SourceValueBudget()),
            ):
                assert result is not None
                assert result.bounds == bindings.bounds


@pytest.mark.parametrize('target', ['self.counters[i]', 'self.counters[-1]',
                                   'self.counters[:]', 'self.counters'])
def test_unknown_position_or_rebind_forgets_all_selected_facts(target):
    fn, plan = planner()
    bindings = _ScalarBindings(scalar_ranges={
        'self.counters': ranges.Range(0, 7),
        'self.counters[0]': ranges.Range(0, 0),
        'self.counters[1]': ranges.Range(7, 7),
    })
    sequence = ValueContract.sequence('list', 2, 2, SCALAR)
    env = {'#state:counters': sequence}
    node = ast.parse(target, mode='eval').body
    array = target.endswith('[:]') or target == 'self.counters'
    plan._target(fn, node, sequence if array else SCALAR, env, bindings,
                 interval=ranges.Range(3, 3), element_interval=ranges.Range(3, 3))
    assert not any('[' in name for name in bindings.bounds)


def test_sibling_store_is_strong_and_numeric_cache_tracks_selected_values():
    fn, plan = planner()
    env = {'#state:counters': ValueContract.sequence('list', 2, 2, SCALAR)}
    bindings = _ScalarBindings(scalar_ranges={
        'self.counters': ranges.Range(-10, 10),
        'self.counters[0]': ranges.Range(0, 0),
    })
    read = ast.parse('self.counters[0] + 1', mode='eval').body
    assert plan._bounds_of(fn, read, bindings) == ranges.Bounds(1, 1)
    plan._target(fn, ast.parse('self.counters[1]', mode='eval').body,
                 SCALAR, env, bindings, interval=ranges.Range(7, 7))
    assert plan._bounds_of(fn, read, bindings) == ranges.Bounds(1, 1)
    plan._target(fn, ast.parse('self.counters[0]', mode='eval').body,
                 SCALAR, env, bindings, interval=ranges.Range(3, 3))
    assert plan._bounds_of(fn, read, bindings) == ranges.Bounds(4, 4)


def test_selected_branch_refines_only_that_cell_and_cached_paths_stay_separate():
    fn, plan = planner()
    predicate = ast.parse('self.counters[i] > 0', mode='eval').body
    bounds = {'self.counters': ranges.Bounds(-5, 5),
              'self.counters[0]': ranges.Bounds(0, 3),
              'self.counters[1]': ranges.Bounds(-5, 5)}
    for index in (0, 1, 0):
        result = plan._refine_scalar_condition(
            fn, predicate, _ScalarBindings({'i': index}, scalar_bounds=bounds), True)
        selected = f'self.counters[{index}]'
        assert result.bounds[selected].lo == 1
        assert all(result.bounds[key] == value for key, value in bounds.items()
                   if key != selected)


def test_static_unknown_reuses_result_but_numeric_queries_keep_runtime_indices():
    fn, plan = planner()
    expression = ast.parse('self.counters[i] > limit', mode='eval').body
    for index in (0, 1):
        bindings = _ScalarBindings({'i': index, 'limit': 2}, scalar_ranges={
            'self.counters': ranges.Range(0, 7),
            'self.counters[0]': ranges.Range(0, 0),
            'self.counters[1]': ranges.Range(7, 7),
        })
        assert plan._static_truth(fn, expression, bindings) is None
        remaining = plan.budget.left
        assert plan._static_truth(fn, expression, bindings) is None
        assert plan.budget.left == remaining
        refined = plan._refine_scalar_condition(fn, expression, bindings, True)
        assert (refined is not None) is bool(index)
    assert len(plan._query_cache.truth) == 1


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_fixed_cells_across_helpers_branches_and_calls_match_native(lowerer):
    proc = lower_with_state_ranges(SOURCE, input_widths=[3], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(SOURCE)
    for first in range(8):
        for second in range(8):
            model = namespace['M']()
            model.reset()
            state = dict(proc.initial)
            for a in (first, second):
                hits.clear()
                expected = model.process(a)
                state, outputs, ir_hits, error = interp.run_step(proc, {'a': a}, state)
                assert not error and outputs.transactions == [expected]
                assert set(ir_hits) == set(hits)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('cutoff', [False, True])
def test_proven_empty_ranked_loop_evaluates_guard_and_else_without_helper_activation(lowerer, cutoff):
    source = '''class M:
    def reset(self): self.counts = [0, 0]
    def take(self, which): self.counts[which] -= 1
    def process(self, a):
        self.counts[0] = 0
        floor = a & 1
        while self.counts[0] > floor:
            self.take(0)
        else:
            self.counts[1] = a
        return {'y': self.counts[1]}
'''
    if cutoff:
        source = source.replace('while self.counts[0] > floor:',
            'i = 0\n        while 2 * i + 1 < 0:\n            i = 2 * i + 1')
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(source)
    for a in range(8):
        model = namespace['M']()
        model.reset()
        hits.clear()
        expected = model.process(a)
        _, outputs, ir_hits, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert set(ir_hits) == set(hits)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_helper_dead_nested_loop_keeps_alias_proof_and_live_activation(lowerer):
    source = '''class M:
    def reset(self): self.counts = [0, 0]
    def take(self, which):
        n = self.counts[which] - 1
        self.counts[which] = n
        if n > 0:
            while n > 0:
                n -= 1
        return n
    def process(self, a):
        self.counts[0] = 1
        self.counts[1] = a + 1
        x = self.take(0)
        return {'y': x + self.take(1)}
'''
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(source)
    for a in range(8):
        model = namespace['M']()
        model.reset()
        hits.clear()
        expected = model.process(a)
        _, outputs, ir_hits, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert set(ir_hits) == set(hits)
