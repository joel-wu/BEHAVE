"""Completed local UNSAT subqueries survive an unfinished disjunction, not changes of meaning."""

import pytest
import z3

from behave.sim.symbolic import encoder, interp, ir, ranges
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines import local_ir as exact
from behave.sim.engines import probing
from behave.sim.engines.behavior import generation_job as jobs

from .test_behavior_local_unsat_reuse import _key_fixture, _selection, _trace
from .test_local_query_sharing import problem

pytestmark = pytest.mark.solver


def two_occurrences():
    proc = ir.Procedure(("s",), ("a",), ("i", "j"), (), ir.Seq(
        ir.For("i", 1, ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(4)),
                             ir.Mark("goal"), ir.Skip())),
        ir.For("j", 1, ir.If(ir.Op("eq", ir.Var("a"), ir.Var("s")),
                             ir.Mark("goal"), ir.Skip()))),
        initial={"s": 0}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    return proc, {"a": ranges.Range(0, 3), "s": ranges.Range(0, 4)}


def test_deadline_keeps_only_completed_unsat_and_later_sat_is_replayed(monkeypatch):
    proc, env = two_occurrences()
    prepared = exact._LocalQueryPreparation(proc, env)
    proved, encoded, clock = set(), [], [0.0]
    encode = exact._encode_query_step
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])

    def expire_second(*args, **kwargs):
        encoded.append(kwargs["keep"])
        if len(encoded) == 2:
            clock[0] += .101
        return encode(*args, **kwargs)

    monkeypatch.setattr(exact, "_encode_query_step", expire_second)
    first = prepared.probe({"s": 2}, ("goal",), timeout_ms=100, _proved_unsat=proved)
    assert first.outcome is probing.Outcome.UNKNOWN
    assert first.candidate is None and len(proved) == 1
    assert len(encoded) == 2 and encoded[0] != encoded[1]
    clock[0] = 0.0
    second = prepared.probe({"s": 2}, ("goal",), timeout_ms=100, _proved_unsat=proved)
    assert second.outcome is probing.Outcome.SAT
    assert second.candidate == {"a": 2}
    assert second.goal in interp.run_step(proc, second.candidate, {"s": 2})[2]
    assert len(encoded) == 3 and encoded[-1] == encoded[1]
    assert len(proved) == 1  # SAT did not add an answer or a candidate.
    again = prepared.probe({"s": 2}, ("goal",), timeout_ms=100, _proved_unsat=proved)
    assert again.outcome is probing.Outcome.SAT and len(encoded) == 4


def test_solver_unknown_is_retried_before_the_whole_question_can_be_unsat(monkeypatch):
    proc, env = two_occurrences()
    prepared, proved, checks = exact._LocalQueryPreparation(proc, env), set(), []
    solver_type = type(z3.Solver())
    check = solver_type.check

    def first_unknown(self, *args, **kwargs):
        checks.append(1)
        return z3.unknown if len(checks) == 1 else check(self, *args, **kwargs)

    monkeypatch.setattr(solver_type, "check", first_unknown)
    first = prepared.probe({"s": 4}, ("goal",), _proved_unsat=proved,
        timeout_ms=60_000)
    assert first.outcome is probing.Outcome.UNKNOWN
    assert len(checks) == 2 and len(proved) == 1
    second = prepared.probe({"s": 4}, ("goal",), _proved_unsat=proved,
        timeout_ms=60_000)
    assert second.outcome is probing.Outcome.LOCAL_UNSAT
    assert second.scope is probing.Scope.EXACT_LOCAL_HIT
    assert len(checks) == 3 and len(proved) == 2
    assert second.candidate is None and second.goal is None
    # No goal declaration disappeared, and another state still reaches it.
    assert sum(isinstance(node, ir.Mark) for node in ir.walk(proc.body)) == 2
    other = prepared.probe({"s": 2}, ("goal",),
        timeout_ms=60_000)
    assert other.outcome is probing.Outcome.SAT
    assert other.goal in interp.run_step(proc, other.candidate, {"s": 2})[2]


def test_missing_hit_terms_never_publish_a_complete_group_proof(monkeypatch):
    proc, env = problem()
    prepared, proved, calls = exact._LocalQueryPreparation(proc, env), set(), []
    encode = exact._encode_query_step
    supports = encoder.StepEncoding.supports
    monkeypatch.setattr(encoder.StepEncoding, "supports",
                        lambda self, goal: goal != "bad" and supports(self, goal))

    def missing(*args, **kwargs):
        encoded = encode(*args, **kwargs)
        calls.append(kwargs["keep"])
        return encoded

    monkeypatch.setattr(exact, "_encode_query_step", missing)
    for _ in range(2):
        answer = prepared.probe({"s": 4}, ("hit", "bad"), _proved_unsat=proved,
            timeout_ms=60_000)
        assert answer.outcome is probing.Outcome.UNSUPPORTED
        assert "bad" in answer.detail
        assert not proved
    assert len(calls) == 2


