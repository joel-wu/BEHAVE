"""Unknown encoding queries are unavailable audits, not semantic disagreements."""

import pickle
from concurrent.futures import ProcessPoolExecutor

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.closure import QueryRefused
from behave.sim.core.conditional_evidence import QueryEvidence
from behave.sim.core.failure import CandidateFault, FailureRaised, RunFailure
from behave.sim.core.query import SolverAnswer
from behave.sim.core.refusal import Refusal, Refused

from .test_encoding_plan import PLAIN, instance


def _unavailable_worker():
    raise Refused(Refusal(
        "behavior-step-audit-unresolved", "the worker query is unresolved",
        solver={"answer": "unknown", "timeout_ms": 100}))


def test_worker_refusal_survives_exception_transport():
    refusal = Refusal("behavior-step-audit-unresolved", "an unresolved query",
                      solver={"answer": "unknown", "timeout_ms": 100})
    decoded = pickle.loads(pickle.dumps(Refused(refusal)))
    assert type(decoded) is Refused and decoded.refusal == refusal
    with ProcessPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_unavailable_worker)
        with pytest.raises(Refused) as raised:
            future.result(timeout=30)
    assert raised.value.refusal.kind == "behavior-step-audit-unresolved"
    assert raised.value.refusal.as_dict()["detail"]["solver"]["answer"] == "unknown"


@pytest.mark.parametrize("kind", ["candidate", "query", "run"])
def test_typed_exception_reconstruction_preserves_subtype_and_evidence(kind):
    reason = Refusal("test-stopped", "a typed stop", requested={"goal": "g"})
    query = QueryEvidence(0, ("g",), SolverAnswer.UNKNOWN, reason="test budget")
    error = {
        "candidate": CandidateFault(reason),
        "query": QueryRefused(reason, (query,)),
        "run": FailureRaised(RunFailure.verifier("test-audit", reason)),
    }[kind]
    decoded = pickle.loads(pickle.dumps(error))
    assert type(decoded) is type(error) and str(decoded) == str(error)
    if kind == "run":
        assert decoded.failure.as_dict() == error.failure.as_dict()
    else:
        assert decoded.refusal == reason
    if kind == "query":
        assert [item.as_dict() for item in decoded.queries] == [query.as_dict()]


@pytest.mark.parametrize("bound", [None, 2])
def test_behavior_step_audit_preserves_unknown_and_retries(monkeypatch, bound):
    model = instance(PLAIN, bound=bound)
    traces = (({"a": 1}, {"a": 2}),)
    assert model.audit(traces).status is AuditCheckStatus.AGREED
    original, calls = z3.Solver.check, []

    def check(solver, *args, **kwargs):
        calls.append(None)
        return z3.unknown if len(calls) == 1 else original(solver, *args, **kwargs)

    monkeypatch.setattr(z3.Solver, "check", check)
    monkeypatch.setattr(z3.Solver, "reason_unknown", lambda _self: "audit-test-budget")
    result = model.audit(traces)
    assert result.status is AuditCheckStatus.REFUSED, result.as_dict()
    assert result.kind == "behavior-step-audit-unresolved"
    assert "audit-test-budget" in result.why
    assert len(calls) == 1
    assert model.audit(traces).status is AuditCheckStatus.AGREED


@pytest.mark.parametrize("bound", [None, 2])
def test_behavior_step_audit_still_detects_a_genuinely_empty_encoding(monkeypatch, bound):
    model = instance(PLAIN, bound=bound)
    traces = (({"a": 1},),)
    assert model.audit(traces).status is AuditCheckStatus.AGREED
    original = z3.Solver

    def empty_encoding():
        solver = original()
        solver.add(z3.BoolVal(False))
        return solver

    monkeypatch.setattr(z3, "Solver", empty_encoding)
    result = model.audit(traces)
    assert result.status is AuditCheckStatus.INCONSISTENT, result.as_dict()
    assert any("no run" in finding for finding in result.findings)


def test_atomic_behavior_audit_preserves_unknown_and_retries(monkeypatch):
    from .test_behavior_atomic_exact import _instance, _rows

    model = _instance()
    traces = (_rows(),)
    assert model.audit(traces).status is AuditCheckStatus.AGREED
    original, calls = z3.Solver.check, []

    def check(solver, *args, **kwargs):
        calls.append(None)
        return z3.unknown if len(calls) == 1 else original(solver, *args, **kwargs)

    monkeypatch.setattr(z3.Solver, "check", check)
    monkeypatch.setattr(z3.Solver, "reason_unknown", lambda _self: "atomic-test-budget")
    result = model.audit(traces)
    assert result.status is AuditCheckStatus.REFUSED, result.as_dict()
    assert result.kind == "behavior-step-audit-unresolved"
    assert "atomic-test-budget" in result.why
    assert len(calls) == 1
    assert model.audit(traces).status is AuditCheckStatus.AGREED


def test_atomic_behavior_audit_rejects_an_empty_encoding_as_inconsistent(monkeypatch):
    from .test_behavior_atomic_exact import _instance, _rows

    model = _instance()
    traces = (_rows(),)
    assert model.audit(traces).status is AuditCheckStatus.AGREED
    original = z3.Solver

    def empty_encoding():
        solver = original()
        solver.add(z3.BoolVal(False))
        return solver

    monkeypatch.setattr(z3, "Solver", empty_encoding)
    result = model.audit(traces)
    assert result.status is AuditCheckStatus.INCONSISTENT, result.as_dict()
    assert any("no run" in finding for finding in result.findings)


@pytest.mark.parametrize("entrypoint", ["audit", "source_audit"])
def test_atomic_audits_compare_typed_witness_values(monkeypatch, entrypoint):
    from behave.sim.behavior.atomic_exact import AtomicBehaviorInstance
    from behave.sim.core.audit import AuditedWitness

    from .test_behavior_atomic_exact import _instance, _rows

    model = _instance()
    traces = (_rows(),)
    assert getattr(model, entrypoint)(traces).status is AuditCheckStatus.AGREED
    original = AtomicBehaviorInstance._semantic_witness

    def changed(self, *args, **kwargs):
        witness = original(self, *args, **kwargs)
        image = witness.as_dict()
        # This coordinate is Integer 1, not a Boolean, in the validated request.
        assert type(image["inputs"][0]["a.a"]) is int
        assert image["inputs"][0]["a.a"] == 1
        image["inputs"][0]["a.a"] = True
        altered = AuditedWitness(**image)
        assert altered.as_dict() == witness.as_dict()  # Python loses the type.
        assert altered.fingerprint != witness.fingerprint
        return altered

    monkeypatch.setattr(AtomicBehaviorInstance, "_semantic_witness", changed)
    got = getattr(model, entrypoint)(traces)
    assert got.status is AuditCheckStatus.INCONSISTENT, got.as_dict()
