"""Template plans cover their supplied state, not proposed invariants."""

import pytest
import z3

from behave.sim.symbolic import encoder, ir, primitives, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import StepTemplate, _leaves, _tree
from behave.sim.core.contract import FiniteType

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('operation', ['and', 'or', 'xor'])
@pytest.mark.parametrize('width', [1, 2, 4, 65, 513])
def test_signed_bitwise_ranges_close_without_inventing_high_bits(operation, width):
    interval = ranges.Range(-(1 << (width - 1)), (1 << (width - 1)) - 1)
    result = primitives.primitive(operation).range_transform(interval, interval)
    assert (result.lo, result.hi) == (interval.lo, interval.hi)


@pytest.mark.parametrize('operation', ['and', 'or', 'xor'])
def test_signed_bitwise_ranges_contain_every_small_integer_result(operation):
    primitive = primitives.primitive(operation)
    calculate = {'and': int.__and__, 'or': int.__or__, 'xor': int.__xor__}[operation]
    intervals = [(lo, hi) for lo in range(-8, 8) for hi in range(lo, 8)]
    for alo, ahi in intervals:
        for blo, bhi in intervals:
            result = primitive.range_transform(ranges.Range(alo, ahi), ranges.Range(blo, bhi))
            for a in range(alo, ahi + 1):
                for b in range(blo, bhi + 1):
                    value = calculate(a, b)
                    assert result.lo <= value <= result.hi


@pytest.mark.parametrize('array', [False, True])
@pytest.mark.parametrize('read_state', [False, True])
def test_planned_branches_preserve_all_representable_entry_states(array, read_state):
    state = ir.Sel(ir.Var('q'), ir.Lit(0)) if array else ir.Var('q')
    def write(value):
        value = ir.Op('swrap', value, ir.Lit(4))
        return ir.Store('q', ir.Lit(0), value) if array else ir.Assign('q', value)
    source = state if read_state else ir.Var('a')
    body = ir._sequence((
        ir.Assign('t', ir.Op('add', source, ir.Lit(1))),
        ir.If(ir.Op('lt', ir.Var('t'), ir.Lit(0)), ir._sequence((
            write(ir.Op('neg', ir.Var('t'))), ir.Fail('negative', 'negative'))),
            ir._sequence((write(ir.Var('t')), ir.Mark('nonnegative'))))))
    kwargs = dict(initial={'q': [0] if array else 0},
                  types={'q': types.Array(1)} if array else {},
                  signature=ProcedureSignature((('a', FiniteType(3)),), ()))
    # This is deliberately only a proposed invariant. A supplied state may
    # contain any signed value representable by its carrier, including -4.
    env = {'q': ranges.Range(0, 3), 'a': ranges.Range(0, 7)}
    proc = ir.Procedure(('q',), ('a',), ('t', 'i'), (), ir.For('i', 1, body), **kwargs)
    compiled = StepTemplate(proc, env).encode()
    plain_proc = ir.Procedure(proc.state, proc.inputs, ('t',), (), body, **kwargs)
    width = next(_leaves(compiled.pre['q'])).size()
    full_env = {**env, 'q': ranges.Range(-(1 << (width - 1)), (1 << (width - 1)) - 1)}
    plain = encoder.encode_step(plain_proc, pre=compiled.pre, env=full_env)
    different = [plain.ok != compiled.ok, plain.error != compiled.error]
    for field in ('state_post', 'post', 'hits'):
        for name, value in getattr(plain, field).items():
            for left, right in zip(_leaves(_tree(value)),
                                   _leaves(getattr(compiled, field)[name]), strict=True):
                if z3.is_bv(left):
                    left, right = encoder.widen(left, right)
                different.append(left != right)
    solver = z3.Solver()
    solver.add(plain.constraint, compiled.constraint, z3.Or(*different))
    assert solver.check() == z3.unsat
    # Negative state is not silently excluded, and a source failure remains
    # reachable precisely when this program actually reads that state.
    solver = z3.Solver()
    entry = next(_leaves(compiled.pre['q']))
    solver.add(compiled.constraint, entry == -4, compiled.pre['a'] == 0)
    assert solver.check() == z3.sat
    assert z3.is_true(solver.model().eval(compiled.hits['negative'])) == read_state
