"""Immutable construction records and verified views, not publication decisions.

TaskManifest is reused as the task's content layout. Enclosing it in a draft
does not publish it: only a separately admitted ReleaseManifest can do that.
"""

from __future__ import annotations

from dataclasses import dataclass

from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, LoadedSource, SourceArtifact, read_verified
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, version
from behave.data.published import TaskManifest
from behave.data.published.files import task_files


def _record(reader: ContentReader, reference: BlobRef) -> bytes:
    return read_verified(reader, reference)


def _canonical(payload: bytes, expected: bytes) -> None:
    if payload != expected:
        raise RecordError("record-not-canonical", "construction records must use canonical serialization")


def _texts(values: tuple[str, ...], name: str) -> None:
    if type(values) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    for value in values:
        text(value, name)


def _files(reader: ContentReader, snapshot: WorkspaceSnapshot) -> tuple[FileData, ...]:
    return tuple(FileData(f, read_verified(reader, f.content)) for f in snapshot.files)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    origin: str
    revision: str | None
    license: str | None
    selected: SourceArtifact
    acquisition: BlobRef | None = None

    def __post_init__(self) -> None:
        for name in ("source_id", "origin"):
            text(getattr(self, name), name)
        for name in ("revision", "license"):
            if getattr(self, name) is not None:
                text(getattr(self, name), name)
        if type(self.selected) is not SourceArtifact:
            raise TypeError("selected source requires SourceArtifact")
        if self.acquisition is not None and type(self.acquisition) is not BlobRef:
            raise TypeError("source acquisition requires an immutable receipt")

    def to_bytes(self) -> bytes:
        row = {"schema": "behave.source-record/1", "source_id": self.source_id,
                               "origin": self.origin, "revision": self.revision, "license": self.license,
                               "selected": self.selected.as_dict()}
        if self.acquisition is not None:
            row.update(schema="behave.source-record/2", acquisition=self.acquisition.as_dict())
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload: bytes) -> SourceRecord:
        value = parse_json(payload)
        extended = value.get("schema") == "behave.source-record/2"
        row = fields(value, {"schema", "source_id", "origin", "revision", "license", "selected"}
                     | ({"acquisition"} if extended else set()))
        version(row.pop("schema"), f"behave.source-record/{2 if extended else 1}")
        if extended:
            row["acquisition"] = BlobRef.from_dict(row["acquisition"])
        result = cls(**{**row, "selected": SourceArtifact.from_dict(row["selected"])})
        _canonical(payload, result.to_bytes())
        return result


