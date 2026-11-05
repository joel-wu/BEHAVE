"""Source planning shares syntax, never a different activation's facts."""

import ast

import pytest

from behave.sim.symbolic import ranges
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.source_sequences import (
    _FunctionContracts, _ScalarBindings, _SourceQueryCache,
)


@pytest.mark.parametrize("expression, selected, static", (
    ("a + 1", set(), {"a"}),
    ("self.count + a", set(), {"self", ("self", "count"), "a"}),
    ("items[i][j] + self.table[k]", {"#element:items", "self.table"}, set()),
    ("[x + self.offset for x in values if x < limit]", set(),
     {"x", "self", ("self", "offset"), "values", "limit"}),
    ("sum(x for x in values)", set(), set()),
    ("a if ready else b", set(), {"a", "ready", "b"}),
    ("helper(items[i], **options)", {"#element:items"}, set()),
    ("self.helper()[i]", set(), set()),
))
def test_static_dependencies_and_runtime_projection_stay_distinct(expression, selected, static):
    node = ast.parse(expression, mode="eval").body
    occurrences = tuple(ast.walk(node))
    names = {child.id for child in occurrences if isinstance(child, ast.Name)}
    fields = {child.attr for child in occurrences
              if (isinstance(child, ast.Attribute)
                  and isinstance(child.value, ast.Name)
                  and child.value.id == "self")}
    dependencies = names | {("self", name) for name in fields}
    numeric = names | {f"self.{name}" for name in fields}
    syntax = _SourceQueryCache().syntax_of(node)
    assert syntax.dependencies == static
    assert syntax.environment_keys == (
        names, fields, dependencies | numeric, numeric)
    assert all(isinstance(keys, frozenset) for keys in syntax.environment_keys)
    assert syntax.selected == selected
    assert syntax.expressions == tuple(
        child for child in occurrences if isinstance(child, ast.expr))


def test_state_layers_share_one_walk_but_keep_current_bounds(monkeypatch):
    model = source_model('''
class M:
    def process(self, a):
        return {"y": a}
''')
    resolver = ScopeResolver(model)
    node = ast.parse("items[i] + self.count", mode="eval").body
    cache = _SourceQueryCache()
    walk = ast.walk
    walked = []

    def record_walk(root):
        walked.append(root)
        return walk(root)

    monkeypatch.setattr(ast, "walk", record_walk)
    for upper in (2, 7, 4):
        planner = _FunctionContracts(model, resolver, query_cache=cache)
        planner._context[:] = [("layer", upper)]
        bindings = _ScalarBindings({"i": upper}, scalar_bounds={
            "#element:items": ranges.Bounds(-upper, upper),
            "self.count": ranges.Bounds(0, upper),
            "unread": ranges.Bounds(3, 8),
        })
        structural = planner._structural_bindings(bindings, node)
        assert structural["i"] == upper
        assert structural.bounds == {
            "#element:items": ranges.Bounds(-upper, upper),
            "self.count": ranges.Bounds(0, upper),
        }
        planner._numeric_query_key(model.step, node, bindings)
        planner._remember_fixed(node, bindings)
        # Array-leaf envelopes stay in their private numeric namespace.
        assert planner.numeric_bounds(node, planner._context[-1]) == {
            "self.count": ranges.Bounds(0, upper),
            "#element:items": ranges.Bounds(-upper, upper)}
        assert "#element:items" in planner._query_dependency_sets(node)[3]
    assert sum(root is node for root in walked) == 1


def test_distinct_source_occurrences_keep_distinct_descendants():
    cache = _SourceQueryCache()
    first = ast.parse("a + 1", mode="eval").body
    second = ast.parse("a + 1", mode="eval").body
    a, b = cache.syntax_of(first), cache.syntax_of(second)
    assert a.environment_keys == b.environment_keys
    assert a.expressions[0] is first
    assert b.expressions[0] is second
    assert a.expressions[1] is not b.expressions[1]
    assert cache.syntax_of(first) is a


