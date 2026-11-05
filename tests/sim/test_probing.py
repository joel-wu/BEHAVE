"""What a local probe may claim, and whether the baseline adds up.

G0 and G1 of section 4B. The first is a guard against a conversion nothing performs
yet, and the second is a measurement that has to be taken before the thing it
judges exists.
"""
import pytest

from behave.sim.engines import probing
from behave.sim.engines.probing import Hit, Outcome, ProbeResult, Scope, Tally

# ── G0. a probe result that cannot become a verdict ──

def test_a_local_unsat_cannot_be_read_as_a_verdict():
    """The whole point of the type.

    A local solve asks whether some input, from **one sampled state**, satisfies a
    query built from **one observed path prefix**. An unsatisfiable answer says
    nothing about the artifact: another state, or another prefix from the same
    state, may reach the goal. `unreachable` is a claim over every admissible trace
    and only the horizon closure ranges over those.

    Made impossible rather than inconvenient, since the conversion is the kind that
    looks reasonable at the call site.
    """
    result = ProbeResult(Outcome.LOCAL_UNSAT, Scope.PATH_PREFIX, goal="g")
    assert not hasattr(result, "as_goal_status"), \
        "the generation algebra has no status conversion to call accidentally"


def test_no_horizon_scope_or_authority_flag_exists_in_generation():
    """Horizon-wide answers live in ``sim.core.analysis``, not this algebra."""
    for scope in (Scope.EXACT_LOCAL_HIT, Scope.PATH_PREFIX, Scope.RTL_LOCAL_HIT):
        assert not hasattr(ProbeResult(Outcome.LOCAL_UNSAT, scope),
                           "carries_authority"), scope
    assert not hasattr(Scope, "HORIZON")


def test_a_result_without_a_scope_cannot_be_built():
    """The scope is what makes the result readable, so it is not optional. An exact
    provider's UNSAT rules out every input at the snapshot; the Dual fallback's
    rules out one path-prefix query. A reader handed neither would take the
    stronger reading."""
    with pytest.raises(TypeError):
        ProbeResult(Outcome.LOCAL_UNSAT, "path-prefix")
    with pytest.raises(TypeError):
        ProbeResult("unsat", Scope.PATH_PREFIX)


def test_a_satisfiable_probe_with_no_candidate_is_refused():
    """SAT that produced nothing is a bookkeeping error, not an answer."""
    with pytest.raises(ValueError):
        ProbeResult(Outcome.SAT, Scope.PATH_PREFIX, goal="g")
    assert ProbeResult(Outcome.SAT, Scope.PATH_PREFIX, goal="g",
                       candidate=[1, 2]).candidate == [1, 2]
    with pytest.raises(ValueError):
        ProbeResult(Outcome.LOCAL_UNSAT, Scope.PATH_PREFIX, candidate=[1])


# ── G1. the baseline ──

def test_the_four_classes_account_for_every_satisfiable_probe():
    """The identity, which is the reason `check` exists:

        SAT = target-hit + other-hit + novel-state-only + rejected
    """
    tally = Tally("test")
    for outcome, scope in ((Outcome.SAT, Scope.PATH_PREFIX),
                           (Outcome.SAT, Scope.PATH_PREFIX),
                           (Outcome.LOCAL_UNSAT, Scope.PATH_PREFIX),
                           (Outcome.UNKNOWN, Scope.PATH_PREFIX),
                           (Outcome.UNSUPPORTED, Scope.RTL_LOCAL_HIT)):
        tally.record(ProbeResult(outcome, scope,
                                 candidate=[0] if outcome is Outcome.SAT else None))
    tally.classify(target_hit=True, other_hit=False, novel_state=False)
    with pytest.raises(AssertionError):
        tally.check()               # one SAT still unclassified
    tally.classify(target_hit=False, other_hit=False, novel_state=False)
    assert tally.check()
    assert tally.queries == 5
    assert tally.hits[Hit.TARGET] == 1 and tally.hits[Hit.REJECTED] == 1


def test_a_candidate_that_does_several_jobs_is_counted_once():
    """Priority, first match wins, and that is what makes the identity hold. One
    candidate can hit its target, hit a second new goal, and reach a novel state;
    counted in three columns it would break the sum."""
    tally = Tally("test")
    tally.record(ProbeResult(Outcome.SAT, Scope.EXACT_LOCAL_HIT, candidate=[0]))
    where = tally.classify(target_hit=True, other_hit=True, novel_state=True)
    assert where is Hit.TARGET
    assert sum(tally.hits.values()) == 1
    assert tally.check()

    # And the overlaps are kept, non-exclusively, because dropping them would hide
    # how often one candidate does several jobs.
    assert tally.overlaps["target_and_other"] == 1
    assert tally.overlaps["target_and_novel_state"] == 1
    assert tally.overlaps["other_and_novel_state"] == 1


def test_the_priority_order_is_the_definition():
    for kwargs, expected in (
            ({"target_hit": True, "other_hit": False, "novel_state": False},
             Hit.TARGET),
            ({"target_hit": False, "other_hit": True, "novel_state": True},
             Hit.OTHER),
            ({"target_hit": False, "other_hit": False, "novel_state": True},
             Hit.NOVEL_STATE),
            ({"target_hit": False, "other_hit": False, "novel_state": False},
             Hit.REJECTED)):
        tally = Tally("test")
        tally.record(ProbeResult(Outcome.SAT, Scope.PATH_PREFIX, candidate=[0]))
        assert tally.classify(**kwargs) is expected, kwargs
        assert tally.check()


