"""Nested backing capacity changes must not change any logical sequence length."""

import itertools

import pytest

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


def source_for(carrier):
    empty = 'bytearray()' if carrier == 'bytearray' else '[]'
    row = 'bytearray([a, a + 1])' if carrier == 'bytearray' else '[a, a + 1]'
    return f'''class M:
    def reset(self): self.rows = [{empty}, {empty}]
    def process(self, a):
        if a == 0:
            self.reset()
        else:
            self.rows[a & 1] = {row}
        y = len(self.rows[0]) + 10 * len(self.rows[1])
        if len(self.rows[0]): y += 100 * self.rows[0][-1]
        if len(self.rows[1]): y += 1000 * self.rows[1][-1]
        return {{'y': y}}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('carrier', ['list', 'bytearray'])
def test_nested_empty_reset_retains_lengths_values_state_and_goal_hits(lowerer, carrier):
    source = source_for(carrier)
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   lowerer=lowerer, bound=3)
    namespace, _, hits = instrumented.load_source(source)
    histories = [[{'a': a} for a in values]
                 for values in itertools.product(range(4), repeat=3)]
    for history in histories:
        native = namespace['M']()
        native.reset()
        state = dict(proc.initial)
        for inputs in history:
            hits.clear()
            expected = native.process(**inputs)
            state, outputs, ir_hits, error = interp.run_step(proc, inputs, state)
            assert not error and outputs.transactions == [expected]
            assert set(ir_hits) == set(hits)
    plain = {}
    exec(compile(source, 'behavior.py', 'exec'), plain)
    assert audit.model_audit(proc, plain['M'], histories) == []


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('carrier', ['list', 'bytearray'])
def test_nested_storage_queries_replay_the_original_python(carrier):
    from behave.sim.behavior.instance import BehaviorInstance

    source = source_for(carrier)
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=3)
    result = instance.query(instance.goals(), 3)
    assert not result.undecided
    assert result.reachable
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_padding_never_makes_an_empty_row_index_valid(lowerer):
    source = '''class M:
    def reset(self): self.rows = [[], []]
    def process(self, a):
        if a: self.rows[0] = [a, a + 1]
        else: self.reset()
        return {'y': self.rows[0][0]}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   lowerer=lowerer, bound=2)
    state, _, _, error = interp.run_step(proc, {'a': 1})
    assert not error
    _, _, _, error = interp.run_step(proc, {'a': 0}, state)
    assert error == 'IndexError'
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    assert audit.model_audit(proc, namespace['M'], [[{'a': 1}, {'a': 0}]]) == []


def test_recursive_padding_without_its_length_evidence_is_rejected(monkeypatch):
    original = _Lowerer._coerce_sequence_storage

    def lose_lengths(self, source, value, actual, storage):
        result = original(self, source, value, actual, storage)
        if actual.physical_shape != storage.physical_shape and len(storage.physical_shape) == 2:
            self.storage.length_images.pop(result.name, None)
        return result

    monkeypatch.setattr(_Lowerer, '_coerce_sequence_storage', lose_lengths)
    with pytest.raises(LoweringError, match='storage contract'):
        lower_with_state_ranges(source_for('list'), input_widths=[2], outputs=['y'], bound=3)