def test_binding_projection_reads_only_declared_dependencies():
    class Bindings(dict):
        def __iter__(self):
            pytest.fail("a projected query must not walk unrelated locals")

        def keys(self):
            pytest.fail("a projected query must not copy all locals")

        def items(self):
            pytest.fail("a projected query must not walk unrelated locals")

    bindings = Bindings(a=1, b=True, c=None, unread=42)
    key = _FunctionContracts._static_binding_key(
        bindings, ("a", "b", "c", "missing", "a"))
    assert key == (("'a'", ("int", 1)), ("'b'", ("bool", True)),
                   ("'c'", ("unproved",)))
    bindings["a"] = 3
    assert _FunctionContracts._static_binding_key(bindings, ("a",)) == (
        ("'a'", ("int", 3)),)
    assert _FunctionContracts._static_binding_key(None, ("a",)) == ()
    assert _FunctionContracts._static_binding_key({"a": 1}) == (
        ("'a'", ("int", 1)),)


@pytest.mark.parametrize("expression", (
    "a + 1", "self.count + a", "items[i][j]", "record['key']",
    "a if ready else b", "a < b < c", "items[start:stop]",
    "items[helper():]", "helper(a)", "[a for a in items]", "None",
))
def test_cached_purity_keeps_record_and_unknown_record_contexts(expression):
    from behave.sim.behavior.source_sequences import _contract_expression_is_pure

    node = ast.parse(expression, mode="eval").body
    cache = _SourceQueryCache()
    for records in (None, frozenset(), frozenset({"record"}),
                    frozenset({"items"}), frozenset({"unread"}), None):
        assert cache.is_pure(node, record_names=records) == \
            _contract_expression_is_pure(node, record_names=records)


def test_contract_key_avoids_unrelated_bindings_and_repeated_syntax_walks(monkeypatch):
    from behave.sim.behavior.source_values import SCALAR, ValueContract

    class Projected(dict):
        def __iter__(self):
            pytest.fail("a contract query must not traverse unrelated bindings")

        def keys(self):
            pytest.fail("a contract query must not copy the whole environment")

        def items(self):
            pytest.fail("a contract query must read just its syntactic dependencies")

    model = source_model('''
class M:
    def process(self, a):
        return {"y": a}
''')
    planner = _FunctionContracts(model, ScopeResolver(model))
    planner._context[:] = [("test",)]
    node = ast.parse("items[i] + self.count", mode="eval").body
    sequence = ValueContract.sequence("list", 4, 4, SCALAR)
    env = Projected(items=sequence, i=SCALAR, unread=SCALAR)
    bindings = _ScalarBindings({"i": 1}, scalar_bounds={
        "i": ranges.Bounds(0, 3), "self.count": ranges.Bounds(0, 8),
        "unread": ranges.Bounds(0, 100),
    })
    # Numeric projections also retain private selected-cell facts. The
    # fixed-value mapping still must not scan unrelated local bindings.
    abstract = Projected(i=SCALAR, unread=sequence)
    first = planner._contract_query_key(model.step, node, env, bindings, abstract)
    assert first is not None

    def no_walk(*_args, **_kwargs):
        pytest.fail("a repeated query must reuse its existing syntax facts")

    monkeypatch.setattr(ast, "walk", no_walk)
    monkeypatch.setattr(ast, "iter_child_nodes", no_walk)
    env["unread"] = sequence
    bindings.bounds["unread"] = ranges.Bounds(-100, 100)
    assert planner._contract_query_key(model.step, node, env, bindings, abstract) == first
    bindings.bounds["self.count"] = ranges.Bounds(0, 9)
    assert planner._contract_query_key(model.step, node, env, bindings, abstract) != first
    bindings.bounds["self.count"] = ranges.Bounds(0, 8)
    bindings["i"] = 2
    assert planner._contract_query_key(model.step, node, env, bindings, abstract) != first


@pytest.mark.parametrize("expression", (
    "a + 1", "a < b", "a < b < 4", "a if b else 0", "a[0]", "self.a", "None",
))
def test_shared_expression_list_preserves_scalar_bounds_decision(expression):
    from behave.sim.behavior.source_sequences import _scalar_contract_ignores_bounds
    from behave.sim.behavior.source_values import SCALAR, ValueContract

    node = ast.parse(expression, mode="eval").body
    syntax = _SourceQueryCache().syntax_of(node)
    for a in (SCALAR, ValueContract.sequence("list", 2, 2, SCALAR)):
        env = {"a": a, "b": SCALAR}
        for bindings in ({}, {"a": 1}):
            for abstract in (None, {"a": a}):
                assert _scalar_contract_ignores_bounds(
                    node, env, bindings, abstract, expressions=syntax.expressions
                ) == _scalar_contract_ignores_bounds(node, env, bindings, abstract)
