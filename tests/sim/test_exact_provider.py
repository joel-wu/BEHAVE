"""G4's acceptance, which the item says to build before the provider.

Of everything in section 4B this is the only test that validates a **provider**
rather than the plumbing around one, it needs none of G6 to G8, and the same harness
serves the RTL provider afterwards.

    on small-width models, enumerate states and inputs exhaustively and require the
    provider's SAT and the concrete hit to agree **in both directions**

Both directions, because each catches a different defect and only one of them is a
soundness fault.

*SAT must be honest.* A candidate the provider returns has to reach the goal when it
is actually run. A provider that returns a candidate the model does not follow is one
whose encoding disagrees with the interpreter, and the search would spend its budget
replaying answers that buy nothing -- which G1 measured the path-prefix probe doing
98.1% of the time on new goals.

*UNSAT must be exhaustive.* If the provider says no input reaches the goal from this
state, then **no** input in the enumerated space may reach it. This is the direction
that would be a defect rather than an inefficiency: it is a claim over every input,
and the whole reason `Scope.EXACT_LOCAL_HIT` is a stronger scope than
`Scope.PATH_PREFIX` is that it is entitled to make it. Getting it wrong would put a
false claim behind the stronger label.

Exhaustive enumeration is what makes this an acceptance rather than a spot check, and
it is affordable only on small widths -- which is why the models here are written with
two- and three-bit inputs rather than borrowed from the population.
"""
import itertools

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import interp, ir, slicing
from behave.sim.engines import local_ir as exact
from behave.sim.engines import probing

# ── the models, written small so that "every input" is a loop ──

#: A branch on the input and a branch on the state, so a goal exists that no input
#: reaches from some states and every input reaches from others. That asymmetry is
#: what the UNSAT direction is about.
GATED = ('class M:\n'
         '    def reset(self):\n'
         '        self.armed = 0\n'
         '    def process(self, a):\n'
         '        y = 0\n'
         '        if self.armed:\n'
         '            if a & 1:\n'
         '                y = 1\n'
         '        if a > 5:\n'
         '            self.armed = 1\n'
         '        return {"y": y}\n')

#: A failure, so that the goal reporting it is reachable only for one input value and
#: the run ends there.
DIVIDES = ('class M:\n'
           '    def reset(self):\n'
           '        self.n = 0\n'
           '    def process(self, a):\n'
           '        y = 8 // a\n'
           '        self.n = (self.n + 1) & 3\n'
           '        return {"y": y}\n')

#: An array in the state, since pinning one means pinning its cells.
TABLE = ('class M:\n'
         '    def reset(self):\n'
         '        self.t = [0, 0, 0, 0]\n'
         '    def process(self, a):\n'
         '        i = a & 3\n'
         '        y = 0\n'
         '        if self.t[i]:\n'
         '            y = 1\n'
         '        self.t[i] = 1\n'
         '        return {"y": y}\n')

#: A finite loop whose entered/not-entered event is controlled by one input bit.
#: It closes the loop-activation row in the same exhaustive provider harness instead
#: of treating loop guidance as a declaration-only special case.
LOOP = ('class M:\n'
        '    def reset(self):\n'
        '        self.n = 0\n'
        '    def process(self, a):\n'
        '        y = 0\n'
        '        for i in range(a & 1):\n'
        '            y = y + 1\n'
        '        self.n = y\n'
        '        return {"y": y}\n')

#: One source branch copied to two IR sites by inlining.  From ``armed == 1``
#: the first copy is unreachable but the second is reachable; from ``armed ==
#: 0`` both are unreachable.  This distinguishes "one occurrence is UNSAT"
#: from "the public goal is locally UNSAT".
REPEATED = ('def helper(v, armed):\n'
            '    if armed and v > 3:\n'
            '        return 1\n'
            '    return 0\n'
            '\n'
            'class M:\n'
            '    def reset(self):\n'
            '        self.armed = 0\n'
            '    def process(self, a):\n'
            '        p = helper(a & 1, self.armed)\n'
            '        q = helper(a, self.armed)\n'
            '        self.armed = a & 1\n'
            '        return {"y": p + q}\n')

WIDTH = 3          # inputs enumerated over 0 .. 7


