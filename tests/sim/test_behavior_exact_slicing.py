"""Production checks for Behavior's audited definitional-slicing boundary."""

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

import behave.sim.symbolic.exact_solve as exact_solve_mod
from behave.sim.symbolic.exact_formula import (
    ExactFormula,
    FormulaDefinition,
    SliceFallbackCode,
    SliceMode,
)
from behave.sim.symbolic.exact_solve import (
    ExactAnswerAuthority,
    ExactCheckResult,
    ExactPhase,
)
from behave.sim.behavior import budgets, exact_solver, residual
from behave.sim.symbolic import ir, ranges, typecheck as types
from behave.sim.core.query import SolverAnswer
from behave.sim.core.query_telemetry import QueryTelemetryScope
from behave.sim.core.refusal import Refused


def _formula():
    u = z3.BitVec("behavior_exact_u", 8)
    x = z3.BitVec("behavior_exact_x", 8)
    dead = z3.BitVec("behavior_exact_dead", 8)
    return ExactFormula(
        definitions=(
            FormulaDefinition("x", x, u + 1),
            FormulaDefinition("dead", dead, u + 9),
        ),
        boundary_atoms=(u,),
    ), u, x, dead


def test_a_sliced_sat_is_pinned_and_rebuilt_by_the_full_formula():
    formula, u, x, dead = _formula()

    result = exact_solver.solve_exact(formula, x == 2, 10_000)

    assert result.answer is SolverAnswer.SAT
    assert result.phases == (
        ExactPhase.SLICE, ExactPhase.FULL_PINNED_SAT_VALIDATION)
    assert result.slice.mode is SliceMode.SLICED
    assert result.slice.dropped_definitions == ("dead",)
    assert result.model.eval(u).as_long() == 1
    # ``dead`` is absent from the reduced query.  Its value in the published
    # model proves that publication came from the complete formula.
    assert result.model.eval(dead).as_long() == 10
    assert result.shape.full_assertions > result.shape.sliced_assertions
    assert {"slice_ns", "solver_load_ns", "solve_ns"} \
        <= set(result.timings.as_dict())


def test_formula_measurement_failure_withholds_only_telemetry(monkeypatch):
    formula, _u, x, _dead = _formula()

    def broken_measurement(*_args, **_kwargs):
        raise RuntimeError("instrumentation failed")

    monkeypatch.setattr(
        exact_solve_mod, "measure_formula_shape", broken_measurement)
    result = exact_solver.solve_exact(formula, x == 2, 10_000)

    assert result.answer is SolverAnswer.SAT
    assert result.shape is None
    assert "solve_ns" in result.timings.as_dict()


def test_slice_unknown_retries_the_complete_formula_in_one_call(monkeypatch):
    formula, _u, x, _dead = _formula()
    real_check = exact_solver._check
    calls = []

    def first_unknown(assertions, timeout_ms):
        calls.append(tuple(assertions))
        if len(calls) == 1:
            return ExactCheckResult(
                SolverAnswer.UNKNOWN, reason="forced slice unknown")
        return real_check(assertions, timeout_ms)

    monkeypatch.setattr(exact_solver, "_check", first_unknown)
    result = exact_solver.solve_exact(formula, x == 2, 10_000)

    assert result.answer is SolverAnswer.SAT
    assert result.phases == (
        ExactPhase.SLICE, ExactPhase.FULL_AFTER_SLICE_UNKNOWN)
    assert len(calls) == 2


def test_an_audited_sliced_unsat_answer_needs_no_full_solver_retry(monkeypatch):
    formula, _u, x, _dead = _formula()
    real_check = exact_solver._check
    calls = []

    def record(assertions, timeout_ms):
        calls.append(tuple(assertions))
        return real_check(assertions, timeout_ms)

    monkeypatch.setattr(exact_solver, "_check", record)
    result = exact_solver.solve_exact(
        formula, z3.And(x == 2, x == 3), 10_000)

    assert result.answer is SolverAnswer.UNSAT
    assert result.phases == (ExactPhase.SLICE,)
    assert result.slice.mode is SliceMode.SLICED
    assert len(calls) == 1


