"""Finite-table termination preserves source execution and every native hit."""

import pytest

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


SOURCE = '''class M:
    def reset(self): self.last = 0
    def process(self, a):
        costs = [a & 3, (a >> 2) & 3, (a >> 4) & 3]
        at = 0
        best = costs[at]
        changed = 1
        while changed:
            changed = 0
            for j in range(3):
                neighbor = (at + 1) % 3
                if costs[neighbor] < best:
                    best = costs[neighbor]
                    at = neighbor
                    changed = 1
        else:
            self.last = best
        return {'y': best * 4 + at}
'''

LAYERED = '''class M:
    def reset(self): pass
    def process(self, a):
        costs = [a & 3, (a >> 2) & 3, (a >> 4) & 3]
        at = 0
        layer = a >> 6
        while layer:
            previous = at
            for j in range(3):
                target = (at + layer) % 3
                if costs[target] < costs[at]:
                    at = target
            if previous == at:
                layer -= 1
        return {'y': at}
'''

WORKLIST = '''class M:
    def reset(self): self.seen = 0
    def process(self, a):
        pending = 1
        seen = 0
        visits = 0
        while pending:
            pending -= 1
            visits += 1
            for j in range(4):
                target = (a >> (2 * j)) & 3
                if not ((seen >> target) & 1):
                    seen |= 1 << target
                    if target != 3:
                        pending += 1
        self.seen = seen
        return {'y': visits}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
def test_visited_worklist_is_finite_without_monotone_queue_size(lowerer):
    proc = lower_with_state_ranges(WORKLIST, input_widths=[8], outputs=['y'], lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(WORKLIST)
    for a in range(256):
        model = namespace['M']()
        model.reset()
        native_hits.clear()
        expected = model.process(a)
        _, outputs, hits, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert set(hits) == set(native_hits)


@pytest.mark.parametrize('old,new', [
    ('pending -= 1', 'pending += 0'),
    ('pending -= 1', 'if a: pending -= 1'),
    ('pending -= 1', 'if a: continue\n            pending -= 1'),
    ('seen |= 1 << target', 'seen = 1 << target'),
    ('seen |= 1 << target', 'seen |= 1 << target\n                    seen = 0'),
    ('pending += 1', 'pending += 2'),
    ('pending += 1', 'for k in range(3): pending += 1'),
    ('(a >> (2 * j)) & 3', 'visits'),
    ('(a >> (2 * j)) & 3', 'visits - 1'),
    ('target != 3', 'target != 3 or (seen := 0)'),
])
def test_worklist_must_prove_finite_unique_admissions(old, new):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(WORKLIST.replace(old, new), input_widths=[8], outputs=['y'])


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
def test_conditional_counter_or_indexed_descent(lowerer):
    proc = lower_with_state_ranges(LAYERED, input_widths=[8], outputs=['y'], lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(LAYERED)
    for a in range(256):
        model = namespace['M']()
        model.reset()
        native_hits.clear()
        expected = model.process(a)
        _, outputs, hits, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert set(hits) == set(native_hits)


@pytest.mark.parametrize('old,new', [
    ('previous == at', 'previous != at'),
    ('previous = at', 'previous = 17'),
    ('layer -= 1', 'layer += 1'),
    ('layer = a >> 6', 'layer = -1'),
    ('for j in range(3)', 'for previous in range(3)'),
    ('for j in range(3)', 'for layer in range(3)'),
    ('at = target', 'at = target\n                previous = 17'),
    ('previous = at', 'previous = at\n            if a: continue'),
])
def test_counter_descent_cannot_skip_progress(old, new):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(LAYERED.replace(old, new), input_widths=[8], outputs=['y'])


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('order', ['<', '>'])
@pytest.mark.parametrize('control', ['', 'if at == 2: break',
                                      'if at == 2: continue',
                                      "if at == 2: return {'y': best * 4 + at}"])
def test_finite_table_loop_matches_native(lowerer, order, control):
    source = SOURCE.replace('< best', order + ' best')
    if control:
        source = source.replace('                    changed = 1',
                                '                    changed = 1\n            ' + control)
    proc = lower_with_state_ranges(source, input_widths=[6], outputs=['y'],
                                   bound=2, lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    histories = [[{'a': a}, {'a': 63 - a}] for a in range(64)]
    for history in histories:
        model = namespace['M']()
        model.reset()
        state = dict(proc.initial)
        for valuation in history:
            native_hits.clear()
            expected = model.process(**valuation)
            state, outputs, hits, error = interp.run_step(proc, valuation, state)
            assert not error
            assert outputs.transactions == [expected]
            assert set(hits) == set(native_hits)
    plain = {}
    exec(source, plain)
    assert audit.model_audit(proc, plain['M'], histories) == []


@pytest.mark.parametrize('old,new', [
    ('changed = 0', 'changed = 1'),
    ('< best', '<= best'),
    ('best = costs[neighbor]', 'best = best + 1'),
    ('changed = 0', 'changed = 0; costs[0] = a'),
    ('changed = 0', 'changed = 0; alias = costs; alias[0] = a'),
    ('changed = 0', 'changed = 0; costs += [a]'),
    ('changed = 0', 'changed = 0; costs = [a]'),
    ('changed = 0', 'changed = 0; self.reset()'),
    ('for j in range(3)', 'for changed in range(3)'),
    ('for j in range(3)', 'for best in range(3)'),
    ('else:\n            self.last', '    changed = 1\n        else:\n            self.last'),
])
@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
def test_unproved_progress_or_mutated_table_is_not_truncated(old, new, lowerer):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(SOURCE.replace(old, new), input_widths=[6],
                                outputs=['y'], lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('source,width', [(SOURCE, 6), (LAYERED, 8), (WORKLIST, 8)],
                         ids=['flag', 'layered', 'worklist'])
def test_all_finite_descent_goals_are_queried_and_native_confirmed(source, width):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[width], outputs=['y'], bound=2)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=2)
    result = instance.query(instance.goals(), 2)
    assert not result.undecided
    namespace, _, hits = instrumented.load_source(source)
    expected = set()
    for a in range(1 << width):
        model = namespace['M']()
        model.reset()
        hits.clear()
        model.process(a)
        expected.update(hits)
    assert set(result.reachable) == expected
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'
