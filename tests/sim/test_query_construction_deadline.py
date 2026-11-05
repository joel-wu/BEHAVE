"""Budget stops construction; incomplete questions never acquire proof authority."""

import time
from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.exact_formula import _FormulaConstruction
from behave.sim.core.construction_checkpoint import ConstructionCheckpoint
from behave.sim.behavior import budgets, occurrence_queries, residual
from behave.sim.symbolic import encoder, ir, plan, ranges, typecheck as types
from behave.sim.core.query import SolverAnswer

from .test_occurrence_query_reuse import _loop_proc, _sequence

pytestmark = pytest.mark.solver


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    original = budgets.Spent

    def spent(*args, **kwargs):
        if kwargs.get("now") is None:
            kwargs["now"] = lambda: now[0]
        return original(*args, **kwargs)

    monkeypatch.setattr(budgets, "Spent", spent)
    monkeypatch.setattr(occurrence_queries, "Spent", spent)
    monkeypatch.setattr(residual, "time", SimpleNamespace(
        monotonic=lambda: now[0], monotonic_ns=time.monotonic_ns))
    return now


@pytest.mark.parametrize("path", ["joined", "fragment", "group", "absent", "transactions",
                                  "prefix", "singleton"])
def test_construction_expiry_is_unknown_without_launching_solver(path, clock, monkeypatch):
    proc = _loop_proc(failure_goals=path == "group")
    env = {"a": ranges.Range(-1, 4)}
    goals = ("hit", "bad") if path == "group" else ("absent" if path == "absent" else "hit",)
    original = encoder._encode_step
    reached = []

    def expire(*args, **kwargs):
        reached.append(True)
        clock[0] = 2.0
        # Exercise the encoder's real entry checkpoint, not a mocked verdict.
        return original(*args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("an incomplete formula must not enter the solver")

    monkeypatch.setattr(encoder, "_encode_step", expire)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *args: path in {
        "fragment", "group", "absent"})
    monkeypatch.setattr(residual, "_solve_with_memory", forbidden)
    if path in {"prefix", "singleton"}:
        work = (proc, env, 1, None, goals, 1000, 1000, None, None, None, 0.0)
        if path == "prefix":
            rows, calls, deferred, *_ = residual._decide_prefix_chunk(work)
            assert not deferred
        else:
            rows, calls, _ = residual._decide_singleton_chunk((*work, 0, 0))
        assert {goal for goal, verdict, _ in rows if verdict == "unknown"} == set(goals)
    else:
        result = residual.decide_goals(
            proc, env, goals, workers=1, artifact_ms=1000,
            transactions=object() if path == "transactions" else None)
        assert not result.reachable and not result.unreachable
        assert set(result.undecided) == set(goals)
        calls = result.queries
    assert reached and calls
    assert all(call.answer is SolverAnswer.UNKNOWN and call.solver_calls == 0 for call in calls)
    assert all("1000 ms" in call.reason for call in calls)


def test_expired_fragment_retains_prior_sat_and_literal_unsat(clock, monkeypatch):
    proc = _loop_proc()
    original = residual.unroll
    built = []

    def expire_second(*args, **kwargs):
        built.append(kwargs["goals"])
        if kwargs["goals"] == ("bad",):
            clock[0] = 2.0
        return original(*args, **kwargs)

    monkeypatch.setattr(residual, "unroll", expire_second)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *args: True)
    result = residual.decide_goals(
        proc, {"a": ranges.Range(-1, 4)}, ("never", "hit", "bad"),
        workers=1, artifact_ms=1000)
    assert set(result.reachable) == {"hit"}
    assert result.unreachable == {"never"}
    assert set(result.undecided) == {"bad"}
    assert built == [("hit",), ("bad",)]
    assert sum(call.solver_calls for call in result.queries) == 2
    assert result.queries[-1].solver_calls == 0


def test_partial_unsat_cover_cannot_settle_the_goal(clock, monkeypatch):
    proc = ir.Procedure((), ("a",), (), (), _sequence(*(
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(value)), ir.Mark("hit"), ir.Skip())
        for value in (0, 1))), types={"a": types.INTEGER})
    original = residual.unroll
    built = []

    def expire_second(*args, **kwargs):
        built.append(kwargs["_occurrence"])
        if len(built) == 2:
            clock[0] = 2.0
        return original(*args, **kwargs)

    monkeypatch.setattr(residual, "unroll", expire_second)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *args: True)
    monkeypatch.setattr(residual, "_occurrence_work_limit", lambda: 1)
    result = residual.decide_goals(proc, {"a": ranges.Range(1, 1)}, ("hit",),
                                   workers=1, artifact_ms=1000)
    assert len(built) == 2
    assert not result.reachable and not result.unreachable
    assert set(result.undecided) == {"hit"}
    assert result.queries[0].answer is SolverAnswer.UNKNOWN
    assert result.queries[0].solver_calls == 1


def test_formula_registration_observes_construction_deadline(clock, monkeypatch):
    original = _FormulaConstruction.advance
    reached = []

    def expired(owner, checkpoint):
        reached.append(owner)
        clock[0] = 2.0
        return original(owner, checkpoint)

    monkeypatch.setattr(_FormulaConstruction, "advance", expired)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *args: False)
    result = residual.decide_goals(_loop_proc(), {"a": ranges.Range(-1, 4)}, ("hit",),
                                   workers=1, artifact_ms=1000)
    assert len(reached) == 1 and reached[0].result is None
    assert set(result.undecided) == {"hit"}
    assert not result.reachable and not result.unreachable
    assert result.queries[0].solver_calls == 0


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("error", [RuntimeError, TimeoutError])
def test_unrelated_exceptions_are_not_reclassified_as_budget_expiry(split, error, monkeypatch):
    def broken(*args, **kwargs):
        raise error("actual construction defect")

    monkeypatch.setattr(residual, "_preunroll_split", lambda *args: split)
    monkeypatch.setattr(encoder, "_encode_step", broken)
    with pytest.raises(error, match="actual construction defect"):
        residual.decide_goals(_loop_proc(), {"a": ranges.Range(-1, 4)}, ("hit",), workers=1)


