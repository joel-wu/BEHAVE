"""The production handoff from a decided score to bounded analysis.

Concrete evaluation and bounded classification answer different questions.  The
former owns the score and is persisted first; the latter is an annotation bound to
that exact immutable evaluation.  This module is deliberately small so neither the
legacy orchestrator nor the analysis coordinator can turn one result into the other.

An exact provider is optional at this boundary.  A supported path supplies the bound
instance; an unsupported post-score construction supplies its exact typed
``exact_refusal``.  Omitting both produces the generic missing-capability refusal.  All
three cases preserve the same ordering and the same concrete score bytes.
"""

from __future__ import annotations

from . import analysis_service
from .analysis_budget import AnalysisStopped, analysis_scope
from .authority import Authoritative
from .core.outcome import AnalysisOutcome, ConcreteEvaluation
from .core.stimulus import canonical_json
from .result_store import ResultStore

__all__ = ["analyse_after_decision", "analyse_evaluation"]


def analyse_after_decision(store, authoritative, *, exact=None,
                           exact_refusal=None, analysis_request=None,
                           record_audit=None,
                           goal_policy=None,
                           feasibility_timeout_ms=None,
                           query_timeout_ms=None, batch=None, wall_timeout_seconds=None):
    """Analyse the exact evaluation already persisted by ``store``.

    Returns ``None`` for a result with no concrete evaluation (for example an
    admission refusal).  Otherwise the same :class:`ConcreteEvaluation` object held by
    ``authoritative`` is passed to the public coordinator and the resulting exact
    :class:`AnalysisOutcome` is attached to the terminal report.

    The terminal check is part of the API, not merely an orchestration convention:
    analysis is allowed to time out or fail without being able to erase or replace an
    already-decided score.
    """
    if type(store) is not ResultStore:
        raise TypeError(
            "the production analysis handoff requires the invocation's ResultStore")
    if type(authoritative) is not Authoritative:
        raise TypeError(
            "the production analysis handoff requires the exact Authoritative result")

    evaluation = authoritative.evaluation
    if evaluation is None:
        return None
    if type(evaluation) is not ConcreteEvaluation:
        raise TypeError(
            "an authoritative evaluation must be the exact ConcreteEvaluation type")

    envelope = store.snapshot()
    if not envelope.terminal:
        raise RuntimeError(
            "bounded analysis starts only after the concrete result is persisted")

    # The store checks the serialized copy too.  This identity check catches a caller
    # handing us an Authoritative object other than the one this invocation decided.
    standing = store.standing_authority()
    if canonical_json(standing.get("evaluation")) != \
            canonical_json(evaluation.as_evidence_dict()):
        raise ValueError(
            "the evaluation handed to analysis is not the evaluation this run "
            "persisted")

    outcome = analyse_evaluation(
        evaluation, exact=exact, exact_refusal=exact_refusal,
        analysis_request=analysis_request, record_audit=record_audit,
        goal_policy=goal_policy, feasibility_timeout_ms=feasibility_timeout_ms,
        query_timeout_ms=query_timeout_ms, batch=batch,
        wall_timeout_seconds=wall_timeout_seconds)
    store.attach_analysis(outcome)
    if store.standing_authority() != standing:
        raise RuntimeError("attaching analysis changed the concrete authority record")
    return outcome


def analyse_evaluation(evaluation, *, exact=None, exact_refusal=None,
                       analysis_request=None, record_audit=None, goal_policy=None,
                       feasibility_timeout_ms=None, query_timeout_ms=None, batch=None,
                       wall_timeout_seconds=None):
    """Own one deadline even when called without the command orchestrator."""
    try:
        with analysis_scope(wall_timeout_seconds):
            return _analyse_evaluation(
                evaluation, exact=exact, exact_refusal=exact_refusal,
                analysis_request=analysis_request, record_audit=record_audit,
                goal_policy=goal_policy, feasibility_timeout_ms=feasibility_timeout_ms,
                query_timeout_ms=query_timeout_ms, batch=batch)
    except AnalysisStopped as exc:
        return analysis_service._coordinator_failure(evaluation, exc,
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)


def _analyse_evaluation(evaluation, *, exact=None, exact_refusal=None,
                        analysis_request=None, record_audit=None, goal_policy=None,
                        feasibility_timeout_ms=None, query_timeout_ms=None, batch=None):
    """One analysis handoff, shared by inline and separately retained reports."""
    if type(evaluation) is not ConcreteEvaluation:
        raise TypeError("analysis requires the original ConcreteEvaluation")
    before = evaluation.fingerprint

    try:
        outcome = analysis_service.analyse_prepared(
            evaluation, exact=exact, exact_refusal=exact_refusal,
            analysis_request=analysis_request, record_audit=record_audit,
            goal_policy=goal_policy, feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)
    except Exception as exc:  # noqa: BLE001 - the production analysis boundary
        outcome = analysis_service._coordinator_failure(
            evaluation, exc,
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)
    if type(outcome) is not AnalysisOutcome:
        raise TypeError(
            "the public analysis coordinator returned no typed AnalysisOutcome")
    if evaluation.fingerprint != before:
        raise RuntimeError(
            "bounded analysis changed the ConcreteEvaluation whose score was decided")
    if outcome.concrete != before:
        raise ValueError(
            "the analysis outcome is bound to a different ConcreteEvaluation")

    return outcome
