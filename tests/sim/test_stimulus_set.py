"""The canonical stimulus set: decoding, drain, one horizon, dedup, provenance.

Split out of `test_wave1_closure.py` by concern; every test is a review counterexample
turned regression against `behave.sim.stimulus_set` and the schedule pieces of
`behave.sim.api`. Shared fixtures are imported from where they are defined.
"""
import pathlib  # noqa: F401

import pytest

from behave.sim import api
from behave.sim.core.refusal import Refusal, Refused  # noqa: F401
from behave.sim.stimulus_set import GenerationSpec

from .test_api_path import Deaf, Doubling, _live, _ref, contract, realization, schedule  # noqa: F401
from .test_wave1_closure import HOLD, _concrete, _repo, _staged, _top_of  # noqa: F401


@pytest.mark.parametrize("values", [(20, 12, 3, 2, None), (20, 12, 3, 2, 0), (5, 1, 3, 1, 3)])
def test_public_generation_spec_record_round_trip(values):
    spec = api.GenerationSpec(*values)
    restored = api.GenerationSpec.from_dict(spec.as_dict())
    assert restored.as_dict() == spec.as_dict()
    assert restored.identity == spec.identity


@pytest.mark.parametrize("change", [
    {"max_body_steps": value} for value in (0, 2, 1.0, True, None, "1")
] + [{"horizon": 5.0}, {"horizon": 4}, {"queue_bound": True}, {"drain": -1}, {"extra": 1}])
def test_public_generation_spec_record_rejects_changed_or_coerced_fields(change):
    image = {**api.GenerationSpec(5, 1, 3, 1).as_dict(), **change}
    with pytest.raises((ValueError, api.Refused)):
        api.GenerationSpec.from_dict(image)


@pytest.mark.parametrize("field", ["horizon", "queue_bound", "reset_hold_cycles", "drain", "stall_every", "max_body_steps"])
def test_public_generation_spec_record_requires_the_whole_image(field):
    image = api.GenerationSpec(5, 1, 3, 1).as_dict()
    del image[field]
    with pytest.raises(ValueError):
        api.GenerationSpec.from_dict(image)


def test_a_recorded_schedule_is_the_environment_the_provider_ran():
    """Dropping these made every design whose providers record one `partial` -- 10
    candidates of 761 was enough to deny `comparator_3bit` a score over its complete
    `Xi_A`. And T3-02's complaint about the old replay is the opposite error: it ignored
    the recorded schedule and invented its own stalls.

    A witness carries both forms and they answer different questions. The rows are the
    transactions; the schedule is the per-cycle environment around them. So the queue
    still comes from the rows -- the driver's pointer decides presentation, which is the
    one rule -- and the controls come from the schedule.
    """
    from behave.sim.core.stimulus import from_legacy
    from behave.sim.stimulus_set import _on_recorded_schedule, stimuli_from_witnesses

    c = contract()
    rows = {"inputs": [[1], [2]], "source": "p"}
    # **As many reset cycles as the clock domain declares.** This test used one, and the
    # contract declares three -- so it was asserting that a schedule starting from
    # somewhere the task never described is accepted. See the reset audit below.
    held = [{"data": {}, "control": {"rst": 1, "in_valid": 0, "out_ready": 0}}
            for _ in range(c.clocks[0].reset_hold_cycles)]
    recorded = {"controls": ["in_valid", "out_ready", "rst"],
                "source": "a solver",
                "cycles": held + [
                    {"data": {}, "control": {"rst": 0, "in_valid": 1,
                                             "out_ready": 0}},
                    {"data": {}, "control": {"rst": 0, "in_valid": 1,
                                             "out_ready": 1}},
                    {"data": {}, "control": {"rst": 0, "in_valid": 0,
                                             "out_ready": 1}}]}

    body = from_legacy(rows, endpoint="in")
    got = _on_recorded_schedule(c, body, recorded, source="t")
    assert len(got.slots) == len(held) + 3, "one slot per recorded cycle"
    assert [s.reset for s in got.slots] == [True] * len(held) + [False] * 3
    assert [bool(s.offers) for s in got.slots] == \
        [False] * len(held) + [True, True, False]
    # The environment is the recorded one, not a pattern this build chose.
    assert dict(got.slots[len(held)].controls)["out_ready"] == 0
    assert dict(got.slots[len(held) + 1].controls)["out_ready"] == 1

    # Nothing is dropped for carrying one any more.
    stimuli, dropped, _recorded = stimuli_from_witnesses(
        c, [rows, {**rows, "source": "q", "schedule": recorded}], ["in_a"], 4,
        stall_every=0)
    assert len(stimuli) == 2 and not dropped
    assert any("recorded" in s.source for s in stimuli)


def test_a_recorded_schedule_that_leaves_a_control_unnamed_is_refused():
    """A control signal no cycle names is one this build would have to choose, which is
    the hole the recorded schedule exists to close -- and `Schedule` itself refuses one
    at construction for the same reason."""
    from behave.sim.core.stimulus import from_legacy
    from behave.sim.stimulus_set import _on_recorded_schedule

    c = contract()
    body = from_legacy({"inputs": [[1]], "source": "p"}, endpoint="in")
    with pytest.raises(Refused, match="authority-recorded-schedule-incomplete"):
        _on_recorded_schedule(c, body, {
            "controls": ["rst"], "source": "partial",
            "cycles": [{"data": {}, "control": {"rst": 1}}]}, source="t")
    with pytest.raises(Refused, match="authority-recorded-schedule-empty"):
        _on_recorded_schedule(c, body, {"controls": [], "cycles": []}, source="t")


