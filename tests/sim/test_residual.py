"""Properties the per-goal queries have to have, whatever they answer.

A verdict is about a bound, and the bounds are ordered: a goal a run of one
step reaches is reached by a run of two, which does the same thing first. So
the set called unreachable can only shrink as the bound grows. It grew, once,
because two steps of one run were encoded with the same names for their
intermediate values, and conjoining them asserted that those values agree.
Nothing about that shows up as an error; it shows up as a goal that stops
being reachable when more of the run is considered.
"""
import weakref

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic.exact_formula import (
    ExactFormula,
    FormulaDefinition,
    SliceMode,
)
from behave.sim.behavior import budgets, residual
from behave.sim.symbolic import interp, ir, ranges, slicing, typecheck as types
from behave.sim.behavior import instance as behavior_instance
from behave.sim.behavior.exact_solver import solve_exact_components
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.core.query import QueryCall, SolverAnswer
from behave.sim.core.query_telemetry import StageTimings

# A step that can only reach its second branch after the first has run, so the
# bound is the difference between the two verdicts.
MODEL = ('class M:\n'
         '    def reset(self):\n'
         '        self.seen = 0\n'
         '    def process(self, a):\n'
         '        y = 0\n'
         '        if self.seen:\n'
         '            y = 1\n'
         '        if a & 1:\n'
         '            self.seen = 1\n'
         '        return {"y": y}\n')


def build():
    proc = lower_with_state_ranges(MODEL)
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
    return proc, env


def test_a_goal_needing_a_previous_step_is_unreachable_at_one():
    proc, env = build()
    goals = sorted(behavior_instance.BehaviorInstance(proc, {}).goals())
    at_one = residual.decide_goals(proc, env, goals, steps=1, timeout_ms=30_000)
    needs_history = [g for g in goals if ":6:" in g and g.endswith(":T")]
    assert len(needs_history) == 1
    assert needs_history[0] in at_one.unreachable


def test_what_is_unreachable_at_a_bound_stays_so_at_a_smaller_one():
    """The bound is the whole content of the verdict, so the sets are nested.
    Growing them is how a shared name between two steps announces itself."""
    proc, env = build()
    goals = sorted(behavior_instance.BehaviorInstance(proc, {}).goals())
    at_one = residual.decide_goals(proc, env, goals, steps=1, timeout_ms=30_000)
    at_two = residual.decide_goals(proc, env, goals, steps=2, timeout_ms=30_000)
    assert not at_one.undecided and not at_two.undecided
    assert at_two.unreachable <= at_one.unreachable
    assert at_one.unreachable - at_two.unreachable, \
        "the second step reaches something the first cannot"


FAILS = ('class M:\n'
         '    def reset(self):\n'
         '        self.n = 0\n'
         '    def process(self, a):\n'
         '        y = 1 // (a & 1)\n'
         '        self.n = (self.n + 1) & 7\n'
         '        return {"y": y}\n')


def test_a_failed_step_ends_the_run():
    """A step that fails is the last one. The formula used to chain the state
    past it, so it could satisfy "the first step failed and the second
    completed", which no run does."""
    proc = lower_with_state_ranges(FAILS)
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
    run = residual.unroll(proc, env, 2)

    s = z3.Solver()
    s.add(run.constraint)
    s.add(z3.Not(run.encodings[0].ok))       # the first step fails
    s.add(run.encodings[1].ok)               # and the second completes anyway
    assert s.check() == z3.sat, "the steps themselves are still encoded"
    s.add(z3.Not(run.halted[2]))             # but the run did not end
    assert s.check() == z3.unsat


def test_a_halted_run_reaches_nothing_after_it_halts():
    proc = lower_with_state_ranges(FAILS)
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
    run = residual.unroll(proc, env, 2)
    goals = sorted(behavior_instance.BehaviorInstance(proc, {}).goals())
    safety = next(g for g in goals if g.startswith("safety:"))

    # The failure is reached, since the goal that reports it is marked before
    # the step fails.
    s = z3.Solver()
    s.add(run.constraint, run.goal_condition(safety))
    assert s.check() == z3.sat

    # And a run that halts on its first step reaches nothing on its second.
    s = z3.Solver()
    s.add(run.constraint, run.halted[1])
    s.add(run.encodings[1].goal_condition(safety))
    s.add(run.goal_condition(safety) == z3.BoolVal(False))
    assert s.check() == z3.unsat


def test_the_step_that_fails_does_not_commit_its_partial_state():
    source = ('class M:\n'
              '    def reset(self):\n'
              '        self.n = 0\n'
              '    def process(self, a):\n'
              '        self.n = self.n + 1\n'
              '        y = 1 // (a & 1)\n'
              '        return {"y": y}\n')
    proc = lower_with_state_ranges(
        source, input_bounds={"a": (0, 255)}, bound=2)
    env = dict(proc.state_ranges)
    run = residual.unroll(proc, env, 2)
    solver = z3.Solver()
    solver.add(run.constraint,
               run.encodings[0].pre["a"] == 0,
               z3.Not(run.encodings[0].ok))
    solver.add(run.encodings[1].pre["n"] != 0)
    assert solver.check() == z3.unsat


def test_the_run_and_the_representation_agree_on_halting():
    proc = lower_with_state_ranges(FAILS)
    for a, halts in ((0, True), (1, False)):
        _, outputs, _, error = interp.run_trace(proc, [{"a": a}, {"a": 1}])
        assert bool(error) is halts, a
        assert len(outputs) == 2, "the trace has the length it was asked for"
        if halts:
            # Nothing produced is a count of zero, which the record says now
            # rather than leaving an empty mapping that reads the same as a
            # step whose outputs were not looked at.
            assert outputs[1].count == 0, "nothing is produced after it halts"
            assert outputs[1].transactions == []


@pytest.mark.parametrize("steps", [1, 8, 64])
def test_nonfailing_unroll_keeps_literal_control_constant(steps):
    proc = ir.Procedure(
        ("x",), ("a",), (), ("y",),
        ir.Seq(ir.Assign("x", ir.Var("a")),
               ir.Seq(ir.Assign("y", ir.Var("x")), ir.Mark("always"))),
        initial={"x": 0},
        types={name: types.INTEGER for name in ("x", "a", "y")})
    run = residual.unroll(proc, {"a": ranges.Range(0, 7), "x": ranges.Range(0, 7)}, steps)
    assert all(z3.is_false(term) for term in run.halted)
    assert all(z3.is_true(state.normal) for state in run.state_boundaries)
    assert all(z3.is_true(output.present) for output in run.output_boundaries)
    assert all(z3.is_true(hit) for hit in run.goal_components("always"))


