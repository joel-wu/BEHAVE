"""Only complete, identical local negative questions cross audited seed boundaries."""

from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.engines import probing
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.engines.behavior import generation_session as child

from .test_behavior_local_unsat_reuse import _count_queries, _selection, _trace
from .test_behavior_generation_session import _requests

pytestmark = pytest.mark.solver


def _session(monkeypatch, built, targets):
    # Keep the actual lowered source, contract, audit and solver; avoid building
    # a second copy merely to exercise the session's lifetime in this test.
    monkeypatch.setattr(child, "_build_with_context", lambda *a, **kw: (built, None, False))
    return child.PreparedGeneration({"targets": list(targets)})


def _query(seed, targets):
    return {"seed": seed.as_dict(), "trace": _trace(seed).as_dict(), "targets": list(targets)}


def _select(prepared, query):
    """Inspect local questions in trace order, not scheduler priority order."""
    answer = prepared.select(query)
    local = prepared.selected.occurrences
    requests = sorted((request for request in _requests(answer)
                       if request['occurrence'] in local),
                      key=lambda request: (local[request['occurrence']]['rank'], request['rank']))
    return {**answer, 'requests': requests}


def _other(seed, right):
    return api.Stimulus({"left": dict(seed.queues)["left"], "right": [(x,) for x in right]},
                        seed.slots)


def test_distinct_seeds_share_only_complete_proofs_after_both_native_audits(monkeypatch):
    built, initial = _selection(3)
    prepared = _session(monkeypatch, built, initial.targets)
    seeds = [initial.seed, _other(initial.seed, [7, 9, 11])]
    queries = [_query(seed, initial.targets) for seed in seeds]
    audits = []
    audit = jobs._replay_feedback

    def replay(*args):
        audits.append(args[2].fingerprint)
        return audit(*args)

    monkeypatch.setattr(jobs, "_replay_feedback", replay)
    calls = _count_queries(monkeypatch)
    # Baseline-first Select visits both seeds, then Probe restores an evicted
    # selection. Returning to either seed must still use the same exact proof.
    selections = [_select(prepared, query) for query in queries]
    for index in [0, 1, 0, 1]:
        answer = prepared.probe({**queries[index], "request": _requests(selections[index])[0],
                                 "timeout_ms": 20_000})
        assert answer["outcome"] == "local-unsat"
        assert answer["scope"] == "exact-local-hit"
        assert answer["candidate"] is None and answer["goal"] is None
    assert len(calls) == 1
    assert audits == [seed.fingerprint for seed in seeds]
    assert len(prepared.proved_questions) == 1
    assert prepared.selected._unsat_questions is prepared.proved_questions
    assert not prepared.selected._partial_queries  # No partial formula/proof imported.
    assert all(goal in built[5] for row in selections for req in _requests(row)
               for goal in req["targets"])

    # Another worker, even with identical source bytes, starts without proofs.
    another = _session(monkeypatch, built, initial.targets)
    selected = _select(another, queries[0])
    assert not another.proved_questions
    assert another.probe({**queries[0], "request": _requests(selected)[0],
                          "timeout_ms": 20_000})["outcome"] == "local-unsat"
    assert len(calls) == 2


@pytest.mark.parametrize("outcome", [probing.Outcome.UNKNOWN, probing.Outcome.UNSUPPORTED])
def test_unfinished_answers_are_not_shared_between_seeds(monkeypatch, outcome):
    built, initial = _selection(2)
    prepared = _session(monkeypatch, built, initial.targets)
    calls = []

    def incomplete(*args, **kwargs):
        calls.append(args[1])
        # A completed subquery is not a proof of the whole disjunction.
        kwargs["proved_unsat"].add("unfinished-disjunction")
        return probing.ProbeResult(outcome, probing.Scope.EXACT_LOCAL_HIT)

    monkeypatch.setattr(jobs, "_local_hit", incomplete)
    for seed in [initial.seed, _other(initial.seed, [7, 9])]:
        query = _query(seed, initial.targets)
        selected = _select(prepared, query)
        assert not prepared.selected._partial_queries
        assert prepared.probe({**query, "request": _requests(selected)[0],
                               "timeout_ms": 1000})["outcome"] == outcome.value
    assert len(calls) == 2 and not prepared.proved_questions


