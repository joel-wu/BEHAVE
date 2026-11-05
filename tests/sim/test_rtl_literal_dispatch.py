"""Literal case lookup preserves first match, signed values and all events."""

import random

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.rtl.source_ir import _literal_case_dispatch, _sequence
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_packed import literal_window


def test_sorted_lookup_matches_source_priority_over_a_finite_domain():
    rng = random.Random(42)
    for trial in range(100):
        values = [tuple(rng.randrange(-10, 10) for _ in range(rng.randrange(1, 4)))
                  for _ in range(rng.randrange(1, 20))]
        labels = [[([], ir.Lit(value)) for value in row] for row in values]
        arms = [([], None, _sequence([ir.Mark(str(index)), ir.Assign('q', ir.Lit(index))]))
                for index in range(len(values))]
        fallback = _sequence([ir.Mark('default'), ir.Assign('q', ir.Lit(-1))])
        command = _literal_case_dispatch(ir.Var('a'), labels, arms, fallback, (-8, 7))
        declared = {str(index) for index in range(len(values))} | {'default'}
        assert {cmd.goal for cmd in ir.walk(command) if isinstance(cmd, ir.Mark)} == declared
        for a in range(-8, 8):
            expected = next((i for i, row in enumerate(values) if a in row), -1)
            machine, hits = interp._Interp({'a': a, 'q': 99}), set()
            machine.run(command, hits)
            assert machine.rho['q'] == expected, (trial, a)
            assert hits == ({'default'} if expected == -1 else {str(expected)})


def test_effectful_labels_do_not_use_reordered_lookup():
    labels = [[([ir.Mark('label')], ir.Lit(1))]]
    arms = [([], None, ir.Mark('body'))]
    assert _literal_case_dispatch(ir.Var('a'), labels, arms, ir.Skip(), (0, 3)) is None


def test_exhaustive_lookup_retains_the_unreachable_default_producer():
    labels = [[([], ir.Lit(value))] for value in range(16)]
    arms = [([], None, ir.Mark(str(value))) for value in range(16)]
    command = _literal_case_dispatch(ir.Var('a'), labels, arms, ir.Mark('default'), (0, 15))
    assert 'default' in {cmd.goal for cmd in ir.walk(command) if isinstance(cmd, ir.Mark)}
    for a in range(16):
        machine, hits = interp._Interp({'a': a}), set()
        machine.run(command, hits)
        assert hits == {str(a)}


@pytest.mark.parametrize('width', [2, 4, 6])
@pytest.mark.parametrize('signed', [False, True])
def test_propagated_literal_conversion_matches_the_original_primitive_tree(width, signed):
    target, source = IntegralType(width, signed), IntegralType(4, False)
    for value in range(16):
        literal = target.convert(ir.Lit(value), source, propagated=True)
        assert isinstance(literal, ir.Lit)
        original = ir.Op('swrap' if signed else 'uwrap', ir.Op(
            'swrap' if signed else 'uwrap', ir.Lit(value), ir.Lit(4)), ir.Lit(width))
        machine = interp._Interp({})
        machine.run(ir.Assign('q', original), set())
        assert literal.value == machine.rho['q']


@pytest.mark.parametrize('width', [1, 3, 8, 17, 32])
@pytest.mark.parametrize('signed', [False, True])
def test_packed_literal_windows_match_every_in_range_offset(width, signed):
    rng = random.Random(71)
    owner_width = 173
    bits = rng.getrandbits(owner_width) | (1 << (owner_width - 1))
    value = bits - (1 << owner_width) if signed else bits
    kind = IntegralType(width, signed)
    expression = kind.wrap(literal_window(ir.Lit(value), ir.Var('offset'), owner_width, width))
    for offset in range(owner_width - width + 1):
        actual = interp._Interp({'offset': offset}).value(expression)
        expected = (bits >> offset) & ((1 << width) - 1)
        if signed and expected & (1 << (width - 1)):
            expected -= 1 << width
        assert actual == expected, (width, signed, offset)
