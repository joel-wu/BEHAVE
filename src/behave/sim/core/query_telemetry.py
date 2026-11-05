"""Closed, non-semantic measurements for one logical exact query.

The exact query transcript is evidence; performance measurements are not.  This
module keeps that distinction structural.  :class:`QueryTelemetry` names the
semantic instance a call was made against, but timing, cache and resource fields do
not participate in the query or evidence identities.

Every measurement is optional.  Absence means *not measured*, never zero.  A
producer may therefore add instrumentation without fabricating values for stages it
cannot observe at its boundary.
"""

from __future__ import annotations

import enum
import hashlib
import json
import math
import sys
import time

from .frozen import Frozen, FrozenMap
from .record_value import FrozenValueMap, thaw_record_value

__all__ = [
    "AnalysisCacheBinding",
    "CacheDisposition",
    "FormulaShape",
    "QueryKind",
    "QueryTelemetry",
    "QueryTelemetryFragment",
    "QueryTelemetryScope",
    "StageTimings",
    "analysis_semantic_instance",
    "annotate_query",
    "annotate_refusal",
    "annotate_verdict",
    "process_peak_rss_bytes",
    "query_kind_for_goals",
    "semantic_instance_of",
]


TELEMETRY_VERSION = "exact-query-telemetry/1"
TELEMETRY_FRAGMENT_VERSION = "exact-query-telemetry-fragment/1"
CACHE_BINDING_VERSION = "bounded-analysis-cache-binding/1"


def analysis_semantic_instance(*, artifact, task_contract, domain, catalogue,
                               representation, encoding):
    """Digest the semantic inputs shared by all calls in one bounded analysis.

    Backend, tool version, timeout and worker policy deliberately do not appear here.
    The first two belong to :class:`AnalysisCacheBinding`; the latter two are execution
    telemetry.  Keeping this digest separate prevents a timeout change from naming a
    different transition system while still preventing evidence from another finite
    representation or catalogue from sharing an instance label.
    """

    components = {
        "artifact": artifact,
        "task_contract": task_contract,
        "trace_domain": domain,
        "catalogue": catalogue,
        "representation": representation,
        "encoding": encoding,
    }
    if any(type(value) is not str or not value for value in components.values()):
        raise ValueError(
            "an analysis semantic instance needs every nonempty identity component")
    image = json.dumps(
        components, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "analysis-query/1:" + hashlib.sha256(image.encode()).hexdigest()


def semantic_instance_of(exact):
    """Read the same bound semantic identity for worker caches and telemetry."""
    identities = exact.evidence_identity()
    return analysis_semantic_instance(
        artifact=exact.domain.artifact,
        task_contract=exact.domain.task_contract,
        domain=exact.domain.digest(),
        catalogue=exact.catalogue_manifest().digest(),
        representation=identities["representation"],
        encoding=identities["encoding"])


def _identity(prefix, value):
    image = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False)
    return prefix + hashlib.sha256(image.encode()).hexdigest()


