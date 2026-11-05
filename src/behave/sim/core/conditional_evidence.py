"""Typed evidence for one conditional bounded-analysis conclusion.

The appendix's conclusion is conditional on a finite domain, representation and
encoding.  A free-form dictionary cannot hold that boundary: it permits a caller to
omit the encoding identity, attach goal answers from another catalogue, or spell a
solver timeout as an UNSAT answer.  The records here are the closed serialization
boundary used by :mod:`behave.sim.core.analysis`.

They intentionally contain no frontend objects.  Behavior and RTL provide identities
and traces; the core records their finite JSON images and checks the relationships that
are common to both frontends.
"""

from __future__ import annotations

import enum
import hashlib
import json
import math
from types import MappingProxyType

from .audit import AuditedWitness
from .audit_plan import AuditKind, AuditPlan
from .catalogue import CatalogueManifest, DomainManifest
from .frozen import Frozen, FrozenMap
from .query import SolverAnswer
from .query_telemetry import (
    QueryKind,
    QueryTelemetry,
    analysis_semantic_instance,
    query_kind_for_goals,
)
from .refusal import Refusal

__all__ = [
    "AnalysisBinding",
    "AnalysisPolicy",
    "AnalysisStopEvidence",
    "AnalysisStopKind",
    "ClassifiedGoalStatus",
    "ConditionalEvidence",
    "FeasibilityAnswer",
    "FeasibilityEvidence",
    "FeasibilitySource",
    "GoalEvidenceRecord",
    "IssueEvidence",
    "PassedCheck",
    "QueryEvidence",
    "TrustedAssumption",
    "WitnessEvidence",
    "WitnessOrigin",
]


def _image(value, what="evidence"):
    """Return a detached strict-JSON image of ``value``."""
    def plain(item, path, active):
        kind = type(item)
        if item is None or kind in (bool, int, str):
            return item
        if kind is float:
            if not math.isfinite(item):
                raise ValueError(f"{path} contains a non-finite float")
            return item
        if kind is CatalogueManifest:
            item = item.as_dict()
            kind = dict
        if kind in (dict, MappingProxyType, FrozenMap):
            identity = id(item)
            if identity in active:
                raise ValueError(f"{path} contains a reference cycle")
            active.add(identity)
            try:
                out = {}
                for key, child in item.items():
                    if type(key) is not str:
                        raise TypeError(f"{path} has a non-string key {key!r}")
                    out[key] = plain(child, f"{path}.{key}", active)
                return out
            finally:
                active.remove(identity)
        if kind in (tuple, list):
            identity = id(item)
            if identity in active:
                raise ValueError(f"{path} contains a reference cycle")
            active.add(identity)
            try:
                return [plain(child, f"{path}[{index}]", active)
                        for index, child in enumerate(item)]
            finally:
                active.remove(identity)
        raise TypeError(f"{path} contains an unsupported {kind.__name__}")

    try:
        value = plain(value, what, set())
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise TypeError(f"{what} has no finite JSON image: {exc}") from None


