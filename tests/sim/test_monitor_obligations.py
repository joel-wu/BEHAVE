"""The resident monitor's obligations, one behaviour each.

Split out of `test_wave1_closure.py` by concern: stability under stall, deadlines,
correlation by order and by tag, what a horizon's end does and does not establish, and
what an unreadable payload withholds. Shared fixtures are imported from where they are
defined.
"""
import time

import pytest

from behave.sim import api
from behave.sim.core.contract import ResetPolicy
from behave.sim.core.monitor import Monitor
from behave.sim.core.refusal import Refusal, Refused
from tests.support.device_boundary import evaluate_device, run_device

from .test_api_path import U8, Deaf, Doubling, _evaluate, _live, _ref, contract, realization, schedule  # noqa: F401
from .test_wave1_closure import HOLD, _concrete, _repo, _top_of  # noqa: F401


def _watched(**kw):
    base = contract()
    return api.TaskContract(
        endpoints=[
            api.Endpoint("in", api.Direction.IN, base.endpoint("in").fields,
                         base.endpoint("in").adapter, clock="clk",
                         kind=api.Kind.TRANSACTION,
                         max_outstanding=kw.get("max_outstanding"),
                         reset_policy=kw.get("reset_policy"),
                         response_deadline=kw.get("response_deadline")),
            api.Endpoint("out", api.Direction.OUT, base.endpoint("out").fields,
                         base.endpoint("out").adapter, clock="clk",
                         kind=api.Kind.TRANSACTION,
                         answers_per_transaction=kw.get("answers", 1),
                         terminal_quiescence=kw.get("quiescent")),
        ],
        clocks=list(base.clocks))


class _Holding:
    """A device that offers an answer and, under stall, does something with it.

    ``on_stall`` is `hold` (correct), `drop` (withdraws `valid`), `mutate` (changes the
    payload), `unknown` (drives a value the testbench cannot read), or `x-then-drop`
    (unreadable payload first, then `valid` withdrawn).
    """

    kind = "device"
    identity = "doubling"

    def __init__(self, on_stall="hold"):
        self.on_stall = on_stall
        self.reset()

    def reset(self):
        self.held = None
        self.next_held = None
        # How many slots this offer has already been held for. The misbehaviours only
        # begin on the **second** stalled slot: withdrawing `valid` before ever
        # asserting it is a design that declines to offer, which the protocol permits,
        # and the obligation is about what happens to an offer already made.
        self.stalled = 0
        self.stalling_now = False

    def settle(self, presented, controls):
        taking = self.held is not None and controls.get("out_ready", 0)
        ready = 1 if (self.held is None or taking) else 0
        accepting = presented is not None and controls.get("in_valid", 0) and ready
        self.next_held = None if taking else self.held
        if accepting:
            self.next_held = ((presented[0] * 2) & 0xFF,)
        self.stalling_now = self.held is not None and not taking
        if self.held is None:
            return {"in_ready": ready, "out_valid": 0, "out_y": 0}
        if taking or self.on_stall == "hold":
            return {"in_ready": ready, "out_valid": 1, "out_y": self.held[0]}
        if self.on_stall == "unknown":
            # X on the data lines from the first stalled slot: the offer is asserted
            # and what it carries is unreadable.
            return {"in_ready": ready, "out_valid": 1, "out_y": None}
        if self.on_stall == "x-then-drop":
            # First stalled slot: offering, payload unreadable. After that: `valid`
            # withdrawn with the offer still untaken.
            return {"in_ready": ready,
                    "out_valid": 1 if self.stalled == 0 else 0,
                    "out_y": None}
        if self.stalled == 0:
            return {"in_ready": ready, "out_valid": 1, "out_y": self.held[0]}
        if self.on_stall == "drop":
            return {"in_ready": ready, "out_valid": 0, "out_y": self.held[0]}
        if self.on_stall == "mutate":
            return {"in_ready": ready, "out_valid": 1,
                    "out_y": (self.held[0] + 1) & 0xFF}
        raise AssertionError(f"no behaviour for on_stall={self.on_stall}")

    def edge(self):
        self.held = self.next_held
        self.stalled = self.stalled + 1 if self.stalling_now else 0


