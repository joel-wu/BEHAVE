"""Workflow outcomes: two artifact scores, never a second simulator verdict."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType

from behave.core.artifacts import BlobRef, FileRef
from behave.core.lifecycle import CleanupState
from behave.core.records import array, fields, metadata, text, thaw_json, version

from .analysis import AnalysisReport


def final_score(*scores: int | float | None) -> float:
    """Settle requested components: unresolved outcomes contribute zero."""
    return sum(0.0 if score is None else score for score in scores) / len(scores)


@dataclass(frozen=True, slots=True)
class ComponentJudgment:
    score: int | None
    code: str
    origin: str | None = None
    retryable: bool = False
    evidence: tuple[BlobRef, ...] = ()
    detail: MappingProxyType | None = None
    analysis: AnalysisReport | None = None
    cleanup: CleanupState = CleanupState.COMPLETE
    # Explicit public files, not permission to traverse arbitrary judge evidence.
    diagnostics: tuple[FileRef, ...] = ()
    # Coordinator wall time through worker cleanup/publication, including any
    # requested analysis. Older records and unexecuted judgments have no timing.
    elapsed_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.score is not None and (type(self.score) is not int or self.score not in (0, 1)):
            raise ValueError("an artifact score is exactly zero, one, or absent")
        text(self.code, "judgment code")
        if type(self.cleanup) is not CleanupState:
            raise TypeError("judgment requires a typed cleanup witness")
        if self.retryable and not self.cleanup.quiescent:
            raise ValueError("unfinished execution cannot authorize a retry")
        if self.origin not in (None, "candidate", "reference", "verifier", "infrastructure", "resource-policy"):
            raise ValueError("unknown failure origin")
        if type(self.retryable) is not bool or (self.retryable and self.score is not None):
            raise ValueError("only a no-result may be retried")
        if self.origin in ("reference", "verifier", "infrastructure") and self.score is not None:
            raise ValueError("reference, verifier and infrastructure failures establish no score")
        if self.origin == "candidate" and self.score != 0:
            raise ValueError("a determinate candidate fault scores zero")
        if self.score is None and self.origin is None:
            raise ValueError("a no-result must name its origin")
        if type(self.evidence) is not tuple or any(type(ref) is not BlobRef for ref in self.evidence):
            raise TypeError("judgment evidence requires a tuple of BlobRef")
        object.__setattr__(self, "detail", metadata({} if self.detail is None else self.detail))
        if type(self.diagnostics) is not tuple or any(type(file) is not FileRef for file in self.diagnostics):
            raise TypeError("judgment diagnostics require named public files")
        if self.elapsed_seconds is not None and (
                type(self.elapsed_seconds) not in (int, float)
                or not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0):
            raise ValueError("elapsed time must be finite and nonnegative")
        if self.analysis is not None:
            if type(self.analysis) is not AnalysisReport or self.score is None:
                raise ValueError("ancillary analysis requires a decided score")
            if self.analysis.concrete != self.detail.get("evaluation"):
                raise ValueError("analysis belongs to a different concrete evaluation")

    @property
    def closure(self) -> tuple[BlobRef, ...]:
        return (self.evidence + tuple(file.content for file in self.diagnostics)
                + (() if self.analysis is None else self.analysis.closure))

    @property
    def status(self) -> str:
        return "no_result" if self.score is None else "scored"

    def as_dict(self) -> dict:
        row = {"score": self.score, "code": self.code, "origin": self.origin, "retryable": self.retryable,
                "evidence": [ref.as_dict() for ref in self.evidence], "detail": thaw_json(self.detail),
                "analysis": None if self.analysis is None else self.analysis.as_dict(), "cleanup": self.cleanup.value}
        if self.diagnostics:
            row["diagnostics"] = [file.as_dict() for file in self.diagnostics]
        if self.elapsed_seconds is not None:
            row["elapsed_seconds"] = self.elapsed_seconds
        return row

    @classmethod
    def from_dict(cls, value: object) -> ComponentJudgment:
        row = dict(value)
        elapsed_seconds = row.pop("elapsed_seconds", None)
        diagnostics = tuple(FileRef.from_dict(file) for file in array(row.pop("diagnostics", [])))
        row = fields(row, {"score", "code", "origin", "retryable", "evidence", "detail", "analysis", "cleanup"})
        return cls(row["score"], row["code"], row["origin"], row["retryable"],
                   tuple(BlobRef.from_dict(ref) for ref in array(row["evidence"])), row["detail"],
                   None if row["analysis"] is None else AnalysisReport.from_dict(row["analysis"]),
                   CleanupState(row["cleanup"]), diagnostics, elapsed_seconds)


@dataclass(frozen=True, slots=True)
class SubmissionJudgment:
    submission: BlobRef
    behavior: ComponentJudgment | None
    rtl: ComponentJudgment
    head: BlobRef

    def __post_init__(self) -> None:
        if any(type(ref) is not BlobRef for ref in (self.submission, self.head)):
            raise TypeError("judgment identity requires BlobRef")
        if type(self.rtl) is not ComponentJudgment or (
                self.behavior is not None and type(self.behavior) is not ComponentJudgment):
            raise TypeError("a submission requires RTL and an optional Behavior outcome")

    @property
    def cleanup_state(self) -> CleanupState:
        if self.behavior is None:
            return self.rtl.cleanup
        return CleanupState.combine(self.behavior.cleanup, self.rtl.cleanup)

    @property
    def score(self) -> float | None:
        # None means Behavior was not requested, not a failed or unscored model.
        if self.behavior is None:
            return None if self.rtl.score is None else float(self.rtl.score)
        if self.behavior.score is None or self.rtl.score is None:
            return None
        return (self.behavior.score + self.rtl.score) / 2

    @property
    def final_score(self) -> float:
        return final_score(*(part.score for part in (self.rtl, self.behavior) if part is not None))

    def as_dict(self) -> dict:
        return {"schema": "behave.submission-judgment/3", "submission": self.submission.as_dict(),
                "behavior": None if self.behavior is None else self.behavior.as_dict(),
                "rtl": self.rtl.as_dict(), "score": self.score,
                "head": self.head.as_dict()}

    @classmethod
    def from_dict(cls, value: object) -> SubmissionJudgment:
        row = fields(value, {"schema", "submission", "behavior", "rtl", "score", "head"})
        version(row["schema"], "behave.submission-judgment/3")
        result = cls(BlobRef.from_dict(row["submission"]),
                     None if row["behavior"] is None else ComponentJudgment.from_dict(row["behavior"]),
                     ComponentJudgment.from_dict(row["rtl"]), BlobRef.from_dict(row["head"]))
        if ((row["score"] is not None and type(row["score"]) not in (int, float))
                or row["score"] != result.score):
            raise ValueError("aggregate score differs from the requested component outcomes")
        return result