def test_folded_absorption_obeys_the_complete_symbolic_recurrence():
    proc = lower_with_state_ranges(FAILS, input_bounds={"a": (0, 3)}, bound=3)
    run = residual.unroll(proc, proc.state_ranges, 3)
    halted, error = z3.BoolVal(False), run.errors[0]
    differences = []
    for step, encoding in enumerate(run.encodings):
        stopped = z3.Or(halted, z3.Not(encoding.ok))
        error = z3.If(halted, error, encoding.error)
        differences.extend((
            run.halted[step + 1] != stopped,
            run.errors[step + 1] != error,
            run.state_boundaries[step + 1].normal != z3.Not(stopped),
            run.output_boundaries[step].present != z3.And(z3.Not(halted), encoding.ok),
        ))
        for name in proc.state:
            old, new = residual.widen(run.state_boundaries[step].value[name],
                                      encoding.state_post[name])
            differences.append(run.state_boundaries[step + 1].value[name]
                               != z3.If(stopped, old, new))
        halted = stopped
    solver = z3.Solver()
    solver.add(run.constraint, z3.Or(differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("stopped", [False, True])
def test_literal_hold_keeps_recursive_shape_and_the_common_width(stopped):
    old = [[z3.BitVecVal(-1, 3), z3.BitVecVal(1, 3)]]
    new = [[z3.BitVecVal(9, 6), z3.BitVecVal(-2, 6)]]
    held = residual._held(z3.BoolVal(stopped), old, new)
    assert len(held) == 1 and len(held[0]) == 2
    solver = z3.Solver()
    for index, term in enumerate(held[0]):
        assert term.size() == 6
        expected = z3.If(z3.BoolVal(stopped), *residual.widen(old[0][index], new[0][index]))
        solver.push()
        solver.add(term != expected)
        assert solver.check() == z3.unsat
        solver.pop()
    with pytest.raises(residual.EncodingError, match="cells"):
        residual._held(z3.BoolVal(stopped), old, [[]])


@pytest.mark.parametrize("stopped", [False, True, None])
@pytest.mark.parametrize("compact", ["old", "new", "both"])
@pytest.mark.parametrize("nested", [False, True])
def test_history_hold_can_join_compact_and_component_arrays(stopped, compact, nested):
    from behave.sim.symbolic.functional_array import FunctionalArray

    ctx = z3.Context()
    old_cells = [z3.BitVecVal(-1, 3, ctx), z3.BitVecVal(1, 3, ctx)]
    new_cells = [z3.BitVecVal(9, 6, ctx), z3.BitVecVal(-2, 6, ctx)]
    old = FunctionalArray.from_cells(old_cells) if compact != "new" else old_cells
    new = FunctionalArray.from_cells(new_cells) if compact != "old" else new_cells
    before = z3.Bool("stopped", ctx) if stopped is None else z3.BoolVal(stopped, ctx)
    held = residual._held(before, [old] if nested else old, [new] if nested else new)
    held = held[0] if nested else held
    assert isinstance(held, FunctionalArray)
    assert held.element_width == 6
    solver = z3.Solver(ctx=ctx)
    for i, (old_cell, new_cell) in enumerate(zip(old_cells, new_cells)):
        expected = z3.If(before, *residual.widen(old_cell, new_cell))
        solver.push()
        solver.add(held.select(z3.BitVecVal(i, 3, ctx)) != expected)
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize("wrong", [[], [[1], [2]], [1]])
def test_history_hold_still_rejects_different_array_shapes(wrong):
    from behave.sim.symbolic.functional_array import FunctionalArray

    def encode(value):
        return [encode(v) for v in value] if isinstance(value, list) else z3.BitVecVal(value, 4)

    old = FunctionalArray.uniform(2, z3.BitVecVal(0, 4))
    with pytest.raises(residual.EncodingError, match="shape"):
        residual._held(z3.BoolVal(False), old, encode(wrong))


def test_a_fixed_sequence_length_makes_only_that_index_occurrence_false():
    source = ('class M:\n'
              '    def reset(self):\n'
              '        pass\n'
              '    def process(self, a):\n'
              '        q = []\n'
              '        for i in range(3):\n'
              '            q.append(a + i)\n'
              '        return {"y": q[2]}\n')
    proc = lower_with_state_ranges(
        source, input_bounds={"a": (0, 3)}, bound=1)
    goal = next(goal for goal in ir.goals_of(proc.body)
                if goal.endswith(":indexerror"))

    assert goal not in proc.discharged
    run = residual.unroll(proc, proc.state_ranges, 1)
    assert run.supports(goal)
    assert z3.is_false(z3.simplify(run.goal_condition(goal)))


def test_a_safe_inline_discharge_cannot_hide_a_live_sibling_context():
    source = ('class M:\n'
              '    def reset(self):\n'
              '        pass\n'
              '    def first(self, xs):\n'
              '        return xs[0]\n'
              '    def process(self, a, b):\n'
              '        safe = []\n'
              '        safe.append(a)\n'
              '        maybe = []\n'
              '        if a:\n'
              '            maybe.append(a)\n'
              '        if b:\n'
              '            y = self.first(safe)\n'
              '        else:\n'
              '            y = self.first(maybe)\n'
              '        return {"y": y}\n')
    proc = lower_with_state_ranges(
        source, input_bounds={"a": (0, 3), "b": (0, 3)}, bound=1)
    goal = next(goal for goal in ir.goals_of(proc.body)
                if goal.endswith(":indexerror"))

    # The source-site identifier is shared by both inline contexts.  One is
    # statically safe, but the other fails when ``a == b == 0``.
    assert goal not in proc.discharged
    run = residual.unroll(proc, proc.state_ranges, 1)
    solver = z3.Solver()
    solver.add(run.constraint, run.goal_condition(goal))
    assert solver.check() == z3.sat


@pytest.mark.parametrize(
    ("operator", "safe_right", "suffix"),
    (("//", "(b & 1) + 1", "divzero"),
     (">>", "b & 1", "negativeshift")),
)
def test_a_proved_partial_operation_has_a_false_occurrence(
        monkeypatch, operator, safe_right, suffix):
    source = ('class M:\n'
              '    def reset(self):\n'
              '        pass\n'
              '    def apply(self, x, right):\n'
              f'        return x {operator} right\n'
              '    def process(self, a, b):\n'
              f'        return {{"y": self.apply(a, {safe_right})}}\n')
    proc = lower_with_state_ranges(
        source, input_bounds={"a": (0, 3), "b": (0, 3)}, bound=1)
    goal = next(goal for goal in ir.goals_of(proc.body)
                if goal.endswith(suffix))

    assert goal not in proc.discharged
    run = residual.unroll(proc, proc.state_ranges, 1)
    assert run.supports(goal)
    assert all(z3.is_false(z3.simplify(component))
               for component in run.goal_components(goal))

    # Later range/term proofs are independent of the frontend proof. Disabling
    # any one optimization must preserve the declared goal and its semantics;
    # it need not force the backend to retain a syntactically nonconstant term.
    monkeypatch.setattr(
        _Lowerer, "_partial_operand_is_safe", lambda *args: False)
    unproved = lower_with_state_ranges(
        source, input_bounds={"a": (0, 3), "b": (0, 3)}, bound=1)
    unproved_run = residual.unroll(unproved, unproved.state_ranges, 1)
    assert all(z3.is_false(z3.simplify(component))
               for component in unproved_run.goal_components(goal))
    from behave.sim.symbolic.term_rewrite import _ComparisonRewriter

    monkeypatch.setattr(_ComparisonRewriter, "rewrite", lambda self, value: value)
    unproved_run = residual.unroll(unproved, unproved.state_ranges, 1)
    assert unproved_run.supports(goal)
    solver = z3.Solver()
    solver.add(unproved_run.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(unproved_run.goal_condition(goal),
                     z3.Not(unproved_run.encodings[0].ok)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize(
    ("operator", "safe_right", "unsafe_right", "suffix"),
    (("//", "(b & 1) + 1", "b", "divzero"),
     (">>", "b & 1", "b - 1", "negativeshift")),
)
def test_a_safe_partial_operation_cannot_hide_an_unsafe_sibling_context(
        operator, safe_right, unsafe_right, suffix):
    source = ('class M:\n'
              '    def reset(self):\n'
              '        pass\n'
              '    def apply(self, x, right):\n'
              f'        return x {operator} right\n'
              '    def process(self, a, b):\n'
              f'        safe = self.apply(a, {safe_right})\n'
              f'        return {{"y": safe + self.apply(a, {unsafe_right})}}\n')
    proc = lower_with_state_ranges(
        source, input_bounds={"a": (0, 3), "b": (0, 3)}, bound=1)
    goal = next(goal for goal in ir.goals_of(proc.body)
                if goal.endswith(suffix))

    # Both inline contexts share the source-site goal.  The first occurrence
    # is statically safe; the second still fails at b == 0.
    assert goal not in proc.discharged
    run = residual.unroll(proc, proc.state_ranges, 1)
    solver = z3.Solver()
    solver.add(run.constraint, run.goal_condition(goal))
    assert solver.check() == z3.sat


def test_two_steps_do_not_share_the_names_they_bind():
    proc, env = build()
    run = residual.unroll(proc, env, 3)
    names = [{d.arg(0) for d in enc.constraint.children()
              if d.decl().kind() == z3.Z3_OP_EQ}
             for enc in run.encodings]
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            assert not (left & right)

# ── P3: the budget bounds the artifact, not the question ──

def test_a_spent_artifact_leaves_its_goals_undecided_with_the_budget_named():
    """P3. `QUERY_MS` bounds one query at fifteen minutes and nothing bounded the
    total, so nineteen goals is four and three quarter hours in which every part is
    behaving as designed.

    What matters is not that it stops but **what it leaves**: a goal undecided with
    the budget named is a report, and a worker the harness killed from outside is a
    model missing from the results.
    """
    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goals = sorted(g for g in ir.goals_of(proc.body))
    assert goals

    out_of_queries = budgets.Spent(ms=None, max_queries=0)
    found, ruled, open_, calls = residual.decide_open(
        run, proc, goals, 10_000, out_of_queries)
    assert not found and not ruled, "nothing may be decided on no queries"
    assert set(open_) == set(goals)
    assert all("query budget" in why for why in open_.values())
    assert [call.goals for call in calls] == [(goal,) for goal in goals]
    assert all(call.answer is SolverAnswer.UNKNOWN
               and call.solver_calls == 0 for call in calls)


def test_a_spent_budget_never_makes_a_goal_unreachable():
    """The direction that would be a soundness fault. Running out of time is a third
    answer, and `unreachable` is a claim over every admissible trace -- a budget can
    never establish one."""
    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goals = sorted(ir.goals_of(proc.body))
    for spent in (budgets.Spent(ms=0, max_queries=None),
                  budgets.Spent(ms=None, max_queries=0)):
        _found, ruled, _open, calls = residual.decide_open(
            run, proc, goals, 10_000, spent)
        assert not ruled
        assert [call.goals for call in calls] == [(goal,) for goal in goals]
        assert all(call.answer is SolverAnswer.UNKNOWN
                   and call.solver_calls == 0 for call in calls)


def test_decide_goals_does_not_build_after_its_artifact_budget_is_spent(
        monkeypatch):
    """A later caller-level batch cannot repay formula setup on no budget."""

    proc, env = build()
    goals = sorted(ir.goals_of(proc.body))

    def no_unrolling_without_a_possible_query(*_args, **_kwargs):
        raise AssertionError("the spent artifact rebuilt its exact unrolling")

    monkeypatch.setattr(residual, "unroll", no_unrolling_without_a_possible_query)
    got = residual.decide_goals(
        proc, env, goals, workers=1, artifact_ms=None, max_queries=0)

    assert not got.reachable and not got.unreachable
    assert set(got.undecided) == set(goals)
    assert [call.goals for call in got.queries] == [
        (goal,) for goal in goals]
    assert all(call.answer is SolverAnswer.UNKNOWN
               and call.solver_calls == 0 for call in got.queries)


def test_unknown_set_query_defers_only_missing_singleton_evidence(
        monkeypatch):
    """Parallel execution may move refinement, never repeat a singleton call."""

    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goals = sorted(ir.goals_of(proc.body))

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        reason = "forced set timeout"

    monkeypatch.setattr(residual, "solve_exact", lambda *_args, **_kw: Unknown())
    monkeypatch.setattr(
        residual, "solve_exact_components",
        lambda *_args, **_kw: Unknown())
    deferred = []
    found, ruled, open_, calls = residual.decide_open(
        run, proc, goals, 10_000,
        budgets.Spent(ms=None, max_queries=None),
        _defer_singletons=deferred.extend)

    assert not found and not ruled
    assert deferred == goals
    assert not open_

    def internal_batches(batch):
        if len(batch) == 1:
            return []
        refinements = residual._dependency_refinements(run, batch)
        return [tuple(batch), *(item
                for group in refinements
                for item in internal_batches(group))]

    initial = residual._dependency_partitions(run, tuple(goals)) \
        or (tuple(goals),)
    expected = [item for group in initial
                for item in internal_batches(group)]
    assert [call.goals for call in calls] == expected
    assert all(call.answer is SolverAnswer.UNKNOWN for call in calls)


def test_unknown_open_set_refines_balanced_children_before_singletons(
        monkeypatch):
    """A child disjunction is a schedule, never an answer for its members."""

    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goals = tuple(sorted(ir.goals_of(proc.body))[:3])
    assert len(goals) == 3

    class Outcome:
        model = None
        timings = StageTimings({"solve_ns": 0})
        shape = None

        def __init__(self, answer, reason=""):
            self.answer = answer
            self.reason = reason

    answers = iter((
        Outcome(SolverAnswer.UNKNOWN, "the open set timed out"),
        Outcome(SolverAnswer.UNKNOWN, "the balanced child timed out"),
        Outcome(SolverAnswer.UNSAT),
        Outcome(SolverAnswer.UNSAT),
        Outcome(SolverAnswer.UNKNOWN, "one goal timed out"),
    ))
    monkeypatch.setattr(
        residual, "_dependency_refinements",
        lambda _run, batch: (
            (batch[:2], batch[2:]) if len(batch) == 3 else
            (batch[:1], batch[1:])))
    monkeypatch.setattr(
        residual, "_dependency_partitions", lambda _run, _batch: None)
    monkeypatch.setattr(
        residual, "solve_exact_components",
        lambda *_args, **_kwargs: next(answers))

    found, ruled, open_, calls = residual.decide_open(
        run, proc, goals, 10_000,
        budgets.Spent(ms=None, max_queries=None))

    assert not found
    assert ruled == {goals[0], goals[2]}
    assert open_ == {goals[1]: "one goal timed out"}
    assert [(call.goals, call.answer) for call in calls] == [
        (goals, SolverAnswer.UNKNOWN),
        (goals[:2], SolverAnswer.UNKNOWN),
        ((goals[0],), SolverAnswer.UNSAT),
        ((goals[1],), SolverAnswer.UNKNOWN),
        ((goals[2],), SolverAnswer.UNSAT),
    ]


def test_balanced_unsat_children_settle_without_singleton_reloads(monkeypatch):
    proc, env = build()
    run = residual.unroll(proc, env, 1)
    # Exercise four independent branch obligations.  Source-projected location
    # goals are also legitimate members of the complete catalogue, but are not
    # part of this set-refinement fixture.
    goals = tuple(sorted(
        goal for goal in ir.goals_of(proc.body) if goal.startswith("branch:")))
    assert len(goals) == 4

    class Outcome:
        model = None
        reason = ""
        timings = StageTimings({"solve_ns": 0})
        shape = None

        def __init__(self, answer, reason=""):
            self.answer = answer
            self.reason = reason

    answers = iter((
        Outcome(SolverAnswer.UNKNOWN, "the complete set timed out"),
        Outcome(SolverAnswer.UNSAT),
        Outcome(SolverAnswer.UNSAT),
    ))
    monkeypatch.setattr(
        residual, "_dependency_refinements",
        lambda _run, batch: residual._balanced_refinements(batch))
    monkeypatch.setattr(
        residual, "_dependency_partitions", lambda _run, _batch: None)
    monkeypatch.setattr(
        residual, "solve_exact_components",
        lambda *_args, **_kwargs: next(answers))

    found, ruled, open_, calls = residual.decide_open(
        run, proc, goals, 10_000,
        budgets.Spent(ms=None, max_queries=None))

    left, right = residual._balanced_refinements(goals)
    assert not found and not open_ and ruled == set(goals)
    assert [(call.goals, call.answer) for call in calls] == [
        (goals, SolverAnswer.UNKNOWN),
        (left, SolverAnswer.UNSAT),
        (right, SolverAnswer.UNSAT),
    ]


@pytest.mark.parametrize("shared", [False, True])
def test_initial_queries_combine_only_goals_with_one_dependency_cone(
        monkeypatch, shared):
    """Independent cones are not rescanned after another cone finds a witness."""

    goals = ("g0", "g1", "g2")
    terms = {goal: z3.Bool(f"initial_partition_{goal}") for goal in goals}

    class Formula:
        @staticmethod
        def dependency_roots(term):
            if shared:
                return frozenset(("shared",))
            return frozenset((term.get_id(),))

    class Run:
        formula = Formula()

        @staticmethod
        def supports(goal):
            return goal in terms

        @staticmethod
        def goal_components(goal):
            return (terms[goal],)

        dependency_roots = staticmethod(Formula.dependency_roots)
        dependency_cone = staticmethod(Formula.dependency_roots)

        @staticmethod
        def dependency_cones(targets):
            return tuple(Formula.dependency_roots(term) for term in targets)

        @staticmethod
        def dependency_masks(targets):
            indices = {term.get_id(): i for i, term in enumerate(terms.values())}
            return tuple(1 if shared else 1 << indices[term.get_id()] for term in targets)

    class Unsat:
        answer = SolverAnswer.UNSAT
        model = None
        reason = ""
        timings = StageTimings({"solve_ns": 0})
        shape = None

    physical = []

    def solve(_formula, components, _timeout_ms, **_options):
        physical.append(tuple(components))
        return Unsat()

    monkeypatch.setattr(residual, "solve_exact_components", solve)
    found, ruled, open_, transcript = residual.decide_open(
        Run(), object(), goals, 10_000,
        budgets.Spent(ms=None, max_queries=None))

    assert not found and not open_ and ruled == set(goals)
    expected = [goals] if shared else [(goal,) for goal in goals]
    assert [call.goals for call in transcript] == expected
    assert len(physical) == len(expected)


def test_exact_component_scheduling_and_evidence_have_a_protocol_version():
    from behave.sim.behavior.analysis_job import VERSION

    assert VERSION == "behavior-exact-analysis/109"


@pytest.mark.parametrize('argument', ['a + 1', '(a + 1) & 7'])
def test_one_public_goal_retains_its_inlined_occurrence_conditions(argument):
    source = ('class M:\n'
              '    def reset(self):\n'
              '        pass\n'
              '    def branch(self, x):\n'
              '        if x:\n'
              '            return 1\n'
              '        return 0\n'
              '    def process(self, a):\n'
              '        y = self.branch(a)\n'
              f'        z = self.branch({argument})\n'
              '        return {"y": y + z}\n')
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"])
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 7)})
    run = residual.unroll(proc, env, 1)
    goals = sorted(behavior_instance.BehaviorInstance(proc, {}).goals())
    branches = [goal for goal in goals if goal.startswith('branch:')]
    assert len(branches) == 2  # Both source outcomes remain in the catalogue.
    for goal in branches:
        # a+1 is never zero in this input domain. Its false inline occurrence
        # may fold away, but the false source goal still has its first call.
        expected = 1 if argument == 'a + 1' and goal.endswith(':F') else 2
        assert len(run.goal_components(goal)) == expected
        solver = z3.Solver()
        solver.add(run.constraint)
        solver.add(run.goal_condition(goal) != z3.Or(run.goal_components(goal)))
        assert solver.check() == z3.unsat
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_plan import plan_source

    native_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=native_plan)
    native = namespace['M']()
    solver = z3.Solver()
    solver.add(run.constraint)
    for a in range(8):
        native.reset()
        native_hits.clear()
        output = native.process(a)
        _, result, hits, failure = interp.run_step(proc, {'a': a})
        assert not failure and result.transactions == [output]
        assert hits == set(native_hits)
        solver.push()
        solver.add(run.encodings[0].pre['a'] == a)
        assert solver.check() == z3.sat
        solver.add(z3.Or(*(run.goal_condition(goal) != (goal in hits)
                           for goal in goals)))
        assert solver.check() == z3.unsat
        solver.pop()


