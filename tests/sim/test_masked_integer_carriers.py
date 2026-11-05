"""Literal mask compaction is exact on the full signed operand domain."""

import pytest
import z3

from behave.sim.symbolic import encoder, ir, primitives, ranges
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import StepTemplate
from behave.sim.core.contract import FiniteType

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('width,mask', [
    (4, 0), (4, 1), (4, 5), (4, 7), (8, 15), (8, 85),
    (8, -1), (8, -16), (65, 255), (513, (1 << 64) - 1),
])
@pytest.mark.parametrize('reverse', [False, True])
def test_masked_carrier_is_exact_without_range_assumptions(width, mask, reverse):
    # Deliberately pass a result width of one: a range hint must not justify
    # truncating any bits. Only the actual mask's zero prefix permits it.
    context = encoder._Encoder(None, {})
    variable = z3.BitVec('full_signed_operand', width)
    literal = z3.BitVecVal(mask, width)
    args = [literal, variable] if reverse else [variable, literal]
    compact = primitives.primitive('and').symbolic(context, args, 1)
    expected_width = mask.bit_length() + 1 if mask >= 0 else width
    assert compact.size() == expected_width
    full, compact = encoder.widen(variable & literal, compact)
    solver = z3.Solver()
    solver.add(full != compact)
    assert solver.check() == z3.unsat


def test_symbolic_mask_keeps_full_carrier_even_with_narrow_range_hint():
    context = encoder._Encoder(None, {})
    left, right = z3.BitVecs('unknown_left unknown_right', 8)
    value = primitives.primitive('and').symbolic(context, [left, right], 1)
    assert value.size() == 8
    assert value.eq(left & right)


def test_mask_keeps_the_horizon_wide_result_representation():
    context = encoder._Encoder(None, {})
    context.scalar_width = 32
    context.env = {'x': ranges.Range(-16, 15)}
    term = context.expr(ir.Op('and', ir.Var('x'), ir.Lit(3)),
                        {'x': z3.BitVec('horizon_input', 32)})
    assert term.size() == 32


@pytest.mark.parametrize('value', [-8, -1, 0, 7])
def test_literal_extension_preserves_signed_value_and_private_context(value):
    context = z3.Context()
    literal = z3.BitVecVal(value, 4, ctx=context)
    extended = encoder._extend(literal, 17)
    assert z3.is_bv_value(extended)
    assert extended.as_signed_long() == value
    assert extended.ctx == context
    assert z3.is_true(z3.simplify(extended == z3.SignExt(13, literal)))


@pytest.mark.parametrize('failure', [False, True])
def test_masked_update_closes_and_matches_every_signed_state(failure):
    update = ir.Assign('q', ir.Op('and', ir.Op('add', ir.Var('q'), ir.Var('a')), ir.Lit(15)))
    tail = ir.If(ir.Op('lt', ir.Var('q'), ir.Lit(0)),
                 ir.Fail('negative', 'negative') if failure else ir.Mark('negative'),
                 ir.Skip())
    proc = ir.Procedure(('q',), ('a',), (), (), ir._sequence((tail, update)),
                        initial={'q': 0},
                        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    template = StepTemplate(proc, {'q': ranges.Range(0, 15), 'a': ranges.Range(0, 15)})
    encoded = template.encode()
    assert encoded.pre['q'].size() == encoded.state_post['q'].size() == 5
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for state in range(-16, 16):
        for value in range(16):
            solver.push()
            solver.add(encoded.pre['q'] == state, encoded.pre['a'] == value)
            assert solver.check() == z3.sat
            model = solver.model()
            failed = failure and state < 0
            assert model.eval(encoded.state_post['q']).as_signed_long() == (
                state if failed else (state + value) & 15)
            assert z3.is_true(model.eval(encoded.ok)) == (not failed)
            assert z3.is_true(model.eval(encoded.hits['negative'])) == (state < 0)
            solver.pop()
