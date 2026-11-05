"""Private array arguments retain writes without enabling general aliases."""
import pytest

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

SOURCE = '''class M:
    def reset(self): self.last = 0
    def update(self, cells, i, value):
        cells[i] = value
        if value == 3: return
        cells[(i + 1) % 3] = value + 1
    def process(self, a):
        cells = [0, 0, 0]
        for j in range(3):
            self.update(cells, j, (a >> (2 * j)) & 3)
        self.last = cells[0] + cells[1] + cells[2]
        return {'y': self.last}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('forward', [False, True])
@pytest.mark.parametrize('keywords', [False, True])
def test_owned_argument_writes_reach_caller_and_native_hits(lowerer, forward, keywords):
    source = SOURCE
    if forward:
        source = source.replace('def update(self, cells, i, value):',
            'def update(self, cells, i, value):\n        self.inner(cells, i, value)\n'
            '    def inner(self, cells, i, value):')
    if keywords:
        source = source.replace('self.update(cells, j, (a >> (2 * j)) & 3)',
                                'self.update(value=(a >> (2 * j)) & 3, i=j, cells=cells)')
    proc = lower_with_state_ranges(source, input_widths=[6], outputs=['y'], bound=2, lowerer=lowerer)
    namespace, _, native_hits = instrumented.load_source(source)
    histories = [[{'a': a}, {'a': 63 - a}] for a in range(64)]
    for history in histories:
        model = namespace['M']()
        model.reset()
        state = dict(proc.initial)
        for values in history:
            native_hits.clear()
            expected = model.process(**values)
            state, outputs, hits, error = interp.run_step(proc, values, state)
            assert not error and outputs.transactions == [expected]
            assert set(hits) == set(native_hits)
    plain = {}
    exec(source, plain)
    assert audit.model_audit(proc, plain['M'], histories) == []


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_formal_rebind_does_not_rebind_caller(lowerer):
    source = SOURCE.replace('        cells[i] = value',
                            '        cells = [1, 1, 1]\n        cells[i] = value')
    proc = lower_with_state_ranges(source, input_widths=[6], outputs=['y'], lowerer=lowerer)
    for a in range(64):
        _, outputs, _, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [{'y': 0}]


def test_visible_caller_alias_is_not_silently_copied():
    source = SOURCE.replace('        for j in range(3):',
                            '        other = cells\n        for j in range(3):')
    with pytest.raises(LoweringError, match='(alias|reference|shares)'):
        lower_with_state_ranges(source, input_widths=[6], outputs=['y'])


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('keywords', [False, True])
def test_outer_evaluation_hold_is_not_a_borrowed_formal(lowerer, keywords):
    source = '''class M:
    def reset(self): pass
    def update(self, cells):
        cells[0] = 7
        return 0
    def process(self, a):
        cells = [a, 0]
        return {'y': cells[self.update(cells)]}
'''
    if keywords:
        source = source.replace('self.update(cells)]', 'self.update(cells=cells)]')
    with pytest.raises(LoweringError, match='(alias|reference|shares)'):
        lower_with_state_ranges(source, input_widths=[3], outputs=['y'], lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_proved_empty_while_does_not_require_helper_activation(lowerer):
    source = '''class M:
    def reset(self): self.n = 0
    def update(self, cells): cells[0] = 9
    def process(self, a):
        cells = [a, 0]
        count = self.n
        while count > 0:
            self.update(cells)
            count -= 1
        else:
            cells[1] = 1
        return {'y': cells[0] + cells[1]}
'''
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'], lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(source)
    for a in range(8):
        model = namespace['M']()
        model.reset()
        hits.clear()
        expected = model.process(a)
        _, outputs, actual, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert set(actual) == set(hits)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('consumer', ['branch', 'loop', 'helper'])
def test_borrowed_write_invalidates_caller_value_facts(lowerer, consumer):
    body = {
        'branch': "if cells[0]:\n            return {'y': 7}\n        return {'y': 0}",
        'loop': "total = 0\n        for j in range(cells[0]):\n            total += j + 1\n        return {'y': total}",
        'helper': "return {'y': self.consume(cells[0])}",
    }[consumer]
    source = '''class M:
    def reset(self): pass
    def update(self, cells, value): cells[0] = value
    def consume(self, value):
        if value: return 7
        return 0
    def process(self, a):
        cells = [0]
        self.update(cells, a)
        ''' + body + '\n'
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'], lowerer=lowerer)
    namespace, _, hits = instrumented.load_source(source)
    for a in range(8):
        model = namespace['M']()
        model.reset()
        hits.clear()
        expected = model.process(a)
        _, outputs, actual, error = interp.run_step(proc, {'a': a})
        assert not error and outputs.transactions == [expected]
        assert set(actual) == set(hits)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_shape_changing_helper_is_not_a_fixed_array_borrow(lowerer):
    source = '''class M:
    def reset(self): pass
    def update(self, cells, value): cells.append(value)
    def process(self, a):
        cells = [0]
        self.update(cells, a)
        return {'y': len(cells)}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[3], outputs=['y'], lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_two_formals_do_not_hide_argument_aliasing(lowerer):
    source = '''class M:
    def reset(self): pass
    def update(self, left, right, a):
        left[0] = a
        if right[0]: return 7
        return 0
    def process(self, a):
        cells = [0]
        return {'y': self.update(cells, cells, a)}
'''
    with pytest.raises(LoweringError, match='(alias|reference|shares)'):
        lower_with_state_ranges(source, input_widths=[3], outputs=['y'], lowerer=lowerer)
