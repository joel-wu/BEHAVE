"""The one post-run bounded-analysis coordinator.

The concrete evaluator and the bounded analyzer answer different questions.  This
module consumes a completed :class:`ConcreteEvaluation`; it never edits, filters or
rebuilds it.  One invocation owns the audited witness pool and report facts consumed by the
existing closure algorithm; no working state is shared across evaluations.

The three stages are:

1. audit each eligible raw run record into a trace of the already-bound domain;
2. establish that the domain is inhabited, using the audited pool when possible and
   otherwise exactly one feasibility query;
3. hand the resulting non-empty pool to :func:`closure.close_over`.

``close_over`` owns goal classification, set-query scheduling and singleton
fallback. This coordinator does not repeat feasibility after establishing it.
"""

from __future__ import annotations

import enum
import hashlib
import json
import math
from types import MappingProxyType

from ..analysis_budget import AnalysisStopped, check_analysis, remaining_query_ms
from . import closure as closure_mod
from .audit import (
    AuditCheck,
    AuditCheckStatus,
    AuditedWitness,
    AuditedWitnessLimitError,
    WitnessInconsistent,
    audit_check,
    witness_replay_check,
)
from .audit_plan import AuditKind, AuditPlan
from .catalogue import CatalogueManifest, DomainManifest
from .conditional_evidence import (
    AnalysisBinding,
    AnalysisPolicy,
    AnalysisStopEvidence,
    AnalysisStopKind,
    ClassifiedGoalStatus,
    ConditionalEvidence,
    FeasibilityAnswer,
    FeasibilityEvidence,
    FeasibilitySource,
    GoalEvidenceRecord,
    IssueEvidence,
    PassedCheck,
    TrustedAssumption,
    WitnessEvidence,
    WitnessOrigin,
)
from .domain import TraceDomain
from .evidence import GoalStatus
from .frozen import Frozen, FrozenMap
from .goal_provider import EXACT_BOUNDED_HIT, GoalCapabilityUse
from .instance import BoundedInstance, Feasible, Tagged
from .outcome import AnalysisOutcome, ConcreteEvaluation, Verdict
from .query import FeasibilityCall, SolverAnswer, _FeasibilityDetailError
from .query_telemetry import QueryTelemetry
from .record_value import FrozenValueMap, freeze_record_value, thaw_record_value
from .refusal import Refusal, Refused
from .stimulus import Completion, canonical_json

__all__ = [
    "AuditDisposition", "RecordAudit", "analyse",
    "audit_environment_assumptions",
]


class _AnalysisSubject(Frozen):
    """The immutable part of a concrete score consumed by bounded analysis.

    The public entry still accepts only :class:`ConcreteEvaluation`.  This smaller
    closed record is also the JSON worker boundary: exact analysis needs the task,
    catalogue and raw runs, not a second copy of the already-decided comparisons.
    Its ``concrete`` identity remains the digest of the complete evaluation image.
    """

    __slots__ = (
        "_live", "_record", "catalogue", "concrete", "results", "task")

    def __init__(self, task, results, catalogue, concrete, record, *, live=None):
        from .run_record import RunResult

        results = tuple(results)
        if any(type(result) is not RunResult for result in results):
            raise TypeError("an analysis subject contains only exact RunResult rows")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("an analysis subject needs one exact catalogue manifest")
        if type(concrete) is not str or len(concrete) != 64 or any(
                char not in "0123456789abcdef" for char in concrete):
            raise ValueError("an analysis subject needs a SHA-256 concrete identity")
        self.task = task
        self.results = results
        self.catalogue = catalogue
        self.concrete = concrete
        self._record = canonical_json(record)
        if live is not None and type(live) is not ConcreteEvaluation:
            raise TypeError("a live analysis subject is a ConcreteEvaluation")
        self._live = live
        self._freeze()

    @property
    def fingerprint(self):
        return self.concrete if self._live is None else self._live.fingerprint

    def as_record_reference(self):
        return (json.loads(self._record) if self._live is None else
                self._live.as_record_reference())

    @classmethod
    def from_evaluation(cls, evaluation):
        if type(evaluation) is not ConcreteEvaluation:
            raise TypeError("an analysis subject is derived from ConcreteEvaluation")
        return cls(
            evaluation.task, evaluation.results, evaluation.catalogue,
            evaluation.fingerprint, evaluation.as_record_reference(),
            live=evaluation)


def _analysis_subject_from_wire(task, value, catalogue):
    """Rebuild the raw-run analysis view from a strict score-owned JSON image."""

    from .failure import RunFailure
    from .refusal import Refusal
    from .run_record import RunResult
    from .stimulus import AuditedTrace, Stimulus

    if type(value) is not dict or set(value) != {
            "concrete", "evaluation", "raw_records"}:
        raise ValueError("a wire analysis subject has unknown or missing fields")
    evidence = value["evaluation"]
    rows = value["raw_records"]
    if type(evidence) is not dict or type(rows) is not list:
        raise TypeError("a wire analysis subject carries finite JSON records")
    concrete = hashlib.sha256(canonical_json(evidence).encode()).hexdigest()
    if value["concrete"] != concrete:
        raise ValueError("the wire analysis subject has the wrong concrete identity")
    if evidence.get("contract") != task.contract.digest \
            or evidence.get("reference") != task.reference \
            or evidence.get("realization") != task.realization.as_dict():
        raise ValueError("the wire analysis subject names another admitted task")
    carried_catalogue = CatalogueManifest.from_dict(
        evidence.get("catalogue_manifest"))
    if carried_catalogue != catalogue \
            or evidence.get("catalogue_digest") != catalogue.digest():
        raise ValueError("the wire analysis subject names another goal catalogue")
    score_rows = evidence.get("results")
    if type(score_rows) is not list or len(score_rows) != len(rows):
        raise ValueError("the score and raw analysis rows have different lengths")

    results = []
    for index, (score, raw) in enumerate(zip(score_rows, rows)):
        if type(score) is not dict or type(raw) is not dict \
                or set(raw) != {"stimulus", "trace"}:
            raise TypeError("each wire analysis row carries one stimulus and trace")
        stimulus = Stimulus.from_dict(raw["stimulus"])
        if score.get("stimulus") != stimulus.fingerprint:
            raise ValueError(f"wire analysis row {index} names another stimulus")
        if raw["trace"] is None:
            if score.get("trace") is not None or not score.get("run_failure"):
                raise ValueError(
                    f"wire analysis row {index} has no trace without a run failure")
            results.append(RunResult.failed_to_run(
                stimulus, RunFailure.from_dict(score["run_failure"])))
            continue
        trace = AuditedTrace.from_dict(raw["trace"])
        if score.get("trace") != trace.fingerprint:
            raise ValueError(f"wire analysis row {index} names another raw trace")
        results.append(RunResult.not_compared(
            stimulus, trace,
            RunFailure.verifier(
                "bounded-analysis-view",
                Refusal(
                    "analysis-raw-run-not-compared",
                    "bounded analysis reads the persisted raw run without "
                    "recomputing its concrete comparison")),
            observations_eligible=score["observations_eligible"]))

    record = dict(evidence)
    record.pop("catalogue_manifest")
    return _AnalysisSubject(task, results, catalogue, concrete, record)


class AuditDisposition(enum.Enum):
    """The four results of auditing one raw run record."""

    ADMITTED = "admitted"
    OUTSIDE = "outside"
    INCONSISTENT = "inconsistent"
    REFUSED = "refused"

    def __bool__(self):
        raise TypeError(
            "AuditDisposition has no truth value; inspect the named audit result")


def _snapshot_row(row):
    if type(row) in (dict, MappingProxyType, FrozenMap):
        return FrozenMap(row)
    if type(row) in (tuple, list):
        return tuple(_snapshot_row(item) for item in row)
    return row


def _snapshot_trace(trace):
    """Materialize a decoded feasibility recipe before it can become evidence."""
    if isinstance(trace, Tagged):
        raise TypeError(
            "a decoded trace must belong to this bound instance rather than carry "
            "another domain's tag")
    if type(trace) not in (tuple, list):
        raise TypeError(
            "a decoded trace must be a materialized finite tuple or list of steps, "
            f"not {type(trace).__name__}")
    return tuple(_snapshot_row(row) for row in trace)


