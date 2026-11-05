"""Pinned role policy and runtime outcomes; no task score or optimizer policy."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from types import MappingProxyType

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, relative_paths
from behave.core.records import array, fields, metadata, text, thaw_json


@dataclass(frozen=True, slots=True)
class AttemptSpec:
    attempt_id: str
    role: str
    model_policy: MappingProxyType
    max_turns: int = defaults.AGENT_MAX_TURNS
    max_retries: int = defaults.AGENT_MAX_RETRIES
    request_timeout_seconds: int | None = defaults.AGENT_REQUEST_SECONDS
    read_only_paths: tuple[str, ...] = ()
    retry_uncertain_generation: bool = False
    context_tokens: int | None = defaults.AGENT_CONTEXT_TOKENS
    context_reset_message: str | None = None

    def __post_init__(self) -> None:
        text(self.attempt_id, "attempt id")
        text(self.role, "role")
        object.__setattr__(self, "model_policy", metadata(self.model_policy))
        if type(self.max_turns) is not int or self.max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        if self.context_tokens is not None and (type(self.context_tokens) is not int or self.context_tokens < 1):
            raise ValueError("context_tokens must be a positive integer or None")
        if self.context_reset_message is not None:
            text(self.context_reset_message, "context reset message")
        if self.request_timeout_seconds is not None and (
                type(self.request_timeout_seconds) is not int or self.request_timeout_seconds < 1):
            raise ValueError("request_timeout_seconds must be a positive integer or None")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("max_retries must be a nonnegative integer")
        if type(self.retry_uncertain_generation) is not bool:
            raise TypeError("retry_uncertain_generation must be a boolean")
        if self.retry_uncertain_generation and self.model_policy.get("generation_only") is not True:
            raise ValueError("uncertain retries require a declared generation-only profile")
        object.__setattr__(self, "read_only_paths", relative_paths(self.read_only_paths))

    def as_dict(self) -> dict:
        return {"attempt_id": self.attempt_id, "role": self.role, "model_policy": thaw_json(self.model_policy),
                "max_turns": self.max_turns, "max_retries": self.max_retries,
                "request_timeout_seconds": self.request_timeout_seconds,
                "retry_uncertain_generation": self.retry_uncertain_generation,
                "read_only_paths": list(self.read_only_paths),
                **({"context_tokens": self.context_tokens} if self.context_tokens is not None else {}),
                **({"context_reset_message": self.context_reset_message}
                   if self.context_reset_message is not None else {})}

    @classmethod
    def from_dict(cls, value: object) -> AttemptSpec:
        # Old stored configurations remain readable. New attempts have no
        # cumulative wall-time budget and never serialize the retired field.
        names = {item.name for item in dataclass_fields(cls)}
        if isinstance(value, dict) and "context_tokens" not in value:
            names.remove("context_tokens")
        if isinstance(value, dict) and "context_reset_message" not in value:
            names.remove("context_reset_message")
        if isinstance(value, dict) and "wall_timeout_seconds" in value:
            names.add("wall_timeout_seconds")
        row = dict(fields(value, names))
        row.pop("wall_timeout_seconds", None)
        # A historical request without a total budget must replay unchanged.
        row.setdefault("context_tokens", None)
        return cls(**{**row, "read_only_paths": tuple(array(row["read_only_paths"]))})


@dataclass(frozen=True, slots=True)
class AttemptResult:
    reason: str
    turns: int
    workspace: BlobRef
    final_action: MappingProxyType | None
    head: BlobRef


class AttemptInterrupted(RuntimeError):
    """No task outcome. The journal retains settled evidence and unresolved calls."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