class AnalysisCacheBinding(Frozen):
    """Worker-authenticated semantic identity for reusable exact answers.

    ``semantic_instance`` already binds artifact, task contract, trace domain,
    catalogue, representation and encoding.  This record adds the implementation
    identities that can change a solver answer without changing that transition
    system.  Timeouts, worker counts and other execution policy are deliberately not
    accepted by the constructor and therefore cannot enter a cache key.

    The tool digest currently commits to the registered tool name and exact version
    string.  It is not a hash of a filesystem path: the isolated job package is the
    authority that constructs this record, and a path outside that package would be a
    second, mutable tool identity.
    """

    __slots__ = (
        "backend", "backend_digest", "backend_version", "identity",
        "semantic_instance", "tool", "tool_digest", "tool_version",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("AnalysisCacheBinding is a closed semantic identity")

    def __init__(self, *, semantic_instance, backend, backend_version, tool,
                 tool_version):
        values = {
            "semantic_instance": semantic_instance,
            "backend": backend,
            "backend_version": backend_version,
            "tool": tool,
            "tool_version": tool_version,
        }
        if any(type(value) is not str or not value for value in values.values()):
            raise ValueError(
                "an analysis cache binding needs every nonempty identity component")
        self.semantic_instance = semantic_instance
        self.backend = backend
        self.backend_version = backend_version
        self.tool = tool
        self.tool_version = tool_version
        self.backend_digest = _identity(
            "backend-version/1:",
            {"backend": backend, "version": backend_version})
        self.tool_digest = _identity(
            "tool-version/1:", {"tool": tool, "version": tool_version})
        self.identity = _identity(
            "bounded-analysis-cache/1:", {
                "semantic_instance": semantic_instance,
                "backend_digest": self.backend_digest,
                "tool_digest": self.tool_digest,
            })
        self._freeze()

    def query_identity(self, kind, goals=()):
        """Identity of one logical exact request, independent of its budget."""

        if type(kind) is not QueryKind:
            raise TypeError("a semantic cache request needs the exact QueryKind")
        goals = tuple(goals)
        if any(type(goal) is not str or not goal for goal in goals) \
                or len(goals) != len(set(goals)):
            raise ValueError("a semantic cache request needs distinct goal names")
        goals = tuple(sorted(goals))
        if kind is QueryKind.FEASIBILITY and goals:
            raise ValueError("a feasibility cache request carries no goals")
        if kind is not QueryKind.FEASIBILITY and not goals:
            raise ValueError("a goal cache request carries at least one goal")
        return _identity(
            "bounded-analysis-answer/1:", {
                "binding": self.identity,
                "query_kind": kind.value,
                "goals": goals,
            })

    def as_dict(self):
        return {
            "version": CACHE_BINDING_VERSION,
            "semantic_instance": self.semantic_instance,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "backend_digest": self.backend_digest,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "tool_digest": self.tool_digest,
            "identity": self.identity,
        }

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {
                "version", "semantic_instance", "backend", "backend_version",
                "backend_digest", "tool", "tool_version", "tool_digest",
                "identity"} \
                or value.get("version") != CACHE_BINDING_VERSION:
            raise TypeError("an analysis cache binding has its complete versioned image")
        rebuilt = cls(
            semantic_instance=value["semantic_instance"],
            backend=value["backend"], backend_version=value["backend_version"],
            tool=value["tool"], tool_version=value["tool_version"])
        if rebuilt.as_dict() != value:
            raise ValueError("an analysis cache binding has inconsistent digests")
        return rebuilt


class QueryKind(enum.Enum):
    """The four solver-call roles used by generation and bounded analysis."""

    LOCAL_PROBE = "local-probe"
    FEASIBILITY = "feasibility"
    SET = "set"
    SINGLETON = "singleton"

    def __bool__(self):
        raise TypeError("a query kind is not a truth value")


class CacheDisposition(enum.Enum):
    """Whether an actually configured semantic cache served this call."""

    HIT = "hit"
    MISS = "miss"

    def __bool__(self):
        raise TypeError("a cache disposition is not a truth value")


def query_kind_for_goals(goals):
    """The exact-analysis call kind determined by its asked goal set."""

    goals = tuple(goals)
    if len(goals) == 1:
        return QueryKind.SINGLETON
    if len(goals) > 1:
        return QueryKind.SET
    raise ValueError("a goal query must ask at least one goal")


_SHAPE_FIELDS = (
    "full_assertions",
    "full_nodes",
    "full_text_bytes",
    "sliced_assertions",
    "sliced_nodes",
    "sliced_text_bytes",
    "scalar_bits",
    "scalar_cells",
    "smt_arrays",
)


class FormulaShape(Frozen):
    """Measured sizes of the formula and its finite representation.

    The units are part of the field names.  In particular, an assertion count is
    not silently compared with an AST-node or byte count.  A field is omitted when
    its producer cannot measure it.
    """

    __slots__ = _SHAPE_FIELDS

    def __init__(self, **values):
        unknown = sorted(set(values) - set(_SHAPE_FIELDS))
        if unknown:
            raise TypeError(f"unknown formula-shape fields: {unknown}")
        for name in _SHAPE_FIELDS:
            value = values.get(name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(
                    f"formula-shape {name} must be a natural number or absent")
            setattr(self, name, value)
        if all(getattr(self, name) is None for name in _SHAPE_FIELDS):
            raise ValueError("an empty FormulaShape is an unmeasured value")
        self._freeze()

    def as_dict(self):
        return {
            name: getattr(self, name)
            for name in _SHAPE_FIELDS if getattr(self, name) is not None
        }

    def merge(self, other):
        """Combine disjoint measurements without choosing between producers."""

        if type(other) is not FormulaShape:
            raise TypeError("formula-shape fragments must be exact FormulaShape records")
        left, right = self.as_dict(), other.as_dict()
        overlap = sorted(set(left) & set(right))
        if overlap:
            raise ValueError(
                f"formula-shape fields have more than one owner: {overlap}")
        return FormulaShape(**(left | right))

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a formula-shape image must be an exact mapping")
        return cls(**value)


_STAGES = frozenset({
    "frontend_ns",
    "lowering_ns",
    "plan_ns",
    "slice_ns",
    "encode_unroll_ns",
    "solver_load_ns",
    "solve_ns",
    "decode_ns",
    "replay_audit_ns",
    "worker_start_ns",
    "ipc_ns",
    "total_ns",
})


class StageTimings(Frozen):
    """Measured nanoseconds for named query stages.

    The closed stage vocabulary prevents two producers from reporting unlike
    measurements under the same informal label.  Combined or unobservable stages
    stay absent; they are not divided or copied between calls.
    """

    __slots__ = ("_values",)

    def __init__(self, values):
        if type(values) not in (dict, FrozenMap):
            raise TypeError("stage timings need an exact built-in or FrozenMap")
        unknown = sorted(set(values) - _STAGES)
        if unknown:
            raise ValueError(f"unregistered exact-query timing stages: {unknown}")
        if not values:
            raise ValueError("empty stage timings are unmeasured, not a record")
        for name, value in values.items():
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"exact-query timing {name} must be natural nanoseconds")
        self._values = FrozenMap(dict(values))
        self._freeze()

    def as_dict(self):
        return dict(self._values)

    def merge(self, other):
        """Combine disjoint stage measurements without adding or overwriting."""

        if type(other) is not StageTimings:
            raise TypeError("timing fragments must be exact StageTimings records")
        left, right = self.as_dict(), other.as_dict()
        overlap = sorted(set(left) & set(right))
        if overlap:
            raise ValueError(
                f"exact-query timing stages have more than one owner: {overlap}")
        return StageTimings(left | right)

    @classmethod
    def from_dict(cls, value):
        return cls(value)


def _query_coordinates(*, backend, kind, semantic_instance, horizon, goals_asked):
    """Validate and canonicalise the identity shared by telemetry fragments."""

    if type(backend) is not str or not backend:
        raise ValueError("query telemetry needs a nonempty backend")
    if type(kind) is not QueryKind:
        raise TypeError("query telemetry needs the exact QueryKind enum")
    if type(semantic_instance) is not str or not semantic_instance:
        raise ValueError("query telemetry needs a semantic instance identity")
    if type(horizon) is not int or horizon < 1:
        raise ValueError("query telemetry needs a positive horizon")
    asked = tuple(goals_asked)
    if any(type(goal) is not str or not goal for goal in asked):
        raise TypeError("query telemetry goals must be nonempty exact strings")
    if len(asked) != len(set(asked)):
        raise ValueError("query telemetry goals must be distinct")
    asked = tuple(sorted(asked))
    if kind is QueryKind.FEASIBILITY and asked:
        raise ValueError("a feasibility query asks domain inhabitation, not goals")
    if kind is QueryKind.SINGLETON and len(asked) != 1:
        raise ValueError("a singleton query asks exactly one goal")
    if kind is QueryKind.SET and len(asked) < 2:
        raise ValueError("a set query asks at least two goals")
    if kind is QueryKind.LOCAL_PROBE and not asked:
        raise ValueError("a local goal probe asks at least one goal")
    return asked


class QueryTelemetryFragment(Frozen):
    """One producer's immutable measurements for one logical exact query.

    Frontend, isolated worker and outer coordinator observe different stages of the
    same call.  Each emits a fragment containing only fields it measured.  Fragments
    merge only when their complete query coordinates agree and their measured fields
    are disjoint; an equal duplicate is still rejected because it has two owners.
    """

    __slots__ = (
        "backend", "goals_asked", "horizon", "kind", "semantic_instance",
        "shape", "timings",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("QueryTelemetryFragment is a closed measurement record")

    def __init__(self, *, backend, kind, semantic_instance, horizon,
                 goals_asked=(), shape=None, timings=None):
        asked = _query_coordinates(
            backend=backend, kind=kind, semantic_instance=semantic_instance,
            horizon=horizon, goals_asked=goals_asked)
        if shape is not None and type(shape) is not FormulaShape:
            raise TypeError("a telemetry fragment shape is FormulaShape or absent")
        if timings is not None and type(timings) is not StageTimings:
            raise TypeError("a telemetry fragment timing is StageTimings or absent")
        if shape is None and timings is None:
            raise ValueError("an empty telemetry fragment measured no query field")
        self.backend = backend
        self.kind = kind
        self.semantic_instance = semantic_instance
        self.horizon = horizon
        self.goals_asked = asked
        self.shape = shape
        self.timings = timings
        self._freeze()

    def _coordinates(self):
        return (
            self.backend, self.kind, self.semantic_instance, self.horizon,
            self.goals_asked,
        )

    def merge(self, *others):
        """Return the union of fragments with one owner per measured field."""

        shape, timings = self.shape, self.timings
        for other in others:
            if type(other) is not QueryTelemetryFragment:
                raise TypeError(
                    "query telemetry merges exact QueryTelemetryFragment records")
            if self._coordinates() != other._coordinates():
                raise ValueError(
                    "query telemetry fragments name different logical queries")
            if other.shape is not None:
                shape = other.shape if shape is None else shape.merge(other.shape)
            if other.timings is not None:
                timings = (
                    other.timings if timings is None
                    else timings.merge(other.timings))
        return QueryTelemetryFragment(
            backend=self.backend, kind=self.kind,
            semantic_instance=self.semantic_instance, horizon=self.horizon,
            goals_asked=self.goals_asked, shape=shape, timings=timings)

    def as_dict(self):
        out = {
            "version": TELEMETRY_FRAGMENT_VERSION,
            "backend": self.backend,
            "query_kind": self.kind.value,
            "semantic_instance": self.semantic_instance,
            "horizon": self.horizon,
            "goals_asked": list(self.goals_asked),
        }
        if self.shape is not None:
            out["formula"] = self.shape.as_dict()
        if self.timings is not None:
            out["timings_ns"] = self.timings.as_dict()
        return out

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or value.get("version") \
                != TELEMETRY_FRAGMENT_VERSION:
            raise TypeError("a telemetry fragment image has its registered version")
        required = {
            "version", "backend", "query_kind", "semantic_instance", "horizon",
            "goals_asked",
        }
        optional = {"formula", "timings_ns"}
        if not required <= set(value) or set(value) - required - optional:
            raise ValueError("a telemetry fragment image has its closed fields")
        if type(value["goals_asked"]) is not list:
            raise TypeError("wire query goals are an explicit JSON list")
        return cls(
            backend=value["backend"], kind=QueryKind(value["query_kind"]),
            semantic_instance=value["semantic_instance"], horizon=value["horizon"],
            goals_asked=value["goals_asked"],
            shape=(None if "formula" not in value
                   else FormulaShape.from_dict(value["formula"])),
            timings=(None if "timings_ns" not in value
                     else StageTimings.from_dict(value["timings_ns"])))

    def finalize(self, *, outcome, goals_settled=(), cache_identity=None,
                 cache=None, peak_rss_bytes=None, provenance=None):
        """Create the complete record passed to the corresponding QueryCall."""

        return QueryTelemetry(
            backend=self.backend, kind=self.kind,
            semantic_instance=self.semantic_instance, horizon=self.horizon,
            goals_asked=self.goals_asked, goals_settled=goals_settled,
            outcome=outcome, cache_identity=cache_identity, cache=cache,
            shape=self.shape, timings=self.timings,
            peak_rss_bytes=peak_rss_bytes, provenance=provenance)


class QueryTelemetryScope(Frozen):
    """Worker-owned identity from which per-call measurement fragments are made."""

    __slots__ = ("backend", "horizon", "semantic_instance")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("QueryTelemetryScope is a closed query identity")

    def __init__(self, *, backend, semantic_instance, horizon):
        # A feasibility coordinate validates the shared fields without inventing a
        # goal query.  ``fragment`` validates the actual goal set and derived kind.
        _query_coordinates(
            backend=backend, kind=QueryKind.FEASIBILITY,
            semantic_instance=semantic_instance, horizon=horizon,
            goals_asked=())
        self.backend = backend
        self.semantic_instance = semantic_instance
        self.horizon = horizon
        self._freeze()

    def fragment(self, goals, *, shape=None, timings=None):
        goals = tuple(goals)
        return QueryTelemetryFragment(
            backend=self.backend, kind=query_kind_for_goals(goals),
            semantic_instance=self.semantic_instance, horizon=self.horizon,
            goals_asked=goals, shape=shape, timings=timings)


def _json_image(value, path="query provenance", active=None):
    active = set() if active is None else active
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if kind in (tuple, list):
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} contains a reference cycle")
        active.add(identity)
        try:
            return [_json_image(item, f"{path}[]", active) for item in value]
        finally:
            active.remove(identity)
    if kind in (dict, FrozenMap):
        if any(type(key) is not str for key in value):
            raise TypeError(f"{path} contains a non-string key")
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} contains a reference cycle")
        active.add(identity)
        try:
            return {key: _json_image(item, f"{path}.{key}", active)
                    for key, item in value.items()}
        finally:
            active.remove(identity)
    raise TypeError(f"{path} contains unsupported {kind.__name__}")


