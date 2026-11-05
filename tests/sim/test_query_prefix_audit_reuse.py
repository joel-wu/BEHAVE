"""Reuse completed structural work, never prefix UNSAT or a solver model."""

import pytest
import z3

from behave.sim.symbolic.exact_formula import (
    ExactFormula, FormulaDefinition, SliceFallbackCode, _FormulaConstruction,
)

pytestmark = pytest.mark.solver


def register(definitions, restrictions, boundary, prefix=None):
    return _FormulaConstruction(definitions, restrictions, boundary, prefix=prefix).advance(lambda: None)


def problem(context=None):
    x, a, b = z3.BitVecs('x a b', 8, ctx=context)
    first = FormulaDefinition('a', a, x + 1)
    second = FormulaDefinition('b', b, a * 3)
    restrictions = (x != 255,)
    return x, a, b, first, second, restrictions


def test_extended_prefix_audits_only_new_definitions(monkeypatch):
    x, a, b, first, second, restrictions = problem()
    prefix = register((first,), restrictions, (x,))
    old_edges = dict(prefix._dependency_edges)
    visited, walk = [], ExactFormula._ordered_dependency_steps

    def watched(self, term, *args, **kwargs):
        visited.append(term.get_id())
        return (yield from walk(self, term, *args, **kwargs))

    monkeypatch.setattr(ExactFormula, '_ordered_dependency_steps', watched)
    extended = register((first, second), restrictions, (x,), prefix)
    assert visited == [second.rhs.get_id()]
    assert extended.sliced_sat_extends
    assert dict(prefix._dependency_edges) == old_edges
    fresh = register((first, second), restrictions, (x,))
    for value in (0, 1, 17, 254, 255):
        for target in (a == value, b == value):
            left, right = extended.slice(target), fresh.slice(target)
            assert left.mode == right.mode and left.kept_definitions == right.kept_definitions
            solver = z3.Solver()
            solver.add(z3.Xor(z3.And(*left.query_assertions), z3.And(*right.query_assertions)))
            assert solver.check() == z3.unsat


@pytest.mark.parametrize('change', ['definition', 'restriction', 'boundary', 'context'])
def test_changed_formula_never_borrows_the_previous_audit(change, monkeypatch):
    x, a, b, first, second, restrictions = problem()
    prefix = register((first,), restrictions, (x,))
    boundary = (x,)
    if change == 'definition':
        first = FormulaDefinition('a', a, x - 7)
    elif change == 'restriction':
        restrictions = (x == 255,)
    elif change == 'boundary':
        boundary = ()
    else:
        x, a, b, first, second, restrictions = problem(z3.Context())
        boundary = (x,)
    visited, walk = [], ExactFormula._ordered_dependency_steps

    def watched(self, term, *args, **kwargs):
        visited.append(term)
        return (yield from walk(self, term, *args, **kwargs))

    monkeypatch.setattr(ExactFormula, '_ordered_dependency_steps', watched)
    extended = register((first, second), restrictions, boundary, prefix)
    assert any(term.eq(first.rhs) for term in visited)
    fresh = register((first, second), restrictions, boundary)
    left, right = extended.slice(b == 0), fresh.slice(b == 0)
    assert left.mode == right.mode
    assert (None if left.fallback is None else left.fallback.code) == (
        None if right.fallback is None else right.fallback.code)
    solver = z3.Solver(ctx=b.ctx)
    solver.add(z3.Xor(z3.And(*left.query_assertions), z3.And(*right.query_assertions)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('invalid', ['forward', 'duplicate', 'boundary', 'unknown'])
def test_append_still_checks_freshness_order_and_closed_vocabulary(invalid):
    x, a, b, first, second, restrictions = problem()
    prefix = register((first,), restrictions, (x,))
    c = z3.BitVec('c', 8)
    extras = {
        'forward': (FormulaDefinition('b', b, c + 1), FormulaDefinition('c', c, a)),
        'duplicate': (FormulaDefinition('again', a, x + 2),),
        'boundary': (FormulaDefinition('x', x, a),),
        'unknown': (FormulaDefinition('b', b, c + 1),),
    }[invalid]
    expected = {'forward': SliceFallbackCode.CYCLIC_DEFINITIONS,
                'duplicate': SliceFallbackCode.DUPLICATE_DEFINITION,
                'boundary': SliceFallbackCode.ATOM_ROLE_CONFLICT,
                'unknown': SliceFallbackCode.UNKNOWN_ATOM}[invalid]
    extended = register((first, *extras), restrictions, (x,), prefix)
    assert not extended.sliced_sat_extends
    assert extended.slice(b == 0).fallback.code is expected


def test_cancelled_extension_resumes_without_mutating_previous_product():
    x, a, b, first, second, restrictions = problem()
    prefix = register((first,), restrictions, (x,))
    owner = _FormulaConstruction((first, second), restrictions, (x,), prefix=prefix)
    count = 0

    def cancel():
        nonlocal count
        count += 1
        if count == 5:
            raise TimeoutError('diagnostic stop')

    with pytest.raises(TimeoutError):
        owner.advance(cancel)
    assert owner.result is None and prefix.sliced_sat_extends
    result = owner.advance(lambda: None)
    assert result.sliced_sat_extends
    assert result.slice(b == 3).kept_definitions == ('a', 'b')