def test_xi_is_replayed_in_an_order_determined_by_the_set():
    """`Xi_A` is a set and a batched replay drives it as a sequence, so the sequence is
    part of the experiment. The providers search in parallel and return in whatever order
    they finish, so nothing fixes the sequence unless this does.

    **Not because it explained a moving score.** It was introduced for that and the claim
    failed its own test: every one of `comparator_3bit`'s 761 records is identical under
    sorted and reversed replay. A determined order is right on its own terms."""
    from behave.sim.stimulus_set import stimuli_from_witnesses

    c = contract()
    rows = [{"inputs": [[v]], "source": f"p{v}"} for v in (7, 1, 4, 2)]
    forward, _, _ = stimuli_from_witnesses(c, rows, ["in_a"], 4, stall_every=0)
    backward, _, _ = stimuli_from_witnesses(c, rows[::-1], ["in_a"], 4,
                                        stall_every=0)

    assert {s.fingerprint for s in forward} == {s.fingerprint for s in backward}
    assert [s.fingerprint for s in forward] == [s.fingerprint for s in backward], \
        "the same set is replayed in the same sequence however the providers ordered it"
    assert [s.fingerprint for s in forward] == sorted(
        s.fingerprint for s in forward), "and the sequence is a function of the set"


# ── 11. the forgeries: four ways a wrong design passed, each run rather than read ──


def test_a_recorded_schedule_must_start_where_the_run_says():
    """Two readings were live at once. Either the recorded schedule is this candidate's
    authoritative environment -- and then `run.toml`'s `stall_every` is a rule for
    candidates without one -- or `run.toml` is a trace domain every stimulus is audited
    against. The code did the first for the environment and claimed the second in the
    report.

    Where the reading lands: the **environment** is the provider's, because overwriting
    it is the defect T3-02 names; **where the run starts** is the run policy's, because a
    record taken from a different starting state is not in the same trace domain as the
    rest of the set. Reproduced before the audit existed: a one-cycle recorded schedule
    produced a stimulus with zero reset slots against a hold of three, while the caller
    had asked for a drain of 64.

    **Whose number, and what happens to the candidate.** The refusal used to say "the
    reset the clock domain declares" -- no specification in this population declares one,
    it was a constructor default, and it is `run.toml`'s `reset_hold_cycles` now. And a
    candidate this path cannot carry is **dropped and counted** rather than refusing the
    whole evaluation: one schedule in several hundred deciding the fate of the rest is the
    error the `dropped` list already existed to prevent for other causes.
    """
    from behave.sim.core.stimulus import from_legacy
    from behave.sim.stimulus_set import _on_recorded_schedule, canonical_set, stimuli_from_witnesses

    c = contract()
    body = from_legacy({"inputs": [[3]], "source": "p"}, endpoint="in")
    one_cycle = {"controls": ["in_valid", "out_ready", "rst"],
                 "cycles": [{"data": {},
                             "control": {"rst": 0, "in_valid": 1, "out_ready": 1}}]}

    with pytest.raises(Refused,
                       match="authority-recorded-schedule-reset-not-the-run-s"):
        _on_recorded_schedule(c, body, one_cycle, source="t")

    # **Exactly the run's hold, not at least it.** `reset_hold_cycles` is in the
    # trace-domain identity as one number, so a schedule holding five under a policy of
    # three was accepted and its record carried a domain naming a reset it did not run.
    held = c.clocks[0].reset_hold_cycles
    longer = {"controls": ["in_valid", "out_ready", "rst"],
              "cycles": [{"data": {}, "control": {"rst": 1, "in_valid": 0,
                                                  "out_ready": 0}}] * (held + 2)
                        + [{"data": {}, "control": {"rst": 0, "in_valid": 1,
                                                    "out_ready": 1}}]}
    with pytest.raises(Refused,
                       match="authority-recorded-schedule-reset-not-the-run-s"):
        _on_recorded_schedule(c, body, longer, source="t", drain=2,
                              reset_hold_cycles=held)

    # And a schedule that does hold reset for the declared cycles is taken as it is --
    # its own length, its own environment, neither replaced by the run's policy.
    held = c.clocks[0].reset_hold_cycles
    proper = {"controls": ["in_valid", "out_ready", "rst"],
              "cycles": [{"data": {}, "control": {"rst": 1, "in_valid": 0,
                                                  "out_ready": 0}}] * held
                        + [{"data": {}, "control": {"rst": 0, "in_valid": 1,
                                                    "out_ready": 0}}]}
    got = _on_recorded_schedule(c, body, proper, source="t")
    assert len(got.slots) == held + 1
    # The environment is the provider's: `out_ready` is 0 where it recorded 0, whatever
    # a `stall_every` would have chosen.
    assert dict(got.slots[held].controls)["out_ready"] == 0

    # And the set says how many of its members ran under an environment that is not the
    # configured one, so a report naming one policy cannot be read as covering them.
    stimuli, dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[1]], "source": "plain"},
            {"inputs": [[2]], "source": "q", "schedule": proper}],
        ["in_a"], 4, stall_every=3)
    assert len(stimuli) == 2 and not dropped
    recorded = [f for f, rows in origin.items()
                if any(r["schedule"] == "recorded" for r in rows)]
    assert len(recorded) == 1
    # Provenance a reader can follow back: which candidate, and which schedule. A list
    # per stimulus, because several candidates can canonicalise to one.
    row = origin[recorded[0]][0]
    assert row["of"] == "q" and len(row["digest"]) == 64

    # ── a schedule this run cannot carry drops its own candidate, not the set ──
    stimuli, dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[1]], "source": "plain"},
            {"inputs": [[2]], "source": "bad", "schedule": one_cycle}],
        ["in_a"], 4, stall_every=3)
    assert len(stimuli) == 1, "one bad schedule took the rest of the set with it"
    assert [d["source"] for d in dropped] == ["bad"]
    assert dropped[0]["carries"] == \
        ["authority-recorded-schedule-reset-not-the-run-s"]

    # ── and the count is after the dedup and the padding, not before either ──
    #
    # Two identical recorded witnesses are one stimulus. The count used to be appended as
    # each was built, so it said two -- and the dedup itself used to run before the
    # padding to `N`, which is earlier than the set is canonical. Both are
    # `canonical_set`'s now, in that order.
    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles = 4, 3, None, 3

    raw, dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[2]], "source": "q", "schedule": proper},
            {"inputs": [[2]], "source": "q", "schedule": proper}],
        ["in_a"], 4, stall_every=3)
    assert len(raw) == 2, "this stage does not deduplicate; canonicalising does"
    stimuli, _n, provenance = canonical_set(raw, origin, c, _Declared())
    assert len(stimuli) == 1
    assert len(provenance) == 1, provenance
    assert len(provenance[stimuli[0].fingerprint]) == 1, \
        "one candidate, offered twice, is one row"