@pytest.mark.parametrize("phase", ["loop", "expansion", "carriers"])
def test_cancelled_plan_has_no_published_partial_result(phase, monkeypatch):
    proc = _loop_proc()
    env = {"a": ranges.Range(-1, 4)}
    owner = encoder._procedure_validation(proc)
    current = [None]
    stopped = []

    for name, stage in (("_loop_plan", "loop"), ("_contextual_expansion", "expansion"),
                        ("_occurrence_carriers", "carriers")):
        original = getattr(plan, name)

        def enter(*args, _original=original, _stage=stage, **kwargs):
            current[0] = _stage
            return _original(*args, **kwargs)

        monkeypatch.setattr(plan, name, enter)

    def cancel():
        if current[0] == phase:
            stopped.append(phase)
            raise residual._ConstructionExpired("planning expired")

    with pytest.raises(residual._ConstructionExpired, match="planning expired"):
        plan.reuse_encoding_plan(owner, env, _checkpoint=cancel)
    assert stopped == [phase]
    assert not owner._implicit_plans
    actual = plan.reuse_encoding_plan(owner, env)
    assert actual is plan.reuse_encoding_plan(owner, env)
    assert actual == plan.plan_encoding(proc, env)


def test_shared_plan_uses_artifact_clock_and_survives_short_goal_expiry(clock, monkeypatch):
    proc = _loop_proc()
    env = {"a": ranges.Range(-1, 4)}
    prepared = residual._prepare_unroll(proc, env, 1, None)
    original = plan.plan_encoding
    builds = []

    def advance_clock(*args, **kwargs):
        builds.append(True)
        clock[0] += 0.2
        return original(*args, **kwargs)

    monkeypatch.setattr(plan, "plan_encoding", advance_clock)
    artifact = budgets.Spent(ms=1000, max_queries=None)
    goal = occurrence_queries.GroupBudget(artifact, 100)
    with pytest.raises(residual._ConstructionExpired, match="100 ms"):
        residual.unroll(proc, env, 1, goals=("hit",), _prepared=prepared,
                        _checkpoint=residual._budget_checkpoint(goal),
                        _plan_checkpoint=residual._budget_checkpoint(artifact))
    assert prepared.validation._implicit_plans
    fresh_goal = occurrence_queries.GroupBudget(artifact, 100)
    run = residual.unroll(proc, env, 1, goals=("bad",), _prepared=prepared,
                          _checkpoint=residual._budget_checkpoint(fresh_goal),
                          _plan_checkpoint=residual._budget_checkpoint(artifact))
    assert builds == [True]
    expected = residual.unroll(proc, env, 1, goals=("bad",))
    assert z3.eq(run.constraint, expected.constraint)
    assert z3.eq(run.goal_condition("bad"), expected.goal_condition("bad"))


def test_expiry_at_plan_publication_keeps_successor_private(monkeypatch):
    original = plan._derive_encoding_plan
    complete = [False]
    successors = []

    def finish(*args, **kwargs):
        result = original(*args, **kwargs)
        complete[0] = True
        return result

    def check():
        if complete[0]:
            raise residual._ConstructionExpired("completed after the budget")

    monkeypatch.setattr(plan, "_derive_encoding_plan", finish)
    with pytest.raises(residual._ConstructionExpired, match="after the budget"):
        plan.plan_encoding(_loop_proc(), {"a": ranges.Range(-1, 4)},
                           _successor_sink=successors.append, _checkpoint=check)
    assert complete[0] and not successors


def test_completed_formula_does_not_retain_construction_deadline():
    expired = [False]
    checks = []

    def check():
        checks.append(True)
        if expired[0]:
            raise residual._ConstructionExpired("finished construction clock")

    proc = _loop_proc()
    env = {"a": ranges.Range(-1, 4)}
    expected = residual.unroll(proc, env, 2)
    actual = residual.unroll(proc, env, 2, _checkpoint=check)
    assert checks
    count = len(checks)
    expired[0] = True
    assert z3.eq(actual.constraint, expected.constraint)
    for goal in ("hit", "bad", "never"):
        assert z3.eq(actual.goal_condition(goal), expected.goal_condition(goal))
    solver = z3.Solver()
    solver.add(actual.constraint, actual.goal_condition("hit"))
    assert solver.check() == z3.sat
    assert len(checks) == count


def test_unbudgeted_encoding_retains_no_deadline_callback(monkeypatch):
    original = encoder._encode_step

    def encode(*args, **kwargs):
        assert kwargs["_checkpoint"] is None
        assert kwargs["_plan_checkpoint"] is None
        return original(*args, **kwargs)

    monkeypatch.setattr(encoder, "_encode_step", encode)
    run = residual.unroll(_loop_proc(), {"a": ranges.Range(-1, 4)}, 1)
    # Nested resumable operations may retain detached cells, never an owning
    # operation or active callback. The ordinary encoder still receives None.
    callback, seen = run.formula._checkpoint, set()
    while callback is not None:
        assert type(callback) is ConstructionCheckpoint
        assert id(callback) not in seen
        seen.add(id(callback))
        callback = callback.callback
