"""Finite scalar extrema retain the iterator's length, values and native events."""

import pytest

from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

from .test_iterable_products import (
    _assert_outputs_and_every_goal, _assert_public_goal_queries, _source,
)

pytestmark = pytest.mark.solver


ITERABLES = [
    '[a, 3 - a, -2]',
    '(a, a + 1, -a)',
    '[a - i for i in range(4)]',
    '[a + i for i in range(1, a + 2)]',
    'range(-3, a + 1)',
    'reversed([a, 3 - a, -2])',
]


def source_for(expression, operation):
    setup, argument = (('', expression) if expression.startswith('range(')
                       else (f'        values = {expression}\n', 'values'))
    return _source(setup + f'''        result = {operation}({argument})
        if result == a:
            return {{'y': result + 10}}
        return {{'y': result + 32}}
''')


@pytest.mark.parametrize('operation', ['min', 'max'])
@pytest.mark.parametrize('expression', ITERABLES)
@pytest.mark.parametrize('lowerer', [GraphLowerer, _Lowerer])
def test_extrema_match_native_ir_and_symbolic_events(expression, operation, lowerer):
    _assert_outputs_and_every_goal(source_for(expression, operation), lowerer)


@pytest.mark.parametrize('operation', ['min', 'max'])
@pytest.mark.parametrize('expression', ITERABLES)
def test_extrema_goals_have_local_queries_and_native_witnesses(expression, operation):
    _assert_public_goal_queries(source_for(expression, operation))


@pytest.mark.parametrize('expression', ['[]', '[i for i in range(a)]'])
def test_empty_iterables_are_not_replaced_by_padding(expression):
    with pytest.raises(LoweringError, match='possibly empty iterable'):
        lower_with_state_ranges(source_for(expression, 'max'), input_widths=[2])
