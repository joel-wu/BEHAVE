"""Ranges indexed by the bound, rather than an interval closed for all time.

    R(0) = Init      R(t+1) = post#(R(t))      for t < N

joined over the iterates, because one width has to hold every step of the unrolling.
No fixpoint, nothing declared settled, no divergence limit: nothing is claimed about step
N + 1, and the verification never asks about it.

The fixpoint answers a harder question than the verification asks, and the difference is
coverage rather than elegance. Over the population, at N = 10, the fixpoint gives a range
to 433 of 614 state variables and this gives one to 543, with 67 models gaining and none
losing; only 5 models have a state variable whose starting value could not be read, which
is what this needs and the fixpoint does not.

Soundness was checked against runs and not only against the argument: 246 models, 71250
state readings over 10 steps by 12 random stimuli each, none outside the interval.
"""
import random

import pytest

from behave.sim.symbolic import ranges
from behave.sim.symbolic.interp import run_step
from behave.sim.behavior.lowering import lower_procedure, plan_state

COUNTER = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = self.x + 1
        return {"y": self.x}
"""

MASKED = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = (self.x + a) & 15
        return {"y": self.x}
"""

NO_START = """class M:
    def reset(self):
        pass

    def process(self, a):
        self.x = self.x + 1
        return {"y": self.x}
"""


def _schema():
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    return TransactionSchema([Endpoint("a", "input", 8),
                              Endpoint("y", "output", 16)],
                             source="declared by the test",
                             authority=Authority.TASK,
                             reading=Reading.ELABORATED)


def test_a_counter_is_unbounded_for_all_time_and_bounded_over_n_steps():
    """The coverage this buys, in one model. `self.x = self.x + 1` has no interval
    closed under the step, so the fixpoint leaves it out and everything computed from
    it loses its range too."""
    assert "x" not in plan_state(COUNTER, input_widths=[8]).env
    assert plan_state(COUNTER, input_widths=[8], bound=10).env["x"] == ranges.Range(0, 10)


def test_a_finite_range_record_need_not_claim_an_extra_step_of_closure():
    from behave.sim.behavior import instance as behavior_instance

    inst = behavior_instance.lower(COUNTER, _schema(), bound=2)
    assert inst.proc.state_range_bound == 2
    assert inst.well_formed() == []

    forged = behavior_instance.BehaviorInstance(
        inst.proc, inst.env, source=COUNTER, schema=_schema(), bound=3)
    assert any("were not derived for this finite bound" in reason
               for reason in forged.well_formed())


@pytest.mark.parametrize("bound", [0, 1, 2, 10, 64])
def test_the_counter_reaches_exactly_the_bound(bound):
    """Not a bound-shaped over-approximation: R(t) is exact for this step, so the
    interval is [0, N] and the test would catch an off-by-one in either direction."""
    assert plan_state(COUNTER, input_widths=[8], bound=bound).env["x"] \
        == ranges.Range(0, bound)


def test_state_planning_accelerates_a_proved_additive_recurrence(monkeypatch):
    """A finite counter envelope does not require one whole-program pass per slot."""

    proc = lower_procedure(COUNTER, input_widths=[8])
    original = ranges._procedure_post_with_graph
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ranges, "_procedure_post_with_graph", counted)
    got = ranges.bounded(
        proc, {"a": ranges.Range(0, 255)}, 64,
        _state_planning=True)
    assert got["x"] == ranges.Range(0, 64)
    assert calls <= 3


def test_additive_acceleration_contains_the_finite_iterates_with_resets():
    source = """class M:
    def reset(self):
        self.x = 4

    def process(self, a):
        if a & 1:
            self.x = self.x + 2
        else:
            self.x = 1
        if a & 2:
            self.x = self.x - 3
        return {"y": self.x}
"""
    proc = lower_procedure(source, input_widths=[2])
    inputs = {"a": ranges.Range(0, 3)}
    iterated = ranges.bounded(proc, inputs, 7)
    accelerated = ranges.bounded(
        proc, inputs, 7, _state_planning=True)
    assert accelerated["x"].lo <= iterated["x"].lo
    assert iterated["x"].hi <= accelerated["x"].hi