@dataclass(frozen=True, slots=True)
class ScopeProposal:
    """Declared intent/adaptations, not proof that an implementation preserves them."""

    source: BlobRef
    intent: str
    capabilities: tuple[str, ...]
    adaptations: tuple[str, ...]
    contract: FileRef | None = None
    parent: BlobRef | None = None
    selection: BlobRef | None = None
    brief: BlobRef | None = None

    def __post_init__(self) -> None:
        if type(self.source) is not BlobRef:
            raise TypeError("scope source requires BlobRef")
        text(self.intent, "intent")
        _texts(self.capabilities, "capabilities")
        _texts(self.adaptations, "adaptations")
        if not self.capabilities:
            raise ValueError("scope must declare the capabilities to retain")
        if self.contract is not None and type(self.contract) is not FileRef:
            raise TypeError("scope contract requires a pinned file")
        if self.parent is not None and (type(self.parent) is not BlobRef or self.contract is None):
            raise TypeError("scope revision requires a parent reference and pinned contract")
        if self.selection is not None and (type(self.selection) is not BlobRef or self.contract is None):
            raise TypeError("screened scope requires a selection receipt and pinned contract")
        if self.brief is not None and type(self.brief) is not BlobRef:
            raise TypeError("construction brief requires a pinned document")
        if self.contract is not None and self.contract.path.startswith((".construction/", ".scope/")):
            raise ValueError("scope contract cannot use reserved construction context")

    def to_bytes(self) -> bytes:
        row = {"schema": "behave.scope-proposal/1", "source": self.source.as_dict(),
               "intent": self.intent, "capabilities": list(self.capabilities), "adaptations": list(self.adaptations)}
        if self.contract is not None:
            row.update(schema="behave.scope-proposal/2", contract=self.contract.as_dict(),
                       parent=None if self.parent is None else self.parent.as_dict())
        if self.selection is not None:
            row.update(schema="behave.scope-proposal/3", selection=self.selection.as_dict())
        if self.brief is not None:
            row.update(schema="behave.scope-proposal/4", brief=self.brief.as_dict())
            if self.contract is None:
                # Analyst objective only. Builder chooses the interface; no
                # scope author/reviewer or pre-approved contract is implied.
                row.update(schema="behave.scope-proposal/5", contract=None, parent=None)
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload: bytes) -> ScopeProposal:
        value = parse_json(payload)
        objective = value.get("schema") == "behave.scope-proposal/5"
        brief = objective or value.get("schema") == "behave.scope-proposal/4"
        selected = "selection" in value
        extended = brief or selected or value.get("schema") == "behave.scope-proposal/2"
        row = fields(value, {"schema", "source", "intent", "capabilities", "adaptations"}
                     | ({"contract", "parent"} if extended else set()) | ({"selection"} if selected else set())
                     | ({"brief"} if brief else set()))
        version(row["schema"], f"behave.scope-proposal/{5 if objective else 4 if brief else 3 if selected else 2 if extended else 1}")
        result = cls(BlobRef.from_dict(row["source"]), row["intent"], tuple(array(row["capabilities"])),
                     tuple(array(row["adaptations"])), FileRef.from_dict(row["contract"]) if row.get("contract") is not None else None,
                     BlobRef.from_dict(row["parent"]) if row.get("parent") is not None else None,
                     BlobRef.from_dict(row["selection"]) if selected else None,
                     BlobRef.from_dict(row["brief"]) if brief else None)
        _canonical(payload, result.to_bytes())
        return result


@dataclass(frozen=True, slots=True)
class TaskDraft:
    scope: BlobRef
    task: TaskManifest
    workspace: BlobRef
    parent: BlobRef | None = None
    adaptations: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if type(self.scope) is not BlobRef or type(self.workspace) is not BlobRef:
            raise TypeError("draft requires scope and workspace references")
        if type(self.task) is not TaskManifest or (self.parent is not None and type(self.parent) is not BlobRef):
            raise TypeError("invalid draft task or parent")
        if self.adaptations is not None:
            _texts(self.adaptations, "task adaptations")
        public = (self.task.spec, self.task.contract, *self.task.public_files)
        sources = (self.task.reference_behavior, *(() if self.task.reference_rtl is None else (self.task.reference_rtl,)))
        all_files = (*public, *(f for source in sources for f in source.files))
        if any(f.path.startswith((".construction/", "experience/")) for f in all_files):
            raise RecordError("draft-reserved-path", "construction context cannot be exported as task files")
        private_entries = tuple(f for source in sources for f in source.files if f.path == source.entry_file)
        if any(f.path == p.path or f.content == p.content for f in public for p in private_entries):
            raise RecordError("draft-public-private-overlap", "primary implementations cannot be public reader inputs")

    def to_bytes(self) -> bytes:
        row = {"schema": "behave.task-draft/1", "scope": self.scope.as_dict(),
                               "task": self.task.as_dict(), "workspace": self.workspace.as_dict(),
                               "parent": None if self.parent is None else self.parent.as_dict()}
        if self.adaptations is not None:
            row.update(schema="behave.task-draft/2", adaptations=list(self.adaptations))
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload: bytes) -> TaskDraft:
        value = parse_json(payload)
        adapted = value.get("schema") == "behave.task-draft/2"
        row = fields(value, {"schema", "scope", "task", "workspace", "parent"}
                     | ({"adaptations"} if adapted else set()))
        version(row["schema"], f"behave.task-draft/{2 if adapted else 1}")
        result = cls(BlobRef.from_dict(row["scope"]), TaskManifest.from_bytes(canonical_json(row["task"])),
                     BlobRef.from_dict(row["workspace"]),
                     None if row["parent"] is None else BlobRef.from_dict(row["parent"]),
                     tuple(array(row["adaptations"])) if adapted else None)
        _canonical(payload, result.to_bytes())
        return result