# ── 18. the ratio rule, against sentences written to fool it ──

#: Specifications whose ratio is **not** 1:1, written the ways a loose rule reads as
#: 1:1. Two of these were reproduced against the previous rule and both came back
#: `answers_per_transaction = 1`.
def test_every_stimulus_gets_the_declared_drain_before_a_horizon_is_taken():
    """The order is: each candidate takes its own drain, then `N` is taken over the set.

    Reproduced the other way round: `one_horizon` took the longest existing stimulus, a
    recorded schedule carried no drain of its own, and no `run.toml` states an explicit
    horizon -- so with `drain = 64` a recorded candidate ran to
    `declared_drain=64, N=2, slots_after_last_offer=0`. The declared drain did not reach
    it at all.
    """
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses

    c = contract()
    held = c.clocks[0].reset_hold_cycles
    proper = {"controls": ["in_valid", "out_ready", "rst"],
              "cycles": [{"data": {}, "control": {"rst": 1, "in_valid": 0,
                                                  "out_ready": 0}}] * held
                        + [{"data": {}, "control": {"rst": 0, "in_valid": 1,
                                                    "out_ready": 1}}]}

    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles = 64, 3, None, 3

    raw, _dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[2]], "source": "recorded", "schedule": proper},
            {"inputs": [[1], [2], [3]], "source": "constructed"}],
        ["in_a"], 64, stall_every=3, reset_hold_cycles=held)
    stimuli, horizon, _prov = canonical_set(raw, origin, c, _Declared())

    for stimulus in stimuli:
        offers = [i for i, slot in enumerate(stimulus.slots) if slot.offers]
        assert offers, stimulus.source
        assert len(stimulus.slots) - 1 - offers[-1] >= 64, \
            f"{stimulus.source} has {len(stimulus.slots) - 1 - offers[-1]} slots after "\
            f"its last offer and the run declares a drain of 64"
        assert len(stimulus.slots) == horizon, "the set is not one horizon"

    # And a declared `N` a candidate does not fit in is refused rather than truncated.
    class _Short(_Declared):
        horizon = 4

    with pytest.raises(Refused, match="authority-horizon-shorter-than-a-candidate"):
        canonical_set(raw, origin, c, _Short())


def test_a_slot_never_records_a_control_the_run_will_not_drive():
    """One authority per pin.

    An input endpoint's own `valid` was in the slot's controls *and* decided by `offers`,
    and the two disagreed wherever nothing was presented: `_padded_to` copied the last
    cycle's `in_valid = 1` into an idle slot, and the testbench, seeing `present()`
    return `None`, drove 0. The stimulus fingerprint then bound a control sequence that
    never ran -- which is the binding between stimulus, trace and domain that the whole
    path rests on.

    `in_valid` is not in a slot at all now. `TaskContract.scheduled_controls` is what a
    schedule names, `core/driver.Driver.controls` derives the rest from the same
    `present` call the bus is driven from, and a slot that names one is a fault.
    """
    from behave.sim.core.stimulus import EventSlot, Stimulus
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses

    c = contract()
    held = c.clocks[0].reset_hold_cycles
    assert "in_valid" in c.environment_controls()
    assert "in_valid" in c.presented_controls()
    assert "in_valid" not in c.scheduled_controls()
    assert "out_ready" in c.scheduled_controls() and "rst" in c.scheduled_controls()

    recorded = {"controls": ["in_valid", "out_ready", "rst"],
                "cycles": [{"data": {}, "control": {"rst": 1, "in_valid": 0,
                                                    "out_ready": 0}}] * held
                          + [{"data": {}, "control": {"rst": 0, "in_valid": 1,
                                                      "out_ready": 1}}]}

    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles = 6, 3, None, 3

    raw, _d, origin = stimuli_from_witnesses(
        c, [{"inputs": [[3]], "source": "q", "schedule": recorded},
            {"inputs": [[1], [2]], "source": "p"}],
        ["in_a"], 6, stall_every=3, reset_hold_cycles=held)
    stimuli, _n, _p = canonical_set(raw, origin, c, _Declared())

    for stimulus in stimuli:
        for i, slot in enumerate(stimulus.slots):
            assert "in_valid" not in dict(slot.controls), (stimulus.source, i)
        assert not stimulus.faults_against(c), stimulus.faults_against(c)

    # And a slot that does name it is a fault, by a message naming the rule.
    two = Stimulus(queues={"in": ((3,),)},
                   slots=[EventSlot(offers=["in"],
                                    controls={"rst": 0, "in_valid": 1,
                                              "out_ready": 1})],
                   source="two-authorities")
    assert any("second authority over one pin" in f
               for f in two.faults_against(c)), two.faults_against(c)


