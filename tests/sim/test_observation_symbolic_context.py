"""Logical observation literals belong to their caller's solver context."""

from functools import partial

import pytest
import z3

from behave.sim import api
from behave.sim.core.observation_product import TransactionProduct
from behave.sim.core.observation_symbolic import logical_payload, logical_zero, predicate_term
from behave.sim.rtl.task_symbolic import Z3Algebra

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('kind,expected', [
    (api.ValueType.boolean(), True), (api.FiniteType(1), 1), (api.FiniteType(1, signed=True), -1)])
def test_one_bit_boolean_packing_and_predicates_use_private_context(kind, expected):
    context = z3.Context()
    endpoint = api.Endpoint('out', 'out', [api.Field('value', kind, 'y')], api.settled('out'), None)
    contract = api.TaskContract([endpoint], [], observation_goals=(
        api.TransactionGoal('value', 'out', api.GoalCondition('eq', api.GoalField('value'), expected)),))
    binding = TransactionProduct(contract)
    algebra = Z3Algebra(context)
    initial = binding.initial(algebra, zero=partial(logical_zero, context=context))
    bits = z3.Bool('bits', ctx=context)
    _state, hits = binding.advance(initial,
        {'out': ((algebra.bool(True), logical_payload(endpoint, (bits,))),)}, algebra,
        predicate=partial(predicate_term, context=context))
    solver = z3.Solver(ctx=context)
    solver.add(hits['observation:value'] != bits)
    assert solver.check() == z3.unsat
