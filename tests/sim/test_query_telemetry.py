import json
from types import SimpleNamespace

import pytest

from behave.sim.core.conditional_evidence import QueryEvidence
from behave.sim.core.instance import Feasible
from behave.sim.core.query import FeasibilityCall, QueryCall, SolverAnswer
from behave.sim.core.query_telemetry import (
    CacheDisposition,
    FormulaShape,
    QueryKind,
    QueryTelemetry,
    QueryTelemetryFragment,
    QueryTelemetryScope,
    StageTimings,
    analysis_semantic_instance,
)
from behave.sim.rtl.analysis_job import decode_analysis_reply as decode_rtl_reply


def _telemetry(**overrides):
    values = {
        "backend": "behavior-z3",
        "kind": QueryKind.SINGLETON,
        "semantic_instance": "domain:abc",
        "horizon": 4,
        "goals_asked": ("g",),
        "goals_settled": ("g",),
        "outcome": "unsat",
        "shape": FormulaShape(
            full_assertions=7, full_nodes=31, scalar_bits=12,
            scalar_cells=3, smt_arrays=1),
        "timings": StageTimings({"solver_load_ns": 11, "solve_ns": 23}),
        "peak_rss_bytes": 4096,
        "provenance": {"tool": "z3", "timeout_ms": 100},
    }
    values.update(overrides)
    return QueryTelemetry(**values)


def _fragment(**overrides):
    values = {
        "backend": "behavior-z3",
        "kind": QueryKind.SET,
        "semantic_instance": "domain:abc",
        "horizon": 4,
        "goals_asked": ("g0", "g1"),
        "shape": FormulaShape(full_assertions=7, full_nodes=31),
    }
    values.update(overrides)
    return QueryTelemetryFragment(**values)


def test_query_telemetry_has_a_canonical_round_trip():
    telemetry = _telemetry(
        cache_identity="cache:one", cache=CacheDisposition.MISS)

    assert QueryTelemetry.from_dict(telemetry.as_dict()).as_dict() \
        == telemetry.as_dict()


def test_disjoint_stage_fragments_cross_json_and_attach_at_call_creation():
    frontend = _fragment(
        shape=FormulaShape(
            full_assertions=7, full_nodes=31, full_text_bytes=201),
        timings=StageTimings({"frontend_ns": 3, "lowering_ns": 5}))
    worker = _fragment(
        shape=FormulaShape(
            sliced_assertions=4, sliced_nodes=17, sliced_text_bytes=109,
            scalar_bits=12, scalar_cells=3, smt_arrays=1),
        timings=StageTimings({
            "slice_ns": 7, "encode_unroll_ns": 11, "solve_ns": 13,
        }))
    outer = _fragment(
        shape=None,
        timings=StageTimings({
            "worker_start_ns": 17, "ipc_ns": 19, "total_ns": 23,
        }))

    across_wire = QueryTelemetryFragment.from_dict(
        json.loads(json.dumps(worker.as_dict())))
    measured = frontend.merge(across_wire, outer)
    telemetry = measured.finalize(
        outcome="unsat", goals_settled=("g0", "g1"))
    call = QueryCall(("g0", "g1"), SolverAnswer.UNSAT, telemetry=telemetry)

    assert call.telemetry.shape.as_dict() == {
        "full_assertions": 7,
        "full_nodes": 31,
        "full_text_bytes": 201,
        "sliced_assertions": 4,
        "sliced_nodes": 17,
        "sliced_text_bytes": 109,
        "scalar_bits": 12,
        "scalar_cells": 3,
        "smt_arrays": 1,
    }
    assert call.telemetry.timings.as_dict() == {
        "frontend_ns": 3,
        "lowering_ns": 5,
        "slice_ns": 7,
        "encode_unroll_ns": 11,
        "solve_ns": 13,
        "worker_start_ns": 17,
        "ipc_ns": 19,
        "total_ns": 23,
    }
    assert frontend.shape.as_dict() == {
        "full_assertions": 7,
        "full_nodes": 31,
        "full_text_bytes": 201,
    }