def test_padding_to_one_horizon_cannot_leave_a_repeat_in_the_set():
    """Two candidates that differ only in their tail are one stimulus once both reach `N`.

    The dedup ran **before** the padding, so the repeat survived into the final set:
    `distinct_before=2, members_after=2, distinct_after=1`. The telemetry then said two
    stimuli while the old raw evaluator quietly removed one, the second candidate's provenance
    overwrote the first's, and a cap's denominator was not the set that ran.

    Canonicalising is padding, then deduplication, then the caller's cap -- and
    Concrete evaluation refuses a set with a repeat rather than making one.
    """
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses

    c = contract()
    held = c.clocks[0].reset_hold_cycles

    def recorded(tail):
        return {"controls": ["in_valid", "out_ready", "rst"],
                "cycles": [{"data": {}, "control": {"rst": 1, "in_valid": 0,
                                                    "out_ready": 0}}] * held
                          + [{"data": {}, "control": {"rst": 0, "in_valid": 1,
                                                      "out_ready": 1}}]
                          + [{"data": {}, "control": {"rst": 0, "in_valid": 0,
                                                      "out_ready": 1}}] * tail}

    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles = 0, 0, None, 3

    raw, _d, origin = stimuli_from_witnesses(
        c, [{"inputs": [[7]], "source": "short", "schedule": recorded(0)},
            {"inputs": [[7]], "source": "long", "schedule": recorded(2)}],
        ["in_a"], 0, stall_every=0, reset_hold_cycles=held)
    assert len({s.fingerprint for s in raw}) == 2, "these two start out distinct"

    stimuli, horizon, provenance = canonical_set(raw, origin, c, _Declared())
    assert len(stimuli) == 1, "the padded repeat survived into the set"
    assert len({s.fingerprint for s in stimuli}) == len(stimuli)
    assert len(provenance) == 1
    # Both candidates are named, so nothing is silently overwritten.
    assert sorted(r["of"] for r in provenance[stimuli[0].fingerprint]) == \
        ["long", "short"]
    assert {r["padded_from"] for r in provenance[stimuli[0].fingerprint]} == {4, 6}
    assert all(r["slots"] == horizon for r in provenance[stimuli[0].fingerprint])


@pytest.mark.parametrize("active_low", [False, True])
def test_an_idle_tail_releases_reset_rather_than_inheriting_it(active_low):
    """`slot.reset` and the reset pin say the same thing, in every slot this build makes.

    The idle tail copied the previous slot's controls, so a schedule that **ended in
    reset** produced a tail with `reset = False` and `rst` still asserted. A Python
    device reads the flag and comes out of reset; an RTL testbench reads the pin and
    stays in it. One stimulus, two meanings, and nothing in the record to say which.

    Both polarities, because "released" is a different bit for each, and a mid-run reset
    followed by a drain is the shape that produced it.
    """
    from behave.sim.api import idle_tail
    from behave.sim.core.contract import ClockDomain, TaskContract
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses

    base = contract()
    c = TaskContract(
        endpoints=list(base.endpoints),
        clocks=[ClockDomain("clk", "clk", "rst", reset_active_low=active_low,
                            reset_hold_cycles=3)],
        source=base.source, authority=base.authority)
    asserted, released = (0, 1) if active_low else (1, 0)
    held = 3

    def cycle(rst, valid, ready):
        return {"data": {}, "control": {"rst": rst, "in_valid": valid,
                                        "out_ready": ready}}

    # Reset, one offer, then reset again -- so the drain follows a reset slot.
    ends_in_reset = {"controls": ["in_valid", "out_ready", "rst"],
                     "cycles": [cycle(asserted, 0, 0)] * held
                               + [cycle(released, 1, 1)]
                               + [cycle(asserted, 0, 0)]}

    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles = 2, 0, None, 3

    raw, dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[3]], "source": "q", "schedule": ends_in_reset}],
        ["in_a"], 2, stall_every=0, reset_hold_cycles=held)
    assert not dropped, dropped
    stimuli, _n, _p = canonical_set(raw, origin, c, _Declared())

    for stimulus in stimuli:
        assert not stimulus.faults_against(c), stimulus.faults_against(c)
        for i, slot in enumerate(stimulus.slots):
            driven = dict(slot.controls)["rst"]
            assert (driven == asserted) == slot.reset, (i, slot.reset, driven)
        # The mid-run reset is still there, and the tail after it is not in reset.
        assert stimulus.slots[held + 1].reset, "the recorded reset was dropped"
        assert not stimulus.slots[-1].reset
        assert dict(stimulus.slots[-1].controls)["rst"] == released

    # And the helper itself, asked directly, over a reset slot.
    from behave.sim.core.stimulus import EventSlot

    tail = idle_tail(c, [EventSlot(reset=True, controls={"rst": asserted,
                                                         "out_ready": 0})], 2)
    assert [slot.reset for slot in tail] == [False, False]
    assert all(dict(slot.controls)["rst"] == released for slot in tail)


