"""Why a step failed, as a class rather than as a formatted message.

Three sites raised `RuntimeError` with a string, a caller caught `RuntimeError` and stored
the string, and anything wanting to know *which kind* of failure it was had to match on
prose. So these were one bucket:

* the solver gave up (`unknown`) -- a budget symptom, says nothing about the design;
* the step is unsatisfiable (`unsat`) -- not a hard problem at all, an inconsistency in
  what was pinned, usually a state the relation does not admit;
* the handshake never opened -- no solver involved, every query satisfiable.

And the message said "model too complex for z3" for the first two, which is right for one
of them. A fourth class had no name at all: Phase 1 running out of wall clock, which is a
failure of neither the design nor the solver and which a bare "produced no witnesses" does
not distinguish from either.

That conflation is how a 46% and a 60% taken with two different instruments came to be put
in the same table and bisected. The classes exist so that a comparison compares one thing.
"""
import pytest

from behave.sim.core.refusal import Refusal, Refused

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver


# ── the exception carries a value, not a sentence ──

def test_refused_carries_a_refusal_and_not_a_string():
    got = Refused(Refusal("some-kind", "the reason"))
    assert isinstance(got, RuntimeError), \
        "existing `except RuntimeError` handlers have to keep working"
    assert got.refusal.kind == "some-kind"
    assert "some-kind" in str(got) and "the reason" in str(got)


def test_refused_will_not_take_a_bare_message():
    """The whole point is that the kind is something a counter can group by."""
    with pytest.raises(TypeError, match="takes a Refusal"):
        Refused("the solver gave up")


# ── unsat and unknown are different failures ──

# ── and the search records the class it was handed ──