def test_an_unrolling_derives_each_query_planning_view_once():
    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goal = min(ir.goals_of(proc.body))

    components = run.goal_components(goal)
    condition = run.goal_condition(goal)
    roots = run.dependency_roots(components[0])
    cone = run.dependency_cone(components[0])
    mask = run.dependency_masks(components[:1])[0]

    assert run.goal_components(goal) is components
    assert run.goal_condition(goal) is condition
    assert run.dependency_roots(components[0]) is roots
    assert run.dependency_cone(components[0]) is cone
    assert run.dependency_masks(components[:1])[0] is mask
    assert mask.bit_count() == len(cone)


def test_public_disjunction_is_unsat_only_when_every_distinct_cone_is_unsat():
    x, y = z3.BitVecs("component_x component_y", 8)
    left, right = z3.BitVecs("component_left component_right", 8)
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("left", left, x + 1),
            FormulaDefinition("right", right, y + 1),
        ),
        restrictions=(x != 0, y != 0),
        boundary_atoms=(x, y),
    )

    got = solve_exact_components(
        formula, (left == 1, right == 1), timeout_ms=30_000)

    assert got.answer is SolverAnswer.UNSAT
    assert got.subqueries == 1


def test_component_solver_bounds_each_initial_independent_cone_batch():
    inputs = z3.BitVecs("partition_input_0 partition_input_1 "
                        "partition_input_2 partition_input_3", 8)
    values = z3.BitVecs("partition_value_0 partition_value_1 "
                        "partition_value_2 partition_value_3", 8)
    formula = ExactFormula(
        definitions=tuple(
            FormulaDefinition(f"partition-{index}", value, source + 1)
            for index, (source, value) in enumerate(zip(inputs, values))),
        restrictions=tuple(source != 0 for source in inputs),
        boundary_atoms=inputs,
    )

    got = solve_exact_components(
        formula, tuple(value == 1 for value in values), timeout_ms=30_000)

    assert got.answer is SolverAnswer.UNSAT
    assert got.subqueries == 2


