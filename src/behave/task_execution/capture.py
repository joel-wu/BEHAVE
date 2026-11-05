"""Freeze explicit solver selections from one workspace revision, without judging."""

from __future__ import annotations

from dataclasses import dataclass

from behave.agent.prompting import render
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.workspace import Workspace, WorkspaceSnapshot
from behave.core.artifacts import BlobRef, ContentReader, FileRef, SourceArtifact, file_set, read_verified
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, version
from behave.data.published import PublicTaskInputs

from .solver_settings import guide_names, prompt_path, submission_components
from .submission import ArtifactSelection, SubmissionRequest


@dataclass(frozen=True, slots=True)
class CapturedComponent:
    selection: ArtifactSelection | None
    files: tuple[FileRef, ...]
    missing: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", file_set(self.files))
        if type(self.missing) is not tuple or any(type(path) is not str for path in self.missing):
            raise TypeError("missing files must be a tuple of paths")
        names = tuple(file.path for file in self.files)
        if self.selection is None:
            valid = not names and not self.missing
        elif type(self.selection) is ArtifactSelection:
            valid = tuple(sorted((*names, *self.missing))) == self.selection.files
        else:
            raise TypeError("component requires ArtifactSelection or None")
        if not valid:
            raise RecordError("submission-capture-invalid", "captured and missing files must partition the selection")

    @property
    def artifact(self) -> SourceArtifact | None:
        if self.selection is None or self.missing:
            return None
        return SourceArtifact(self.files, self.selection.entry_file, self.selection.entry_symbol)

    def as_dict(self) -> dict:
        return {"selection": None if self.selection is None else self.selection.as_dict(),
                "files": [file.as_dict() for file in self.files], "missing": list(self.missing)}

    @classmethod
    def from_dict(cls, value: object) -> CapturedComponent:
        row = fields(value, {"selection", "files", "missing"})
        return cls(None if row["selection"] is None else ArtifactSelection.from_dict(row["selection"]),
                   tuple(FileRef.from_dict(file) for file in array(row["files"])), tuple(array(row["missing"])))


@dataclass(frozen=True, slots=True)
class FrozenSubmission:
    task_id: str
    release: BlobRef
    revision: BlobRef
    workspace: BlobRef
    behavior: CapturedComponent
    rtl: CapturedComponent
    testbench: CapturedComponent
    setting: str = "joint"

    def __post_init__(self) -> None:
        guide_names(self.setting)
        text(self.task_id, "task id")
        if any(type(ref) is not BlobRef for ref in (self.release, self.revision, self.workspace)):
            raise TypeError("submission identities require BlobRef")
        if any(type(component) is not CapturedComponent for component in (self.behavior, self.rtl, self.testbench)):
            raise TypeError("submission components require CapturedComponent")
        requested = submission_components(self.setting)
        if any(getattr(self, name).selection is not None for name in ("behavior", "rtl", "testbench")
               if name not in requested):
            raise ValueError("submission selects an artifact not requested by this setting")

    def to_bytes(self) -> bytes:
        return canonical_json({
            "schema": "behave.frozen-submission/1", "task_id": self.task_id,
            "release": self.release.as_dict(), "revision": self.revision.as_dict(),
            "workspace": self.workspace.as_dict(), "behavior": self.behavior.as_dict(),
            "rtl": self.rtl.as_dict(), "testbench": self.testbench.as_dict(),
            **({"setting": self.setting} if self.setting != "joint" else {}),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> FrozenSubmission:
        row = parse_json(data)
        setting = row.pop("setting", "joint")
        row = fields(row, {"schema", "task_id", "release", "revision", "workspace",
                                       "behavior", "rtl", "testbench"})
        version(row["schema"], "behave.frozen-submission/1")
        result = cls(row["task_id"], *(BlobRef.from_dict(row[name]) for name in ("release", "revision", "workspace")),
                     *(CapturedComponent.from_dict(row[name]) for name in ("behavior", "rtl", "testbench")),
                     setting=setting)
        if result.to_bytes() != data:
            raise RecordError("submission-not-canonical", "frozen submission must be canonical")
        return result


def _component(snapshot: WorkspaceSnapshot, selection: ArtifactSelection | None) -> CapturedComponent:
    paths = () if selection is None else selection.files
    files = {path: found for path in paths if (found := snapshot.file(path)) is not None}
    return CapturedComponent(selection, tuple(files[path] for path in paths if path in files),
                             tuple(path for path in paths if path not in files))


def load_submission(reader: ContentReader, ref: BlobRef) -> FrozenSubmission:
    submission = FrozenSubmission.from_bytes(read_verified(reader, ref))
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, submission.workspace))
    for file in snapshot.files:
        read_verified(reader, file.content)
    for component in (submission.behavior, submission.rtl, submission.testbench):
        if component != _component(snapshot, component.selection):
            raise RecordError("submission-workspace-mismatch", "submission does not describe its frozen workspace")
    return submission


def submission_tool(task: PublicTaskInputs, *, setting: str = "joint") -> Tool:
    if type(task) is not PublicTaskInputs:
        raise TypeError("solver submit requires the public task view")

    def capture(workspace: Workspace, arguments: dict) -> ToolResult:
        selections = fields(arguments, set(properties))
        request = SubmissionRequest.from_bytes(canonical_json({
            "schema": SubmissionRequest.SCHEMA,
            "behavior": None, "rtl": None, "testbench": None, **selections,
        }))
        workspace.archive(tuple(path for selection in (request.behavior, request.rtl, request.testbench)
                                if selection is not None for path in selection.files))
        root, closure = workspace.checkpoint()
        snapshot = workspace.snapshot()
        submission = FrozenSubmission(task.task_id, task.release, task.revision, root,
                                      *(_component(snapshot, selection) for selection in (
                                          request.behavior, request.rtl, request.testbench)), setting=setting)
        ref = workspace.store.put(submission.to_bytes())
        return ToolResult({"submission": ref.as_dict()}, (*closure, ref), terminal=True)

    selection = object_schema({"files": {"type": "array", "items": {"type": "string"}},
                               "entry_file": {"type": "string"}, "entry_symbol": {"type": "string"}})
    nullable = {"anyOf": [selection, {"type": "null"}]}
    properties = {name: nullable if setting == "joint" else selection
                  for name in submission_components(setting)}
    identity = f"solver-submit/2:{task.release.sha256}:{task.revision.sha256}"
    if setting != "joint":
        identity += f":{setting}"
    return Tool("submit", render(prompt_path(setting, "submit")), object_schema(properties),
                capture, identity, terminal=True)
