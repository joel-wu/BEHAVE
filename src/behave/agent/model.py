"""Provider-neutral broker records. No SDK, credentials or training algorithm.

Adapters must enforce the request timeout in their transport. A response keeps
the provider's raw bytes/usage/token data as well as its normalized tool calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Protocol

from behave.core.records import RecordError, array, canonical_json, fields, metadata, parse_json, text, thaw_json


@dataclass(frozen=True, slots=True)
class Charge:
    amount: str
    currency: str
    pricing_revision: str

    def __post_init__(self) -> None:
        try:
            amount = Decimal(self.amount) if type(self.amount) is str else Decimal("NaN")
        except InvalidOperation as error:
            raise ValueError("invalid charge") from error
        if not amount.is_finite() or amount < 0:
            raise ValueError("charge must be a nonnegative decimal string")
        text(self.currency, "currency")
        text(self.pricing_revision, "pricing revision")

    def as_dict(self) -> dict:
        return {"amount": self.amount, "currency": self.currency, "pricing_revision": self.pricing_revision}


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: MappingProxyType
    raw_arguments: str | None = None
    argument_error: str | None = None

    def __post_init__(self) -> None:
        text(self.call_id, "tool call id")
        text(self.name, "tool name")
        object.__setattr__(self, "arguments", metadata(self.arguments))
        if self.raw_arguments is not None and type(self.raw_arguments) is not str:
            raise TypeError("raw tool arguments must be text")
        if self.argument_error is not None:
            text(self.argument_error, "tool argument error")
            if self.raw_arguments is None or self.arguments:
                raise ValueError("invalid arguments require raw text and no executable arguments")
        elif self.raw_arguments is not None:
            decoded = parse_json(self.raw_arguments.encode("utf-8"))
            if canonical_json(decoded) != canonical_json(self.arguments):
                raise ValueError("wire and executable tool arguments disagree")

    def as_dict(self) -> dict:
        return {"call_id": self.call_id, "name": self.name, "arguments": thaw_json(self.arguments),
                "raw_arguments": self.raw_arguments, "argument_error": self.argument_error}

    @classmethod
    def from_dict(cls, value: object) -> ToolCall:
        return cls(**fields(value, {"call_id", "name", "arguments", "raw_arguments", "argument_error"}))


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: str
    calls: tuple[ToolCall, ...] = ()
    usage: MappingProxyType = field(default_factory=lambda: metadata({}))
    raw: bytes = b""
    charge: Charge | None = None
    token_data: MappingProxyType | None = None
    training_exclusion: str | None = None
    finish_reason: str | None = None
    continuation: MappingProxyType = field(default_factory=lambda: metadata({}))

    def __post_init__(self) -> None:
        if type(self.content) is not str or type(self.raw) is not bytes:
            raise TypeError("reply content/raw must be str/bytes")
        if type(self.calls) is not tuple or any(type(call) is not ToolCall for call in self.calls):
            raise TypeError("reply calls require a tuple of ToolCall")
        if len({call.call_id for call in self.calls}) != len(self.calls):
            raise RecordError("model-call-id-duplicate", "tool call IDs must be unique within a response")
        object.__setattr__(self, "usage", metadata(self.usage))
        if self.charge is not None and type(self.charge) is not Charge:
            raise TypeError("reply charge requires Charge")
        if self.token_data is not None:
            object.__setattr__(self, "token_data", metadata(self.token_data))
        if self.training_exclusion is not None:
            text(self.training_exclusion, "training exclusion")
        if self.finish_reason not in {None, "stop", "tool_calls", "length", "context_limit"}:
            raise ValueError("unsupported model finish reason")
        if (self.finish_reason == "stop" and self.calls) or (self.finish_reason == "tool_calls" and not self.calls):
            raise ValueError("finish reason disagrees with tool calls")
        # A generation boundary is not a training-policy decision. Actual
        # sampled tokens may still learn from the completed rollout's reward.
        # Historical explicit exclusions remain representable for replay.
        if self.finish_reason in {"length", "context_limit"} and any(call.argument_error is None for call in self.calls):
            raise ValueError("a truncated reply cannot authorize tool execution")
        object.__setattr__(self, "continuation", metadata(self.continuation))

    def message(self) -> dict:
        return {"role": "assistant", "content": self.content, "calls": [call.as_dict() for call in self.calls],
                "continuation": thaw_json(self.continuation)}

    def as_dict(self) -> dict:
        # Raw bytes are stored separately so arbitrary provider output is lossless.
        return {
            "content": self.content, "calls": [call.as_dict() for call in self.calls],
            "usage": thaw_json(self.usage), "charge": None if self.charge is None else self.charge.as_dict(),
            "token_data": None if self.token_data is None else thaw_json(self.token_data),
            "training_exclusion": self.training_exclusion,
            "finish_reason": self.finish_reason, "continuation": thaw_json(self.continuation),
        }

    @classmethod
    def from_dict(cls, value: object, *, raw: bytes) -> ModelReply:
        row = fields(value, {"content", "calls", "usage", "charge", "token_data", "training_exclusion",
                             "finish_reason", "continuation"})
        charge = None if row["charge"] is None else Charge(**fields(
            row["charge"], {"amount", "currency", "pricing_revision"}))
        return cls(row["content"], tuple(ToolCall.from_dict(call) for call in array(row["calls"])),
                   row["usage"], raw, charge, row["token_data"], row["training_exclusion"],
                   row["finish_reason"], row["continuation"])


@dataclass(frozen=True, slots=True)
class ModelFailure:
    """Settled transport evidence; uncertainty is independent of retry policy."""

    kind: str
    code: str
    usage: MappingProxyType = field(default_factory=lambda: metadata({}))
    raw: bytes = b""
    charge: Charge | None = None
    retry_after_seconds: int = 0

    def __post_init__(self) -> None:
        if self.kind not in {"retryable", "unknown", "context_limit", "permanent"}:
            raise ValueError("unknown model failure kind")
        text(self.code, "failure code")
        object.__setattr__(self, "usage", metadata(self.usage))
        if type(self.raw) is not bytes or (self.charge is not None and type(self.charge) is not Charge):
            raise TypeError("invalid model failure evidence")
        if type(self.retry_after_seconds) is not int or self.retry_after_seconds < 0:
            raise ValueError("retry delay must be a nonnegative integer")
        try:
            float(self.retry_after_seconds)  # The execution clock uses native floating-point seconds.
        except OverflowError as error:
            raise ValueError("retry delay exceeds native clock representation") from error
        if self.retry_after_seconds and self.kind not in {"retryable", "unknown"}:
            raise ValueError("only transient or uncertain outcomes can request backoff")

    def as_dict(self) -> dict:
        return {"kind": self.kind, "code": self.code, "usage": thaw_json(self.usage),
                "charge": None if self.charge is None else self.charge.as_dict(),
                "retry_after_seconds": self.retry_after_seconds}

    @classmethod
    def from_dict(cls, value: object, *, raw: bytes) -> ModelFailure:
        row = fields(value, {"kind", "code", "usage", "charge", "retry_after_seconds"})
        charge = None if row["charge"] is None else Charge(**fields(
            row["charge"], {"amount", "currency", "pricing_revision"}))
        return cls(row["kind"], row["code"], row["usage"], raw, charge, row["retry_after_seconds"])


@dataclass(frozen=True, slots=True)
class ModelRequest:
    call_id: str
    turn: int
    dispatch: int
    messages: tuple[MappingProxyType, ...]
    tools: tuple[MappingProxyType, ...]
    model_policy: MappingProxyType
    timeout_seconds: int | None
    context_tokens: int | None = None
    native_prefix: MappingProxyType | None = None

    def __post_init__(self) -> None:
        text(self.call_id, "model call id")
        for name in ("turn", "dispatch"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"invalid model request {name}")
        if self.timeout_seconds is not None and (type(self.timeout_seconds) is not int or self.timeout_seconds < 1):
            raise ValueError("model request timeout_seconds must be a positive integer or None")
        if self.context_tokens is not None and (type(self.context_tokens) is not int or self.context_tokens < 1):
            raise ValueError("model request context_tokens must be a positive integer or None")
        object.__setattr__(self, "messages", tuple(metadata(item) for item in self.messages))
        object.__setattr__(self, "tools", tuple(metadata(item) for item in self.tools))
        object.__setattr__(self, "model_policy", metadata(self.model_policy))
        if self.native_prefix is not None:
            object.__setattr__(self, "native_prefix", metadata(self.native_prefix))

    def as_dict(self) -> dict:
        return {"call_id": self.call_id, "turn": self.turn, "dispatch": self.dispatch,
                "messages": [thaw_json(message) for message in self.messages],
                "tools": [thaw_json(tool) for tool in self.tools], "model_policy": thaw_json(self.model_policy),
                "timeout_seconds": self.timeout_seconds,
                **({"context_tokens": self.context_tokens} if self.context_tokens is not None else {}),
                **({"native_prefix": thaw_json(self.native_prefix)} if self.native_prefix is not None else {})}

    @classmethod
    def from_dict(cls, value: object) -> ModelRequest:
        names = {"call_id", "turn", "dispatch", "messages", "tools", "model_policy", "timeout_seconds"}
        if isinstance(value, dict) and "context_tokens" in value:
            names.add("context_tokens")
        if isinstance(value, dict) and "native_prefix" in value:
            names.add("native_prefix")
        row = fields(value, names)
        return cls(row["call_id"], row["turn"], row["dispatch"], tuple(array(row["messages"])),
                   tuple(array(row["tools"])), row["model_policy"], row["timeout_seconds"], row.get("context_tokens"),
                   row.get("native_prefix"))


class ModelBackend(Protocol):
    def generate(self, request: ModelRequest) -> ModelReply | ModelFailure: ...
