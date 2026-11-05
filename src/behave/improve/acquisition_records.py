"""Pinned inputs to the source-only acquisition role."""

from dataclasses import dataclass
from typing import ClassVar

from behave.core.artifacts import BlobRef
from behave.core.records import fields


@dataclass(frozen=True, slots=True)
class AcquisitionInputs:
    library: BlobRef
    feedback: BlobRef
    guidance: BlobRef
    experience: BlobRef | None = None
    task_ids: tuple[str, ...] | None = None
    experience_proposals: tuple[BlobRef, ...] = ()
    construction_history: BlobRef | None = None
    feedback_view: str | None = None
    proposal_target: int | None = None
    experience_mode: str | None = None
    experience_policy: dict | None = None
    pending_experience: tuple[BlobRef, ...] = ()
    role: ClassVar[str] = "source_acquirer"

    def __post_init__(self):
        if any(type(ref) is not BlobRef for ref in (self.library, self.feedback, self.guidance)):
            raise TypeError("acquisition requires a frozen library, safe feedback and guidance")
        if self.experience is not None and type(self.experience) is not BlobRef:
            raise TypeError("experience must reference a frozen workspace snapshot")
        if self.feedback_view not in (None, "compact-v1"):
            raise ValueError("unknown Analyst feedback view")
        if self.proposal_target is not None and (type(self.proposal_target) is not int or self.proposal_target < 0):
            raise ValueError("proposal target must be a nonnegative integer")
        if self.experience_mode not in (None, "delta-v1", "merge-v1", "compact-v1", "compact-v2"):
            raise ValueError("unknown experience submission mode")
        if (self.experience_mode is None) != (self.experience_policy is None):
            raise ValueError("incremental experience requires an explicit policy")
        if self.experience_policy is not None:
            from .experience import ExperiencePolicy
            policy = ExperiencePolicy.from_dict(self.experience_policy)
            if self.experience_mode in ("compact-v1", "compact-v2") and (
                    self.experience_mode != f"compact-v{policy.version}"):
                raise ValueError("compact submission version differs from its policy")
        if any(type(ref) is not BlobRef for ref in self.pending_experience):
            raise TypeError("pending experience requires immutable references")
        if self.pending_experience and self.experience_mode != "delta-v1":
            raise ValueError("pending experience belongs to incremental Analyst inputs")

    def as_dict(self):
        row = {"role": self.role, "library": self.library.as_dict(), "feedback": self.feedback.as_dict(),
               "guidance": self.guidance.as_dict()}
        if self.experience is not None:
            row["experience"] = self.experience.as_dict()
        if self.task_ids is not None:
            row["task_ids"] = list(self.task_ids)
        if self.experience_proposals:
            row["experience_proposals"] = [ref.as_dict() for ref in self.experience_proposals]
        if self.construction_history is not None:
            row["construction_history"] = self.construction_history.as_dict()
        if self.feedback_view is not None:
            row["feedback_view"] = self.feedback_view
        if self.proposal_target is not None:
            row["proposal_target"] = self.proposal_target
        if self.experience_mode is not None:
            row.update(experience_mode=self.experience_mode, experience_policy=self.experience_policy)
        if self.pending_experience:
            row["pending_experience"] = [ref.as_dict() for ref in self.pending_experience]
        return row

    @classmethod
    def from_dict(cls, value):
        extra = {"experience", "task_ids", "experience_proposals", "construction_history",
                 "feedback_view", "proposal_target", "experience_mode", "experience_policy", "pending_experience"} & value.keys()
        row = fields(value, {"role", "library", "feedback", "guidance", *extra})
        if row["role"] != cls.role:
            raise ValueError("not an acquisition role")
        return cls(*(BlobRef.from_dict(row[name]) for name in ("library", "feedback", "guidance")),
                   experience=BlobRef.from_dict(row["experience"]) if "experience" in row else None,
                   task_ids=tuple(row["task_ids"]) if "task_ids" in row else None,
                   experience_proposals=tuple(BlobRef.from_dict(ref) for ref in row.get("experience_proposals", ())),
                   construction_history=BlobRef.from_dict(row["construction_history"])
                   if "construction_history" in row else None,
                   feedback_view=row.get("feedback_view"), proposal_target=row.get("proposal_target"),
                   experience_mode=row.get("experience_mode"), experience_policy=row.get("experience_policy"),
                   pending_experience=tuple(BlobRef.from_dict(ref) for ref in row.get("pending_experience", ())))
