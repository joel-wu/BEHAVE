"""Infinity sentinels are ordered tags, never bounded integer approximations."""
import ast
import itertools
from types import SimpleNamespace

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior import lower_ordered_scalars
from tests.sim.test_source_closed_bindings import agrees

SOURCE = '''class M:
    def reset(self): pass
    def process(self, a, b):
        best = float('inf')
        winner = 0
        for i in range(4):
            distance = (a - i) * (a - i) + b
            if distance < best:
                best = distance
                winner = i
        return {'y': winner}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_infinity_minimum_loop_outputs_and_hits(lowerer):
    proc = lower_with_state_ranges(SOURCE, input_widths=[3, 3], outputs=['y'],
                                  bound=1, lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(SOURCE)
    for a, b in itertools.product(range(8), repeat=2):
        native = namespace['M']()
        native.reset()
        hits.clear()
        expected = native.process(a, b)
        _, outputs, observed, error = interp.run_step(proc, {'a': a, 'b': b}, dict(proc.initial))
        assert not error
        assert outputs.transactions == [expected]
        assert set(observed) == set(hits)


@pytest.mark.isolation
@pytest.mark.solver
def test_infinity_ordering_goals_query_and_native_audit():
    from behave.sim.behavior.instance import BehaviorInstance
    proc = lower_with_state_ranges(SOURCE, input_widths=[3, 3], outputs=['y'], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=SOURCE, bound=1)
    result = instance.query(instance.goals(), 1)
    assert not result.undecided
    assert result.reachable
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.parametrize('operator', ['+', '-', '*', '//', '&'])
def test_infinity_is_not_silently_integer_arithmetic(operator):
    source = SOURCE.replace("return {'y': winner}", "return {'y': float('inf') " + operator + " a}")
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[3, 3], outputs=['y'], bound=1)


@pytest.mark.parametrize('operator', ['<', '<=', '==', '!=', '>=', '>'])
@pytest.mark.parametrize('sign', ['inf', '-Infinity'])
def test_infinity_and_arbitrary_integer_ordering(operator, sign):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        if a: bound = 1 << 256
        else: bound = float('{sign}')
        saved = bound
        bound = -7
        return {{'y': int(saved {operator} (1 << 256))}}
'''
    proc = lower_with_state_ranges(source, input_widths=[1], outputs=['y'], bound=1)
    namespace, _, hits = instrumented.load_source(source)
    for a in (0, 1):
        native = namespace['M']()
        native.reset()
        hits.clear()
        expected = native.process(a)
        _, outputs, observed, error = interp.run_step(proc, {'a': a}, dict(proc.initial))
        assert not error
        assert outputs.transactions == [expected]
        assert set(observed) == set(hits)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('operator', ['<', '<=', '==', '!=', '>=', '>'])
@pytest.mark.parametrize('right', ["float('-inf')", 'a == 1', '1 << 256', "float('inf')"])
def test_ordered_alias_keeps_both_columns_when_original_is_rebound(lowerer, operator, right):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        if a == 0: value = float('-inf')
        elif a == 1: value = float('inf')
        else: value = 1 << 256
        saved = value
        value = a - 1
        return {{'y': int(saved {operator} ({right})) + 2 * int(value == a - 1)}}
''', lowerer, bound=1)


def test_ordered_local_discovery_is_cached_per_invocation_and_source_scope(monkeypatch):
    outer = ast.parse('''def process(a):
    saved = -bound
    bound: int = float('inf') if a else 7
    choice = saved or 0
    def helper():
        hidden = float('-inf')
''').body[0]
    nested = outer.body[-1]
    walks = []
    walk = lower_ordered_scalars._walk_function_scope

    def counted(scope):
        walks.append(scope)
        return walk(scope)

    monkeypatch.setattr(lower_ordered_scalars, '_walk_function_scope', counted)
    low = SimpleNamespace(activation=SimpleNamespace(scope=None), ordered_locals={},
                          _spelling_is_builtin=lambda name: name == 'float')
    assert not lower_ordered_scalars.is_local(low, 'bound')
    assert walks == []
    low.activation.scope = outer
    for name in ('saved', 'bound', 'choice'):
        assert lower_ordered_scalars.is_local(low, name)
    assert not lower_ordered_scalars.is_local(low, 'hidden')
    assert walks == [outer]
    low.activation.scope = nested
    assert lower_ordered_scalars.is_local(low, 'hidden')
    assert not lower_ordered_scalars.is_local(low, 'bound')
    assert walks == [outer, nested]
    # The same source in another invocation cannot inherit this classification:
    # here float resolves to a user binding, not the builtin.
    shadowed = SimpleNamespace(activation=SimpleNamespace(scope=outer), ordered_locals={},
                               _spelling_is_builtin=lambda _name: False)
    assert not lower_ordered_scalars.is_local(shadowed, 'bound')
    assert shadowed.ordered_locals[outer] == set()
    assert low.ordered_locals[outer] == {'saved', 'bound', 'choice'}
    assert walks == [outer, nested, outer]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_nested_infinity_local_does_not_change_outer_sequence_binding(lowerer):
    agrees('''class M:
    def reset(self): pass
    def process(self, a):
        def helper(x):
            value = float('inf')
            return int(x < value)
        value = [a, 1]
        return {'y': sum(value) + helper(a)}
''', lowerer, bound=2)
