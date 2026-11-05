"""A product probe spends one budget across construction and solving."""

import time

import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver


def test_native_solver_is_interrupted_without_poisoning_next_query():
    from behave.sim.rtl.query_deadline import QueryDeadline

    context = z3.Context()
    # Pigeonhole constraints keep a native call active. No solver-local timeout
    # is set: only the query owner's context watchdog can stop this check.
    pigeons = [z3.BitVec(f"p{i}", 8, ctx=context) for i in range(80)]
    solver = z3.Tactic("default", ctx=context).solver()
    solver.add(z3.Distinct(*pigeons), *(z3.ULT(p, 79) for p in pigeons))
    deadline = QueryDeadline(context, 50, started=time.monotonic())
    with pytest.raises(TimeoutError, match="query budget"), deadline:
        solver.check()
    assert deadline._expired.is_set()
    assert not deadline._timer.is_alive()
    following = z3.Solver(ctx=context)
    following.add(pigeons[0] == 7)
    assert following.check() == z3.sat
    assert following.model().eval(pigeons[0]).as_long() == 7