def _digest(value):
    encoded = json.dumps(
        _image(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _nonempty(value, what):
    if type(value) is not str or not value:
        raise ValueError(f"{what} must be a nonempty string")
    return value


def _strings(values, what, *, nonempty=True):
    values = tuple(values)
    if any(type(value) is not str or (nonempty and not value)
           for value in values):
        raise TypeError(f"{what} must contain only exact strings")
    return values


def _audited_witness(value, what):
    """Validate and canonicalize one complete audited-witness image.

    Evidence constructors accept serialized records because they are also the report
    boundary.  Re-running the closed witness constructor here prevents a plausible JSON
    dictionary with malformed rows, hit sets, or trace lengths from acquiring the same
    standing as a replay-produced :class:`AuditedWitness`.
    """
    image = _image(value, what)
    if type(image) is not dict:
        raise ValueError(f"{what} is not a complete trace record")
    try:
        witness = AuditedWitness.from_record(image)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} is not a valid audited witness: {exc}") from None
    return witness


def _witness_hits(image):
    return frozenset(image.get("initial_hits", ())) | frozenset(
        goal for step in image["hit_sets"] for goal in step)


class AnalysisPolicy(Frozen):
    """Resource policy used for this analysis, never part of its trace domain."""

    __slots__ = (
        "artifact_timeout_ms", "batch", "feasibility_timeout_ms",
        "query_timeout_ms",
    )

    def __init__(self, feasibility_timeout_ms=None, query_timeout_ms=None,
                 batch=None, artifact_timeout_ms=None):
        for name, value in (
                ("feasibility_timeout_ms", feasibility_timeout_ms),
                ("query_timeout_ms", query_timeout_ms), ("batch", batch),
                ("artifact_timeout_ms", artifact_timeout_ms)):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be a positive integer or absent")
            setattr(self, name, value)
        self._freeze()

    def as_dict(self):
        return {
            "feasibility_timeout_ms": self.feasibility_timeout_ms,
            "query_timeout_ms": self.query_timeout_ms,
            "batch": self.batch,
            "artifact_timeout_ms": self.artifact_timeout_ms,
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("an analysis-policy image must be a mapping")
        got = cls(
            feasibility_timeout_ms=value.get("feasibility_timeout_ms"),
            query_timeout_ms=value.get("query_timeout_ms"),
            batch=value.get("batch"),
            artifact_timeout_ms=value.get("artifact_timeout_ms"))
        if got.as_dict() != value:
            raise ValueError("an analysis-policy image has unknown or missing fields")
        return got


class AnalysisBinding(Frozen):
    """Identities whose equality makes the evidence one conditional claim."""

    __slots__ = (
        "artifact", "audit_plan",
        "catalogue",
        "domain",
        "domain_manifest",
        "encoding",
        "provider",
        "representation",
        "task_contract",
    )

    def __init__(self, *, task_contract, artifact, provider, domain, catalogue,
                 domain_manifest, representation, encoding, audit_plan):
        for name, value in (
                ("task_contract", task_contract), ("artifact", artifact),
                ("provider", provider), ("domain", domain),
                ("catalogue", catalogue), ("representation", representation),
                ("domain_manifest", domain_manifest),
                ("encoding", encoding), ("audit_plan", audit_plan)):
            setattr(self, name, _nonempty(value, f"analysis binding {name}"))
        self._freeze()

    def as_dict(self):
        return {
            "task_contract": self.task_contract,
            "artifact": self.artifact,
            "provider_identity": self.provider,
            "domain": self.domain,
            "catalogue": self.catalogue,
            "domain_manifest": self.domain_manifest,
            "representation": self.representation,
            "encoding": self.encoding,
            "audit_plan": self.audit_plan,
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("an analysis-binding image must be a mapping")
        got = cls(
            task_contract=value.get("task_contract"),
            artifact=value.get("artifact"),
            provider=value.get("provider_identity"),
            domain=value.get("domain"),
            catalogue=value.get("catalogue"),
            domain_manifest=value.get("domain_manifest"),
            representation=value.get("representation"),
            encoding=value.get("encoding"),
            audit_plan=value.get("audit_plan"))
        if got.as_dict() != value:
            raise ValueError("an analysis-binding image has unknown or missing fields")
        return got


def _check_binding_domain(binding, domain, what):
    """Require all identities shared by a binding and its domain to agree."""
    if binding.domain != domain.digest():
        raise ValueError(f"the {what} domain identity does not name this domain")
    if binding.task_contract != domain.task_contract:
        raise ValueError(
            f"the {what} task-contract identity does not name this domain")
    if binding.artifact != domain.artifact:
        raise ValueError(f"the {what} artifact identity does not name this domain")
    if binding.provider != domain.artifact:
        raise ValueError(
            f"the {what} provider identity does not name this domain's artifact")


class PassedCheck(Frozen):
    """One check that actually ran and agreed on named evidence subjects.

    A required-audit *name* says what must run; it is not evidence that the check ran.
    This record is the latter.  Subject references make it impossible to satisfy a
    witness audit with an unrelated sample.
    """

    __slots__ = ("check_id", "name", "subjects")

    def __init__(self, name, subjects):
        self.name = _nonempty(name, "passed-check name")
        subjects = tuple(sorted(_strings(subjects, "passed-check subjects")))
        if not subjects or any(not item for item in subjects) \
                or len(subjects) != len(set(subjects)):
            raise ValueError(
                "a passed check needs a nonempty set of distinct subject references")
        self.subjects = subjects
        self.check_id = "check:" + _digest({
            "name": self.name,
            "subjects": list(self.subjects),
            "result": "agreed",
        })
        self._freeze()

    def as_dict(self):
        return {
            "check_id": self.check_id,
            "name": self.name,
            "result": "agreed",
            "subjects": list(self.subjects),
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a passed-check image must be a mapping")
        got = cls(value.get("name"), value.get("subjects", ()))
        if got.as_dict() != value:
            raise ValueError("a passed-check image is not canonical")
        return got


class TrustedAssumption(Frozen):
    """A universal premise listed beside, and never discharged by, finite checks."""

    __slots__ = ("code", "scope", "statement")

    def __init__(self, code, statement, *, scope):
        self.code = _nonempty(code, "trusted-assumption code")
        self.statement = _nonempty(
            statement, "trusted-assumption statement")
        self.scope = _nonempty(scope, "trusted-assumption scope")
        self._freeze()

    def as_dict(self):
        return {
            "code": self.code,
            "scope": self.scope,
            "statement": self.statement,
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a trusted-assumption image must be a mapping")
        got = cls(
            value.get("code"), value.get("statement"),
            scope=value.get("scope"))
        if got.as_dict() != value:
            raise ValueError("a trusted-assumption image is not canonical")
        return got


class IssueEvidence(Frozen):
    """A stable refusal or inconsistency, detached from mutable detail."""

    __slots__ = ("_detail", "kind", "why")

    def __init__(self, kind, why, detail=None):
        self.kind = _nonempty(kind, "analysis issue kind")
        self.why = _nonempty(why, "analysis issue explanation")
        if detail is None:
            detail = {}
        self._detail = json.dumps(
            _image(detail, "analysis issue detail"),
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False)
        self._freeze()

    @classmethod
    def from_refusal(cls, refusal):
        if type(refusal) is not Refusal:
            raise TypeError("an analysis issue must be built from the exact Refusal type")
        # Cross the public record boundary through its detached image.  The live
        # ``detail`` view is intentionally recursively immutable and is not a second
        # JSON carrier for conditional evidence to interpret.
        detail = refusal.as_dict().get("detail", {})
        return cls(refusal.kind, refusal.why, detail)

    @property
    def detail(self):
        return json.loads(self._detail)

    def as_dict(self):
        out = {"kind": self.kind, "why": self.why}
        if self.detail:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("an analysis-issue image must be a mapping")
        got = cls(value.get("kind"), value.get("why"), value.get("detail"))
        if got.as_dict() != value:
            raise ValueError("an analysis-issue image has unknown or missing fields")
        return got


class FeasibilitySource(enum.Enum):
    AUDITED_POOL = "audited-pool"
    EXACT_QUERY = "exact-query"

    def __bool__(self):
        raise TypeError("a feasibility source is not a truth value")


class FeasibilityAnswer(enum.Enum):
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNRESOLVED = "unresolved"

    def __bool__(self):
        raise TypeError("a feasibility answer is not a truth value")


class FeasibilityEvidence(Frozen):
    """The one fact that establishes, refutes or leaves feasibility open."""

    __slots__ = (
        "_decoded_request", "_witness", "answer", "detail", "replayed",
        "solver_calls", "source", "telemetry", "witness_ref")

    def __init__(self, source, *, answer=FeasibilityAnswer.FEASIBLE, witness=None,
                 decoded_request=None, solver_calls=None, replayed=False,
                 detail="", telemetry=None):
        if type(source) is not FeasibilitySource:
            raise TypeError("feasibility evidence needs a FeasibilitySource")
        if type(answer) is not FeasibilityAnswer:
            raise TypeError("feasibility evidence needs a FeasibilityAnswer")
        if type(replayed) is not bool:
            raise TypeError("feasibility replay status must be an exact Boolean")
        self.source = source
        self.answer = answer
        expected_calls = 0 if self.source is FeasibilitySource.AUDITED_POOL else 1
        if solver_calls is None:
            solver_calls = expected_calls
        if type(solver_calls) is not int or solver_calls != expected_calls:
            raise ValueError(
                f"{self.source.value} feasibility requires {expected_calls} solver call(s)")
        self.solver_calls = solver_calls
        if self.source is FeasibilitySource.AUDITED_POOL \
                and answer is not FeasibilityAnswer.FEASIBLE:
            raise ValueError("a nonempty audited pool establishes feasibility")
        if self.source is FeasibilitySource.AUDITED_POOL and witness is None:
            raise ValueError("audited-pool feasibility must carry its witness")
        if self.source is FeasibilitySource.AUDITED_POOL \
                and decoded_request is not None:
            raise ValueError("an audited-pool witness has no solver request")
        if answer is not FeasibilityAnswer.FEASIBLE and witness is not None:
            raise ValueError("an infeasible or unresolved answer cannot carry a witness")
        if answer is not FeasibilityAnswer.FEASIBLE \
                and decoded_request is not None:
            raise ValueError("an infeasible or unresolved answer has no decoded request")
        if self.source is FeasibilitySource.EXACT_QUERY \
                and answer is FeasibilityAnswer.FEASIBLE:
            if decoded_request is None:
                raise ValueError(
                    "a SAT feasibility response needs its decoded request")
            if witness is not None and replayed is not True:
                raise ValueError(
                    "a retained SAT feasibility witness must have completed replay")
            if witness is None and replayed:
                raise ValueError("a feasibility response cannot replay a missing witness")
        if self.source is FeasibilitySource.AUDITED_POOL and replayed:
            raise ValueError("an audited-pool witness is not a solver witness replay")
        if type(detail) is not str:
            raise TypeError("feasibility detail must be a string")
        if answer is not FeasibilityAnswer.FEASIBLE and not detail:
            raise ValueError("an infeasible or unresolved answer must explain itself")
        audited = None if witness is None else _audited_witness(witness, "feasibility witness")
        witness_image = None if audited is None else audited.as_record()
        request_image = (None if decoded_request is None else
                         _image(decoded_request, "feasibility decoded request"))
        if self.source is FeasibilitySource.EXACT_QUERY and witness_image is not None \
                and not audited.matches_inputs(request_image):
            raise ValueError(
                "the feasibility decoded request differs from the replayed witness "
                "inputs")
        self._witness = (None if witness_image is None else json.dumps(
            witness_image, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False))
        self._decoded_request = (None if decoded_request is None else json.dumps(
            request_image, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False))
        self.witness_ref = (None if witness is None else
                            "witness:" + _digest(self.witness))
        self.replayed = replayed
        self.detail = detail
        if telemetry is not None:
            if type(telemetry) is not QueryTelemetry:
                raise TypeError(
                    "feasibility telemetry must be the exact QueryTelemetry record")
            if source is not FeasibilitySource.EXACT_QUERY:
                raise ValueError(
                    "an audited-pool feasibility fact issued no query telemetry")
            if telemetry.kind is not QueryKind.FEASIBILITY:
                raise ValueError("feasibility evidence has non-feasibility telemetry")
            expected = {
                FeasibilityAnswer.FEASIBLE: SolverAnswer.SAT.value,
                FeasibilityAnswer.INFEASIBLE: SolverAnswer.UNSAT.value,
                FeasibilityAnswer.UNRESOLVED: SolverAnswer.UNKNOWN.value,
            }[answer]
            if telemetry.outcome != expected:
                raise ValueError(
                    "feasibility evidence and query telemetry disagree")
        self.telemetry = telemetry
        self._freeze()

    @property
    def decoded_request(self):
        return (None if self._decoded_request is None
                else json.loads(self._decoded_request))

    @property
    def witness(self):
        return None if self._witness is None else json.loads(self._witness)

    @property
    def established(self):
        return (self.answer is FeasibilityAnswer.FEASIBLE
                and self.witness is not None
                and (self.source is FeasibilitySource.AUDITED_POOL or self.replayed))

    def as_dict(self):
        out = {
            "established_by": self.source.value,
            "answer": self.answer.value,
            "established": self.established,
            "solver_calls": self.solver_calls,
        }
        if self.witness is not None:
            out["witness"] = self.witness
            out["witness_ref"] = self.witness_ref
        if self.decoded_request is not None:
            out["decoded_run_request"] = self.decoded_request
        if self.replayed:
            out["replayed"] = True
        if self.detail:
            out["detail"] = self.detail
        if self.telemetry is not None:
            out["telemetry"] = self.telemetry.as_dict()
        return out

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a feasibility-evidence image must be a mapping")
        try:
            source = FeasibilitySource(value.get("established_by"))
            answer = FeasibilityAnswer(value.get("answer"))
        except ValueError as exc:
            raise ValueError("a feasibility-evidence image has an unknown enum") \
                from exc
        telemetry = value.get("telemetry")
        got = cls(
            source, answer=answer, witness=value.get("witness"),
            decoded_request=value.get("decoded_run_request"),
            solver_calls=value.get("solver_calls"),
            replayed=value.get("replayed", False),
            detail=value.get("detail", ""),
            telemetry=(None if telemetry is None
                       else QueryTelemetry.from_dict(telemetry)))
        if got.as_dict() != value:
            raise ValueError("a feasibility-evidence image is not canonical")
        return got


class QueryEvidence(Frozen):
    """One logical ``Phi[D,S]`` attempt and the audited reading of its outcome."""

    __slots__ = (
        "_decoded_request", "_witness", "answer", "goals", "order",
        "passed_audits", "query_id", "reason", "replayed_hits", "solver_calls",
        "telemetry", "witness_ref")

    def __init__(self, order, goals, answer, *, decoded_request=None,
                 audited_witness=None, replayed_hits=(), reason="",
                 passed_audits=(), telemetry=None, solver_calls=1):
        if type(order) is not int or order < 0:
            raise ValueError("an exact query order must be a nonnegative integer")
        goals = tuple(sorted(_strings(goals, "query goals")))
        if not goals or len(set(goals)) != len(goals):
            raise ValueError("an exact query must ask a nonempty set of distinct goals")
        if type(answer) is not SolverAnswer:
            raise TypeError("an exact query answer must be a SolverAnswer")
        if type(solver_calls) is not int or solver_calls < 0:
            raise ValueError("an exact query records natural solver calls")
        self.solver_calls = solver_calls
        hits = tuple(sorted(_strings(replayed_hits, "replayed hits")))
        if answer is SolverAnswer.SAT:
            if decoded_request is None:
                raise ValueError("a SAT set query needs its decoded model request")
            if audited_witness is None:
                if hits or passed_audits:
                    raise ValueError(
                        "an unaudited SAT request cannot carry replay hits or passed "
                        "audits")
            elif not hits or not set(hits) & set(goals):
                raise ValueError(
                    "an audited SAT query needs a witness reaching an asked goal")
            if reason:
                raise ValueError("a SAT answer has no unresolved reason")
        else:
            if decoded_request is not None or audited_witness is not None or hits:
                raise ValueError(
                    "only SAT carries a decoded request, witness and replay hits")
        if type(reason) is not str:
            raise TypeError("an exact query reason must be a string")
        if answer is SolverAnswer.UNKNOWN and not reason:
            raise ValueError("an unresolved query must carry the solver's reason")
        if answer is not SolverAnswer.UNKNOWN and reason:
            raise ValueError("only UNKNOWN carries an unresolved reason")
        self.goals = goals
        self.answer = answer
        self.order = order
        request_image = (None if decoded_request is None else
                         _image(decoded_request, "decoded query request"))
        audited = (None if audited_witness is None else
                   _audited_witness(audited_witness, "audited query witness"))
        witness_image = None if audited is None else audited.as_record()
        if witness_image is not None:
            if not audited.matches_inputs(request_image):
                raise ValueError(
                    "the SAT decoded request differs from the replayed witness inputs")
            if frozenset(hits) != _witness_hits(witness_image):
                raise ValueError(
                    "the SAT replayed hits differ from the retained witness hit sets")
        self._decoded_request = (None if request_image is None else json.dumps(
            request_image, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False))
        self._witness = (None if witness_image is None else json.dumps(
            witness_image, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False))
        self.replayed_hits = hits
        self.reason = reason
        self.passed_audits = tuple(sorted(_strings(
            passed_audits, "passed audit names")))
        if telemetry is not None:
            if type(telemetry) is not QueryTelemetry:
                raise TypeError(
                    "query evidence telemetry must be QueryTelemetry or absent")
            if telemetry.goals_asked != goals:
                raise ValueError(
                    "query evidence and telemetry name different asked goals")
            if telemetry.outcome != answer.value:
                raise ValueError(
                    "query evidence and telemetry name different outcomes")
            if telemetry.kind is not query_kind_for_goals(goals):
                raise ValueError(
                    "bounded-query evidence cannot carry local-probe telemetry")
        self.telemetry = telemetry
        self.witness_ref = (None if self.witness is None else
                            "witness:" + _digest(self.witness))
        self.query_id = "query:" + _digest({
            "order": order,
            "goals": list(goals),
            "answer": answer.value,
            "decoded_request": self.decoded_request,
            "reason": self.reason,
        })
        self._freeze()

    @property
    def decoded_request(self):
        return (None if self._decoded_request is None
                else json.loads(self._decoded_request))

    @property
    def witness(self):
        return None if self._witness is None else json.loads(self._witness)

    def as_dict(self):
        out = {
            "query_id": self.query_id,
            "order": self.order,
            "goals": list(self.goals),
            "answer": self.answer.value,
            "solver_calls": self.solver_calls,
            "passed_audits": list(self.passed_audits),
        }
        if self.decoded_request is not None:
            out["decoded_run_request"] = self.decoded_request
        if self.witness is not None:
            out["audited_witness"] = self.witness
            out["witness_ref"] = self.witness_ref
            out["replayed_hits"] = list(self.replayed_hits)
        if self.reason:
            out["reason"] = self.reason
        if self.telemetry is not None:
            out["telemetry"] = self.telemetry.as_dict()
        return out

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a query-evidence image must be a mapping")
        try:
            answer = SolverAnswer(value.get("answer"))
        except ValueError as exc:
            raise ValueError("a query-evidence image has an unknown answer") from exc
        telemetry = value.get("telemetry")
        got = cls(
            value.get("order"), value.get("goals", ()), answer,
            decoded_request=value.get("decoded_run_request"),
            audited_witness=value.get("audited_witness"),
            replayed_hits=value.get("replayed_hits", ()),
            reason=value.get("reason", ""),
            passed_audits=value.get("passed_audits", ()),
            solver_calls=value.get("solver_calls"),
            telemetry=(None if telemetry is None
                       else QueryTelemetry.from_dict(telemetry)))
        if got.as_dict() != value:
            raise ValueError("a query-evidence image is not canonical")
        return got


class WitnessOrigin(enum.Enum):
    EVALUATED_RECORD = "evaluated-record"
    FEASIBILITY_QUERY = "feasibility-query"
    BOUNDED_QUERY = "bounded-query"

    def __bool__(self):
        raise TypeError("a witness origin is not a truth value")


class WitnessEvidence(Frozen):
    """One complete audited trace and where it entered the evidence ledger."""

    __slots__ = (
        "_witness", "origin", "passed_audits", "query_ref", "raw_record_ref",
        "witness_ref")

    def __init__(self, origin, witness, *, raw_record_ref="", query_ref="",
                 passed_audits=()):
        if type(origin) is not WitnessOrigin:
            raise TypeError("an audited witness needs a WitnessOrigin")
        if origin is WitnessOrigin.EVALUATED_RECORD and not raw_record_ref:
            raise ValueError("an evaluated-record witness must name its raw record")
        if origin is not WitnessOrigin.EVALUATED_RECORD and raw_record_ref:
            raise ValueError("only an evaluated-record witness names a raw record")
        if origin is WitnessOrigin.BOUNDED_QUERY and not query_ref:
            raise ValueError("a bounded-query witness must name its SAT query")
        if origin is not WitnessOrigin.BOUNDED_QUERY and query_ref:
            raise ValueError("only a bounded-query witness names a bounded query")
        image = _audited_witness(witness, "audited witness").as_record()
        self._witness = json.dumps(
            image, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False)
        self.witness_ref = "witness:" + _digest(image)
        self.origin = origin
        if type(raw_record_ref) is not str or type(query_ref) is not str:
            raise TypeError("witness ledger references must be strings")
        self.raw_record_ref = raw_record_ref
        self.query_ref = query_ref
        self.passed_audits = tuple(sorted(_strings(
            passed_audits, "passed audit names")))
        self._freeze()

    @property
    def witness(self):
        return json.loads(self._witness)

    def as_dict(self):
        out = {
            "witness_ref": self.witness_ref,
            "origin": self.origin.value,
            "trace": self.witness,
            "passed_audits": list(self.passed_audits),
        }
        if self.raw_record_ref:
            out["raw_record_ref"] = self.raw_record_ref
        if self.query_ref:
            out["query_ref"] = self.query_ref
        return out

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a witness-evidence image must be a mapping")
        try:
            origin = WitnessOrigin(value.get("origin"))
        except ValueError as exc:
            raise ValueError("a witness-evidence image has an unknown origin") from exc
        got = cls(
            origin, value.get("trace"),
            raw_record_ref=value.get("raw_record_ref", ""),
            query_ref=value.get("query_ref", ""),
            passed_audits=value.get("passed_audits", ()))
        if got.as_dict() != value:
            raise ValueError("a witness-evidence image is not canonical")
        return got


class ClassifiedGoalStatus(enum.Enum):
    REACHED = "reached"
    BOUNDED_UNREACHABLE = "bounded-unreachable"
    UNRESOLVED = "unresolved"

    def __bool__(self):
        raise TypeError("a classified goal status is not a truth value")


class GoalEvidenceRecord(Frozen):
    """One goal, one status, and the evidence supporting that status."""

    __slots__ = ("detail", "status", "support_ref")

    def __init__(self, status, support_ref, *, detail=""):
        if type(status) is not ClassifiedGoalStatus:
            raise TypeError(
                "a classified goal needs an exact ClassifiedGoalStatus")
        support_ref = _nonempty(support_ref, "goal evidence support")
        if status is ClassifiedGoalStatus.REACHED \
                and not support_ref.startswith("witness:"):
            raise ValueError("a reached goal must point to an audited witness")
        if status is not ClassifiedGoalStatus.REACHED \
                and not support_ref.startswith("query:"):
            raise ValueError(
                "bounded-unreachable and unresolved goals must point to an exact query")
        self.status = status
        self.support_ref = support_ref
        if type(detail) is not str:
            raise TypeError("goal evidence detail must be a string")
        self.detail = detail
        self._freeze()

    def as_dict(self):
        out = {"status": self.status.value, "support_ref": self.support_ref}
        if self.detail:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a goal-evidence image must be a mapping")
        try:
            status = ClassifiedGoalStatus(value.get("status"))
        except ValueError as exc:
            raise ValueError("a goal-evidence image has an unknown status") from exc
        got = cls(
            status, value.get("support_ref"), detail=value.get("detail", ""))
        if got.as_dict() != value:
            raise ValueError("a goal-evidence image is not canonical")
        return got


class ConditionalEvidence(Frozen):
    """The complete evidence for one ``Classified`` result.

    The trusted assumptions are listed, not discharged.  Finite audits may refute an
    assumption, but their passing on the recorded traces is not a universal proof.
    """

    __slots__ = (
        "_catalogue", "_catalogue_manifest", "_closure", "_domain",
        "_domain_manifest", "_goals",
        "audit_plan", "binding", "checks", "feasibility", "policy", "queries",
        "record_audits", "trusted_assumptions", "witnesses")

    def __init__(self, *, binding, policy, record_audits, feasibility,
                 domain_manifest, audit_plan, checks, trusted_assumptions, queries,
                 goals, closure, witnesses):
        if type(binding) is not AnalysisBinding:
            raise TypeError("conditional evidence requires an AnalysisBinding")
        if type(policy) is not AnalysisPolicy:
            raise TypeError("conditional evidence requires an AnalysisPolicy")
        if type(feasibility) is not FeasibilityEvidence \
                or not feasibility.established:
            raise TypeError("a classification requires established FeasibilityEvidence")
        queries = tuple(queries)
        witnesses = tuple(witnesses)
        if any(type(query) is not QueryEvidence for query in queries):
            raise TypeError("conditional evidence accepts only exact QueryEvidence records")
        if any(type(witness) is not WitnessEvidence for witness in witnesses):
            raise TypeError(
                "conditional evidence accepts only exact WitnessEvidence records")
        if any(type(check) is not PassedCheck for check in checks):
            raise TypeError(
                "conditional evidence accepts only exact PassedCheck records")
        if any(type(item) is not TrustedAssumption
               for item in trusted_assumptions):
            raise TypeError(
                "conditional evidence accepts only exact TrustedAssumption records")
        checks = tuple(checks)
        if len({check.check_id for check in checks}) != len(checks):
            raise ValueError("conditional evidence contains a duplicate passed check")
        trusted_assumptions = tuple(trusted_assumptions)
        if len({item.code for item in trusted_assumptions}) \
                != len(trusted_assumptions):
            raise ValueError("trusted assumptions must have distinct stable codes")
        for witness in witnesses:
            if type(witness.witness.get("domain")) is not str \
                    or witness.witness["domain"] != binding.domain:
                raise ValueError(
                    "an audited witness belongs to a different trace domain")
        for query in queries:
            if query.witness is not None and (
                    type(query.witness) is not dict
                    or type(query.witness.get("domain")) is not str
                    or query.witness["domain"] != binding.domain):
                raise ValueError(
                    "an audited query witness belongs to a different trace domain")
        by_witness = {}
        for witness in witnesses:
            by_witness.setdefault(witness.witness_ref, []).append(witness)
        if type(goals) not in (dict, MappingProxyType, FrozenMap):
            raise TypeError(
                "conditional evidence goals require an exact immutable or built-in "
                "mapping")
        goals = dict(goals.items())
        if any(type(item) is not GoalEvidenceRecord for item in goals.values()):
            raise TypeError("conditional evidence needs one typed record per goal")
        if type(domain_manifest) is not DomainManifest:
            raise TypeError(
                "conditional evidence requires the exact DomainManifest record")
        domain = domain_manifest.domain
        manifest = domain_manifest.catalogue
        catalogue = manifest.goals_image()
        catalogue_ids = set(manifest)
        if catalogue_ids != set(goals):
            raise ValueError("the evidence partition must cover exactly the catalogue")
        domain_image = domain.as_dict()
        _check_binding_domain(binding, domain, "binding's")
        if binding.catalogue != manifest.digest():
            raise ValueError("the binding's catalogue identity does not name this catalogue")
        if binding.domain_manifest != domain_manifest.digest():
            raise ValueError(
                "the binding's domain-manifest identity does not name this manifest")
        telemetry_instance = analysis_semantic_instance(
            artifact=binding.artifact,
            task_contract=binding.task_contract,
            domain=binding.domain,
            catalogue=binding.catalogue,
            representation=binding.representation,
            encoding=binding.encoding)
        if feasibility.telemetry is not None and (
                feasibility.telemetry.semantic_instance != telemetry_instance
                or feasibility.telemetry.horizon != domain.bound):
            raise ValueError(
                "feasibility telemetry names another semantic instance or horizon")
        for query in queries:
            if query.telemetry is not None and (
                    query.telemetry.semantic_instance != telemetry_instance
                    or query.telemetry.horizon != domain.bound):
                raise ValueError(
                    "query telemetry names another semantic instance or horizon")
        if type(audit_plan) is not AuditPlan:
            raise TypeError("conditional evidence requires the exact AuditPlan record")
        if binding.audit_plan != audit_plan.digest():
            raise ValueError(
                "the binding's audit-plan identity does not name this plan")
        required_audits = audit_plan.names
        trace_audits = tuple(
            name for name in required_audits
            if name in {"step_audit", "source_audit"})
        check_subjects = {
            (check.name, subject)
            for check in checks for subject in check.subjects
        }
        mandatory = {
            "domain_manifest_binding": frozenset((
                "domain-manifest:" + binding.domain_manifest,
                "provider:" + binding.provider,
            )),
            "task_binding": frozenset((
                "domain:" + binding.domain,
                "task-contract:" + binding.task_contract,
                "artifact:" + binding.artifact,
                "provider:" + binding.provider,
            )),
            "catalogue_binding": frozenset((
                "catalogue:" + binding.catalogue,
                "provider:" + binding.provider,
            )),
            "audit_plan_binding": frozenset((
                "audit-plan:" + binding.audit_plan,
                "provider:" + binding.provider,
            )),
            "representation_well_formed": frozenset((
                "representation:" + binding.representation,
                "encoding:" + binding.encoding,
            )),
        }
        missing = sorted(
            name for name, subjects in mandatory.items()
            if not any(check.name == name and frozenset(check.subjects) == subjects
                       for check in checks))
        if missing:
            raise ValueError(
                f"conditional evidence is missing mandatory passed checks: {missing}")
        if audit_plan.requires(AuditKind.GOAL_SET) and (
                "goal_set_audit", "catalogue:" + binding.catalogue) \
                not in check_subjects:
            raise ValueError(
                "the required goal-set audit has no passed catalogue check")
        if tuple(query.order for query in queries) != tuple(range(len(queries))):
            raise ValueError("exact query evidence must retain one linearized call order")
        if len({query.query_id for query in queries}) != len(queries):
            raise ValueError("exact query evidence contains a duplicate query identity")
        witness_hits = {}
        for witness_ref, ledger_entries in by_witness.items():
            hits = _witness_hits(ledger_entries[0].witness)
            if not hits <= catalogue_ids:
                raise ValueError(
                    "an audited witness reports a hit outside the catalogue")
            witness_hits[witness_ref] = hits
        for query in queries:
            if not set(query.goals) <= catalogue_ids:
                raise ValueError("an exact query asks about a goal outside the catalogue")
            if query.answer is SolverAnswer.SAT:
                if query.witness is None:
                    raise ValueError(
                        "a classified SAT query must retain its complete audited witness")
                if query.passed_audits != trace_audits:
                    raise ValueError(
                        "a retained SAT witness must pass exactly the required "
                        "per-trace audits")
                if query.witness_ref not in by_witness:
                    raise ValueError(
                        "a SAT query points outside the audited witness ledger")
                if not any(
                        ledger.origin is WitnessOrigin.BOUNDED_QUERY
                        and ledger.query_ref == query.query_id
                        and ledger.witness == query.witness
                        for ledger in by_witness[query.witness_ref]):
                    raise ValueError(
                        "a SAT query and its audited witness ledger entry disagree")
                for name in trace_audits:
                    if (name, query.witness_ref) not in check_subjects:
                        raise ValueError(
                            "a SAT witness names a required audit with no passed "
                            "check on that witness")
            elif query.passed_audits:
                raise ValueError("a query without a witness cannot pass trace audits")
        for goal, evidence in goals.items():
            if evidence.status is ClassifiedGoalStatus.REACHED:
                matching = by_witness.get(evidence.support_ref, ())
                if not matching:
                    raise ValueError("a reached goal points outside the witness ledger")
                if goal not in witness_hits[evidence.support_ref]:
                    raise ValueError("a reached goal's audited witness does not hit it")
            if evidence.status is ClassifiedGoalStatus.BOUNDED_UNREACHABLE \
                    and not any(
                    query.query_id == evidence.support_ref
                    and query.answer is SolverAnswer.UNSAT and goal in query.goals
                    for query in queries):
                raise ValueError("an unreachable goal has no exact UNSAT response")
            if evidence.status is ClassifiedGoalStatus.UNRESOLVED and not any(
                    query.query_id == evidence.support_ref
                    and query.answer is SolverAnswer.UNKNOWN
                    and query.goals == (goal,)
                    for query in queries):
                raise ValueError(
                    "an unresolved goal has no unresolved singleton query response")
        classified_reached = {
            goal for goal, evidence in goals.items()
            if evidence.status is ClassifiedGoalStatus.REACHED
        }
        retained_hits = set().union(*witness_hits.values()) if witness_hits else set()
        if retained_hits != classified_reached:
            raise ValueError(
                "the classified reached goals differ from the retained audited "
                "witness hits")
        unsat_goals = {
            goal for query in queries if query.answer is SolverAnswer.UNSAT
            for goal in query.goals
        }
        omitted_unsat = sorted(
            goal for goal in unsat_goals
            if goals[goal].status is not ClassifiedGoalStatus.BOUNDED_UNREACHABLE)
        if omitted_unsat:
            raise ValueError(
                "an actual UNSAT query must classify every goal it contains as "
                f"bounded-unreachable; omitted {omitted_unsat[:8]}")
        for witness in witnesses:
            if witness.passed_audits != trace_audits:
                raise ValueError(
                    "every retained witness must pass exactly the required per-trace "
                    "audits")
            for name in trace_audits:
                if (name, witness.witness_ref) not in check_subjects:
                    raise ValueError(
                        "a retained witness has no passed required-audit check")
        closure = _image(closure, "closure summary")
        expected = {
            "reached": sum(
                g.status is ClassifiedGoalStatus.REACHED for g in goals.values()),
            "unreachable": sum(
                g.status is ClassifiedGoalStatus.BOUNDED_UNREACHABLE
                for g in goals.values()),
            "unresolved": sum(
                g.status is ClassifiedGoalStatus.UNRESOLVED
                for g in goals.values()),
        }
        for status, count in expected.items():
            if closure.get(f"n_{status}") != count:
                raise ValueError(f"the closure summary disagrees on {status}")
        if "n_inconsistent" in closure or closure.get("accounted_for") is not True:
            raise ValueError("a classified closure must be complete and consistent")
        if closure.get("query_transcript_complete") is not True:
            raise ValueError(
                "a classification must retain every bounded logical query")
        record_audits = tuple(_image(row, "record audit") for row in record_audits)
        # Records retain input order, not necessarily execution/audit order.
        # Support must end at a checked native witness, never another selection
        # receipt. Validate all such anchors before checking selection links.
        admitted_records = set()
        for index, row in enumerate(record_audits):
            if row.get("record") != index:
                raise ValueError("record audits must be complete and in record order")
            if row.get("disposition") == "admitted":
                matching = by_witness.get(row.get("witness_ref"), ())
                if not any(
                        witness.origin is WitnessOrigin.EVALUATED_RECORD
                        and witness.raw_record_ref == f"record:{index}"
                        and row.get("witness") == witness.witness
                        for witness in matching):
                    raise ValueError(
                        "an admitted record audit and its witness ledger entry disagree")
                admitted_records.add(row["witness_ref"])
        for index, row in enumerate(record_audits):
            if row.get("disposition") == "not-selected":
                if set(row) != {"record", "disposition", "reason", "observed_hits", "covered_by"} \
                        or row["reason"] != "redundant-goal-support":
                    raise ValueError("a nonselected record needs a goal-support selection receipt")
                refs, hits = row["covered_by"], row["observed_hits"]
                if type(refs) is not list or not refs or any(type(ref) is not str for ref in refs) \
                        or refs != sorted(set(refs)) or not set(refs) <= admitted_records \
                        or type(hits) is not list or any(type(hit) is not str for hit in hits) \
                        or hits != sorted(set(hits)) \
                        or not set(hits) <= set().union(*(witness_hits[ref] for ref in refs)):
                    raise ValueError("a nonselected record needs audited goal support")
                if ("record_audit", f"record:{index}") in check_subjects:
                    raise ValueError("a nonselected record cannot claim a passed record audit")
                continue
            if row.get("disposition") not in {"admitted", "outside"}:
                raise ValueError("a classification cannot retain a stopping record audit")
        if self_feasibility_ref := feasibility.witness_ref:
            matching = by_witness.get(self_feasibility_ref, ())
            if not matching:
                raise ValueError(
                    "the feasibility witness points outside the audited witness ledger")
            wanted_origin = (WitnessOrigin.EVALUATED_RECORD
                             if feasibility.source is FeasibilitySource.AUDITED_POOL
                             else WitnessOrigin.FEASIBILITY_QUERY)
            if not any(witness.origin is wanted_origin
                       and witness.witness == feasibility.witness
                       for witness in matching):
                raise ValueError("the feasibility evidence and witness ledger disagree")
        self.binding = binding
        self.policy = policy
        self.checks = checks
        self.record_audits = record_audits
        self.feasibility = feasibility
        self._domain = json.dumps(
            domain_image, sort_keys=True, separators=(",", ":"))
        self._catalogue = json.dumps(
            catalogue, sort_keys=True, separators=(",", ":"))
        self._catalogue_manifest = manifest.canonical
        self._domain_manifest = domain_manifest.canonical
        self.audit_plan = audit_plan
        self.trusted_assumptions = trusted_assumptions
        self.queries = tuple(queries)
        self.witnesses = witnesses
        self._goals = tuple(sorted(goals.items()))
        self._closure = json.dumps(closure, sort_keys=True, separators=(",", ":"))
        self._freeze()

    @property
    def domain(self):
        return json.loads(self._domain)

    @property
    def catalogue(self):
        return json.loads(self._catalogue)

    @property
    def catalogue_manifest(self):
        return json.loads(self._catalogue_manifest)

    @property
    def domain_manifest(self):
        return json.loads(self._domain_manifest)

    @property
    def goals(self):
        return {goal: evidence.as_dict() for goal, evidence in self._goals}

    @property
    def closure(self):
        return json.loads(self._closure)

    def as_dict(self):
        return {
            "binding": self.binding.as_dict(),
            "analysis_policy": self.policy.as_dict(),
            "record_audits": list(self.record_audits),
            "feasibility": self.feasibility.as_dict(),
            "domain": self.domain,
            "domain_digest": self.binding.domain,
            "domain_manifest": self.domain_manifest,
            "domain_manifest_digest": self.binding.domain_manifest,
            "catalogue": self.catalogue,
            "catalogue_manifest": self.catalogue_manifest,
            "catalogue_digest": self.binding.catalogue,
            "audit_plan": self.audit_plan.as_dict(),
            "passed_checks": [check.as_dict() for check in self.checks],
            "trusted_assumptions": [
                assumption.as_dict() for assumption in self.trusted_assumptions],
            "queries": [query.as_dict() for query in self.queries],
            "witnesses": [witness.as_dict() for witness in self.witnesses],
            "goals": self.goals,
            "closure": self.closure,
        }

    @classmethod
    def from_dict(cls, value):
        """Decode the strict JSON boundary and rerun every evidence invariant."""

        if type(value) is not dict:
            raise TypeError("a conditional-evidence image must be a mapping")
        got = cls(
            binding=AnalysisBinding.from_dict(value.get("binding")),
            policy=AnalysisPolicy.from_dict(value.get("analysis_policy")),
            record_audits=value.get("record_audits", ()),
            feasibility=FeasibilityEvidence.from_dict(value.get("feasibility")),
            domain_manifest=DomainManifest.from_dict(
                value.get("domain_manifest")),
            audit_plan=AuditPlan.from_dict(value.get("audit_plan")),
            checks=tuple(PassedCheck.from_dict(row)
                         for row in value.get("passed_checks", ())),
            trusted_assumptions=tuple(
                TrustedAssumption.from_dict(row)
                for row in value.get("trusted_assumptions", ())),
            queries=tuple(QueryEvidence.from_dict(row)
                          for row in value.get("queries", ())),
            goals={name: GoalEvidenceRecord.from_dict(row)
                   for name, row in value.get("goals", {}).items()},
            closure=value.get("closure"),
            witnesses=tuple(WitnessEvidence.from_dict(row)
                            for row in value.get("witnesses", ())))
        if got.as_dict() != value:
            raise ValueError("a conditional-evidence image is not canonical")
        return got


class AnalysisStopKind(enum.Enum):
    """The four run-level ways bounded analysis can stop without a partition."""

    REFUSED = "refused"
    INCONSISTENT = "inconsistent"
    INFEASIBLE_DOMAIN = "infeasible-domain"
    FEASIBILITY_UNRESOLVED = "feasibility-unresolved"

    def __bool__(self):
        raise TypeError("an analysis-stop kind is not a truth value")


class AnalysisStopEvidence(Frozen):
    """Typed evidence for a run-level stop, which never carries goal statuses."""

    __slots__ = (
        "_catalogue", "_catalogue_digest", "_catalogue_manifest", "_closure",
        "_domain", "_domain_digest", "_domain_manifest",
        "_domain_manifest_digest", "audit_plan", "binding", "checks",
        "feasibility", "issue", "kind", "policy", "queries",
        "record_audits", "trusted_assumptions")

    def __init__(self, kind, *, policy=None, issue=None, binding=None,
                 record_audits=(), feasibility=None, domain_manifest=None,
                 closure=None, audit_plan=None, queries=(), checks=(),
                 trusted_assumptions=()):
        if type(kind) is not AnalysisStopKind:
            raise TypeError("an analysis stop needs an exact AnalysisStopKind")
        if policy is not None and type(policy) is not AnalysisPolicy:
            raise TypeError("an analysis stop policy must be an AnalysisPolicy")
        if issue is not None and type(issue) is not IssueEvidence:
            raise TypeError("an analysis stop issue must be an IssueEvidence")
        if kind in {AnalysisStopKind.REFUSED, AnalysisStopKind.INCONSISTENT} \
                and issue is None:
            raise ValueError(f"{kind.value} must carry its typed issue")
        if kind in {
                AnalysisStopKind.INFEASIBLE_DOMAIN,
                AnalysisStopKind.FEASIBILITY_UNRESOLVED}:
            wanted = (FeasibilityAnswer.INFEASIBLE
                      if kind is AnalysisStopKind.INFEASIBLE_DOMAIN
                      else FeasibilityAnswer.UNRESOLVED)
            if type(feasibility) is not FeasibilityEvidence \
                    or feasibility.answer is not wanted:
                raise ValueError(
                    f"{kind.value} must carry the matching feasibility answer")
        if binding is not None and type(binding) is not AnalysisBinding:
            raise TypeError("an analysis stop binding must be an AnalysisBinding")
        if audit_plan is not None and type(audit_plan) is not AuditPlan:
            raise TypeError("an analysis stop audit plan must be an AuditPlan")
        if binding is not None and audit_plan is not None \
                and binding.audit_plan != audit_plan.digest():
            raise ValueError("an analysis stop binds a different audit plan")
        if domain_manifest is not None and type(domain_manifest) is not DomainManifest:
            raise TypeError(
                "an analysis stop manifest must be the exact DomainManifest")
        if binding is not None and domain_manifest is None:
            raise ValueError(
                "an analysis stop with a binding must retain its DomainManifest")
        if domain_manifest is not None and binding is not None:
            _check_binding_domain(
                binding, domain_manifest.domain, "analysis stop binding's")
            if binding.catalogue != domain_manifest.catalogue.digest() \
                    or binding.domain_manifest != domain_manifest.digest():
                raise ValueError(
                    "the analysis stop binding names a different domain manifest")
        if any(type(query) is not QueryEvidence for query in queries):
            raise TypeError("an analysis stop accepts only exact QueryEvidence records")
        if any(type(check) is not PassedCheck for check in checks):
            raise TypeError("an analysis stop accepts only exact PassedCheck records")
        if any(type(item) is not TrustedAssumption
               for item in trusted_assumptions):
            raise TypeError(
                "an analysis stop accepts only exact TrustedAssumption records")
        self.kind = kind
        self.policy = policy
        self.issue = issue
        self.binding = binding
        self.audit_plan = audit_plan
        self.record_audits = tuple(
            _image(row, "record audit") for row in record_audits)
        self.feasibility = feasibility
        self.queries = tuple(queries)
        self.checks = tuple(checks)
        self.trusted_assumptions = tuple(trusted_assumptions)
        self._domain = (None if domain_manifest is None else json.dumps(
            domain_manifest.domain.as_dict(), sort_keys=True, separators=(",", ":")))
        self._closure = (None if closure is None else json.dumps(
            _image(closure, "closure summary"), sort_keys=True,
            separators=(",", ":")))
        self._catalogue = (None if domain_manifest is None else json.dumps(
            domain_manifest.catalogue.goals_image(), sort_keys=True,
            separators=(",", ":")))
        self._catalogue_manifest = (
            None if domain_manifest is None else domain_manifest.catalogue.canonical)
        self._domain_manifest = (
            None if domain_manifest is None else domain_manifest.canonical)
        self._domain_digest = (
            None if domain_manifest is None else domain_manifest.domain.digest())
        self._catalogue_digest = (
            None if domain_manifest is None else domain_manifest.catalogue.digest())
        self._domain_manifest_digest = (
            None if domain_manifest is None else domain_manifest.digest())
        self._freeze()

    def as_dict(self):
        out = {}
        if self.issue is not None:
            out["refusal" if self.kind is AnalysisStopKind.REFUSED
                else "inconsistency"] = \
                self.issue.as_dict()
        if self.policy is not None:
            out["analysis_policy"] = self.policy.as_dict()
        if self.binding is not None:
            out["binding"] = self.binding.as_dict()
        if self.audit_plan is not None:
            out["audit_plan"] = self.audit_plan.as_dict()
        if self.record_audits:
            out["record_audits"] = list(self.record_audits)
        if self.feasibility is not None:
            out["feasibility"] = self.feasibility.as_dict()
        if self._domain is not None:
            out["domain"] = json.loads(self._domain)
            out["domain_digest"] = self._domain_digest
            out["domain_manifest"] = json.loads(self._domain_manifest)
            out["domain_manifest_digest"] = self._domain_manifest_digest
        if self._closure is not None:
            out["closure"] = json.loads(self._closure)
        if self._catalogue_manifest is not None:
            out["catalogue"] = json.loads(self._catalogue)
            out["catalogue_manifest"] = json.loads(self._catalogue_manifest)
            out["catalogue_digest"] = self._catalogue_digest
        if self.queries:
            out["queries"] = [query.as_dict() for query in self.queries]
        if self.checks:
            out["passed_checks"] = [check.as_dict() for check in self.checks]
        if self.trusted_assumptions:
            out["trusted_assumptions"] = [
                item.as_dict() for item in self.trusted_assumptions]
        return out

    @classmethod
    def from_dict(cls, value, *, kind):
        """Decode one stopped analysis under its enclosing verdict."""

        if type(value) is not dict:
            raise TypeError("an analysis-stop image must be a mapping")
        if type(kind) is not AnalysisStopKind:
            raise TypeError("an analysis-stop decoder needs its exact kind")
        issue_key = (
            "refusal" if kind is AnalysisStopKind.REFUSED else
            "inconsistency" if kind is AnalysisStopKind.INCONSISTENT else None)
        issue = (None if issue_key is None else
                 IssueEvidence.from_dict(value.get(issue_key)))
        policy = value.get("analysis_policy")
        binding = value.get("binding")
        audit_plan = value.get("audit_plan")
        feasibility = value.get("feasibility")
        domain_manifest = value.get("domain_manifest")
        got = cls(
            kind,
            policy=(None if policy is None else AnalysisPolicy.from_dict(policy)),
            issue=issue,
            binding=(None if binding is None
                     else AnalysisBinding.from_dict(binding)),
            record_audits=value.get("record_audits", ()),
            feasibility=(None if feasibility is None
                         else FeasibilityEvidence.from_dict(feasibility)),
            domain_manifest=(None if domain_manifest is None
                             else DomainManifest.from_dict(domain_manifest)),
            closure=value.get("closure"),
            audit_plan=(None if audit_plan is None
                        else AuditPlan.from_dict(audit_plan)),
            queries=tuple(QueryEvidence.from_dict(row)
                          for row in value.get("queries", ())),
            checks=tuple(PassedCheck.from_dict(row)
                         for row in value.get("passed_checks", ())),
            trusted_assumptions=tuple(
                TrustedAssumption.from_dict(row)
                for row in value.get("trusted_assumptions", ())))
        if got.as_dict() != value:
            raise ValueError("an analysis-stop image is not canonical")
        return got
