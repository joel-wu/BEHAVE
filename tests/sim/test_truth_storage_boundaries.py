"""Additional domain and nonempty-padding controls for the shared representation."""

import itertools

import pytest

from behave.sim.behavior import audit
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

from .test_nested_sequence_storage import source_for


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('initial', ["'nonempty'", '[1]', 'None'])
def test_truth_normalization_is_not_a_numeric_type_conversion(lowerer, initial):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        x = {initial}
        while x:
            x >>= 1
        return {{'y': a}}
'''
    # This proof covers integer recurrences, not coercion of None/containers
    # into zero. The existing noninteger planning boundary remains explicit.
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'], lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('carrier', ['list', 'bytearray'])
def test_short_nonempty_rows_also_preserve_their_real_length(lowerer, carrier):
    source = source_for(carrier)
    if carrier == 'bytearray':
        source = source.replace('bytearray()', 'bytearray([7])')
    else:
        source = source.replace('[[], []]', '[[7], [7]]')
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'],
                                   lowerer=lowerer, bound=3)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    histories = [[{'a': a} for a in values]
                 for values in itertools.product(range(4), repeat=3)]
    assert audit.model_audit(proc, namespace['M'], histories) == []
