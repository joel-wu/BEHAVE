"""A solver transcript carries exact calls, not best-effort Python images."""

import pytest

from behave.sim.core.query import QueryCall, SolverAnswer, decide_setwise


@pytest.mark.parametrize("witness", [
    [], [{"x": 1}], [{"x": [True, None, 1.5, "状态"]}],
    {"state": {"samples": [1, 2]}, "slots": [[0], [1]]},
])
@pytest.mark.parametrize("answer", list(SolverAnswer))
@pytest.mark.parametrize("measured", [False, True])
def test_verdict_record_roundtrip_preserves_native_and_json_carriers(witness, answer, measured):
    import json

    from behave.sim.core.instance import Verdict
    from behave.sim.core.query_telemetry import QueryKind, QueryTelemetry

    telemetry = (QueryTelemetry(
        backend="test", kind=QueryKind.SINGLETON, semantic_instance="bound:1",
        horizon=1, goals_asked=("g",), outcome=answer.value,
        goals_settled=() if answer is SolverAnswer.UNKNOWN else ("g",)) if measured else None)
    fields = ({"witness": witness} if answer is SolverAnswer.SAT else
              {"reason": "budget"} if answer is SolverAnswer.UNKNOWN else {})
    call = QueryCall(("g",), answer, telemetry=telemetry, **fields)
    verdict = Verdict(
        {"g": witness} if answer is SolverAnswer.SAT else {},
        ("g",) if answer is SolverAnswer.UNSAT else (),
        {"g": "budget"} if answer is SolverAnswer.UNKNOWN else {}, queries=(call,))
    image = verdict.as_record()
    assert image == json.loads(json.dumps(verdict.as_dict()))
    restored = Verdict.from_record(image)
    assert restored.as_dict() == verdict.as_dict()
    assert restored.as_record() == image
    assert restored.queries[0].as_dict() == call.as_dict()
    image["reachable"]["injected"] = {"state": "changed"}
    image["queries"].clear()
    assert restored.as_record() == verdict.as_record()


@pytest.mark.parametrize("field,value", [
    ("reachable", []), ("reachable", {"g": float("nan")}),
    ("reachable", {"g": ({"x": 1},)}),
    ("unreachable", ("g",)), ("unreachable", ["g", "g"]),
    ("unreachable", ["z", "a"]), ("unreachable", [False]),
    ("undecided", []), ("undecided", {"g": ""}),
    ("queries", ()), ("queries", [None]), ("queries", [{}]),
])
def test_verdict_record_rejects_noncanonical_fields(field, value):
    from behave.sim.core.instance import Verdict

    image = {"reachable": {}, "unreachable": [], "undecided": {}, "queries": []}
    image[field] = value
    with pytest.raises((TypeError, ValueError)):
        Verdict.from_record(image)


def test_legacy_aggregate_stays_native_not_a_complete_wire_record():
    from behave.sim.core.instance import Verdict

    verdict = Verdict({"g": ({"x": 1},)}, (), {})
    assert verdict.as_dict() == {
        "reachable": {"g": ({"x": 1},)}, "unreachable": [], "undecided": {}}
    with pytest.raises(TypeError):
        verdict.as_record()
    with pytest.raises(TypeError, match="complete finite fields"):
        Verdict.from_record(verdict.as_dict())


def test_logical_cache_strips_only_run_telemetry_from_the_shared_record():
    from behave.sim.core.instance import Verdict
    from behave.sim.core.query_cache import _query_from_image, _query_image
    from behave.sim.core.query_telemetry import QueryKind, QueryTelemetry

    telemetry = QueryTelemetry(
        backend="test", kind=QueryKind.SINGLETON, semantic_instance="bound:1",
        horizon=1, goals_asked=("g",), outcome="unsat", goals_settled=("g",))
    verdict = Verdict({}, ("g",), {}, queries=(QueryCall(
        ("g",), SolverAnswer.UNSAT, telemetry=telemetry, solver_calls=3),))
    image = verdict.as_record()
    with pytest.raises(TypeError, match="exact semantic fields"):
        _query_from_image(image)
    expected = verdict.as_record()
    expected["queries"][0].pop("telemetry")
    assert _query_image(verdict) == expected
    assert _query_from_image(expected).as_record() == expected
    assert verdict.as_record() == image


