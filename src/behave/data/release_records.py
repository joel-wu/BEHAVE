"""Explicit admission policy and dispositions, separate from simulator scores.

These records live in trusted supervisor storage. HumanApproval is an operator
attestation, not a signature or authentication mechanism; agents are never given
an API or store capability that can issue it.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from behave.core.artifacts import BlobRef
from behave.core.records import (
    RecordError,
    array,
    canonical_json,
    fields,
    metadata,
    parse_json,
    text,
    thaw_json,
    version,
)

from .corpus import CorpusGuard


def _canonical(payload, result):
    if payload != result.to_bytes():
        raise RecordError("record-not-canonical", "release records must be canonical")
    return result


@dataclass(frozen=True, slots=True)
class ScopeReviewPolicy:
    """Require a pre-author review with this guidance and model policy."""

    guidance: BlobRef
    model_policy: MappingProxyType

    def __post_init__(self):
        if type(self.guidance) is not BlobRef:
            raise TypeError("scope review policy requires pinned guidance")
        object.__setattr__(self, "model_policy", metadata(self.model_policy))

    def as_dict(self):
        return {"guidance": self.guidance.as_dict(), "model_policy": thaw_json(self.model_policy)}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"guidance", "model_policy"})
        return cls(BlobRef.from_dict(row["guidance"]), row["model_policy"])


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    name: str
    mode: str
    guidance: BlobRef
    judge_policy: BlobRef
    review_model_policy: MappingProxyType
    require_license_metadata: bool = True
    scope_review: ScopeReviewPolicy | None = None
    corpus: CorpusGuard | None = None

    def __post_init__(self):
        text(self.name, "release policy name/version")
        if self.mode not in {"reviewed-benchmark", "acquired-training"}:
            raise ValueError("release mode must distinguish reviewed benchmark from acquired training")
        if type(self.guidance) is not BlobRef or type(self.judge_policy) is not BlobRef:
            raise TypeError("release policy requires pinned review guidance and judge policy")
        object.__setattr__(self, "review_model_policy", metadata(self.review_model_policy))
        if type(self.require_license_metadata) is not bool:
            raise TypeError("license metadata requirement must be Boolean")
        if self.scope_review is not None and type(self.scope_review) is not ScopeReviewPolicy:
            raise TypeError("scope review requirement needs a ScopeReviewPolicy")
        if self.corpus is not None:
            if type(self.corpus) is not CorpusGuard:
                raise TypeError("corpus requirement needs a CorpusGuard")
            if self.mode == "acquired-training" and self.corpus.partition != "train-acquired":
                raise ValueError("acquired training may publish only to train-acquired")

    def to_bytes(self):
        row = {"schema": "behave.release-policy/1", "name": self.name, "mode": self.mode,
            "guidance": self.guidance.as_dict(), "judge_policy": self.judge_policy.as_dict(),
            "review_model_policy": thaw_json(self.review_model_policy),
            "require_license_metadata": self.require_license_metadata}
        if self.scope_review is not None:
            row.update(schema="behave.release-policy/2", scope_review=self.scope_review.as_dict())
        if self.corpus is not None:
            row.update(schema="behave.release-policy/3", corpus=self.corpus.as_dict(),
                       scope_review=None if self.scope_review is None else self.scope_review.as_dict())
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload):
        value = parse_json(payload)
        guarded = value.get("schema") == "behave.release-policy/3"
        extended = guarded or value.get("schema") == "behave.release-policy/2"
        row = fields(value, {"schema", "name", "mode", "guidance", "judge_policy",
                            "review_model_policy", "require_license_metadata"}
                     | ({"scope_review"} if extended else set()) | ({"corpus"} if guarded else set()))
        version(row["schema"], f"behave.release-policy/{3 if guarded else 2 if extended else 1}")
        return _canonical(payload, cls(row["name"], row["mode"], BlobRef.from_dict(row["guidance"]),
            BlobRef.from_dict(row["judge_policy"]), row["review_model_policy"], row["require_license_metadata"],
            ScopeReviewPolicy.from_dict(row["scope_review"]) if extended and row["scope_review"] is not None else None,
            CorpusGuard.from_dict(row["corpus"]) if guarded else None))


@dataclass(frozen=True, slots=True)
class ReviewPackage:
    synthesis_head: BlobRef
    source_review_head: BlobRef | None
    construction_prefix: BlobRef | None = None

    def __post_init__(self):
        if type(self.synthesis_head) is not BlobRef:
            raise TypeError("review package requires a sealed synthesis head")
        if self.source_review_head is not None and type(self.source_review_head) is not BlobRef:
            raise TypeError("source review head requires BlobRef or None")
        if self.construction_prefix is not None and type(self.construction_prefix) is not BlobRef:
            raise TypeError("construction history requires a sealed prefix")

    def to_bytes(self):
        row = {"schema": "behave.review-package/1", "synthesis_head": self.synthesis_head.as_dict(),
            "source_review_head": None if self.source_review_head is None else self.source_review_head.as_dict()}
        if self.construction_prefix is not None:
            row.update(schema="behave.review-package/2", construction_prefix=self.construction_prefix.as_dict())
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload):
        value = parse_json(payload)
        extended = value.get("schema") == "behave.review-package/2"
        row = fields(value, {"schema", "synthesis_head", "source_review_head"}
                     | ({"construction_prefix"} if extended else set()))
        version(row["schema"], f"behave.review-package/{2 if extended else 1}")
        return _canonical(payload, cls(BlobRef.from_dict(row["synthesis_head"]),
            None if row["source_review_head"] is None else BlobRef.from_dict(row["source_review_head"]),
            BlobRef.from_dict(row["construction_prefix"]) if extended else None))


@dataclass(frozen=True, slots=True)
class ReleaseRequest:
    """Explicit operator selection; never infer approval or include a directory."""

    release_id: str
    policy: BlobRef
    packages: tuple[BlobRef, ...]
    approvals: tuple[BlobRef, ...] = ()

    def __post_init__(self):
        text(self.release_id, "release id")
        if type(self.policy) is not BlobRef:
            raise TypeError("publication needs a pinned policy")
        for name in ("packages", "approvals"):
            refs = getattr(self, name)
            if type(refs) is not tuple or any(type(ref) is not BlobRef for ref in refs):
                raise TypeError("publication references must be tuples of BlobRef")
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate publication input")
            object.__setattr__(self, name, tuple(sorted(refs, key=lambda ref: ref.sha256)))
        if not self.packages or len(self.approvals) > len(self.packages):
            raise ValueError("publication needs explicit packages and at most one approval per package")

    def as_dict(self):
        return {"schema": "behave.release-request/1", "release_id": self.release_id,
                "policy": self.policy.as_dict(), "packages": [ref.as_dict() for ref in self.packages],
                "approvals": [ref.as_dict() for ref in self.approvals]}

    def to_bytes(self):
        return canonical_json(self.as_dict())

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "release_id", "policy", "packages", "approvals"})
        version(row["schema"], "behave.release-request/1")
        return _canonical(payload, cls(row["release_id"], BlobRef.from_dict(row["policy"]),
            *(tuple(BlobRef.from_dict(ref) for ref in array(row[key])) for key in ("packages", "approvals"))))


@dataclass(frozen=True, slots=True)
class HumanApproval:
    package: BlobRef
    policy: BlobRef
    reviewer: str
    rationale: str

    def __post_init__(self):
        if type(self.package) is not BlobRef or type(self.policy) is not BlobRef:
            raise TypeError("approval requires an exact package and policy")
        text(self.reviewer, "operator identity")
        text(self.rationale, "approval rationale")

    def to_bytes(self):
        return canonical_json({"schema": "behave.human-approval/1", "package": self.package.as_dict(),
            "policy": self.policy.as_dict(), "reviewer": self.reviewer, "rationale": self.rationale})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "package", "policy", "reviewer", "rationale"})
        version(row["schema"], "behave.human-approval/1")
        return _canonical(payload, cls(BlobRef.from_dict(row["package"]), BlobRef.from_dict(row["policy"]),
                                       row["reviewer"], row["rationale"]))


@dataclass(frozen=True, slots=True)
class ReleaseDisposition:
    package: BlobRef
    policy: BlobRef
    source: BlobRef
    task_id: str
    draft: BlobRef | None
    outcome: str
    reasons: tuple[str, ...]
    approval: BlobRef | None = None

    def __post_init__(self):
        if any(type(r) is not BlobRef for r in (self.package, self.policy, self.source)):
            raise TypeError("disposition requires pinned inputs")
        if any(r is not None and type(r) is not BlobRef for r in (self.draft, self.approval)):
            raise TypeError("draft/approval requires BlobRef or None")
        text(self.task_id, "task id")
        if self.outcome not in {"include", "pending"}:
            raise ValueError("automatic admission includes or defers; it never silently culls a source")
        if type(self.reasons) is not tuple or not self.reasons:
            raise ValueError("disposition needs explicit reasons")
        for reason in self.reasons:
            text(reason, "disposition reason")
        if self.outcome == "include" and self.draft is None:
            raise ValueError("inclusion requires a draft")

    def to_bytes(self):
        return canonical_json({"schema": "behave.release-disposition/1", "package": self.package.as_dict(),
            "policy": self.policy.as_dict(), "source": self.source.as_dict(), "task_id": self.task_id,
            "draft": None if self.draft is None else self.draft.as_dict(), "outcome": self.outcome,
            "reasons": list(self.reasons), "approval": None if self.approval is None else self.approval.as_dict()})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "package", "policy", "source", "task_id", "draft",
                                           "outcome", "reasons", "approval"})
        version(row["schema"], "behave.release-disposition/1")
        return _canonical(payload, cls(BlobRef.from_dict(row["package"]), BlobRef.from_dict(row["policy"]),
            BlobRef.from_dict(row["source"]), row["task_id"], None if row["draft"] is None else BlobRef.from_dict(row["draft"]),
            row["outcome"], tuple(array(row["reasons"])),
            None if row["approval"] is None else BlobRef.from_dict(row["approval"])))