def test_a_bound_of_zero_is_the_starting_state():
    """R(0) = Init and nothing has run."""
    assert plan_state(COUNTER, input_widths=[8], bound=0).env["x"] == ranges.Range(0, 0)


def test_a_variable_whose_start_could_not_be_read_gets_no_range():
    """R(0) is Init, so a variable with no Init has no R(0) and no join.  The total
    result marks it unknown, and the known-only encoder projection omits it rather than
    assuming zero:
    zero feeds the width and every loop bound naming it, and a run starting elsewhere
    is a run those widths do not cover."""
    proc = lower_procedure(COUNTER, input_widths=[8])
    proc.initial.pop("x")
    assert "x" not in ranges.bounded(proc, {"a": ranges.Range(0, 255)}, 10)


def test_an_absent_source_attribute_is_not_its_physical_payload():
    """An explicit existence bit keeps an inactive carrier out of source reads."""
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
    from behave.sim.behavior.lower_graph import GraphLowerer
    from behave.sim.symbolic.state_projection import ABSENT_STATE

    namespace = {}
    exec(compile(NO_START, "behavior.py", "exec"), namespace)
    for lowerer in (_Lowerer, GraphLowerer):
        proc = lower_with_state_ranges(NO_START, input_widths=[8], bound=10,
                                       lowerer=lowerer)
        assert proc.contract.projection("x").reconstruct(proc.initial) is ABSENT_STATE
        assert run_step(proc, {"a": 0})[-1] == "AttributeError"
        assert audit.model_audit(proc, namespace["M"], [[{"a": 0}]]) == []


def test_where_both_answer_the_bounded_range_is_no_wider():
    """A loop bound resolved from these unrolls no further than one resolved from the
    fixpoint, and no less far, so nothing that lowered stops lowering."""
    wide = plan_state(MASKED, input_widths=[8]).env
    tight = plan_state(MASKED, input_widths=[8], bound=10).env
    both = set(wide) & set(tight)
    assert both
    for name in both:
        assert tight[name].lo >= wide[name].lo, name
        assert tight[name].hi <= wide[name].hi, name


def test_a_masked_variable_agrees_with_the_fixpoint():
    """Where an interval really is closed under the step, both answers are the same
    one, so this is not a different analysis wearing the same name."""
    assert plan_state(MASKED, input_widths=[8], bound=10).env["x"] \
        == plan_state(MASKED, input_widths=[8]).env["x"]


def test_a_negative_bound_is_refused_rather_than_treated_as_zero():
    proc = lower_procedure(COUNTER, input_widths=[8])
    with pytest.raises(ValueError):
        ranges.bounded(proc, {}, -1)


@pytest.mark.parametrize("bound", [3, 10])
def test_a_run_of_n_steps_stays_inside(bound):
    """The claim, against runs. Every state value at every step of every trial is
    inside the interval, which is what a width taken from it needs."""
    proc = lower_procedure(COUNTER, input_widths=[8],
                           state_ranges=plan_state(COUNTER, input_widths=[8],
                                                   bound=bound).env)
    interval = plan_state(COUNTER, input_widths=[8], bound=bound).env["x"]
    rng = random.Random(7)
    for _trial in range(8):
        state = None
        for _step in range(bound):
            state, _out, *_ = run_step(proc, {"a": rng.randint(0, 255)}, state)
            assert interval.lo <= state["x"] <= interval.hi


def test_an_instance_will_not_bind_to_a_domain_of_another_bound():
    """The premise, checked where it is used.

    Ranges from `bounded(N)` cover runs of N steps and say nothing about step N + 1, so
    binding them to a domain of a different bound leaves every width -- and therefore
    every unreachable verdict -- a statement about a trace set the analysis was never
    about. Nothing downstream could detect it: the widths would simply be too narrow
    and the formula satisfiable in fewer places. The same fault B12 exists to prevent,
    one stage earlier.
    """
    from behave.sim.behavior import instance as bi

    schema = _schema()
    inst = bi.lower(COUNTER, schema, bound=10)
    assert inst.bound == 10
    inst.bind(10)                                   # the one it was lowered for
    with pytest.raises(ValueError) as caught:
        inst.bind(11)
    assert "10 steps" in str(caught.value)


