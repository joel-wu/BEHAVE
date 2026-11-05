"""Backend-neutral Search contracts, independent of execution orchestration.

Providers and the scheduler exchange these closed requests, plans, selections
and answers. None owns a queue, worker, replay operation or scoring decision.
Actual source observations use ReplayFeedback; local solver answers retain their
scope and have no conversion to bounded-Analysis evidence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from .core.catalogue import CatalogueManifest
from .core.frozen import Frozen
from .core.refusal import Refusal
from .core.stimulus import Stimulus, canonical_json
from .generation_cache import GenerationCacheTelemetry

if TYPE_CHECKING:
    from .core.goal_provider import GoalCapabilityUse

__all__ = [
    "GenerationProvider", "GenerationExtensions", "Outcome", "ProbeRequest", "ProbeResult", "ProbeSelection",
    "QueryPlan", "QueryTemplate", "ReplayFeedback", "Scope", "StimulusField",
    "stimulus_bytes",
]


class Scope(Enum):
    """The finite local question answered by a generation probe."""

    EXACT_LOCAL_HIT = "exact-local-hit"
    PATH_PREFIX = "path-prefix"
    RTL_LOCAL_HIT = "rtl-local-hit"
    STATE_PREPARATION = "state-preparation"


class Outcome(Enum):
    """Local query status, including a suspended build, never an analysis verdict."""

    CONSTRUCTING = "constructing"
    SAT = "sat"
    PROPOSED = "proposed"
    LOCAL_UNSAT = "local-unsat"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class ProbeResult:
    """One local probe answer and the scope over which it was obtained."""

    __slots__ = ("candidate", "detail", "goal", "outcome", "scope", "solve_ms")

    def __init__(self, outcome, scope, goal=None, candidate=None, detail="",
                 solve_ms=0.0):
        if not isinstance(outcome, Outcome):
            raise TypeError(f"{outcome!r} is not an Outcome")
        if not isinstance(scope, Scope):
            raise TypeError(
                f"{scope!r} is not a Scope, and a probe result without one "
                "cannot be read")
        if outcome in (Outcome.SAT, Outcome.PROPOSED) and candidate is None:
            raise ValueError("a satisfiable probe or preparation proposal must carry a candidate")
        if outcome not in (Outcome.SAT, Outcome.PROPOSED) and candidate is not None:
            raise ValueError(f"a {outcome.value} probe cannot carry a candidate")
        # A speculative preparation may pause or find no proposal. Neither
        # outcome is a solver verdict about the original source or path.
        if (outcome is Outcome.PROPOSED and scope is not Scope.STATE_PREPARATION) or (
                scope is Scope.STATE_PREPARATION and
                (outcome not in (Outcome.PROPOSED, Outcome.UNSUPPORTED,
                                 Outcome.CONSTRUCTING, Outcome.UNKNOWN) or goal is not None)):
            raise ValueError("state preparation proposes inputs, never a solver answer or named goal")
        if outcome is Outcome.CONSTRUCTING and goal is not None:
            raise ValueError("unfinished construction cannot name a solved goal")
        self.outcome = outcome
        self.scope = scope
        self.goal = goal
        self.candidate = candidate
        self.detail = detail
        self.solve_ms = solve_ms

    def as_dict(self):
        return {
            "outcome": self.outcome.value,
            "scope": self.scope.value,
            "goal": self.goal,
            "detail": self.detail,
            "solve_ms": round(self.solve_ms, 3),
        }

    def __repr__(self):
        return (
            f"ProbeResult({self.outcome.value}, {self.scope.value}, "
            f"{self.goal!r})")


def stimulus_bytes(stimulus: Stimulus) -> bytes:
    """The exact canonical bytes of the stimulus's semantic identity.

    ``source`` is provenance and is explicitly outside ``Stimulus.fingerprint``.  It is
    therefore excluded here too.  The scheduler keeps these bytes, rather than only the
    digest, as the key in ``Seen`` so deduplication does not assume collision freedom.
    """

    if type(stimulus) is not Stimulus:
        raise TypeError(f"expected Stimulus, got {type(stimulus).__name__}")
    return stimulus.identity_bytes


class StimulusField(Frozen):
    """One finite queue-payload field a request permits a provider to solve.

    ``source_position`` is the schedule slot that releases this queue transaction,
    not the later slot when it is first presented or accepted. Keeping it next to
    the queue coordinate makes the input map auditable and rejects out-of-range,
    non-offered, contradictory, or nonmonotone maps before a query is posed.
    """

    __slots__ = ("endpoint", "field", "source_position", "transaction")

    def __init__(self, endpoint, transaction, field, source_position):
        self.endpoint = str(endpoint)
        self.transaction = transaction
        self.field = field
        self.source_position = source_position
        self._freeze()

    @property
    def coordinate(self):
        return (self.endpoint, self.transaction, self.field)

    def as_dict(self):
        return {
            "endpoint": self.endpoint,
            "transaction": self.transaction,
            "field": self.field,
            "source_position": self.source_position,
        }

    @classmethod
    def from_dict(cls, value):
        """Decode the exact finite image used by isolated generation jobs."""

        fields = {"endpoint", "transaction", "field", "source_position"}
        if type(value) is not dict or set(value) != fields:
            raise TypeError("a stimulus-field image has exactly four fields")
        if type(value["endpoint"]) is not str or not value["endpoint"]:
            raise TypeError("a stimulus-field endpoint is a nonempty exact string")
        for name in ("transaction", "field", "source_position"):
            if type(value[name]) is not int or value[name] < 0:
                raise TypeError(
                    f"a stimulus-field {name} is a nonnegative exact integer")
        got = cls(
            value["endpoint"], value["transaction"], value["field"],
            value["source_position"])
        if got.as_dict() != value:
            raise ValueError("a stimulus-field image is not canonical")
        return got


def _field_identity(field):
    """A total canonical image, including for a field later refused as malformed."""

    if type(field) is not StimulusField:
        return {"invalid_type": type(field).__name__}
    body = {"endpoint": field.endpoint}
    for name in ("transaction", "field", "source_position"):
        value = getattr(field, name)
        body[name] = (value if type(value) is int and not isinstance(value, bool)
                      else {"invalid_type": type(value).__name__})
    return body


class ProbeRequest(Frozen):
    """One canonical local generation question selected from a seed.

    ``rank`` is the provider's explicit deterministic policy order.  It is not part of
    the question's identity: duplicate questions with different ranks are one request,
    scheduled at the minimum rank.  The scheduler breaks rank ties by the exact
    canonical identity bytes. Select returns an ordered finite sequence or closed
    query plan; unordered containers cannot silently invent a provider policy.
    ``catalogue_digest`` binds the question to the provider's complete source-goal
    manifest.  The evaluated artifact catalogue is carried separately by
    :class:`GeneratedStimulusSet`; a source-guided search cannot replace it.
    """

    __slots__ = (
        "_identity", "catalogue_digest", "mutable_fields", "occurrence", "rank",
        "targets",
    )

    def __init__(self, targets=(), occurrence="", mutable_fields=(), rank=0, *,
                 catalogue_digest):
        self.targets = tuple(sorted(str(target) for target in targets))
        self.occurrence = str(occurrence)
        if type(catalogue_digest) is not str \
                or len(catalogue_digest) != 64 \
                or any(char not in "0123456789abcdef"
                       for char in catalogue_digest):
            raise TypeError(
                "a probe request names its lowercase SHA-256 catalogue digest")
        self.catalogue_digest = catalogue_digest
        self.mutable_fields = tuple(sorted(
            mutable_fields,
            key=lambda field: canonical_json(_field_identity(field))))
        self.rank = rank
        self._identity = hashlib.sha256(self.identity_bytes).hexdigest()
        self._freeze()

    @property
    def identity_bytes(self):
        return canonical_json({
            "targets": list(self.targets),
            "occurrence": self.occurrence,
            "catalogue_digest": self.catalogue_digest,
            "mutable_fields": [_field_identity(field)
                               for field in self.mutable_fields],
        }).encode("utf-8")

    @property
    def identity(self):
        return self._identity

    def as_dict(self):
        return {
            "id": self.identity,
            "targets": list(self.targets),
            "occurrence": self.occurrence,
            "catalogue_digest": self.catalogue_digest,
            "mutable_fields": [_field_identity(field)
                               for field in self.mutable_fields],
            "rank": self.rank,
        }

    def with_targets(self, targets):
        """Restrict a selected question without changing its execution domain."""
        result = ProbeRequest(targets, occurrence=self.occurrence,
                              mutable_fields=self.mutable_fields, rank=self.rank,
                              catalogue_digest=self.catalogue_digest)
        if not result.is_restriction_of(self):
            raise ValueError("a selected request may only lose target goals")
        return result

    def is_restriction_of(self, selected):
        if type(selected) is not ProbeRequest or not self.targets:
            return False
        return (set(self.targets) <= set(selected.targets)
                and self.occurrence == selected.occurrence
                and self.catalogue_digest == selected.catalogue_digest
                and self.rank == selected.rank
                and tuple(_field_identity(f) for f in self.mutable_fields)
                == tuple(_field_identity(f) for f in selected.mutable_fields))

    @classmethod
    def from_dict(cls, value):
        """Decode the one canonical request image shared by isolated providers."""

        fields = {
            "id", "targets", "occurrence", "catalogue_digest",
            "mutable_fields", "rank",
        }
        if type(value) is not dict or set(value) != fields:
            raise TypeError("a probe-request image has its complete field set")
        if type(value["id"]) is not str \
                or type(value["targets"]) is not list \
                or type(value["occurrence"]) is not str \
                or type(value["catalogue_digest"]) is not str \
                or type(value["mutable_fields"]) is not list \
                or type(value["rank"]) is not int or value["rank"] < 0:
            raise TypeError("a probe-request image has exact finite field types")
        if any(type(target) is not str or not target
               for target in value["targets"]):
            raise TypeError("probe-request targets are nonempty exact strings")
        got = cls(
            value["targets"], occurrence=value["occurrence"],
            mutable_fields=tuple(
                StimulusField.from_dict(field)
                for field in value["mutable_fields"]),
            rank=value["rank"], catalogue_digest=value["catalogue_digest"])
        if got.as_dict() != value:
            raise ValueError("a probe-request image is not canonical")
        return got


def _targets(values):
    values = tuple(sorted(values))
    if (not values or any(type(t) is not str or not t.strip() for t in values)
            or len(set(values)) != len(values)):
        raise ValueError("query targets are distinct nonempty strings")
    return values


class QueryTemplate(Frozen):
    """A question's immutable parameters, before hashing or wire expansion."""

    __slots__ = ("targets", "occurrence", "mutable_fields", "rank", "catalogue_digest", "key")

    def __init__(self, targets, occurrence, mutable_fields, rank, *, catalogue_digest):
        targets = _targets(targets)
        fields = tuple(mutable_fields)
        if type(occurrence) is not str or not occurrence.strip():
            raise ValueError("a query occurrence is a nonempty string")
        if type(rank) is not int or rank < 0:
            raise ValueError("a query rank is a nonnegative exact integer")
        if (type(catalogue_digest) is not str or len(catalogue_digest) != 64
                or any(c not in '0123456789abcdef' for c in catalogue_digest)):
            raise ValueError("a query plan binds its catalogue SHA-256")
        if not fields or any(type(f) is not StimulusField for f in fields):
            raise TypeError("query fields are a nonempty sequence of StimulusField records")
        # Validate field shape independently of the seed/schedule check.
        for field in fields:
            StimulusField.from_dict(field.as_dict())
        fields = tuple(sorted(fields, key=lambda f: canonical_json(f.as_dict())))
        self.targets, self.occurrence = targets, occurrence
        self.mutable_fields, self.rank = fields, rank
        self.catalogue_digest = catalogue_digest
        self.key = (occurrence, targets, tuple(
            (f.endpoint, f.transaction, f.field, f.source_position) for f in fields))
        self._freeze()

    @classmethod
    def factory(cls):
        """Share checked immutable field domains within one plan construction.

        The cache dies with this factory, not with the worker. Each target set,
        occurrence and rank is still validated; only an identical tuple of
        already checked field objects can avoid repeated sorting/serialization.
        """
        domains = {}

        def make(targets, occurrence, mutable_fields, rank, *, catalogue_digest):
            if type(occurrence) is not str or not occurrence.strip():
                raise ValueError("a query occurrence is a nonempty string")
            fields = tuple(mutable_fields)
            key = catalogue_digest, fields
            previous = domains.get(key)
            if previous is None:
                previous = cls(targets, occurrence, fields, rank,
                               catalogue_digest=catalogue_digest)
                domains[key] = previous
                return previous
            return previous._derived(_targets(targets), rank, occurrence)

        return make

    def request(self):
        return ProbeRequest(self.targets, self.occurrence, self.mutable_fields,
                            self.rank, catalogue_digest=self.catalogue_digest)

    @property
    def identity(self):
        # Only diagnostics or a consumed question need this digest.
        return self.request().identity

    def with_targets(self, targets):
        targets = tuple(sorted(targets))
        if not targets or not set(targets) <= set(self.targets):
            raise ValueError("a query template may only lose targets")
        if targets == self.targets:
            return self
        return self._derived(targets, self.rank)

    def with_rank(self, rank):
        if type(rank) is not int or rank < 0:
            raise ValueError("a query rank is a nonnegative exact integer")
        return self if rank == self.rank else self._derived(self.targets, rank)

    def at(self, occurrence, rank):
        """A different query site over the same checked field/target domain."""
        if type(occurrence) is not str or not occurrence.strip():
            raise ValueError("a query occurrence is a nonempty string")
        return self._derived(self.targets, rank, occurrence)

    def _derived(self, targets, rank, occurrence=None):
        if type(rank) is not int or rank < 0:
            raise ValueError("a query rank is a nonnegative exact integer")
        occurrence = self.occurrence if occurrence is None else occurrence
        if type(occurrence) is not str or not occurrence.strip():
            raise ValueError("a query occurrence is a nonempty string")
        # Only already-validated immutable fields are shared; neither caller
        # inputs nor a lazy callback can replace their execution domain.
        result = object.__new__(QueryTemplate)
        result.targets, result.rank = targets, rank
        result.occurrence, result.mutable_fields = occurrence, self.mutable_fields
        result.catalogue_digest = self.catalogue_digest
        result.key = (occurrence, targets, self.key[2])
        result._freeze()
        return result