def test_a_control_this_build_schedules_is_one_bit():
    """`int(...)` took whatever was there, so a slot could carry `out_ready = 2`, the
    driver would put a 2 on a one-bit pin, and the fingerprint would be over a value no
    run drove."""
    from behave.sim.core.driver import Driver
    from behave.sim.core.stimulus import EventSlot, Stimulus

    c = contract()
    for value in (2, -1, 255):
        stim = Stimulus(queues={"in": ((1,),)},
                        slots=[EventSlot(offers=["in"],
                                         controls={"rst": 0, "out_ready": value})],
                        source="out-of-domain")
        faults = stim.faults_against(c)
        assert any("one bit" in f for f in faults), (value, faults)
        with pytest.raises(Refused, match="driver-stimulus-not-of-this-contract"):
            Driver(c, stim)
    # And the two values that are a bit are admitted.
    for value in (0, 1):
        assert not Stimulus(
            queues={"in": ((1,),)},
            slots=[EventSlot(offers=["in"], controls={"rst": 0,
                                                      "out_ready": value})],
            source="in-domain").faults_against(c)


def test_a_recorded_control_outside_the_bit_domain_is_dropped_by_name():
    """`int(...)` is a conversion, not a validation -- and the input's own `valid` is
    projected out of the slot after being read, so a slot-level check never sees it.

    Reproduced: a recorded `in_valid = 2` came through as a schedule with zero offers
    and `faults_against == []` -- an illegal environment silently rewritten into a
    different, legal stimulus under the same name. The raw record is validated before
    the projection now, and the candidate is dropped with the refusal's name on it.
    """
    from behave.sim.core.stimulus import from_legacy
    from behave.sim.stimulus_set import _on_recorded_schedule, stimuli_from_witnesses

    c = contract()
    held = c.clocks[0].reset_hold_cycles
    reset = [{"data": {}, "control": {"rst": 1, "in_valid": 0, "out_ready": 0}}] * held

    for control, value in (("in_valid", 2), ("out_ready", 2), ("rst", -1),
                           ("in_valid", "x")):
        bad = {"controls": ["in_valid", "out_ready", "rst"],
               "cycles": reset + [{"data": {},
                                   "control": {"rst": 0, "in_valid": 1,
                                               "out_ready": 1, control: value}}]}
        with pytest.raises(Refused,
                           match="authority-recorded-schedule-control-not-a-bit"):
            _on_recorded_schedule(
                c, from_legacy({"inputs": [[3]], "source": "p"}, endpoint="in"),
                bad, source="t", drain=2, reset_hold_cycles=held)

    # Through the set: the candidate is dropped by name, the rest survive.
    bad = {"controls": ["in_valid", "out_ready", "rst"],
           "cycles": reset + [{"data": {}, "control": {"rst": 0, "in_valid": 2,
                                                       "out_ready": 1}}]}
    raw, dropped, _origin = stimuli_from_witnesses(
        c, [{"inputs": [[3]], "source": "q", "schedule": bad},
            {"inputs": [[5]], "source": "p"}],
        ["in_a"], 4, stall_every=0, reset_hold_cycles=held)
    assert len(raw) == 1
    assert dropped[0]["carries"] == ["authority-recorded-schedule-control-not-a-bit"]


def test_an_output_only_task_is_refused_and_does_not_crash_canonicalisation():
    """Appendix A.1's score is a rate over accepted input transfers, so a task with no
    input endpoint has no denominator: refused by name at admission.

    And the branch behind the refusal is repaired too: its provenance was a bare dict
    where every other stimulus carries a list of rows, so the first output-only contract
    through `canonical_set` crashed the whole evaluation into `authority-crashed` -- an
    exception in place of a typed answer.
    """
    from behave.sim.core.supported import refusals_for
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses

    base = contract()
    sink_only = api.TaskContract(
        endpoints=[e for e in base.endpoints if e.direction is api.Direction.OUT],
        clocks=list(base.clocks), source=base.source, authority=base.authority)

    kinds = [r.kind for r in refusals_for(sink_only, None, (), 4, 0)]
    assert "unsupported-no-input-endpoint" in kinds, kinds

    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles = 4, 0, None, 3

    raw, dropped, origin = stimuli_from_witnesses(sink_only, [], [], 4, stall_every=0)
    stimuli, _horizon, provenance = canonical_set(raw, origin, sink_only, _Declared())
    assert len(stimuli) == 1 and not dropped
    rows = provenance[stimuli[0].fingerprint]
    assert isinstance(rows, list) and rows[0]["of"] == "authority/ticks"


def test_the_trace_domain_is_the_predicate_and_not_the_construction_policy():
    """A recorded stimulus does not obey `stall_every` and a constructed one does, and
    both used to carry a domain identity that named it -- so half of every run's records
    were labelled with a policy they never ran under.

    A trace domain is what its admissibility predicate is over: the interface, the
    horizon, the reset policy, the adapter version. The back-pressure pattern is the rule
    that *constructs* a stimulus for a candidate carrying no schedule of its own; which
    environment each stimulus actually ran under is recorded per stimulus instead.
    """
    from behave.sim.core.stimulus import contract_of_domain, trace_domain

    base = trace_domain("iface", horizon=100, reset_hold_cycles=3)
    assert contract_of_domain(base) == "iface"
    # What separates two domains.
    assert base != trace_domain("other", horizon=100, reset_hold_cycles=3)
    assert base != trace_domain("iface", horizon=101, reset_hold_cycles=3)
    assert base != trace_domain("iface", horizon=100, reset_hold_cycles=5)
    assert base != trace_domain("iface", horizon=100, reset_hold_cycles=3,
                                adapter_version="2")
    # And an unstated horizon is not the same document as a stated one.
    assert base != trace_domain("iface", horizon=None, reset_hold_cycles=3)
    # The identity names no stall pattern at all: there is no argument for one.
    import inspect

    assert "stall" not in inspect.signature(trace_domain).parameters


