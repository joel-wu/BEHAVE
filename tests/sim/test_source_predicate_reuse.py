"""Predicate syntax is reusable; path bounds and outcomes are not."""

import ast

import pytest

from behave.sim.symbolic import ranges
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.source_sequences import (
    _FunctionContracts, _ScalarBindings, _SourceQueryCache,
    _refine_scalar_ranges,
)
from behave.sim.behavior.source_values import (
    SCALAR, SourceValueBudget, ValueContract, contract_of_expression,
)


SOURCE = """
LIMIT = 4
class M:
    def helper(self, LIMIT):
        return LIMIT
    def process(self, a):
        return {"y": a}
"""


def _planner():
    model = source_model(SOURCE)
    return _FunctionContracts(model, ScopeResolver(model))


@pytest.mark.parametrize("expression", (
    "a < b", "a <= b", "a > b", "a >= b", "a == b", "a != b",
    "a + 2 < b * 3", "(a & 7) == b", "a // 2 > b",
    "a % 2 == b", "(a if a < 2 else b) < 3",
    "0 < a < b", "not (a < b)", "a < b and b < 3",
    "self.count < b", "a < LIMIT", "a << 2 < b", "a >> 1 < b",
))
def test_reused_predicate_matches_uncached_refinement(expression):
    planner = _planner()
    fn = planner.model.step
    node = ast.parse(expression, mode="eval").body
    for fixed in ({}, {"a": 0}, {"a": 3}, {"b": 2}, {"LIMIT": 9}):
        for lo, hi in ((-4, -1), (-2, 5), (0, 0), (1, 10)):
            bindings = _ScalarBindings(fixed, scalar_bounds={
                "a": ranges.Bounds(lo, hi),
                "b": ranges.Bounds(0, 6),
                "self.count": ranges.Bounds(lo, hi),
                "unread": ranges.Bounds(-100, 100),
            }, guard_facts={("caller", "unread"): True})
            for truth in (True, False):
                expected = _refine_scalar_ranges(
                    node, planner.resolver.owner_of(fn), bindings, truth,
                    budget=SourceValueBudget())
                got = planner._refine_scalar_condition(fn, node, bindings, truth)
                if expected is None:
                    assert got is None
                else:
                    assert got is not bindings
                    assert dict(got) == dict(expected)
                    assert got.bounds == expected.bounds
                    assert got.guards == expected.guards


def test_translation_reuse_across_layers_does_not_reuse_a_branch_answer():
    planner = _planner()
    fn = planner.model.step
    node = ast.parse("i < count", mode="eval").body
    cache = _SourceQueryCache()
    budget = SourceValueBudget(100)
    spent = None
    for upper in range(1, 20):
        layer = _FunctionContracts(
            planner.model, planner.resolver, budget=budget, query_cache=cache)
        bindings = _ScalarBindings({"i": 3}, scalar_bounds={
            "count": ranges.Bounds(0, upper),
            "unrelated": ranges.Bounds(upper, upper),
        })
        got = layer._refine_scalar_condition(fn, node, bindings, True)
        if upper <= 3:
            assert got is None
        else:
            assert got.bounds["count"] == ranges.Bounds(4, upper)
            assert got.bounds["unrelated"] == ranges.Bounds(upper, upper)
        if spent is None:
            spent = budget.left
        assert budget.left == spent
    assert len(cache.predicates) == 1

    changed = _ScalarBindings({"i": 1}, scalar_bounds={
        "count": ranges.Bounds(0, 2)})
    got = layer._refine_scalar_condition(fn, node, changed, True)
    assert got.bounds["count"] == ranges.Bounds(2, 2)
    assert len(cache.predicates) == 2
    assert budget.left < spent