@pytest.mark.parametrize("answer,fields", [
    (SolverAnswer.SAT, {"witness": [{"x": 1}], "solver_calls": 2}),
    (SolverAnswer.UNSAT, {"solver_calls": 1}),
    (SolverAnswer.UNKNOWN, {"reason": "budget", "solver_calls": 0}),
])
@pytest.mark.parametrize("measured", [False, True])
def test_query_call_wire_round_trip_keeps_outcome_and_telemetry(answer, fields, measured):
    from behave.sim.core.query_telemetry import QueryKind, QueryTelemetry

    telemetry = None
    if measured:
        telemetry = QueryTelemetry(
            backend="test", kind=QueryKind.SINGLETON, semantic_instance="test:domain",
            horizon=3, goals_asked=("g",), outcome=answer.value,
            goals_settled=() if answer is SolverAnswer.UNKNOWN else ("g",))
    call = QueryCall(("g",), answer, telemetry=telemetry, **fields)
    image = call.as_dict()
    restored = QueryCall.from_dict(image)
    assert restored.as_dict() == image
    assert restored.answer is answer
    image["goals"].append("not-asked")
    if "witness" in image:
        image["witness"][0]["x"] = 99
    assert restored.as_dict() == call.as_dict()


@pytest.mark.parametrize("image", [
    None, [], {},
    {"goals": ["g"], "answer": "unsat", "solver_calls": 1, "extra": 0},
    {"goals": ["g"], "answer": "unknown", "solver_calls": 0},
    {"goals": ["g"], "answer": "sat", "solver_calls": 1},
    {"goals": ["g"], "answer": "sat", "solver_calls": 1, "witness": [], "reason": "bad"},
    {"goals": ["g"], "answer": "unsat", "solver_calls": True},
    {"goals": ["g"], "answer": "unsat", "solver_calls": 1, "telemetry": {}},
    {"goals": ["g"], "answer": "sat", "solver_calls": 1, "witness": float("nan")},
])
def test_query_call_wire_rejects_noncanonical_images(image):
    with pytest.raises((TypeError, ValueError)):
        QueryCall.from_dict(image)


def test_verdict_uses_the_record_owned_query_decoder(monkeypatch):
    from behave.sim.behavior import exact_service
    from behave.sim.core.instance import Verdict
    from behave.sim.rtl import analysis_job

    seen = []
    decode = QueryCall.from_dict

    def observed(cls, image):
        assert cls is QueryCall
        seen.append(image)
        return decode(image)

    monkeypatch.setattr(QueryCall, "from_dict", classmethod(observed))
    query = QueryCall(("g",), SolverAnswer.UNSAT).as_dict()
    image = {"reachable": {}, "unreachable": ["g"], "undecided": {}, "queries": [query]}
    result = Verdict.from_record(image)
    assert result.queries[0].as_dict() == query
    assert result.unreachable == {"g"}
    assert seen == [query]
    for module, names in ((exact_service, ("_decode_query_call", "_decode_witness", "_decode_verdict")),
                          (analysis_job, ("_query_call", "decode_audited_witness", "decode_query_verdict", "_query_image"))):
        assert all(not hasattr(module, name) for name in names)


def test_setwise_query_uses_one_sat_model_for_all_hits_then_the_residual_set():
    calls = []

    def solve(goals):
        calls.append(goals)
        if goals == ("a", "b", "c"):
            return QueryCall(goals, SolverAnswer.SAT, witness=[{"x": 1}]), \
                ("a", "c")
        return QueryCall(goals, SolverAnswer.UNSAT), ()

    got = decide_setwise(("c", "a", "b"), solve)

    assert calls == [("a", "b", "c"), ("b",)]
    assert got.reachable == {"a": ({"x": 1},), "c": ({"x": 1},)}
    assert got.unreachable == {"b"}
    assert got.undecided == {}
    assert [call.goals for call in got.queries] == calls


