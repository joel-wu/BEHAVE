"""Submitted-test telemetry, deliberately without score or reward fields.

Coverage is a parsed submitted-process diagnostic, not an attestation. Neither
its counters nor the submitted test's comparison status establish correctness.
An unavailable measurement is distinct from measured zero coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import array, fields, text


@dataclass(frozen=True, slots=True)
class RTLCoverage:
    rtl: BlobRef
    collector: str
    metric: str
    covered: int
    total: int
    evidence: BlobRef
    assurance: str = "submitted-process-diagnostic"

    def __post_init__(self) -> None:
        if type(self.rtl) is not BlobRef or type(self.evidence) is not BlobRef:
            raise TypeError("coverage needs the RTL bundle and measurement content references")
        text(self.collector, "coverage collector version")
        text(self.metric, "coverage metric")
        if self.assurance != "submitted-process-diagnostic":
            raise ValueError("submitted-TB coverage is diagnostic, not attested")
        if type(self.covered) is not int or type(self.total) is not int or not 0 <= self.covered <= self.total:
            raise ValueError("coverage requires integer counts with 0 <= covered <= total")

    @property
    def fraction(self) -> float | None:
        return self.covered / self.total if self.total else None

    def as_dict(self) -> dict:
        return {"rtl": self.rtl.as_dict(), "collector": self.collector, "metric": self.metric,
                "covered": self.covered, "total": self.total, "evidence": self.evidence.as_dict(),
                "assurance": self.assurance}

    @classmethod
    def from_dict(cls, value: object) -> RTLCoverage:
        row = fields(value, {"rtl", "collector", "metric", "covered", "total", "evidence", "assurance"})
        return cls(BlobRef.from_dict(row["rtl"]), row["collector"], row["metric"], row["covered"], row["total"],
                   BlobRef.from_dict(row["evidence"]), row["assurance"])


@dataclass(frozen=True, slots=True)
class TBObservation:
    status: str
    code: str
    comparison: str = "not_reported"
    coverage: RTLCoverage | None = None
    retryable: bool = False
    evidence: tuple[BlobRef, ...] = ()
    cleanup: CleanupState = CleanupState.COMPLETE

    def __post_init__(self) -> None:
        if self.status not in ("completed", "failed", "unavailable"):
            raise ValueError("unknown testbench observation status")
        text(self.code, "testbench reason code")
        if type(self.cleanup) is not CleanupState:
            raise TypeError("testbench execution requires a typed cleanup witness")
        if self.retryable and not self.cleanup.quiescent:
            raise ValueError("unfinished execution cannot authorize a retry")
        if self.comparison not in ("passed", "failed", "not_reported"):
            raise ValueError("unknown submitted comparison status")
        if self.coverage is not None and type(self.coverage) is not RTLCoverage:
            raise TypeError("coverage requires RTLCoverage or None")
        if self.status == "unavailable" and (self.coverage is not None or self.comparison != "not_reported"):
            raise ValueError("unavailable execution cannot claim a measurement or comparison")
        if type(self.retryable) is not bool or (self.retryable and self.status != "unavailable"):
            raise ValueError("only unavailable testbench execution may be retried")
        if type(self.evidence) is not tuple or any(type(ref) is not BlobRef for ref in self.evidence):
            raise TypeError("testbench evidence requires a tuple of BlobRef")

    @property
    def closure(self) -> tuple[BlobRef, ...]:
        refs = self.evidence if self.coverage is None else (*self.evidence, self.coverage.evidence, self.coverage.rtl)
        return tuple(dict.fromkeys(refs))

    def as_dict(self) -> dict:
        return {"status": self.status, "code": self.code, "comparison": self.comparison,
                "coverage": None if self.coverage is None else self.coverage.as_dict(),
                "retryable": self.retryable, "evidence": [ref.as_dict() for ref in self.evidence],
                "cleanup": self.cleanup.value}

    @classmethod
    def from_dict(cls, value: object) -> TBObservation:
        row = fields(value, {"status", "code", "comparison", "coverage", "retryable", "evidence", "cleanup"})
        return cls(row["status"], row["code"], row["comparison"],
                   None if row["coverage"] is None else RTLCoverage.from_dict(row["coverage"]), row["retryable"],
                   tuple(BlobRef.from_dict(ref) for ref in array(row["evidence"])), CleanupState(row["cleanup"]))


@dataclass(frozen=True, slots=True)
class TBReport:
    inputs: BlobRef
    observation: TBObservation
    head: BlobRef

    def __post_init__(self) -> None:
        if type(self.inputs) is not BlobRef or type(self.head) is not BlobRef:
            raise TypeError("testbench report needs input and journal identities")
        if type(self.observation) is not TBObservation:
            raise TypeError("testbench report needs TBObservation")

    def as_dict(self) -> dict:
        return {"schema": "behave.testbench-report/3", "inputs": self.inputs.as_dict(),
                "observation": self.observation.as_dict(), "head": self.head.as_dict()}
