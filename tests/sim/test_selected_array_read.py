"""Closed selection visits one stored cell, preserving guarded read semantics."""

from unittest.mock import patch

import pytest
import z3

from behave.sim.symbolic import encoder, ir, ranges, typecheck as types
from tests.sim.test_guarded_reads import compare, make


@pytest.mark.parametrize('position', [-1, 0, 1, 3, 4, 'symbolic'])
@pytest.mark.parametrize('guard_kind', ['active', 'opposite', 'unknown'])
def test_selected_value_matches_reading_the_whole_array(position, guard_kind):
    active, outer, unknown = z3.Bools('cell_active cell_outer cell_unknown')
    enc = encoder._Encoder(None, {'i': ranges.Range(-4, 3)})
    enc._read_guard = {'active': active, 'opposite': z3.Not(active),
                       'unknown': unknown}[guard_kind]
    old = z3.BitVecs('cell_old_0 cell_old_1 cell_old_2 cell_old_3', 8)
    new = z3.BitVecs('cell_new_0 cell_new_1 cell_new_2 cell_new_3', 8)
    cells = [enc.bind(z3.If(outer, enc.bind(z3.If(active, b, a)), a))
             for a, b in zip(old, new, strict=True)]
    index = ir.Var('i') if position == 'symbolic' else ir.Lit(position)
    state = {'array': cells, 'i': z3.BitVec('cell_index', 3)}
    actual = enc.expr(ir.Sel(ir.Var('array'), index), state)
    refined = enc._guarded_reads.read(cells, enc._read_guard)
    at = enc.bind(enc.expr(index, state))
    expected = z3.BitVecVal(0, 8)
    for number, cell in enumerate(refined):
        expected = z3.If(encoder._at(at, number, len(cells)), cell, expected)
    actual, expected = encoder.widen(actual, expected)
    solver = z3.Solver()
    solver.add(*enc.defs, enc._read_guard, actual != expected)
    assert solver.check() == z3.unsat
    assert state['array'] is cells


def test_closed_selection_does_not_visit_unselected_cells():
    enc = encoder._Encoder(None, {})
    active = z3.Bool('selected_active')
    enc._read_guard = active
    cells = [enc.bind(z3.If(active, z3.BitVecVal(i, 16),
                            z3.BitVec(f'unselected_{i}', 16))) for i in range(512)]
    with patch.object(enc._guarded_reads, '_index_choices',
                      wraps=enc._guarded_reads._index_choices) as indexed:
        value = enc.expr(ir.Sel(ir.Var('array'), ir.Lit(37)), {'array': cells})
    assert value.eq(z3.BitVecVal(37, 16))
    assert {call.args[0].get_id() for call in indexed.call_args_list} <= {
        cells[37].get_id(), value.get_id()}
    assert not any(cell.get_id() in enc._guarded_reads._choices
                   for i, cell in enumerate(cells) if i != 37)


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_selected_reads_preserve_complete_loop_and_failure_observables(nested, failure):
    row = ir.Op('arraylit', ir.Var('a'), ir.Op('add', ir.Var('a'), ir.Lit(1)))
    value = ir.Op('arraylit', row, row) if nested else row
    selected = ir.Sel(ir.Var('t'), ir.Lit(1))
    if nested:
        selected = ir.Sel(selected, ir.Lit(0))
    body = ir._sequence((
        ir.Assign('t', value),
        ir.For('i', ir.Var('n'), ir.If(ir.Op('eq', selected, ir.Lit(1)),
            ir.Fail('stop', 'failed') if failure else ir.Mark('one'),
            ir.Assign('q', selected))),
        ir.Mark('suffix')))
    kind = types.Array(2, types.Array(2)) if nested else types.Array(2)
    compare(*make(body, kinds={'t': kind}))