def test_a_candidate_carrying_more_than_inputs_is_refused_not_trimmed():
    """A witness with memory content describes a run in which the design and the
    reference exchange values through state no transaction carries. Building a stimulus
    from its `inputs` alone runs a different experiment and then compares the two
    transaction streams as though they were the whole of it."""
    from behave.sim.stimulus_set import stimuli_from_witnesses

    c = contract()
    plain = [{"inputs": [[1], [2]], "source": "p"}]
    stimuli, dropped, _recorded = stimuli_from_witnesses(c, plain, ["in_a"], 4, stall_every=0)
    assert stimuli and not dropped

    # **That candidate, not the run.** The seam run found the difference: the real
    # providers put a recorded `schedule` on ten witnesses out of 761, and refusing the
    # whole task threw away the 751 that carried nothing. Dropped by name, counted, and
    # the other candidates still run.
    mixed = plain + [{"inputs": [[3]], "source": "q", "mem_init": {"m": {"0": 1}}}]
    stimuli, dropped, _recorded = stimuli_from_witnesses(c, mixed, ["in_a"], 4, stall_every=0)
    assert len(stimuli) == 1 and [d["carries"] for d in dropped] == [["mem_init"]]

    # And where nothing survives, the run is refused rather than scored over nothing.
    with pytest.raises(Refused, match="authority-no-usable-candidate"):
        stimuli_from_witnesses(c, [{**plain[0], "mem_init": {"m": 1}}], ["in_a"], 4)
    # A policy for an undeclared memory is no more consumable than its contents.  It is
    # dropped at the witness boundary instead of surviving as a loose second authority
    # beside the task-owned memory declaration and the stimulus's exact ready schedule.
    with pytest.raises(Refused, match="authority-no-usable-candidate"):
        stimuli_from_witnesses(
            c, [{"inputs": [[1], [2]], "source": "p",
                 "mem_policy": {"m": {"read_latency": 1}}}], ["in_a"], 4)


def test_a_candidate_in_the_action_form_keeps_its_ticks_and_resets():
    """`witness["inputs"]` was read directly, so a candidate carrying `interleave` or
    `actions` -- which is where a tick or a reset can appear at all -- produced a
    stimulus with none of them, or none at all. The boundary is the one place either
    form is read, and this now goes through it."""
    from behave.sim.stimulus_set import stimuli_from_witnesses

    c = contract()
    rows = [{"inputs": [[1], [2]], "source": "rows"}]
    acted = [{"interleave": [["drive", "in", [1]], ["tick", "clk", []],
                             ["drive", "in", [2]]], "source": "acted"}]
    plain = stimuli_from_witnesses(c, rows, ["in_a"], 2, stall_every=0)[0][0]
    ticked = stimuli_from_witnesses(c, acted, ["in_a"], 2, stall_every=0)[0][0]
    assert len(ticked.slots) == len(plain.slots) + 1, "the tick is a slot"
    assert plain.fingerprint != ticked.fingerprint

    # And a record carrying two forms is refused rather than resolved, by the boundary.
    with pytest.raises(Refused, match="legacy-witness-two-forms"):
        stimuli_from_witnesses(c, [{**rows[0], **acted[0]}], ["in_a"], 2)


def test_the_columns_are_reindexed_in_the_action_form_too():
    """Reindexing by field name protected the row form and not the action form, whose
    `drive` values are just as positional."""
    from behave.sim.stimulus_set import _reindexed

    got = _reindexed({"inputs": [[7, 9]],
                      "interleave": [["drive", "in", [7, 9]], ["reset", "clk", []]]},
                     [1, 0])
    assert got["inputs"] == [[9, 7]]
    assert got["interleave"][0] == ["drive", "in", [9, 7]]
    assert got["interleave"][1] == ["reset", "clk", []], "untouched"


def test_the_queue_bound_is_run_policy_in_the_digest_and_the_domain():
    """todo3 T3-01: the run configuration fixes a finite queue bound per input endpoint;
    queue lengths vary within it and stay one domain; changing it changes the domain
    fingerprint; an over-bound queue is refused (here: that candidate drops by name)."""
    from behave.sim.core.stimulus import trace_domain
    from behave.sim.stimulus_set import GenerationSpec, canonical_set, effective_queue_bound, stimuli_from_witnesses

    c = contract()
    held = c.clocks[0].reset_hold_cycles

    # In the policy digest: two bounds are two documents.
    assert GenerationSpec(100, 4, 3, 64, 3).identity != \
        GenerationSpec(100, 8, 3, 64, 3).identity
    # In the domain identity: two bounds are two domains.
    assert trace_domain("iface", horizon=10, queue_bound=4) != \
        trace_domain("iface", horizon=10, queue_bound=8)

    # Over the set: within the bound both candidates are one domain's members; over it,
    # the candidate drops by name and the rest survive.
    raw, dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[1]], "source": "one"},
            {"inputs": [[1], [2], [3]], "source": "three"}],
        ["in_a"], 4, stall_every=0, reset_hold_cycles=held, queue_bound=2)
    assert len(raw) == 1
    assert dropped[0]["source"] == "three"
    assert dropped[0]["carries"] == ["authority-queue-exceeds-the-bound"]

    # An unstated bound is **no** bound, never the horizon: one transfer per slot
    # bounds how much of a queue a run can consume, not how deep a queue a provider
    # hands over, and the derived number certified depths it did not bound (review 9
    # P0-3). A direct caller that states none gets a domain whose membership test
    # skips the check; the authoritative path requires it since run-policy version 4.
    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles, queue_bound = 4, 0, None, 3, None

    raw, dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[1], [2], [3]], "source": "three"}],
        ["in_a"], 4, stall_every=0, reset_hold_cycles=held)
    stimuli, horizon, _prov = canonical_set(raw, origin, c, _Declared())
    assert not dropped and len(stimuli) == 1
    assert effective_queue_bound(_Declared(), horizon) is None