def test_unsat_is_counted_by_scope_and_not_only_in_total():
    """A total over the scopes is a number nobody can read, since an UNSAT means
    different things from an exact provider and from the path-prefix fallback."""
    tally = Tally("test")
    tally.record(ProbeResult(Outcome.LOCAL_UNSAT, Scope.EXACT_LOCAL_HIT))
    tally.record(ProbeResult(Outcome.LOCAL_UNSAT, Scope.PATH_PREFIX))
    tally.record(ProbeResult(Outcome.LOCAL_UNSAT, Scope.PATH_PREFIX))
    got = tally.as_dict()
    assert got["outcomes"]["local-unsat"] == 3
    assert got["by_scope"]["exact-local-hit/local-unsat"] == 1
    assert got["by_scope"]["path-prefix/local-unsat"] == 2


def test_the_baseline_survives_being_written_down():
    """It is reported as JSON, and the first version keyed `by_scope` by the
    `(scope, outcome)` pair -- which dropped every model of the first baseline run
    with `TypeError: keys must be str, int, float, bool or None`. A measurement that
    cannot be serialised is a measurement nobody has."""
    import json

    tally = Tally("test")
    tally.record(ProbeResult(Outcome.SAT, Scope.PATH_PREFIX, candidate=[1]))
    tally.classify(target_hit=True, other_hit=False, novel_state=False)
    text = json.dumps(tally.as_dict())
    assert json.loads(text)["hits"]["target-hit"] == 1


def test_as_dict_refuses_to_report_a_tally_that_does_not_add_up():
    """Reporting is where a broken count would escape, so the check is there too."""
    tally = Tally("test")
    tally.record(ProbeResult(Outcome.SAT, Scope.PATH_PREFIX, candidate=[0]))
    with pytest.raises(AssertionError):
        tally.as_dict()


def test_every_outcome_and_scope_has_a_name():
    """Exactly the four local answers and three local scopes from T3-06, plus a
    suspended build (`constructing`) and a relaxed state-preparation proposal
    (`proposed`, only in its own `state-preparation` scope); none is a verdict."""
    assert {o.value for o in Outcome} == {"sat", "local-unsat", "unknown",
                                          "unsupported", "constructing", "proposed"}
    assert {s.value for s in Scope} == {"exact-local-hit", "path-prefix",
                                        "rtl-local-hit", "state-preparation"}
    # Five, where G1 specified four. `not-replayed` is its own class because the
    # search discards most satisfiable candidates before running them, and on the
    # first baseline that was 76.3% of 51,885 -- putting them in `rejected` would
    # assert a replay that never happened.
    assert {h.value for h in Hit} == {"target-hit", "other-hit",
                                      "novel-state-only", "rejected",
                                      "not-replayed"}
    assert set(probing.__all__) == {"Outcome", "ProbeResult", "Scope", "Tally",
                                    "Hit"}


def test_a_discarded_candidate_is_not_a_rejected_one():
    """The fifth class, and the reason it exists.

    `rejected` says a replay ran and reached nothing. `not-replayed` says nothing
    ran. On the first baseline the second was **76.3% of 51,885 satisfiable
    probes**, so folding them together would have put three quarters of the data
    into a column whose meaning is a replay that did not happen -- and that column
    is one G10's migration is judged by.
    """
    tally = Tally("test")
    for _ in range(3):
        tally.record(ProbeResult(Outcome.SAT, Scope.PATH_PREFIX, candidate=[0]))
    assert tally.discarded() is Hit.NOT_REPLAYED
    tally.discarded()
    tally.classify(target_hit=False, other_hit=False, novel_state=False)
    assert tally.check()
    assert tally.hits[Hit.NOT_REPLAYED] == 2
    assert tally.hits[Hit.REJECTED] == 1
    # And discarding records no overlaps, since there is nothing to overlap.
    assert set(tally.overlaps.values()) == {0}


def test_the_target_hit_rate_is_split_by_whether_the_goal_was_new():
    """The split that turned a meaningless average into two real numbers.

    Measured over 80 models: a candidate solved for a goal **nothing had reached**
    hits it **1.9%** of the time (142 of 7,675), and one solved for an
    already-reached goal hits it **98.8%** (4,414 of 4,467). The single figure this
    entry first reported, 37%, was the average of those weighted by which candidates
    happened to be replayed, and is a number about neither case.
    """
    tally = Tally("test")
    for _ in range(4):
        tally.record(ProbeResult(Outcome.SAT, Scope.PATH_PREFIX, candidate=[0]))
    tally.classify(target_hit=False, other_hit=False, novel_state=True,
                   primary=True)
    tally.classify(target_hit=True, other_hit=False, novel_state=False,
                   primary=True)
    tally.classify(target_hit=True, other_hit=False, novel_state=True,
                   primary=False)
    tally.discarded(primary=False)

    got = tally.as_dict()["by_target"]
    assert got["primary"] == {"sat": 2, "target_hit": 1, "replayed": 2}
    # A discarded candidate counts as a SAT for its kind and as no replay, since
    # nothing ran it: `sat` exceeds `replayed` by exactly the discards.
    assert got["advancing"] == {"sat": 2, "target_hit": 1, "replayed": 1}
    assert tally.check()