def test_measurement_merge_rejects_duplicate_owners_and_identity_mismatch():
    base = _fragment(
        timings=StageTimings({"frontend_ns": 3}), shape=None)

    with pytest.raises(ValueError, match="more than one owner"):
        base.merge(_fragment(
            timings=StageTimings({"frontend_ns": 3}), shape=None))
    with pytest.raises(ValueError, match="more than one owner"):
        _fragment().merge(_fragment(shape=FormulaShape(full_nodes=31)))
    with pytest.raises(ValueError, match="different logical queries"):
        base.merge(_fragment(
            semantic_instance="domain:other",
            timings=StageTimings({"solve_ns": 5}), shape=None))


def test_complete_telemetry_accepts_only_disjoint_matching_fragments():
    telemetry = _fragment(
        timings=StageTimings({"solve_ns": 5}), shape=None).finalize(
            outcome="unknown")
    enriched = telemetry.with_measurements(_fragment(
        timings=StageTimings({"ipc_ns": 7}), shape=None))

    assert enriched.timings.as_dict() == {"solve_ns": 5, "ipc_ns": 7}
    with pytest.raises(ValueError, match="more than one owner"):
        enriched.with_measurements(_fragment(
            timings=StageTimings({"ipc_ns": 7}), shape=None))
    with pytest.raises(ValueError, match="different logical query"):
        enriched.with_measurements(_fragment(
            horizon=5, timings=StageTimings({"total_ns": 11}), shape=None))


def test_worker_scope_and_runtime_enrichment_are_closed_and_wire_safe():
    scope = QueryTelemetryScope(
        backend="behavior-z3", semantic_instance="domain:abc", horizon=4)
    fragment = scope.fragment(
        ("g",), shape=FormulaShape(full_assertions=2),
        timings=StageTimings({"solve_ns": 7}))
    across_wire = QueryTelemetryFragment.from_dict(
        json.loads(json.dumps(fragment.as_dict())))
    telemetry = across_wire.finalize(outcome="unsat", goals_settled=("g",))
    enriched = telemetry.with_runtime(
        peak_rss_bytes=8192, provenance={"tool": "z3"})

    assert enriched.as_dict()["formula"] == {"full_assertions": 2}
    assert enriched.as_dict()["timings_ns"] == {"solve_ns": 7}
    assert enriched.peak_rss_bytes == 8192
    with pytest.raises(ValueError, match="owning measurement boundary"):
        enriched.with_runtime(peak_rss_bytes=16384)
    with pytest.raises(ValueError, match="owning boundary"):
        enriched.with_runtime(provenance={"tool": "other"})


@pytest.mark.parametrize("kind,goals", [
    (QueryKind.LOCAL_PROBE, ("g",)),
    (QueryKind.FEASIBILITY, ()),
    (QueryKind.SET, ("g0", "g1")),
    (QueryKind.SINGLETON, ("g",)),
])
@pytest.mark.parametrize("outcome", ["sat", "unsat", "unknown", "refused"])
def test_every_query_role_and_outcome_has_one_canonical_wire_image(
        kind, goals, outcome):
    telemetry = QueryTelemetry(
        backend="test-z3", kind=kind, semantic_instance="domain:test",
        horizon=3, goals_asked=goals, outcome=outcome)

    assert QueryTelemetry.from_dict(telemetry.as_dict()).as_dict() \
        == telemetry.as_dict()


def test_unmeasured_fields_are_absent_rather_than_invented_zeroes():
    telemetry = QueryTelemetry(
        backend="rtl-z3", kind=QueryKind.FEASIBILITY,
        semantic_instance="domain:rtl", horizon=2, outcome="unknown")

    image = telemetry.as_dict()
    assert "formula" not in image
    assert "timings_ns" not in image
    assert "peak_rss_bytes" not in image
    assert "cache" not in image


