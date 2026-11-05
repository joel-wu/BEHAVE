"""Comparison-selected operands retain their order without solver assumptions."""

import itertools
import operator
from copy import deepcopy

import pytest

from behave.sim import api
from behave.sim.behavior import signature
from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.behavior.task_boundary import lower_task_behavior


def _contract():
    return api.TaskContract(endpoints=[
        api.Endpoint("in", api.Direction.IN,
                     [api.Field(name, api.FiniteType(4), name) for name in ("a", "b")],
                     api.ready_valid("in", "in_valid", "in_ready"), clock="clk"),
        api.Endpoint("out", api.Direction.OUT, [api.Field("y", api.FiniteType(3), "y")],
                     api.ready_valid("out", "out_valid", "out_ready"), clock="clk"),
    ], clocks=[api.ClockDomain("clk", "clk", "rst")])


def test_exact_structural_comparison_matches_typed_digest_without_hashing(monkeypatch):
    leaves = [ir.Var("x"), ir.Var("y"), ir.Lit(0), ir.Lit(False),
              ir.Lit(1), ir.Lit(1, types.INTEGER), ir.Lit(True, types.BOOLEAN)]
    trees = [*leaves, *[ir.Op(op, a, b) for op in ("add", "mul", "eq")
                       for a, b in itertools.product(leaves[:3], repeat=2)],
             ir.Sel(ir.Op("arraylit", ir.Lit(1), ir.Lit(2)), ir.Var("x"))]
    copies = deepcopy(trees)
    fingerprints = [ir.expression_fingerprint(tree) for tree in trees]
    def no_digest(*args):
        pytest.fail("in-memory structural equality must not serialize or hash IR")
    monkeypatch.setattr(ir, "expression_fingerprint", no_digest)
    for i, left in enumerate(trees):
        for j, right in enumerate(copies):
            assert ir.expressions_equal(left, right) == (fingerprints[i] == fingerprints[j])
    x = ir.Var("x")
    square = ir.Op("mul", x, ir.Var("x"))
    assert ranges.of_expression(square, {"x": ranges.Range(-2, 3)}) == ranges.Range(0, 9)
    maximum = ir.Op("ite", ir.Op("gt", ir.Var("x"), ir.Lit(0)), x, ir.Lit(0))
    assert ranges.of_expression(maximum, {"x": ranges.Range(-2, 3)}) == ranges.Range(0, 3)


def test_structural_comparison_is_iterative_and_does_not_cache_mutable_ir():
    left, right = ir.Var("x"), ir.Var("x")
    root_left, root_right = left, right
    for _ in range(3000):
        root_left = ir.Op("add", root_left, root_left)
        root_right = ir.Op("add", root_right, root_right)
    assert ir.expressions_equal(root_left, root_right)
    right.name = "y"
    assert not ir.expressions_equal(root_left, root_right)


@pytest.mark.parametrize("comparison", ["lt", "le", "gt", "ge"])
@pytest.mark.parametrize("swapped", [False, True])
@pytest.mark.parametrize("copied", [False, True])
@pytest.mark.parametrize("intervals", [((-5, 7), (-2, 8)), ((0, 0), (0, 0)),
                                     ((-8, -4), (2, 3)), ((1, 6), (-7, -1))])
def test_compound_ordered_choice_encloses_every_value(comparison, swapped, copied, intervals):
    x, y = ir.Var("x"), ir.Var("y")
    left = ir.Op("fdiv", ir.Op("sub", x, ir.Lit(2)), ir.Lit(3))
    right = ir.Op("add", y, ir.Lit(1))
    arms = (right, left) if swapped else (left, right)
    if copied:
        arms = deepcopy(arms)
    expression = ir.Op("ite", ir.Op(comparison, left, right), *arms)
    env = {name: ranges.Range(*bound) for name, bound in zip(("x", "y"), intervals)}
    got = ranges.of_expression(expression, env)
    assert ranges.bounds_of_expression(expression, env) == ranges.Bounds(got.lo, got.hi)
    assert ranges.post(ir.Assign("chosen", expression), env)["chosen"] == got
    predicate = getattr(operator, comparison)
    values = []
    for a, b in itertools.product(*(range(lo, hi + 1) for lo, hi in intervals)):
        first, second = (a - 2) // 3, b + 1
        yes, no = (second, first) if swapped else (first, second)
        expected = yes if predicate(first, second) else no
        assert interp._Interp({"x": a, "y": b}).value(expression) == expected
        assert got.lo <= expected <= got.hi
        values.append(expected)
    assert got == ranges.Range(min(values), max(values))