class QueryTelemetry(Frozen):
    """One typed measurement record beside, never inside, solver semantics."""

    __slots__ = (
        "_provenance",
        "backend",
        "cache",
        "cache_identity",
        "goals_asked",
        "goals_settled",
        "horizon",
        "kind",
        "outcome",
        "peak_rss_bytes",
        "semantic_instance",
        "shape",
        "timings",
    )

    def __init__(self, *, backend, kind, semantic_instance, horizon,
                 goals_asked=(), goals_settled=(), outcome, cache_identity=None,
                 cache=None, shape=None, timings=None, peak_rss_bytes=None,
                 provenance=None):
        asked = _query_coordinates(
            backend=backend, kind=kind, semantic_instance=semantic_instance,
            horizon=horizon, goals_asked=goals_asked)
        settled = tuple(goals_settled)
        if any(type(goal) is not str or not goal for goal in settled):
            raise TypeError("query telemetry goals must be nonempty exact strings")
        if len(settled) != len(set(settled)):
            raise ValueError("query telemetry goals must be distinct")
        settled = tuple(sorted(settled))
        if not set(settled) <= set(asked):
            raise ValueError("a call cannot settle a goal it did not ask")
        if type(outcome) is not str or outcome not in {
                "sat", "unsat", "unknown", "refused"}:
            raise ValueError("query telemetry has a registered exact outcome")
        if outcome == "refused" and settled:
            raise ValueError("a refused query settles no goals")
        if cache_identity is not None and (
                type(cache_identity) is not str or not cache_identity):
            raise ValueError("a cache identity is nonempty or absent")
        if cache is not None and type(cache) is not CacheDisposition:
            raise TypeError("cache telemetry needs CacheDisposition or absence")
        if (cache is None) != (cache_identity is None):
            raise ValueError(
                "a cache hit/miss and the semantic cache identity appear together")
        if shape is not None and type(shape) is not FormulaShape:
            raise TypeError("query telemetry shape must be FormulaShape or absent")
        if timings is not None and type(timings) is not StageTimings:
            raise TypeError("query telemetry timings must be StageTimings or absent")
        if peak_rss_bytes is not None and (
                type(peak_rss_bytes) is not int or peak_rss_bytes < 1):
            raise ValueError("peak RSS is positive bytes or absent")

        provenance = {} if provenance is None else _json_image(provenance)
        if type(provenance) is not dict:
            raise TypeError("query provenance is a JSON mapping")
        self.backend = backend
        self.kind = kind
        self.semantic_instance = semantic_instance
        self.horizon = horizon
        self.goals_asked = asked
        self.goals_settled = settled
        self.outcome = outcome
        self.cache_identity = cache_identity
        self.cache = cache
        self.shape = shape
        self.timings = timings
        self.peak_rss_bytes = peak_rss_bytes
        self._provenance = json.dumps(
            provenance, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False)
        self._freeze()

    @property
    def provenance(self):
        return json.loads(self._provenance)

    def as_dict(self):
        out = {
            "version": TELEMETRY_VERSION,
            "backend": self.backend,
            "query_kind": self.kind.value,
            "semantic_instance": self.semantic_instance,
            "horizon": self.horizon,
            "goals_asked": list(self.goals_asked),
            "goals_settled": list(self.goals_settled),
            "outcome": self.outcome,
        }
        if self.cache_identity is not None:
            out["cache_identity"] = self.cache_identity
            out["cache"] = self.cache.value
        if self.shape is not None:
            out["formula"] = self.shape.as_dict()
        if self.timings is not None:
            out["timings_ns"] = self.timings.as_dict()
        if self.peak_rss_bytes is not None:
            out["peak_rss_bytes"] = self.peak_rss_bytes
        if self.provenance:
            out["provenance"] = self.provenance
        return out

    @classmethod
    def from_dict(cls, value):
        # A telemetry image nested in a public Refusal is already frozen.  Accept the
        # one registered immutable record carrier and detach it before validating the
        # wire schema; arbitrary Mapping implementations remain outside this decoder.
        if type(value) is FrozenValueMap:
            value = thaw_record_value(value)
        if type(value) is not dict or value.get("version") != TELEMETRY_VERSION:
            raise TypeError("a query telemetry image has its registered version")
        allowed = {
            "version", "backend", "query_kind", "semantic_instance", "horizon",
            "goals_asked", "goals_settled", "outcome", "cache_identity", "cache",
            "formula", "timings_ns", "peak_rss_bytes", "provenance",
        }
        if set(value) - allowed:
            raise ValueError("a query telemetry image has no extension fields")
        return cls(
            backend=value["backend"],
            kind=QueryKind(value["query_kind"]),
            semantic_instance=value["semantic_instance"],
            horizon=value["horizon"],
            goals_asked=value["goals_asked"],
            goals_settled=value["goals_settled"],
            outcome=value["outcome"],
            cache_identity=value.get("cache_identity"),
            cache=(None if "cache" not in value
                   else CacheDisposition(value["cache"])),
            shape=(None if "formula" not in value
                   else FormulaShape.from_dict(value["formula"])),
            timings=(None if "timings_ns" not in value
                     else StageTimings.from_dict(value["timings_ns"])),
            peak_rss_bytes=value.get("peak_rss_bytes"),
            provenance=value.get("provenance"),
        )

    def with_settled(self, goals, *, replay_audit_ns=None):
        """Return the same measured call with audited settled goals attached."""

        if self.outcome != "sat":
            raise ValueError(
                "only a SAT query can gain settled goals through replay and audit")
        timings = self.timings
        if replay_audit_ns is not None:
            if type(replay_audit_ns) is not int or replay_audit_ns < 0:
                raise ValueError("replay/audit time is natural nanoseconds")
            measured = StageTimings({"replay_audit_ns": replay_audit_ns})
            timings = measured if timings is None else timings.merge(measured)
        return QueryTelemetry(
            backend=self.backend, kind=self.kind,
            semantic_instance=self.semantic_instance, horizon=self.horizon,
            goals_asked=self.goals_asked, goals_settled=goals,
            outcome=self.outcome, cache_identity=self.cache_identity,
            cache=self.cache, shape=self.shape,
            timings=timings,
            peak_rss_bytes=self.peak_rss_bytes, provenance=self.provenance)

    def with_measurements(self, fragment):
        """Attach disjoint frontend/worker/outer measurements to this call."""

        if type(fragment) is not QueryTelemetryFragment:
            raise TypeError(
                "query measurements need an exact QueryTelemetryFragment")
        coordinates = (
            self.backend, self.kind, self.semantic_instance, self.horizon,
            self.goals_asked,
        )
        if coordinates != fragment._coordinates():
            raise ValueError(
                "query measurements name a different logical query")
        shape = self.shape
        if fragment.shape is not None:
            shape = (
                fragment.shape if shape is None else shape.merge(fragment.shape))
        timings = self.timings
        if fragment.timings is not None:
            timings = (
                fragment.timings
                if timings is None else timings.merge(fragment.timings))
        return QueryTelemetry(
            backend=self.backend, kind=self.kind,
            semantic_instance=self.semantic_instance, horizon=self.horizon,
            goals_asked=self.goals_asked, goals_settled=self.goals_settled,
            outcome=self.outcome, cache_identity=self.cache_identity,
            cache=self.cache, shape=shape, timings=timings,
            peak_rss_bytes=self.peak_rss_bytes, provenance=self.provenance)

    def with_cache(self, identity, disposition):
        """Attach one cache lookup without changing the measured logical query."""

        if type(identity) is not str or not identity:
            raise ValueError("a semantic answer cache identity is nonempty")
        if type(disposition) is not CacheDisposition:
            raise TypeError("a semantic answer cache lookup has one disposition")
        return QueryTelemetry(
            backend=self.backend, kind=self.kind,
            semantic_instance=self.semantic_instance, horizon=self.horizon,
            goals_asked=self.goals_asked, goals_settled=self.goals_settled,
            outcome=self.outcome, cache_identity=identity,
            cache=disposition, shape=self.shape, timings=self.timings,
            peak_rss_bytes=self.peak_rss_bytes, provenance=self.provenance)

    def with_runtime(self, *, peak_rss_bytes=None, provenance=None):
        """Attach worker runtime facts once, without replacing call measurements."""

        if self.peak_rss_bytes is not None and peak_rss_bytes is not None:
            raise ValueError("peak RSS already has an owning measurement boundary")
        if self.provenance and provenance:
            raise ValueError("query provenance already has an owning boundary")
        return QueryTelemetry(
            backend=self.backend, kind=self.kind,
            semantic_instance=self.semantic_instance, horizon=self.horizon,
            goals_asked=self.goals_asked, goals_settled=self.goals_settled,
            outcome=self.outcome, cache_identity=self.cache_identity,
            cache=self.cache, shape=self.shape, timings=self.timings,
            peak_rss_bytes=(
                self.peak_rss_bytes
                if peak_rss_bytes is None else peak_rss_bytes),
            provenance=self.provenance if provenance is None else provenance)


