"""The queue and the novelty policy, and what a novelty claim is relative to.

G2's second clause: *neither explorer keeps its own queue, admission rule or novelty
policy*. They did. The behavior side keyed novelty on `(hash(freeze(model.__dict__)),
remaining)`; the RTL side had **no novelty test at all** -- its pool was filled by Phase
1 and never extended, a successful flip's post-states were dropped, and `classify` got a
flat `novel_state=False` because there was nowhere to say "there is no frontier".

Two things are tested here and they are different in kind.

The **mechanism** is shared: one key, one novelty rule, one queue with a priority end.
The **admission** is not, and must not be: G3 says the two admissions are independent,
and the behavior side pools every Phase 1 state whether or not the position repeats.
Folding both into one `bank` would have dropped repeated Phase 1 positions out of the
pool -- a change to what the search probes from, arriving disguised as a refactor. So
`mark` and `push` are separate and `bank` is the composition, and what is checked is
that each caller's composition is the one it had.

And a `Basis` because "a novel state" is relative to what counts as the state. On the
RTL side that is a three-branch rule; only the first branch is "the registers that are
not ports". The third is combinational wires, under which every input is a new position
and a novelty test admits everything **while looking like a frontier that grows**.
Measured over the population: branch one on 798 of 799 designs the explorer can build,
branch two on none, branch three on one.
"""

import pytest

from behave.sim.core.refusal import Refusal
from behave.sim.engines.probing import Hit, Outcome, ProbeResult, Scope, Tally


# ── what a novelty claim is relative to ──

# ── the queue ──

# ── the two admissions, which are not the same admission ──

# ── and the tally will not read an absent answer as an answer ──

def sat():
    return ProbeResult(Outcome.SAT, Scope.RTL_LOCAL_HIT, goal="g",
                       candidate={"a": 1})


def test_an_unavailable_novelty_is_counted_not_assumed():
    t = Tally("test")
    t.record(sat())
    got = t.classify(target_hit=False, other_hit=False, primary=True,
                     novel_state=Refusal("no-state-basis", "combinational"))
    assert got is Hit.REJECTED, "still exactly one class, so the identity holds"
    assert t.refusals == {"no-state-basis": 1}
    t.check()
    assert t.as_dict()["refusals"] == {"no-state-basis": 1}
    assert t.as_dict()["refusal_why"]["no-state-basis"] == "combinational"


def test_a_real_novel_state_is_not_counted_as_a_refusal():
    t = Tally("test")
    t.record(sat())
    assert t.classify(target_hit=False, other_hit=False, novel_state=True,
                      primary=True) is Hit.NOVEL_STATE
    assert t.refusals == {}
    t.check()


def test_novel_state_false_and_refused_land_in_the_same_class_but_read_apart():
    """They must land in the same class -- the identity is over every satisfiable probe
    and does not care why a flag was unset -- and they must still be tellable apart,
    which is the whole point of counting refusals."""
    t = Tally("test")
    for novel in (False, Refusal("no-state-basis", "combinational")):
        t.record(sat())
        t.classify(target_hit=False, other_hit=False, novel_state=novel,
                   primary=True)
    t.check()
    assert t.hits[Hit.REJECTED] == 2
    assert t.refusals == {"no-state-basis": 1}


def test_a_new_kind_of_refusal_needs_no_change_to_the_tally():
    """The reuse test, and the reason this is a type rather than another counter.
    Adding `Outcome.OUTSIDE_DOMAIN` cost seven edits inside `Tally`. A provider todo3
    has not been written yet must be able to refuse without touching this file."""
    t = Tally("test")
    t.refused(Refusal("provider-that-does-not-exist-yet", "invented in this test"))
    t.refused(Refusal("provider-that-does-not-exist-yet", "invented in this test"))
    t.refused(Refusal("some-other-kind", "a different reason"))
    t.check()
    assert t.as_dict()["refusals"] == {"provider-that-does-not-exist-yet": 2,
                                       "some-other-kind": 1}
    assert set(t.as_dict()["refusal_why"]) == {"provider-that-does-not-exist-yet",
                                               "some-other-kind"}


def test_a_refusal_must_carry_a_reason_to_be_counted():
    """A count with no reason is a number nobody can act on, which is what
    `novelty_unavailable` was."""
    t = Tally("test")
    with pytest.raises(TypeError, match="takes a Refusal"):
        t.refused("no-state-basis")
    with pytest.raises(ValueError, match="must say why"):
        Refusal("some-kind", "")


# ── the RTL explorer declares its basis rather than assuming one ──