class QueryPlan(Frozen):
    """An indexed, compact selection whose requests expand only on demand."""

    __slots__ = ("templates", "catalogue_digest", "chains", "equivalents", "explorations", "contexts", "preparations", "_ranks")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("QueryPlan is a closed generation-boundary record")

    def __init__(self, templates, catalogue_digest, *, chains=(), equivalents=(), explorations=(), contexts=(), preparations=()):
        if type(templates) not in (list, tuple):
            raise TypeError("a query plan has a finite ordered template sequence")
        self.templates = tuple(templates)
        if (type(catalogue_digest) is not str or len(catalogue_digest) != 64
                or any(c not in '0123456789abcdef' for c in catalogue_digest)):
            raise ValueError("a query plan binds its catalogue SHA-256")
        self.catalogue_digest = catalogue_digest
        if any(type(t) is not QueryTemplate or t.catalogue_digest != catalogue_digest
               for t in self.templates):
            raise ValueError("all query templates belong to one catalogue")
        ranks = tuple(t.rank for t in self.templates)
        if ranks != tuple(sorted(set(ranks))):
            raise ValueError("query-plan ranks are distinct and increasing")
        if len({t.key for t in self.templates}) != len(self.templates):
            raise ValueError("query-plan questions are distinct")
        self._ranks = MappingProxyType({t.rank: t for t in self.templates})
        self.chains = self._groups(chains)
        self.equivalents = self._groups(equivalents)
        if (type(explorations) not in (list, tuple)
                or any(type(rank) is not int or rank not in self._ranks for rank in explorations)
                or tuple(explorations) != tuple(sorted(set(explorations)))):
            raise ValueError("context explorations name distinct selected ranks in order")
        self.explorations = tuple(explorations)
        # Replay-only setup recipes and queries of a prepared state share no
        # proof authority, but need distinct scheduling roles after a setup.
        exploratory = set(self.explorations)
        if (type(preparations) not in (list, tuple)
                or any(type(rank) is not int or rank not in exploratory for rank in preparations)
                or tuple(preparations) != tuple(sorted(set(preparations)))):
            raise ValueError("preparations name distinct exploratory ranks in order")
        self.preparations = tuple(preparations)
        # Opaque source-context identities guide ordering only. They cannot
        # retire a question, share a verdict, or establish state equivalence.
        if (type(contexts) not in (list, tuple)
                or any(type(row) not in (list, tuple) or len(row) != 2
                       or type(row[0]) is not int or row[0] not in self.explorations
                       or type(row[1]) is not str or len(row[1]) != 64
                       or any(c not in '0123456789abcdef' for c in row[1]) for row in contexts)
                or [row[0] for row in contexts] != sorted({row[0] for row in contexts})):
            raise ValueError("context identities name distinct exploratory ranks and SHA-256 hints")
        self.contexts = MappingProxyType(dict(contexts))
        if any(set(group).intersection(self.explorations) for group in self.equivalents):
            raise ValueError("context proposals cannot share goal-UNSAT answers")
        self._freeze()

    def _groups(self, groups):
        if type(groups) not in (list, tuple):
            raise TypeError("query groups are a finite ordered sequence")
        out, used = [], set()
        for group in groups:
            if (type(group) not in (list, tuple) or len(group) < 2
                    or any(type(rank) is not int or rank not in self._ranks for rank in group)
                    or len(set(group)) != len(group) or used.intersection(group)):
                raise ValueError("query groups are disjoint selected rank sequences")
            if len({self._ranks[rank].targets for rank in group}) != 1:
                raise ValueError("a query group has one target set")
            used.update(group)
            out.append(tuple(group))
        return tuple(out)

    def __len__(self):
        return len(self.templates)

    def __iter__(self):
        return (item.request() for item in self.templates)

    def __getitem__(self, index):
        if type(index) is str:
            # Compatibility for inspection; production Probe uses its bound rank.
            return next(item for item in self if item.identity == index)
        if type(index) is slice:
            return tuple(item.request() for item in self.templates[index])
        return self.templates[index].request()

    def values(self):
        return iter(self)

    def selected(self, request):
        item = self._ranks.get(request.rank)
        if item is None:
            return None
        expected = item.request()
        return expected if request.is_restriction_of(expected) else None

    def request_groups(self, groups):
        return tuple(tuple(self._ranks[rank].identity for rank in group) for group in groups)

    def retaining(self, templates):
        ranks = {item.rank for item in templates}
        def kept(groups):
            return tuple(row for group in groups
                         if len(row := tuple(rank for rank in group if rank in ranks)) > 1)
        return QueryPlan(tuple(templates), self.catalogue_digest,
                         chains=kept(self.chains), equivalents=kept(self.equivalents),
                         explorations=tuple(rank for rank in self.explorations if rank in ranks),
                         contexts=tuple((rank, key) for rank, key in self.contexts.items() if rank in ranks),
                         preparations=tuple(rank for rank in self.preparations if rank in ranks))

    def as_dict(self):
        targets, sites, rows = {}, {}, []
        for item in self.templates:
            target = targets.setdefault(item.targets, len(targets))
            site = sites.setdefault((item.occurrence, item.key[2]), len(sites))
            rows.append([item.rank, target, site])
        image = dict(version="query-plan/1", catalogue_digest=self.catalogue_digest,
                    targets=[list(group) for group in targets],
                    sites=[[occurrence, [list(field) for field in fields]]
                           for occurrence, fields in sites], rows=rows,
                    chains=[list(g) for g in self.chains],
                    equivalents=[list(g) for g in self.equivalents])
        if self.explorations:
            image.update(version="query-plan/2", explorations=list(self.explorations))
        if self.contexts:
            image.update(version="query-plan/3", contexts=[list(row) for row in self.contexts.items()])
        if self.preparations:
            image.update(version="query-plan/4", preparations=list(self.preparations),
                         contexts=[list(row) for row in self.contexts.items()])
        return image

    @classmethod
    def from_dict(cls, value):
        keys = {"version", "catalogue_digest", "targets", "sites", "rows", "chains", "equivalents"}
        if type(value) is dict and value.get("version") in ("query-plan/2", "query-plan/3", "query-plan/4"):
            keys.add("explorations")
        if type(value) is dict and value.get("version") in ("query-plan/3", "query-plan/4"):
            keys.add("contexts")
        if type(value) is dict and value.get("version") == "query-plan/4":
            keys.add("preparations")
        if (type(value) is not dict or set(value) != keys
                or value["version"] not in ("query-plan/1", "query-plan/2", "query-plan/3", "query-plan/4")):
            raise ValueError("a query plan has its exact versioned image")
        if any(type(value[k]) is not list for k in ("targets", "sites", "rows", "chains", "equivalents")):
            raise TypeError("query-plan tables are exact lists")
        targets = []
        for group in value["targets"]:
            if type(group) is not list:
                raise TypeError("query target sets are exact lists")
            targets.append(_targets(group))
        sites, checked_fields = [], {}
        for row in value["sites"]:
            if type(row) is not list or len(row) != 2 or type(row[1]) is not list:
                raise TypeError("query sites name one occurrence and finite fields")
            fields = []
            for field in row[1]:
                if type(field) is not list or len(field) != 4:
                    raise TypeError("query fields have four coordinates")
                parsed = StimulusField.from_dict(dict(zip(
                    ("endpoint", "transaction", "field", "source_position"), field, strict=True)))
                # Validate before interning: True must not alias integer 1.
                fields.append(checked_fields.setdefault(tuple(field), parsed))
            sites.append((row[0], tuple(fields)))
        templates = []
        template = QueryTemplate.factory()
        checked_sites = {}
        for row in value["rows"]:
            if (type(row) is not list or len(row) != 3
                    or any(type(i) is not int or i < 0 for i in row)
                    or row[1] >= len(value["targets"]) or row[2] >= len(sites)):
                raise ValueError("query rows have valid rank/target/site indices")
            occurrence, fields = sites[row[2]]
            previous = checked_sites.get(row[2])
            if previous is None:
                previous = template(targets[row[1]], occurrence, fields, row[0],
                                    catalogue_digest=value["catalogue_digest"])
                checked_sites[row[2]] = previous
            # Target tables and site domains are checked once, not once per
            # row's Cartesian pairing. Rank validation still applies to each row.
            templates.append(previous._derived(targets[row[1]], row[0]))
        got = cls(templates, value["catalogue_digest"], chains=value["chains"],
                  equivalents=value["equivalents"], explorations=value.get("explorations", ()),
                  contexts=value.get("contexts", ()), preparations=value.get("preparations", ()))
        if got.as_dict() != value:
            raise ValueError("a query plan image is canonical, without unused table entries")
        return got


