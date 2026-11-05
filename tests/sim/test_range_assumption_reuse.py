"""Reuse exact guard transfers without losing carriers or loop occurrences."""

import itertools

import pytest

from behave.sim.symbolic import cfg, ir, ranges


def _guards():
    x, y = ir.Var("x"), ir.Var("y")
    comparisons = [ir.Op(op, x, y) for op in ("lt", "le", "gt", "ge", "eq", "ne")]
    return [*comparisons, ir.Lit(True), ir.Lit(False), x,
            ir.Op("truth", ir.Op("and", x, ir.Lit(2))),
            ir.Op("not", comparisons[0]),
            ir.Op("land", comparisons[0], ir.Op("ge", x, ir.Lit(0))),
            ir.Op("lor", comparisons[0], ir.Op("ge", x, ir.Lit(0))),
            ir.Op("lt", ir.Sel(ir.Var("a"), y), x)]


@pytest.mark.parametrize("guard", _guards())
@pytest.mark.parametrize("truth", [False, True])
def test_normalized_structural_assumption_matches_public_transfer(guard, truth):
    intervals = [None, ranges.Bounds(), ranges.Bounds(None, 3),
                 ranges.Bounds(-2, None), ranges.Bounds(-3, 4), ranges.Bounds(0, 0)]
    for x, y in itertools.product(intervals, repeat=2):
        env = {"x": x, "y": y, "a": ranges.Bounds(-5, 8),
               "unread": ranges.Bounds(10, 11)}
        expected = ranges.assume(guard, truth, env)
        actual = ranges.assume_bounds(guard, truth, env)
        assert actual == expected
        if actual is not None:
            assert actual is not env
            assert actual["unread"] is env["unread"]
            actual.clear()
        assert env == {"x": x, "y": y, "a": ranges.Bounds(-5, 8),
                       "unread": ranges.Bounds(10, 11)}


def test_normalized_assumption_does_not_visit_unread_bindings(monkeypatch):
    original = ranges._bound
    unread = ranges.Bounds(12345, 12346)
    env = {f"unused_{i}": unread for i in range(1000)}
    env["x"] = ranges.Bounds(0, 9)

    def bound(value):
        assert value is not unread, "a guard should not renormalize unrelated bindings"
        return original(value)

    monkeypatch.setattr(ranges, "_bound", bound)
    result = ranges.assume_bounds(ir.Op("lt", ir.Var("x"), ir.Lit(4)), True, env)
    assert result["x"] == ranges.Bounds(0, 3)
    assert env["x"] == ranges.Bounds(0, 9)
    assert all(result[f"unused_{i}"] is unread for i in range(1000))


@pytest.mark.parametrize("operation,truth", [("lor", True), ("land", False)])
def test_disjunctive_structural_join_preserves_an_unknown_binding(operation, truth):
    env = {"x": None, "y": ranges.Bounds(0, 3)}
    predicate = ir.Op(operation, ir.Op("eq", ir.Var("x"), ir.Lit(0)),
                      ir.Op("eq", ir.Var("y"), ir.Lit(1)))
    for assume in (ranges.assume, ranges.assume_bounds):
        result = assume(predicate, truth, env)
        assert result["x"] is None
        assert result["y"] == ranges.Bounds(0, 3)
    assert env["x"] is None


@pytest.mark.parametrize("guard", _guards())
@pytest.mark.parametrize("truth", [False, True])
@pytest.mark.parametrize("copies", [(), (("copy", "x"),),
                                    (("copy", "x"), ("alias", "x"))])