def build(source, *, bound=1):
    """Lower through the entry point the pipeline uses, not around it.

    The first version called `lower_with_state_ranges` and then `ranges.fixpoint`
    directly, and the array model failed with "t has no range": `fixpoint` gives an
    array state variable none, and what supplies it is `instance.lower`, which reads
    the initial state out of `reset` and hands the ranges on as `proc.state_ranges`.
    A provider tested against an environment the pipeline never builds is tested
    against a different program.
    """
    from behave.sim.behavior import instance as bi
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    schema = TransactionSchema([Endpoint("a", "input", WIDTH),
                                Endpoint("y", "output", 8)],
                               source="declared by the test",
                               authority=Authority.TASK,
                               reading=Reading.ELABORATED)
    inst = bi.lower(source, schema, bound=bound)
    return inst.proc, inst.env


def states_of(proc, env):
    """Every state the enumeration covers, as concrete snapshots.

    Not "every reachable state": this is a *local* provider and the question it
    answers is about the state it is handed, so the enumeration hands it states
    including ones no run reaches. That is deliberate -- a provider that is only
    correct on reachable states would be correct by accident, since nothing tells it
    which it was given.
    """
    spaces = []
    for name in proc.state:
        shape = proc.types.get(name)
        cells = getattr(shape, "cells", None)
        rng = env.get(name)
        assert rng is not None, name
        span = list(range(rng.lo, min(rng.hi, 3) + 1))
        if cells:
            spaces.append([list(combo)
                           for combo in itertools.product(span, repeat=cells)])
        else:
            spaces.append(span)
    for combo in itertools.product(*spaces):
        yield dict(zip(proc.state, combo))


def reaches(proc, state, goal, value):
    """Whether running one step from ``state`` on input ``value`` reaches ``goal``.

    The oracle, and it is the interpreter rather than a second encoding: two
    encodings that have to agree are two things that can both be wrong in the same
    way, and what a certificate is measured against is what running the model does.
    """
    _s, _o, hits, _e = interp.run_trace(proc, [{"a": value}], dict(state))
    return goal in hits


def every_input():
    return range(1 << WIDTH)


# ── the acceptance ──

#: Whether this model has a goal that some state blocks, so that the provider must
#: produce a `LOCAL_UNSAT` on it. `divides` does not: its safety goal fires on `a == 0`
#: and that input is admissible from **every** state, so no snapshot blocks it. That is
#: a fact about the model rather than a gap in the harness, and stating it is what keeps
#: "both answers must appear" from being either a false alarm or quietly dropped.
BLOCKS_SOMETHING = {
    "gated": True,
    "divides": False,
    "table": True,
    "loop": False,
}


@pytest.mark.parametrize("source,name", [
    (GATED, "gated"),
    (DIVIDES, "divides"),
    (TABLE, "table"),
    (LOOP, "loop"),
])
def test_the_provider_and_the_concrete_run_agree_in_both_directions(source, name):
    proc, env = build(source)
    goals = sorted(ir.goals_of(proc.body))
    assert goals, name

    checked = sat_cases = unsat_cases = 0
    polarities = {goal: set() for goal in goals}
    for state in states_of(proc, env):
        truth = {g: {v for v in every_input() if reaches(proc, state, g, v)}
                 for g in goals}
        for goal in goals:
            if truth[goal]:
                polarities[goal].add(True)
            if len(truth[goal]) != 1 << WIDTH:
                polarities[goal].add(False)
            got = exact.local_hit(proc, env, state, goal, timeout_ms=20_000)
            assert got.scope is probing.Scope.EXACT_LOCAL_HIT
            if got.outcome is probing.Outcome.UNKNOWN:
                continue
            assert got.outcome is not probing.Outcome.UNSUPPORTED, \
                (name, goal, got.detail)
            checked += 1
            if got.outcome is probing.Outcome.SAT:
                sat_cases += 1
                value = got.candidate["a"]
                # SAT is honest: the candidate it named reaches the goal when run.
                assert reaches(proc, state, goal, value), \
                    (name, goal, state, value, "SAT but the run does not reach it")
                assert truth[goal], (name, goal, state,
                                     "SAT where no input reaches it at all")
            else:
                unsat_cases += 1
                # UNSAT is exhaustive: *no* input reaches it from here. This is the
                # direction that would be a defect rather than a slow query.
                assert not truth[goal], \
                    (name, goal, state,
                     f"LOCAL_UNSAT but these inputs reach it: {sorted(truth[goal])}")

    assert checked, name
    assert sat_cases, f"{name}: no satisfiable case, so nothing tested the SAT side"
    if name == "loop":
        loop_polarities = {
            goal: values for goal, values in polarities.items()
            if goal.startswith("branch:")
        }
        assert all(values == {False, True}
                   for values in loop_polarities.values()), \
            (name, "the loop fixture needs positive and negative concrete cells",
             loop_polarities)
    if BLOCKS_SOMETHING[name]:
        assert unsat_cases, \
            (f"{name}: {sat_cases} sat and 0 unsat, and this model has a goal some "
             f"state blocks -- so the exhaustive direction went untested")
    else:
        assert unsat_cases == 0, \
            (f"{name}: {unsat_cases} unsat, but every goal of this model is "
             f"reachable from every state, so an UNSAT here is the provider "
             f"over-claiming")


