"""How far the frontend gets with a model, as one of four answers instead of a boolean.

M4. "Supported" was one boolean over a chain of quite different capabilities, and the
shape it produces is the problem: a model called admitted and well formed, which then
fails in the middle of building a formula, reads as a defect of the model rather than as
a limit of this side. A model that runs and instruments but does not bounded-encode is
not unsupported -- it still scores and still takes concolic testing.

Each test drives one gate and checks both halves of the answer: which tier, and which
gate stopped it. A tier without the gate that stopped it is the boolean again with more
names.
"""
import pytest

from behave.sim.behavior.tiers import TIERS, tier_of
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

PLAIN = """class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        if a > 3:
            self.n = (self.n + 1) & 255
        return {"y": self.n}
"""

#: `try` is not in the admitted grammar.
UNADMITTED = """class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        try:
            self.n = a
        except Exception:
            self.n = 0
        return {"y": self.n}
"""

#: Admitted, runnable and instrumentable.  Recursive helpers are inlined only up to
#: the helper activation limit, and an 8-bit argument can recurse far deeper, so
#: this model deliberately stops at lowering.
NOT_LOWERED = """class M:
    def reset(self):
        self.n = 0

    def descend(self, v):
        if v:
            return self.descend(v - 1)
        return 0

    def process(self, a):
        return {"y": self.descend(a)}
"""


def schema():
    # The task owns the type boundary; the candidate may only realize it.
    return TransactionSchema([Endpoint("a", "input", 8),
                              Endpoint("y", "output", 16)],
                             source="declared by the task",
                             authority=Authority.TASK,
                             reading=Reading.ELABORATED)


def test_a_plain_model_reaches_the_top():
    got = tier_of(PLAIN, schema(), bound=10)
    assert got.name == "bounded", got.as_dict()
    assert got.stopped_by is None


def test_an_unadmitted_construct_stops_at_admission():
    got = tier_of(UNADMITTED, schema(), bound=10)
    assert (got.name, got.stopped_by) == ("none", "admission")
    assert "Try" in got.detail


def test_a_model_that_does_not_lower_is_still_instrumented():
    """The distinction the boolean could not make. Its goals enumerate and a run
    reports them, so it scores and takes concolic testing; what it does not do is
    reach a solver."""
    got = tier_of(NOT_LOWERED, schema(), bound=10)
    assert (got.name, got.stopped_by) == ("instrumented", "lowering")
    assert got.detail


def test_every_answer_is_one_of_the_declared_tiers():
    for src in (PLAIN, UNADMITTED, NOT_LOWERED):
        assert tier_of(src, schema(), bound=10).name in TIERS


def test_the_tiers_are_ordered_so_a_reader_can_compare_them():
    high = tier_of(PLAIN, schema(), bound=10)
    low = tier_of(UNADMITTED, schema(), bound=10)
    assert high >= low
    assert high >= "bounded"
    assert not low >= "instrumented"


def test_a_model_that_cannot_be_constructed_stops_before_instrumentation():
    """A gate that cannot be asked is not skipped: whether its goals enumerate is not
    a fact about a model nobody can build."""
    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.n = 1 // 0\n"
           "    def process(self, a):\n"
           "        return {'y': a}\n")
    got = tier_of(src, schema(), bound=10)
    assert (got.name, got.stopped_by) == ("none", "construction")


@pytest.mark.parametrize("bound", [1, 10])
def test_the_bound_is_the_one_the_tier_is_about(bound):
    """A range is a property of a bounded instance, so a tier computed at one bound is
    a statement about that bound."""
    assert tier_of(PLAIN, schema(), bound=bound).name == "bounded"