def test_predicate_translation_keeps_lexical_ownership():
    planner = _planner()
    node = ast.parse("a < LIMIT", mode="eval").body
    helper = next(n for n in ast.walk(planner.model.tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "helper")
    bindings = _ScalarBindings({}, scalar_bounds={
        "a": ranges.Bounds(5, 5), "LIMIT": ranges.Bounds(6, 8)})
    assert planner._refine_scalar_condition(
        planner.model.step, node, bindings, True) is None
    got = planner._refine_scalar_condition(helper, node, bindings, True)
    assert got is not None
    assert got.bounds["LIMIT"] == ranges.Bounds(6, 8)
    assert len(planner._query_cache.predicates) == 2


def test_selected_cell_does_not_refine_the_whole_array():
    planner = _planner()
    node = ast.parse("items[a] < 3", mode="eval").body
    for bounds in ({}, {"#element:items": ranges.Bounds(0, 10)},
                   {"#element:items": ranges.Bounds(5, 8)}):
        bindings = _ScalarBindings({}, scalar_bounds=bounds)
        got = planner._refine_scalar_condition(
            planner.model.step, node, bindings, True)
        if bounds.get("#element:items") == ranges.Bounds(5, 8):
            assert got is None
        else:
            assert got.bounds == bindings.bounds


def test_final_comparison_needs_no_unused_truth_query():
    node = ast.parse("a < b", mode="eval").body
    budget = SourceValueBudget(3)
    visited = []

    def resolve(child, bindings):
        visited.append(child.id)
        return SCALAR

    def no_unused_truth(*args):
        pytest.fail("no remaining operand needs a short-circuit decision")

    assert contract_of_expression(
        node, resolve=resolve, static_value=no_unused_truth,
        known_truth=no_unused_truth, budget=budget) is SCALAR
    assert visited == ["a", "b"]
    assert budget.left == 0


@pytest.mark.parametrize("values, visited, queried", (
    ({"a": 0, "b": 1, "c": 2, "d": 3}, ["a", "b", "c", "d"],
     ["a", "b", "b", "c"]),
    ({"a": 2, "b": 1, "c": 2, "d": 3}, ["a", "b"], ["a", "b"]),
    ({"a": 0, "b": 2, "c": 1, "d": 3}, ["a", "b", "c"],
     ["a", "b", "b", "c"]),
))
def test_chained_comparison_still_short_circuits(values, visited, queried):
    reached, fixed = [], []

    def resolve(node, bindings):
        reached.append(node.id)
        return SCALAR

    def static(node, bindings):
        fixed.append(node.id)
        return values[node.id]

    assert contract_of_expression(
        ast.parse("a < b < c < d", mode="eval").body,
        resolve=resolve, static_value=static) is SCALAR
    assert reached == visited
    assert fixed == queried


def test_scalar_contract_reuse_records_current_occurrence_ranges():
    planner = _planner()
    node = ast.parse("a + 1 < b * 2", mode="eval").body
    after_first = None
    for upper in range(1, 12):
        context = ("layer", upper)
        planner._context[:] = [context]
        bindings = _ScalarBindings({}, scalar_bounds={
            "a": ranges.Bounds(0, upper), "b": ranges.Bounds(2, 5)})
        assert planner._expr(planner.model.step, node,
                             {"a": SCALAR, "b": SCALAR}, bindings) is SCALAR
        if after_first is None:
            after_first = planner.budget.left
        assert planner.budget.left == after_first
        assert planner.numeric_bounds(node, context)["a"] == ranges.Bounds(0, upper)
        assert planner.numeric_bounds(node.left.left, context)["a"] == ranges.Bounds(0, upper)


def test_range_sensitive_sequence_repetition_is_not_a_scalar_query():
    planner = _planner()
    node = ast.parse("items * n", mode="eval").body
    for upper in (2, 5, 3):
        planner._context[:] = [("layer", upper)]
        got = planner._expr(
            planner.model.step, node,
            {"items": ValueContract.sequence("list", 2, 2, SCALAR),
             "n": SCALAR},
            _ScalarBindings({}, scalar_bounds={"n": ranges.Bounds(1, upper)}))
        assert (got.minimum, got.maximum) == (2, 2 * upper)