class RecordAudit(Frozen):
    """The typed audit result for one raw record.

    ``ADMITTED`` is the only disposition carrying a trace.  ``OUTSIDE`` is an honest
    run outside this analysis domain and is omitted from the initial pool.  The other
    two stop the analysis globally; neither can be turned into a per-goal status.
    ``audits`` carries checks already completed by the provider against the saved
    native execution and this witness, not checks merely requested by a caller.
    """

    __slots__ = ("audits", "detail", "disposition", "kind", "trace", "why", "witness")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("RecordAudit is a closed audit result and cannot be subclassed")

    def __init__(self, disposition, witness=None, kind="", why="", audits=None, detail=None):
        if type(disposition) is not AuditDisposition:
            raise TypeError(
                "a record-audit disposition must be the exact AuditDisposition enum")
        self.disposition = disposition
        # A provider may carry checks it already completed against this exact
        # witness. Absence retains the independent checks for older providers.
        if audits is not None:
            if type(audits) is not AuditPlan or any(
                    item not in (AuditKind.STEP, AuditKind.SOURCE)
                    for item in audits.audits):
                raise TypeError("record audits contain only completed step/source checks")
            if disposition is not AuditDisposition.ADMITTED:
                raise ValueError("only an admitted record carries completed audits")
        self.audits = audits
        if self.disposition is AuditDisposition.ADMITTED:
            if type(witness) is not AuditedWitness:
                raise TypeError(
                    "an admitted record audit needs the exact AuditedWitness produced "
                    "by semantic replay")
            self.witness = witness
            self.trace = witness.inputs
        else:
            if witness is not None:
                raise ValueError("only an admitted record audit carries a witness")
            self.witness = None
            self.trace = ()
        if type(kind) is not str:
            raise TypeError("a record-audit refusal kind must be an exact string")
        if type(why) is not str:
            raise TypeError("a record-audit explanation must be an exact string")
        self.kind = kind
        self.why = why
        if self.disposition is AuditDisposition.ADMITTED and (kind or why):
            raise ValueError("an admitted record carries neither a refusal nor a reason")
        if self.disposition is not AuditDisposition.ADMITTED and not self.why:
            raise ValueError(f"an audit result of {self.disposition.value} must say why")
        if self.disposition is AuditDisposition.REFUSED and not self.kind:
            raise ValueError("a refused record audit must carry a stable refusal kind")
        if self.disposition is not AuditDisposition.REFUSED and self.kind:
            raise ValueError("only a refused record audit carries a refusal kind")
        self.detail = freeze_record_value({} if detail is None else detail, 'record-audit.detail')
        if type(self.detail) is not FrozenValueMap:
            raise TypeError('record-audit details must be a finite mapping')
        if self.detail and self.disposition is not AuditDisposition.REFUSED:
            raise ValueError('only a refused record audit carries refusal details')
        self._freeze()

    @classmethod
    def admitted(cls, witness, *, audits=None):
        return cls(AuditDisposition.ADMITTED, witness=witness, audits=audits)

    @classmethod
    def outside(cls, why):
        return cls(AuditDisposition.OUTSIDE, why=why)

    @classmethod
    def inconsistent(cls, why):
        return cls(AuditDisposition.INCONSISTENT, why=why)

    @classmethod
    def refused(cls, kind, why, **detail):
        return cls(AuditDisposition.REFUSED, kind=kind, why=why, detail=detail)

    def as_dict(self):
        out = {"disposition": self.disposition.value}
        if self.why:
            out["why"] = self.why
        if self.kind:
            out["kind"] = self.kind
        if self.detail:
            out["detail"] = thaw_record_value(self.detail)
        if self.disposition is AuditDisposition.ADMITTED:
            out["witness"] = self.witness.as_record()
            out["witness_ref"] = "witness:" + self.witness.fingerprint
            if self.audits is not None:
                out["audits"] = self.audits.as_dict()
        return out

    def __reduce__(self):
        return (RecordAudit, (
            self.disposition, self.witness, self.kind, self.why, self.audits,
            thaw_record_value(self.detail)))


def audit_environment_assumptions(contract, stimulus, trace, *, artifact_kind="rtl"):
    """Return ``OUTSIDE`` exactly when a recorded environment action was illegal.

    Use the concrete evaluator's observation boundary: RTL has pin protocols,
    whereas Behavior has logical transaction batches, not missing pin samples.
    Declared transaction observations still apply to both representations.
    """

    from .observation_monitor import execution_monitor_for

    try:
        monitor = execution_monitor_for(contract, artifact_kind)
        violations = (() if monitor is None else
                      monitor.check(stimulus, trace).assumption_violations)
    except Refused as exc:
        return RecordAudit.refused(exc.refusal.kind, exc.refusal.why, **exc.refusal.detail)
    if not violations:
        return None
    first = violations[0]
    more = len(violations) - 1
    suffix = f"; {more} further violation(s) follow" if more else ""
    return RecordAudit.outside(
        f"slot {first.slot} violates environment assumption {first.goal!r} at "
        f"endpoint {first.endpoint!r}: {first.why}{suffix}")