class ReplayFeedback(Frozen):
    """Positive source hits bound to one seed, catalogue and replay record."""

    __slots__ = ("catalogue_digest", "hits", "record", "stimulus")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("ReplayFeedback is a closed record")

    def __init__(self, stimulus, catalogue_digest, record, hits):
        for value in (stimulus, catalogue_digest, record):
            if (type(value) is not str or len(value) != 64
                    or any(c not in "0123456789abcdef" for c in value)):
                raise TypeError("replay feedback identities are SHA-256 digests")
        if type(hits) not in (tuple, list, set, frozenset) or any(
                type(goal) is not str or not goal for goal in hits):
            raise TypeError("replay feedback hits are finite nonempty goal names")
        self.stimulus, self.catalogue_digest, self.record = stimulus, catalogue_digest, record
        self.hits = tuple(sorted(set(hits)))
        self._freeze()

    def as_dict(self):
        return {"stimulus": self.stimulus, "catalogue_digest": self.catalogue_digest,
                "record": self.record, "hits": list(self.hits)}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {
                "stimulus", "catalogue_digest", "record", "hits"}:
            raise TypeError("replay feedback has exactly its four fields")
        result = cls(**value)
        if result.as_dict() != value:
            raise ValueError("replay feedback must be canonical")
        return result


