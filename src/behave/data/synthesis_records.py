"""Pinned synthesis plan and review-package outcome; no publication authority."""

from __future__ import annotations

from dataclasses import dataclass, replace

from behave.agent.attempt import AttemptSpec
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, version

from .role_policy import ProcessRoles, RtlBuildPolicy


def coordinator_for(plan: SynthesisPlan) -> str:
    """Wire identity shared by execution and read-only admission."""
    if plan.source_review is not None:
        return "behave.synthesis/7"
    if plan.rtl is not None:
        return "behave.synthesis/5"
    if plan.preparation is not None:
        return "behave.synthesis/4"
    if plan.processes is not None:
        return "behave.synthesis/3"
    return "behave.synthesis/1"


def role_attempt(plan: SynthesisPlan, revision: int, role: str) -> AttemptSpec:
    template = plan.source_review.reviewer if role == "source_review" else getattr(plan, role)
    return replace(template, attempt_id=f"{plan.run_id}:r{revision}:{role}:{template.attempt_id}")


@dataclass(frozen=True, slots=True)
class SourceReviewPlan:
    guidance: BlobRef
    reviewer: AttemptSpec

    def __post_init__(self) -> None:
        if type(self.guidance) is not BlobRef or type(self.reviewer) is not AttemptSpec:
            raise TypeError("source review requires pinned guidance and an AttemptSpec")
        if self.reviewer.role != "source_reviewer":
            raise ValueError("source review requires a source_reviewer role")

    def as_dict(self) -> dict:
        return {"guidance": self.guidance.as_dict(), "reviewer": self.reviewer.as_dict()}

    @classmethod
    def from_dict(cls, value: object) -> SourceReviewPlan:
        row = fields(value, {"guidance", "reviewer"})
        return cls(BlobRef.from_dict(row["guidance"]), AttemptSpec.from_dict(row["reviewer"]))