def _plain(value):
    """A strict evidence image for the finite values this coordinator carries."""
    if isinstance(value, FrozenMap):
        return {key: _plain(item) for key, item in value.items()}
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("an evidence mapping has a non-string key")
        return {key: _plain(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_plain(item) for item in value]
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError(
        f"{type(value).__name__} has no exact finite evidence representation")


def _provider_domain_manifest(exact):
    """Freeze the exact provider's catalogue together with its trace domain."""

    manifest_of = getattr(exact.instance, "catalogue_manifest", None)
    if not callable(manifest_of):
        raise TypeError("the exact provider has no catalogue_manifest authority")
    catalogue = manifest_of()
    if type(catalogue) is not CatalogueManifest:
        raise TypeError("the exact provider returned a non-CatalogueManifest")
    return DomainManifest(exact.domain, catalogue)


def _bind_domain_manifest(evaluation, exact):
    """Bind domain and catalogue once before any exact query can be issued."""

    try:
        if type(evaluation.catalogue) is not CatalogueManifest:
            raise TypeError("the concrete evaluation has no CatalogueManifest")
        provider = _provider_domain_manifest(exact)
        # The provider is the authority for what its solver will classify.  Rebuild the
        # immutable wrapper from that authority even when a caller supplied an already
        # bound wrapper: a stale caller-side manifest is evidence of a mismatch, not an
        # alternative way to choose the query denominator.
        bound = BoundedInstance(
            exact.instance, exact.domain,
            analysis_cache=exact.analysis_cache,
            request_budget=exact.request_budget).bind_catalogue(
                provider.catalogue)
        if exact.domain_manifest is not None and exact.domain_manifest != provider:
            return bound, Refusal(
                "analysis-domain-manifest-mismatch",
                "the supplied exact binding and the provider name different domain "
                "manifests",
                supplied=exact.domain_manifest.digest(), exact=provider.digest(),
                domain=exact.domain.digest())
        concrete = DomainManifest(exact.domain, evaluation.catalogue)
        if provider != concrete:
            return bound, Refusal(
                "analysis-domain-manifest-mismatch",
                "concrete replay and bounded analysis name different catalogues for "
                "the same exact trace domain",
                concrete=concrete.digest(), exact=provider.digest(),
                domain=exact.domain.digest(),
                concrete_catalogue=evaluation.catalogue.digest(),
                exact_catalogue=provider.catalogue.digest())
        return bound, None
    except Refused as exc:
        return None, exc.refusal
    except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
        return None, Refusal(
            "analysis-catalogue-unavailable",
            "the exact instance could not bind its trace domain and catalogue before "
            "bounded analysis",
            error=f"{type(exc).__name__}: {exc}")


def _binding_preflight(evaluation, exact):
    """Bind the concrete record, task and provider before asking the provider.

    A task-contract digest is necessary and not sufficient.  Copying the right digest
    into a domain did not show that the exact provider represented the admitted
    artifact, or that its finite input boundary was the task's.  Both errors strengthen
    an UNSAT answer, so neither may be discovered after a query.
    """
    task_contract = getattr(getattr(evaluation.task, "contract", None),
                            "digest", None)
    if type(task_contract) is not str or not task_contract:
        return Refusal(
            "analysis-task-contract-unbound",
            "the concrete evaluation has no stable task-contract identity",
            got=repr(task_contract))
    if exact.domain.task_contract != task_contract:
        return Refusal(
            "analysis-task-contract-mismatch",
            "the exact trace domain must name the same task-contract digest as the "
            "concrete evaluation",
            concrete=task_contract, exact=exact.domain.task_contract)

    realization = getattr(evaluation.task, "realization", None)
    concrete_artifact = getattr(realization, "identity", None)
    if type(concrete_artifact) is not str or not concrete_artifact:
        return Refusal(
            "analysis-concrete-artifact-unbound",
            "the concrete evaluation does not identify the admitted artifact, so an "
            "exact transition system cannot be paired with it",
            got=repr(concrete_artifact))
    if type(exact.domain.artifact) is not str or not exact.domain.artifact:
        return Refusal(
            "analysis-domain-artifact-unbound",
            "the exact trace domain does not identify its transition system",
            got=repr(exact.domain.artifact))
    if exact.domain.artifact != concrete_artifact:
        return Refusal(
            "analysis-artifact-mismatch",
            "the concrete evaluation and exact trace domain name different artifacts",
            concrete=concrete_artifact, exact=exact.domain.artifact)

    try:
        identity = exact.analysis_identity()
    except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
        return Refusal(
            "analysis-provider-identity-unavailable",
            "the exact provider could not identify the transition system it will "
            "query",
            error=f"{type(exc).__name__}: {exc}")
    if type(identity) is not str or not identity:
        return Refusal(
            "analysis-provider-identity-invalid",
            "the exact provider returned no stable transition-system identity",
            got=repr(identity))
    if identity != exact.domain.artifact:
        return Refusal(
            "analysis-provider-domain-mismatch",
            "the provider currently in the bound instance is not the transition "
            "system named by its frozen trace domain",
            provider=identity, domain=exact.domain.artifact)

    try:
        check = audit_check(exact.task_audit(evaluation.task),
                            "task-binding-audit")
    except Refused as exc:
        return exc.refusal
    except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
        return Refusal(
            "analysis-task-binding-audit-failed",
            "the exact provider could not check its finite boundary against the task",
            error=f"{type(exc).__name__}: {exc}")
    if check.status is AuditCheckStatus.REFUSED:
        return check.refusal
    if check.status is AuditCheckStatus.INCONSISTENT:
        return Refusal(
            "analysis-task-binding-disagrees",
            "the exact provider's finite boundary is not the task-declared boundary",
            first=check.findings[0], count=len(check.findings))
    return identity


def _audit_plan(exact):
    try:
        plan = exact.audit_plan()
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - a provider boundary
        raise Refused(Refusal(
            "analysis-audit-plan-unavailable",
            "the exact instance could not state its required audit plan",
            error=f"{type(exc).__name__}: {exc}")) from None
    if type(plan) is not AuditPlan:
        raise Refused(Refusal(
            "analysis-audit-plan-invalid",
            "the exact instance must return the closed AuditPlan record rather than "
            "an untyped collection",
            got=type(plan).__name__))
    return plan


def _trusted_assumptions(exact):
    """Freeze the universal premises before any solver call is issued."""
    try:
        trusted = tuple(getattr(exact, "trusted_assumptions", lambda: ())())
    except Exception as exc:  # noqa: BLE001 - a provider boundary
        raise Refused(Refusal(
            "analysis-trusted-assumptions-unavailable",
            "the exact instance could not state the assumptions its conclusion "
            "would rest on",
            error=f"{type(exc).__name__}: {exc}")) from None
    if any(type(item) is not TrustedAssumption for item in trusted):
        raise Refused(Refusal(
            "analysis-trusted-assumption-invalid",
            "the exact instance returned an untyped trusted premise; finite checks "
            "and universal assumptions must remain separate closed records"))
    if len({item.code for item in trusted}) != len(trusted):
        raise Refused(Refusal(
            "analysis-trusted-assumption-duplicate",
            "trusted assumptions must have distinct stable codes"))
    return trusted


def _preflight(exact, plan):
    """Checks that must precede feasibility or goal queries."""
    passed = ["representation_well_formed"]
    try:
        malformed = exact.well_formed()
    except Exception as exc:  # noqa: BLE001 - a provider boundary
        raise Refused(Refusal(
            "analysis-well-formedness-unavailable",
            "the exact instance could not check whether its representation is well "
            "formed",
            error=f"{type(exc).__name__}: {exc}")) from None
    if malformed is None:
        raise Refused(Refusal(
            "analysis-well-formedness-unresolved",
            "the exact instance did not establish whether its representation is well "
            "formed"))
    if malformed:
        raise Refused(Refusal(
            "analysis-instance-not-well-formed",
            "the exact instance is not well formed, so its queries cannot support a "
            "bounded conclusion",
            first=str(malformed[0]), count=len(malformed)))

    if plan.requires(AuditKind.GOAL_SET):
        try:
            missing = exact.unaccounted_goals()
        except Exception as exc:  # noqa: BLE001 - a provider boundary
            raise Refused(Refusal(
                "analysis-goal-audit-unavailable",
                "the exact instance could not reconcile its declared and encoded "
                "goals",
                error=f"{type(exc).__name__}: {exc}")) from None
        if missing is None:
            raise Refused(Refusal(
                "analysis-goal-audit-unresolved",
                "the exact instance did not establish that every declared goal has an "
                "exact hit term"))
        if missing:
            raise Refused(Refusal(
                "analysis-goals-unaccounted",
                "some declared goals have no reconciled exact producer",
                first=str(missing[0]), count=len(missing)))
        passed.append("goal_set_audit")
    return tuple(passed)


def _trace_audits(exact, traces, plan, completed=()):
    """Run the frontend audits required for these exact traces.

    Returns ``None`` on agreement or ``(verdict, refusal)`` on a global stop.
    """
    def pending(kind):
        return tuple(trace for index, trace in enumerate(traces)
                     if not completed or completed[index] is None
                     or not completed[index].requires(kind))

    step_traces = pending(AuditKind.STEP)
    if plan.requires(AuditKind.STEP) and step_traces:
        try:
            check = audit_check(exact.audit(step_traces), "step-audit")
        except WitnessInconsistent as exc:
            return Verdict.INCONSISTENT, Refusal(exc.kind, exc.why, **exc.detail)
        except Refused as exc:
            return Verdict.REFUSED, exc.refusal
        except Exception as exc:  # noqa: BLE001 - a provider boundary
            return Verdict.REFUSED, Refusal(
                "analysis-step-audit-failed",
                "the exact step audit could not be carried out",
                error=f"{type(exc).__name__}: {exc}")
        if check.status is AuditCheckStatus.REFUSED:
            return Verdict.REFUSED, check.refusal
        if check.status is AuditCheckStatus.INCONSISTENT:
            return Verdict.INCONSISTENT, Refusal(
                "analysis-step-audit-disagrees",
                "the semantic and encoded readings disagree on an audited trace",
                first=check.findings[0], count=len(check.findings))

    source_traces = pending(AuditKind.SOURCE)
    if plan.requires(AuditKind.SOURCE) and source_traces:
        try:
            check = audit_check(exact.source_audit(source_traces), "source-audit")
        except WitnessInconsistent as exc:
            return Verdict.INCONSISTENT, Refusal(exc.kind, exc.why, **exc.detail)
        except Refused as exc:
            return Verdict.REFUSED, exc.refusal
        except Exception as exc:  # noqa: BLE001 - a provider boundary
            return Verdict.REFUSED, Refusal(
                "analysis-source-audit-failed",
                "the source audit could not be carried out",
                error=f"{type(exc).__name__}: {exc}")
        if check.status is AuditCheckStatus.REFUSED:
            return Verdict.REFUSED, check.refusal
        if check.status is AuditCheckStatus.INCONSISTENT:
            return Verdict.INCONSISTENT, Refusal(
                "analysis-source-audit-disagrees",
                "the source and lowered readings disagree on an audited trace",
                first=check.findings[0], count=len(check.findings))
    return None


def _same_finite(left, right):
    try:
        return canonical_json(_plain(left)) == canonical_json(_plain(right))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _witness_replay(exact, witness, context):
    """Check one complete witness against the bound instance's ordinary replay."""
    try:
        replayed = exact.replay(witness.inputs)
    except WitnessInconsistent as exc:
        return Verdict.INCONSISTENT, Refusal(exc.kind, exc.why, **exc.detail)
    except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
        refusal = Refusal(
            "analysis-witness-replay-failed",
            "the independent replay for an audited witness could not be carried out",
            context=context, error=f"{type(exc).__name__}: {exc}")
        return Verdict.REFUSED, refusal
    check = witness_replay_check(witness, replayed)
    if check.status is AuditCheckStatus.REFUSED:
        return Verdict.REFUSED, Refusal(
            check.kind, check.why, **{**check.refusal.detail, "context": context})
    if check.status is AuditCheckStatus.INCONSISTENT:
        return Verdict.INCONSISTENT, Refusal(
            "analysis-witness-replay-disagrees",
            "the complete audited witness and the independent replay disagree",
            context=context, first=check.findings[0],
            count=len(check.findings))
    return None


def _record_hit_hint(result, evaluation, *, horizon=None):
    """Return scheduling hints only; malformed records need the ordinary audit."""
    trace = result.trace
    if trace is None or not result.observations_eligible \
            or trace.completion is not Completion.NORMAL:
        return None
    try:
        if result.check_task(evaluation.task):
            return None
        names, hits = trace.goal_summary
        if not names or not names <= set(evaluation.catalogue):
            return None
        if horizon is not None and len(trace.records) > horizon:
            from .hit_vector import HitVector, _reached_union

            # Selection follows the declared finite window, not later events.
            # These remain hints: only an audited witness can support a hit.
            selected = trace.records[:horizon]
            hits = (_reached_union(record.hits for record in selected)
                    if selected and type(selected[0].hits) is HitVector else
                    frozenset(name for record in selected for name, reached in record.hits if reached))
    except Exception:  # noqa: BLE001 - selection cannot hide an invalid record
        return None
    return hits


def _record_audit_order(evaluation, *, horizon=None):
    """Audit invalid records first, then prefer complementary native hints.

    Stable original indices remain the evidence identity. A hint cannot mark a
    goal reached or skip an audit unless an actual audited witness supports it.
    This only plans the order: failed/outside hints still grant no support.
    An inverted index visits each record/goal edge once; lazy heap priorities
    avoid rescanning every remaining record after each selection.
    """
    from heapq import heapify, heappop, heappush

    ordered, hints, gains, owners = [], {}, {}, {}
    for index, result in enumerate(evaluation.results):
        check_analysis()
        hits = _record_hit_hint(result, evaluation, horizon=horizon)
        if hits is None:
            ordered.append((index, result))
            continue
        hints[index], gains[index] = hits, len(hits)
        for goal in hits:
            owners.setdefault(goal, []).append(index)
    pending = [(-gain, index) for index, gain in gains.items()]
    heapify(pending)
    while pending:
        check_analysis()
        priority, index = heappop(pending)
        if -priority != gains[index]:
            heappush(pending, (-gains[index], index))
            continue
        del gains[index]
        ordered.append((index, evaluation.results[index]))
        for goal in hints[index]:
            for other in owners.pop(goal, ()):
                if other in gains:
                    gains[other] -= 1
    return ordered


def _redundant_record_support(result, evaluation, witnesses, supported, *, horizon=None):
    """Select support, not a second validation of every test or a new proof."""
    if not witnesses:
        return None
    hits = _record_hit_hint(result, evaluation, horizon=horizon)
    if hits is None or not hits <= supported.keys():
        return None
    # Even an empty hit set names the already established nonempty domain.
    refs = {supported[goal] for goal in hits} or {"witness:" + witnesses[0].fingerprint}
    return {"disposition": "not-selected", "reason": "redundant-goal-support",
            "observed_hits": sorted(hits), "covered_by": sorted(refs)}


def _per_goal(closure, witnesses):
    """Derive the public partition from closed witness/query references."""
    retained = [(item.witness_ref, AuditedWitness.from_record(item.witness)) for item in witnesses]
    out = {}
    for goal in closure.goals:
        got = closure.of(goal)
        status = (ClassifiedGoalStatus.BOUNDED_UNREACHABLE
                  if got.status is GoalStatus.UNREACHABLE
                  else (ClassifiedGoalStatus.REACHED
                        if got.status is GoalStatus.REACHED
                        else ClassifiedGoalStatus.UNRESOLVED))
        if got.status is GoalStatus.REACHED:
            support = next((
                reference for reference, witness in retained
                if goal in witness.hits and witness.matches_inputs(got.witness)), None)
        elif got.status is GoalStatus.UNREACHABLE:
            support = next((
                query.query_id for query in closure.queries
                if query.answer is SolverAnswer.UNSAT
                and goal in query.goals), None)
        elif got.status is GoalStatus.UNRESOLVED:
            support = next((
                query.query_id for query in closure.queries
                if query.answer is SolverAnswer.UNKNOWN
                and query.goals == (goal,)), None)
        else:
            support = None
        if support is None:
            raise Refused(Refusal(
                "analysis-goal-evidence-unlinked",
                "a classified goal has no exact witness or query record supporting "
                "its status",
                goal=goal, status=status.value))
        out[goal] = GoalEvidenceRecord(
            status, support, detail=str(got.detail or ""))
    return out


def _issue_from_image(value):
    if type(value) is not dict:
        raise TypeError("an analysis issue image must be a mapping")
    return IssueEvidence(value.get("kind"), value.get("why"), value.get("detail"))


def _feasibility_from_image(value):
    if type(value) is not dict:
        raise TypeError("feasibility evidence must be a mapping")
    return FeasibilityEvidence(
        FeasibilitySource(value["established_by"]),
        answer=FeasibilityAnswer(value.get("answer", "feasible")),
        witness=value.get("witness"),
        decoded_request=value.get("decoded_run_request"),
        solver_calls=value.get("solver_calls"),
        replayed=value.get("replayed", False),
        detail=value.get("detail", ""),
        telemetry=(None if "telemetry" not in value else
                   QueryTelemetry.from_dict(value["telemetry"])))


def analyse(evaluation, exact=None, record_audit=None, *, exact_refusal=None,
            goal_capability_use=None, feasibility_timeout_ms=None,
            query_timeout_ms=None, batch=None, _artifact_timeout_ms=None):
    """Analyse one frozen concrete result over one already-bound exact instance.

    ``record_audit`` is the frontend boundary
    ``(RunResult, BoundedInstance) -> RecordAudit``.  It is intentionally not inferred
    from a raw record's shape: only the frontend knows how that record denotes a trace
    of its semantic domain.  ``exact_refusal`` carries the typed reason why construction
    of that bound instance was unavailable *after* concrete scoring.  It is mutually
    exclusive with ``exact`` and cannot be converted into a score refusal.
    """
    if type(evaluation) is not ConcreteEvaluation:
        refusal = Refusal(
            "analysis-concrete-record-invalid",
            "the bounded analyzer was given something other than the exact frozen "
            "ConcreteEvaluation type",
            got=type(evaluation).__name__)
        return AnalysisOutcome(
            Verdict.REFUSED,
            why=refusal.why,
                evidence=AnalysisStopEvidence(
                    AnalysisStopKind.REFUSED,
                    issue=IssueEvidence.from_refusal(refusal)))

    return _analyse_subject(
        _AnalysisSubject.from_evaluation(evaluation), exact=exact,
        record_audit=record_audit, exact_refusal=exact_refusal,
        goal_capability_use=goal_capability_use,
        feasibility_timeout_ms=feasibility_timeout_ms,
        query_timeout_ms=query_timeout_ms, batch=batch,
        _artifact_timeout_ms=_artifact_timeout_ms)


class _AnalysisRun:
    """One invocation's working state, separate from its frozen concrete result.

    The coordinator below owns phase order. This object owns the bound provider,
    evidence pool and report facts for that invocation only; it is neither a
    reusable query cache nor another goal scheduler. Closure remains the single
    owner of residual queries and classifications.
    """

    def __init__(self, evaluation, exact):
        self.evaluation = evaluation
        self.exact = exact
        # Snapshot before invoking any frontend/provider callbacks.
        self.before = canonical_json(evaluation.as_record_reference())
        self.concrete = evaluation.fingerprint
        self.binding_evidence = None
        self.domain_manifest = None
        self.audit_plan = None
        self.passed_checks = []
        self.trusted = ()
        self.analysis_policy = None

    def finish(self, verdict, why="", *, per_goal=None, evidence=None,
               closure_record=None):
        # A frontend callback is outside this module.  If it somehow changed a nested
        # object reachable from the concrete record, publish no conclusion and name the
        # violation instead of pairing the old fingerprint with changed evidence.
        try:
            unchanged = (canonical_json(self.evaluation.as_record_reference()) == self.before
                         and self.evaluation.fingerprint == self.concrete)
        except Exception:  # noqa: BLE001 - mutation may make serialization fail
            unchanged = False
        if not unchanged:
            issue = IssueEvidence.from_refusal(Refusal(
                "analysis-mutated-concrete-evaluation",
                "bounded analysis is read-only over the complete concrete record; "
                "its serialized bytes changed during the call"))
            return AnalysisOutcome(
                Verdict.INCONSISTENT,
                why=issue.why,
                evidence=AnalysisStopEvidence(
                    AnalysisStopKind.INCONSISTENT,
                    policy=self.analysis_policy, issue=issue,
                    binding=self.binding_evidence,
                    domain_manifest=self.domain_manifest),
                concrete=self.concrete)
        try:
            evidence = dict(evidence or {})
            if "record_audits" in evidence:
                evidence["record_audits"] = sorted(
                    evidence["record_audits"], key=lambda row: row["record"])
            if verdict is Verdict.CLASSIFIED:
                if closure_record is None:
                    raise TypeError(
                        "a classification must retain its exact Closure record")
                claim = ConditionalEvidence(
                    binding=self.binding_evidence,
                    policy=self.analysis_policy,
                    record_audits=evidence.get("record_audits", ()),
                    feasibility=_feasibility_from_image(evidence["feasibility"]),
                    domain_manifest=self.domain_manifest,
                    audit_plan=self.audit_plan,
                    checks=evidence.get("passed_checks", ()),
                    trusted_assumptions=evidence.get("trusted_assumptions", ()),
                    queries=closure_record.queries,
                    goals=per_goal,
                    closure=closure_record.summary(),
                    witnesses=evidence.get("witnesses", ()))
                return AnalysisOutcome(
                    verdict, evidence=claim, concrete=self.concrete)

            issue = None
            issue_image = evidence.get(
                "refusal" if verdict is Verdict.REFUSED else "inconsistency")
            if issue_image is not None:
                issue = _issue_from_image(issue_image)
            feasibility = (None if "feasibility" not in evidence else
                           _feasibility_from_image(evidence["feasibility"]))
            stopped = AnalysisStopEvidence(
                AnalysisStopKind(verdict.value),
                policy=self.analysis_policy,
                issue=issue,
                binding=self.binding_evidence,
                audit_plan=self.audit_plan,
                domain_manifest=self.domain_manifest,
                record_audits=evidence.get("record_audits", ()),
                feasibility=feasibility,
                closure=evidence.get("closure"),
                queries=evidence.get("queries", ()),
                checks=tuple(self.passed_checks),
                trusted_assumptions=self.trusted)
            supported_why = (
                issue.why if issue is not None else feasibility.detail)
            return AnalysisOutcome(
                verdict, why=supported_why, evidence=stopped, concrete=self.concrete)
        except Exception as exc:  # noqa: BLE001 - evidence is an isolation boundary
            # Evidence construction consumes frontend/provider data.  A malformed
            # catalogue, witness ledger or closure record is therefore an analysis
            # refusal, never an exception that can erase the already-complete score.
            refusal = Refusal(
                "analysis-evidence-assembly-failed",
                "bounded analysis could not assemble a closed typed evidence record; "
                "the concrete evaluation remains unchanged",
                intended_verdict=verdict.value,
                error_type=type(exc).__name__)
            issue = IssueEvidence.from_refusal(refusal)
            stopped = AnalysisStopEvidence(
                AnalysisStopKind.REFUSED,
                policy=(self.analysis_policy
                        if type(self.analysis_policy) is AnalysisPolicy else None),
                issue=issue,
                binding=self.binding_evidence,
                domain_manifest=self.domain_manifest,
                audit_plan=self.audit_plan)
            return AnalysisOutcome(
                Verdict.REFUSED, why=issue.why, evidence=stopped,
                concrete=self.concrete)

    def bind(self, requested_policy, exact_refusal, goal_capability_use):
        """Bind one declared domain, catalogue and provider before consuming evidence."""
        for name, value in requested_policy.items():
            if value is not None and (type(value) is not int or value < 1):
                refusal = Refusal(
                    "analysis-policy-value-invalid",
                    "analysis timeouts and the goal-query batch size must be positive "
                    "integers or absent; zero cannot mean both exhausted and unlimited",
                    field=name, got=repr(value))
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict()})
        self.analysis_policy = AnalysisPolicy(**requested_policy)

        if exact_refusal is not None and type(exact_refusal) is not Refusal:
            refusal = Refusal(
                "analysis-exact-capability-refusal-invalid",
                "an unavailable exact capability must be represented by the exact typed "
                "Refusal record",
                got=type(exact_refusal).__name__)
            return self.finish(Verdict.REFUSED, refusal.why,
                          evidence={"refusal": refusal.as_dict()})
        if self.exact is not None and exact_refusal is not None:
            refusal = Refusal(
                "analysis-exact-capability-ambiguous",
                "bounded analysis cannot receive both an exact instance and a refusal "
                "claiming that instance is unavailable")
            return self.finish(Verdict.REFUSED, refusal.why,
                          evidence={"refusal": refusal.as_dict()})

        if self.exact is None:
            if exact_refusal is not None:
                return self.finish(
                    Verdict.REFUSED, exact_refusal.why,
                    evidence={"refusal": exact_refusal.as_dict()})
            return self.finish(
                Verdict.REFUSED,
                "no exact bounded instance was supplied, so no goal was classified",
                evidence={"refusal": Refusal(
                    "analysis-exact-instance-missing",
                    "bounded reachability requires an exact instance already bound to its "
                    "trace domain; a local generation probe is not one").as_dict()})
        if type(self.exact) is not BoundedInstance \
                or type(getattr(self.exact, "domain", None)) is not TraceDomain:
            return self.finish(
                Verdict.REFUSED,
                "the supplied analysis instance is not bound to an exact trace domain",
                evidence={"refusal": Refusal(
                    "analysis-exact-instance-unbound",
                    "the coordinator accepts only BoundedInstance, so every audit, "
                    "feasibility query and goal query is about one pre-existing domain",
                    got=type(self.exact).__name__).as_dict()})
        if type(self.exact.domain.bound) is not int or self.exact.domain.bound < 1:
            return self.finish(
                Verdict.REFUSED,
                "the supplied trace domain has no finite positive horizon",
                evidence={"refusal": Refusal(
                    "analysis-domain-bound-invalid",
                    "this is bounded analysis and its trace domain must state the exact "
                    "positive integer horizon",
                    got=self.exact.domain.bound).as_dict()})

        required_of = getattr(self.exact.instance, "goal_capability_required", None)
        if callable(required_of) and required_of() is True:
            if type(goal_capability_use) is not GoalCapabilityUse:
                refusal = Refusal(
                    "analysis-exact-goal-capability-missing",
                    "a production exact provider must be accompanied by its closed "
                    "pre-generation bounded-hit capability")
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict()})
            capability_of = getattr(self.exact.instance, "goal_capability_use", None)
            implementation_of = getattr(
                self.exact.instance, "exact_goal_implementation", None)
            carried = capability_of() if callable(capability_of) else None
            implementation = (implementation_of()
                              if callable(implementation_of) else None)
            if carried is not goal_capability_use:
                refusal = Refusal(
                    "analysis-exact-goal-capability-replaced",
                    "the coordinator received a bounded-hit capability other than the "
                    "one carried by the exact provider")
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict()})
            try:
                goal_capability_use.reconcile(
                    EXACT_BOUNDED_HIT, implementation,
                    goal_capability_use.source_catalogue,
                    tuple(goal_capability_use.targets))
            except Refused as exc:
                return self.finish(Verdict.REFUSED, exc.refusal.why,
                              evidence={"refusal": exc.refusal.as_dict()})

        bound, manifest_refusal = _bind_domain_manifest(self.evaluation, self.exact)
        if bound is not None:
            self.exact = bound
            self.domain_manifest = self.exact.domain_manifest
        if manifest_refusal is not None:
            return self.finish(Verdict.REFUSED, manifest_refusal.why,
                          evidence={"refusal": manifest_refusal.as_dict()})

        binding = _binding_preflight(self.evaluation, self.exact)
        if isinstance(binding, Refusal):
            return self.finish(Verdict.REFUSED, binding.why,
                          evidence={"refusal": binding.as_dict()})

        try:
            self.identities = self.exact.evidence_identity()
        except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
            refusal = Refusal(
                "analysis-evidence-identity-unavailable",
                "the exact provider could not identify the finite representation and "
                "encoding used by its bounded queries",
                error=f"{type(exc).__name__}: {exc}")
            return self.finish(Verdict.REFUSED, refusal.why,
                          evidence={"refusal": refusal.as_dict()})
        if type(self.identities) is not dict \
                or set(self.identities) != {"representation", "encoding"} \
                or any(type(value) is not str or not value for value in self.identities.values()):
            refusal = Refusal(
                "analysis-evidence-identity-invalid",
                "the exact provider must return exactly one stable representation identity "
                "and one stable encoding identity",
                got=repr(self.identities))
            return self.finish(Verdict.REFUSED, refusal.why,
                          evidence={"refusal": refusal.as_dict()})
        try:
            self.audit_plan = _audit_plan(self.exact)
        except Refused as exc:
            return self.finish(Verdict.REFUSED, exc.refusal.why,
                          evidence={"refusal": exc.refusal.as_dict()})

        self.binding_evidence = AnalysisBinding(
            task_contract=self.exact.domain.task_contract,
            artifact=self.exact.domain.artifact,
            provider=binding,
            domain=self.exact.domain.digest(),
            catalogue=self.evaluation.catalogue.digest(),
            domain_manifest=self.domain_manifest.digest(),
            representation=self.identities["representation"],
            encoding=self.identities["encoding"],
            audit_plan=self.audit_plan.digest())
        self.passed_checks.extend((
            PassedCheck("domain_manifest_binding", (
                "domain-manifest:" + self.binding_evidence.domain_manifest,
                "provider:" + self.binding_evidence.provider,
            )),
            PassedCheck("task_binding", (
                "domain:" + self.binding_evidence.domain,
                "task-contract:" + self.binding_evidence.task_contract,
                "artifact:" + self.binding_evidence.artifact,
                "provider:" + self.binding_evidence.provider,
            )),
            PassedCheck("catalogue_binding", (
                "catalogue:" + self.binding_evidence.catalogue,
                "provider:" + self.binding_evidence.provider,
            )),
            PassedCheck("audit_plan_binding", (
                "audit-plan:" + self.binding_evidence.audit_plan,
                "provider:" + self.binding_evidence.provider,
            )),
        ))

        try:
            preflight_passed = _preflight(self.exact, self.audit_plan)
            self.trusted = _trusted_assumptions(self.exact)
        except Refused as exc:
            return self.finish(Verdict.REFUSED, exc.refusal.why,
                          evidence={"refusal": exc.refusal.as_dict()})
        for name in preflight_passed:
            subjects = (
                ("representation:" + self.binding_evidence.representation,
                 "encoding:" + self.binding_evidence.encoding)
                if name == "representation_well_formed" else
                ("catalogue:" + self.binding_evidence.catalogue,
                 "encoding:" + self.binding_evidence.encoding)
            )
            self.passed_checks.append(PassedCheck(name, subjects))

    def read_records(self, record_audit):
        """Admit retained records once; keep receipts even if a later record stops."""
        self.audited, self.audited_witnesses, self.witness_sources = [], [], []
        self.completed_audits = []
        supported = {}
        self.audit_rows, seen = [], set()
        if self.evaluation.results and record_audit is None:
            refusal = Refusal(
                "analysis-record-audit-missing",
                "the concrete evaluation contains raw run records and this frontend did "
                "not supply the typed audit that can admit them into the semantic domain")
            return self.finish(Verdict.REFUSED, refusal.why,
                          evidence={"refusal": refusal.as_dict()})

        for index, result in _record_audit_order(self.evaluation, horizon=self.exact.domain.bound):
            try:
                check_analysis()
                redundant = _redundant_record_support(
                    result, self.evaluation, self.audited_witnesses, supported, horizon=self.exact.domain.bound)
                if redundant is not None:
                    self.audit_rows.append({"record": index, **redundant})
                    continue
                got = record_audit(result, self.exact)
            except AnalysisStopped as exc:
                # A later record must not erase earlier completed audit receipts.
                # These are progress diagnostics, not a completed goal partition:
                # the remaining trace checks and closure have not run.
                refusal = Refusal(
                    'analysis-budget-exhausted',
                    'the shared analysis wall budget ended during record admission; '
                    'completed record audits are retained but classification is incomplete',
                    wall_seconds=exc.wall_seconds, elapsed_seconds=exc.elapsed_seconds,
                    notes=list(getattr(exc, '__notes__', ())), phase='record-audit',
                    interrupted_record=index, completed_records=len(self.audit_rows),
                    completed_record_audits=sum(
                        row['disposition'] != 'not-selected' for row in self.audit_rows),
                    admitted_witnesses=len(self.audited_witnesses),
                    admitted_record_hits=sorted(supported))
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={'refusal': refusal.as_dict(),
                                        'record_audits': self.audit_rows})
            except WitnessInconsistent as exc:
                got = RecordAudit.inconsistent(str(exc))
            except AuditedWitnessLimitError as exc:
                got = RecordAudit.refused(
                    "analysis-audited-witness-evidence-too-large",
                    "the complete replay exceeds the public evidence carrier and "
                    f"cannot support a bounded conclusion ({exc})")
            except Refused as exc:
                got = RecordAudit.refused(
                    exc.refusal.kind, exc.refusal.why, **exc.refusal.detail)
            except Exception as exc:  # noqa: BLE001 - a frontend boundary
                got = RecordAudit.refused(
                    "analysis-record-audit-failed",
                    f"the record audit raised {type(exc).__name__}: {exc}")
            if type(got) is not RecordAudit:
                refusal = Refusal(
                    "analysis-record-audit-result-invalid",
                    "the record auditor returned something outside its closed result "
                    "algebra",
                    index=index, got=type(got).__name__)
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict(),
                                        "record_audits": self.audit_rows})
            row = {"record": index, **got.as_dict()}
            self.audit_rows.append(row)
            if got.disposition is AuditDisposition.OUTSIDE:
                continue
            if got.disposition is AuditDisposition.INCONSISTENT:
                issue = Refusal(
                    "analysis-record-audit-inconsistent",
                    got.why, index=index)
                return self.finish(
                    Verdict.INCONSISTENT, got.why,
                    evidence={"inconsistency": issue.as_dict(),
                              "record_audits": self.audit_rows})
            if got.disposition is AuditDisposition.REFUSED:
                return self.finish(
                    Verdict.REFUSED, got.why,
                              evidence={"refusal": Refusal(got.kind, got.why, **got.detail).as_dict(),
                                        "record_audits": self.audit_rows})
            if got.witness.domain != self.exact.domain.digest():
                issue = Refusal(
                    "analysis-record-audit-domain-mismatch",
                    "the record audit admitted a complete witness stamped for another "
                    "trace domain",
                    index=index, witness=got.witness.domain,
                    analysis=self.exact.domain.digest())
                return self.finish(
                    Verdict.INCONSISTENT, issue.why,
                    evidence={"inconsistency": issue.as_dict(),
                              "record_audits": self.audit_rows})
            try:
                wrong = self.exact.validate(got.trace)
            except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
                refusal = Refusal(
                    "analysis-domain-validation-failed",
                    "the bound instance could not validate a trace admitted by the record "
                    "audit",
                    index=index, error=f"{type(exc).__name__}: {exc}")
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict(),
                                        "record_audits": self.audit_rows})
            if wrong:
                refusal = Refusal(
                    "analysis-audit-admitted-outside-trace",
                    "the record audit called a trace admitted but the bound domain rejects "
                    "it, so the audit and domain disagree",
                    index=index, first=str(wrong[0]))
                return self.finish(Verdict.INCONSISTENT, refusal.why,
                              evidence={"inconsistency": refusal.as_dict(),
                                        "record_audits": self.audit_rows})
            verified = got.audits is not None and all(
                got.audits.requires(kind) for kind in (AuditKind.STEP, AuditKind.SOURCE))
            replay_stop = None if verified else _witness_replay(
                self.exact, got.witness, f"evaluated-record:{index}")
            if replay_stop is not None:
                verdict, issue = replay_stop
                key = "inconsistency" if verdict is Verdict.INCONSISTENT else "refusal"
                return self.finish(
                    verdict, issue.why,
                    evidence={key: issue.as_dict(), "record_audits": self.audit_rows})
            try:
                key = got.witness.fingerprint
            except Exception as exc:  # noqa: BLE001 - a frontend boundary
                refusal = Refusal(
                    "analysis-audited-trace-not-serializable",
                    "an admitted trace has no stable finite evidence image",
                    index=index, error=f"{type(exc).__name__}: {exc}")
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict(),
                                        "record_audits": self.audit_rows})
            if key not in seen:
                self.audited.append(got.trace)
                self.audited_witnesses.append(got.witness)
                self.completed_audits.append(got.audits)
                seen.add(key)
            self.witness_sources.append(
                (WitnessOrigin.EVALUATED_RECORD,
                 got.witness, f"record:{index}", ""))
            self.passed_checks.append(PassedCheck(
                "record_audit",
                ("record:" + str(index), "witness:" + got.witness.fingerprint)))
            for goal in got.witness.hits:
                supported.setdefault(goal, "witness:" + got.witness.fingerprint)

    def establish_feasibility(self):
        """Use an admitted witness, or one feasibility query, before goal closure."""
        feasibility_timeout_ms = self.analysis_policy.feasibility_timeout_ms
        self.feasibility = {}
        if self.audited:
            self.feasibility = {
                "established_by": "audited-pool",
                "witness": self.audited_witnesses[0].as_record(),
                "solver_calls": 0,
            }
        else:
            try:
                feasibility_timeout_ms = remaining_query_ms(feasibility_timeout_ms)
                answer = self.exact.feasible(
                    **({"timeout_ms": feasibility_timeout_ms}
                       if feasibility_timeout_ms is not None else {}))
            except WitnessInconsistent as exc:
                return self.finish(Verdict.INCONSISTENT, exc.why,
                              evidence={"inconsistency": exc.as_dict(),
                                        "record_audits": self.audit_rows})
            except _FeasibilityDetailError as exc:
                issue = Refusal(
                    ("analysis-feasibility-witness-undecodable"
                     if exc.answer is Feasible.YES else
                     "analysis-feasibility-result-invalid"),
                    ("the feasibility query reported SAT but its decoded request has no "
                     "exact finite evidence representation"
                     if exc.answer is Feasible.YES else
                     "the feasibility query returned detail with no exact finite "
                     "evidence representation"),
                    error=exc.why)
                verdict = (Verdict.INCONSISTENT if exc.answer is Feasible.YES
                           else Verdict.REFUSED)
                key = "inconsistency" if verdict is Verdict.INCONSISTENT else "refusal"
                return self.finish(
                    verdict, issue.why,
                    evidence={key: issue.as_dict(), "record_audits": self.audit_rows})
            except Refused as exc:
                return self.finish(Verdict.REFUSED, exc.refusal.why,
                              evidence={"refusal": exc.refusal.as_dict(),
                                        "record_audits": self.audit_rows})
            except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
                refusal = Refusal(
                    "analysis-feasibility-query-failed",
                    "the exact feasibility query could not be carried out",
                    error=f"{type(exc).__name__}: {exc}")
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict(),
                                        "record_audits": self.audit_rows})
            if type(answer) is not FeasibilityCall:
                refusal = Refusal(
                    "analysis-feasibility-result-invalid",
                    "the bound exact provider returned something outside the closed "
                    "feasibility-call algebra",
                    got=repr(answer))
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict(),
                                        "record_audits": self.audit_rows})
            status, detail = answer
            self.feasibility = {"established_by": "exact-query",
                           "answer": status.value, "solver_calls": 1}
            if answer.telemetry is not None:
                self.feasibility["telemetry"] = answer.telemetry.as_dict()
            if status is Feasible.NO:
                self.feasibility["detail"] = (str(detail)
                                         or "the exact query found no trace in this domain")
                return self.finish(
                    Verdict.INFEASIBLE_DOMAIN,
                    self.feasibility["detail"],
                    evidence={"record_audits": self.audit_rows,
                              "feasibility": self.feasibility,
                              "domain": self.exact.domain.as_dict(),
                              "domain_digest": self.exact.domain.digest()})
            if status is Feasible.UNRESOLVED:
                self.feasibility["detail"] = (str(detail)
                                         or "the exact feasibility query was unresolved")
                return self.finish(
                    Verdict.FEASIBILITY_UNRESOLVED,
                    self.feasibility["detail"],
                    evidence={"record_audits": self.audit_rows,
                              "feasibility": self.feasibility,
                              "domain": self.exact.domain.as_dict(),
                              "domain_digest": self.exact.domain.digest()})

            try:
                decoded = _snapshot_trace(detail)
                self.feasibility["decoded_run_request"] = _plain(decoded)
            except (TypeError, ValueError, OverflowError, RecursionError) as exc:
                refusal = Refusal(
                    "analysis-feasibility-witness-undecodable",
                    "the feasibility query reported SAT but its decoded request has no "
                    "exact finite evidence representation",
                    error=str(exc))
                return self.finish(Verdict.INCONSISTENT, refusal.why,
                              evidence={"inconsistency": refusal.as_dict(),
                                        "record_audits": self.audit_rows})
            admitted = self.exact.audit_request(decoded)
            if type(admitted) is AuditCheck:
                if admitted.status is AuditCheckStatus.INCONSISTENT:
                    refusal = Refusal(
                        "analysis-feasibility-witness-inconsistent",
                        "the feasibility query's decoded request did not audit to one "
                        "complete trace",
                        first=admitted.findings[0], count=len(admitted.findings))
                    return self.finish(Verdict.INCONSISTENT, refusal.why,
                                  evidence={"inconsistency": refusal.as_dict(),
                                            "record_audits": self.audit_rows,
                                            "feasibility": self.feasibility})
                refusal = admitted.refusal
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict(),
                                        "record_audits": self.audit_rows,
                                        "feasibility": self.feasibility})
            if type(admitted) is not AuditedWitness:
                refusal = Refusal(
                    "analysis-feasibility-witness-audit-result-invalid",
                    "the feasibility query's decoded request returned neither a complete "
                    "AuditedWitness nor a typed audit result",
                    got=type(admitted).__name__)
                return self.finish(Verdict.REFUSED, refusal.why,
                              evidence={"refusal": refusal.as_dict(),
                                        "record_audits": self.audit_rows,
                                        "feasibility": self.feasibility})
            replay_stop = _witness_replay(
                self.exact, admitted, "feasibility-query")
            if replay_stop is not None:
                verdict, issue = replay_stop
                key = "inconsistency" if verdict is Verdict.INCONSISTENT else "refusal"
                return self.finish(
                    verdict, issue.why,
                    evidence={key: issue.as_dict(),
                              "record_audits": self.audit_rows,
                              "feasibility": self.feasibility})
            self.feasibility["witness"] = admitted.as_record()
            self.feasibility["replayed"] = True
            self.audited.append(admitted.inputs)
            self.audited_witnesses.append(admitted)
            self.completed_audits.append(None)
            self.witness_sources.append((
                WitnessOrigin.FEASIBILITY_QUERY, admitted, "", ""))

        stopped = _trace_audits(self.exact, self.audited, self.audit_plan, self.completed_audits)
        if stopped is not None:
            verdict, issue = stopped
            key = "inconsistency" if verdict is Verdict.INCONSISTENT else "refusal"
            return self.finish(verdict, issue.why,
                          evidence={key: issue.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility,
                                    "domain": self.exact.domain.as_dict(),
                                    "domain_digest": self.exact.domain.digest()})
        self.trace_required = tuple(
            audit.value for audit in self.audit_plan.audits
            if audit in {AuditKind.STEP, AuditKind.SOURCE})
        for witness in self.audited_witnesses:
            witness_ref = "witness:" + witness.fingerprint
            for name in self.trace_required:
                self.passed_checks.append(PassedCheck(name, (witness_ref,)))

    def query_remaining(self, query_execution):
        """Delegate residual-goal scheduling to the existing closure algorithm."""
        try:
            self.closure = closure_mod.close_over(
                self.exact, pool=self.audited, batch=self.analysis_policy.batch,
                _audited_witnesses=tuple(self.audited_witnesses),
                catalogue_manifest=self.evaluation.catalogue,
                audit_plan=self.audit_plan,
                **query_execution,
                **({"timeout_ms": self.analysis_policy.query_timeout_ms}
                   if self.analysis_policy.query_timeout_ms is not None else {}))
        except closure_mod.WitnessInconsistent as exc:
            return self.finish(Verdict.INCONSISTENT, exc.why,
                          evidence={"inconsistency": exc.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility,
                                    "queries": exc.queries,
                                    "domain": self.exact.domain.as_dict(),
                                    "domain_digest": self.exact.domain.digest()})
        except closure_mod.QueryRefused as exc:
            return self.finish(Verdict.REFUSED, exc.refusal.why,
                          evidence={"refusal": exc.refusal.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility,
                                    "queries": exc.queries})
        except Refused as exc:
            return self.finish(Verdict.REFUSED, exc.refusal.why,
                          evidence={"refusal": exc.refusal.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility})
        except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
            refusal = Refusal(
                "analysis-goal-query-failed",
                "the exact goal closure could not be carried out",
                error=f"{type(exc).__name__}: {exc}")
            return self.finish(Verdict.REFUSED, refusal.why,
                          evidence={"refusal": refusal.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility})

        if self.closure.broken:
            refusal = Refusal(
                "analysis-audited-pool-replay-failed",
                "a retained audited trace could not be replayed during goal closure",
                first=str(self.closure.broken[0]), count=len(self.closure.broken))
            return self.finish(Verdict.REFUSED, refusal.why,
                          evidence={"refusal": refusal.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility})
        if not self.closure.query_transcript_complete:
            refusal = Refusal(
                "analysis-query-transcript-missing",
                "the exact provider returned only an aggregate goal partition, not the "
                "bounded logical queries needed to support it")
            return self.finish(
                Verdict.REFUSED, refusal.why,
                evidence={"refusal": refusal.as_dict(),
                          "record_audits": self.audit_rows,
                          "feasibility": self.feasibility,
                          "closure": self.closure.summary(),
                          "domain": self.exact.domain.as_dict()})

    def check_binding(self):
        """Reject provider/domain changes before publishing any classification."""
        try:
            ending_manifest = _provider_domain_manifest(self.exact)
            ending_catalogue = (
                None if ending_manifest == self.domain_manifest else Refusal(
                    "analysis-domain-manifest-changed",
                    "the exact provider changed its domain/catalogue binding while "
                    "bounded evidence was being gathered",
                    before=self.domain_manifest.digest(), after=ending_manifest.digest()))
        except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
            ending_catalogue = Refusal(
                "analysis-catalogue-unavailable",
                "the exact provider could not reproduce its domain manifest after the "
                "bounded queries",
                error=f"{type(exc).__name__}: {exc}")
        if ending_catalogue is not None:
            unavailable = ending_catalogue.kind == "analysis-catalogue-unavailable"
            verdict = Verdict.REFUSED if unavailable else Verdict.INCONSISTENT
            issue = Refusal(
                (ending_catalogue.kind if unavailable
                 else "analysis-domain-manifest-changed"),
                (ending_catalogue.why if unavailable else
                 "the exact provider's domain manifest changed while bounded evidence was "
                 "being gathered"),
                **({"cause": ending_catalogue.kind} if not unavailable else {}))
            key = "refusal" if verdict is Verdict.REFUSED else "inconsistency"
            return self.finish(verdict, issue.why,
                          evidence={key: issue.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility,
                                    "closure": self.closure.summary(),
                                    "domain": self.exact.domain.as_dict()})

        try:
            ending_trusted = _trusted_assumptions(self.exact)
        except Refused as exc:
            return self.finish(Verdict.REFUSED, exc.refusal.why,
                          evidence={"refusal": exc.refusal.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility})
        if canonical_json([item.as_dict() for item in ending_trusted]) != \
                canonical_json([item.as_dict() for item in self.trusted]):
            issue = Refusal(
                "analysis-trusted-assumptions-changed",
                "the exact provider changed the universal premises it declares while "
                "bounded evidence was being gathered")
            return self.finish(Verdict.INCONSISTENT, issue.why,
                          evidence={"inconsistency": issue.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility,
                                    "closure": self.closure.summary(),
                                    "domain": self.exact.domain.as_dict()})

        try:
            current_provider = self.exact.analysis_identity()
            current_identities = self.exact.evidence_identity()
        except Exception as exc:  # noqa: BLE001 - an exact-provider boundary
            refusal = Refusal(
                "analysis-evidence-identity-recheck-failed",
                "the exact provider could not reproduce its identities after the queries",
                error=f"{type(exc).__name__}: {exc}")
            return self.finish(Verdict.REFUSED, refusal.why,
                          evidence={"refusal": refusal.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility})
        if (current_provider != self.binding_evidence.provider
                or current_identities != self.identities):
            issue = Refusal(
                "analysis-evidence-identity-changed",
                "the provider, finite representation or encoding identity changed while "
                "the bounded evidence was being gathered",
                before={
                    "provider": self.binding_evidence.provider,
                    "representation": self.binding_evidence.representation,
                    "encoding": self.binding_evidence.encoding,
                },
                after={
                    "provider": current_provider,
                    **(current_identities if type(current_identities) is dict else
                       {"identity": repr(current_identities)}),
                })
            return self.finish(Verdict.INCONSISTENT, issue.why,
                          evidence={"inconsistency": issue.as_dict(),
                                    "record_audits": self.audit_rows,
                                    "feasibility": self.feasibility,
                                    "closure": self.closure.summary(),
                                    "domain": self.exact.domain.as_dict()})

    def report(self):
        """Assemble typed goal evidence; never replace the concrete decision."""
        for query in self.closure.queries:
            if query.answer is SolverAnswer.SAT:
                self.witness_sources.append((
                    WitnessOrigin.BOUNDED_QUERY,
                    query.witness, "", query.query_id))
                for name in query.passed_audits:
                    self.passed_checks.append(PassedCheck(
                        name, (query.query_id, query.witness_ref)))
        witnesses = tuple(
            WitnessEvidence(
                origin,
                witness.as_record() if type(witness) is AuditedWitness else witness,
                raw_record_ref=raw_record_ref,
                query_ref=query_ref,
                passed_audits=self.trace_required)
            for origin, witness, raw_record_ref, query_ref in self.witness_sources)
        try:
            per_goal = _per_goal(self.closure, witnesses)
        except Refused as exc:
            return self.finish(
                Verdict.REFUSED, exc.refusal.why,
                evidence={"refusal": exc.refusal.as_dict(),
                          "record_audits": self.audit_rows,
                          "feasibility": self.feasibility,
                          "closure": self.closure.summary(),
                          "domain": self.exact.domain.as_dict()})
        evidence = {
            "record_audits": self.audit_rows,
            "feasibility": self.feasibility,
            "closure": self.closure.summary(),
            "domain": self.exact.domain.as_dict(),
            "domain_digest": self.exact.domain.digest(),
            "audit_plan": self.audit_plan,
            "passed_checks": tuple(self.passed_checks),
            "trusted_assumptions": self.trusted,
            "witnesses": witnesses,
        }
        return self.finish(Verdict.CLASSIFIED, per_goal=per_goal,
                      evidence=evidence, closure_record=self.closure)


