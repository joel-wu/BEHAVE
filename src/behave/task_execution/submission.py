"""Solver selections, with separate tool arguments and versioned records.

These are workspace selections, not captured artifacts or evidence that a
submission committed. A later workspace transaction resolves them to immutable
content. Parsing never reads a path, compiles code or assigns a score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from behave.core.artifacts import ArtifactSelection  # compatibility re-export
from behave.core.records import canonical_json, fields, parse_json, version


@dataclass(frozen=True, slots=True)
class SubmissionRequest:
    """A parsed explicit submission; ``None`` records an absent component.

    An absent TB cannot invalidate the request or gate the other two artifacts.
    Even an all-absent explicit request differs from never issuing a submission.
    Missing files are classified by capture, not guessed by this parser.
    """

    SCHEMA: ClassVar[str] = "behave.solver-submission-request/1"
    behavior: ArtifactSelection | None
    rtl: ArtifactSelection | None
    testbench: ArtifactSelection | None = None

    def __post_init__(self) -> None:
        for item in (self.behavior, self.rtl, self.testbench):
            if item is not None and type(item) is not ArtifactSelection:
                raise TypeError("submission components require ArtifactSelection or None")

    def as_arguments(self) -> dict:
        """Model-facing selections, without internal record metadata."""
        return {
            "behavior": None if self.behavior is None else self.behavior.as_dict(),
            "rtl": None if self.rtl is None else self.rtl.as_dict(),
            "testbench": None if self.testbench is None else self.testbench.as_dict(),
        }

    def as_dict(self) -> dict:
        return {"schema": self.SCHEMA, **self.as_arguments()}

    def to_bytes(self) -> bytes:
        return canonical_json(self.as_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> SubmissionRequest:
        row = fields(parse_json(payload), {"schema", "behavior", "rtl", "testbench"})
        version(row["schema"], cls.SCHEMA)
        return cls(*(None if row[name] is None else ArtifactSelection.from_dict(row[name])
                     for name in ("behavior", "rtl", "testbench")))
