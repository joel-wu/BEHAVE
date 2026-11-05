"""Source range projection preserves Python short-circuit operand values."""

import ast

import pytest

from behave.sim.symbolic import ranges
from behave.sim.behavior.source_sequences import _scalar_bounds, _ScalarBindings
from behave.sim.behavior.source_values import SourceValueBudget


@pytest.mark.parametrize('text,expected', [
    ('a and b', (-4, 3)),
    ('a or b', (-4, 3)),
    ('a and 0', (0, 0)),
    ('a or 0', (-2, 2)),
    ('a and False', (0, 0)),
    ('a or True', (-2, 2)),
    ('(a == 1) and (b > 5)', (0, 0)),
    ('(a == 1) or (b < 5)', (1, 1)),
    ('a and b and 7', (-4, 7)),
    ('(a and 0) or 7', (7, 7)),
    ('(a and 0) and 7', (0, 0)),
    ('(a == 1) and True', (0, 1)),
])
def test_short_circuit_range_is_a_sound_operand_envelope(text, expected):
    node = ast.parse(text, mode='eval').body
    bindings = _ScalarBindings(scalar_bounds={
        'a': ranges.Bounds(-2, 2), 'b': ranges.Bounds(-4, 3)})
    actual = _scalar_bounds(node, lambda _node: None, bindings,
                            budget=SourceValueBudget())
    assert actual is not None
    assert actual.lo >= expected[0] and actual.hi <= expected[1]
    code = compile(text, 'range-fixture', 'eval')
    for a in range(-2, 3):
        for b in range(-4, 4):
            value = eval(code, {'__builtins__': {}}, {'a': a, 'b': b})
            assert actual.lo <= value <= actual.hi


@pytest.mark.parametrize('text', ['"" and a', 'a or []'])
def test_unrepresented_operand_is_not_an_integer_zero(text):
    bindings = _ScalarBindings(scalar_bounds={'a': ranges.Bounds(-2, 2)})
    actual = _scalar_bounds(ast.parse(text, mode='eval').body,
                            lambda _node: None, bindings, budget=SourceValueBudget())
    assert actual is None
