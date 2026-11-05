"""Data-owned publication manifests; no synthesis workflow is imported here."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

from behave.core.artifacts import BlobRef, FileRef, SourceArtifact, file_set
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


@dataclass(frozen=True, slots=True)
class TaskManifest:
    """One task revision, referencing rather than redefining simulator policy.

    Metadata retains provenance, family, tier and licensing information. It is
    not an executable policy and is not implicitly made agent-visible. The
    reserved ``archive_files`` metadata lists additional private FileRefs; it
    preserves original inputs without promoting them to executable references.
    """

    SCHEMA: ClassVar[str] = "behave.published-task/1"
    task_id: str
    spec: FileRef
    contract: FileRef
    reference_behavior: SourceArtifact
    run_policy: BlobRef
    judge_policy: BlobRef
    public_files: tuple[FileRef, ...] = ()
    reference_rtl: SourceArtifact | None = None
    metadata: MappingProxyType = field(default_factory=dict)

    def __post_init__(self) -> None:
        text(self.task_id, "task_id")
        if type(self.spec) is not FileRef or type(self.contract) is not FileRef:
            raise TypeError("spec and contract require FileRef")
        if type(self.reference_behavior) is not SourceArtifact:
            raise TypeError("reference_behavior requires SourceArtifact")
        if self.reference_rtl is not None and type(self.reference_rtl) is not SourceArtifact:
            raise TypeError("reference_rtl requires SourceArtifact or None")
        if type(self.run_policy) is not BlobRef or type(self.judge_policy) is not BlobRef:
            raise TypeError("run_policy and judge_policy require BlobRef")
        object.__setattr__(self, "public_files", file_set(self.public_files))
        file_set((self.spec, self.contract, *self.public_files))
        object.__setattr__(self, "metadata", metadata(self.metadata))
        _ = self.archive_files  # Validate the reserved inventory without rewriting metadata.

    @property
    def archive_files(self) -> tuple[FileRef, ...]:
        """Private distribution attachments, separate from solver/judge inputs."""
        from .files import task_archive_files

        return task_archive_files(self)

    def as_dict(self) -> dict:
        return {
            "schema": self.SCHEMA, "task_id": self.task_id,
            "spec": self.spec.as_dict(), "contract": self.contract.as_dict(),
            "public_files": [item.as_dict() for item in self.public_files],
            "reference_behavior": self.reference_behavior.as_dict(),
            "reference_rtl": None if self.reference_rtl is None else self.reference_rtl.as_dict(),
            "run_policy": self.run_policy.as_dict(), "judge_policy": self.judge_policy.as_dict(),
            "metadata": thaw_json(self.metadata),
        }

    def to_bytes(self) -> bytes:
        return canonical_json(self.as_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> TaskManifest:
        row = fields(parse_json(payload), {
            "schema", "task_id", "spec", "contract", "public_files", "reference_behavior",
            "reference_rtl", "run_policy", "judge_policy", "metadata",
        })
        version(row["schema"], cls.SCHEMA)
        result = cls(
            task_id=row["task_id"], spec=FileRef.from_dict(row["spec"]),
            contract=FileRef.from_dict(row["contract"]),
            public_files=tuple(FileRef.from_dict(item) for item in array(row["public_files"])),
            reference_behavior=SourceArtifact.from_dict(row["reference_behavior"]),
            reference_rtl=None if row["reference_rtl"] is None else SourceArtifact.from_dict(row["reference_rtl"]),
            run_policy=BlobRef.from_dict(row["run_policy"]), judge_policy=BlobRef.from_dict(row["judge_policy"]),
            metadata=row["metadata"],
        )
        _canonical(payload, result.to_bytes())
        return result


@dataclass(frozen=True, slots=True)
class TaskRevision:
    task_id: str
    manifest: BlobRef

    def __post_init__(self) -> None:
        text(self.task_id, "task_id")
        if type(self.manifest) is not BlobRef:
            raise TypeError("manifest requires BlobRef")

    def as_dict(self) -> dict:
        return {"task_id": self.task_id, "manifest": self.manifest.as_dict()}

    @classmethod
    def from_dict(cls, value: object) -> TaskRevision:
        row = fields(value, {"task_id", "manifest"})
        return cls(row["task_id"], BlobRef.from_dict(row["manifest"]))


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    SCHEMA: ClassVar[str] = "behave.dataset-release/1"
    release_id: str
    tasks: tuple[TaskRevision, ...]
    policy: BlobRef
    metadata: MappingProxyType = field(default_factory=dict)

    def __post_init__(self) -> None:
        text(self.release_id, "release_id")
        if type(self.tasks) is not tuple or any(type(item) is not TaskRevision for item in self.tasks):
            raise TypeError("tasks must be a tuple of TaskRevision")
        if type(self.policy) is not BlobRef:
            raise TypeError("policy requires BlobRef")
        if len({item.task_id for item in self.tasks}) != len(self.tasks):
            raise RecordError("release-task-duplicate", "a release may name only one revision per task")
        object.__setattr__(self, "tasks", tuple(sorted(self.tasks, key=lambda item: item.task_id)))
        object.__setattr__(self, "metadata", metadata(self.metadata))

    def as_dict(self) -> dict:
        return {
            "schema": self.SCHEMA, "release_id": self.release_id,
            "tasks": [item.as_dict() for item in self.tasks],
            "policy": self.policy.as_dict(), "metadata": thaw_json(self.metadata),
        }

    def to_bytes(self) -> bytes:
        return canonical_json(self.as_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> ReleaseManifest:
        row = fields(parse_json(payload), {"schema", "release_id", "tasks", "policy", "metadata"})
        version(row["schema"], cls.SCHEMA)
        result = cls(row["release_id"], tuple(TaskRevision.from_dict(item) for item in array(row["tasks"])),
                     BlobRef.from_dict(row["policy"]), row["metadata"])
        _canonical(payload, result.to_bytes())
        return result


def _canonical(payload: bytes, expected: bytes) -> None:
    if payload != expected:
        raise RecordError("record-not-canonical", "published manifests must use their canonical serialization")
