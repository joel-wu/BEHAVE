"""Truth-tested scalar loops reuse the proved zero-exit recurrence, not a cap."""

import ast

import pytest

from behave.sim.behavior import audit, instrumented, while_plan
from behave.sim.symbolic import interp, ranges
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


def test_truth_progress_does_not_rewrite_source_or_invent_comparison_goals():
    loop = ast.parse('while x:\n    x >>= 2').body[0]
    original = ast.dump(loop, include_attributes=True)
    progress = while_plan.analyse(loop)
    assert ast.dump(loop, include_attributes=True) == original
    assert progress.name == 'x' and progress.not_equal
    for value in range(65):
        expected = (value.bit_length() + 1) // 2
        assert while_plan.trip_span(progress, ranges.Range(value, value),
                                    ranges.Range(0, 0)) == ranges.Range(expected, expected)


CASES = [
    ('shift', 'x = a', 'x >>= 1', ''),
    ('stride', 'x = a', 'x = x >> 2', ''),
    ('decrement', 'x = a', 'x -= 1', ''),
    ('binary-parent', 'x = a', 'x = (x - 1) // 2', ''),
    ('binary-parent-temporary', 'x = a', 'parent = (x - 1) // 2; x = parent', ''),
    ('negative-to-zero', 'x = -a', 'x += 1', ''),
    ('break', 'x = a', 'x >>= 1', 'if x == 2: break'),
    ('continue', 'x = a', 'x >>= 1', 'if x == 2: continue'),
]


@pytest.mark.parametrize('update', ['x = (x - 1) // 2', 'x >>= 2'])
@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_positive_guard_excludes_negative_entry_without_changing_zero_trip_hits(update, lowerer):
    source = source_for('x = a - 16', update, '').replace('while x:', 'while x > 0:')
    proc = lower_with_state_ranges(source, input_widths=[5], outputs=['y'], lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    for a in range(32):
        model = namespace['M']()
        model.reset()
        native_hits.clear()
        expected = model.process(a)
        _, outputs, hits, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert set(hits) == set(native_hits)
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source.replace('while x > 0:', 'while x:'),
                                input_widths=[5], outputs=['y'], lowerer=lowerer)


@pytest.mark.parametrize('update', ['x = (x - 1) // 2', 'x >>= 2'])
def test_positive_guard_needs_only_an_upper_entry_bound(update):
    progress = while_plan.analyse(ast.parse(f'while x > 0:\n    {update}').body[0])
    maximum = 5 if progress.operation == 'parent' else 3
    assert while_plan.trip_span(progress, ranges.Bounds(None, 31), ranges.Range(0, 0)) == ranges.Range(0, maximum)


def source_for(initial, update, control):
    return f'''class M:
    def reset(self): self.total = 0
    def process(self, a):
        {initial}
        while x:
            {update}
            {control or 'pass'}
            self.total += x
        else:
            self.total += 17
        return {{'y': self.total}}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('name,initial,update,control', CASES, ids=[c[0] for c in CASES])
def test_truth_loop_outputs_state_and_native_hits(lowerer, name, initial, update, control):
    source = source_for(initial, update, control)
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    for first in range(8):
        for second in range(8):
            native = namespace['M']()
            native.reset()
            state = dict(proc.initial)
            for value in (first, second):
                native_hits.clear()
                expected = native.process(value)
                state, outputs, hits, error = interp.run_step(proc, {'a': value}, state)
                assert not error
                assert outputs.transactions == [expected], (name, first, second)
                assert set(hits) == set(native_hits), (name, value)
    plain = {}
    exec(compile(source, 'behavior.py', 'exec'), plain)
    assert audit.model_audit(proc, plain['M'],
                             [[{'a': a}, {'a': b}] for a in range(8) for b in range(8)]) == []


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_state_and_helper_inductions_keep_the_original_read_sites(lowerer):
    source = '''class M:
    def reset(self): self.x = 0
    def decode(self, x):
        result = 0
        while x:
            result ^= x
            x >>= 1
        return result
    def process(self, a):
        self.x = a
        result = self.decode(a)
        while self.x:
            result += self.x
            self.x >>= 1
        return {'y': result}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=['y'], lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(source)
    for value in range(16):
        native = namespace['M']()
        native.reset()
        hits.clear()
        expected = native.process(value)
        _, outputs, ir_hits, error = interp.run_step(proc, {'a': value})
        assert not error and outputs.transactions == [expected]
        assert ir_hits == set(hits)


@pytest.mark.parametrize('initial,update', [
    ('-a - 1', 'x >>= 1'), ('a', 'x >>= 0'), ('a', 'x <<= 1'),
    ('a', 'x -= 2'), ('a', 'x += 1'), ('a', 'x >>= 1; x = a'),
    ('a', 'parent = (x - 1) // 2; x -= 1; x = parent'),
    ('a', 'parent = (x - 1) // 2; parent = a; x = parent'),
    ('a', 'parent = (x - 1) // 2; x = parent; x = parent'),
])
@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_unproved_termination_is_not_truncated(initial, update, lowerer):
    source = source_for('x = ' + initial, update, '')
    with pytest.raises(LoweringError, match='while'):
        lower_with_state_ranges(source, input_widths=[3], outputs=['y'], lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('name,initial,update,control', CASES, ids=[c[0] for c in CASES])
def test_every_truth_loop_goal_can_be_queried_and_sat_is_native_confirmed(
        name, initial, update, control):
    from behave.sim.behavior.instance import BehaviorInstance

    source = source_for(initial, update, control)
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    result = instance.query(instance.goals(), 2)
    assert not result.undecided, name
    namespace, _, hits = instrumented.load_source(source)
    expected = set()
    for value in range(8):
        native = namespace['M']()
        native.reset()
        hits.clear()
        native.process(value)
        expected.update(hits)
    assert set(result.reachable) == expected
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'
