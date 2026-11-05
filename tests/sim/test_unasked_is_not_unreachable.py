"""A goal nobody encoded is not a goal nobody can reach.

`StepEncoding.goal_condition` returned `BoolVal(False)` for a goal the step does not
encode. A caller asserted it, got `unsat`, and reported **`LOCAL_UNSAT` -- "no admissible
input reaches it from this snapshot"**: a claim about a query that was never posed. Worse,
`exact.py` carried a guard against exactly this, `if condition is None`, and it could
never fire, because nothing ever returned `None`. The protection and the defect were in
the same function.

This is the same shape as the rest of the corrections in this tree -- `LOCAL_UNSAT` for a
question outside the domain, a `False` where there was no frontier, a bare stall standing
for four different failures. An absence has to be its own outcome, or it is read as the
negative answer that happens to look like it.

So: the supported set is explicit, asking outside it raises, and every caller reports
`UNSUPPORTED` or `undecided`. Never `unsat`.
"""
import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver

SOURCE = ('class M:\n'
          '    def reset(self):\n'
          '        self.seen = 0\n'
          '    def process(self, a):\n'
          '        y = 0\n'
          '        if self.seen:\n'
          '            y = 1\n'
          '        if a & 1:\n'
          '            self.seen = 1\n'
          '        return {"y": y}\n')


@pytest.fixture
def encoding(tmp_path):
    from behave.sim.symbolic import ranges
    from behave.sim.symbolic.encoder import encode_step
    from behave.sim.behavior.lowering import lower_with_state_ranges

    proc = lower_with_state_ranges(SOURCE, file="behavior.py")
    env = ranges.fixpoint(proc, {n: ranges.Range(0, 255) for n in proc.inputs})
    return encode_step(proc, env=env)


def test_the_supported_goals_are_stated(encoding):
    """Stated, because the alternative is that a caller infers the set from whether the
    answer came back false -- and false is also what an unreachable goal looks like."""
    assert encoding.goals
    for goal in encoding.goals:
        assert encoding.supports(goal)
    assert not encoding.supports("no:such:goal")


def test_asking_for_a_goal_the_step_does_not_encode_raises(encoding):
    """A value for a caller that can proceed with a non-answer, an exception for one that
    cannot -- and a caller about to assert the condition cannot."""
    from behave.sim.symbolic.encoder import EncodingError

    with pytest.raises(EncodingError) as caught:
        encoding.goal_condition("no:such:goal")
    assert caught.value.kind == "goal"
    assert "no:such:goal" in str(caught.value)


def test_a_known_goal_still_answers(encoding):
    goal = min(encoding.goals)
    condition = encoding.goal_condition(goal)
    assert z3.is_bool(condition)


def test_the_exact_engine_reports_unsupported_and_not_unsat(tmp_path):
    """The defect as a caller saw it. Before this, the probe came back `LOCAL_UNSAT`
    with "no admissible input reaches it from this snapshot" -- for a goal it had not
    asked about."""
    from behave.sim.symbolic import ranges
    from behave.sim.behavior.lowering import lower_with_state_ranges
    from behave.sim.engines import probing
    from behave.sim.engines.local_ir import local_hit

    proc = lower_with_state_ranges(SOURCE, file="behavior.py")
    env = ranges.fixpoint(proc, {n: ranges.Range(0, 255) for n in proc.inputs})
    state = {name: 0 for name in proc.state}
    got = local_hit(proc, env, state, "no:such:goal", timeout_ms=5_000)
    assert got.outcome is probing.Outcome.UNSUPPORTED, \
        f"an unasked goal came back as {got.outcome}: {got.detail}"
    assert got.outcome is not probing.Outcome.LOCAL_UNSAT