def test_the_admissibility_predicate_is_the_domain_membership_test():
    """The identity string binds interface, N, reset hold, adapter version and queue
    bound; `stimulus_admissible` is those fields as a membership test over the
    **stimulus** -- not the paper's closed-loop `K_N^env`, which nothing implements yet
    -- so "is this stimulus well-formed for this domain" is a function call rather than
    a re-reading of the construction sites. Every canonical set passes it by
    construction, and this checks each field refuses alone.
    """
    from behave.sim.core.stimulus import EventSlot, Stimulus
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses
    from behave.sim.stimulus_set import stimulus_admissible as admissible

    c = contract()
    held = c.clocks[0].reset_hold_cycles

    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles, queue_bound = 4, 0, None, 3, None

    raw, _d, origin = stimuli_from_witnesses(
        c, [{"inputs": [[1], [2]], "source": "p"}],
        ["in_a"], 4, stall_every=0, reset_hold_cycles=held)
    stimuli, horizon, _prov = canonical_set(raw, origin, c, _Declared())
    member = stimuli[0]
    ok = {"horizon": horizon, "reset_hold_cycles": held, "queue_bound": horizon}
    assert admissible(member, c, **ok) == []

    # Each field of the identity, violated alone, refused alone.
    assert any("horizon" in f
               for f in admissible(member, c, **{**ok, "horizon": horizon + 1}))
    assert any("holds reset" in f
               for f in admissible(member, c,
                                   **{**ok, "reset_hold_cycles": held + 1}))
    assert any("bound" in f
               for f in admissible(member, c, **{**ok, "queue_bound": 1}))
    # And the stimulus-level faults ride along: a slot carrying a presented control is
    # not a member of any domain of this contract.
    two = Stimulus(queues={"in": ((1,),)},
                   slots=[EventSlot(offers=["in"],
                                    controls={"rst": 0, "in_valid": 1,
                                              "out_ready": 1})],
                   source="two-authorities")
    assert any("second authority" in f
               for f in admissible(two, c, horizon=1, reset_hold_cycles=0,
                                   queue_bound=1))


def test_an_over_horizon_candidate_drops_by_name_and_never_the_run():
    """Review 9 P0-3. A candidate longer than the declared `N` used to reach
    `one_horizon`, whose refusal took the **whole evaluation** to `refused` -- one
    candidate deciding the fate of the rest, the exact failure the per-candidate drops
    exist to stop. Generation now receives the declared horizon and drops the
    candidate by name; the backstop refusal behind it stays for a construction bug.
    """
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses

    c = contract()
    held = c.clocks[0].reset_hold_cycles

    # The bound is roomy on purpose: the 16-transaction candidate must reach the
    # horizon check, not fall to the queue bound first.
    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles, queue_bound = 4, 0, 12, 3, 64

    raw, dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[v] for v in [1, 2, 3, 4]] * 4, "source": "long"},
            {"inputs": [[1], [2]], "source": "fits"}],
        ["in_a"], 4, stall_every=0, reset_hold_cycles=held,
        queue_bound=_Declared.queue_bound, horizon=_Declared.horizon)
    assert [d["carries"] for d in dropped] == \
        [["authority-candidate-exceeds-the-horizon"]]
    assert dropped[0]["source"] == "long"
    stimuli, n, _prov = canonical_set(raw, origin, c, _Declared())
    assert n == 12 and len(stimuli) == 1, "the fitting candidate still runs"


def test_a_deep_queue_under_a_declared_bound_is_that_candidates_problem():
    """Review 9 P0-3. With the bound *derived* from the horizon, a depth-10 queue
    under a short declared `N` passed generation (nothing was declared to trip) and
    then failed `canonical_set`'s own membership check -- escalating one candidate's
    problem to `authority-canonical-set-outside-its-domain` over the whole run. With
    the bound declared and handed to generation, the candidate drops by name and the
    set canonicalises.
    """
    from behave.sim.stimulus_set import canonical_set, stimuli_from_witnesses

    c = contract()
    held = c.clocks[0].reset_hold_cycles

    class _Declared:
        drain, stall_every, horizon, reset_hold_cycles, queue_bound = 4, 0, 12, 3, 4

    raw, dropped, origin = stimuli_from_witnesses(
        c, [{"inputs": [[v] for v in range(10)], "source": "deep"},
            {"inputs": [[1], [2]], "source": "fits"}],
        ["in_a"], 4, stall_every=0, reset_hold_cycles=held,
        queue_bound=_Declared.queue_bound, horizon=_Declared.horizon)
    assert [d["carries"] for d in dropped] == \
        [["authority-queue-exceeds-the-bound"]]
    stimuli, _n, _prov = canonical_set(raw, origin, c, _Declared())
    assert len(stimuli) == 1, "one candidate's depth never decides the set's fate"