def test_component_solver_scales_physical_batches_sublinearly():
    count = 25
    inputs = tuple(
        z3.BitVec(f"wide_partition_input_{index}", 8)
        for index in range(count)
    )
    values = tuple(
        z3.BitVec(f"wide_partition_value_{index}", 8)
        for index in range(count)
    )
    formula = ExactFormula(
        definitions=tuple(
            FormulaDefinition(f"wide-partition-{index}", value, source + 1)
            for index, (source, value) in enumerate(zip(inputs, values))),
        boundary_atoms=inputs,
    )
    root_counts = []

    class Unsat:
        answer = SolverAnswer.UNSAT
        model = None
        reason = ""
        timings = StageTimings({"solve_ns": 1})

    def solve(registered, target, _timeout_ms):
        roots = registered.dependency_roots(target)
        assert roots is not None
        root_counts.append(len(roots))
        return Unsat()

    got = solve_exact_components(
        formula, tuple(value == 0 for value in values),
        timeout_ms=30_000, _solve=solve)

    assert got.answer is SolverAnswer.UNSAT
    assert got.subqueries == 5
    assert root_counts == [5] * 5

    root_counts.clear()
    capped = solve_exact_components(
        formula, tuple(value == 0 for value in values),
        timeout_ms=30_000, max_groups_per_query=3, _solve=solve)

    assert capped.answer is SolverAnswer.UNSAT
    assert capped.subqueries == 9
    assert root_counts == [3] * 8 + [1]
    with pytest.raises(ValueError, match="batch ceiling"):
        solve_exact_components(
            formula, (values[0] == 0,), timeout_ms=30_000,
            max_groups_per_query=True, _solve=solve)


def test_component_solver_combines_nested_cones_within_the_same_budget():
    count = 25
    source = z3.BitVec("nested_cone_source", 8)
    values = tuple(
        z3.BitVec(f"nested_cone_value_{index}", 8)
        for index in range(count)
    )
    definitions = []
    previous = source
    for index, value in enumerate(values):
        definitions.append(FormulaDefinition(
            f"nested-cone-{index}", value, previous + 1))
        previous = value
    formula = ExactFormula(
        definitions=tuple(definitions), boundary_atoms=(source,))
    cone_sizes = []

    class Unsat:
        answer = SolverAnswer.UNSAT
        model = None
        reason = ""
        timings = StageTimings({"solve_ns": 1})

    def solve(registered, target, _timeout_ms):
        cone = registered.dependency_cone(target)
        assert cone is not None
        cone_sizes.append(len(cone))
        return Unsat()

    got = solve_exact_components(
        formula, tuple(value == 0 for value in values),
        timeout_ms=30_000, _solve=solve)

    assert got.answer is SolverAnswer.UNSAT
    assert got.subqueries == 1
    assert cone_sizes == [count]

    cone_sizes.clear()
    capped = solve_exact_components(
        formula, tuple(value == 0 for value in values),
        timeout_ms=30_000, max_groups_per_query=1, _solve=solve)

    assert capped.answer is SolverAnswer.UNSAT
    assert capped.subqueries == count
    assert cone_sizes == list(range(1, count + 1))


def test_component_solver_obeys_its_physical_query_ceiling():
    inputs = z3.BitVecs(
        "physical_cap_input_0 physical_cap_input_1 "
        "physical_cap_input_2 physical_cap_input_3", 8)
    values = z3.BitVecs(
        "physical_cap_value_0 physical_cap_value_1 "
        "physical_cap_value_2 physical_cap_value_3", 8)
    formula = ExactFormula(
        definitions=tuple(
            FormulaDefinition(f"physical-cap-{index}", value, source + 1)
            for index, (source, value) in enumerate(zip(inputs, values))),
        boundary_atoms=inputs,
    )
    calls = []

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        model = None
        reason = "split this batch"
        timings = StageTimings({"solve_ns": 1})

    def solve(_formula, target, _timeout_ms):
        calls.append(target)
        return Unknown()

    got = solve_exact_components(
        formula, tuple(value == 0 for value in values),
        timeout_ms=30_000, max_groups_per_query=2,
        max_subqueries=2, _solve=solve)

    assert got.answer is SolverAnswer.UNKNOWN
    assert got.subqueries == len(calls) == 2
    assert "solver-invocation budget" in got.reason
    with pytest.raises(ValueError, match="physical-query ceiling"):
        solve_exact_components(
            formula, (values[0] == 0,), max_subqueries=0)


def test_component_solver_packs_overlap_without_growing_the_largest_cone():
    source = z3.BitVec("overlap_source", 8)
    base, left, right, independent = z3.BitVecs(
        "overlap_base overlap_left overlap_right overlap_independent", 8)
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("base", base, source + 1),
            FormulaDefinition("left", left, base + 1),
            FormulaDefinition("right", right, base + 2),
            FormulaDefinition("independent", independent, source + 3),
        ),
        boundary_atoms=(source,),
    )
    targets = (left == 0, base == 0, right == 0, independent == 0)
    largest = max(len(formula.dependency_cone(target)) for target in targets)
    cone_sizes = []

    class Unsat:
        answer = SolverAnswer.UNSAT
        model = None
        reason = ""
        timings = StageTimings({"solve_ns": 1})

    def solve(registered, target, _timeout_ms):
        cone = registered.dependency_cone(target)
        assert cone is not None
        cone_sizes.append(len(cone))
        return Unsat()

    got = solve_exact_components(
        formula, targets, timeout_ms=30_000,
        preserve_largest_cone=True, _solve=solve)

    assert got.answer is SolverAnswer.UNSAT
    assert cone_sizes
    assert max(cone_sizes) <= largest
    # ``base`` is contained in the first target's cone, so preserving the
    # largest cone is less conservative than forcing every root group alone.
    assert got.subqueries < len(targets)