def test_every_semantic_component_invalidates_the_analysis_instance():
    components = {
        "artifact": "artifact:a",
        "task_contract": "task:a",
        "domain": "domain:a",
        "catalogue": "catalogue:a",
        "representation": "representation:a",
        "encoding": "encoding:a",
    }
    baseline = analysis_semantic_instance(**components)

    for name in components:
        changed = dict(components)
        changed[name] += ":changed"
        assert analysis_semantic_instance(**changed) != baseline, name


def test_cache_result_cannot_exist_without_its_semantic_identity():
    with pytest.raises(ValueError, match="appear together"):
        _telemetry(cache=CacheDisposition.HIT)


def test_invalid_measurements_and_recursive_provenance_are_rejected():
    with pytest.raises(ValueError, match="natural nanoseconds"):
        StageTimings({"solve_ns": -1})
    with pytest.raises(TypeError, match="unknown formula-shape fields"):
        FormulaShape(formula_size=3)
    cyclic = {}
    cyclic["again"] = cyclic
    with pytest.raises(ValueError, match="reference cycle"):
        _telemetry(provenance=cyclic)


def test_query_call_wire_carries_telemetry_but_semantic_answer_stays_closed():
    call = QueryCall(("g",), SolverAnswer.UNSAT, telemetry=_telemetry())
    image = call.as_dict()
    restored = QueryCall.from_dict(image)

    assert restored.as_dict() == image
    with pytest.raises(ValueError, match="solver answer disagree"):
        QueryCall(("g",), SolverAnswer.SAT, witness=[], telemetry=_telemetry())


def test_query_evidence_identity_excludes_performance_measurements():
    first = QueryEvidence(
        0, ("g",), SolverAnswer.UNSAT, telemetry=_telemetry())
    second = QueryEvidence(
        0, ("g",), SolverAnswer.UNSAT,
        telemetry=_telemetry(
            timings=StageTimings({"solve_ns": 999}),
            peak_rss_bytes=8192))

    assert first.query_id == second.query_id
    assert first.as_dict()["telemetry"] != second.as_dict()["telemetry"]


def test_replay_audit_enrichment_retains_the_original_measurements():
    before = _telemetry(
        outcome="sat", goals_settled=(),
        timings=StageTimings({"solve_ns": 23}))
    after = before.with_settled(("g",), replay_audit_ns=17)

    assert after.goals_settled == ("g",)
    assert after.timings.as_dict() == {"solve_ns": 23, "replay_audit_ns": 17}

    with pytest.raises(ValueError, match="more than one owner"):
        after.with_settled(("g",), replay_audit_ns=19)


def test_only_sat_replay_can_add_settled_goals():
    with pytest.raises(ValueError, match="refused query settles no goals"):
        _telemetry(outcome="refused")
    with pytest.raises(ValueError, match="only a SAT query"):
        _telemetry().with_settled(("g",))


def test_bounded_query_evidence_rejects_local_probe_measurements():
    telemetry = _telemetry(kind=QueryKind.LOCAL_PROBE)

    with pytest.raises(ValueError, match="local-probe telemetry"):
        QueryEvidence(0, ("g",), SolverAnswer.UNSAT, telemetry=telemetry)


def test_feasibility_call_is_typed_serializable_and_pair_compatible():
    telemetry = QueryTelemetry(
        backend="rtl-z3", kind=QueryKind.FEASIBILITY,
        semantic_instance="domain:rtl", horizon=3, outcome="sat",
        timings=StageTimings({"total_ns": 42}))
    call = FeasibilityCall(Feasible.YES, [{"a": 1}], telemetry=telemetry)

    answer, detail = call
    assert answer is Feasible.YES and detail == [{"a": 1}]
    assert call[0] is Feasible.YES
    assert call.as_dict()["telemetry"] == telemetry.as_dict()


