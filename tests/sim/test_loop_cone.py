"""Cyclic command cones preserve hits, not just overall SAT verdicts."""

import itertools
import random

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import residual
from behave.sim.symbolic import encoder, interp, ir, ranges, slicing, typecheck as types
from behave.sim.symbolic.plan import prepare_positions


def _eq(name, value):
    return ir.Op("eq", ir.Var(name), ir.Lit(value))


def _hit(test, goal="hit"):
    return ir.If(test, ir.Mark(goal), ir.Skip())


def _procedure(case):
    dead = ir.Assign("junk", ir.Op("mul", ir.Var("a"), ir.Var("a")))
    if case == "suffix-chain":
        body = ir._sequence((
            _hit(_eq("x", 1)), ir.Assign("x", ir.Var("z")),
            ir.Assign("z", ir.Var("a")), dead))
    elif case == "conditional":
        body = ir._sequence((
            _hit(_eq("x", 1)),
            ir.If(_eq("a", 1), ir.Assign("x", ir.Lit(1)), ir.Skip()), dead))
    elif case == "array":
        body = ir._sequence((
            _hit(ir.Op("eq", ir.Sel(ir.Var("buf"), ir.Lit(0)), ir.Lit(1))),
            ir.Store("buf", ir.Lit(0), ir.Var("a")), dead))
    elif case == "nested":
        body = ir._sequence((
            ir.For("j", ir.Var("a"), ir._sequence((
                _hit(_eq("x", 1)), ir.Assign("x", ir.Var("z")), dead))),
            ir.Assign("z", ir.Lit(1)), dead))
    else:
        assert case == "failure"
        body = ir._sequence((
            _hit(_eq("i", 1)),
            ir.For("j", ir.Lit(1), ir.If(
                _eq("a", 1), ir.Fail("stopped", "safety"), ir.Skip())), dead))
    prefix = (ir.Assign("i", ir.Lit(-1)), ir.Assign("j", ir.Lit(-1)),
              ir.Assign("x", ir.Var("s")), ir.Assign("z", ir.Lit(0)),
              ir.Assign("buf", ir.Op("arraylit", ir.Lit(0), ir.Lit(0))))
    command = ir._sequence((*prefix, ir.For("i", ir.Var("n"), body)))
    scalars = ("s", "a", "n", "i", "j", "x", "z", "junk")
    proc = ir.Procedure(
        ("s",), ("a", "n"), ("i", "j", "x", "z", "junk", "buf"), (),
        command, initial={"s": 0}, types={
            **{name: types.INTEGER for name in scalars},
            "buf": types.Array(2, types.INTEGER)})
    return proc, dead


def _hits(command, initial):
    hits = set()
    try:
        interp._Interp(dict(initial)).run(command, hits)
    except interp.Halt:
        pass
    return hits


@pytest.mark.parametrize("case", [
    "suffix-chain", "conditional", "array", "nested", "failure",
])
def test_cyclic_closure_matches_full_predicates_occurrences_and_interpreter(case):
    proc, dead = _procedure(case)
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 2), "n": ranges.Range(0, 3)})
    pre = {name: z3.BitVec("loop_pre_" + name, ranges.encoding_width(env[name]))
           for name in (*proc.state, *proc.inputs)}
    events = slicing.linearize(proc.body)
    site = next(i for i, (c, _) in enumerate(events)
                if isinstance(c, ir.Mark) and c.goal == "hit")
    cut = slicing.cone(events, "hit", _site=site)
    assert id(dead) not in cut.kept
    assert slicing.audit_occurrence(proc, "hit", site, cut.kept, events) == ()
    for command, _ in events:
        if isinstance(command, ir.Fail):
            assert id(command) in cut.kept

    whole = encoder.encode_step(proc, pre=pre, env=env, tag="whole")
    query = encoder._encode_goal_occurrence_query(
        proc, "hit", site, pre=pre, env=env, tag="query")
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)
    solver.add(whole.constraint, query.constraint)
    differences = [whole.goal_condition("hit") != query.goal_condition("hit")]
    differences.extend(a != b for a, b in zip(
        whole.hit_occurrences["hit"], query.hit_occurrences["hit"], strict=True))
    solver.add(z3.Or(differences))
    assert solver.check() == z3.unsat

    pruned = slicing.prune(proc.body, cut.kept)
    for a, n, s in itertools.product(range(3), range(4), range(2)):
        initial = {"a": a, "n": n, "s": s}
        assert ("hit" in _hits(proc.body, initial)) == (
            "hit" in _hits(pruned, initial))


def test_live_loop_index_keeps_the_loop_and_its_failures():
    failure = ir.Fail("stopped", "safety")
    dead = ir.Assign("junk", ir.Lit(9))
    loop = ir.For("i", ir.Var("n"), ir._sequence((
        ir.If(_eq("a", 1), failure, ir.Skip()), dead)))
    proc = ir.Procedure((), ("n", "a"), ("i", "junk"), (), ir._sequence((
        ir.Assign("i", ir.Lit(-1)), loop, _hit(_eq("i", 1)))))
    events = slicing.linearize(proc.body)
    cut = slicing.cone(events, "hit")
    assert id(loop) in cut.kept and id(failure) in cut.kept
    assert id(dead) not in cut.kept
    reduced = slicing.prune(proc.body, cut.kept)
    for n, a in itertools.product(range(4), range(2)):
        values = {"n": n, "a": a}
        assert ("hit" in _hits(proc.body, values)) == ("hit" in _hits(reduced, values))