def test_component_solver_keeps_independent_maximal_cones_separate():
    inputs = z3.BitVecs(
        "cone_ceiling_input_0 cone_ceiling_input_1 cone_ceiling_input_2", 8)
    values = z3.BitVecs(
        "cone_ceiling_value_0 cone_ceiling_value_1 cone_ceiling_value_2", 8)
    formula = ExactFormula(
        definitions=tuple(
            FormulaDefinition(f"cone-ceiling-{index}", value, source + 1)
            for index, (source, value) in enumerate(zip(inputs, values))),
        boundary_atoms=inputs,
    )
    targets = tuple(value == 0 for value in values)
    root_counts = []

    class Unsat:
        answer = SolverAnswer.UNSAT
        model = None
        reason = ""
        timings = StageTimings({"solve_ns": 1})

    def solve(registered, target, _timeout_ms):
        roots = registered.dependency_roots(target)
        assert roots is not None
        root_counts.append(len(roots))
        return Unsat()

    got = solve_exact_components(
        formula, targets, timeout_ms=30_000,
        preserve_largest_cone=True, _solve=solve)

    assert got.answer is SolverAnswer.UNSAT
    assert got.subqueries == len(targets)
    assert root_counts == [1, 1, 1]


def test_only_unknown_component_partitions_are_refined():
    inputs = z3.BitVecs("refine_input_0 refine_input_1 "
                        "refine_input_2 refine_input_3", 8)
    values = z3.BitVecs("refine_value_0 refine_value_1 "
                        "refine_value_2 refine_value_3", 8)
    formula = ExactFormula(
        definitions=tuple(
            FormulaDefinition(f"refine-{index}", value, source + 1)
            for index, (source, value) in enumerate(zip(inputs, values))),
        boundary_atoms=inputs,
    )
    targets = tuple(value == index for index, value in enumerate(values))
    calls = []

    class Outcome:
        model = None
        reason = ""
        timings = StageTimings({"solve_ns": 1})

        def __init__(self, answer, *, model=None, reason=""):
            self.answer = answer
            self.model = model
            self.reason = reason

    def solve(_formula, target, _timeout_ms):
        calls.append(target)
        if z3.is_or(target):
            return Outcome(SolverAnswer.UNKNOWN, reason="split this partition")
        if target.eq(targets[-1]):
            solver = z3.Solver()
            solver.add(target)
            assert solver.check() == z3.sat
            return Outcome(SolverAnswer.SAT, model=solver.model())
        return Outcome(SolverAnswer.UNSAT)

    got = solve_exact_components(
        formula, targets, timeout_ms=30_000, _solve=solve)

    assert got.answer is SolverAnswer.SAT
    assert got.subqueries == 6
    assert got.sat_components == (targets[-1],)
    expected = (z3.Or(targets[:2]), z3.Or(targets[2:]), *targets)
    assert len(calls) == len(expected)
    assert all(actual.eq(wanted)
               for actual, wanted in zip(calls, expected))


def test_component_refinement_gives_real_children_a_solver_opportunity(
        monkeypatch):
    """Once UNKNOWN creates children, later parents share time with them."""

    inputs = tuple(
        z3.BitVec(f"fair_component_input_{index}", 8)
        for index in range(4)
    )
    values = tuple(
        z3.BitVec(f"fair_component_value_{index}", 8)
        for index in range(4)
    )
    formula = ExactFormula(
        definitions=tuple(
            FormulaDefinition(f"fair-component-{index}", value, source + 1)
            for index, (source, value) in enumerate(zip(inputs, values))
        ),
        boundary_atoms=inputs,
    )
    targets = tuple(value == 0 for value in values)
    clock = [0.0]
    ceilings = []

    class Outcome:
        model = None
        reason = "split the physical batch"
        timings = StageTimings({"solve_ns": 1})

        def __init__(self, answer):
            self.answer = answer

    def solve(_formula, target, timeout_ms):
        ceilings.append(timeout_ms)
        clock[0] += timeout_ms / 1000.0
        return Outcome(
            SolverAnswer.UNKNOWN if z3.is_or(target)
            else SolverAnswer.UNSAT)

    from behave.sim.behavior import exact_solver

    monkeypatch.setattr(exact_solver.time, "monotonic", lambda: clock[0])
    got = solve_exact_components(
        formula, targets, timeout_ms=600, _solve=solve)

    assert got.answer is SolverAnswer.UNSAT
    assert got.subqueries == 6
    assert ceilings == [300, 100, 50, 50, 50, 50]
    assert sum(ceilings) == 600


def test_component_deadline_includes_dependency_planning(monkeypatch):
    inputs = z3.BitVecs("planning_deadline_input_0 planning_deadline_input_1", 8)
    values = z3.BitVecs("planning_deadline_value_0 planning_deadline_value_1", 8)
    formula = ExactFormula(
        definitions=tuple(
            FormulaDefinition(f"planning-deadline-{index}", value, source + 1)
            for index, (source, value) in enumerate(zip(inputs, values))),
        boundary_atoms=inputs,
    )
    targets = tuple(value == 0 for value in values)
    clock = [0.0]
    root_calls = []

    def roots(target):
        root_calls.append(target)
        clock[0] += 0.006
        return formula.dependency_roots(target)

    def should_not_solve(*_args, **_kwargs):
        raise AssertionError("an expired logical query must not launch a solver")

    from behave.sim.behavior import exact_solver

    monkeypatch.setattr(exact_solver.time, "monotonic", lambda: clock[0])
    got = solve_exact_components(
        formula, targets, timeout_ms=10,
        _dependency_roots=roots, _solve=should_not_solve)

    assert len(root_calls) == 2
    assert got.answer is SolverAnswer.UNKNOWN
    assert got.subqueries == 0
    assert "dependency roots" in got.reason


def test_a_later_satisfiable_occurrence_settles_the_public_disjunction():
    x, y = z3.BitVecs("component_sat_x component_sat_y", 8)
    left, right = z3.BitVecs("component_sat_left component_sat_right", 8)
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("left", left, x + 1),
            FormulaDefinition("right", right, y + 1),
        ),
        restrictions=(x != 0,),
        boundary_atoms=(x, y),
    )

    got = solve_exact_components(
        formula, (left == 1, right == 1), timeout_ms=30_000)

    assert got.answer is SolverAnswer.SAT
    assert got.subqueries == 1
    assert got.model.eval(right).as_long() == 1


def test_component_sat_readback_never_claims_an_unasked_public_goal():
    x, y = z3.BitVecs("component_scope_x component_scope_y", 8)
    left, right = z3.BitVecs("component_scope_left component_scope_right", 8)
    left_hit, right_hit = left == 1, right == 1
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("left", left, x + 1),
            FormulaDefinition("right", right, y + 1),
        ),
        restrictions=(x == 0, y == 0),
        boundary_atoms=(x, y),
    )
    outcome = solve_exact_components(
        formula, (left_hit,), timeout_ms=30_000)

    class Run:
        @staticmethod
        def goal_components(goal):
            return {"left": (left_hit,), "right": (right_hit,)}[goal]

        @classmethod
        def goal_condition(cls, goal):
            return z3.Or(cls.goal_components(goal))

    assert outcome.answer is SolverAnswer.SAT
    assert z3.is_true(outcome.model.eval(right_hit))
    assert residual._sat_goals(Run(), ("left", "right"), outcome) == ["left"]


def test_component_solver_releases_each_audited_slice_before_the_next_one():
    x, y = z3.BitVecs("component_release_x component_release_y", 8)
    left, right = z3.BitVecs("component_release_left component_release_right", 8)
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("left", left, x + 1),
            FormulaDefinition("right", right, y + 1),
        ),
        boundary_atoms=(x, y),
    )
    live = weakref.WeakSet()
    live_before_each_call = []

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        model = None
        reason = "forced timeout"
        timings = StageTimings({"slice_ns": 1})

    def solve(*_args, **_kwargs):
        live_before_each_call.append(len(live))
        outcome = Unknown()
        live.add(outcome)
        return outcome

    got = solve_exact_components(
        formula, (left == 1, right == 1), timeout_ms=30_000,
        _solve=solve)

    assert got.answer is SolverAnswer.UNKNOWN
    assert got.subqueries == 3
    assert live_before_each_call == [0, 0, 0]


