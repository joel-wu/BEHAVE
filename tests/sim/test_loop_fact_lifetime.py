"""Numeric loop transfer must not resurrect expired helper activations."""

import ast
import itertools

import pytest

from behave.sim.behavior import flow_facts
from behave.sim.symbolic import ranges
from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.isolation.preparation_control import PreparationCancelled
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges


def facts(finite, bounds=None, unknown=()):
    return flow_facts.FlowFacts({}, {}, {}, finite,
                      {name: ranges.Bounds.of(value) for name, value in finite.items()}
                      if bounds is None else bounds, frozenset(unknown), {})


def test_loop_transfer_keeps_live_names_but_not_expired_helper_temporaries():
    low = _Lowerer(())
    before = facts({'source': ranges.Range(0, 1)},
                   {'source': ranges.Bounds(0, 1), 'open': ranges.Bounds(0, None)},
                   ('unproved',))
    transfer = {'source': ranges.Range(2, 3), 'open': ranges.Range(0, 10),
                'unproved': ranges.Range(1, 4), 'expired_helper': ranges.Range(3, 3)}
    low._set_joined_flow_facts([before], transfer)
    assert low.flow.ranges == {name: value for name, value in transfer.items()
                       if name != 'expired_helper'}
    assert low.flow.bounds == {name: ranges.Bounds.of(value) for name, value in low.flow.ranges.items()}
    assert not low.flow.unknown_ranges


def test_live_one_sided_names_keep_transfer_precision_and_missing_values_stay_unknown():
    low = _Lowerer(())
    left = facts({'left': ranges.Range(0, 1), 'missing': ranges.Range(0, 3)})
    right = facts({'right': ranges.Range(2, 4)})
    low._set_joined_flow_facts([left, right],
                             {'left': ranges.Range(0, 8), 'right': ranges.Range(2, 9)})
    assert low.flow.ranges == {'left': ranges.Range(0, 8), 'right': ranges.Range(2, 9)}
    assert low.flow.unknown_ranges == {'missing'}


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('stop', [None, RuntimeError, PreparationCancelled])
def test_loop_scope_owns_controls_facts_and_indices_and_restores_together(lowerer, stop):
    low = lowerer(())
    owner = object()
    low.activation.destination = owner
    with low._loop_scope('outer-run', 'outer-continue', indices=('row',),
                         exit_label='outer-exit') as outer:
        outer.breaks.append(low._flow_facts())
        try:
            with low._loop_scope('inner-run', indices=(), exit_label='inner-exit') as inner:
                assert inner.parent is outer and inner.return_destination is owner
                assert inner.running == 'inner-run' and inner.continuing is None
                assert inner.exit_label == 'inner-exit' and inner.indices == ()
                assert inner.contains_index('row') and not inner.contains_index('absent')
                assert inner.breaks == inner.continues == []
                inner.continues.append(low._flow_facts())
                if stop is not None:
                    raise stop('body stopped')
        except (RuntimeError, PreparationCancelled) as error:
            assert type(error) is stop
        assert low.loop is outer
        assert len(outer.breaks) == 1 and outer.continues == []
        assert inner.breaks == [] and len(inner.continues) == 1
    assert low.loop is None


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
def test_empty_inner_index_scope_cannot_drop_outer_structured_index_protection(lowerer):
    low = lowerer((), arrays={'row': (2,)})
    target = ast.parse('row[0]', mode='eval').body
    with low._loop_scope(None, indices=('row',)):
        with low._loop_scope(None, indices=()):
            with pytest.raises(LoweringError) as error:
                low._require_writable_target(target)
            assert error.value.code == 'loop-index-assignment'
        with pytest.raises(LoweringError):
            low._require_writable_target(target)
    low._require_writable_target(target)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
def test_break_and_continue_collect_only_innermost_loop_facts(lowerer):
    low = lowerer(())
    with low._loop_scope('outer-run', 'outer-continue', exit_label='outer-exit') as outer:
        with low._loop_scope('inner-run', 'inner-continue', exit_label='inner-exit') as inner:
            low.stmt(ast.Break())
            low.stmt(ast.Continue())
            assert len(inner.breaks) == len(inner.continues) == 1
            assert outer.breaks == outer.continues == []
        low.stmt(ast.Break())
        assert len(outer.breaks) == 1 and outer.continues == []


@pytest.mark.parametrize('stop', [RuntimeError, PreparationCancelled])
def test_graph_loop_failure_restores_parent_graph_and_shared_scope(stop):
    low = GraphLowerer(())
    label, instructions, blocks = low.label, low._instrs, dict(low.blocks)
    node = ast.parse('for i in range(2): pass').body[0]

    def interrupted_body():
        assert low.loop.exit_label is not None
        assert low.loop.indices == ('i',)
        raise stop('body stopped')

    with low._loop_scope('outer-run', exit_label='outer-exit') as parent:
        with pytest.raises(stop, match='body stopped'):
            low._loop_region(node, low._flow_facts(), ('i',), 'inner-run',
                             lower_body=interrupted_body)
        assert low.loop is parent
        assert low.label == label and low._instrs is instructions
        assert low.blocks == blocks
    assert low.loop is None


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('length', [1, 3, 8])
def test_repeated_helper_comprehensions_preserve_values_state_and_fact_lifetimes(monkeypatch, lowerer, length):
    source = f'''class M:
    def reset(self): self.saved = 0
    def helper(self, x):
        local = x * 3
        if x & 1:
            return local + 1
        return local - 2
    def process(self, a):
        xs = [self.helper(a + i) for i in range({length})]
        ys = [self.helper(x) for x in xs]
        total = self.saved
        for i in range({length}):
            if i == a:
                continue
            total += ys[i]
        self.saved = total
        return {{"y": total}}
'''
    original = lowerer._set_joined_flow_facts
    checked = []

    def joined(self, states, ranges_override=None):
        live = set().union(*(set(state.ranges) | set(state.bounds) | set(state.unknown_ranges)
                             for state in states))
        original(self, states, ranges_override)
        if ranges_override is not None:
            checked.append(len(ranges_override) - len(live & ranges_override.keys()))
            assert set(self.flow.ranges) <= live

    monkeypatch.setattr(lowerer, '_set_joined_flow_facts', joined)
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=2, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102 -- fixed fixture
    histories = [[{'a': value} for value in values]
                 for values in itertools.product(range(4), repeat=2)]
    assert model_audit(proc, namespace['M'], histories) == []
    assert checked and max(checked) > 0