@dataclass(frozen=True, slots=True)
class SynthesisPlan:
    run_id: str
    task_id: str
    scope: BlobRef
    judge_policy: BlobRef
    author: AttemptSpec
    reader: AttemptSpec
    max_revisions: int = defaults.SYN_TASK_REVISIONS
    source_review: SourceReviewPlan | None = None
    processes: ProcessRoles | None = None
    preparation: BlobRef | None = None
    rtl: RtlBuildPolicy | None = None
    construction_experience: BlobRef | None = None

    def __post_init__(self) -> None:
        text(self.run_id, "run id")
        text(self.task_id, "task id")
        if type(self.scope) is not BlobRef or type(self.judge_policy) is not BlobRef:
            raise TypeError("synthesis requires pinned scope and judge policy")
        if self.preparation is not None and type(self.preparation) is not BlobRef:
            raise TypeError("scope preparation requires a sealed history reference")
        if self.construction_experience is not None and type(self.construction_experience) is not BlobRef:
            raise TypeError("construction experience requires a text reference")
        if self.rtl is not None and (type(self.rtl) is not RtlBuildPolicy or self.processes is None):
            raise TypeError("auxiliary RTL requires a policy and process-backed synthesis")
        if type(self.author) is not AttemptSpec or type(self.reader) is not AttemptSpec:
            raise TypeError("synthesis roles require AttemptSpec")
        if self.author.role != "task_author" or self.reader.role != "spec_reader":
            raise ValueError("synthesis requires distinct author and spec-reader roles")
        if type(self.max_revisions) is not int or self.max_revisions < 1:
            raise ValueError("max_revisions must be positive")
        if self.source_review is not None and type(self.source_review) is not SourceReviewPlan:
            raise TypeError("source review requires SourceReviewPlan or None")
        if self.processes is not None:
            if type(self.processes) is not ProcessRoles:
                raise TypeError("process execution requires ProcessRoles")
            self.processes.author.check_attempt(self.author)
            self.processes.reader.check_attempt(self.reader)
            if (self.source_review is None) != (self.processes.source_review is None):
                raise ValueError("source review needs both its plan and execution policy")
            if self.source_review is not None:
                self.processes.source_review.check_attempt(self.source_review.reviewer)

    def to_bytes(self) -> bytes:
        row = {"schema": "behave.synthesis-plan/1", "run_id": self.run_id,
            "task_id": self.task_id, "scope": self.scope.as_dict(), "judge_policy": self.judge_policy.as_dict(),
            "author": self.author.as_dict(), "reader": self.reader.as_dict(), "max_revisions": self.max_revisions}
        if self.source_review is not None:
            row.update(schema="behave.synthesis-plan/2", source_review=self.source_review.as_dict())
        if self.processes is not None:
            row.update(schema="behave.synthesis-plan/3", processes=self.processes.as_dict(),
                       source_review=None if self.source_review is None else self.source_review.as_dict())
        if self.preparation is not None:
            row.update(schema="behave.synthesis-plan/4", preparation=self.preparation.as_dict(),
                       processes=None if self.processes is None else self.processes.as_dict(),
                       source_review=None if self.source_review is None else self.source_review.as_dict())
        if self.rtl is not None:
            row.update(schema="behave.synthesis-plan/5", rtl=self.rtl.as_dict(),
                       preparation=None if self.preparation is None else self.preparation.as_dict())
        if self.construction_experience is not None:
            row.update(schema="behave.synthesis-plan/6", construction_experience=self.construction_experience.as_dict(),
                       rtl=None if self.rtl is None else self.rtl.as_dict(),
                       preparation=None if self.preparation is None else self.preparation.as_dict(),
                       processes=None if self.processes is None else self.processes.as_dict(),
                       source_review=None if self.source_review is None else self.source_review.as_dict())
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload: bytes) -> SynthesisPlan:
        value = parse_json(payload)
        experienced = value.get("schema") == "behave.synthesis-plan/6"
        rtl = experienced or value.get("schema") == "behave.synthesis-plan/5"
        prepared = rtl or value.get("schema") == "behave.synthesis-plan/4"
        process = prepared or value.get("schema") == "behave.synthesis-plan/3"
        extended = process or value.get("schema") == "behave.synthesis-plan/2"
        row = fields(value, {"schema", "run_id", "task_id", "scope", "judge_policy", "author",
                             "reader", "max_revisions"} | ({"source_review"} if extended else set())
                     | ({"processes"} if process else set()) | ({"preparation"} if prepared else set())
                     | ({"rtl"} if rtl else set()) | ({"construction_experience"} if experienced else set()))
        version(row["schema"], f"behave.synthesis-plan/{6 if experienced else 5 if rtl else 4 if prepared else 3 if process else 2 if extended else 1}")
        result = cls(row["run_id"], row["task_id"], BlobRef.from_dict(row["scope"]),
                     BlobRef.from_dict(row["judge_policy"]), AttemptSpec.from_dict(row["author"]),
                     AttemptSpec.from_dict(row["reader"]), row["max_revisions"],
                     SourceReviewPlan.from_dict(row["source_review"]) if row.get("source_review") is not None else None,
                     ProcessRoles.from_dict(row["processes"]) if row.get("processes") is not None else None,
                     BlobRef.from_dict(row["preparation"]) if row.get("preparation") is not None else None,
                     RtlBuildPolicy.from_dict(row["rtl"]) if row.get("rtl") is not None else None,
                     BlobRef.from_dict(row["construction_experience"]) if experienced else None)
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "synthesis plan must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class ProcessReceipt:
    supervision: BlobRef
    provider: BlobRef | None = None
    development: BlobRef | None = None

    def __post_init__(self) -> None:
        if type(self.supervision) is not BlobRef:
            raise TypeError("process receipt requires its supervision head")
        for head in (self.provider, self.development):
            if head is not None and type(head) is not BlobRef:
                raise TypeError("auxiliary head requires BlobRef or None")

    def as_dict(self) -> dict:
        return {key: None if value is None else value.as_dict()
                for key, value in (("supervision", self.supervision), ("provider", self.provider),
                                   ("development", self.development))}

    @classmethod
    def from_dict(cls, value: object) -> ProcessReceipt:
        return cls(**{key: None if ref is None else BlobRef.from_dict(ref)
                      for key, ref in fields(value, {"supervision", "provider", "development"}).items()})