def test_reuse_matches_uncached_transfer_for_every_binding(guard, truth, copies):
    cache = ranges._range_transfer_cache()
    intervals = [None, ranges.Range(-3, -1), ranges.Range(0, 0), ranges.Range(1, 3)]
    array = ranges._CellRange((ranges.Range(-2, -2), ranges.Range(4, 4)))
    for x, y, copy in itertools.product(intervals, repeat=3):
        env = {"a": array, "unread_array": array, "alias": ranges.Range(-4, 4)}
        env.update({name: value for name, value in (("x", x), ("y", y), ("copy", copy))
                    if value is not None})
        expected = ranges._finite_assumption(guard, truth, env, copies)
        for marker in (5, 11):
            # An unrelated binding changes between cache hits, not just calls
            # with the very same environment object.
            current = {**env, "unread": ranges.Range(marker, marker)}
            actual = ranges._finite_assumption(guard, truth, current, copies, cache=cache)
            assert actual == (None if expected is None else {
                **expected, "unread": current["unread"]})
            if actual is not None:
                assert actual["unread_array"] is array
                actual.clear()  # The caller owns the returned branch mapping.


def test_equal_array_hulls_do_not_make_distinct_cell_bindings_equal():
    guard = ir.Op("eq", ir.Sel(ir.Var("a"), ir.Lit(0)), ir.Lit(0))
    cache = ranges._range_transfer_cache()
    first = ranges._CellRange((ranges.Range(0, 0), ranges.Range(1, 1)))
    second = ranges._CellRange((ranges.Range(1, 1), ranges.Range(0, 0)))
    for array in (first, second, first):
        env = {"a": array}
        assert ranges._finite_assumption(guard, True, env, cache=cache) == (
            ranges._finite_assumption(guard, True, env))
    # A fixed selection now refutes the second binding even though the two
    # whole-array hulls are equal. The cached first binding cannot authorize it.
    assert ranges._finite_assumption(guard, True, {"a": second}, cache=cache) is None
    assert ranges._finite_assumption(guard, True, {"a": first}, cache=cache)["a"] is first
    common = ir.Op("ge", ir.Sel(ir.Var("a"), ir.Lit(0)), ir.Lit(0))
    for array in (first, second, first):
        assert ranges._finite_assumption(common, True, {"a": array}, cache=cache)["a"] is array


def test_repeated_guard_does_not_repeat_comparison_work(monkeypatch):
    original = ranges._assume_comparison
    calls = []

    def count(expr, *args, **kwargs):
        calls.append(expr)
        return original(expr, *args, **kwargs)

    monkeypatch.setattr(ranges, "_assume_comparison", count)
    guard = ir.Op("lt", ir.Var("x"), ir.Lit(4))
    cache = ranges._range_transfer_cache()
    for index in range(100):
        env = {"x": ranges.Range(0, 7), "i": ranges.Range(index, index)}
        assert ranges._finite_assumption(guard, True, env, cache=cache)["x"] == ranges.Range(0, 3)
    assert len(calls) == 1
    assert ranges._finite_assumption(
        guard, True, {"x": ranges.Range(5, 7)}, cache=cache) is None
    assert len(calls) == 2
    guard.args = (ir.Var("x"), ir.Lit(9))
    assert ranges._finite_assumption(
        guard, True, {"x": ranges.Range(5, 7)}, cache=ranges._range_transfer_cache()) == {
            "x": ranges.Range(5, 7)}


def test_shared_arithmetic_reused_across_changing_loop_guards(monkeypatch):
    common = ir.Op("add", ir.Op("mul", ir.Var("x"), ir.Var("x")), ir.Lit(5))
    guard = ir.Op("lt", ir.Op("sub", common, ir.Var("i")), ir.Lit(0))
    original = ranges._bounds_of_op
    calls = []

    def count(expr, parts):
        calls.append(id(expr))
        return original(expr, parts)

    monkeypatch.setattr(ranges, "_bounds_of_op", count)
    cache = ranges._range_transfer_cache()
    for index in range(20):
        env = {"x": ranges.Range(-2, 3), "i": ranges.Range(index, index)}
        with monkeypatch.context() as patch:
            patch.setattr(ranges, "_bounds_of_op", original)
            expected = ranges._finite_assumption(guard, True, env)
        assert ranges._finite_assumption(guard, True, env, cache=cache) == expected
    assert calls.count(id(common)) == 1
    assert calls.count(id(common.args[0])) == 1
    assert calls.count(id(guard.args[0])) == 20


