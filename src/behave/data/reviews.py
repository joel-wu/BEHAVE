"""Link a reader reconstruction to the exact draft it was asked to implement.

The trusted caller supplies an exclusively owned check journal. This operation
does not decide which side of a mismatch is correct or approve publication.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from behave.agent.admission import ResourcePool
from behave.agent.journal import AttemptJournal
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, version
from behave.task_execution.comparison import ComparisonInputs

from .checks import ConcreteCheck, check_sources
from .drafts import Reconstruction, load_draft, load_source_artifact


@dataclass(frozen=True, slots=True)
class DraftCheck:
    draft: BlobRef
    reconstruction: BlobRef
    comparison: ConcreteCheck

    def __post_init__(self) -> None:
        if type(self.draft) is not BlobRef or type(self.reconstruction) is not BlobRef:
            raise TypeError("draft check requires immutable subject references")
        if type(self.comparison) is not ConcreteCheck:
            raise TypeError("draft check requires a concrete comparison")

    def to_bytes(self) -> bytes:
        return canonical_json({"schema": "behave.draft-check/1", "draft": self.draft.as_dict(),
                               "reconstruction": self.reconstruction.as_dict(),
                               "comparison": self.comparison.as_dict()})

    @classmethod
    def from_bytes(cls, payload: bytes) -> DraftCheck:
        row = fields(parse_json(payload), {"schema", "draft", "reconstruction", "comparison"})
        version(row["schema"], "behave.draft-check/1")
        result = cls(BlobRef.from_dict(row["draft"]), BlobRef.from_dict(row["reconstruction"]),
                     ConcreteCheck.from_dict(row["comparison"]))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "draft check must be canonical")
        return result


def compare_reconstruction(*, draft: BlobRef, reconstruction: BlobRef, reader: ContentReader,
                           journal: AttemptJournal, admission: ResourcePool | None = None,
                           cancelled: Callable[[], bool] = lambda: False) -> DraftCheck:
    inputs = reconstruction_inputs(reader, draft, reconstruction)
    comparison = check_sources(inputs, journal=journal, admission=admission, cancelled=cancelled)
    return DraftCheck(draft, reconstruction, comparison)


def reconstruction_inputs(reader, draft, reconstruction):
    """One shared input constructor for the coordinator and Reviewer rechecks."""
    loaded = load_draft(reader, draft)
    record = Reconstruction.from_bytes(read_verified(reader, reconstruction))
    if record.draft != draft:
        raise RecordError("reconstruction-draft-mismatch", "reader implemented a different draft")
    if record.behavior is None:
        raise RecordError("reconstruction-findings-only", "findings without an implementation cannot be scored")
    source = load_source_artifact(reader, record.behavior)
    task = loaded.record.task
    return ComparisonInputs("behavior", source,
        loaded.source(task.reference_behavior), loaded.file(task.contract.path),
        read_verified(reader, task.run_policy), loaded.judge_policy, reference_visible=True,
        spec=loaded.file(task.spec.path))