@dataclass(frozen=True, slots=True)
class Reconstruction:
    """A reader's submitted implementation/findings, not proof of independence."""

    draft: BlobRef
    behavior: SourceArtifact | None
    findings: tuple[str, ...]
    workspace: BlobRef | None = None

    def __post_init__(self) -> None:
        if type(self.draft) is not BlobRef or (self.behavior is not None and type(self.behavior) is not SourceArtifact):
            raise TypeError("reconstruction requires a draft reference and optional Behavior artifact")
        _texts(self.findings, "findings")
        if self.workspace is not None and type(self.workspace) is not BlobRef:
            raise TypeError("reconstruction workspace must be a snapshot reference")
        if self.behavior is None and not self.findings:
            raise RecordError("reconstruction-findings-required", "a reader without an implementation must report findings")

    def to_bytes(self) -> bytes:
        row = {"schema": f"behave.spec-reconstruction/{2 if self.behavior is None else 1}",
                               "draft": self.draft.as_dict(),
                               "behavior": None if self.behavior is None else self.behavior.as_dict(),
                               "findings": list(self.findings)}
        if self.workspace is not None:
            row.update(schema="behave.spec-reconstruction/3", workspace=self.workspace.as_dict())
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload: bytes) -> Reconstruction:
        value = parse_json(payload)
        saved = value.get("schema") == "behave.spec-reconstruction/3"
        row = fields(value, {"schema", "draft", "behavior", "findings"} | ({"workspace"} if saved else set()))
        version(row["schema"], f"behave.spec-reconstruction/{3 if saved else 2 if row['behavior'] is None else 1}")
        result = cls(BlobRef.from_dict(row["draft"]),
                     None if row["behavior"] is None else SourceArtifact.from_dict(row["behavior"]),
                     tuple(array(row["findings"])), BlobRef.from_dict(row["workspace"]) if saved else None)
        _canonical(payload, result.to_bytes())
        return result


def _source_record(reader: ContentReader, reference: BlobRef):
    source = SourceRecord.from_bytes(_record(reader, reference))
    files = _files(reader, WorkspaceSnapshot(source.selected.files))
    provenance = ()
    if source.acquisition is not None:
        from .ingest.fetch_evidence import source_acquisition_closure

        provenance = source_acquisition_closure(reader, source)
    return source, files, provenance


def load_source_record(reader: ContentReader, reference: BlobRef) -> tuple[SourceRecord, tuple[FileData, ...]]:
    source, files, _ = _source_record(reader, reference)
    return source, files


def source_closure(reader: ContentReader, reference: BlobRef) -> tuple[BlobRef, ...]:
    source, _, provenance = _source_record(reader, reference)
    return tuple(dict.fromkeys((reference, *(f.content for f in source.selected.files), *provenance)))