class ProbeSelection(Frozen):
    """One worker-confirmed, possibly empty, deterministic Select answer.

    The catalogue digest is part of the answer rather than mutable provider telemetry.
    Consequently an empty selection still records which source declaration the worker
    actually rebuilt and queried.  This closes the otherwise-unobservable case in which
    a provider exposes catalogue A before and after a search while selecting under B.
    For runtime input, ``recorded_seed`` binds source positions to its actual
    execution schedule; it does not replace the candidate's runtime policy.

    ``equivalent_requests`` partitions selected request identities whose exact
    local questions are equal under this selection's fixed source/trace. Only a
    completed EXACT_LOCAL_HIT UNSAT may skip another member. SAT, UNKNOWN and
    other scopes keep every occurrence; this relation never changes goal hits.

    ``constant_false_goals`` records IR simplifications in this selection's
    prepared context (which may include its memory image). It is not a global
    proof, a coverage hit, or permission to exclude that goal from another seed.

    ``query_chains`` groups related requests that can extend one construction.
    It controls scheduling only: every member retains its own domain and result.
    Chain membership never permits skipping a request or reusing an answer.
    """

    __slots__ = ("catalogue_digest", "constant_false_goals", "_equivalent_requests",
                 "_query_chains", "recorded_seed", "replay", "requests")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("ProbeSelection is a closed generation-boundary record")

    def __init__(self, requests, catalogue_digest, *, replay=None, recorded_seed=None,
                 equivalent_requests=(), constant_false_goals=(), query_chains=()):
        planned = type(requests) is QueryPlan
        if type(requests) not in (tuple, list, QueryPlan):
            raise TypeError("a probe selection has an ordered request list or closed query plan")
        requests = requests if planned else tuple(requests)
        templates = requests.templates if planned else requests
        if not planned and any(type(request) is not ProbeRequest for request in requests):
            raise TypeError("a probe selection contains only exact ProbeRequest records")
        if planned and (requests.catalogue_digest != catalogue_digest
                        or equivalent_requests or query_chains):
            raise ValueError("a query plan owns its catalogue and rank-bound groups")
        if type(catalogue_digest) is not str or len(catalogue_digest) != 64 \
                or any(char not in "0123456789abcdef" for char in catalogue_digest):
            raise TypeError(
                "a probe selection names its actual lowercase SHA-256 catalogue digest")
        self.requests = requests
        false_goals = tuple(constant_false_goals)
        if (any(type(goal) is not str or not goal for goal in false_goals)
                or false_goals != tuple(sorted(set(false_goals)))
                or any(set(request.targets) & set(false_goals) for request in templates)):
            raise ValueError("selection-local false goals are sorted, distinct and not queried")
        self.constant_false_goals = false_goals
        if type(equivalent_requests) not in (tuple, list):
            raise TypeError("local question equivalence has ordered request groups")
        selected = {} if planned else {request.identity: request for request in requests}
        groups, grouped = [], set()
        for group in equivalent_requests:
            if type(group) not in (tuple, list) or len(group) < 2:
                raise ValueError("an equivalent request group names at least two requests")
            if any(type(key) is not str or key not in selected for key in group):
                raise ValueError("equivalence names only selected request identities")
            if len(set(group)) != len(group) or grouped.intersection(group):
                raise ValueError("equivalent request groups are disjoint")
            if len({selected[key].targets for key in group}) != 1:
                raise ValueError("equivalent requests have the same original target set")
            groups.append(tuple(group))
            grouped.update(group)
        self._equivalent_requests = tuple(groups)
        if type(query_chains) not in (tuple, list):
            raise TypeError("query chains have ordered request groups")
        chains, chained = [], set()
        for chain in query_chains:
            if type(chain) not in (tuple, list) or len(chain) < 2:
                raise ValueError("a query chain names at least two requests")
            if any(type(key) is not str or key not in selected for key in chain):
                raise ValueError("query chains name only selected requests")
            if len(set(chain)) != len(chain) or chained.intersection(chain):
                raise ValueError("query chains are disjoint")
            if len({selected[key].targets for key in chain}) != 1:
                raise ValueError("a query chain retains one original target group")
            chains.append(tuple(chain))
            chained.update(chain)
        self._query_chains = tuple(chains)
        self.catalogue_digest = catalogue_digest
        if replay is not None and type(replay) is not ReplayFeedback:
            raise TypeError("Select replay feedback must be a closed record")
        self.replay = replay
        if recorded_seed is not None and (
                type(recorded_seed) is not Stimulus or recorded_seed.replay_policy is not None):
            raise TypeError("a recorded Select seed has an executed, fixed schedule")
        self.recorded_seed = recorded_seed
        self._freeze()

    @property
    def equivalent_requests(self):
        if type(self.requests) is QueryPlan:
            return self.requests.request_groups(self.requests.equivalents)
        return self._equivalent_requests

    @property
    def query_chains(self):
        if type(self.requests) is QueryPlan:
            return self.requests.request_groups(self.requests.chains)
        return self._query_chains

    def as_dict(self):
        result = {
            "requests": (self.requests.as_dict() if type(self.requests) is QueryPlan else
                         [request.as_dict() for request in self.requests]),
            "catalogue_digest": self.catalogue_digest,
        }
        if self._equivalent_requests:
            result["equivalent_requests"] = [list(group) for group in self._equivalent_requests]
        if self._query_chains:
            result["query_chains"] = [list(chain) for chain in self._query_chains]
        if self.constant_false_goals:
            result["constant_false_goals"] = list(self.constant_false_goals)
        if self.replay is not None:
            result["replay"] = self.replay.as_dict()
        if self.recorded_seed is not None:
            result["recorded_seed"] = self.recorded_seed.as_dict()
        return result