def test_bounds_cache_keeps_missing_infinite_and_array_bindings_distinct():
    array = ir.Var("a")
    expression = ir.Op("add", ir.Sel(array, ir.Var("i")), ir.Var("x"))
    cache = ranges._range_transfer_cache()
    arrays = [ranges._CellRange((ranges.Range(0, 0), ranges.Range(1, 1))),
              ranges._CellRange((ranges.Range(1, 1), ranges.Range(0, 0)))]
    for index, bound, product in itertools.product(
            (0, 1, 2), (None, ranges.Bounds(), ranges.Bounds(0, None), ranges.Range(1, 5)), arrays):
        env = {"a": ranges.Range(0, 1), "i": ranges.Range(index, index)}
        if bound is not None:
            env["x"] = bound
        context = {"a": product}
        assert ranges.bounds_of_expression(expression, env, context, _cache=cache) == (
            ranges.bounds_of_expression(expression, env, context))


def test_cached_dependency_walk_handles_a_deep_shared_expression():
    expression = ir.Var("x")
    for _ in range(2000):
        expression = ir.Op("add", expression, ir.Lit(0))
    cache = ranges._range_transfer_cache()
    for low in (-2, 0, 8):
        env = {"x": ranges.Range(low, low + 3)}
        assert ranges.bounds_of_expression(expression, env, _cache=cache) == ranges.Bounds(low, low + 3)


def test_cached_literal_array_hull_does_not_hide_selected_child_bounds():
    array = ir.Op("arraylit", ir.Op("add", ir.Var("x"), ir.Lit(1)), ir.Lit(8))
    expression = ir.Sel(array, ir.Var("i"))
    cache = ranges._range_transfer_cache()
    for index in (0, 1, 0, 1):
        env = {"x": ranges.Range(0, 0), "i": ranges.Range(index, index)}
        assert ranges.bounds_of_expression(expression, env, _cache=cache) == (
            ranges.bounds_of_expression(expression, env))
    # Preserve the pre-existing arrays mapping semantics for an anonymous
    # array as well as named arrays, including repeated equal-hull products.
    for cell in (3, 7, 3):
        arrays = {None: ranges._UniformCellRange(2, ranges.Range(cell, cell))}
        assert ranges.bounds_of_expression(expression, env, arrays, _cache=cache) == ranges.Bounds(cell, cell)


def test_range_memos_follow_syntax_size_not_number_of_distinct_visits():
    expression = ir.Op("add", ir.Var("x"), ir.Lit(1))
    guard = ir.Op("lt", expression, ir.Var("limit"))
    command = ir.If(guard, ir.Assign("y", expression), ir.Assign("y", ir.Lit(-1)))
    loop = ir.For("i", 2, command)
    cache = ranges._range_transfer_cache()
    keys = (ranges._VALUE_RANGE_CACHE, ranges._BOUNDS_CACHE,
            ranges._FINITE_ASSUMPTION_CACHE, ranges._COMMAND_POST_CACHE)
    sizes = None
    for index in range(1000):
        env = {"x": ranges.Range(index, index), "limit": ranges.Range(4, 4)}
        assert ranges._value_range(expression, env, cache) == ranges.Range(index + 1, index + 1)
        assert ranges.bounds_of_expression(expression, env, _cache=cache) == ranges.Bounds(index + 1, index + 1)
        for truth in (False, True):
            assert ranges._finite_assumption(guard, truth, env, cache=cache) == (
                ranges._finite_assumption(guard, truth, env))
        assert ranges._post(loop, dict(env), _flows=cache) == ranges._post(loop, dict(env))
        current = tuple(len(cache.get(key, {})) for key in keys)
        if index == 10:
            sizes = current
        elif index > 10:
            assert current == sizes
    # Revisit an evicted binding: compute it again, never borrow the newer one.
    first = {"x": ranges.Range(0, 0), "limit": ranges.Range(4, 4)}
    assert ranges._post(loop, dict(first), _flows=cache) == ranges._post(loop, dict(first))