def test_component_queries_share_one_declared_memory_ceiling():
    x, y = z3.BitVecs("component_memory_x component_memory_y", 8)
    left, right = z3.BitVecs("component_memory_left component_memory_right", 8)
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("left", left, x + 1),
            FormulaDefinition("right", right, y + 1),
        ),
        boundary_atoms=(x, y),
    )
    seen = []

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        model = None
        reason = "max. memory exceeded"
        timings = StageTimings({"solve_ns": 1})

    def solve(_formula, _target, _timeout_ms, *, memory_mb):
        seen.append(memory_mb)
        return Unknown()

    got = solve_exact_components(
        formula, (left == 1, right == 1), timeout_ms=30_000,
        memory_mb=23, _solve=solve)

    assert got.answer is SolverAnswer.UNKNOWN
    assert seen == [23, 23, 23]


def test_an_unauditable_set_target_falls_back_to_the_complete_formula():
    source = z3.BitVec("batch_fallback_source", 8)
    defined = z3.BitVec("batch_fallback_defined", 8)
    missing = z3.BitVec("batch_fallback_missing", 8)
    formula = ExactFormula(
        definitions=(FormulaDefinition("defined", defined, source + 1),),
        boundary_atoms=(source,),
    )
    conditions = {
        "first": defined == 0,
        "fallback": missing == 0,
        "last": defined == 1,
    }

    selected = formula.slice(z3.Or(tuple(conditions.values())))

    assert selected.mode is SliceMode.FULL
    assert selected.constraints == formula.full_constraints


def test_query_time_is_shared_by_every_call_that_still_needs_an_opportunity(
        monkeypatch):
    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goals = sorted(ir.goals_of(proc.body))
    assert len(goals) > 1
    clock = [0.0]
    ceilings = []

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        reason = "forced timeout"

    complete_calls = []

    def solve(_formula, _target, timeout_ms, *, complete=False):
        ceilings.append(timeout_ms)
        complete_calls.append(complete)
        clock[0] += timeout_ms / 1000.0
        return Unknown()

    monkeypatch.setattr(residual, "solve_exact", solve)
    monkeypatch.setattr(
        residual, "solve_exact_components",
        lambda formula, targets, timeout_ms, **_kwargs:
            solve(formula, z3.Or(targets), timeout_ms))
    spent = budgets.Spent(
        ms=1_000, max_queries=None, now=lambda: clock[0], started=0.0)
    found, ruled, open_, calls = residual.decide_open(
        run, proc, goals, 900_000, spent)

    assert not found and not ruled and set(open_) == set(goals)
    def all_batches(batch):
        if len(batch) == 1:
            return [tuple(batch)]
        refinements = residual._dependency_refinements(run, batch)
        return [tuple(batch), *(item
                for group in refinements
                for item in all_batches(group))]

    initial = residual._dependency_partitions(run, tuple(goals)) \
        or (tuple(goals),)
    expected_calls = [item for group in initial
                      for item in all_batches(group)]
    assert [call.goals for call in calls] == expected_calls
    # Only questions that exist share the clock.  UNKNOWN creates the next
    # level, which still receives an opportunity, but a hypothetical complete
    # refinement tree cannot shrink its parents below useful setup time.
    fair_ceiling = 1_000 // len(initial)
    assert ceilings[0] <= fair_ceiling + 1
    hypothetical = sum(2 * len(group) - 1 for group in initial)
    assert ceilings[0] > 1_000 // hypothetical
    assert all(value >= 1 for value in ceilings)
    assert sum(ceilings) <= 1_000
    assert complete_calls and not any(complete_calls)


def test_an_audited_command_cone_uses_adaptive_physical_queries(
        monkeypatch):
    proc, env = build()
    goals = tuple(sorted(ir.goals_of(proc.body)))
    run = residual.unroll(proc, env, 1, goals=goals)
    assert run.preselected(goals)
    complete_calls = []

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        reason = "forced timeout"
        timings = StageTimings({"slice_ns": 1})
        shape = None

    def solve(_formula, _target, _timeout_ms, *, complete=False):
        complete_calls.append(complete)
        return Unknown()

    monkeypatch.setattr(residual, "solve_exact", solve)
    component_calls = []

    def components(_formula, targets, _timeout_ms, **kwargs):
        component_calls.append((
            tuple(targets), kwargs["max_groups_per_query"]))
        return Unknown()

    monkeypatch.setattr(residual, "solve_exact_components", components)

    _found, _ruled, open_, transcript = residual.decide_open(
        run, proc, goals, 30_000,
        budgets.Spent(ms=None, max_queries=None))

    assert set(open_) == set(goals)
    assert len(goals) <= len(transcript) <= 2 * len(goals) - 1
    assert complete_calls == []
    assert len(component_calls) == len(transcript)
    assert all(limit is None for _targets, limit in component_calls)


def test_an_unknown_singleton_is_not_asked_twice(monkeypatch):
    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goal = min(ir.goals_of(proc.body))
    calls = []

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        reason = "forced timeout"
        timings = StageTimings({"solve_ns": 1})
        shape = None

    def solve(*_args, **_kwargs):
        calls.append(True)
        return Unknown()

    monkeypatch.setattr(residual, "solve_exact", solve)
    found, ruled, open_, transcript = residual.decide_open(
        run, proc, (goal,), 10_000,
        budgets.Spent(ms=None, max_queries=None))

    assert not found and not ruled and open_ == {goal: "forced timeout"}
    assert len(calls) == len(transcript) == 1
    assert transcript[0].goals == (goal,)


def test_query_memory_exhaustion_is_one_unresolved_goal_not_a_failed_artifact(
        monkeypatch):
    proc, env = build()
    goal = min(ir.goals_of(proc.body))
    seen = []

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        model = None
        reason = "max. memory exceeded"
        timings = StageTimings({"solve_ns": 1})
        shape = None

    def solve(_formula, _target, _timeout_ms, *, memory_mb, complete=False):
        seen.append((memory_mb, complete))
        return Unknown()

    monkeypatch.setattr(residual, "solve_exact", solve)
    verdict = residual.decide_goals(
        proc, env, (goal,), steps=1, timeout_ms=10_000,
        workers=1, memory_mb=400)

    assert verdict.reachable == {}
    assert verdict.unreachable == set()
    assert verdict.undecided == {goal: "max. memory exceeded"}
    assert seen == [(budgets.solver_memory_mb(400), False)]
    assert verdict.queries[0].answer is SolverAnswer.UNKNOWN


def test_a_one_worker_refinement_reuses_the_prefix_unrolling(monkeypatch):
    proc, env = build()
    goals = sorted(ir.goals_of(proc.body))
    built = []
    original = residual.unroll

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        reason = "forced timeout"
        timings = StageTimings({"slice_ns": 0, "solver_load_ns": 0})

    def counted(*args, **kwargs):
        built.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(residual, "unroll", counted)
    monkeypatch.setattr(residual, "solve_exact", lambda *_a, **_k: Unknown())
    monkeypatch.setattr(residual, "process_peak_rss_bytes", lambda: 1 << 20)
    monkeypatch.setattr(budgets, "workers_for", lambda *_a, **_k: 1)

    rows, queries, deferred, _peak, _unroll_ns, _query_setup_ns = \
        residual._decide_prefix_chunk((
            proc, env, 1, None, goals, 10_000, 60_000, None,
            None, None, residual.time.monotonic(),
        ))

    assert built == [True]
    assert deferred == ()
    assert len(goals) <= len(queries) <= 2 * len(goals) - 1
    assert {goal for goal, verdict, _detail in rows if verdict == "unknown"} \
        == set(goals)


def test_the_prefix_worker_uses_preunroll_occurrence_fragments(monkeypatch):
    proc, env = build()
    goals = tuple(sorted(ir.goals_of(proc.body)))
    assert len(goals) >= 3
    trace = [{name: 0 for name in proc.inputs}]
    calls = (
        QueryCall((goals[0],), SolverAnswer.SAT, witness=trace),
        QueryCall((goals[1],), SolverAnswer.UNSAT),
        QueryCall((goals[2],), SolverAnswer.UNKNOWN, reason="fragment timeout"),
    )
    seen = []

    def fragmented(
            _proc, _env, selected, _steps, _initial, _plan, _prepared,
            schedule, _events, _timeout_ms, _spent, **_kwargs):
        seen.append((tuple(selected), schedule))
        return (
            {goals[0]: trace}, {goals[1]}, {goals[2]: "fragment timeout"},
            calls,
        )

    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    monkeypatch.setattr(residual, "_decide_occurrence_goals", fragmented)
    monkeypatch.setattr(residual, "process_peak_rss_bytes", lambda: 1 << 20)

    rows, got_calls, deferred, peak, _unroll_ns, query_setup_ns = \
        residual._decide_prefix_chunk((
            proc, env, 1, None, goals[:3], 10_000, 60_000, None,
            None, None, residual.time.monotonic(),
        ))

    assert seen and seen[0][0] == goals[:3]
    assert {goal: (verdict, detail) for goal, verdict, detail in rows} == {
        goals[0]: ("sat", trace),
        goals[1]: ("unsat", None),
        goals[2]: ("unknown", "fragment timeout"),
    }
    assert got_calls == calls
    assert deferred == ()
    assert peak == 1 << 20
    assert query_setup_ns == 0