def _analyse_subject(evaluation, exact=None, record_audit=None, *,
                     exact_refusal=None, goal_capability_use=None,
                     feasibility_timeout_ms=None, query_timeout_ms=None,
                     batch=None, _query_execution=None,
                     _artifact_timeout_ms=None):
    """Run the shared score-read-only Analysis lifecycle in explicit stages."""
    if type(evaluation) is not _AnalysisSubject:
        raise TypeError("the internal analyzer needs its closed analysis subject")
    run = _AnalysisRun(evaluation, exact)
    if _query_execution is None:
        _query_execution = {}
    elif type(_query_execution) is not dict \
            or any(type(name) is not str or not name
                   for name in _query_execution):
        raise TypeError("internal query execution options need a plain string-keyed map")
    else:
        _query_execution = dict(_query_execution)
    if set(_query_execution) & {"timeout_ms", "steps"}:
        raise ValueError(
            "internal query execution cannot replace the analysis timeout or domain")
    requested_policy = {
        "feasibility_timeout_ms": feasibility_timeout_ms,
        "query_timeout_ms": query_timeout_ms,
        "batch": batch,
        "artifact_timeout_ms": _artifact_timeout_ms,
    }

    stopped = run.bind(requested_policy, exact_refusal, goal_capability_use)
    if stopped is not None:
        return stopped
    stopped = run.read_records(record_audit)
    if stopped is not None:
        return stopped
    stopped = run.establish_feasibility()
    if stopped is not None:
        return stopped
    stopped = run.query_remaining(_query_execution)
    if stopped is not None:
        return stopped
    stopped = run.check_binding()
    if stopped is not None:
        return stopped
    return run.report()