def _stalling(c, rows, drain=8):
    """A stimulus whose environment declines **two consecutive slots** in every three.

    Two and not one, deliberately. A single declined slot between two receptive ones
    means the offer the design begins is taken on the next slot, so no offer is ever
    held across a slot boundary and the stability obligation has nothing to observe --
    which is the same hole the population sweep had with `out_ready` high throughout,
    reached a different way. Holding is only visible over a stall the design has to
    survive.
    """
    base = api.reset_then(c, [(v,) for v in rows], drain=drain,
                          controls={"out_ready": 1}, source="stalling")
    return api.Stimulus(
        queues=dict(base.queues),
        slots=[api.EventSlot(
            offers=s.offers, ticks=s.ticks, reset=s.reset,
            controls={**dict(s.controls),
                      "out_ready": 0 if (i % 3 in (1, 2) and not s.reset) else 1})
               for i, s in enumerate(base.slots)],
        source="stalling")


class _Starving:
    """Accepts two, answers the first, and never answers the second."""

    kind = "device"
    identity = "doubling"

    def __init__(self):
        self.reset()

    def reset(self):
        self.taken = 0
        self.answered = False
        self.offering = None
        self.next_offering = None
        self.next_taken = 0
        self.next_answered = False

    def settle(self, presented, controls):
        taking = self.offering is not None and controls.get("out_ready", 0)
        ready = 1 if self.taken < 2 else 0
        accepting = presented is not None and controls.get("in_valid", 0) and ready
        self.next_taken = self.taken + (1 if accepting else 0)
        self.next_answered = self.answered or taking
        # It offers an answer only for the first transaction it ever took.
        holding = (self.taken >= 1 or accepting) and not (self.answered or taking)
        self.next_offering = ((presented[0] * 2) & 0xFF,) if holding else None
        if taking:
            self.next_offering = None
        return {"in_ready": ready,
                "out_valid": 1 if self.offering is not None else 0,
                "out_y": self.offering[0] if self.offering is not None else 0}

    def edge(self):
        self.offering = self.next_offering
        self.taken = self.next_taken
        self.answered = self.next_answered


class _TwiceThenNothing:
    """Answers its first transaction twice, and every one after it not at all."""

    kind = "device"
    identity = "doubling"

    def __init__(self):
        self.reset()

    def reset(self):
        self.owed = 0
        self.value = 0
        self.seen = 0
        self.next_owed = 0
        self.next_value = 0
        self.next_seen = 0

    def settle(self, presented, controls):
        taking = self.owed > 0 and controls.get("out_ready", 0)
        ready = 1
        accepting = presented is not None and controls.get("in_valid", 0)
        self.next_owed = self.owed - (1 if taking else 0)
        self.next_value = self.value
        self.next_seen = self.seen + (1 if accepting else 0)
        if accepting and self.seen == 0:
            self.next_owed = 2
            self.next_value = (presented[0] * 2) & 0xFF
        return {"in_ready": ready,
                "out_valid": 1 if self.owed > 0 else 0,
                "out_y": self.value if self.owed > 0 else 0}

    def edge(self):
        self.owed, self.value, self.seen = (
            self.next_owed, self.next_value, self.next_seen)


def _tagged(depth=2, **kw):
    """A contract whose transactions carry a tag, correlated by it."""
    return api.TaskContract(
        endpoints=[
            api.Endpoint("in", api.Direction.IN,
                         [api.Field("id", U8, "in_id"), api.Field("a", U8, "in_a")],
                         api.ready_valid("in", "in_valid", "in_ready"), clock="clk",
                         kind=api.Kind.TRANSACTION, max_outstanding=depth,
                         reset_policy=kw.get("reset_policy"),
                         response_deadline=kw.get("response_deadline")),
            api.Endpoint("out", api.Direction.OUT,
                         [api.Field("id", U8, "out_id"), api.Field("y", U8, "out_y")],
                         api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
                         kind=api.Kind.TRANSACTION, matching=api.Matching.IDENTIFIER,
                         identifier="id", answers_per_transaction=1,
                         terminal_quiescence=kw.get("quiescent")),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])


def _tagged_realization():
    ports = [api.Port("in_id", 8, driven_by="environment", clock="clk"),
             api.Port("in_a", 8, driven_by="environment", clock="clk"),
             api.Port("out_id", 8, driven_by="artifact", clock="clk"),
             api.Port("out_y", 8, driven_by="artifact", clock="clk"),
             api.Port("in_valid", 1, driven_by="environment"),
             api.Port("in_ready", 1, driven_by="artifact"),
             api.Port("out_valid", 1, driven_by="artifact"),
             api.Port("out_ready", 1, driven_by="environment"),
             api.Port("clk", 1, driven_by="environment"),
             api.Port("rst", 1, driven_by="environment")]
    return api.Realization("device", ports, identity="tagged",
                           channel_ports={"in": ["in_id", "in_a"],
                                          "out": ["out_id", "out_y"]})


