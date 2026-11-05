"""Optional bounded-analysis policy and result, separate from score authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, validate_sha256_digest
from behave.core.records import fields, text


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    wall_timeout_seconds: int
    feasibility_timeout_ms: int | None = defaults.ANALYSIS_FEASIBILITY_MS
    query_timeout_ms: int | None = defaults.ANALYSIS_QUERY_MS
    batch: int = defaults.ANALYSIS_BATCH

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value is None and name in {"feasibility_timeout_ms", "query_timeout_ms"}:
                continue
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> AnalysisOptions:
        return cls(**fields(value, {"wall_timeout_seconds", "feasibility_timeout_ms", "query_timeout_ms", "batch"}))


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """A native outcome or an interrupted analysis of one decided evaluation.

    Native refusals are returned outcomes, not transport failures. No verdict is
    reinterpreted here; the native document retains classifications and evidence.
    """

    concrete: str
    policy: BlobRef
    code: str
    outcome: BlobRef | None = None

    def __post_init__(self) -> None:
        validate_sha256_digest(self.concrete)
        text(self.code, "analysis code")
        if type(self.policy) is not BlobRef or (self.outcome is not None and type(self.outcome) is not BlobRef):
            raise TypeError("analysis policy and outcome require content references")
        if (self.code == "analysis-returned") != (self.outcome is not None):
            raise ValueError("a returned analysis must retain its native outcome")

    @property
    def status(self) -> str:
        return "returned" if self.outcome is not None else "unavailable"

    @property
    def closure(self) -> tuple[BlobRef, ...]:
        return (self.policy,) if self.outcome is None else (self.policy, self.outcome)

    def as_dict(self) -> dict:
        return {"concrete": self.concrete, "policy": self.policy.as_dict(), "code": self.code,
                "outcome": None if self.outcome is None else self.outcome.as_dict()}

    @classmethod
    def from_dict(cls, value: object) -> AnalysisReport:
        row = fields(value, {"concrete", "policy", "code", "outcome"})
        return cls(row["concrete"], BlobRef.from_dict(row["policy"]), row["code"],
                   None if row["outcome"] is None else BlobRef.from_dict(row["outcome"]))