class _Observer:
    requires_complete_loop_visits = True
    requires_pointwise_loop_indices = True

    def __init__(self):
        self.visits = []

    def __call__(self, command, env):
        self.visits.append((id(command), dict(env)))

    def enter_loop_iteration(self, loop, turn):
        self.visits.append(("enter", id(loop), turn))

    def leave_loop_iteration(self, loop, turn):
        self.visits.append(("leave", id(loop), turn))


def test_reuse_preserves_every_nested_loop_position_and_branch(monkeypatch):
    body = ir.For("i", ir.Lit(5), ir.For("j", ir.Var("i"), ir.If(
        ir.Op("lt", ir.Var("x"), ir.Lit(4)),
        ir.Store("a", ir.Var("j"), ir.Var("i")),
        ir.Assign("x", ir.Op("sub", ir.Var("x"), ir.Lit(1))))))
    env = {"x": ranges.Range(0, 8), "a": ranges._UniformCellRange(5, ranges.Range(0, 0))}
    cached, plain = _Observer(), _Observer()
    actual = ranges.walk_post(body, dict(env), cached)
    original = ranges._finite_assumption

    def uncached(expr, truth, env, copies=(), *, cache=None):
        return original(expr, truth, env, copies)

    monkeypatch.setattr(ranges, "_finite_assumption", uncached)
    expected = ranges.walk_post(body, dict(env), plain)
    assert actual == expected
    assert cached.visits == plain.visits
    assert len([row for row in plain.visits if row[0] == "enter"]) == 15


def test_graph_refinement_keeps_aliases_missing_names_and_infeasible_edges(monkeypatch):
    graph = cfg.CFG({
        "entry": cfg.Block("entry", (ir.Assign("copy", ir.Var("x")),),
                           cfg.Branch(ir.Op("lt", ir.Var("copy"), ir.Lit(4)), "yes", "no")),
        "yes": cfg.Block("yes", (ir.Assign("z", ir.Var("copy")),), cfg.Return()),
        "no": cfg.Block("no", (ir.Fail("negative-control"),), cfg.Return()),
    }, "entry")
    original = ranges._finite_assumption

    def uncached(expr, truth, env, copies=(), *, cache=None):
        return original(expr, truth, env, copies)

    for env in ({}, {"x": ranges.Range(0, 7)}, {"x": ranges.Range(5, 9)}):
        for include_dead in (False, True):
            cached, plain = _Observer(), _Observer()
            actual = ranges.graph_post(graph, env, cached, _include_infeasible_edges=include_dead)
            with monkeypatch.context() as patch:
                patch.setattr(ranges, "_finite_assumption", uncached)
                expected = ranges.graph_post(graph, env, plain, _include_infeasible_edges=include_dead)
            assert actual == expected
            assert cached.visits == plain.visits


def test_repeated_graph_visits_reuse_guard_syntax_not_its_truth(monkeypatch):
    graph = cfg.CFG({
        "entry": cfg.Block("entry", (ir.Assign("copy", ir.Var("x")),),
            cfg.Branch(ir.Op("lt", ir.Var("copy"), ir.Lit(4)), "yes", "no")),
        "yes": cfg.Block("yes", (ir.Assign("y", ir.Var("copy")),), cfg.Return()),
        "no": cfg.Block("no", (ir.Assign("y", ir.Lit(-1)),), cfg.Return()),
    }, "entry")
    original, calls = ranges._PredicateCopies.predicate, []

    def recovered(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(ranges._PredicateCopies, "predicate", recovered)
    cache = ranges._range_transfer_cache()
    for index in (*range(100), 0):
        env = {"x": ranges.Range(index, index)}
        actual, expected = _Observer(), _Observer()
        got = ranges.graph_post(graph, env, actual, _flows=cache)
        with monkeypatch.context() as patch:
            patch.setattr(ranges._PredicateCopies, "predicate", original)
            want = ranges.graph_post(graph, env, expected)
        assert got == want
        assert actual.visits == expected.visits
    assert len(calls) == 1
    assert len(cache[ranges._FINITE_ASSUMPTION_CACHE]) == 2