class _Reordering:
    """Holds up to two transactions and answers the **newer** one first."""

    kind = "device"
    identity = "tagged"

    def __init__(self, depth=2, lag=0):
        self.depth = depth
        #: Slots of silence after each answer. With a lag, the transaction the design
        #: leaves until last waits measurably longer than the one it answers first --
        #: which is what makes an identifier-correlated deadline and an order-correlated
        #: one give different verdicts on one run.
        self.lag = lag
        self.reset()

    def reset(self):
        self.queue = []
        self.next_queue = []
        self.quiet = 0
        self.next_quiet = 0

    def settle(self, presented, controls):
        # It fills up before it answers anything, and then answers the **newest** first.
        # Offering as soon as it holds one would answer in arrival order whatever the
        # queue discipline, and then there is nothing for a correlation policy to
        # disagree about.
        # **Not sticky.** Latching it across slots made the reset slots -- where the
        # environment drives `in_valid` low -- put the device in flush mode before the
        # run had started, so it answered the first request the moment it arrived and
        # there was nothing to reorder.
        flush = bool(self.queue) and not controls.get("in_valid", 0)
        ripe = len(self.queue) >= self.depth or flush
        out = self.queue[-1] if (self.queue and ripe and not self.quiet) else None
        taking = out is not None and controls.get("out_ready", 0)
        ready = 1 if len(self.queue) < self.depth else 0
        accepting = presented is not None and controls.get("in_valid", 0) and ready
        self.next_queue = list(self.queue[:-1]) if taking else list(self.queue)
        self.next_quiet = self.lag if taking else max(0, self.quiet - 1)
        if accepting:
            self.next_queue.append((presented[0], (presented[1] * 2) & 0xFF))
        return {"in_ready": ready,
                "out_valid": 1 if out is not None else 0,
                "out_id": out[0] if out is not None else 0,
                "out_y": out[1] if out is not None else 0}

    def edge(self):
        self.queue = self.next_queue
        self.quiet = self.next_quiet


def _tagged_stimulus(c, rows, drain=8):
    return api.reset_then(c, rows, drain=drain, controls={"out_ready": 1},
                          source="tagged")


def _ordered_twin(c):
    """The same contract, correlated by arrival order instead of by tag."""
    return api.TaskContract(
        endpoints=[c.endpoint("in"),
                   api.Endpoint("out", api.Direction.OUT, c.endpoint("out").fields,
                                c.endpoint("out").adapter, clock="clk",
                                kind=api.Kind.TRANSACTION,
                                answers_per_transaction=1,
                                terminal_quiescence=c.endpoint("out").terminal_quiescence)],
        clocks=list(c.clocks))


@pytest.mark.parametrize("behaviour,expected", [
    ("hold", False), ("drop", True), ("mutate", True)])
def test_back_pressure_is_what_makes_the_stability_obligation_mean_anything(
        behaviour, expected):
    """The population sweep held `out_ready` high throughout, so this obligation was
    reported as checked on 794 runs and exercised on none of them."""
    c = _watched()
    task = api.admit(c, realization(), reference=_ref())
    stim = _stalling(c, [1, 2, 3])
    report = Monitor(c).check(stim, run_device(task, stim, _Holding(behaviour)))
    broke = [v for v in report.violations if v.goal == "monitor.sink-stability"]
    assert bool(broke) is expected, report.violations
    assert report.opportunities["monitor.sink-stability"] > 0, \
        "an obligation nothing exercised is not an obligation that was met"


def test_an_unreadable_payload_under_stall_is_unresolved_not_agreement():
    """Two `None`s compared equal, so a design driving X while holding an offer passed
    the stability check on the strength of two absences."""
    c = _watched()
    task = api.admit(c, realization(), reference=_ref())
    stim = _stalling(c, [1, 2, 3])
    report = Monitor(c).check(stim, run_device(task, stim, _Holding("unknown")))
    assert not [v for v in report.violations if v.goal == "monitor.sink-stability"]
    assert [u for u in report.unresolved if u.goal == "monitor.sink-stability"], \
        "it could not be checked, which is neither a pass nor a violation"


def test_accepting_and_retiring_in_one_slot_is_one_transaction_in_flight():
    """The limit was checked between the accept and the retirement, so a design holding
    one transaction was reported holding two."""
    c = _watched(max_outstanding=1)
    task = api.admit(c, realization(), reference=_ref())
    stim = api.reset_then(c, [(1,), (2,), (3,), (4,)], drain=6,
                          controls={"out_ready": 1})
    report = Monitor(c).check(stim, run_device(task, stim, Doubling()))
    assert not [v for v in report.violations
                if v.goal == "monitor.outstanding-exceeded"], report.violations
    assert report.opportunities["monitor.outstanding-exceeded"] > 0