@pytest.mark.parametrize("state", range(5))
def test_resuming_a_disjunction_matches_exhaustive_inputs_and_a_fresh_query(monkeypatch, state):
    proc, env = two_occurrences()
    prepared, proved, checks = exact._LocalQueryPreparation(proc, env), set(), []
    solver_type = type(z3.Solver())
    check = solver_type.check

    def first_unknown(self, *args, **kwargs):
        checks.append(1)
        return z3.unknown if len(checks) == 1 else check(self, *args, **kwargs)

    monkeypatch.setattr(solver_type, "check", first_unknown)
    prepared.probe({"s": state}, ("goal",), _proved_unsat=proved,
        timeout_ms=60_000)
    resumed = prepared.probe({"s": state}, ("goal",), _proved_unsat=proved,
        timeout_ms=60_000)
    fresh = exact.local_hit_any(proc, env, {"s": state}, ("goal",),
        timeout_ms=60_000)
    reached = [a for a in range(4) if "goal" in interp.run_step(proc, {"a": a}, {"s": state})[2]]
    assert resumed.outcome is fresh.outcome
    assert (resumed.outcome is probing.Outcome.SAT) == bool(reached)
    if reached:
        assert resumed.candidate["a"] in reached
    else:
        assert resumed.outcome is probing.Outcome.LOCAL_UNSAT


def test_another_selection_does_not_borrow_completed_subqueries(monkeypatch):
    built, first = _selection(2)
    received = []

    def partial(*_args, proved_unsat, constructions, **_kwargs):
        received.append(set(proved_unsat))
        assert bool(constructions) == bool(proved_unsat)
        proved_unsat.add("a completed physical query")
        constructions["pending formula"] = object()
        return probing.ProbeResult(probing.Outcome.UNKNOWN, probing.Scope.EXACT_LOCAL_HIT)

    monkeypatch.setattr(jobs, "_local_hit", partial)
    # Native-path proposals precede unrestricted queries. This test injects
    # the unrestricted solver seam, not whichever heuristic is ranked first.
    request = next(item for item in first.requests.values()
                   if item.occurrence in first.occurrences).as_dict()
    first.probe(request, 1000)
    first.probe(request, 1000)
    second = jobs.SelectedTrace(built, first.seed.as_dict(), _trace(first.seed).as_dict(),
                                list(first.targets), preparation=first._query_preparation)
    second.probe(request, 1000)
    assert received == [set(), {"a completed physical query"}, set()]


def test_an_undeclared_goal_stays_unsupported_after_other_occurrences_are_proved():
    proc, env = two_occurrences()
    prepared, proved = exact._LocalQueryPreparation(proc, env), set()
    for _ in range(2):
        answer = prepared.probe({"s": 4}, ("goal", "missing"), _proved_unsat=proved,
            timeout_ms=60_000)
        assert answer.outcome is probing.Outcome.UNSUPPORTED
        assert "missing" in answer.detail and len(proved) == 2


@pytest.mark.parametrize("kind", ["range", "shape", "type", "capability", "goal"])
def test_completed_encoding_diagnostic_is_reused_but_not_a_goal_proof(monkeypatch, kind):
    proc, env = two_occurrences()
    prepared = exact._LocalQueryPreparation(proc, env)
    constructions, proved, encoded = {}, set(), []
    encode = exact._encode_query_step

    def first_unrepresented(*args, **kwargs):
        encoded.append(kwargs["keep"])
        if kwargs["keep"] == encoded[0]:
            raise encoder.EncodingError("fixture unrepresented occurrence", kind=kind)
        return encode(*args, **kwargs)

    monkeypatch.setattr(exact, "_encode_query_step", first_unrepresented)
    for _ in range(3):
        answer = prepared.probe({"s": 4}, ("goal",),
                                _constructions=constructions, _proved_unsat=proved,
            timeout_ms=60_000)
        assert answer.outcome is probing.Outcome.UNSUPPORTED
        assert "fixture unrepresented occurrence" in answer.detail
        assert answer.candidate is None
    assert len(encoded) == 2  # One refusal and one complete local UNSAT.
    assert len(proved) == 1   # The refused occurrence supplies no proof.
    # A new question starts with new construction/proof ownership, and can hit.
    other = prepared.probe({"s": 2}, ("goal",), _constructions={}, _proved_unsat=set(),
        timeout_ms=60_000)
    assert other.outcome is probing.Outcome.SAT
    assert other.goal in interp.run_step(proc, other.candidate, {"s": 2})[2]
    assert len(encoded) == 4