def test_set_refinement_does_not_retain_prior_solver_outcomes(monkeypatch):
    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goals = sorted(ir.goals_of(proc.body))
    live = weakref.WeakSet()
    live_before_each_call = []

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        reason = "forced timeout"
        timings = StageTimings({"slice_ns": 1, "solver_load_ns": 1})

    def solve(*_args, **_kwargs):
        live_before_each_call.append(len(live))
        outcome = Unknown()
        live.add(outcome)
        return outcome

    monkeypatch.setattr(residual, "solve_exact", solve)

    found, ruled, open_, queries = residual.decide_open(
        run, proc, goals, 10_000,
        budgets.Spent(ms=None, max_queries=None))

    assert not found and not ruled and set(open_) == set(goals)
    assert len(goals) <= len(queries) <= 2 * len(goals) - 1
    # The active set outcome and the immediately preceding singleton may exist
    # while the next call starts; outcomes from earlier calls do not accumulate.
    assert max(live_before_each_call) <= 2


def test_parallel_chunks_are_balanced_but_do_not_reorder_values():
    values = tuple(f"g{i}" for i in range(7))
    chunks = residual._chunks(values, 3)

    assert tuple(item for index in range(3)
                 for item in chunks[index]) != values
    assert sorted(item for chunk in chunks for item in chunk) == sorted(values)
    assert max(map(len, chunks)) - min(map(len, chunks)) <= 1


def test_the_clock_shortens_the_solvers_own_timeout():
    """The only mechanism that can bind a solve.

    A Python alarm cannot: the signal is delivered but its handler runs between
    bytecodes, and `check()` is one long call into C. Measured the hard way -- a probe
    with `signal.alarm(180)` around each model ran 152 workers past 900 seconds
    without one alarm firing.
    """
    spent = budgets.Spent(ms=250, max_queries=None)
    assert spent.for_query(900_000) <= 250, "the artifact's clock is the smaller one"
    assert spent.for_query(10) == 10, "and a shorter per-query ceiling still wins"

    never = budgets.Spent(ms=None, max_queries=None)
    assert never.for_query(900_000) == 900_000
    assert never.exhausted() is None

    assert spent.for_query(900_000, participants=5) <= 50
    with pytest.raises(ValueError, match="participants"):
        spent.for_query(10, participants=0)

    shared_start = budgets.Spent(
        ms=250, max_queries=None, now=lambda: 10.2, started=10.0)
    assert 49 <= shared_start.remaining_ms() <= 50


def test_query_charges_can_be_reserved_as_one_positive_batch():
    spent = budgets.Spent(ms=None, max_queries=5)
    spent.charge(4)
    assert spent.queries == 4
    assert spent.remaining_queries() == 1
    with pytest.raises(ValueError, match="positive"):
        spent.charge(0)


def test_artifact_charges_every_physical_component_query(monkeypatch):
    proc, env = build()
    run = residual.unroll(proc, env, 1)
    goal = min(ir.goals_of(proc.body))
    spent = budgets.Spent(ms=None, max_queries=7)

    class Unknown:
        answer = SolverAnswer.UNKNOWN
        reason = "the component batches timed out"
        subqueries = 3
        timings = StageTimings({"solve_ns": 1})
        shape = None

    limits = []

    def components(_formula, _targets, _timeout_ms, **options):
        limits.append(options["max_subqueries"])
        return Unknown()

    monkeypatch.setattr(residual, "solve_exact_components", components)
    found, ruled, open_, calls = residual.decide_open(
        run, proc, (goal,), 10_000, spent)

    assert not found and not ruled and set(open_) == {goal}
    assert len(calls) == 1
    assert limits == [7]
    assert spent.queries == 3


def test_a_component_deadline_before_launch_spends_no_query():
    spent = budgets.Spent(ms=None, max_queries=7)

    class NoLaunch:
        subqueries = 0

    residual._charge_physical_queries(spent, NoLaunch())

    assert spent.queries == 0




def test_the_worker_count_follows_from_memory_as_well_as_cores(monkeypatch):
    """Both constraints, measured on opposite sides of the same question.

    P1 measured more workers winning when the formula is small: nineteen goals from
    three rounds of eight workers to one round of nineteen, 34.9s to 18.0s. And a
    single worker was measured at **50.8 GB** on a ten-step unrolling, so a ceiling
    divided by that is a count of one. Neither `cpu_count - 2` nor a fixed small
    number is right, because the two regimes are the same code on different models.
    """
    # The release gate deliberately sets the operator cap to one.  This test
    # isolates the underlying core/memory calculation from that independent cap.
    monkeypatch.delenv("BEHAVE_ANALYSIS_WORKERS", raising=False)
    monkeypatch.setattr(budgets, "available_cpus", lambda: 66)
    goals = [f"g{i}" for i in range(64)]
    assert budgets.workers_for(goals, reservation_mb=None) == 64
    # One exact job has an 8-GiB aggregate ceiling.  A 1-GiB scheduling
    # reservation plus parent headroom permits five formula workers, not sixty-four
    # workers each reasoning from the host's much larger memory.
    assert budgets.workers_for(goals, reservation_mb=1_024,
                               total_mb=8_192) == 5
    # Room for more, and then cores and goals bind instead.
    many = budgets.workers_for(goals, reservation_mb=16, total_mb=1_000_000)
    assert many == budgets.workers_for(goals, reservation_mb=None)
    assert budgets.workers_for(["one"], reservation_mb=16,
                               total_mb=1_000_000) == 1
    # And it is never zero, however little there is: one worker that may hit the
    # ceiling and report it beats no worker and no report.
    assert budgets.workers_for(goals, reservation_mb=8_192, total_mb=64) == 1
    assert budgets.workers_for([], reservation_mb=None) == 0


def test_a_measured_formula_peak_raises_the_parallel_reservation():
    mib = 1 << 20
    floor = budgets.PARALLEL_WORKER_RESERVATION_MB

    assert budgets.reservation_for_peak(None) == floor
    assert budgets.reservation_for_peak(100 * mib) == floor
    assert budgets.reservation_for_peak(2_000 * mib) == 2_500
    with pytest.raises(ValueError, match="nonnegative"):
        budgets.reservation_for_peak(-1)


def test_measured_refinement_setup_gets_a_scheduling_not_semantic_reserve():
    assert residual._observed_setup_reserve_ms(0) == 1
    assert residual._observed_setup_reserve_ms(8_000_000) == 10
    with pytest.raises(ValueError, match="natural nanoseconds"):
        residual._observed_setup_reserve_ms(-1)


def test_parallel_refinement_does_not_start_when_measured_setup_will_not_fit(
        monkeypatch):
    import concurrent.futures

    proc, env = build()
    goals = sorted(ir.goals_of(proc.body))
    prefix_call = QueryCall(
        goals, SolverAnswer.UNKNOWN, reason="the set query timed out")
    submitted = []

    class Immediate:
        def __init__(self, value):
            self.value = value

        def result(self):
            return self.value

    class InlinePool:
        def __init__(self, max_workers, *, mp_context):
            self.max_workers = max_workers
            assert mp_context.get_start_method() == "spawn"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def submit(self, fn, work):
            submitted.append(fn)
            assert fn is residual._decide_prefix_chunk
            # Six milliseconds of setup receives a 7.5-ms reserve.  The complete
            # artifact has only 5 ms, so no singleton worker can be justified.
            return Immediate((
                [], (prefix_call,), tuple(goals), 1 << 20,
                3_000_000, 3_000_000,
            ))

    monkeypatch.setattr(
        concurrent.futures, "ProcessPoolExecutor", InlinePool)
    monkeypatch.setattr(budgets, "workers_for", lambda *_a, **_kw: 2)

    got = residual.decide_goals(
        proc, env, goals, workers=2, artifact_ms=5,
        timeout_ms=30_000, max_queries=None)

    assert submitted == [residual._decide_prefix_chunk]
    assert not got.reachable and not got.unreachable
    assert set(got.undecided) == set(goals)
    assert all("measured refinement setup" in reason
               for reason in got.undecided.values())
    assert got.queries[0].as_dict() == prefix_call.as_dict()
    assert [call.goals for call in got.queries[1:]] == [
        (goal,) for goal in goals]
    assert all(call.answer is SolverAnswer.UNKNOWN
               and call.solver_calls == 0 for call in got.queries[1:])