class GenerationProvider(Protocol):
    """The required backend seam used by :func:`generate`.

    Optional capabilities are bound by ``GenerationExtensions`` once per
    invocation/lane. Their callbacks read live state; binding does not prepare
    a source, open a worker, or cache a refusal/solver answer.

    Selection omissions use ``Refusal``.  Probe answers use the existing closed local
    ``ProbeResult`` algebra.  Neither method receives the scheduler's queue, ``Seen``,
    result set, or telemetry, so a provider has no mutation or status authority.

    Optional ``search_refusal()`` reports an already established, direction-wide
    capability failure. It performs no preparation or queries. A local Select or
    Probe refusal alone never terminates the direction or proves unreachability.
    Optional ``seed_refusal(seed)`` similarly reports a failed recorded query
    context without disabling seeds belonging to other contexts.

    ``targets`` restricts request construction, never observation. An empty set
    requests feedback without local requests; ``None`` uses the full capability.
    ``replay_guided=True`` additionally requires bound ReplayFeedback on every
    successful Select. The scheduler may further restrict requests after new
    hits, preserving occurrence, rank and mutable-field bindings.

    Optional ``query_targets(targets, seed=seed)`` further restricts query work
    using already established literal-false IR predicates in this seed's
    prepared domain. It performs no preparation, records no hits, and must not
    generalize local UNSAT. The catalogue and coverage ledger remain intact.

    A composite provider may expose ``request_refusal(seed, request)``: a
    read-only report of a known unavailable query capability, or None. This
    skips only affected requests; it must not prepare/retry a worker, change
    the catalogue, or turn a failed query into an unreachability claim.
    """

    name: str
    policy: str
    targetable_goals: tuple[str, ...]

    def catalogue_manifest(self) -> CatalogueManifest: ...

    def cache_telemetry(self) -> GenerationCacheTelemetry: ...

    def select(self, seed: Stimulus, *, targets=None) -> ProbeSelection | Refusal: ...

    def probe(self, seed: Stimulus, request: ProbeRequest) -> ProbeResult: ...