def test_worklist_reaches_long_reverse_carried_chains_without_iteration_limit():
    size = 80
    # Each dependency runs after its consumer; one backwards sweep is not a
    # fixed point. Names are initialized before the loop for the first visit.
    initialize = [ir.Assign(f"x{i}", ir.Lit(0)) for i in range(size)]
    chain = [ir.Assign(f"x{i}", ir.Var(f"x{i+1}")) for i in range(size - 1)]
    chain.append(ir.Assign(f"x{size-1}", ir.Var("a")))
    dead = [ir.Assign(f"dead{i}", ir.Var("a")) for i in range(size)]
    loop = ir.For("i", ir.Lit(2), ir._sequence((
        _hit(_eq("x0", 1)), *chain, *dead)))
    cut = slicing.cone(slicing.linearize(ir._sequence((*initialize, loop))), "hit")
    assert all(id(command) in cut.kept for command in chain)
    assert all(id(command) in cut.kept for command in initialize)
    assert all(id(command) not in cut.kept for command in dead)


def test_boundary_projection_still_omits_failures_but_closes_array_dependencies():
    proc, dead = _procedure("array")
    cut = slicing.boundary_cone(proc, ("buf",))
    assert cut is not None and id(dead) not in cut.kept
    stores = [c for c in ir.walk(proc.body) if isinstance(c, ir.Store)]
    assert stores and all(id(c) in cut.kept for c in stores)
    other, _ = _procedure("failure")
    boundary = slicing.boundary_cone(other, ("i",))
    assert boundary is not None
    assert all(id(c) not in boundary.kept for c in ir.walk(other.body)
               if isinstance(c, ir.Fail))


def test_cyclic_dependencies_cross_step_boundaries_and_absorb_failures():
    mark = ir.Mark("hit")
    loop = ir.For("i", ir.Lit(2), ir._sequence((
        ir.If(_eq("s", 1), mark, ir.Skip()),
        ir.Assign("s", ir.Op("fmod", ir.Op("add", ir.Var("s"), ir.Var("a")),
                             ir.Lit(2))),
        ir.If(_eq("a", 2), ir.Fail("stopped", "safety"), ir.Skip()),
        ir.Assign("junk", ir.Var("a")))))
    proc = ir.Procedure(("s",), ("a",), ("i", "junk"), (), loop, initial={"s": 0})
    env = {"s": ranges.Range(0, 1), "a": ranges.Range(0, 2)}
    events = slicing.linearize(loop)
    sites = tuple(i for i, (command, _) in enumerate(events) if command is mark)
    planned = prepare_positions(proc, env, 3)
    whole = residual.unroll(proc, env, 3, plan=planned)
    reduced = residual.unroll(proc, env, 3, plan=planned,
                              goals=("hit",), _occurrence=(1, sites))
    # The two builds have their own SSA namespace/lifetime. Check each against
    # the independent concrete execution, not a conjunction that accidentally
    # equates their internal names.
    for values in itertools.product(range(3), repeat=3):
        _, _, hits, _ = interp.run_trace_detailed(proc, [{"a": a} for a in values])
        expected = len(hits) > 1 and "hit" in hits[1]
        for run in (whole, reduced):
            solver = z3.SolverFor("QF_BV")
            solver.set(timeout=10_000)
            solver.add(*run.formula.full_constraints)
            solver.add(*(run.inputs_at(t, "a") == a for t, a in enumerate(values)))
            assert solver.check() == z3.sat
            predicate = (z3.And(z3.Not(run.halted[1]),
                                run.encodings[1].goal_condition("hit")) if run is whole
                         else run.goal_condition("hit"))
            solver.add(predicate != expected)
            assert solver.check() == z3.unsat


@pytest.mark.parametrize("seed", range(16))
def test_generated_nested_cones_match_exhaustive_concrete_execution(seed):
    rng = random.Random(seed)
    names = ("x", "y", "z")

    def value():
        return ir.Var(rng.choice((*names, "a")))

    def condition():
        return ir.Op("eq", value(), ir.Lit(rng.randrange(3)))

    commands = []
    for number in range(15):
        write = ir.Assign(rng.choice(names), value())
        if number % 5 == 0:
            command = _hit(condition(), f"g{number % 2}")
        elif number % 5 == 1:
            command = ir.If(condition(), ir.Fail("stopped", "safety"), ir.Skip())
        elif number % 5 == 2:
            command = ir.If(condition(), write, ir.Skip())
        elif number % 5 == 3:
            command = ir.For("j", ir.Var("a"), _hit(condition(), "nested"))
        else:
            command = write
        commands.append(command)
    body = ir._sequence((
        *(ir.Assign(name, ir.Lit(rng.randrange(3))) for name in names),
        ir.For("i", ir.Var("n"), ir._sequence(commands))))
    events = slicing.linearize(body)
    for goal in sorted(ir.goals_of(body)):
        cut = slicing.cone(events, goal)
        reduced = slicing.prune(body, cut.kept)
        for a, n in itertools.product(range(3), range(4)):
            initial = {"a": a, "n": n}
            assert (goal in _hits(body, initial)) == (goal in _hits(reduced, initial))