def test_the_harness_catches_a_provider_that_is_wrong_in_each_direction():
    """The acceptance is worth what it would reject, so it is checked against two
    deliberately broken providers -- one that over-claims SAT and one that
    over-claims UNSAT. A harness nobody has seen fail is a harness of unknown
    strength."""
    # Two steps make both values of ``armed`` part of the finite state carrier.
    # Select by the semantic property this test needs rather than by a source line.
    proc, env = build(GATED, bound=2)
    goal = next(
        goal for goal in sorted(ir.goals_of(proc.body))
        if any(any(reaches(proc, state, goal, value)
                   for value in every_input())
               for state in states_of(proc, env))
        and any(not any(reaches(proc, state, goal, value)
                        for value in every_input())
                for state in states_of(proc, env)))

    # Somewhere the goal is reachable, and somewhere it is not.
    reachable = next(s for s in states_of(proc, env)
                     if any(reaches(proc, s, goal, v) for v in every_input()))
    blocked = next(s for s in states_of(proc, env)
                   if not any(reaches(proc, s, goal, v) for v in every_input()))

    def always_sat(*_a, **_k):
        return probing.ProbeResult(probing.Outcome.SAT,
                                   probing.Scope.EXACT_LOCAL_HIT, goal=goal,
                                   candidate={"a": 0})

    def always_unsat(*_a, **_k):
        return probing.ProbeResult(probing.Outcome.LOCAL_UNSAT,
                                   probing.Scope.EXACT_LOCAL_HIT, goal=goal)

    # Over-claiming SAT: from a state where nothing reaches the goal, a candidate
    # is named and the run does not follow it.
    got = always_sat()
    assert not reaches(proc, blocked, goal, got.candidate["a"]), \
        "the stub has to actually be wrong for this to test the check"

    # Over-claiming UNSAT: from a state where some input reaches the goal, the
    # provider says none does. This is the soundness direction.
    assert always_unsat().outcome is probing.Outcome.LOCAL_UNSAT
    assert any(reaches(proc, reachable, goal, v) for v in every_input())

    # And the real provider gets both of those right.
    assert exact.local_hit(proc, env, reachable, goal,
                           timeout_ms=20_000).outcome is probing.Outcome.SAT
    assert exact.local_hit(proc, env, blocked, goal,
                           timeout_ms=20_000).outcome \
        is probing.Outcome.LOCAL_UNSAT


def test_an_exact_unsat_is_still_not_a_verdict():
    """G0, on the strongest probe scope there is. `EXACT_LOCAL_HIT` rules out every
    input **at this snapshot**, which is more than the path-prefix probe can say and
    still not unreachability: another state may reach the goal. The stronger label
    does not come with authority."""
    proc, env = build(GATED)
    goal = next(g for g in sorted(ir.goals_of(proc.body)) if g.endswith(":T"))
    blocked = next(s for s in states_of(proc, env)
                   if not any(reaches(proc, s, goal, v) for v in every_input()))

    got = exact.local_hit(proc, env, blocked, goal, timeout_ms=20_000)
    assert got.outcome is probing.Outcome.LOCAL_UNSAT
    assert not hasattr(got, "carries_authority")
    assert not hasattr(got, "as_goal_status")

    # And the goal is reachable from somewhere, so the UNSAT above is local in a way
    # that matters rather than vacuously.
    assert any(any(reaches(proc, s, goal, v) for v in every_input())
               for s in states_of(proc, env))


