"""Exact finite normalization loops, including source-event and solver parity."""

import ast
from itertools import product

import pytest

from behave.sim.behavior import instrumented, while_plan
from behave.sim.symbolic import encoder, interp, ir, ranges
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges


@pytest.mark.parametrize('inclusive,amount', tuple(product((False, True), (1, 2, 3))))
def test_left_shift_trip_span_is_exact_at_every_small_point(inclusive, amount):
    progress = while_plan.WhileProgress(ast.Name(id='limit'), inclusive, 'x', 'lshift', amount)
    for initial, bound in product(range(1, 17), range(-2, 33)):
        x, count = initial, 0
        while x <= bound if inclusive else x < bound:
            x <<= amount
            count += 1
        span = while_plan.trip_span(progress, ranges.Range(initial, initial), ranges.Range(bound, bound))
        assert span == ranges.Range(count, count)
    assert while_plan.trip_span(progress, ranges.Bounds(1, None), ranges.Bounds(None, 32)).lo == 0


@pytest.mark.parametrize('initial', [ranges.Bounds(), ranges.Range(0, 3), ranges.Range(-3, -1)])
def test_nonpositive_induction_cannot_establish_a_shift_termination_proof(initial):
    progress = while_plan.WhileProgress(ast.Name(id='limit'), False, 'x', 'lshift', 1)
    with pytest.raises(while_plan.WhilePlanError, match='positive induction'):
        while_plan.trip_span(progress, initial, ranges.Range(7, 7))


@pytest.mark.parametrize('low,high,excluded', [
    (0, 7, 0), (-7, 0, 0), (-7, 7, 0), (2, 2, 2), (0, None, 0), (None, 0, 0),
])
def test_constant_exclusion_refines_only_interval_endpoints(low, high, excluded):
    result = ranges.assume(ir.Op('ne', ir.Var('x'), ir.Lit(excluded)), True,
                           {'x': ranges.Bounds(low, high)})
    possible = [x for x in range(-10, 11) if (low is None or x >= low)
                and (high is None or x <= high) and x != excluded]
    if result is None:
        assert not possible
    else:
        assert all((result['x'].lo is None or x >= result['x'].lo)
                   and (result['x'].hi is None or x <= result['x'].hi) for x in possible)
        if low == excluded:
            assert result['x'].lo == excluded + 1
        elif high == excluded:
            assert result['x'].hi == excluded - 1
        else:
            assert result['x'] == ranges.Bounds(low, high)


@pytest.mark.solver
@pytest.mark.parametrize('lowerer', [GraphLowerer, _Lowerer])
@pytest.mark.parametrize('inclusive', [False, True])
def test_shift_loop_native_ir_and_encoded_values_and_hits_agree(lowerer, inclusive):
    z3 = pytest.importorskip('z3')
    relation = '<=' if inclusive else '<'
    source = f'''class M:
    def reset(self): pass
    def process(self, a, limit):
        x = a
        if x == 0:
            return {{'y': 0}}
        count = 0
        while x {relation} limit:
            x = x << 2
            count += 1
        return {{'y': x + count}}
'''
    namespace, _sites, native_hits = instrumented.load_source(source)
    proc = lower_with_state_ranges(source, input_widths=[3, 4], lowerer=lowerer)
    assert sum(isinstance(node, ir.For) for node in ir.walk(proc.body)) == 1
    env = ranges.fixpoint(proc, {'a': ranges.Range(0, 7), 'limit': ranges.Range(0, 15)})
    enc = encoder.encode_step(proc, env=env)
    solver = z3.Solver()
    solver.add(enc.constraint)
    for a, limit in product(range(8), range(16)):
        native_hits.clear()
        expected = namespace['M']().process(a, limit)
        _, outputs, hits, error = interp.run_step(proc, {'a': a, 'limit': limit})
        assert not error and outputs.transactions == [expected]
        assert hits == set(native_hits)
        solver.push()
        solver.add(enc.pre['a'] == a, enc.pre['limit'] == limit)
        assert solver.check() == z3.sat
        model = solver.model()
        symbolic = {goal for goal, term in enc.hits.items()
                    if z3.is_true(model.eval(term, model_completion=True))}
        assert symbolic == hits
        solver.pop()


@pytest.mark.parametrize('update', ['x <<= 0', 'x >>= 1', 'x <<= 1; x = 0'])
def test_nonprogressing_or_overwritten_shift_loop_is_still_refused(update):
    source = f'''class M:
    def reset(self): pass
    def process(self, a):
        x = a + 1
        while x < 16:
            {update}
        return {{'y': x}}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2])
