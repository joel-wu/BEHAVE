"""The closed set of semantic audits required by one exact frontend.

An audit plan is a premise of every bounded query.  It is therefore a typed,
versioned value rather than a tuple of strings supplied by a provider.  Unknown names
cannot be ignored and changing the plan while queries are running is detectable by
value equality.
"""

from __future__ import annotations

import enum
import hashlib
import json

from .frozen import Frozen
from .refusal import Refusal, Refused

__all__ = ["AuditKind", "AuditPlan"]


class AuditKind(enum.Enum):
    """The frontend-specific checks understood by the analysis coordinator."""

    GOAL_SET = "goal_set_audit"
    SOURCE = "source_audit"
    STEP = "step_audit"

    def __bool__(self):
        raise TypeError("an audit kind is not a truth value")


class AuditPlan(Frozen):
    """One immutable, canonical set of required frontend audits."""

    VERSION = "analysis-audit-plan/1"
    __slots__ = ("audits",)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("AuditPlan is a closed record and cannot be subclassed")

    def __init__(self, audits=()):
        if type(audits) not in (tuple, list):
            raise TypeError("an audit plan is a materialized tuple or list")
        audits = tuple(audits)
        if any(type(audit) is not AuditKind for audit in audits):
            raise TypeError("an audit plan contains only exact AuditKind values")
        if len(set(audits)) != len(audits):
            raise ValueError("an audit may appear only once in one plan")
        self.audits = tuple(sorted(audits, key=lambda audit: audit.value))
        self._freeze()

    @classmethod
    def all(cls):
        return cls(list(AuditKind))

    @classmethod
    def from_names(cls, names):
        """Decode the exact legacy/wire carrier at its named boundary."""

        if type(names) not in (tuple, list) or any(
                type(name) is not str or not name for name in names):
            raise Refused(Refusal(
                "analysis-audit-plan-malformed",
                "a serialized audit plan is a finite list of exact nonempty names"))
        try:
            return cls([AuditKind(name) for name in names])
        except ValueError as exc:
            unknown = sorted(set(names) - {kind.value for kind in AuditKind})
            kind = ("analysis-audit-plan-unknown" if unknown
                    else "analysis-audit-plan-duplicate")
            why = ("the serialized plan requires an audit this analysis does not "
                   "implement" if unknown else
                   "one required audit appears more than once in the serialized plan")
            raise Refused(Refusal(kind, why, unknown=unknown)) from exc

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"version", "audits"}:
            raise Refused(Refusal(
                "analysis-audit-plan-malformed",
                "an audit-plan record contains exactly its version and audit list"))
        if value["version"] != cls.VERSION:
            raise Refused(Refusal(
                "analysis-audit-plan-version-unsupported",
                "the recorded audit plan uses different prerequisite semantics",
                got=value["version"], supported=cls.VERSION))
        return cls.from_names(value["audits"])

    @property
    def names(self):
        return tuple(audit.value for audit in self.audits)

    def requires(self, audit):
        if type(audit) is not AuditKind:
            raise TypeError("AuditPlan.requires() needs an exact AuditKind")
        return audit in self.audits

    def as_dict(self):
        return {"version": self.VERSION, "audits": list(self.names)}

    def digest(self):
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def __eq__(self, other):
        return type(other) is AuditPlan and other.audits == self.audits

    def __hash__(self):
        return hash((self.VERSION, self.audits))

    def __repr__(self):
        return f"AuditPlan({', '.join(self.names) or 'none'})"