def test_an_unrolling_refuses_a_goal_it_does_not_encode(tmp_path):
    """One level up, and the same defect: the steps all encode one `proc`, so a goal
    missing from one is missing from all and the disjunction over them is `False`."""
    from behave.sim.behavior import residual
    from behave.sim.symbolic import ranges
    from behave.sim.symbolic.encoder import EncodingError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    proc = lower_with_state_ranges(SOURCE, file="behavior.py")
    env = ranges.fixpoint(proc, {n: ranges.Range(0, 255) for n in proc.inputs})
    run = residual.unroll(proc, env, steps=2)

    assert not run.supports("no:such:goal")
    with pytest.raises(EncodingError):
        run.goal_condition("no:such:goal")

    verdict, detail = residual.decide_one(run, proc, "no:such:goal", 5_000)
    assert verdict != "unsat", "an unasked goal was reported unreachable"
    assert verdict == "unsupported"
    assert "no query was posed" in detail


def test_no_surface_that_takes_a_goal_reports_an_unknown_one_unreachable():
    """The guard for the class rather than for the instance.

    This shape has now appeared three times in this tree -- `LOCAL_UNSAT` for a question
    outside the domain, a bare `False` where there was no frontier, and this. Each time
    the absence was reported as the negative answer that happens to look like it, and
    each time it was found by reading rather than by a test. So every public surface that
    takes a goal identifier is listed here, and each has to answer an unknown goal with
    something that is **not** "unreachable".

    A surface added later and not added here is the way this comes back. The list is the
    test.
    """
    from behave.sim.behavior import residual
    from behave.sim.symbolic import ranges
    from behave.sim.behavior.instrumented import Classification
    from behave.sim.behavior.lowering import lower_with_state_ranges
    from behave.sim.engines import probing
    from behave.sim.engines.local_ir import local_hit

    proc = lower_with_state_ranges(SOURCE, file="behavior.py")
    env = ranges.fixpoint(proc, {n: ranges.Range(0, 255) for n in proc.inputs})
    state = {name: 0 for name in proc.state}
    run = residual.unroll(proc, env, steps=1)
    unknown = "no:such:goal"

    probe = local_hit(proc, env, state, unknown, timeout_ms=5_000)
    assert probe.outcome is probing.Outcome.UNSUPPORTED

    verdict, _why = residual.decide_one(run, proc, unknown, 5_000)
    assert verdict != "unsat"

    _found, ruled, open_, calls = residual.decide_open(
        run, proc, [unknown], 5_000)
    assert unknown not in ruled and unknown in open_
    assert calls == (), "unsupported means no solver call was posed"

    decided = residual.decide_goals(proc, env, [unknown], steps=1, timeout_ms=5_000)
    assert unknown not in decided.unreachable, \
        "the public entry point reported an unencoded goal unreachable"
    assert unknown in decided.undecided

    # The pool path already had the right shape and is here so it keeps it: `unreachable`
    # is populated only by a query over every path, so a pool leaves it empty and
    # everything it did not cover is undecided.
    pooled = Classification([unknown], covered=set())
    assert unknown not in pooled.unreachable
    assert unknown in pooled.undecided


def test_a_batch_does_not_settle_goals_it_could_not_ask_about(tmp_path):
    """`decide_open` asks about a whole set at once, and that is where an unencoded goal
    is most dangerous: it contributes `False` to the disjunction, so one `unsat` would
    rule out every goal in the batch including the ones nobody asked about."""
    from behave.sim.behavior import residual
    from behave.sim.symbolic import ranges
    from behave.sim.behavior.lowering import lower_with_state_ranges

    proc = lower_with_state_ranges(SOURCE, file="behavior.py")
    env = ranges.fixpoint(proc, {n: ranges.Range(0, 255) for n in proc.inputs})
    run = residual.unroll(proc, env, steps=1)
    real = sorted(run.goals)[:2]

    found, ruled, open_, calls = residual.decide_open(
        run, proc, [*real, "no:such:goal"], 5_000)
    assert "no:such:goal" not in ruled, "an unasked goal was ruled unreachable"
    assert "no:such:goal" not in found
    assert "no:such:goal" in open_
    assert "no query was posed" in open_["no:such:goal"]
    assert all("no:such:goal" not in call.goals for call in calls)