def test_a_refused_query_keeps_its_typed_telemetry_across_the_rtl_wire():
    from behave.sim.rtl.analysis_job import VERSION

    telemetry = _telemetry(
        outcome="refused", goals_settled=(), shape=None, timings=None,
        peak_rss_bytes=None)
    image = {
        "version": VERSION,
        "operation": "query",
        "status": "refused",
        "refusal": {
            "kind": "test-query-refused",
            "why": "the solver service was unavailable",
            "detail": {"query_telemetry": telemetry.as_dict()},
        },
    }

    refusal = decode_rtl_reply(image, "query")
    restored = QueryTelemetry.from_dict(refusal.detail["query_telemetry"])
    assert restored.outcome == "refused"
    assert restored.semantic_instance == telemetry.semantic_instance


@pytest.mark.parametrize("module_name", [
    "behave.sim.behavior.analysis_job",
    "behave.sim.rtl.analysis_job",
])
def test_instrumentation_failure_cannot_change_a_solver_answer(
        module_name, monkeypatch):
    import importlib

    from behave.sim.core import query_telemetry

    module = importlib.import_module(module_name)
    call = QueryCall(("g",), SolverAnswer.UNSAT)

    def broken(_exact):
        raise RuntimeError("instrumentation is unavailable")

    monkeypatch.setattr(query_telemetry, "semantic_instance_of", broken)
    backend = "behavior-z3" if ".behavior." in module_name else "rtl-z3"
    assert query_telemetry.annotate_query(
        call, object(), None, backend=backend, provenance=module._tool_provenance) is call


@pytest.fixture
def worker_exact(monkeypatch):
    from behave.sim.core import query_telemetry

    monkeypatch.setattr(query_telemetry, "process_peak_rss_bytes", lambda: 8192)
    monkeypatch.setattr(query_telemetry.time, "monotonic_ns", lambda: 1000)
    return SimpleNamespace(
        domain=SimpleNamespace(artifact="artifact", task_contract="contract",
                               bound=4, digest=lambda: "domain"),
        evidence_identity=lambda: {"representation": "representation", "encoding": "encoding"},
        catalogue_manifest=lambda: SimpleNamespace(digest=lambda: "catalogue"))


@pytest.mark.parametrize("backend", ["behavior-z3", "rtl-z3"])
@pytest.mark.parametrize("answer", list(SolverAnswer))
@pytest.mark.parametrize("goals", [("g",), ("g", "h")])
def test_worker_query_measurements_preserve_every_answer(worker_exact, backend, answer, goals):
    from behave.sim.core.query_telemetry import annotate_query, semantic_instance_of

    options = {"witness": {"inputs": [[3]]}} if answer is SolverAnswer.SAT else {}
    if answer is SolverAnswer.UNKNOWN:
        options["reason"] = "solver budget"
    call = QueryCall(goals, answer, solver_calls=2, **options)
    got = annotate_query(call, worker_exact, 31, backend=backend,
                         provenance=lambda timeout: {"timeout_ms": timeout})
    assert {key: value for key, value in got.as_dict().items() if key != "telemetry"} == call.as_dict()
    assert got.telemetry.backend == backend
    assert got.telemetry.semantic_instance == semantic_instance_of(worker_exact)
    assert got.telemetry.horizon == 4 and got.telemetry.peak_rss_bytes == 8192
    expected = goals if answer is SolverAnswer.UNSAT or (
        answer is SolverAnswer.UNKNOWN and len(goals) == 1) else ()
    assert got.telemetry.goals_settled == expected
    assert got.telemetry.provenance == {"timeout_ms": 31}