def scope_closure(reader: ContentReader, reference: BlobRef) -> tuple[BlobRef, ...]:
    """Keep every source/intent revision, including its initial interface proposal."""
    refs, seen, source_ref, selection_ref, brief_ref = [], set(), None, None, None
    while reference is not None:
        if reference in seen:
            raise RecordError("scope-ancestry-invalid", "scope ancestry is cyclic")
        seen.add(reference)
        scope = ScopeProposal.from_bytes(_record(reader, reference))
        if source_ref is not None and source_ref != scope.source:
            raise RecordError("scope-source-mismatch", "scope revisions must retain the pinned source")
        if source_ref is not None and selection_ref != scope.selection:
            raise RecordError("scope-selection-mismatch", "scope revisions must retain the source selection")
        if source_ref is not None and brief_ref != scope.brief:
            raise RecordError("scope-brief-mismatch", "scope revisions must retain the construction objective")
        if source_ref is None:
            refs.extend(source_closure(reader, scope.source))
            source_ref = scope.source
            selection_ref = scope.selection
            brief_ref = scope.brief
            if brief_ref is not None:
                read_verified(reader, brief_ref).decode("utf-8")
                refs.append(brief_ref)
            if selection_ref is not None:
                from .selection import selection_closure

                refs.extend(selection_closure(reader, selection_ref, source=scope.source))
        refs.append(reference)
        if scope.contract is not None:
            _files(reader, WorkspaceSnapshot((scope.contract,)))
            refs.append(scope.contract.content)
        reference = scope.parent
    return tuple(dict.fromkeys(refs))


def load_scope(reader: ContentReader, reference: BlobRef) -> tuple[ScopeProposal, SourceRecord, tuple[FileData, ...]]:
    scope_closure(reader, reference)
    scope = ScopeProposal.from_bytes(_record(reader, reference))
    source, files = load_source_record(reader, scope.source)
    return scope, source, files


def load_source_artifact(reader: ContentReader, artifact: SourceArtifact) -> LoadedSource:
    return LoadedSource(artifact, _files(reader, WorkspaceSnapshot(artifact.files)))


def load_workspace(reader: ContentReader, reference: BlobRef) -> tuple[FileData, ...]:
    """Read an immutable role workspace, including non-submitted scratch files."""
    return _files(reader, WorkspaceSnapshot.from_bytes(read_verified(reader, reference)))


@dataclass(frozen=True, slots=True)
class LoadedDraft:
    record: TaskDraft
    files: tuple[FileData, ...]
    judge_policy: bytes

    def file(self, path: str) -> FileData:
        return next(f for f in self.files if f.reference.path == path)

    def source(self, artifact: SourceArtifact) -> LoadedSource:
        return LoadedSource(artifact, tuple(self.file(f.path) for f in artifact.files))

    @property
    def reader_files(self) -> tuple[FileData, ...]:
        task = self.record.task
        return tuple(self.file(f.path) for f in (task.spec, task.contract, *task.public_files))


def load_draft(reader: ContentReader, reference: BlobRef) -> LoadedDraft:
    draft = TaskDraft.from_bytes(_record(reader, reference))
    load_scope(reader, draft.scope)
    if draft.parent is not None:
        parent = TaskDraft.from_bytes(_record(reader, draft.parent))
        if parent.task.task_id != draft.task.task_id:
            raise RecordError("draft-parent-mismatch", "parent belongs to a different task")
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, draft.workspace))
    task = draft.task
    declared = task_files(task)
    if any(f not in snapshot.files for f in declared):
        raise RecordError("draft-snapshot-mismatch", "task artifacts must belong to the frozen workspace")
    if task.run_policy not in {f.content for f in snapshot.files}:
        raise RecordError("draft-snapshot-mismatch", "run policy must belong to the frozen workspace")
    return LoadedDraft(draft, _files(reader, snapshot), _record(reader, task.judge_policy))


def draft_closure(reader: ContentReader, reference: BlobRef) -> tuple[BlobRef, ...]:
    """Return the complete submitted ancestry needed for cold reconstruction."""
    refs, seen = [], set()
    current = reference
    while current is not None:
        if current in seen:
            raise RecordError("draft-ancestry-invalid", "draft ancestry is cyclic")
        seen.add(current)
        loaded = load_draft(reader, current)
        record = loaded.record
        scope, source, _ = load_scope(reader, record.scope)
        refs.extend((current, record.workspace, record.scope, scope.source, record.task.judge_policy,
                     *(f.reference.content for f in loaded.files), *(f.content for f in source.selected.files)))
        refs.extend(scope_closure(reader, record.scope))
        current = record.parent
    return tuple(dict.fromkeys(refs))