def test_the_deadline_boundary_is_the_declared_number():
    """Off by one in either direction is a different obligation. The design offers on the
    slot after it accepts, so a deadline of 1 is met and a deadline of 0 is not."""
    for deadline, broken in ((1, False), (0, True)):
        c = _watched(response_deadline=deadline)
        task = api.admit(c, realization(), reference=_ref())
        stim = api.reset_then(c, [(1,)], drain=6, controls={"out_ready": 1})
        report = Monitor(c).check(stim, run_device(task, stim, Doubling()))
        got = [v for v in report.violations if v.goal == "monitor.response-deadline"]
        assert bool(got) is broken, (deadline, report.violations)


def test_a_broken_deadline_does_not_retire_the_transaction():
    """It used to `pop(0)`, so the answer that arrived late was matched against the next
    request and the run ended with a phantom unanswered one."""
    c = _watched(response_deadline=0, quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    stim = api.reset_then(c, [(1,)], drain=8, controls={"out_ready": 1})
    report = Monitor(c).check(stim, run_device(task, stim, Doubling()))
    assert len([v for v in report.violations
                if v.goal == "monitor.response-deadline"]) == 1, \
        "one late transaction, reported once"
    assert not [v for v in report.violations
                if v.goal == "monitor.missing-response"], \
        "and it was answered, so nothing is owed at the end"


def test_every_outstanding_transaction_is_timed_not_only_the_oldest():
    """With several in flight the second and third accrued nothing until the first
    retired, so a design that answered the first promptly and the rest never scored one
    violation -- the quietest possible way for a starved request to pass."""
    c = _watched(response_deadline=2, max_outstanding=2, quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    stim = api.reset_then(c, [(1,), (2,)], drain=10, controls={"out_ready": 1})
    report = Monitor(c).check(stim, run_device(task, stim, _Starving()))

    late = [v for v in report.violations if v.goal == "monitor.response-deadline"]
    assert late, "the second request was never answered and its deadline elapsed"
    # And it is not the one that was answered on time.
    assert all("slot 3" not in v.why for v in late), [v.why for v in late]
    # One finding, not two: the terminal check does not restate a transaction whose
    # deadline has already been reported.
    assert not [u for u in report.unresolved if u.goal == Monitor.UNANSWERED]


def test_a_second_answer_belongs_to_the_transaction_that_owed_it():
    """With a declared ratio above one, crediting an offer to "the first transaction
    that has not been offered anything yet" hands transaction A's second answer to
    transaction B -- whose deadline then never fires however long it is starved.

    Under ordered matching a transaction's answers all arrive before the next one's, so
    the transaction being answered is always the oldest outstanding.
    """
    c = _watched(response_deadline=2, answers=2, quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    stim = api.reset_then(c, [(1,), (2,)], drain=10, controls={"out_ready": 1})
    report = Monitor(c).check(stim, run_device(task, stim, _TwiceThenNothing()))

    late = [v for v in report.violations if v.goal == "monitor.response-deadline"]
    assert len(late) == 1, [v.why for v in report.violations]
    assert "accepted at slot 4" in late[0].why, late[0].why


def test_the_end_of_a_horizon_is_not_a_missing_response():
    """A stimulus stops after a caller's drain count. Concluding from that that the
    design owes an answer reports the caller's number as a defect in the design."""
    c = _watched(quiescent=True)
    task = api.admit(c, realization(), reference=_ref())

    # Never answers, the environment receptive throughout. The design was idle at every
    # drain slot, and that establishes nothing: `terminal_quiescence` says *owes nothing
    # implies quiet*, and reading an idle slot as proof that nothing is owed is its
    # converse. With no declared deadline, a finite run cannot tell a design that will
    # never answer from one that had not finished.
    idle = api.reset_then(c, [(1,)], drain=6, controls={"out_ready": 1})
    seen = Monitor(c).check(idle, run_device(task, idle, Deaf()))
    assert not seen.violations, seen.violations
    assert [u for u in seen.unresolved if u.goal == Monitor.UNANSWERED]

    # Declare the deadline and the same run is settled: the design had four receptive
    # slots to offer an answer in and offered none.
    timed = _watched(quiescent=True, response_deadline=2)
    late = Monitor(timed).check(idle, run_device(task, idle, Deaf()))
    assert [v for v in late.violations if v.goal == "monitor.response-deadline"]

    # And under back-pressure the design may be holding a perfectly good answer.
    blocked = api.reset_then(c, [(1,)], drain=6, controls={"out_ready": 0})
    quiet = Monitor(c).check(blocked, run_device(task, blocked, _Holding("hold")))
    assert not quiet.violations, quiet.violations


def test_an_idle_slot_is_not_proof_that_nothing_is_owed():
    """The rule this replaces looked back through the run for a slot on which the design
    offered nothing while the environment was ready, and read it as terminal quiescence.
    A design that takes one cycle to compute is idle in that cycle, and the run that
    ends busy still has idle slots earlier in it -- so the finding read "the design was
    quiet at slot 3 and still owes the transaction accepted at slot 3"."""
    c = _watched(quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    base = api.reset_then(c, [(1,)], drain=0, controls={"out_ready": 1})
    report = Monitor(c).check(base, run_device(task, base, Deaf()))
    assert not report.violations, report.violations
    assert [u for u in report.unresolved if u.goal == Monitor.UNANSWERED], \
        report.unresolved


def test_one_answer_per_request_is_a_declaration():
    """Retiring one pending transaction per output is a guess about the protocol, and 16
    designs of this population produce more outputs than they take inputs."""
    undeclared = api.TaskContract(
        endpoints=[contract().endpoint("in"),
                   api.Endpoint("out", api.Direction.OUT,
                                contract().endpoint("out").fields,
                                contract().endpoint("out").adapter, clock="clk",
                                kind=api.Kind.TRANSACTION)],
        clocks=list(contract().clocks))
    assert Monitor(undeclared).correlation() is None
    for goal in ("monitor.outstanding-exceeded", "monitor.response-deadline",
                 "monitor.output-without-transaction"):
        assert goal in Monitor(undeclared).unchecked()
    assert Monitor(_watched(answers=1)).correlation() == ("ordered", 1)


def test_a_channel_not_declared_transactional_correlates_nothing():
    """`Kind` cannot be read off a run. A continuously meaningful sampled output has a
    value every cycle, and counting its transfers as answers is a different question."""
    base = _watched(answers=1, response_deadline=2)
    sampled = api.TaskContract(
        endpoints=[base.endpoint("in"),
                   api.Endpoint("out", api.Direction.OUT, base.endpoint("out").fields,
                                base.endpoint("out").adapter, clock="clk",
                                answers_per_transaction=1,
                                kind=api.Kind.SAMPLED)],
        clocks=list(base.clocks))
    assert Monitor(sampled).correlation() is None
    assert "monitor.response-deadline" in Monitor(sampled).unchecked()


def test_the_reset_policy_changes_the_verdict_so_it_is_declared():
    """It was an assumption the monitor printed and then acted on."""
    c = _watched(quiescent=True)
    assert Monitor(c).assumptions() == ()
    task = api.admit(c, realization(), reference=_ref())
    base = list(api.reset_then(c, [(1,)], drain=6,
                               controls={"out_ready": 1}).slots)
    mid = api.Stimulus(queues={"in": [(1,)]},
                       slots=base[:-4] + [api.EventSlot(reset=True,
                                                        controls=base[0].controls)]
                       + base[-3:])
    trace = run_device(task, mid, Deaf())
    kept = Monitor(_watched(quiescent=True, reset_policy=ResetPolicy.KEEPS))
    cleared = Monitor(_watched(quiescent=True, reset_policy=ResetPolicy.CLEARS))
    assert [u for u in kept.check(mid, trace).unresolved
            if u.goal == Monitor.UNANSWERED]
    assert not [u for u in cleared.check(mid, trace).unresolved
                if u.goal == Monitor.UNANSWERED]


def test_an_answer_is_correlated_by_its_tag_not_by_arrival_order():
    """The two policies give different verdicts on one run, which is why the policy has
    to be declared rather than assumed.

    The design answers the **newer** request first and leaves the older one waiting. Under
    the tag, the transaction that actually waited is the one whose deadline breaks, and
    the violation names the slot it was accepted at. Under arrival order the first offer
    is credited to the oldest outstanding request -- the wrong one -- so the design that
    starved a request is reported as having answered it promptly.
    """
    c = _tagged(response_deadline=3, quiescent=True)
    task = api.admit(c, _tagged_realization(), reference=None)
    stim = _tagged_stimulus(c, [(7, 1), (9, 2)], drain=14)
    trace = run_device(task, stim, _Reordering(lag=5))

    by_tag = [v for v in Monitor(c).check(stim, trace).violations
              if v.goal == "monitor.response-deadline"]
    by_order = [v for v in Monitor(_ordered_twin(c)).check(stim, trace).violations
                if v.goal == "monitor.response-deadline"]
    # Both report one late transaction, and they disagree about **which**. Under the tag
    # it is the one accepted at slot 3, which is the one the design left waiting; under
    # arrival order the first offer was credited to it, so the blame lands on the request
    # accepted at slot 4 -- the one the design answered immediately.
    assert len(by_tag) == 1 and len(by_order) == 1, (by_tag, by_order)
    assert "accepted at slot 3" in by_tag[0].why, by_tag[0].why
    assert "accepted at slot 4" in by_order[0].why, by_order[0].why


def test_a_repeated_tag_among_those_in_flight_is_outside_the_environment_domain():
    """Uniqueness among outstanding requests is the environment-side premise of ID
    matching. Violating it invalidates the run rather than inventing a correlation."""
    c = _tagged(quiescent=True)
    task = api.admit(c, _tagged_realization(), reference=None)
    stim = _tagged_stimulus(c, [(7, 1), (7, 2)])
    report = Monitor(c).check(stim, run_device(task, stim, _Reordering()))
    assert [v for v in report.assumption_violations
            if v.goal == "environment.identifier-reused-while-outstanding"], report
    assert not [u for u in report.unresolved if "already in flight" in u.why]


def test_a_tag_reused_after_its_transaction_retired_is_a_new_transaction():
    """Uniqueness is among those **in flight**, not over the run. A design answering one
    tag, then being given it again, is doing nothing wrong, and reporting it would make
    any long run with a small tag space unverifiable."""
    c = _tagged(depth=1, quiescent=True)
    task = api.admit(c, _tagged_realization(), reference=None)
    stim = _tagged_stimulus(c, [(7, 1), (7, 2)], drain=10)
    report = Monitor(c).check(stim, run_device(task, stim, _Reordering(depth=1)))
    assert not [u for u in report.unresolved if "already in flight" in u.why]
    assert not report.violations, report.violations


def test_reset_starts_a_new_identifier_epoch_exactly_when_declared():
    """A reset-clears declaration retires the old ID; reset-keeps does not."""
    cleared = _tagged(depth=2, reset_policy=ResetPolicy.CLEARS)
    base = list(_tagged_stimulus(cleared, [(7, 1), (7, 2)], drain=4).slots)
    # Accept tag 7, reset before the depth-two device can answer it, then reuse tag 7.
    slots = base[:4] + [api.EventSlot(reset=True, controls=base[0].controls)] \
        + [base[4]] + base[5:]
    stimulus = api.Stimulus(queues={"in": [(7, 1), (7, 2)]}, slots=slots)

    cleared_task = api.admit(cleared, _tagged_realization(), reference=None)
    cleared_report = Monitor(cleared).check(
        stimulus, run_device(cleared_task, stimulus, _Reordering()))
    assert not cleared_report.assumption_violations, cleared_report.assumption_violations

    kept = _tagged(depth=2, reset_policy=ResetPolicy.KEEPS)
    kept_task = api.admit(kept, _tagged_realization(), reference=None)
    kept_report = Monitor(kept).check(
        stimulus, run_device(kept_task, stimulus, _Reordering()))
    assert [v for v in kept_report.assumption_violations
            if v.goal == "environment.identifier-reused-while-outstanding"], kept_report


def test_an_answer_tagged_for_no_outstanding_request_is_reported():
    """Under an identifier policy the check only asked whether the pending list was
    empty, so an answer tagged 9 while only tag 7 was in flight went unreported --
    the answer to a request that was never made, hidden by any other open one."""
    c = _tagged(quiescent=True)
    task = api.admit(c, _tagged_realization(), reference=None)

    class Mislabelling(_Reordering):
        """Answers, but stamps every answer with a tag nobody asked for."""

        def settle(self, presented, controls):
            got = super().settle(presented, controls)
            if got["out_valid"]:
                got["out_id"] = (got["out_id"] + 100) & 0xFF
            return got

    stim = _tagged_stimulus(c, [(7, 1), (9, 2)], drain=12)
    report = Monitor(c).check(stim, run_device(task, stim, Mislabelling()))
    assert [v for v in report.violations
            if v.goal == "monitor.output-without-transaction"], report.violations


def test_an_offer_whose_payload_is_x_still_stops_the_deadline():
    """`valid` says an offer was made; the data lines decide whether the answer can be
    compared. Requiring both made an X-driven offer invisible, so the deadline went on
    firing against a transaction the design had answered -- one event reported as an
    unresolved and a late violation at once."""
    c = _watched(response_deadline=1, quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    stim = _stalling(c, [1, 2, 3])
    report = Monitor(c).check(stim, run_device(task, stim, _Holding("unknown")))
    late = [v for v in report.violations if v.goal == "monitor.response-deadline"]
    assert not late, [v.why for v in late]
    assert [u for u in report.unresolved if u.goal == "monitor.sink-stability"], \
        "what it offered still cannot be checked, and that is said separately"


def test_the_padded_suffix_of_a_failed_run_is_not_observed_cycles():
    """A `PADDED` trace keeps `N` records so it still describes its own stimulus, but the
    suffix after the artifact failed is terminal stuttering the driver wrote and never
    invoked the artifact for -- `SlotRecord(monitor={"padding": 1})`, no signals at all.
    Reading those as cycles had the monitor accruing deadlines against slots in which
    nothing was running and reporting the design's silence in them as an unreadable
    payload."""
    from behave.sim.core.ledger import ledger_of
    from behave.sim.core.stimulus import AuditedTrace, Completion, SlotRecord

    c = _watched(response_deadline=1, quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    stim = api.reset_then(c, [(1,)], drain=10, controls={"out_ready": 1})
    ran = run_device(task, stim, Deaf())

    # The same run, cut short at slot 6 the way the driver cuts one: every later slot is
    # padding, and the completion says the concrete predicate is false.
    cut = 6
    padded = AuditedTrace(
        stim,
        records=list(ran.records[:cut])
        + [SlotRecord(monitor={"padding": 1})
           for _ in range(len(ran.records) - cut)],
        completion=Completion.PADDED, domain=ran.domain)

    ledger = ledger_of(c, stim, padded)
    assert ledger.observed == cut and len(ledger) == cut
    assert ledger.truncated, "nothing terminal follows from a padded run"

    report = Monitor(c).check(stim, padded)
    assert all(v.slot < cut for v in report.violations), \
        [v.slot for v in report.violations]
    assert not [u for u in report.unresolved
                if u.goal == "monitor.sink-stability"], \
        "padding is not an unreadable payload; it is not a slot"
    assert [u for u in report.unresolved if u.goal == Monitor.UNANSWERED
            and "stopped before its horizon" in u.why]


def test_an_unreadable_payload_does_not_hide_a_withdrawn_valid():
    """The offer and its payload were one variable, so a design that stalls with X on the
    data bus and then withdraws `valid` came back as an unresolved payload and **no
    violation** -- while `valid` is readable on both slots and nothing about it is in
    doubt. The payload comparison is unresolved; the withdrawal is determined."""
    c = _watched()
    task = api.admit(c, realization(), reference=_ref())
    stim = _stalling(c, [1, 2, 3])
    report = Monitor(c).check(stim, run_device(task, stim, _Holding("x-then-drop")))

    dropped = [v for v in report.violations if v.goal == "monitor.sink-stability"]
    assert dropped, report.unresolved
    assert "dropped `valid`" in dropped[0].why, dropped[0].why

    # The payload half is still reported apart from it: a design that keeps offering an
    # unreadable payload across a stall is unresolved, not clean and not broken.
    holding = Monitor(c).check(stim, run_device(task, stim, _Holding("unknown")))
    assert not [v for v in holding.violations if v.goal == "monitor.sink-stability"]
    assert [u for u in holding.unresolved if u.goal == "monitor.sink-stability"]


# ── the reset trace goals and the telemetry-only legacy probe ──


def _claiming():
    """The base contract, with the task claiming its reset restores the design."""
    from behave.sim.core.contract import ClockDomain, TaskContract

    base = contract()
    return TaskContract(endpoints=list(base.endpoints),
                        clocks=[ClockDomain("clk", "clk", "rst",
                                            reset_restores=True)],
                        source=base.source, authority=base.authority)


def test_there_is_no_set_evidence_location_and_no_provider_seam():
    """Review 10 blocker 1. For one release a goal could say `evidence="set"` and
    the old raw evaluator took obligation providers whose judgement was over the whole
    stimulus set. Appendix B's goal semantics has nowhere to put that: every goal
    belongs to some step `t` of some admitted trace -- reached in an `H_t`, queried as
    `h_{t,g}`, certified by the same reached/bounded-unreachable machinery -- and a
    cross-stimulus experiment has no `t`. So the third evidence location is refused at
    the `Goal` and the provider seam is gone from the signature.  Reset restoration is
    instead represented by the monitor's two ordinary trace goals.
    """
    import inspect

    from behave.sim.core.catalogue import SAFETY, Goal

    with pytest.raises(Refused, match="goal-evidence-unknown"):
        Goal("task.reset-restores", SAFETY, provider="reset_probe", evidence="set")
    assert "obligations" not in inspect.signature(evaluate_device).parameters
    task = api.admit(_claiming(), realization(), reference=_ref())
    ev = _evaluate(task, [schedule([3])], Doubling(), reference=_ref())
    assert "task.reset-restores" not in dict(ev.catalogue)
    assert {
        "monitor.reset-restoration.clk.scenario",
        "monitor.reset-restoration.clk.violation",
    }.issubset(ev.catalogue)
    assert all(goal.startswith("monitor.") for goal in dict(ev.catalogue))


def test_reset_audit_does_not_own_the_callers_timer(monkeypatch):
    """The retained diagnostic shares a deadline without replacing SIGALRM."""
    import signal
    from types import SimpleNamespace

    from behave.sim.rtl.artifact import RTLArtifact

    artifact = RTLArtifact("x.sv", "top", "scratch/probe_timer_test", run_policy={
        "reset_hold_cycles": contract().clocks[0].reset_hold_cycles})
    calls = []

    def traces(self, contract_, stimuli, verify_batch=True):
        calls.append((tuple(stimuli), verify_batch))
        return [SimpleNamespace(records=()) for _ in stimuli]

    monkeypatch.setattr(RTLArtifact, "traces", traces)
    first, other = schedule([3]), schedule([4])

    fired = []
    was = signal.signal(signal.SIGALRM, lambda s, f: fired.append(True))
    signal.alarm(60)
    try:
        state, _ = artifact.reset_audit(contract(), [first, other], budget_s=30)
        remaining = signal.alarm(0)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, was)
    assert state == "reset_probe_reproduced"
    assert calls == [((first, other, first), False)]
    assert not fired
    assert remaining > 0, "the caller's alarm was cancelled by the probe"


@pytest.mark.parametrize("seconds", [0, 0.25, 1, 9, 44])
def test_reset_audit_uses_actual_deadline_without_a_private_minimum(monkeypatch, seconds):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.search_policy import active_search

    from .test_api_path import schedule

    captured = {}

    def capture(self, contract_, stimuli, verify_batch=True):
        captured.update(timeout_s=self.timeout_s,
                        remaining=active_search().deadline - time.monotonic())
        return [Refusal("made-by-this-test", "stop here")] * len(stimuli)

    art = RTLArtifact("x.sv", "top", "scratch/probe_budget_test",
                      run_policy={"reset_hold_cycles":
                                  contract().clocks[0].reset_hold_cycles})
    two = [schedule([3]), schedule([4])]
    monkeypatch.setattr(RTLArtifact, "traces", capture)
    state, why = art.reset_audit(contract(), two, budget_s=seconds)
    assert state == "unresolved"
    if seconds == 0:
        assert not captured
        assert "time-budget-exhausted" in why
    else:
        assert captured["timeout_s"] == art.timeout_s
        assert 0 < captured["remaining"] <= seconds


def test_reset_audit_cannot_extend_its_parent_deadline_and_closes_resources(monkeypatch):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.search_policy import SearchBudget, SearchPolicy, active_search, search_scope

    parent = SearchBudget(SearchPolicy(time_budget_s=10)).direction(1)
    closed = []

    def capture(self, contract_, stimuli, verify_batch=True):
        lease = active_search()
        assert lease.deadline == parent.deadline
        lease.resources.callback(closed.append, True)
        return [Refusal("made-by-this-test", "stop here")] * len(stimuli)

    monkeypatch.setattr(RTLArtifact, "traces", capture)
    art = RTLArtifact("x.sv", "top", "scratch/probe_budget_test", run_policy={
        "reset_hold_cycles": contract().clocks[0].reset_hold_cycles})
    with search_scope(parent):
        state, _ = art.reset_audit(contract(), [schedule([3]), schedule([4])], budget_s=30)
        assert active_search() is parent
    assert state == "unresolved"
    assert closed == [True]


@pytest.mark.cosim
def test_public_command_does_not_run_the_auxiliary_reset_probe(tmp_path, monkeypatch):
    from behave.sim.rtl.artifact import RTLArtifact
    from tests.support.scoring_task import handshake_register_command

    def unexpected(*args, **kwargs):
        pytest.fail("legacy reset probe is not part of the public score path")

    monkeypatch.setattr(RTLArtifact, "reset_audit", unexpected)
    result = api.handle_command(handshake_register_command(tmp_path))
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