def test_do_not_combine_bounds_from_different_selected_paths():
    # x > y refines x to [1, 2] only on the true path. Combining that
    # interval with y's false-path [0, 3] as max would wrongly exclude 0.
    x, y = ir.Var("x"), ir.Var("y")
    expression = ir.Op("ite", ir.Op("gt", x, y), x, y)
    env = {"x": ranges.Range(-1, 2), "y": ranges.Range(0, 3)}
    assert ranges.post(ir.Assign("chosen", expression), env)["chosen"] == ranges.Range(0, 3)


@pytest.mark.parametrize("comparison", ["eq", "ne", "lt", "le", "gt", "ge"])
def test_nonmatching_choice_is_not_an_extremum(comparison):
    expression = ir.Op("ite", ir.Op(comparison, ir.Var("x"), ir.Lit(0)),
                       ir.Op("sub", ir.Var("x"), ir.Lit(5)), ir.Lit(3))
    assert ranges._ordered_choice(expression) is None
    env = {"x": ranges.Range(-8, 8)}
    got = ranges.post(ir.Assign("chosen", expression), env)["chosen"]
    for value in range(-8, 9):
        expected = interp._Interp({"x": value}).value(expression)
        assert got.lo <= expected <= got.hi


def test_unknown_operand_does_not_gain_a_finite_upper_bound():
    x, one = ir.Var("x"), ir.Lit(1)
    expression = ir.Op("ite", ir.Op("gt", x, one), x, one)
    assert ranges.of_expression(expression, {}) is None
    bounded = ranges.bounds_of_expression(expression, {"x": ranges.Bounds(-8, None)})
    assert bounded is None or bounded.hi is None


def test_deep_shared_extrema_stay_iterative():
    expression = ir.Var("x")
    for value in range(2000):
        bound = ir.Lit(value)
        expression = ir.Op("ite", ir.Op("gt", expression, bound), expression, bound)
    env = {"x": ranges.Range(-8, 8)}
    assert ranges.of_expression(expression, env) == ranges.Range(1999, 1999)
    assert ranges.post(ir.Assign("chosen", expression), env)["chosen"] == ranges.Range(1999, 1999)


@pytest.mark.parametrize("returned,expected", [
    ("max(1, (a - b + 2) // 3)", lambda a, b: max(1, (a - b + 2) // 3)),
    ("min(7, max(0, (a - b) // 2))", lambda a, b: min(7, max(0, (a - b) // 2))),
])
def test_source_output_contract_clamp_needs_no_exact_fallback(monkeypatch, returned, expected):
    def unexpected(*args, **kwargs):
        pytest.fail("an interval-proved clamp must not construct an exact output formula")

    monkeypatch.setattr(encoder, "encode_step", unexpected)
    source = ("class M:\n"
              "    def reset(self): pass\n"
              "    def process(self, a, b):\n"
              f"        return {{'y': {returned}}}\n")
    model, _ = lower_task_behavior(source, "behavior.py", _contract(), 100)
    evidence = signature.output_range_evidence(model.proc, model.env)
    assert evidence["y"]["method"] == "range"
    for a, b in itertools.product(range(16), repeat=2):
        _state, outputs, _hits, error = interp.run_step(model.proc, {"a": a, "b": b})
        assert not error
        assert outputs.transactions == [{"y": expected(a, b)}]


@pytest.mark.solver
def test_every_clamped_source_goal_matches_native_and_local_query():
    import z3

    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_plan import plan_source

    source = """class M:
    def reset(self): pass
    def process(self, a, b):
        clipped = min(3, max(0, (a - b) // 2))
        y = 0
        for i in range(clipped):
            y += 1
        if clipped == 2 and a > b:
            y = 3
        return {"y": y}
"""
    plan = plan_source(source_model(source), source)
    model, _ = lower_task_behavior(source, "behavior.py", _contract(), 1)
    assert model.frontend_catalogue_manifest() == plan.manifest
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=plan)
    native = namespace["M"]()
    oracle = []
    for a, b in itertools.product(range(16), repeat=2):
        native.reset()
        native_hits.clear()
        expected = native.process(a, b)
        _state, output, hits, error = interp.run_step(model.proc, {"a": a, "b": b})
        assert not error
        assert output.transactions == [expected]
        assert hits == set(native_hits)
        oracle.append(frozenset(hits))

    encoded = encoder.encode_step(model.proc, env=model.env)
    solver = z3.Solver()
    solver.add(encoded.constraint, *(z3.ULE(encoded.pre[name], 15) for name in ("a", "b")))
    assert set(plan.manifest) <= encoded.hits.keys()
    for goal in plan.manifest:
        solver.push()
        solver.add(encoded.hits[goal])
        possible = any(goal in hits for hits in oracle)
        assert solver.check() == (z3.sat if possible else z3.unsat), goal
        if possible:
            witness = solver.model()
            inputs = {name: witness.eval(encoded.pre[name], model_completion=True).as_long()
                      for name in ("a", "b")}
            native.reset()
            native_hits.clear()
            native.process(**inputs)
            assert goal in native_hits
        solver.pop()
