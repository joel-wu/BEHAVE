"""Implicit step plans follow supplied state, never an assumed invariant."""

import itertools

import pytest
import z3

from behave.sim.behavior import residual
from behave.sim.symbolic import encoder, interp, ir, plan, ranges, typecheck as types
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import _leaves
from behave.sim.core.contract import FiniteType

pytestmark = pytest.mark.solver


def _procedure(loop=False, array=False):
    state = ir.Sel(ir.Var('q'), ir.Lit(0)) if array else ir.Var('q')
    result = ir.Op('add', state, ir.Lit(1))
    write = ir.Store('q', ir.Lit(0), result) if array else ir.Assign('q', result)
    body = ir._sequence((
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(3)), ir.Fail('Stopped', 'failed'), ir.Skip()),
        write,
        ir.If(ir.Op('lt', state, ir.Lit(0)), ir.Mark('negative'), ir.Mark('nonnegative'))))
    return ir.Procedure(('q',), ('a',), ('i',) if loop else (), (),
        ir.For('i', 1, body) if loop else body,
        initial={'q': [0, 0] if array else 0},
        types={'q': types.Array(2)} if array else {},
        signature=ProcedureSignature((('a', FiniteType(2)),), ()))


@pytest.mark.parametrize('loop', [False, True])
@pytest.mark.parametrize('array', [False, True])
def test_unplanned_step_matches_all_supplied_values_not_only_the_proposed_range(loop, array):
    proc = _procedure(loop, array)
    q = z3.BitVec('growing_state', 4)
    # Array leaves deliberately have different widths. Planning must not
    # confuse a narrower exact representation with a different value domain.
    before = {'q': [q, z3.BitVec('unchanged_cell', 2)] if array else q}
    encoded = encoder.encode_step(proc, pre=before,
                                  env={'q': ranges.Range(0, 1), 'a': ranges.Range(0, 3)})
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for value, a in itertools.product(range(-8, 8), range(4)):
        state = {'q': [value, -1] if array else value}
        expected, _out, hits, failure = interp.run_step(proc, {'a': a}, state)
        solver.push()
        solver.add(q == value, encoded.pre['a'] == a)
        if array:
            solver.add(before['q'][1] == -1)
        assert solver.check() == z3.sat
        actual = encoded.state_post['q']
        actual = actual[0] if array else actual
        expected = expected['q'][0] if array else expected['q']
        differences = [z3.BV2Int(actual, is_signed=True) != expected,
                       encoded.ok != (not failure),
                       *(term != (goal in hits) for goal, term in encoded.hits.items())]
        if array:
            differences.append(z3.BV2Int(encoded.state_post['q'][1], is_signed=True) != -1)
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize('loop', [False, True])
@pytest.mark.parametrize('array', [False, True])
def test_many_steps_cross_the_initial_carrier_without_wrap_or_false_hits(loop, array):
    proc = _procedure(loop, array)
    run = residual.unroll(proc, {'q': ranges.Range(0, 1), 'a': ranges.Range(0, 3)}, 10)
    solver = z3.Solver()
    solver.add(run.constraint, *(step.pre['a'] == 0 for step in run.encodings))
    assert solver.check() == z3.sat
    wrong = []
    for index, step in enumerate(run.encodings):
        value = next(_leaves(step.state_post['q'])) if array else step.state_post['q']
        wrong.extend((z3.BV2Int(value, is_signed=True) != index + 1,
                      z3.Not(step.hits['nonnegative']), step.hits['negative'],
                      z3.Not(step.ok)))
    solver.add(z3.Or(*wrong))
    assert solver.check() == z3.unsat


def test_explicit_plan_is_not_silently_replaced_to_fit_other_state():
    proc = _procedure(loop=True)
    env = {'q': ranges.Range(0, 1), 'a': ranges.Range(0, 3)}
    explicit = plan.plan_encoding(proc, env)
    with pytest.raises(encoder.EncodingError, match='supplied value has another leaf width'):
        encoder.encode_step(proc, pre={'q': z3.BitVec('larger_state', 8)}, env=env, plan=explicit)


def test_functional_array_remains_compact_at_the_supplied_boundary():
    proc = _procedure(loop=True, array=True)
    memory = FunctionalArray.uniform(2, z3.BitVec('array_value', 8))
    encoded = encoder.encode_step(proc, pre={'q': memory},
                                  env={'q': ranges.Range(0, 1), 'a': ranges.Range(0, 3)})
    assert encoded.pre['q'] is memory
    updated = encoded.state_post['q']
    assert isinstance(updated, FunctionalArray)
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.pre['a'] == 0, memory.default == 127)
    assert solver.check() == z3.sat
    value = updated.select(z3.BitVecVal(0, 2))
    solver.add(z3.BV2Int(value, is_signed=True) != 128)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('array', [False, True])
