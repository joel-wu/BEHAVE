"""Shared, role-neutral attempt runtime; hardware policy lives in task_execution.

Command profiles are imported separately from ``behave.agent.commands`` so a
record/trajectory consumer does not initialize process-isolation dependencies.
The execution loop is loaded only when its public ``run_attempt`` is requested.
"""

from .attempt import AttemptInterrupted, AttemptResult, AttemptSpec
from .journal import AttemptJournal
from .lifetime import AttemptControl
from .model import Charge, ModelBackend, ModelFailure, ModelReply, ModelRequest, ToolCall
from .publication import publish_attempt
from .tools import Tool, ToolResult, ToolUnavailable, file_tools
from .trajectory import RecordedTurn, Trajectory, trajectory
from .workspace import Workspace, WorkspaceSnapshot

__all__ = [
    "AttemptControl", "AttemptInterrupted", "AttemptJournal", "AttemptResult", "AttemptSpec", "Charge",
    "ModelBackend", "ModelFailure", "ModelReply", "ModelRequest", "RecordedTurn", "Tool", "ToolCall",
    "ToolResult", "ToolUnavailable", "Trajectory", "Workspace", "WorkspaceSnapshot",
    "file_tools", "publish_attempt", "run_attempt", "trajectory",
]


def __getattr__(name):
    if name == "run_attempt":
        from .loop import run_attempt

        globals()[name] = run_attempt
        return run_attempt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