@dataclass(frozen=True, slots=True)
class PhaseReceipt:
    revision: int
    role: str
    head: BlobRef | None
    process: ProcessReceipt | None = None

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("invalid synthesis revision")
        if self.role not in {"author", "reader", "check", "source_review", "scope_author", "scope_reviewer",
                             "rtl_author", "rtl_check", "source_selector", "source_discoverer", "source_acquirer"}:
            raise ValueError("unknown synthesis phase")
        if self.head is not None and type(self.head) is not BlobRef:
            raise TypeError("phase head requires BlobRef or None")
        if self.process is not None and (type(self.process) is not ProcessReceipt or self.role in {"check", "rtl_check"}):
            raise TypeError("only construction roles carry process receipts")

    def as_dict(self) -> dict:
        row = {"revision": self.revision, "role": self.role,
                "head": None if self.head is None else self.head.as_dict()}
        if self.process is not None:
            row["process"] = self.process.as_dict()
        return row

    @classmethod
    def from_dict(cls, value: object) -> PhaseReceipt:
        process = type(value) is dict and "process" in value
        row = fields(value, {"revision", "role", "head"} | ({"process"} if process else set()))
        return cls(row["revision"], row["role"], None if row["head"] is None else BlobRef.from_dict(row["head"]),
                   ProcessReceipt.from_dict(row["process"]) if "process" in row else None)


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    plan: BlobRef
    status: str
    code: str
    drafts: tuple[BlobRef, ...]
    checks: tuple[BlobRef, ...]
    phases: tuple[PhaseReceipt, ...]
    escalation: BlobRef | None = None
    rtl_head: BlobRef | None = None

    def __post_init__(self) -> None:
        if type(self.plan) is not BlobRef:
            raise TypeError("synthesis result requires a plan reference")
        if self.status not in {"review_ready", "revision_limit", "unfinished", "check_unavailable",
                               "cleanup_unconfirmed", "interrupted", "scope_review_requested"}:
            raise ValueError("unknown synthesis status")
        text(self.code, "synthesis outcome code")
        if (self.status == "scope_review_requested") != (self.escalation is not None):
            raise ValueError("scope reopening requires its request record and explicit status")
        if self.escalation is not None and type(self.escalation) is not BlobRef:
            raise TypeError("scope escalation requires an immutable record")
        if self.rtl_head is not None and (type(self.rtl_head) is not BlobRef or self.status != "review_ready"):
            raise TypeError("auxiliary RTL is a branch of a review-ready task")
        for refs in (self.drafts, self.checks):
            if type(refs) is not tuple or any(type(ref) is not BlobRef for ref in refs):
                raise TypeError("synthesis artifacts require tuples of BlobRef")
        if type(self.phases) is not tuple or any(type(p) is not PhaseReceipt for p in self.phases):
            raise TypeError("synthesis phases require PhaseReceipt values")
        if any(p.role not in {"author", "reader", "check", "source_review"} for p in self.phases):
            raise ValueError("scope preparation is a separate run, not an authoring phase")
        if len({(p.revision, p.role) for p in self.phases}) != len(self.phases):
            raise ValueError("synthesis phase receipt is duplicated")
        if self.status == "review_ready" and (not self.drafts or not self.checks or
                not any(p.role == "check" and p.revision == len(self.drafts) - 1 for p in self.phases)):
            raise ValueError("review package needs a concrete check of the final draft")

    def to_bytes(self) -> bytes:
        extended = any(p.role == "source_review" for p in self.phases)
        schema = "behave.synthesis-result/2" if extended else "behave.synthesis-result/1"
        if any(p.process is not None for p in self.phases):
            schema = "behave.synthesis-result/3"
        row = {"schema": schema, "plan": self.plan.as_dict(),
            "status": self.status, "code": self.code, "drafts": [ref.as_dict() for ref in self.drafts],
            "checks": [ref.as_dict() for ref in self.checks], "phases": [p.as_dict() for p in self.phases]}
        if self.escalation is not None:
            row.update(schema="behave.synthesis-result/4", escalation=self.escalation.as_dict())
        if self.rtl_head is not None:
            row.update(schema="behave.synthesis-result/5", rtl_head=self.rtl_head.as_dict())
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload: bytes) -> SynthesisResult:
        value = parse_json(payload)
        row = fields(value, {"schema", "plan", "status", "code", "drafts", "checks", "phases"}
                     | ({"escalation"} if value.get("schema") == "behave.synthesis-result/4" else set())
                     | ({"rtl_head"} if value.get("schema") == "behave.synthesis-result/5" else set()))
        if row["schema"] not in {f"behave.synthesis-result/{r}" for r in (1, 2, 3, 4, 5)}:
            version(row["schema"], "behave.synthesis-result/5")
        result = cls(BlobRef.from_dict(row["plan"]), row["status"], row["code"],
                     tuple(BlobRef.from_dict(r) for r in array(row["drafts"])),
                     tuple(BlobRef.from_dict(r) for r in array(row["checks"])),
                     tuple(PhaseReceipt.from_dict(p) for p in array(row["phases"])),
                     BlobRef.from_dict(row["escalation"]) if "escalation" in row else None,
                     BlobRef.from_dict(row["rtl_head"]) if "rtl_head" in row else None)
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "synthesis result must be canonical")
        return result