@pytest.mark.parametrize("kind", ["resource", None])
def test_resource_or_unclassified_encoding_failure_is_retried(monkeypatch, kind):
    proc, env = problem()
    prepared, constructions, calls = exact._LocalQueryPreparation(proc, env), {}, []
    encode = exact._encode_query_step

    def temporary(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise encoder.EncodingError("fixture interrupted construction", kind=kind)
        return encode(*args, **kwargs)

    monkeypatch.setattr(exact, "_encode_query_step", temporary)
    first = prepared.probe({"s": 0}, ("hit",), _constructions=constructions,
        timeout_ms=60_000)
    assert first.outcome is probing.Outcome.UNSUPPORTED
    assert not constructions
    second = prepared.probe({"s": 0}, ("hit",), _constructions=constructions,
        timeout_ms=60_000)
    assert second.outcome is probing.Outcome.SAT
    assert second.goal in interp.run_step(proc, second.candidate, {"s": 0})[2]
    assert len(calls) == 2


def test_an_incomplete_group_cannot_answer_a_later_larger_disjunction(monkeypatch):
    # bad is possible in the full carrier, but impossible at this pinned state:
    # the first partial group therefore needs an actual physical UNSAT query.
    proc = ir.Procedure(("s",), ("a",), ("i",), (), ir.For("i", 1, ir.Seq(
        ir.If(ir.Op("eq", ir.Var("s"), ir.Lit(1)), ir.Fail("Stopped", "bad"), ir.Skip()),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)), ir.Mark("hit"), ir.Skip()))),
        initial={"s": 0}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    env = {"s": ranges.Range(0, 1), "a": ranges.Range(0, 3)}
    prepared, proved = exact._LocalQueryPreparation(proc, env), set()
    cone, interrupted = prepared.cone, [True]

    def incomplete(goal, site):
        if interrupted[0] and goal == "hit":
            raise encoder.EncodingError("fixture incomplete occurrence planning")
        return cone(goal, site)

    monkeypatch.setattr(prepared, "cone", incomplete)
    first = prepared.probe({"s": 0}, ("bad", "hit"), _proved_unsat=proved,
        timeout_ms=60_000)
    assert first.outcome is probing.Outcome.UNSUPPORTED
    assert len(proved) == 1
    interrupted[0] = False
    second = prepared.probe({"s": 0}, ("bad", "hit"), _proved_unsat=proved,
        timeout_ms=60_000)
    # Both goals use the same command cone, but the earlier proof covered only
    # bad. Its group membership cannot stand in for the newly admitted hit.
    assert second.outcome is probing.Outcome.SAT
    assert second.goal == "hit"
    assert second.goal in interp.run_step(proc, second.candidate, {"s": 0})[2]


@pytest.mark.parametrize("component", ["state", "typed-state", "goals", "history", "typed-history", "domain"])
def test_selection_discards_partial_proofs_when_the_local_question_changes(monkeypatch, component):
    selected, occurrence, request, _calls = _key_fixture(monkeypatch)
    received = []

    def partial(*_args, proved_unsat, constructions, **_kwargs):
        received.append(set(proved_unsat))
        assert bool(constructions) == bool(proved_unsat)
        proved_unsat.add("a completed physical query")
        constructions["pending formula"] = object()
        return probing.ProbeResult(probing.Outcome.UNKNOWN, probing.Scope.EXACT_LOCAL_HIT)

    monkeypatch.setattr(jobs, "_local_hit", partial)
    selected.local_hit(occurrence, request, 1000)
    selected.local_hit(occurrence, request, 2000)
    assert received == [set(), {"a completed physical query"}]
    assert not selected._unsat_questions
    if component == "state":
        occurrence["state"]["array"][0][0] = 9
    elif component == "typed-state":
        occurrence["state"]["array"][0][0] = True
    elif component == "goals":
        request.targets = ("observed", "another-goal")
    elif component == "history":
        occurrence["previous"][("latency", "pending")] = (1, 1)
    elif component == "typed-history":
        occurrence["previous"]["input"] = (True,)
    else:
        selected.input_domain.context_key = lambda rank: (("input", (0,), ({"end": 1},)),)
    selected.local_hit(occurrence, request, 1000)
    assert received[-1] == set()


def test_public_query_cannot_consume_private_partial_proofs(monkeypatch):
    proc, env = two_occurrences()
    counts = []
    encode = exact._encode_query_step

    def counted(*args, **kwargs):
        counts.append(kwargs["keep"])
        return encode(*args, **kwargs)

    monkeypatch.setattr(exact, "_encode_query_step", counted)
    for _ in range(2):
        assert exact.local_hit_any(proc, env, {"s": 4}, ("goal",),
            timeout_ms=60_000).outcome is probing.Outcome.LOCAL_UNSAT
    assert len(counts) == 4
