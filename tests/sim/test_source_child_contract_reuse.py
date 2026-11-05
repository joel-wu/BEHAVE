"""Child reuse has the same activation proof as a top-level scalar query."""

import ast

import pytest

from behave.sim.symbolic import ranges
from behave.sim.behavior.lowering import _Lowerer
from behave.sim.behavior.source_sequences import SequencePlan
from behave.sim.behavior.source_values import SCALAR, UNKNOWN, SourceValueBudget, ValueContract


def lowerer_for(node, *, planned=True):
    low = _Lowerer([], env={'a': ranges.Range(0, 7)})
    scope = ast.parse('def process(a): pass').body[0]
    context = ('entry',)
    low.activation.scope = scope
    low.activation.summary = context
    low.sequence_plan = SequencePlan(
        context_functions=((context, scope),),
        expression_contracts=(((context, id(child)), SCALAR)
                              for child in ast.walk(node) if planned and isinstance(child, ast.expr)),
    )
    low._source_value_budget = SourceValueBudget(1000)
    return low


def test_proved_child_contract_is_shared_between_parent_and_direct_queries():
    child = ast.parse('a + 3', mode='eval').body
    low = lowerer_for(child)
    first = low._contract_ast(ast.List(elts=[child], ctx=ast.Load()))
    before = low._source_value_budget.left
    assert low._contract_ast(child) is SCALAR
    assert low._source_value_budget.left == before
    second = low._contract_ast(ast.Tuple(elts=[child], ctx=ast.Load()))
    assert first.element is SCALAR and second.element is SCALAR
    assert before - low._source_value_budget.left == 1  # Only the new parent is visited.


@pytest.mark.parametrize('overrides', [
    {'bindings': {'a': [1, 2]}},
    {'contracts': {'a': ValueContract.sequence('list', 2, 2, SCALAR)}},
    {'contracts': {'a': UNKNOWN}},
])
def test_explicit_bindings_do_not_inherit_a_cached_scalar_child(overrides):
    child = ast.parse('a + 3', mode='eval').body
    low = lowerer_for(child)
    wrapped = ast.List(elts=[child], ctx=ast.Load())
    low._contract_ast(wrapped)
    # Disable cached facts only in the independent control. Both calls must
    # still apply the explicit binding/contract rather than the source plan.
    control = lowerer_for(child)
    control.activation.summary = None
    before = low._source_value_budget.left
    assert low._contract_ast(wrapped, **overrides) == control._contract_ast(wrapped, **overrides)
    assert before - low._source_value_budget.left > 1  # Not just the cached parent's child.


def test_unproved_or_different_activation_does_not_reuse_scalar_children():
    child = ast.parse('a + 3', mode='eval').body
    for low in (lowerer_for(child, planned=False), lowerer_for(child)):
        low._contract_ast(ast.List(elts=[child], ctx=ast.Load()))
        low.activation.summary = ('different',)
        before = low._source_value_budget.left
        assert low._contract_ast(child) is SCALAR
        assert before - low._source_value_budget.left >= 2


def test_no_plan_does_not_cache_a_changing_collection_shape():
    child = ast.parse('a', mode='eval').body
    low = lowerer_for(child, planned=False)
    low._contract_ast(ast.List(elts=[child], ctx=ast.Load()))
    low.flow.ranges.clear()
    low.flow.contracts['a'] = ValueContract.sequence('tuple', 2, 2, SCALAR)
    result = low._contract_ast(ast.List(elts=[child], ctx=ast.Load()))
    assert result.element == low.flow.contracts['a']
