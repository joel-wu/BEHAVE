"""Shared state computation must not share or weaken goal evidence."""

import itertools

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import budgets, exact_solver, residual
from behave.sim.symbolic import interp, ir, ranges, slicing
from behave.sim.behavior.occurrence_queries import GroupBudget, complete_groups
from behave.sim.core.query import SolverAnswer

from .test_occurrence_query_reuse import _loop_proc as _mixed_loop_proc
from .test_occurrence_query_reuse import _sequence


def _loop_proc(trips=2, stateful=False):
    # Distinct safety goals genuinely share a cyclic cone: every failure
    # affects later visits. Coverage-only marks no longer force that sharing.
    return _mixed_loop_proc(trips, stateful, failure_goals=True)


def _work(proc, goals, steps):
    index = slicing.index(proc)
    schedule = residual._occurrence_schedule(goals, index.events)
    return {goal: residual._occurrence_fragments(
        proc, goal, schedule[goal], steps, index) for goal in goals}


@pytest.mark.parametrize("stateful", [False, True])
@pytest.mark.parametrize("steps", [1, 2])
@pytest.mark.parametrize("trips", [0, 1, 2])
@pytest.mark.parametrize("failure_goals", [False, True])
def test_shared_public_answers_and_full_witnesses_match_exhaustive_execution(
        stateful, steps, trips, failure_goals, monkeypatch):
    proc = _mixed_loop_proc(trips, stateful, failure_goals=failure_goals)
    goals = ("hit", "bad", "never")
    env = {"a": ranges.Range(-1, 4), **({"s": ranges.Range(0, 4)} if stateful else {})}
    expected = set()
    for values in itertools.product(range(-1, 5), repeat=steps):
        _, _, hits, _ = interp.run_trace_detailed(proc, [{"a": v} for v in values])
        expected.update(set().union(*hits))
    built = []
    original = residual.unroll
    def observe(*args, **kwargs):
        if kwargs.get("_shared_occurrences") is not None:
            built.append(kwargs["goals"])
        return original(*args, **kwargs)
    monkeypatch.setattr(residual, "unroll", observe)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    result = residual.decide_goals(proc, env, goals, steps=steps, workers=1)
    assert set(result.reachable) == expected
    assert result.unreachable == set(goals) - expected
    assert not result.undecided
    # A zero-trip body has no live goal occurrences: the literal proof answers
    # it without constructing a shared transition. Failure cones share exactly;
    # mixed coverage cones must not be equated merely because they share a loop.
    assert bool(built) == (failure_goals and trips > 0 and (not stateful or steps == 1))
    for goal, trace in result.reachable.items():
        assert len(trace) == steps
        _, _, hits, _ = interp.run_trace_detailed(proc, trace)
        assert goal in set().union(*hits)
    for call in result.queries:
        if call.answer is SolverAnswer.SAT:
            assert len(call.witness) == steps
            _, _, hits, _ = interp.run_trace_detailed(proc, call.witness)
            assert set(call.goals) & set().union(*hits)
        elif call.answer is SolverAnswer.UNSAT:
            assert not (set(call.goals) & expected)


def test_multifragment_goal_is_not_promoted_to_complete_by_one_shared_selection():
    proc = _loop_proc(stateful=True)
    work = _work(proc, ("hit", "bad", "never"), 2)
    assert all(len(parts) == 2 for parts in work.values())
    assert not complete_groups(work)


def test_distinct_hit_conditions_remain_distinct_in_the_shared_formula():
    proc = _loop_proc()
    goals = ("hit", "bad", "never")
    work = _work(proc, goals, 1)
    run = residual.unroll(proc, {"a": ranges.Range(-1, 4)}, 1, goals=goals,
                          _shared_occurrences=tuple(work[g][0] for g in goals))
    assert run.query_scope == frozenset(goals)
    for value in range(-1, 5):
        _, _, hits, _ = interp.run_trace_detailed(proc, [{"a": value}])
        expected = set().union(*hits)
        solver = z3.Solver()
        solver.add(run.constraint, run.encodings[0].pre["a"] == value)
        assert solver.check() == z3.sat
        for goal in goals:
            solver.push()
            solver.add(run.goal_condition(goal) != (goal in expected))
            assert solver.check() == z3.unsat
            solver.pop()


@pytest.mark.parametrize("fault", ["owner", "horizon", "goal", "selection", "unsealed",
                                   "duplicate", "count", "two-authorities"])
def test_shared_unrolling_rejects_invalid_selection_authority(fault):
    proc = _loop_proc()
    goals = ("hit", "bad")
    work = _work(proc, goals, 1)
    parts = tuple(work[g][0] for g in goals)
    extra = {}
    if fault == "owner":
        proc = _loop_proc()
    elif fault == "horizon":
        parts = tuple(_work(proc, goals, 2)[g][0] for g in goals)
    elif fault == "goal":
        parts = parts[::-1]
    elif fault == "selection":
        proc = proc.derived(body=_sequence(ir.Mark("hit"), proc.body))
        parts = tuple(_work(proc, goals, 1)[g][0] for g in goals)
        assert parts[0].keeps != parts[1].keeps
    elif fault == "unsealed":
        parts = (parts[0].keeps, parts[1])
    elif fault == "duplicate":
        goals, parts = ("hit", "hit"), (parts[0], parts[0])
    elif fault == "count":
        parts = parts[:1]
    else:
        extra["_occurrence"] = parts[0]
    with pytest.raises((TypeError, ValueError)):
        residual.unroll(proc, {"a": ranges.Range(-1, 4)}, 1,
                        goals=goals, _shared_occurrences=parts, **extra)


