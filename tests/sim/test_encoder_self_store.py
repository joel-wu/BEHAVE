"""Self-store assignment keeps one guarded update, with unchanged semantics."""

import pytest
import z3

from behave.sim.symbolic import encoder, ir, ranges, typecheck as types
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.step_template import StepTemplate


@pytest.mark.parametrize('same_array', [False, True])
@pytest.mark.parametrize('functional', [False, True])
def test_guarded_store_assignment_matches_dense_cells(same_array, functional):
    enc = encoder._Commands(None, {
        'a': ranges.Range(-8, 7), 'b': ranges.Range(-8, 7),
        'i': ranges.Range(-8, 7), 'v': ranges.Range(-256, 255)})
    a = [z3.BitVec(f'a_{i}', 4) for i in range(4)]
    b = [z3.BitVec(f'b_{i}', 4) for i in range(4)]
    address, value = z3.BitVec('address', 4), z3.BitVec('value', 9)
    enabled = z3.Bool('enabled')
    state = encoder._State({
        'a': FunctionalArray.from_cells(a) if functional else a,
        'b': FunctionalArray.from_cells(b) if functional else b,
        'i': address, 'v': value}, {}, z3.BoolVal(True), z3.IntVal(0))
    old = state.rho['a']
    command = ir.Assign('a', ir.Op('store', ir.Var('a' if same_array else 'b'),
                                  ir.Var('i'), ir.Var('v')))
    enc._assign(command, state, enabled)
    current = state.rho['a']
    expected = [z3.If(enabled,
        z3.If(address == i, value, z3.SignExt(5, (a if same_array else b)[i])),
        z3.SignExt(5, a[i])) for i in range(4)]
    actual = [current.select(z3.BitVecVal(i, 4)) for i in range(4)] if functional else current
    solver = z3.Solver()
    solver.add(*enc.defs, z3.Or(*(got != want for got, want in zip(actual, expected))))
    assert solver.check() == z3.unsat
    assert state.rho['b'] is not None and old is not current
    assert enc.env['a'] == ranges.Range(-256, 255)


def test_self_store_never_rejoins_the_complete_history(monkeypatch):
    def unnecessary(*args, **kwargs):
        pytest.fail('a self-store constructed a whole-array conditional join')

    monkeypatch.setattr(FunctionalArray, 'choose', unnecessary)
    enc = encoder._Commands(None, {'a': ranges.Range(0, 255)})
    state = encoder._State({'a': FunctionalArray.uniform(256, z3.BitVecVal(0, 9))},
                          {}, z3.BoolVal(True), z3.IntVal(0))
    for index in range(64):
        command = ir.Assign('a', ir.Op('store', ir.Var('a'), ir.Lit(index), ir.Lit(index)))
        enc._assign(command, state, z3.Bool(f'write_{index}'))
    assert len(state.rho['a'].writes) == 64


def test_planned_self_store_preserves_old_reads_guards_and_goals():
    body = ir._sequence((
        ir.For('j', ir.Lit(4), ir.If(ir.Op('ne', ir.Var('enabled'), ir.Lit(0)),
            ir.Assign('a', ir.Op('store', ir.Var('a'), ir.Var('j'),
                ir.Op('swrap', ir.Op('add', ir.Sel(ir.Var('a'), ir.Var('j')), ir.Var('v')),
                      ir.Lit(4)))), ir.Skip())),
        ir.If(ir.Op('eq', ir.Sel(ir.Var('a'), ir.Lit(2)), ir.Lit(7)),
              ir.Mark('updated'), ir.Mark('other'))))
    proc = ir.Procedure(('a',), ('enabled', 'v'), ('j',), (), body,
        types={'a': types.Array(4), 'enabled': types.INTEGER, 'v': types.INTEGER,
               'j': types.INTEGER})
    env = {'a': ranges.Range(-8, 7), 'enabled': ranges.Range(0, 1), 'v': ranges.Range(-8, 7)}
    # Full-step component encoding and shared template use independent array
    # representations and retain the original validated occurrence plan.
    plain = encoder.encode_step(proc, env=env)
    template = StepTemplate(proc, env)
    compiled = template.encode(plain.pre)
    solver = z3.Solver()
    solver.add(plain.constraint, compiled.constraint)
    differences = [plain.ok != compiled.ok, plain.error != compiled.error,
                   *(plain.hits[name] != compiled.hits[name] for name in plain.hits)]
    for index, expected in enumerate(plain.state_post['a']):
        actual = compiled.state_post['a'].select(z3.BitVecVal(index, 4))
        left, right = encoder.widen(actual, expected)
        differences.append(left != right)
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat
