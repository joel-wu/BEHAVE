"""Pinned workflow and public-simulator options; no contract inference."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from typing import ClassVar

from behave.core import runtime_defaults as defaults
from behave.core.diagnostics import DiagnosticOptions
from behave.core.records import canonical_json, fields, parse_json, text, version

from .analysis import AnalysisOptions


def _sim_default(name: str):
    # Only constructing a new default policy needs Sim. Reading an explicit
    # saved policy or importing solver interfaces must not initialize Sim.
    from behave.sim import api

    return {
        "DEFAULT_STIMULUS_SEED": api.DEFAULT_STIMULUS_SEED,
        "DEFAULT_BASELINE_CASES": api.DEFAULT_BASELINE_CASES,
        "DEFAULT_SEQUENCE_LENGTH": api.DEFAULT_SEQUENCE_LENGTH,
        "DEFAULT_SEARCH_SECONDS": api.DEFAULT_SEARCH_SECONDS,
    }[name]


@dataclass(frozen=True, slots=True)
class JudgePolicy:
    """Execution budgets and public Sim options; no candidate-semantic rules.

    ``sequence_length`` requests transactions per baseline input endpoint, not
    RTL FIFO capacity or clock cycles; Sim's generation domain still bounds it.
    ``score_timeout_seconds`` bounds one artifact's complete scoring attempt.
    Optional analysis gets its own wall budget after the score checkpoint.
    Sim owns its internal job and replay limits; this policy does not override
    them with a second set of defaults.

    ``search_seconds`` is Sim's total guidance budget across all directions.
    Zero explicitly selects baseline-only. Sim owns search termination and
    scores the retained stimulus pool after search ends. A pass requires the
    complete mandatory baseline, including its continuous workload. Partial
    checks retain evidence but leave an incomplete baseline unresolved; Search
    may stop at its budget without invalidating completed checks. The outer
    timeout still withholds any result not returned/persisted in time.
    """

    SCHEMA: ClassVar[str] = "behave.judge-policy/5"
    command_version: str
    authority_version: str
    max_retries: int = defaults.EVAL_MAX_RETRIES
    seed: str = field(default_factory=lambda: _sim_default("DEFAULT_STIMULUS_SEED"))
    baseline_cases: int = field(default_factory=lambda: _sim_default("DEFAULT_BASELINE_CASES"))
    sequence_length: int = field(default_factory=lambda: _sim_default("DEFAULT_SEQUENCE_LENGTH"))
    search_seconds: float = field(default_factory=lambda: _sim_default("DEFAULT_SEARCH_SECONDS"))
    score_timeout_seconds: int = defaults.EVAL_SCORE_SECONDS
    worker_log_bytes: int = defaults.LOG_RETAIN_BYTES
    analysis: AnalysisOptions | None = None
    diagnostics: DiagnosticOptions | None = None

    def __post_init__(self) -> None:
        text(self.command_version, "simulator command version")
        text(self.authority_version, "simulator authority version")
        text(self.seed, "baseline seed")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("max_retries must be a nonnegative integer")
        if (type(self.search_seconds) not in (int, float)
                or not math.isfinite(self.search_seconds) or self.search_seconds < 0):
            raise ValueError("search_seconds must be finite and nonnegative")
        for name in ("baseline_cases", "sequence_length", "score_timeout_seconds", "worker_log_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.analysis is not None and type(self.analysis) is not AnalysisOptions:
            raise TypeError("analysis requires AnalysisOptions or None")
        if self.diagnostics is not None and type(self.diagnostics) is not DiagnosticOptions:
            raise TypeError("diagnostics requires DiagnosticOptions or None")

    def to_bytes(self) -> bytes:
        row = {"schema": self.SCHEMA, **asdict(self)}
        if self.diagnostics is None:
            row.pop("diagnostics")
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, data: bytes) -> JudgePolicy:
        row = parse_json(data)
        diagnostics = row.pop("diagnostics", None)
        row = fields(row, {"schema", *(field.name for field in dataclass_fields(cls) if field.name != "diagnostics")})
        version(row["schema"], cls.SCHEMA)
        options = {key: value for key, value in row.items() if key != "schema"}
        if options["analysis"] is not None:
            options["analysis"] = AnalysisOptions.from_dict(options["analysis"])
        options["diagnostics"] = None if diagnostics is None else DiagnosticOptions(**diagnostics)
        return cls(**options)