def test_static_membership_does_not_duplicate_closed_loop_admissibility():
    """Review 9 P1-1. The predicate was named `admissible` and its docstring called it
    "the predicate the domain identity names", which reads as the paper's `K_N^env`.
    The name now says what it tests (the stimulus, statically); the docstring assigns
    state-dependent protocol assumptions to registered-adapter replay and the exact
    relation.  The reset helper remains in `core/stimulus` rather than making the
    domain layer depend on one backend (`rtl/`).
    """
    import inspect

    from behave.sim import stimulus_set
    from behave.sim.core.stimulus import leading_reset_slots
    from behave.sim.rtl import artifact as rtl_artifact

    assert not hasattr(stimulus_set, "admissible")
    doc = stimulus_set.stimulus_admissible.__doc__
    assert "K_N^env" in doc and "not" in doc.split("K_N^env")[0][-100:]
    assert "registered adapter" in doc and "Outside" in doc
    # The helper is core's; the rtl module re-exports the same object.
    assert rtl_artifact.leading_reset_slots is leading_reset_slots
    assert ".rtl." not in inspect.getsource(stimulus_set), \
        "the domain layer does not depend on one backend"


def test_the_generation_spec_is_one_record_and_generation_is_handed_it():
    """`GenerationSpec` (domain fields plus construction policy, deliberately not the
    `trace_domain` identity, which excludes drain and stall) is the one record of a
    finite generation domain, and its body bound clamps every constructed schedule.
    """
    from behave.sim.stimulus_set import GenerationSpec

    spec = GenerationSpec(256, 64, 3, 64, 3)
    # One number bounds both forms: a constructed schedule is hold + body + drain and
    # its queue holds one transaction per row; a recorded one is its own cycles plus
    # the drain. min(queue_bound, horizon - hold - drain).
    assert spec.max_body_steps == 64
    assert GenerationSpec(80, 64, 3, 64).max_body_steps == 13
    assert spec.as_dict()["max_body_steps"] == 64

    # Finite search domains are explicit runtime records, not task declarations.
    assert GenerationSpec.from_dict(spec.as_dict()).identity == spec.identity


def test_an_uninhabitable_domain_refuses_at_the_spec_too():
    """`declarations_at` refuses the relation at admission; the spec re-checks it
    because a direct caller can construct one without a manifest, and a domain whose
    horizon cannot fit reset + one offer + drain would silently clamp every engine to
    a nonsense step count instead of refusing by name.
    """
    with pytest.raises(Refused, match="authority-domain-uninhabitable"):
        GenerationSpec(4, 64, 3, 64)
    with pytest.raises(Refused, match="authority-domain-uninhabitable"):
        GenerationSpec(67, 64, 3, 64)
    assert GenerationSpec(68, 64, 3, 64).max_body_steps == 1


@pytest.mark.parametrize("field,value", [
    ("horizon", True),
    ("queue_bound", "64"),
    ("reset_hold_cycles", 3.0),
    ("drain", False),
    ("stall_every", "3"),
])
def test_generation_spec_does_not_coerce_policy_fields(field, value):
    values = {"horizon": 68, "queue_bound": 64, "reset_hold_cycles": 3,
              "drain": 64, "stall_every": 0}
    values[field] = value
    with pytest.raises(
            Refused, match="authority-generation-spec-field-not-an-integer"):
        GenerationSpec(**values)


@pytest.mark.parametrize("field,value", [
    ("horizon", 0),
    ("queue_bound", 0),
    ("reset_hold_cycles", -1),
    ("drain", -1),
    ("stall_every", -1),
])
def test_generation_spec_rejects_policy_fields_outside_their_ranges(field, value):
    values = {"horizon": 68, "queue_bound": 64, "reset_hold_cycles": 3,
              "drain": 64, "stall_every": 0}
    values[field] = value
    with pytest.raises(
            Refused, match="authority-generation-spec-field-out-of-range"):
        GenerationSpec(**values)


def test_legacy_rtl_provider_patterns_decode_to_logical_recursive_values():
    from behave.sim.stimulus_set import stimuli_from_witnesses

    mode = api.ValueType.enumeration(("idle", "run", "hold"))
    task = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("mode", mode, "in_mode")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("mode", mode, "out_mode")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=0)],
    )
    witnesses = [
        {"inputs": [[2]], "source": "physical"},
        {"inputs": [["run"]], "source": "logical"},
        {"inputs": [[3]], "source": "invalid-tag"},
    ]

    stimuli, dropped, _origin = stimuli_from_witnesses(
        task, witnesses, ["in_mode"], 1, stall_every=0,
        reset_hold_cycles=0, queue_bound=1, horizon=4)

    assert {stimulus.head("in", 0) for stimulus in stimuli} == {
        ("hold",), ("run",),
    }
    assert [row["carries"] for row in dropped] == [
        ["authority-provider-packed-value-invalid"]]


def test_provider_signed_patterns_decode_without_changing_logical_values():
    from behave.sim.stimulus_set import stimuli_from_witnesses

    signed = api.ValueType.integer(api.FiniteType(4, signed=True))
    task = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("value", signed, "in_value")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("value", signed, "out_value")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=0)],
    )

    stimuli, dropped, _origin = stimuli_from_witnesses(
        task,
        [{"inputs": [[15]], "source": "physical-minus-one"},
         {"inputs": [[-2]], "source": "logical-minus-two"}],
        ["in_value"], 1, stall_every=0,
        reset_hold_cycles=0, queue_bound=1, horizon=4)

    assert not dropped
    assert {stimulus.head("in", 0) for stimulus in stimuli} == {
        (-1,), (-2,),
    }