@pytest.mark.parametrize('value', [-2, 0, 7])
def test_literal_entry_plan_uses_values_not_their_storage_width(monkeypatch, array, value):
    proc = _procedure(loop=True, array=array)
    literal = z3.BitVecVal(value, 64)
    before = FunctionalArray.uniform(2, literal) if array else literal
    original, seen = plan.plan_encoding, []

    def derive(proc, env, *args, **kwargs):
        seen.append((env['q'].lo, env['q'].hi))
        return original(proc, env, *args, **kwargs)

    monkeypatch.setattr(plan, 'plan_encoding', derive)
    enc = encoder.encode_step(proc, pre={'q': before},
        env={'q': ranges.Range(0, 0), 'a': ranges.Range(0, 3)})
    assert seen == [(value, value)]
    actual = enc.state_post['q']
    actual = actual.select(z3.BitVecVal(0, 2)) if array else actual
    solver = z3.Solver()
    solver.add(enc.constraint, enc.pre['a'] == 0)
    assert solver.check() == z3.sat
    solver.add(z3.BV2Int(actual, is_signed=True) != value + 1)
    assert solver.check() == z3.unsat


def test_reset_chain_keeps_masked_bounds_without_restricting_inputs(monkeypatch):
    a = ir.Var('a')
    read = ir.Sel(ir.Var('q'), ir.Lit(0))
    body = ir._sequence((
        ir.If(ir.Op('eq', a, ir.Lit(255)), ir.Fail('Stopped', 'failed'), ir.Skip()),
        ir.Store('q', ir.Op('and', a, ir.Lit(1)), ir.Op('and', a, ir.Lit(15))),
        ir.If(ir.Op('eq', read, ir.Lit(2)), ir.Mark('two'), ir.Skip())))
    proc = ir.Procedure(('q',), ('a',), ('i',), (), ir.For('i', 1, body),
        initial={'q': [0, 0]}, types={'q': types.Array(2)},
        signature=ProcedureSignature((('a', FiniteType(8)),), ()))
    derive, seen = plan.plan_encoding, []

    def observe(proc, env, *args, **kwargs):
        seen.append((env['q'].lo, env['q'].hi))
        return derive(proc, env, *args, **kwargs)

    monkeypatch.setattr(plan, 'plan_encoding', observe)
    run = residual.unroll(proc, {'q': ranges.Range(0, 0), 'a': ranges.Range(0, 255)}, 3)
    assert seen == [(0, 0), (0, 15)]
    solver = z3.Solver()
    solver.add(run.constraint)
    for values in itertools.product((0, 2, 5, 255), repeat=3):
        expected, hits, stopped = {'q': [0, 0]}, set(), False
        for value in values:
            if not stopped:
                expected, _output, current, failure = interp.run_step(proc, {'a': value}, expected)
                hits.update(current)
                stopped = bool(failure)
        solver.push()
        solver.add(*(step.pre['a'] == value for step, value in zip(run.encodings, values)))
        assert solver.check() == z3.sat
        solver.add(z3.Or(run.halted[-1] != stopped,
                        *(run.goal_condition(goal) != (goal in hits) for goal in run.goals)))
        assert solver.check() == z3.unsat
        solver.pop()


def test_arbitrary_initial_state_does_not_gain_reset_equation_authority(monkeypatch):
    from behave.sim.symbolic.term_rewrite import _ComparisonRewriter

    proc = _procedure(loop=True)
    initial = {'q': z3.BitVec('not_a_reset_state', 8)}

    def refuse(*args, **kwargs):
        pytest.fail('arbitrary initial state must not use owned reset-chain bounds')

    monkeypatch.setattr(_ComparisonRewriter, 'value_bounds', refuse)
    run = residual.unroll(proc, {'q': ranges.Range(0, 0), 'a': ranges.Range(0, 3)},
                          2, initial=initial)
    solver = z3.Solver()
    solver.add(run.constraint, initial['q'] == 127,
               *(step.pre['a'] == 0 for step in run.encodings))
    assert solver.check() == z3.sat
    solver.add(z3.BV2Int(run.encodings[-1].state_post['q'], is_signed=True) != 129)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('compact_old', [False, True])
@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('stopped', [False, True, None])
def test_state_hold_accepts_equivalent_compact_and_cell_representations(
        compact_old, nested, stopped):
    old_cells = [z3.BitVec('held_old0', 3), z3.BitVec('held_old1', 5)]
    new_cells = [z3.BitVec('held_new0', 6), z3.BitVec('held_new1', 4)]
    old = FunctionalArray.from_cells(old_cells) if compact_old else old_cells
    new = new_cells if compact_old else FunctionalArray.from_cells(new_cells)
    guard = z3.Bool('held_stopped') if stopped is None else z3.BoolVal(stopped)
    actual = residual._held(guard, [old] if nested else old,
                            [new] if nested else new, 'q')
    actual = actual[0] if nested else actual
    assert isinstance(actual, FunctionalArray)
    assert actual.element_width == 6
    solver = z3.Solver()
    differences = []
    for index, (before, after) in enumerate(zip(old_cells, new_cells)):
        selected = actual.select(z3.BitVecVal(index, 3))
        expected = z3.If(guard, z3.BV2Int(before, is_signed=True),
                         z3.BV2Int(after, is_signed=True))
        differences.append(z3.BV2Int(selected, is_signed=True) != expected)
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('other', [[], [0], [[0], [0]]])
def test_state_hold_still_rejects_changed_recursive_array_shape(other):
    compact = FunctionalArray.uniform(2, z3.BitVecVal(0, 4))
    with pytest.raises(encoder.EncodingError, match='changes compact array shape'):
        residual._held(z3.Bool('invalid_hold'), compact, other, 'q')
