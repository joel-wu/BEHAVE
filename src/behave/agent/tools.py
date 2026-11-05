"""Role-supplied capabilities. Final actions are control, never guessed from text."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType

from behave.agent.prompting import render
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, fields, metadata, text, thaw_json

from .journal import Event
from .workspace import Workspace

_INVOCATION: ContextVar[Event | None] = ContextVar("behave_tool_invocation", default=None)


@contextmanager
def tool_invocation(event: Event):
    """The durable request authorizing this handler, never model-supplied metadata."""
    token = _INVOCATION.set(event)
    try:
        yield
    finally:
        _INVOCATION.reset(token)


def current_invocation() -> Event | None:
    return _INVOCATION.get()


class ToolUnavailable(RuntimeError):
    """No tool outcome; retain evidence and interrupt rather than score a task."""

    def __init__(self, code: str, *, artifacts: tuple[BlobRef, ...] = ()):
        self.code = text(code, "tool interruption code")
        if type(artifacts) is not tuple or any(type(ref) is not BlobRef for ref in artifacts):
            raise TypeError("tool interruption artifacts require BlobRef values")
        self.artifacts = artifacts
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: MappingProxyType
    artifacts: tuple[BlobRef, ...] = ()
    terminal: bool = False

    def __post_init__(self) -> None:
        try:
            content = metadata(self.content)
        except RecordError as error:
            # This is the handler's output, not the model's tool arguments.
            # Do not let the loop turn an invalid result into input feedback.
            raise TypeError("tool result content must be a JSON object") from error
        object.__setattr__(self, "content", content)
        if type(self.artifacts) is not tuple or any(type(ref) is not BlobRef for ref in self.artifacts):
            raise TypeError("tool artifacts require a tuple of BlobRef")
        if type(self.terminal) is not bool:
            raise TypeError("terminal must be Boolean")


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: MappingProxyType
    handler: Callable[[Workspace, dict], ToolResult]
    revision: str
    terminal: bool = False

    def __post_init__(self) -> None:
        text(self.name, "tool name")
        if type(self.description) is not str:
            raise TypeError("tool description must be text")
        text(self.revision, "tool implementation revision")
        object.__setattr__(self, "parameters", metadata(self.parameters))
        if not callable(self.handler) or type(self.terminal) is not bool:
            raise TypeError("invalid tool handler or terminal flag")

    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": thaw_json(self.parameters),
                "revision": self.revision, "terminal": self.terminal}


def object_schema(properties: dict, required: tuple[str, ...] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties if required is None else required), "additionalProperties": False}


def file_tools() -> tuple[Tool, ...]:
    def read(workspace: Workspace, arguments: dict) -> ToolResult:
        row = fields(arguments, {"path"})
        data = workspace.read(row["path"])
        try:
            content = data.decode("utf-8")
        except UnicodeError as error:
            raise RecordError("workspace-file-not-text", "read_file requires a UTF-8 file") from error
        return ToolResult({"path": row["path"], "content": content})

    def write(workspace: Workspace, arguments: dict) -> ToolResult:
        row = fields(arguments, {"path", "content"})
        if type(row["content"]) is not str:
            raise RecordError("workspace-content-not-text", "write_file requires UTF-8 text")
        workspace.write(row["path"], row["content"].encode("utf-8"))
        return ToolResult({"written": row["path"]})

    def delete(workspace: Workspace, arguments: dict) -> ToolResult:
        row = fields(arguments, {"path"})
        workspace.delete(row["path"])
        return ToolResult({"deleted": row["path"]})

    def listing(workspace: Workspace, arguments: dict) -> ToolResult:
        fields(arguments, set())
        files = ([{"path": path} for path in workspace.paths()] if workspace.local
                 else [file.as_dict() for file in workspace.snapshot().files])
        return ToolResult({"files": files})

    path = {"path": {"type": "string"}}
    return (
        Tool("read_file", render("shared/tools/files/read_file"), object_schema(path), read, "files/1"),
        Tool("write_file", render("shared/tools/files/write_file"),
             object_schema({**path, "content": {"type": "string"}}), write, "files/1"),
        Tool("delete_file", render("shared/tools/files/delete_file"), object_schema(path), delete, "files/1"),
        Tool("list_files", render("shared/tools/files/list_files"),
             object_schema({}), listing, "files/1"),
    )