def test_only_an_unknown_set_query_falls_back_to_singletons():
    calls = []

    def solve(goals):
        calls.append(goals)
        if len(goals) > 1:
            return QueryCall(
                goals, SolverAnswer.UNKNOWN, reason="set timeout"), ()
        if goals == ("a",):
            return QueryCall(
                goals, SolverAnswer.SAT, witness=[{"x": 0}]), ("a",)
        if goals == ("b",):
            return QueryCall(goals, SolverAnswer.UNSAT), ()
        return QueryCall(
            goals, SolverAnswer.UNKNOWN, reason="singleton timeout"), ()

    got = decide_setwise(("a", "b", "c"), solve)

    assert calls == [
        ("a", "b", "c"), ("a",), ("b",), ("c",),
    ]
    assert got.reachable == {"a": ({"x": 0},)}
    assert got.unreachable == {"b"}
    assert got.undecided == {"c": "singleton timeout"}


def test_setwise_query_refuses_a_sat_answer_without_a_true_asked_goal():
    with pytest.raises(ValueError, match="at least one"):
        decide_setwise(
            ("a", "b"),
            lambda goals: (
                QueryCall(goals, SolverAnswer.SAT, witness=[{"x": 1}]), ()))


@pytest.mark.parametrize('earlier_hit', [False, True])
def test_singleton_unknown_is_not_submitted_twice(earlier_hit):
    calls = []

    def solve(goals):
        calls.append(goals)
        if len(goals) > 1:
            return QueryCall(goals, SolverAnswer.SAT, witness=[{"x": 1}]), ('a',)
        return QueryCall(goals, SolverAnswer.UNKNOWN, reason='bounded query unresolved'), ()

    result = decide_setwise(('a', 'b') if earlier_hit else ('b',), solve)
    assert calls == ([('a', 'b'), ('b',)] if earlier_hit else [('b',)])
    assert result.undecided == {'b': 'bounded query unresolved'}
    assert not result.unreachable
    assert len(result.queries) == len(calls)


def test_setwise_query_refuses_duplicate_model_hits():
    def solve(goals):
        return (
            QueryCall(goals, SolverAnswer.SAT, witness=[{"x": 1}]),
            ("a", "a"),
        )

    with pytest.raises(ValueError, match="duplicates"):
        decide_setwise(("a",), solve)


def test_setwise_query_refuses_mixed_goal_types_without_sorting_them():
    with pytest.raises(ValueError, match="exact goal names"):
        decide_setwise(("a", 1), lambda _goals: None)


def test_incremental_setwise_returns_before_the_slow_residual():
    from behave.sim.core.query import iter_decide_setwise

    calls = []
    def solve(goals):
        calls.append(goals)
        if goals == ('a', 'b'):
            return QueryCall(goals, SolverAnswer.SAT, witness=[{'x': 1}]), ('a',)
        raise TimeoutError('unfinished residual')

    parts = iter_decide_setwise(('a', 'b'), solve)
    first = next(parts)
    assert calls == [('a', 'b')]
    assert set(first.reachable) == {'a'} and first.queries[0].goals == ('a', 'b')
    with pytest.raises(TimeoutError):
        next(parts)
    assert calls == [('a', 'b'), ('b',)]


def test_incremental_unknown_set_has_no_invented_per_goal_status():
    from behave.sim.core.query import iter_decide_setwise, collect_verdicts

    def solve(goals):
        if len(goals) == 2:
            return QueryCall(goals, SolverAnswer.UNKNOWN, reason='set unresolved'), ()
        return QueryCall(goals, SolverAnswer.UNSAT), ()

    parts = list(iter_decide_setwise(('a', 'b'), solve))
    assert not parts[0].reachable and not parts[0].unreachable and not parts[0].undecided
    assert collect_verdicts(parts).as_record() == decide_setwise(('a', 'b'), solve).as_record()


