"""Typed goals and canonical catalogue/domain identities.

A goal's public identifier is a name, not an encoding of its metadata. Its schema
declares purpose, provider, policy and provenance; frontends supply stable occurrence
keys. Immutable manifests bind these declarations to the evidence denominator.
Identity images use finite strict JSON, and caller-facing mappings are detached.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from types import MappingProxyType

from .frozen import Frozen, FrozenMap, freeze

__all__ = [
    "COVERAGE",
    "SAFETY",
    "CatalogueManifest",
    "DomainManifest",
    "Goal",
    "GoalId",
    "GoalSchema",
    "OccurrenceKey",
    "catalogue_of",
    "merge_catalogues",
]

#: The two classes a goal can have. A coverage goal reached is a case
#: exercised; a safety goal reached is a failure found. The class comes from
#: the catalogue that declared the goal rather than from its name.
COVERAGE = "coverage"
SAFETY = "safety"

_CATALOGUE_VERSION = "catalogue-manifest/1"
_DOMAIN_VERSION = "domain-manifest/1"
_CAPABILITY = re.compile(r"^[a-z][a-z0-9-]*$")


def _plain(value, path="catalogue", active=None):
    """Detach one value in the finite, exact JSON domain used by identities."""
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if kind not in (dict, MappingProxyType, FrozenMap, tuple, list):
        raise TypeError(
            f"{path} contains a {kind.__name__}, which has no canonical JSON image")
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} contains a reference cycle")
    active.add(identity)
    try:
        if kind in (dict, MappingProxyType, FrozenMap):
            out = {}
            for key, child in value.items():
                if type(key) is not str:
                    raise TypeError(f"{path} has a non-string key {key!r}")
                out[key] = _plain(child, f"{path}.{key}", active)
            return out
        return [_plain(child, f"{path}[{index}]", active)
                for index, child in enumerate(value)]
    finally:
        active.remove(identity)


def _canonical(value, *, project=True):
    try:
        encoded = json.dumps(
            _plain(value) if project else value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False)
        # A lone surrogate is accepted by ``json.dumps`` but has no UTF-8 image, so
        # accepting it would defer rejection until whichever caller first digests it.
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, OverflowError, RecursionError,
            UnicodeError) as exc:
        raise TypeError(f"catalogue identity is not finite strict JSON: {exc}") \
            from None


def _nonempty_string(value, what):
    if type(value) is not str or not value:
        raise TypeError(f"{what} must be a nonempty exact string")
    return value


def _optional_string(value, what):
    if value is not None:
        _nonempty_string(value, what)
    return value


class GoalId(Frozen):
    """The typed identity of one public goal.

    Mapping-facing APIs continue to expose ``value`` as a string.  Keeping the typed
    record inside manifests prevents goal IDs from being confused with occurrence or
    probe-target identities at evidence boundaries.
    """

    __slots__ = ("value",)

    def __init__(self, value):
        if type(value) is GoalId:
            value = value.value
        self.value = _nonempty_string(value, "goal ID")
        self._freeze()

    def as_dict(self):
        return {"id": self.value}

    def __str__(self):
        return self.value

    def __repr__(self):
        return f"GoalId({self.value!r})"

    def __eq__(self, other):
        return type(other) is GoalId and self.value == other.value

    def __hash__(self):
        return hash((GoalId, self.value))


class GoalSchema(Frozen):
    """All semantic metadata of a goal except its public identifier."""

    __slots__ = ("_canonical", "_digest")

    def __init__(self, cls, *, family=None, provider=None, event=None,
                 provenance=None, policy=None, evidence="slot-record"):
        if type(cls) is not str or cls not in (COVERAGE, SAFETY):
            raise ValueError(
                f"a goal class is exactly {COVERAGE!r} or {SAFETY!r}, got {cls!r}")
        for name, value in (("family", family), ("provider", provider),
                            ("event", event), ("policy", policy)):
            _optional_string(value, f"goal {name}")
        if type(evidence) is not str or evidence not in ("slot-record", "trace"):
            from .refusal import Refusal, Refused

            raise Refused(Refusal(
                "goal-evidence-unknown",
                "a goal's evidence is either written in a slot record or judged over "
                "the trace",
                got=repr(evidence), known=["slot-record", "trace"]))
        if provenance is None:
            provenance = {}
        elif type(provenance) not in (dict, MappingProxyType, FrozenMap):
            raise TypeError(
                "goal provenance must be an exact dict, mapping proxy, or FrozenMap")
        body = {
            "class": cls,
            "evidence": evidence,
            "event": event,
            "family": family,
            "policy": policy,
            "provenance": _plain(provenance, "goal provenance"),
            "provider": provider,
        }
        self._canonical = _canonical(body)
        self._digest = hashlib.sha256(self._canonical.encode()).hexdigest()
        self._freeze()

    def _field(self, name):
        return json.loads(self._canonical)[name]

    @property
    def cls(self):
        return self._field("class")

    @property
    def family(self):
        return self._field("family")

    @property
    def provider(self):
        return self._field("provider")

    @property
    def event(self):
        return self._field("event")

    @property
    def policy(self):
        return self._field("policy")

    @property
    def evidence(self):
        return self._field("evidence")

    @property
    def provenance(self):
        return self._field("provenance")

    def as_dict(self):
        return json.loads(self._canonical)

    def digest(self):
        return self._digest

    def __eq__(self, other):
        return type(other) is GoalSchema and self._canonical == other._canonical

    def __hash__(self):
        return hash((GoalSchema, self._canonical))


class OccurrenceKey(Frozen):
    """One frontend occurrence of a public goal.

    ``provider`` names the frontend namespace.  ``path`` is the elaborated or lowered
    structural path, and ``role`` says what at that path emits the hit.  The core does
    not interpret any component; frontends own their adapters to this common identity.
    """

    __slots__ = ("_canonical", "path", "provider", "role")

    def __init__(self, provider, path, role):
        self.provider = _nonempty_string(provider, "occurrence provider")
        self.role = _nonempty_string(role, "occurrence role")
        if type(path) not in (tuple, list):
            raise TypeError("an occurrence path must be an exact tuple or list")
        path = tuple(path)
        if not path:
            raise ValueError("an occurrence path cannot be empty")
        if any(type(part) not in (str, int) or type(part) is bool
               or (type(part) is str and not part) for part in path):
            raise TypeError(
                "an occurrence path contains only nonempty strings and exact integers")
        self.path = path
        self._canonical = _canonical(self.as_dict())
        self._freeze()

    def as_dict(self):
        return {"provider": self.provider, "path": list(self.path), "role": self.role}

    @property
    def canonical(self):
        return self._canonical

    def __str__(self):
        return f"{self.provider}:{'/'.join(map(str, self.path))}:{self.role}"

    def __eq__(self, other):
        return type(other) is OccurrenceKey and self.canonical == other.canonical

    def __hash__(self):
        return hash((OccurrenceKey, self.canonical))


class Goal(Frozen):
    """One goal a run may reach, and what is known about it before any run.

    ``family`` groups goals by the kind of event they are, and is the
    frontend's vocabulary: a branch outcome and a division by zero are
    different families here, and an RTL toggle would be another. ``provider``
    is what enumerated it, so that a report can say which of several goal
    sources a goal came from and a catalogue can be checked against the union
    of its providers.
    """

    __slots__ = ("goal_id", "id", "schema")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Goal is a closed catalogue record and cannot be subclassed")

    def __init__(self, id, cls, family=None, provider=None, event=None,
                 provenance=None, policy=None, evidence="slot-record"):
        self.goal_id = GoalId(id)
        self.id = self.goal_id.value
        self.schema = GoalSchema(
            cls, family=family, provider=provider, event=event,
            provenance=provenance, policy=policy, evidence=evidence)
        self._freeze()

    @property
    def cls(self):
        return self.schema.cls

    @property
    def family(self):
        return self.schema.family

    @property
    def provider(self):
        return self.schema.provider

    @property
    def event(self):
        return self.schema.event

    @property
    def policy(self):
        return self.schema.policy

    @property
    def evidence(self):
        return self.schema.evidence

    @property
    def provenance(self):
        # Compatibility image.  The manifest identity itself stores strict JSON.
        return tuple(sorted((key, freeze(value))
                            for key, value in self.schema.provenance.items()))

    @property
    def identity(self):
        """Public identifier and complete semantic schema used by equality/hashing."""
        return (self.goal_id, self.schema)

    def where(self, key, default=None):
        """Read one provenance entry; its meaning belongs to the declaring frontend."""
        for name, value in self.provenance:
            if name == key:
                return value
        return default

    def __repr__(self):
        return f"Goal({self.id}, {self.cls})"

    def __eq__(self, other):
        return type(other) is Goal and self.identity == other.identity

    def __hash__(self):
        return hash(self.identity)

    def conflict_with(self, other):
        """Which fields disagree, for two records claiming the same identifier."""
        fields = ("cls", "family", "provider", "event", "policy", "provenance",
                  "evidence")
        return {name: (getattr(self, name), getattr(other, name))
                for name in fields
                if getattr(self, name) != getattr(other, name)}

    def as_dict(self):
        """The stable public row retained by existing reports."""
        return {
            "id": self.id,
            "cls": self.cls,
            "family": self.family,
            "provider": self.provider,
            "event": self.event,
            "policy": self.policy,
            "evidence": self.evidence,
            "provenance": [[key, value] for key, value
                           in sorted(self.schema.provenance.items())],
        }


class CatalogueManifest(Frozen, Mapping):
    """The immutable declaration denominator and its occurrence reconciliation.

    It is also a read-only ``Mapping[str, Goal]`` so migrated core callers do not need
    a second catalogue view.  The mapping order, serialized order and digest order are
    all the lexicographic goal-ID order.
    """

    __slots__ = ("_canonical", "_digest", "_entries", "_index", "_occurrences")

    def __init__(self, goals=(), *, occurrences=(), allow_legacy=False):
        from .refusal import Refusal, Refused

        if type(goals) is CatalogueManifest:
            if type(occurrences) not in (
                    dict, MappingProxyType, FrozenMap, tuple, list):
                raise TypeError(
                    "catalogue occurrences are an exact mapping, tuple, or list")
            if len(occurrences) != 0:
                raise ValueError(
                    "occurrences cannot be replaced while copying a catalogue manifest")
            self._entries = goals._entries
            self._index = goals._index
            self._occurrences = goals._occurrences
            self._canonical = goals._canonical
            self._digest = goals._digest
            self._freeze()
            return
        if type(goals) in (dict, MappingProxyType, FrozenMap):
            pairs = tuple(goals.items())
        else:
            if type(goals) not in (tuple, list):
                raise TypeError(
                    "a catalogue is an exact mapping, tuple, or list of Goal records")
            if any(type(goal) is not Goal for goal in goals):
                raise TypeError("a catalogue sequence contains a non-Goal value")
            pairs = tuple((goal.id, goal) for goal in goals)
        entries = {}
        for key, value in pairs:
            if type(key) is GoalId:
                key = key.value
            _nonempty_string(key, "catalogue key")
            if type(value) is Goal:
                goal = value
                if key != goal.id:
                    raise Refused(Refusal(
                        "catalogue-key-is-not-the-goal-id",
                        "a catalogue key and the Goal stored under it must be the same "
                        "exact identifier",
                        key=key, goal=goal.id))
            elif allow_legacy:
                goal = Goal(key, value)
            else:
                raise Refused(Refusal(
                    "catalogue-legacy-mapping",
                    "this boundary requires complete Goal records; a bare class loses "
                    "the schema and policy that define the denominator",
                    goal=key, got=type(value).__name__))
            prior = entries.get(key)
            if prior is not None:
                if prior == goal:
                    raise Refused(Refusal(
                        "catalogue-duplicate-goal",
                        "one goal identifier is declared twice",
                        goal=key))
                raise Refused(Refusal(
                    "catalogue-conflicting-goal",
                    "two declarations reuse one goal identifier with different schema",
                    goal=key,
                    conflict={name: [_plain(left), _plain(right)]
                              for name, (left, right)
                              in prior.conflict_with(goal).items()}))
            entries[key] = goal

        occurrence_entries = {}
        if type(occurrences) in (dict, MappingProxyType, FrozenMap):
            raw_occurrences = tuple(occurrences.items())
        elif type(occurrences) not in (tuple, list):
            raise TypeError(
                "catalogue occurrences are an exact mapping, tuple, or list")
        else:
            raw_occurrences = tuple(occurrences)
        for pair in raw_occurrences:
            if type(pair) not in (tuple, list) or len(pair) != 2:
                raise TypeError("a catalogue occurrence is an exact key/goal pair")
            key, goal_id = pair
            if type(key) is not OccurrenceKey:
                raise TypeError(
                    "a catalogue occurrence must use the exact OccurrenceKey type")
            if type(goal_id) is GoalId:
                goal_id = goal_id.value
            _nonempty_string(goal_id, "occurrence goal ID")
            if goal_id not in entries:
                raise Refused(Refusal(
                    "catalogue-occurrence-unknown-goal",
                    "an occurrence is attached to a goal outside the declaration",
                    occurrence=str(key), goal=goal_id))
            if key in occurrence_entries:
                prior = occurrence_entries[key]
                kind = ("catalogue-duplicate-occurrence" if prior == goal_id
                        else "catalogue-conflicting-occurrence")
                raise Refused(Refusal(
                    kind,
                    "one occurrence key is declared more than once",
                    occurrence=str(key), first=prior, second=goal_id))
            occurrence_entries[key] = goal_id

        self._entries = tuple(sorted(entries.items()))
        self._index = MappingProxyType(dict(self._entries))
        self._occurrences = tuple(sorted(
            occurrence_entries.items(), key=lambda pair: pair[0].canonical))
        body = {
            "version": _CATALOGUE_VERSION,
            "goals": [goal.as_dict() for _key, goal in self._entries],
            "occurrences": [
                {"key": key.as_dict(), "goal": goal}
                for key, goal in self._occurrences
            ],
        }
        # Goal/OccurrenceKey own validated canonical values; this locally built
        # image needs encoding, not another recursive copy of every occurrence.
        self._canonical = _canonical(body, project=False)
        self._digest = hashlib.sha256(self._canonical.encode()).hexdigest()
        self._freeze()

    def __reduce__(self):
        """Rebuild through the canonical public image at process boundaries."""

        return (CatalogueManifest.from_dict, (self.as_dict(),))

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a catalogue manifest image must be an exact mapping")
        if set(value) != {"version", "goals", "occurrences"}:
            raise ValueError("a catalogue manifest image has unknown or missing fields")
        if type(value["version"]) is not str \
                or value["version"] != _CATALOGUE_VERSION:
            raise ValueError("a catalogue manifest image has an unsupported version")
        if type(value["goals"]) is not list or type(value["occurrences"]) is not list:
            raise TypeError("catalogue goals and occurrences must be exact lists")
        goals = []
        goal_fields = {
            "id", "cls", "family", "provider", "event", "policy", "evidence",
            "provenance",
        }
        for row in value["goals"]:
            if type(row) is not dict or set(row) != goal_fields:
                raise ValueError("a catalogue goal image has unknown or missing fields")
            provenance = row["provenance"]
            if type(provenance) is not list or any(
                    type(pair) is not list or len(pair) != 2
                    or type(pair[0]) is not str for pair in provenance):
                raise TypeError("goal provenance must be a list of string-keyed pairs")
            if len({pair[0] for pair in provenance}) != len(provenance):
                raise ValueError("goal provenance contains a duplicate key")
            goals.append(Goal(
                row["id"], row["cls"], family=row["family"],
                provider=row["provider"], event=row["event"],
                policy=row["policy"], evidence=row["evidence"],
                provenance=dict(provenance)))
        occurrence_rows = []
        for row in value["occurrences"]:
            if type(row) is not dict or set(row) != {"key", "goal"}:
                raise ValueError(
                    "a catalogue occurrence image has unknown or missing fields")
            key = row["key"]
            if type(key) is not dict or set(key) != {"provider", "path", "role"}:
                raise ValueError("an occurrence key image has unknown or missing fields")
            if type(key["path"]) is not list:
                raise TypeError("an occurrence path image must be an exact list")
            occurrence_rows.append((OccurrenceKey(
                key["provider"], key["path"], key["role"]), row["goal"]))
        manifest = cls(goals, occurrences=occurrence_rows)
        if manifest.as_dict() != _plain(value):
            raise ValueError("a catalogue manifest image is not canonical")
        return manifest

    @classmethod
    def from_goals_image(cls, rows):
        """Explicit compatibility parser for the pre-manifest report list."""
        if type(rows) is not list:
            raise TypeError("a legacy catalogue image must be an exact list")
        return cls.from_dict({
            "version": _CATALOGUE_VERSION,
            "goals": rows,
            "occurrences": [],
        })

    def __getitem__(self, key):
        if type(key) is GoalId:
            key = key.value
        elif type(key) is not str:
            raise KeyError(key)
        return self._index[key]

    def __iter__(self):
        return iter(goal_id for goal_id, _goal in self._entries)

    def __len__(self):
        return len(self._entries)

    def __eq__(self, other):
        return type(other) is CatalogueManifest and self._canonical == other._canonical

    def __hash__(self):
        return hash((CatalogueManifest, self._canonical))

    @property
    def canonical(self):
        return self._canonical

    def digest(self):
        """The complete SHA-256 over the canonical manifest, never a display prefix."""
        return self._digest

    @property
    def occurrences(self):
        return self._occurrences

    def as_dict(self):
        return json.loads(self._canonical)

    def goals_image(self):
        """The legacy report list, now derived from the canonical manifest."""
        return [goal.as_dict() for _goal_id, goal in self._entries]

    def reconcile(self, supplied, capability, *, exhaustive=True):
        """Validate one producer/encoder/guidance view against this declaration.

        Concrete and bounded capabilities use ``exhaustive=True``.  Guidance uses a
        subset and therefore sets it false; unknown and stale goals still fail closed.
        """
        from .refusal import Refusal, Refused

        capability = _nonempty_string(capability, "catalogue capability")
        if _CAPABILITY.fullmatch(capability) is None:
            raise ValueError("a catalogue capability must be a stable kebab-case name")
        got = CatalogueManifest(supplied, allow_legacy=False)
        declared_ids, supplied_ids = set(self), set(got)
        unknown = sorted(supplied_ids - declared_ids)
        if unknown:
            raise Refused(Refusal(
                f"catalogue-{capability}-unknown-goal",
                "a capability names goals outside the declaration denominator",
                goals=unknown))
        stale = sorted(goal for goal in supplied_ids
                       if self[goal].schema != got[goal].schema)
        if stale:
            raise Refused(Refusal(
                f"catalogue-{capability}-stale-goal",
                "a capability reuses declared IDs with different goal schema",
                goals=stale))
        missing = sorted(declared_ids - supplied_ids)
        if exhaustive and missing:
            raise Refused(Refusal(
                f"catalogue-{capability}-missing-goal",
                "an exhaustive capability omits declared goals",
                goals=missing))
        return tuple(sorted(supplied_ids))

    def reconcile_occurrences(self, supplied, capability, *, exhaustive=True):
        """Validate the occurrence-to-public-goal relation of one capability."""
        from .refusal import Refusal, Refused

        capability = _nonempty_string(capability, "occurrence capability")
        if _CAPABILITY.fullmatch(capability) is None:
            raise ValueError("an occurrence capability must be stable kebab-case")
        # Parse without rebuilding the declaration (which deliberately forbids replacing
        # occurrences on a copied manifest).
        parsed = {}
        if type(supplied) in (dict, MappingProxyType, FrozenMap):
            supplied = tuple(supplied.items())
        elif type(supplied) not in (tuple, list):
            raise TypeError(
                "occurrence evidence is an exact mapping, tuple, or list")
        else:
            supplied = tuple(supplied)
        for pair in supplied:
            if type(pair) not in (tuple, list) or len(pair) != 2:
                raise TypeError("occurrence evidence contains a malformed pair")
            key, goal = pair
            if type(key) is not OccurrenceKey:
                raise TypeError("an occurrence capability uses exact OccurrenceKey values")
            goal = goal.value if type(goal) is GoalId else goal
            _nonempty_string(goal, "occurrence goal ID")
            if goal not in self:
                raise Refused(Refusal(
                    f"catalogue-{capability}-unknown-goal",
                    "an occurrence capability names a goal outside the declaration",
                    occurrence=str(key), goal=goal))
            if key in parsed:
                raise Refused(Refusal(
                    f"catalogue-{capability}-duplicate-occurrence",
                    "an occurrence capability reports one occurrence more than once",
                    occurrence=str(key)))
            parsed[key] = goal
        expected = dict(self._occurrences)
        unknown = sorted(set(parsed) - set(expected), key=lambda key: key.canonical)
        if unknown:
            raise Refused(Refusal(
                f"catalogue-{capability}-unknown-occurrence",
                "a capability reports an occurrence outside the manifest",
                occurrences=[str(key) for key in unknown]))
        stale = sorted((key for key in parsed if parsed[key] != expected[key]),
                       key=lambda key: key.canonical)
        if stale:
            raise Refused(Refusal(
                f"catalogue-{capability}-stale-occurrence",
                "a capability attaches an occurrence to a different public goal",
                occurrences=[str(key) for key in stale]))
        missing = sorted(set(expected) - set(parsed), key=lambda key: key.canonical)
        if exhaustive and missing:
            raise Refused(Refusal(
                f"catalogue-{capability}-missing-occurrence",
                "an exhaustive capability omits declared occurrences",
                occurrences=[str(key) for key in missing]))
        return tuple(sorted(parsed, key=lambda key: key.canonical))


class DomainManifest(Frozen):
    """One trace domain bound to the exact catalogue that its evidence classifies."""

    __slots__ = ("_canonical", "_digest", "catalogue", "domain")

    def __init__(self, domain, catalogue):
        from .domain import TraceDomain

        if type(domain) is not TraceDomain:
            raise TypeError("a DomainManifest requires the exact TraceDomain record")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError(
                "a DomainManifest requires the exact CatalogueManifest record")
        self.domain = domain
        self.catalogue = catalogue
        body = {
            "version": _DOMAIN_VERSION,
            "domain": domain.as_dict(),
            "domain_digest": domain.digest(),
            "catalogue": catalogue.as_dict(),
            "catalogue_digest": catalogue.digest(),
        }
        self._canonical = _canonical(body)
        self._digest = hashlib.sha256(self._canonical.encode()).hexdigest()
        self._freeze()

    @classmethod
    def from_dict(cls, value):
        from .domain import TraceDomain

        fields = {"version", "domain", "domain_digest", "catalogue",
                  "catalogue_digest"}
        if type(value) is not dict or set(value) != fields:
            raise ValueError("a domain manifest image has unknown or missing fields")
        if type(value["version"]) is not str \
                or value["version"] != _DOMAIN_VERSION:
            raise ValueError("a domain manifest image has an unsupported version")
        if type(value["domain_digest"]) is not str \
                or type(value["catalogue_digest"]) is not str:
            raise TypeError("domain-manifest digests must be exact strings")
        domain_row = value["domain"]
        if type(domain_row) is not dict:
            raise TypeError("a domain manifest's domain must be a mapping")
        expected_domain_fields = {
            "bound", "contract", "initial", "schema", "frontend", "policy",
            "artifact", "task_contract",
        }
        if set(domain_row) != expected_domain_fields:
            raise ValueError("a trace-domain image has unknown or missing fields")
        domain = TraceDomain(**domain_row)
        catalogue = CatalogueManifest.from_dict(value["catalogue"])
        if value["domain_digest"] != domain.digest():
            raise ValueError("a domain manifest carries the wrong domain digest")
        if value["catalogue_digest"] != catalogue.digest():
            raise ValueError("a domain manifest carries the wrong catalogue digest")
        return cls(domain, catalogue)

    @property
    def canonical(self):
        return self._canonical

    def digest(self):
        return self._digest

    def as_dict(self):
        return json.loads(self._canonical)

    def __eq__(self, other):
        return type(other) is DomainManifest and self._canonical == other._canonical

    def __hash__(self):
        return hash((DomainManifest, self._canonical))


def catalogue_subset(catalogue, goal_ids):
    """One provider-owned slice, retaining the declaration's occurrences."""

    goal_ids = frozenset(goal_ids)
    return CatalogueManifest(
        tuple(catalogue[goal] for goal in sorted(goal_ids)),
        occurrences=tuple(
            (occurrence, goal) for occurrence, goal in catalogue.occurrences
            if goal in goal_ids),
    )


def catalogue_of(goals, allow_legacy=False):
    """Return the one canonical manifest for ``goals``.

    ``allow_legacy`` is an explicit deserialization-only compatibility door for old
    ``{id: class}`` maps.  Production callers must never acquire that weaker schema by
    omission.  A manifest is returned unchanged, so no caller can silently discard its
    occurrence relation.
    """
    if type(goals) is CatalogueManifest:
        return goals
    return CatalogueManifest(goals, allow_legacy=allow_legacy)


def merge_catalogues(*components):
    """Return the exact disjoint union of typed catalogue components.

    Frontends and task monitors enumerate different parts of one public denominator.
    Their declarations are merged once, before generation.  Reusing an ID or an
    occurrence key is refused even when the two rows happen to agree: two producers for
    one declaration would leave ownership ambiguous and make a later omission
    impossible to attribute.
    """

    if not components:
        return CatalogueManifest(())
    if any(type(component) is not CatalogueManifest for component in components):
        raise TypeError(
            "catalogue components must be exact CatalogueManifest records")
    goals = tuple(goal for component in components
                  for goal in component.values())
    occurrences = tuple(pair for component in components
                        for pair in component.occurrences)
    return CatalogueManifest(goals, occurrences=occurrences)
