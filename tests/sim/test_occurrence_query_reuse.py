"""Identical audited command selections are one exact physical question."""

import itertools

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import exact_solver, residual
from behave.sim.symbolic import interp, ir, ranges, slicing, typecheck as types
from behave.sim.core.query import SolverAnswer


def _sequence(*commands):
    result = ir.Skip()
    for command in reversed(commands):
        result = ir.Seq(command, result)
    return result


def _loop_proc(trips=2, stateful=False, failure_goals=False):
    def equals(left, right):
        return ir.Op("eq", left, right)

    def goal(name):
        return ir.Fail("Assertion", name) if failure_goals else ir.Mark(name)

    body = _sequence(
        ir.Assign("x", ir.Var("s") if stateful else ir.Lit(0)),
        ir.For("i", ir.Lit(trips), _sequence(
            ir.If(equals(ir.Var("a"), ir.Var("x")), goal("hit"), ir.Skip()),
            ir.If(equals(ir.Var("a"), ir.Lit(-1)),
                  ir.Fail("DivZero", "bad"), ir.Skip()),
            ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1))),
            ir.If(equals(ir.Var("a"), ir.Var("x")), goal("hit"), ir.Skip()),
            ir.If(equals(ir.Var("a"), ir.Lit(4)),
                  ir.Fail("NegativeShift", "bad"), ir.Skip()),
            ir.If(ir.Lit(False), goal("never"), ir.Skip()),
            ir.If(ir.Lit(False), goal("never"), ir.Skip()))),
        ir.Assign("s", ir.Var("x")) if stateful else ir.Skip(),
        ir.Assign("y", ir.Var("x")))
    state = ("s",) if stateful else ()
    return ir.Procedure(
        state, ("a",), ("x", "i"), ("y",), body,
        initial={"s": 0} if stateful else {},
        types={name: types.INTEGER for name in (*state, "a", "x", "i", "y")})


def _single_site_fragments(proc, goal, steps):
    index = slicing.index(proc)
    sites = residual._occurrence_schedule((goal,), index.events)[goal]
    return tuple(slicing.occurrence_run(
        proc, goal, (site,), step, steps, _index=index)
        for step in range(steps) for site in sites)


@pytest.mark.parametrize("goal", ["hit", "bad", "never"])
@pytest.mark.parametrize("stateful", [False, True])
def test_equal_selections_produce_identical_formulas_and_hit_conditions(goal, stateful,
                                                                      monkeypatch):
    proc = _loop_proc(stateful=stateful)
    env = {"a": ranges.Range(-1, 4), **({"s": ranges.Range(0, 4)} if stateful else {})}
    index = slicing.index(proc)
    sites = residual._occurrence_schedule((goal,), index.events)[goal]
    originals = _single_site_fragments(proc, goal, 2)
    monkeypatch.setattr(residual, "_occurrence_work_limit", lambda: 1)
    selected = residual._occurrence_fragments(proc, goal, sites, 2, index)
    # Cyclic closure retains all failures but not unrelated observations.
    # Only the two failure sites have identical cones at each target step.
    assert len(originals) == 4 and len(selected) == (2 if goal == "bad" else 4)
    assert {part.target_step for part in selected} == {0, 1}
    by_keep = {}
    for fragment in originals:
        run = residual.unroll(proc, env, 2, goals=(goal,), _occurrence=fragment)
        previous = by_keep.setdefault(fragment.keeps, run)
        assert z3.eq(previous.constraint, run.constraint)
        assert z3.eq(previous.goal_condition(goal), run.goal_condition(goal))
    assert tuple(by_keep) == tuple(fragment.keeps for fragment in selected)


@pytest.mark.parametrize("trips", [0, 1, 2])
@pytest.mark.parametrize("steps", [1, 2])
@pytest.mark.parametrize("stateful", [False, True])
def test_public_answers_match_full_formula_and_exhaustive_replay(trips, steps, stateful,
                                                               monkeypatch):
    proc = _loop_proc(trips, stateful)
    env = {"a": ranges.Range(-1, 4), **({"s": ranges.Range(0, 4)} if stateful else {})}
    goals = ("hit", "bad", "never")
    expected = set()
    for inputs in itertools.product(range(-1, 5), repeat=steps):
        _states, _outputs, hits, _error = interp.run_trace_detailed(
            proc, [{"a": value} for value in inputs])
        expected.update(set().union(*hits))
    full = residual.unroll(proc, env, steps)
    for goal in goals:
        solver = z3.Solver()
        solver.add(full.constraint, full.goal_condition(goal))
        assert (solver.check() == z3.sat) == (goal in expected)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    monkeypatch.setattr(residual, "_occurrence_work_limit", lambda: 1)
    result = residual.decide_goals(proc, env, goals, steps=steps, workers=1)
    assert not result.undecided
    assert set(result.reachable) == expected
    assert result.unreachable == set(goals) - expected
    for goal, trace in result.reachable.items():
        assert len(trace) == steps
        _states, _outputs, hits, _error = interp.run_trace_detailed(proc, trace)
        assert goal in set().union(*hits)