def annotate_query(call, exact, timeout_ms, *, backend, provenance):
    """Add worker measurements; a failed measurement never changes the answer.

    The backend supplies its provenance reader, evaluated inside this boundary
    so missing tool metadata cannot turn a completed query into a refusal.
    """
    from .query import SolverAnswer

    try:
        if call.telemetry is not None:
            return call.with_telemetry(call.telemetry.with_runtime(
                peak_rss_bytes=process_peak_rss_bytes(),
                provenance=provenance(timeout_ms)))
        settled = (
            call.goals if call.answer is SolverAnswer.UNSAT
            or call.answer is SolverAnswer.UNKNOWN and len(call.goals) == 1
            else ())
        telemetry = QueryTelemetry(
            backend=backend, kind=query_kind_for_goals(call.goals),
            semantic_instance=semantic_instance_of(exact),
            horizon=exact.domain.bound, goals_asked=call.goals,
            goals_settled=settled, outcome=call.answer.value,
            peak_rss_bytes=process_peak_rss_bytes(),
            provenance=provenance(timeout_ms))
        return call.with_telemetry(telemetry)
    except Exception:  # noqa: BLE001 - telemetry cannot change the solver answer
        return call


def annotate_refusal(refusal, operation, exact, payload, started_ns, *,
                     backend, provenance):
    """Attach query-attempt measurements only after a semantic binding exists."""
    from .refusal import Refusal

    try:
        if operation not in {"feasible", "query"} or exact is None:
            return refusal
        if operation == "feasible":
            kind, goals = QueryKind.FEASIBILITY, ()
        else:
            goals = payload.get("goals")
            if type(goals) is not list or not goals or any(
                    type(goal) is not str or not goal for goal in goals):
                return refusal
            kind = query_kind_for_goals(goals)
        timeout_ms = payload.get("timeout_ms")
        telemetry = QueryTelemetry(
            backend=backend, kind=kind,
            semantic_instance=semantic_instance_of(exact),
            horizon=exact.domain.bound, goals_asked=goals,
            outcome="refused",
            timings=StageTimings({"total_ns": time.monotonic_ns() - started_ns}),
            peak_rss_bytes=process_peak_rss_bytes(),
            provenance=provenance(
                timeout_ms if type(timeout_ms) is int and timeout_ms > 0 else None))
        detail = dict(refusal.detail)
        detail["query_telemetry"] = telemetry.as_dict()
        return Refusal(refusal.kind, refusal.why, **detail)
    except Exception:  # noqa: BLE001 - telemetry cannot replace the refusal
        return refusal


def annotate_verdict(verdict, exact, timeout_ms, *, backend, provenance):
    """Retain the verdict and query transcript while adding worker measurements."""
    from .instance import Verdict

    return Verdict(
        verdict.reachable, verdict.unreachable, verdict.undecided,
        queries=tuple(annotate_query(
            call, exact, timeout_ms, backend=backend, provenance=provenance)
            for call in verdict.queries))


def process_peak_rss_bytes():
    """The process high-water RSS in bytes where the platform defines its units."""

    try:
        import resource

        measured = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, ValueError):
        return None
    if type(measured) not in (int, float) or measured <= 0:
        return None
    # Linux and the BSDs report KiB; macOS reports bytes.
    return int(measured if sys.platform == "darwin" else measured * 1024)
