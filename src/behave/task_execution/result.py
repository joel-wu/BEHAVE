"""Shared eval/train task outcome; the score is derived, never a third judge.

TB telemetry and training algorithm choices are deliberately absent. A normal
no-submission is an application zero; an interrupted solver/judge is no result.
A saved submission may intentionally await a separate scoring phase.
"""

from __future__ import annotations

from dataclasses import dataclass

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, fields, parse_json, text, version

from .judgment import SubmissionJudgment, final_score

NORMAL_TERMINATIONS = {"stopped", "turn_limit", "context_limit", "wall_time_limit"}


def final_score_from_record(result: dict) -> float:
    """The same settlement for native readers of producer-owned JSON records."""
    judgment = result["judgment"]
    if judgment is None:
        return 0.0
    return final_score(*(part["score"] for part in (judgment["rtl"], judgment["behavior"])
                         if part is not None))


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    release: BlobRef
    revision: BlobRef
    attempt_id: str
    solver_head: BlobRef | None
    termination: str | None
    submission: BlobRef | None
    judgment: SubmissionJudgment | None
    failure_stage: str | None
    failure_code: str | None
    accounting: BlobRef

    def __post_init__(self) -> None:
        text(self.task_id, "task id")
        text(self.attempt_id, "attempt id")
        if any(type(ref) is not BlobRef for ref in (self.release, self.revision, self.accounting)):
            raise TypeError("task identity and accounting require content references")
        if any(ref is not None and type(ref) is not BlobRef for ref in (self.solver_head, self.submission)):
            raise TypeError("optional solver/submission references must be BlobRef")
        if self.judgment is not None and type(self.judgment) is not SubmissionJudgment:
            raise TypeError("task judgment requires SubmissionJudgment")
        if self.failure_stage is None:
            if self.failure_code is not None or self.solver_head is None:
                raise ValueError("a completed solver requires evidence, without an interruption code")
            if self.termination == "final_action":
                if self.submission is None:
                    raise ValueError("a submitted task requires a committed submission")
            elif self.termination not in NORMAL_TERMINATIONS or self.submission is not None or self.judgment is not None:
                raise ValueError("invalid normal no-submission result")
        else:
            text(self.failure_code, "interruption code")
            if self.judgment is not None:
                raise ValueError("a completed judgment cannot be replaced by an interruption")
            if self.failure_stage == "solver":
                if self.termination is not None or self.submission is not None:
                    raise ValueError("an interrupted solver has no declared normal termination")
            elif self.failure_stage == "judge":
                if self.termination != "final_action" or self.submission is None or self.solver_head is None:
                    raise ValueError("judge interruption requires a committed submission")
            else:
                raise ValueError("unknown task interruption stage")
        if self.judgment is not None and self.judgment.submission != self.submission:
            raise ValueError("task judgment belongs to another submission")

    @property
    def score(self) -> float | None:
        if self.failure_stage is not None:
            return None
        if self.submission is None:
            return 0.
        return None if self.judgment is None else self.judgment.score

    @property
    def final_score(self) -> float:
        """Final metric/reward, after any rejudging; keep raw diagnostics intact."""
        return 0.0 if self.judgment is None else self.judgment.final_score

    @property
    def rollout_status(self) -> str:
        if self.submission is not None:
            return "submitted"
        return "interrupted" if self.failure_stage == "solver" else "no_submission"

    @property
    def judgment_status(self) -> str:
        if self.submission is None:
            return "not_requested"
        return "pending" if self.score is None else "scored"

    @property
    def status(self) -> str:
        if self.submission is not None and self.judgment is None and self.failure_stage is None:
            return "pending_judgment"
        if self.score is None:
            return "no_result"
        return "no_submission" if self.submission is None else "scored"

    def as_dict(self) -> dict:
        return {"schema": "behave.task-result/3", "task_id": self.task_id, "release": self.release.as_dict(),
                "revision": self.revision.as_dict(), "attempt_id": self.attempt_id,
                "solver_head": None if self.solver_head is None else self.solver_head.as_dict(),
                "termination": self.termination,
                "submission": None if self.submission is None else self.submission.as_dict(),
                "judgment": None if self.judgment is None else self.judgment.as_dict(),
                "failure_stage": self.failure_stage, "failure_code": self.failure_code,
                "accounting": self.accounting.as_dict(), "status": self.status, "score": self.score}

    def to_bytes(self) -> bytes:
        return canonical_json(self.as_dict())

    @classmethod
    def from_bytes(cls, data: bytes) -> TaskResult:
        row = fields(parse_json(data), {"schema", *cls.__dataclass_fields__, "status", "score"})
        version(row["schema"], "behave.task-result/3")
        result = cls(row["task_id"], BlobRef.from_dict(row["release"]), BlobRef.from_dict(row["revision"]),
                     row["attempt_id"], None if row["solver_head"] is None else BlobRef.from_dict(row["solver_head"]),
                     row["termination"], None if row["submission"] is None else BlobRef.from_dict(row["submission"]),
                     None if row["judgment"] is None else SubmissionJudgment.from_dict(row["judgment"]),
                     row["failure_stage"], row["failure_code"], BlobRef.from_dict(row["accounting"]))
        if result.to_bytes() != data:
            raise ValueError("task result is noncanonical or its derived score/status disagrees")
        return result


@dataclass(frozen=True, slots=True)
class TaskReport:
    result: TaskResult
    content: BlobRef
    head: BlobRef

    def __post_init__(self) -> None:
        if type(self.result) is not TaskResult or type(self.content) is not BlobRef or type(self.head) is not BlobRef:
            raise TypeError("task report requires a typed result and its content/journal references")
        if self.content != BlobRef.of(self.result.to_bytes()):
            raise ValueError("task result differs from its content identity")