def test_group_unknown_is_refined_without_inventing_unreachable_goals(monkeypatch):
    from behave.sim.symbolic.exact_solve import ExactCheckResult

    def unknown(*_args, **_kwargs):
        return ExactCheckResult(SolverAnswer.UNKNOWN, reason="forced unknown")
    monkeypatch.setattr(exact_solver, "_check", unknown)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    result = residual.decide_goals(_loop_proc(), {"a": ranges.Range(-1, 4)},
                                   ("hit", "bad"), steps=1, workers=1)
    assert not result.reachable and not result.unreachable
    assert set(result.undecided) == {"hit", "bad"}
    assert {call.goals[0] for call in result.queries if len(call.goals) == 1} == {"hit", "bad"}


def test_audited_complete_selection_is_not_resliced_during_refinement(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("the complete shared formula must not rediscover dependency cones")
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    monkeypatch.setattr(residual, "_dependency_partitions", forbidden)
    monkeypatch.setattr(residual, "solve_exact_components", forbidden)
    result = residual.decide_goals(_loop_proc(), {"a": ranges.Range(-1, 4)},
                                   ("hit", "bad", "never"), steps=1, workers=1)
    assert set(result.reachable) == {"hit", "bad"}
    assert result.unreachable == {"never"} and not result.undecided


def test_complete_selection_cannot_claim_a_different_goal_scope():
    proc = _loop_proc()
    run = residual.unroll(proc, {"a": ranges.Range(-1, 4)}, 1, goals=("hit", "bad"))
    with pytest.raises(ValueError, match="audited goal scope"):
        residual.decide_open(run, proc, ("hit",), 1000, _complete_selection=True)


def test_shared_group_retains_global_physical_query_limit(monkeypatch):
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    result = residual.decide_goals(_loop_proc(), {"a": ranges.Range(-1, 4)},
                                   ("hit", "bad", "never"), steps=1, workers=1,
                                   max_queries=1)
    assert sum(call.solver_calls for call in result.queries) == 1
    assert set(result.reachable) | result.unreachable | set(result.undecided) \
        == {"hit", "bad", "never"}


def test_shared_group_memory_failure_is_unknown_not_unsat(monkeypatch):
    original = residual.unroll
    def out_of_memory(*args, **kwargs):
        if kwargs.get("_shared_occurrences") is not None:
            raise MemoryError
        return original(*args, **kwargs)
    monkeypatch.setattr(residual, "unroll", out_of_memory)
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    result = residual.decide_goals(_loop_proc(), {"a": ranges.Range(-1, 4)},
                                   ("hit", "bad"), steps=1, workers=1)
    assert not result.reachable and not result.unreachable
    assert set(result.undecided) == {"hit", "bad"}
    assert all(call.solver_calls == 0 for call in result.queries)


def test_group_deadline_includes_construction_and_charges_only_parent():
    now = [0.0]
    parent = budgets.Spent(ms=1000, max_queries=2, now=lambda: now[0])
    group = GroupBudget(parent, 100, now=lambda: now[0])
    now[0] = 0.05
    assert group.for_query(1000) <= 50
    group.charge()
    assert parent.queries == 1 and group.remaining_queries() == 1
    now[0] = 0.101
    assert "shared exact-query clock" in group.exhausted()
    assert not parent.exhausted()
    parent.charge()
    assert "query budget" in group.exhausted()


@pytest.mark.parametrize("cap", [1, None])
def test_interleaved_unshared_goals_do_not_reopen_answered_group_members(cap, monkeypatch):
    base = _loop_proc()
    proc = base.derived(body=_sequence(ir.Mark("outside"), base.body))
    goals = ("hit", "outside", "bad", "never")
    monkeypatch.setattr(residual, "_preunroll_split", lambda *_args: True)
    result = residual.decide_goals(proc, {"a": ranges.Range(-1, 4)}, goals,
                                   steps=1, workers=1, max_queries=cap)
    categories = [set(result.reachable), result.unreachable, set(result.undecided)]
    assert set().union(*categories) == set(goals)
    assert sum(map(len, categories)) == len(goals)
    if cap is None:
        assert set(result.reachable) == {"hit", "outside", "bad"}
        assert result.unreachable == {"never"} and not result.undecided
    else:
        assert sum(call.solver_calls for call in result.queries) == 1
        singletons = [call.goals[0] for call in result.queries if len(call.goals) == 1]
        assert len(singletons) == len(set(singletons))