def test_an_instance_from_the_fixpoint_binds_at_any_bound():
    """The fixpoint over-approximates every reachable state, so it is sound at every
    bound. That is why it stays as the fallback rather than being removed -- a caller
    that does not know the bound yet still gets an answer it can use at any of them."""
    from behave.sim.behavior import instance as bi

    schema = _schema()
    inst = bi.lower(MASKED, schema)
    assert inst.bound is None
    inst.bind(3)
    inst.bind(64)


def test_the_plan_says_how_many_steps_it_is_about():
    """A range is a property of a bounded instance, not of the step, so a reader can
    ask what it is a range over. `None` is the fixpoint, which is the fallback where no
    bound is known and not the intent."""
    assert plan_state(COUNTER, input_widths=[8], bound=10).bound == 10
    assert plan_state(COUNTER, input_widths=[8]).bound is None


# ── the bound a query may ask about ──

GROWS = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = self.x + 1
        y = 0
        if self.x == 5:
            y = 1
        return {"y": y}
"""


def _grows(bound):
    from behave.sim.behavior import instance as bi
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    schema = TransactionSchema([Endpoint("a", "input", 8),
                                Endpoint("y", "output", 8)],
                               source="declared by the test",
                               authority=Authority.TASK,
                               reading=Reading.ELABORATED)
    return bi.lower(GROWS, schema, bound=bound)


def test_a_query_past_the_bound_the_ranges_cover_is_refused():
    """A soundness fault, found by asking what `query` checks that `bind` does.

    `ranges.bounded(N)` is R(0) = Init and R(t+1) = post#(R(t)) for t < N, so it
    covers runs of N steps and says nothing about step N + 1. The widths follow from
    it, and a width that is too narrow does not fail loudly -- the formula wraps
    where the program does not, and a goal the program reaches becomes
    unsatisfiable.

    Measured before the fix, on this model: lowered at a bound of 1 the range for
    `x` is [0,1], two bits, and `query(steps=20)` returned the goal at `x == 5`
    **unreachable**; lowered at 20 the range is [0,20], six bits, and the same query
    returned it reachable. `bind` refused a domain of the wrong bound and `query`
    did not, so the entry point that produces verdicts was the unguarded one.
    """
    import pytest

    from behave.sim.symbolic import ir, ranges

    narrow = _grows(1)
    assert narrow.env["x"] == ranges.Range(0, 1)
    assert ranges.encoding_width(narrow.env["x"]) == 2, \
        "two bits, in which 5 is not representable"

    goal = next(g for g in sorted(ir.goals_of(narrow.proc.body))
                if ":8:" in g and g.endswith(":T"))
    with pytest.raises(ValueError) as caught:
        narrow.query([goal], steps=20, timeout_ms=20_000)
    assert "20" in str(caught.value) and "1" in str(caught.value)

    with pytest.raises(ValueError):
        narrow.feasible(20)


def test_a_query_at_or_below_the_bound_is_allowed():
    """R(t) grows with t, so ranges covering N steps cover every shorter run. Only
    the other direction is unsound, and refusing both would cost coverage for
    nothing."""
    wide = _grows(20)
    from behave.sim.symbolic import ir, ranges

    assert wide.env["x"] == ranges.Range(0, 20)
    goal = next(g for g in sorted(ir.goals_of(wide.proc.body))
                if ":8:" in g and g.endswith(":T"))

    got = wide.query([goal], steps=20, timeout_ms=60_000)
    assert goal in got.reachable, "x reaches 5 within twenty steps"

    fewer = wide.query([goal], steps=3, timeout_ms=60_000)
    assert goal in fewer.unreachable, \
        "and not within three, which is the verdict the bound makes meaningful"