def test_equal_work_counts_do_not_merge_different_command_selections(monkeypatch):
    body = _sequence(*(
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(value)), ir.Mark("hit"), ir.Skip())
        for value in (0, 1, 2)))
    proc = ir.Procedure((), ("a",), (), (), body, types={"a": types.INTEGER})
    index = slicing.index(proc)
    sites = residual._occurrence_schedule(("hit",), index.events)["hit"]
    monkeypatch.setattr(residual, "_occurrence_work_limit", lambda: 1)
    result = residual._occurrence_fragments(proc, "hit", sites, 2, index)
    assert len({fragment.work for fragment in result}) == 1
    assert len(result) == len({fragment.keeps for fragment in result}) == 6


@pytest.mark.parametrize("goal", ["bad", "hit"])
def test_partition_collapses_only_when_a_child_is_the_complete_parent(goal, monkeypatch):
    base = _loop_proc()
    early = ir.Fail("DivZero", goal) if goal == "bad" else ir.Mark(goal)
    proc = base.derived(body=_sequence(
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(-1)), early, ir.Skip()), base.body))
    env = {"a": ranges.Range(-1, 4)}
    index = slicing.index(proc)
    sites = residual._occurrence_schedule((goal,), index.events)[goal]
    parent = slicing.occurrence_run(proc, goal, sites, 0, 1, _index=index)
    children = _single_site_fragments(proc, goal, 1)
    assert len({child.keeps for child in children}) == (2 if goal == "bad" else 3)
    monkeypatch.setattr(residual, "_occurrence_work_limit", lambda: 1)
    selected = residual._occurrence_fragments(proc, goal, sites, 1, index)
    # The previous failure must be in the loop's reaching cone. A previous
    # coverage mark is not a dependency, so that superficially similar case
    # has no child equal to the parent and must keep all three site questions.
    assert len(selected) == (1 if goal == "bad" else 3)
    assert max(part.work for part in selected) == max(part.work for part in children)
    if goal == "bad":
        assert selected[0].sites == sites
        assert selected[0].keeps == parent.keeps == children[-1].keeps
        parent_run = residual.unroll(proc, env, 1, goals=(goal,), _occurrence=parent)
        child_run = residual.unroll(proc, env, 1, goals=(goal,), _occurrence=children[-1])
        assert z3.eq(parent_run.constraint, child_run.constraint)
        assert z3.eq(parent_run.goal_condition(goal), child_run.goal_condition(goal))
    for value in range(-1, 5):
        one_input = {"a": ranges.Range(value, value)}
        result = residual.decide_goals(proc, one_input, (goal,), steps=1)
        _states, _outputs, hits, _error = interp.run_trace_detailed(proc, [{"a": value}])
        assert not result.undecided
        assert (goal in result.reachable) == (goal in set().union(*hits))


def test_an_unknown_identical_query_remains_unknown_without_repeating_it(monkeypatch):
    from behave.sim.symbolic.exact_solve import ExactCheckResult

    proc = _loop_proc()
    calls = []
    def unknown(*_args, **_kwargs):
        calls.append(1)
        return ExactCheckResult(SolverAnswer.UNKNOWN, reason="forced unknown")

    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    monkeypatch.setattr(residual, "_occurrence_work_limit", lambda: 1)
    monkeypatch.setattr(exact_solver, "_check", unknown)
    result = residual.decide_goals(proc, {"a": ranges.Range(-1, 4)}, ("bad",), steps=1)
    assert len(calls) == 1
    assert result.undecided == {"bad": "forced unknown"}
    assert not result.unreachable and not result.reachable
    assert result.queries[0].answer is SolverAnswer.UNKNOWN
    assert result.queries[0].solver_calls == 1