def test_sat_witness_is_a_detached_strict_json_value():
    witness = [{"a": 1, "nested": [True, 2.5, None]}]
    call = QueryCall(("g",), SolverAnswer.SAT, witness=witness)

    witness[0]["a"] = 99
    returned = call.witness
    returned[0]["nested"].append("changed")

    assert call.witness == [{"a": 1, "nested": [True, 2.5, None]}]
    assert call.as_dict()["witness"] == call.witness


@pytest.mark.parametrize("number", [
    float("nan"), float("inf"), -float("inf"),
])
def test_sat_witness_refuses_non_finite_numbers(number):
    with pytest.raises(ValueError, match="non-finite float"):
        QueryCall(("g",), SolverAnswer.SAT, witness=[{"a": number}])


def test_sat_witness_refuses_non_string_mapping_keys_instead_of_coercing_them():
    with pytest.raises(TypeError, match="non-string mapping key"):
        QueryCall(
            ("g",), SolverAnswer.SAT,
            witness=[{1: "integer", "1": "string"}],
        )


def test_sat_witness_refuses_python_only_sequence_carriers():
    with pytest.raises(TypeError, match="tuple.*no strict JSON image"):
        QueryCall(("g",), SolverAnswer.SAT, witness=({"a": 1},))


def test_sat_witness_never_uses_repr_as_a_serialisation_surrogate():
    class PretendsToBeJSON:
        def __repr__(self):
            raise AssertionError("the evidence boundary must not call repr")

    with pytest.raises(TypeError, match="PretendsToBeJSON.*no strict JSON image"):
        QueryCall(("g",), SolverAnswer.SAT, witness=[PretendsToBeJSON()])


def test_sat_witness_refuses_reference_cycles():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="reference cycle"):
        QueryCall(("g",), SolverAnswer.SAT, witness=cycle)


def test_query_identifiers_and_reasons_are_not_string_coerced():
    class LooksLikeAString:
        def __str__(self):
            return "g"

    with pytest.raises(ValueError, match="nonempty set of goals"):
        QueryCall((LooksLikeAString(),), SolverAnswer.UNSAT)
    with pytest.raises(TypeError, match="reason must be a string"):
        QueryCall(
            ("g",), SolverAnswer.UNKNOWN, reason=LooksLikeAString(),
        )


def test_query_call_is_closed_and_does_not_consume_arbitrary_iterators():
    with pytest.raises(TypeError, match="cannot be subclassed"):
        class ForgedCall(QueryCall):
            pass

    with pytest.raises(TypeError, match="finite built-in goal set"):
        QueryCall((goal for goal in ("g",)), SolverAnswer.UNSAT)


def test_an_unlaunched_budget_query_is_distinct_from_a_solver_answer():
    call = QueryCall(
        ("g",), SolverAnswer.UNKNOWN,
        reason="the artifact budget was already exhausted", solver_calls=0)

    assert call.as_dict() == {
        "goals": ["g"],
        "answer": "unknown",
        "solver_calls": 0,
        "reason": "the artifact budget was already exhausted",
    }
    assert QueryCall(
        ("g",), SolverAnswer.SAT, witness=[], solver_calls=0
    ).solver_calls == 0
    assert QueryCall(
        ("g",), SolverAnswer.UNSAT, solver_calls=0
    ).solver_calls == 0
    for invalid in (-1, True, 1.5):
        with pytest.raises(ValueError, match="natural solver calls"):
            QueryCall(
                ("g",), SolverAnswer.UNKNOWN, reason="budget",
                solver_calls=invalid)


def test_solver_answer_is_closed_and_not_a_truth_value():
    with pytest.raises(TypeError, match="SolverAnswer"):
        QueryCall(("g",), "sat", witness=[])
    with pytest.raises(TypeError, match="not a truth value"):
        bool(SolverAnswer.SAT)