def test_generation_searches_copied_goal_occurrences_without_weakening_unsat(
        monkeypatch):
    """SAT needs one copied site; LOCAL_UNSAT needs every copied site.

    The first helper call cannot take its true branch for any three-bit input,
    while the second can.  An all-sites formula is correct but unnecessarily
    large; stopping after the first site's UNSAT would be incorrect.
    """

    proc, env = build(REPEATED)
    events = slicing.linearize(proc.body)
    states = list(states_of(proc, env))
    duplicated = [
        goal for goal in sorted(ir.goals_of(proc.body))
        if sum(isinstance(command, (ir.Mark, ir.Fail))
               and command.goal == goal for command, _under in events) == 2
    ]
    goal = next(
        goal for goal in duplicated
        if any(any(reaches(proc, state, goal, value) for value in every_input())
               for state in states)
        and any(not any(reaches(proc, state, goal, value)
                        for value in every_input())
                for state in states))
    reachable_state = next(
        state for state in states
        if any(reaches(proc, state, goal, value) for value in every_input()))
    blocked_state = next(
        state for state in states
        if not any(reaches(proc, state, goal, value) for value in every_input()))

    reachable = exact.local_hit(
        proc, env, reachable_state, goal, timeout_ms=20_000)
    assert reachable.outcome is probing.Outcome.SAT
    assert reaches(proc, reachable_state, goal, reachable.candidate["a"])

    blocked = exact.local_hit(
        proc, env, blocked_state, goal, timeout_ms=20_000)
    assert blocked.outcome is probing.Outcome.LOCAL_UNSAT

    # Losing one occurrence cannot be reinterpreted as proving the source goal
    # unreachable, even when every occurrence that was encoded is UNSAT.
    # The query preparation serves each occurrence cone and numbers sites in
    # its own procedure's events, not in ``proc.body``.
    real = exact._LocalQueryPreparation.cone

    def misses_one(preparation, goal_, site):
        first = next(index for index, (command, _under) in enumerate(preparation.events())
                     if isinstance(command, (ir.Mark, ir.Fail))
                     and command.goal == goal_)
        if goal_ == goal and site == first:
            raise exact.EncodingError("missing occurrence", kind="goal")
        return real(preparation, goal_, site)

    monkeypatch.setattr(exact._LocalQueryPreparation, "cone", misses_one)
    incomplete = exact.local_hit(
        proc, env, blocked_state, goal, timeout_ms=20_000)
    assert incomplete.outcome is probing.Outcome.UNSUPPORTED
    assert incomplete.outcome is not probing.Outcome.LOCAL_UNSAT
    assert "missing occurrence" in incomplete.detail


def test_a_state_the_snapshot_does_not_mention_is_refused():
    """A state variable left free turns "does an input reach this from here" into
    "does some state reach it", which has more answers -- so the provider would name
    a candidate no run can present. Refused rather than encoded."""
    proc, env = build(GATED)
    goal = min(ir.goals_of(proc.body))
    got = exact.local_hit(proc, env, {}, goal, timeout_ms=5_000)
    assert got.outcome is probing.Outcome.UNSUPPORTED
    assert "armed" in got.detail


def test_an_array_state_goes_in_as_its_cells():
    """Compact array pinning preserves every concrete cell and its query witness."""
    from behave.sim.symbolic.functional_array import FunctionalArray

    proc, env = build(TABLE)
    state = {"t": [0, 1, 0, 0]}
    pinned = exact.pin_state(proc, env, state)
    array = pinned["t"]
    assert isinstance(array, FunctionalArray) and array.length == 4
    assert [z3.simplify(array.select(z3.BitVecVal(i, 3))).as_long()
            for i in range(array.length)] == [0, 1, 0, 0]

    # And the provider gives an answer that the run agrees with: cell 1 is set, so
    # the goal that fires on a non-zero cell is reachable with an index of 1.
    goal = next(g for g in sorted(ir.goals_of(proc.body)) if g.endswith(":T"))
    got = exact.local_hit(proc, env, state, goal, timeout_ms=20_000)
    assert got.outcome is probing.Outcome.SAT
    assert reaches(proc, state, goal, got.candidate["a"])