@pytest.mark.parametrize("backend", ["behavior-z3", "rtl-z3"])
def test_worker_measurements_preserve_existing_fragments_and_owners(worker_exact, backend):
    from behave.sim.core.query_telemetry import annotate_query

    telemetry = _telemetry(backend=backend, peak_rss_bytes=None, provenance={})
    call = QueryCall(("g",), SolverAnswer.UNSAT, telemetry=telemetry)
    got = annotate_query(call, object(), None, backend=backend,
                         provenance=lambda timeout: {"tool": "z3"})
    assert got.telemetry.shape == telemetry.shape
    assert got.telemetry.timings == telemetry.timings
    assert got.telemetry.semantic_instance == telemetry.semantic_instance
    assert got.telemetry.peak_rss_bytes == 8192
    assert got.telemetry.provenance == {"tool": "z3"}
    assert annotate_query(got, worker_exact, None, backend=backend,
                          provenance=lambda timeout: {"tool": "other"}) is got


@pytest.mark.parametrize("backend", ["behavior-z3", "rtl-z3"])
@pytest.mark.parametrize("operation", ["feasible", "query"])
@pytest.mark.parametrize("timeout", [None, 0, -1, True, "10", 10])
def test_worker_refusal_keeps_reason_and_normalizes_only_telemetry_timeout(
        worker_exact, backend, operation, timeout):
    from behave.sim.core.query_telemetry import annotate_refusal
    from behave.sim.core.refusal import Refusal

    original = Refusal("test-refused", "not supported", detail="original diagnostic")
    got = annotate_refusal(original, operation, worker_exact,
                           {"goals": ["g"], "timeout_ms": timeout}, 100,
                           backend=backend, provenance=lambda value: {"timeout_ms": value})
    assert got is not original and got.kind == original.kind and got.why == original.why
    assert got.detail["detail"] == original.detail["detail"]
    measured = QueryTelemetry.from_dict(got.detail["query_telemetry"])
    assert measured.outcome == "refused" and measured.backend == backend
    assert measured.timings.as_dict() == {"total_ns": 900}
    assert measured.goals_asked == (() if operation == "feasible" else ("g",))
    assert measured.provenance == {"timeout_ms": 10 if type(timeout) is int and timeout > 0 else None}
    assert "query_telemetry" not in original.detail


@pytest.mark.parametrize("goals", [None, [], "g", [""], [1], ["g", "g"]])
def test_invalid_refused_goal_request_is_not_reinterpreted(worker_exact, goals):
    from behave.sim.core.query_telemetry import annotate_refusal
    from behave.sim.core.refusal import Refusal

    refusal = Refusal("invalid-request", "invalid goal list")
    for operation, exact in [("query", worker_exact), ("bind", worker_exact), ("query", None)]:
        assert annotate_refusal(
            refusal, operation, exact, {"goals": goals}, 100,
            backend="rtl-z3", provenance=lambda value: {}) is refusal


@pytest.mark.parametrize("backend", ["behavior-z3", "rtl-z3"])
def test_broken_tool_metadata_cannot_change_query_refusal_or_verdict(worker_exact, backend):
    from behave.sim.core.instance import Verdict
    from behave.sim.core.query_telemetry import annotate_query, annotate_refusal, annotate_verdict
    from behave.sim.core.refusal import Refusal

    def broken(_timeout):
        raise RuntimeError("tool metadata unavailable")

    call = QueryCall(("g",), SolverAnswer.UNSAT)
    refusal = Refusal("test-refused", "diagnostic", stage="solve")
    assert annotate_query(call, worker_exact, 30, backend=backend, provenance=broken) is call
    assert annotate_refusal(
        refusal, "query", worker_exact, {"goals": ["g"]}, 100,
        backend=backend, provenance=broken) is refusal
    verdict = Verdict({}, {"g"}, {}, queries=(call,))
    got = annotate_verdict(verdict, worker_exact, 30, backend=backend, provenance=broken)
    assert got.reachable == verdict.reachable and got.unreachable == verdict.unreachable
    assert got.undecided == verdict.undecided and got.queries == (call,)