def test_unsat_sat_validation_is_a_typed_internal_refusal(monkeypatch):
    formula, _u, x, _dead = _formula()
    real_check = exact_solver._check
    calls = []

    def reject_validation(assertions, timeout_ms):
        calls.append(tuple(assertions))
        if len(calls) == 2:
            return ExactCheckResult(SolverAnswer.UNSAT)
        return real_check(assertions, timeout_ms)

    monkeypatch.setattr(exact_solver, "_check", reject_validation)
    with pytest.raises(
            Refused, match="behavior-exact-slice-validation-disagrees"):
        exact_solver.solve_exact(formula, x == 2, 10_000)

    assert len(calls) == 2


def test_unknown_sat_validation_is_published_as_unknown_without_unpinned_retry(
        monkeypatch):
    formula, _u, x, _dead = _formula()
    real_check = exact_solver._check
    calls = []

    def lose_validation(assertions, timeout_ms):
        calls.append(tuple(assertions))
        if len(calls) == 2:
            return ExactCheckResult(
                SolverAnswer.UNKNOWN, reason="forced validation unknown")
        return real_check(assertions, timeout_ms)

    monkeypatch.setattr(exact_solver, "_check", lose_validation)
    result = exact_solver.solve_exact(formula, x == 2, 10_000)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.reason == "forced validation unknown"
    assert result.phases == (
        ExactPhase.SLICE, ExactPhase.FULL_PINNED_SAT_VALIDATION)
    assert len(calls) == 2


def test_an_expired_shared_deadline_does_not_launch_another_solver_phase(
        monkeypatch):
    formula, _u, x, _dead = _formula()
    moments = iter((10.0, 10.011))
    monkeypatch.setattr(exact_solve_mod.time, "monotonic", moments.__next__)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a solver phase started after the shared deadline")

    monkeypatch.setattr(exact_solver, "_check", forbidden)
    result = exact_solver.solve_exact(formula, x == 2, 10)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.phases == (ExactPhase.SLICE,)
    assert "deadline was exhausted" in result.reason


def test_incomplete_registration_uses_the_full_formula_verbatim():
    missing = z3.BitVec("behavior_exact_unregistered", 8)
    x = z3.BitVec("behavior_exact_registered", 8)
    formula = ExactFormula(
        definitions=(FormulaDefinition("x", x, missing + 1),))

    result = exact_solver.solve_exact(formula, x == 2, 10_000)

    assert result.answer is SolverAnswer.SAT
    assert result.phases == (ExactPhase.FULL,)
    assert result.slice.mode is SliceMode.FULL
    assert result.slice.fallback.code is SliceFallbackCode.UNKNOWN_ATOM


def test_behavior_refuses_a_formula_outside_its_declared_qfbv_fragment():
    integer = z3.Int("behavior_exact_integer")
    formula = ExactFormula(boundary_atoms=(integer,))

    with pytest.raises(Refused, match="behavior-exact-fragment-unavailable"):
        exact_solver.solve_exact(formula, integer == 1, 10_000)


def _production_run():
    goal = "branch:exact-slice.py:1:0:0:T"
    body = ir.Seq(
        ir.Assign("dead", ir.Op("add", ir.Var("a"), ir.Lit(9))),
        ir.Seq(
            ir.Assign("y", ir.Op("add", ir.Var("a"), ir.Lit(1))),
            ir.If(
                ir.Op("eq", ir.Var("y"), ir.Lit(2)),
                ir.Mark(goal),
                ir.Skip(),
            ),
        ),
    )
    proc = ir.Procedure(
        (), ("a",), ("dead",), ("y",), body,
        types={"a": types.INTEGER, "dead": types.INTEGER, "y": types.INTEGER},
    )
    return proc, residual.unroll(proc, {"a": ranges.Range(0, 3)}, 1), goal


