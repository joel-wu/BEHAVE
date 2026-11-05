"""Resolve a pinned published task into separate immutable consumer views.

Loading verifies the entire declared content closure, not the task's semantic
correctness. Admission and artifact evaluation still belong to ``behave.sim``.
"""

from __future__ import annotations

from dataclasses import dataclass

from behave.core.artifacts import (
    BlobRef,
    ContentReader,
    FileData,
    FileRef,
    LoadedSource,
    SourceArtifact,
    file_references,
    file_set,
    read_verified,
)
from behave.core.records import RecordError, text

from .records import ReleaseManifest, TaskManifest


@dataclass(frozen=True, slots=True)
class PublicTaskInputs:
    """Safe role input: no private manifest, resolver, parent object or host path."""

    task_id: str
    release: BlobRef
    revision: BlobRef
    spec_path: str
    contract_path: str
    files: tuple[FileData, ...]

    def __post_init__(self) -> None:
        _identity(self.task_id, self.release, self.revision)
        refs = file_references(self.files)
        paths = {item.path for item in refs}
        if self.spec_path == self.contract_path or self.spec_path not in paths or self.contract_path not in paths:
            raise RecordError("publication-public-view-invalid", "spec and contract must name distinct public files")


@dataclass(frozen=True, slots=True)
class JudgeInputs:
    task_id: str
    release: BlobRef
    revision: BlobRef
    contract: FileData
    reference_behavior: LoadedSource
    reference_rtl: LoadedSource | None
    run_policy: bytes
    judge_policy: bytes

    def __post_init__(self) -> None:
        _identity(self.task_id, self.release, self.revision)
        if type(self.contract) is not FileData or type(self.reference_behavior) is not LoadedSource:
            raise TypeError("judge inputs require immutable contract and reference files")
        if self.reference_rtl is not None and type(self.reference_rtl) is not LoadedSource:
            raise TypeError("reference_rtl requires LoadedSource or None")
        if type(self.run_policy) is not bytes or type(self.judge_policy) is not bytes:
            raise TypeError("judge policies must be immutable bytes")


@dataclass(frozen=True, slots=True)
class PublishedTask:
    """Trusted resolved view, from which only ``public`` goes to the agent."""

    release: ReleaseManifest
    manifest: TaskManifest
    public: PublicTaskInputs
    judge: JudgeInputs

    def __post_init__(self) -> None:
        for value, expected in ((self.release, ReleaseManifest), (self.manifest, TaskManifest),
                                (self.public, PublicTaskInputs), (self.judge, JudgeInputs)):
            if type(value) is not expected:
                raise TypeError(f"published task requires {expected.__name__}")


def _identity(task_id: str, release: BlobRef, revision: BlobRef) -> None:
    text(task_id, "task_id")
    if type(release) is not BlobRef or type(revision) is not BlobRef:
        raise TypeError("task identity requires release and revision BlobRef values")


class _Loader:
    """Read each declared object once for this task, sharing repeated file bytes."""

    def __init__(self, reader: ContentReader):
        self.reader = reader
        self.contents: dict[BlobRef, bytes] = {}

    def read(self, ref: BlobRef) -> bytes:
        if ref not in self.contents:
            self.contents[ref] = read_verified(self.reader, ref)
        return self.contents[ref]

    def materialize(self, files: tuple[FileRef, ...]) -> tuple[FileData, ...]:
        return tuple(FileData(item, self.read(item.content)) for item in files)

    def source(self, artifact: SourceArtifact) -> LoadedSource:
        return LoadedSource(artifact, self.materialize(artifact.files))


class PublishedReleaseReader:
    """Resolve tasks from one verified, pinned release inventory.

    Batch callers reuse the inventory/index, not task payloads. Each ``load``
    verifies that task's declared closure anew, without reading other tasks or
    retaining their bytes. This is a trusted reader; only a task's ``public``
    view may be passed to a solver. No code is imported or executed.
    """

    def __init__(self, reader: ContentReader, release: BlobRef):
        if type(release) is not BlobRef:
            raise TypeError("load requires a release BlobRef")
        self._reader = reader
        self._reference = release
        self._payload = read_verified(reader, release)
        self._release = ReleaseManifest.from_bytes(self._payload)
        self._revisions = {item.task_id: item.manifest for item in self._release.tasks}

    @property
    def manifest(self) -> ReleaseManifest:
        """The verified, immutable inventory; accessing it does not read tasks."""
        return self._release

    def load(self, task_id: str) -> PublishedTask:
        if type(task_id) is not str:
            raise TypeError("load requires a task_id string")
        revision = self._revisions.get(task_id)
        if revision is None:
            raise RecordError("release-task-absent", "requested task is not a member of the pinned release")
        loader = _Loader(self._reader)
        # Preserve one verified read per object even if another declared input
        # happens to reference the release bytes themselves.
        loader.contents[self._reference] = self._payload
        manifest = TaskManifest.from_bytes(loader.read(revision))
        if manifest.task_id != task_id:
            raise RecordError("release-task-mismatch", "release entry and task manifest name different tasks")
        loader.read(self._release.policy)
        # Verify retained distribution inputs but never add them to either
        # execution view. Repeated references still use one verified read.
        for file in manifest.archive_files:
            loader.read(file.content)
        public_files = loader.materialize(file_set((manifest.spec, manifest.contract, *manifest.public_files)))
        public = PublicTaskInputs(task_id, self._reference, revision, manifest.spec.path, manifest.contract.path,
                                  public_files)
        judge = JudgeInputs(
            task_id=task_id, release=self._reference, revision=revision,
            contract=next(item for item in public_files if item.reference == manifest.contract),
            reference_behavior=loader.source(manifest.reference_behavior),
            reference_rtl=None if manifest.reference_rtl is None else loader.source(manifest.reference_rtl),
            run_policy=loader.read(manifest.run_policy), judge_policy=loader.read(manifest.judge_policy),
        )
        return PublishedTask(self._release, manifest, public, judge)


def load_published_task(
    reader: ContentReader, release: BlobRef, task_id: str,
) -> PublishedTask:
    """Load one authorized task; batch callers may reuse PublishedReleaseReader.

    A path, latest pointer, glob or candidate filename cannot select a revision.
    No code is imported or executed, and the reader exposes no publication API.
    """
    if type(release) is not BlobRef or type(task_id) is not str:
        raise TypeError("load requires a release BlobRef and task_id string")
    return PublishedReleaseReader(reader, release).load(task_id)
