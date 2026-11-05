"""Partial Boolean facts must avoid dead array work without discarding roots."""

import pytest
import z3

from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.template_arrays import TemplateArrays

pytestmark = pytest.mark.solver


def image(*terms):
    return z3.Function('_step_fields', *(term.sort() for term in terms),
                       z3.BoolSort(ctx=terms[0].ctx))(*terms)


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('conjunction', [False, True])
def test_decisive_operand_does_not_expand_array_reads(monkeypatch, private_context, conjunction):
    arrays = TemplateArrays()
    gate = z3.Var(0, z3.BoolSort())
    data = z3.Var(1, z3.BitVecSort(8))
    array = FunctionalArray.uniform(4, z3.BitVecVal(0, 8)).store(
        z3.BitVecVal(2, 4), data, z3.BoolVal(True))
    read = z3.Select(arrays.term(array), z3.BitVecVal(2, 4)) == 7
    boolean = z3.And if conjunction else z3.Or
    term = image(boolean(gate, read))
    context = z3.Context() if private_context else z3.main_ctx()
    if private_context:
        term = term.translate(context)

    def no_read(*_args):
        pytest.fail('an absorbing Boolean constant does not need an array read')

    monkeypatch.setattr(FunctionalArray, 'select', no_read)
    result, = arrays.instantiate(term, [z3.BoolVal(not conjunction, ctx=context),
                                        z3.BitVec('unneeded', 8, ctx=context)])
    assert z3.is_false(result) if conjunction else z3.is_true(result)


@pytest.mark.parametrize('conjunction', [False, True])
@pytest.mark.parametrize('fixed', [False, True, None])
def test_boolean_folding_preserves_symbolic_truth_table_and_all_shared_roots(conjunction, fixed):
    arrays = TemplateArrays()
    parameters = [z3.Var(i, z3.BoolSort()) for i in range(3)]
    a, b, c = parameters
    boolean = z3.And if conjunction else z3.Or
    inner = boolean(a, z3.And(b, c), b)
    shared = z3.And(b, c)
    expressions = (inner, shared, z3.Or(inner, z3.Not(shared)),
                   boolean(z3.BoolVal(conjunction), b, z3.BoolVal(conjunction)))
    args = list(z3.Bools('actual_a actual_b actual_c'))
    if fixed is not None:
        args[0] = z3.BoolVal(fixed)
    actual = arrays.instantiate(image(*expressions), args)
    expected = [z3.substitute_vars(term, *args) for term in expressions]
    solver = z3.Solver()
    solver.add(z3.Or(*(left != right for left, right in zip(actual, expected, strict=True))))
    assert solver.check() == z3.unsat
    assert z3.eq(actual[-1], args[1])  # Identity operands are removed too.


def test_array_expression_referenced_by_another_root_is_still_observed():
    arrays = TemplateArrays()
    gate, data = z3.Var(0, z3.BoolSort()), z3.Var(1, z3.BitVecSort(8))
    array = FunctionalArray.uniform(4, z3.BitVecVal(0, 8)).store(
        z3.BitVecVal(2, 4), data, z3.BoolVal(True))
    read = z3.Select(arrays.term(array), z3.BitVecVal(2, 4)) == 7
    result, independent = arrays.instantiate(image(z3.And(gate, read), read),
                                            [z3.BoolVal(False), z3.BitVecVal(7, 8)])
    assert z3.is_false(result)
    assert z3.is_true(z3.simplify(independent))