@pytest.mark.parametrize("context", ["state", "history"])
def test_changed_seed_context_can_still_find_and_replay_sat(monkeypatch, context):
    built, initial = _selection(3, stateful=context == "state", history_goal=context == "history")
    prepared = _session(monkeypatch, built, initial.targets)
    calls = _count_queries(monkeypatch)
    query = _query(initial.seed, initial.targets)
    selected = _select(prepared, query)
    first = prepared.probe({**query, "request": _requests(selected)[0], "timeout_ms": 20_000})
    assert first["outcome"] == "local-unsat"

    # For state, the second input sees count=2; for history, the third sees
    # previous output=5. Neither is covered by the first input's negative proof.
    seed = _other(initial.seed, [7, 5, 11])
    query = _query(seed, initial.targets)
    selected = _select(prepared, query)
    request = _requests(selected)[1 if context == "state" else 2]
    for _ in range(2):
        answer = prepared.probe({**query, "request": request, "timeout_ms": 20_000})
        assert answer["outcome"] == "sat"
        candidate = api.Stimulus.from_dict(answer["candidate"])
        confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(),
                                       list(initial.targets))
        assert answer["goal"] not in selected["replay"]["hits"]
        assert answer["goal"] in confirmed.replay.hits
    assert len(calls) == 3  # SAT proposals are never memoized.


def test_a_prior_negative_proof_does_not_bypass_a_later_seed_audit(monkeypatch):
    built, initial = _selection(2)
    prepared = _session(monkeypatch, built, initial.targets)
    query = _query(initial.seed, initial.targets)
    selected = _select(prepared, query)
    assert prepared.probe({**query, "request": _requests(selected)[0],
                           "timeout_ms": 20_000})["outcome"] == "local-unsat"
    from behave.sim.symbolic import interp
    from behave.sim.core.instance import StepOutput

    run = interp._run_step

    def wrong(*args, **kwargs):
        state, _outputs, hits, error = run(*args, **kwargs)
        return state, StepOutput(), hits, error

    monkeypatch.setattr(interp, "_run_step", wrong)
    other = _query(_other(initial.seed, [7, 9]), initial.targets)
    with pytest.raises(api.Refused, match="feedback-unverified"):
        prepared.select(other)
    with pytest.raises(ValueError, match="confirmed Select"):
        prepared.probe({**other, "request": _requests(selected)[0], "timeout_ms": 20_000})


def test_target_groups_can_alternate_without_discarding_complete_answers(monkeypatch):
    from .test_behavior_local_unsat_reuse import _key_fixture

    selected, occurrence, request, calls = _key_fixture(monkeypatch)
    for targets in [("observed",), ("source",), ("observed",), ("source",)]:
        request = SimpleNamespace(targets=targets, identity="request", mutable_fields=())
        assert selected.local_hit(occurrence, request, 1000).outcome is probing.Outcome.LOCAL_UNSAT
    assert len(calls) == 2 and len(selected._unsat_questions) == 2


def test_negative_storage_does_not_grow_with_visited_states(monkeypatch):
    from .test_behavior_local_unsat_reuse import _key_fixture

    selected, occurrence, request, calls = _key_fixture(monkeypatch)
    for value in range(100):
        occurrence["state"]["array"][0][0] = value
        selected.local_hit(occurrence, request, 1000)
        assert len(selected._unsat_questions) == 1
    assert len(calls) == 100
    selected.local_hit(occurrence, request, 1000)
    assert len(calls) == 100  # The latest complete question still reuses its proof.
    occurrence["state"]["array"][0][0] = 0
    selected.local_hit(occurrence, request, 1000)
    assert len(calls) == 101  # An evicted answer is recomputed, never guessed.