def test_one_singleton_measures_memory_before_the_remaining_pool(monkeypatch):
    import concurrent.futures

    proc, env = build()
    goals = tuple(sorted(ir.goals_of(proc.body)))
    assert len(goals) > 2
    prefix_call = QueryCall(
        goals, SolverAnswer.UNKNOWN, reason="the set query timed out")
    pools = []
    reservations = []
    singleton_work = []

    class Immediate:
        def __init__(self, value):
            self.value = value

        def result(self):
            return self.value

    class InlinePool:
        def __init__(self, max_workers, *, mp_context):
            self.max_workers = max_workers
            assert mp_context.get_start_method() == "spawn"
            pools.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def submit(self, fn, work):
            if fn is residual._decide_prefix_chunk:
                return Immediate((
                    [], (prefix_call,), goals, 100 << 20, 0, 0,
                ))
            assert fn is residual._decide_singleton_chunk
            chunk = work[4]
            singleton_work.append((chunk, work[-1]))
            rows = [(goal, "unsat", None) for goal in chunk]
            calls = tuple(
                QueryCall((goal,), SolverAnswer.UNSAT) for goal in chunk)
            peak = 2 << 30 if chunk == goals[:1] else 1 << 20
            return Immediate((rows, calls, peak))

    def workers_for(values, reservation_mb=None, total_mb=None):
        reservations.append((tuple(values), reservation_mb, total_mb))
        return min(len(values), 4 if reservation_mb == 1_024 else 2)

    monkeypatch.setattr(
        concurrent.futures, "ProcessPoolExecutor", InlinePool)
    monkeypatch.setattr(budgets, "workers_for", workers_for)

    got = residual.decide_goals(
        proc, env, goals, workers=4, artifact_ms=60_000,
        timeout_ms=30_000, memory_mb=8_192, max_queries=None)

    assert pools == [1, 1, 2]
    assert singleton_work[0] == (goals[:1], len(goals) - 1)
    assert all(reserved == 0 for _chunk, reserved in singleton_work[1:])
    assert reservations[0] == (goals, 1_024, 8_192)
    assert reservations[1] == (goals[1:], 2_560, 8_192)
    assert got.unreachable == set(goals)
    assert [call.goals for call in got.queries] == [
        goals, *((goal,) for goal in goals)
    ]


def test_an_explicit_nonpositive_solver_worker_count_is_not_auto():
    proc, env = build()

    with pytest.raises(ValueError, match="positive integer"):
        residual.decide_goals(proc, env, [], workers=0)


def test_a_finite_global_query_budget_keeps_catalogue_order(monkeypatch):
    proc, env = build()
    goals = sorted(ir.goals_of(proc.body))

    def parallel_pool_would_make_the_last_permit_a_race(*_args, **_kwargs):
        raise AssertionError("a finite global query budget must stay ordered")

    monkeypatch.setattr(residual, "_process_pool",
                        parallel_pool_would_make_the_last_permit_a_race)
    got = residual.decide_goals(
        proc, env, goals, workers=4, max_queries=64,
        timeout_ms=30_000)

    assert not got.undecided
    assert set(got.reachable) | got.unreachable == set(goals)


def test_preunroll_occurrence_fragments_preserve_the_public_verdict(monkeypatch):
    """Formula granularity changes physical work, never the bounded answer."""

    proc, env = build()
    goals = sorted(behavior_instance.BehaviorInstance(proc, {}).goals())
    built_indexes = []
    original_loop_bodies = slicing._loop_bodies

    def one_index(events):
        built_indexes.append(len(events))
        return original_loop_bodies(events)

    monkeypatch.setattr(slicing, "_loop_bodies", one_index)

    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    fragmented = residual.decide_goals(
        proc, env, goals, steps=2, timeout_ms=30_000)
    assert fragmented.queries
    assert all(len(call.goals) == 1 for call in fragmented.queries)
    assert len(built_indexes) == 1

    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: False)
    joined = residual.decide_goals(
        proc, env, goals, steps=2, timeout_ms=30_000)

    assert fragmented.reachable.keys() == joined.reachable.keys()
    assert fragmented.unreachable == joined.unreachable
    assert fragmented.undecided == joined.undecided == {}
    assert all(len(trace) == 2 for trace in fragmented.reachable.values())


def test_occurrence_batches_are_bounded_by_cone_work_not_site_count(monkeypatch):
    goal = "distant-copies"
    body = ir._sequence(tuple(
        command
        for name in ("a", "b", "c")
        for command in (
            ir.Assign("x", ir.Var(name)),
            ir.If(ir.Var("x"), ir.Mark(goal), ir.Skip()),
        )
    ))
    proc = ir.Procedure(("x",), ("a", "b", "c"), (), (), body,
                        initial={"x": 0})
    events = slicing.linearize(body)
    sites = tuple(
        position for position, (command, _under) in enumerate(events)
        if isinstance(command, ir.Mark))
    assert len(sites) == 3
    # sqrt(16) is four retained commands.  Each copied site's dependency cone
    # fits alone, but two distant copies do not.  A count-only sqrt batching
    # rule would combine two of the three sites here.
    monkeypatch.setattr(residual, "_occurrence_work_limit", lambda: 4)

    sites_by_goal = residual._occurrence_schedule((goal,), events)
    scheduled = residual._occurrence_fragments(
        proc, goal, sites_by_goal[goal], 1, slicing.index(proc))

    assert tuple((fragment.target_step, fragment.sites)
                 for fragment in scheduled) \
        == tuple((0, (site,)) for site in sites)
    assert all(type(fragment) is slicing.OccurrenceRunSlice
               for fragment in scheduled)


def test_one_unknown_occurrence_prevents_a_public_unsat(monkeypatch):
    goal = "copied-goal"
    proc = ir.Procedure(
        (), (), (), (),
        ir._sequence((ir.Mark(goal), ir.Mark(goal), ir.Mark(goal))))

    class Outcome:
        subqueries = 1
        model = None

        def __init__(self, answer):
            self.answer = answer
            self.reason = "one fragment timed out" \
                if answer is SolverAnswer.UNKNOWN else ""
            self.timings = StageTimings({"solve_ns": 1})

    answers = iter((SolverAnswer.UNKNOWN, SolverAnswer.UNSAT))
    monkeypatch.setattr(
        residual, "_solve_with_memory",
        lambda *_args, **_kwargs: Outcome(next(answers)))
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    monkeypatch.setattr(residual, "_occurrence_work_limit", lambda: 2)

    got = residual.decide_goals(
        proc, {}, (goal,), timeout_ms=30_000, artifact_ms=30_000)

    assert got.unreachable == set()
    assert got.undecided == {goal: "one fragment timed out"}
    assert len(got.queries) == 1
    assert got.queries[0].answer is SolverAnswer.UNKNOWN
    assert got.queries[0].solver_calls == 2


def test_worker_count_does_not_change_the_logical_queries(monkeypatch):
    """A resource choice may not partition the set posed to the solver."""
    monkeypatch.setattr(budgets, "available_cpus", lambda: 8)
    proc, env = build()
    goals = sorted(behavior_instance.BehaviorInstance(proc, {}).goals())

    local = residual.decide_goals(
        proc, env, goals, steps=1, timeout_ms=30_000, workers=1,
        max_queries=None)
    child = residual.decide_goals(
        proc, env, goals, steps=1, timeout_ms=30_000, workers=4,
        max_queries=None)

    assert child.reachable.keys() == local.reachable.keys()
    assert child.unreachable == local.unreachable
    assert child.undecided.keys() == local.undecided.keys()
    assert [call.as_dict() for call in child.queries] == [
        call.as_dict() for call in local.queries]


def test_the_machine_is_asked_what_it_was_given_not_what_it_has(monkeypatch):
    """A job on a shared node has an allocation, and `SC_PHYS_PAGES` does not know
    about it. Reading the hardware is how a 16 GB allocation on a 1.5 TB node comes
    to believe it can run a hundred and eighty workers of 8 GB."""
    monkeypatch.setenv("SLURM_MEM_PER_NODE", "16384")
    assert budgets.available_mb() == 16_384

    monkeypatch.delenv("SLURM_MEM_PER_NODE")
    monkeypatch.setenv("SLURM_MEM_PER_CPU", "2048")
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "8")
    assert budgets.available_mb() == 16_384

    for var in ("SLURM_MEM_PER_CPU", "SLURM_CPUS_ON_NODE"):
        monkeypatch.delenv(var)
    fallback = budgets.available_mb()
    assert fallback is None or fallback > 0