@dataclass(frozen=True, slots=True)
class GenerationExtensions:
    """One lane's optional ports, separate from its changing execution state.

    The port surface is fixed for the invocation/context. These are bound
    callables, not the results of calling them. Refusal readers
    must remain live across Select/Probe and cleanup. The scheduler owns all
    queues, budgets and coverage; no extension receives those mutable owners.
    Minimal providers need only GenerationProvider's required interface.

    ``fork_search_worker(index)`` returns a private provider with search_scope;
    it does not allocate another budget or decide CPU concurrency. Batch ports
    retain their existing ordered, one-answer-per-seed contracts. Absence is
    explicit so the scheduler can choose its single-seed fallback.
    """

    replay_guided: bool = False
    runtime_guidance: bool = False
    explores_contexts: bool = False
    parallel_search: bool = True
    search_scope: Callable[..., AbstractContextManager] | None = None
    fork_search_worker: Callable[[int], GenerationProvider] | None = None
    prepare_seeds: Callable[[Sequence[Stimulus]], object] | None = None
    select_many: Callable[..., Sequence[ProbeSelection | Refusal]] | None = None
    observe_many: Callable[[Sequence[Stimulus]], Sequence[ReplayFeedback | Refusal] | None] | None = None
    search_refusal: Callable[[], Refusal | None] | None = None
    seed_refusal: Callable[[Stimulus], Refusal | None] | None = None
    request_refusal: Callable[[Stimulus, ProbeRequest], Refusal | None] | None = None
    query_targets: Callable[..., Sequence[str]] | None = None
    goal_capability_use: Callable[[], GoalCapabilityUse | None] | None = None
    execution_cost: Callable[[], object] | None = None
    # Optional detached input material shared across recorded query contexts;
    # never solver states, hit claims, or answers imported from another lane.
    collect_preparation: Callable[..., object] | None = None
    use_preparation: Callable[..., object] | None = None
    preparation_updates: Callable[..., object] | None = None

    def __post_init__(self):
        for port in fields(self):
            value = getattr(self, port.name)
            if type(port.default) is bool:
                if type(value) is not bool:
                    raise TypeError(f"a provider's {port.name} capability must be Boolean")
            elif value is not None and not callable(value):
                raise TypeError(f"a provider's {port.name} port must be callable or absent")
        if self.explores_contexts and not self.replay_guided:
            raise TypeError("context exploration requires native replay feedback")

    @classmethod
    def bind(cls, provider):
        """Read the capability surface without invoking any execution port."""
        return cls(**{port.name: getattr(provider, port.name, port.default)
                      for port in fields(cls)})

    def direction_refusal(self):
        """Read current direction-wide state, never infer scope from a code."""
        refusal = None if self.search_refusal is None else self.search_refusal()
        if refusal is not None and type(refusal) is not Refusal:
            raise TypeError("search_refusal must return an exact Refusal or None")
        return refusal

    def request_failure(self, seed, request):
        """Read current request-local state without preparing/retrying work."""
        refusal = None if self.request_refusal is None else self.request_refusal(seed, request)
        if refusal is not None and type(refusal) is not Refusal:
            raise TypeError("request_refusal must return an exact Refusal or None")
        return refusal