def test_behavior_unrolling_registers_definitions_at_the_encoder_boundary():
    _proc, run, goal = _production_run()

    selected = run.formula.slice(run.goal_condition(goal))

    assert selected.mode is SliceMode.SLICED
    assert selected.kept_definitions
    assert selected.dropped_definitions
    assert tuple(str(atom) for atom in run.formula.boundary_atoms) == ("a@0",)


def test_generated_behavior_sat_uses_its_registered_extension_theorem():
    _proc, run, goal = _production_run()

    result = exact_solver.solve_exact(
        run.formula, run.goal_condition(goal), 10_000)

    assert result.answer is SolverAnswer.SAT
    assert result.phases == (ExactPhase.SLICE,)
    assert result.authority is ExactAnswerAuthority.AUDITED_SLICE
    assert result.model.eval(run.inputs_at(0, "a")).as_long() == 1


def test_internal_atoms_are_distinct_across_multidigit_step_indices():
    """Unrolling must not identify encoder-local values from two steps."""
    commands = []
    carriers = {"a": ranges.Range(0, 1)}
    variable_types = {"a": types.INTEGER}
    for index in range(14):
        source = "a" if index == 0 else f"v{index - 1}"
        name = f"v{index}"
        commands.append(ir.Assign(
            name, ir.Op("add", ir.Var(source), ir.Lit(index + 1))))
        low = sum(range(1, index + 2))
        carriers[name] = ranges.Range(low, low + 1)
        variable_types[name] = types.INTEGER
    body = commands[0]
    for command in commands[1:]:
        body = ir.Seq(body, command)
    proc = ir.Procedure(
        (), ("a",), tuple(f"v{i}" for i in range(13)), ("v13",), body,
        types=variable_types,
    )

    run = residual.unroll(proc, carriers, 12)
    names = [
        definition.lhs.decl().name()
        for step in run.encodings
        for definition in step.definitions
    ]

    assert all(len(step.definitions) >= 11 for step in run.encodings)
    assert len(names) == len(set(names))


def test_behavior_slice_and_complete_formula_agree_on_every_boundary_value():
    _proc, run, goal = _production_run()
    target = run.goal_condition(goal)
    selected = run.formula.slice(target)

    assert selected.mode is SliceMode.SLICED
    for value in range(4):
        pin = run.inputs_at(0, "a") == value
        full = z3.Solver()
        full.add(*run.formula.full_constraints, pin, target)
        sliced = z3.Solver()
        sliced.add(*selected.constraints, pin, target)
        assert sliced.check() == full.check(), value


def test_internal_slice_phases_consume_one_query_budget_and_one_transcript_call():
    proc, run, goal = _production_run()
    spent = budgets.Spent(ms=None, max_queries=1)

    reached, ruled, open_, transcript = residual.decide_open(
        run, proc, (goal,), 10_000, spent,
        telemetry_scope=QueryTelemetryScope(
            backend="behavior-z3", semantic_instance="test:behavior-slice",
            horizon=1))

    assert goal in reached
    assert not ruled and not open_
    assert spent.queries == 1
    assert len(transcript) == 1
    assert transcript[0].goals == (goal,)
    assert transcript[0].telemetry.shape.sliced_assertions > 0
    assert {"slice_ns", "solver_load_ns", "solve_ns", "decode_ns"} \
        <= set(transcript[0].telemetry.timings.as_dict())


def test_production_queries_never_prune_the_source_procedure(monkeypatch):
    proc, _run, goal = _production_run()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the exact query attempted arbitrary source slicing")

    monkeypatch.setattr(
        "behave.sim.symbolic.slicing.sliced", forbidden)
    result = residual.decide_goals(
        proc, {"a": ranges.Range(0, 3)}, (goal,),
        steps=1, timeout_ms=10_000, workers=1)

    assert goal in result.reachable
