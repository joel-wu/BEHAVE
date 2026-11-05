"""Integer sums do not build Python-recursion-depth-sized IR spines."""

import pytest

from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer
from tests.sim.test_source_closed_bindings import agrees


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer], ids=['term', 'graph'])
@pytest.mark.parametrize('values', ['[a - 2] * 1024', '[]'])
def test_balanced_sum_keeps_integer_values_and_start(lowerer, values):
    agrees(f'''class M:
    def reset(self): pass
    def process(self, a):
        values = {values}
        return {{"y": sum(values, 7)}}
''', lowerer, bound=1)
