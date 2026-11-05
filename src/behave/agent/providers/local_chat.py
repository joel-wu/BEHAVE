"""Public, hash-bound configuration for a local Chat Completions endpoint.

This profile supports non-streaming text/function calls and reasoning_content.
It preserves the server's reasoning/reasoning_content spelling and targets
explicitly provisioned SGLang/vLLM-compatible servers. The runtime
revision identifies the operator's model/weights/tokenizer/server lock; it is
not a cryptographic attestation of a server. Hosted credentials and other wire
protocols need separate profiles, not arbitrary headers in this public record.
"""

from __future__ import annotations

import ipaddress
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, fields, metadata, text, thaw_json, version

from ..billing import TokenPrices
from ..model import ModelRequest
from ..process_model import ModelCommand
from .native_tokens import NativeChatTokens

SCHEMA = "behave.local-chat/5"


def _local_url(url: str) -> None:
    text(url, "endpoint URL")
    parts = urlsplit(url)
    if (parts.scheme != "http" or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.path != "/v1/chat/completions"
            or not parts.hostname or parts.port is None or not 1 <= parts.port <= 65535
            or not ipaddress.ip_address(parts.hostname).is_loopback):
        raise ValueError("local chat requires a numeric loopback HTTP URL with an explicit port")


@dataclass(frozen=True, slots=True)
class LocalChatEndpoint:
    url: str
    model: str
    runtime_revision: str
    # An omitted output cap lets the local server use the remaining context.
    parameters: MappingProxyType = field(default_factory=lambda: metadata({}))
    prices: TokenPrices | None = None
    tokens: NativeChatTokens | None = None
    routing: str = "none"
    worker_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.routing not in ("none", "attempt"):
            raise ValueError("local chat routing must be none or attempt")
        if self.tokens is not None and type(self.tokens) is not NativeChatTokens:
            raise TypeError("native token capture requires an explicit pinned profile")
        _local_url(self.url)
        if type(self.worker_urls) is not tuple or any(type(url) is not str for url in self.worker_urls):
            raise ValueError("worker URLs must be a tuple of strings")
        if self.worker_urls:
            if self.routing != "attempt" or len(set(self.worker_urls)) != len(self.worker_urls):
                raise ValueError("direct workers require attempt affinity and distinct URLs")
            for url in self.worker_urls:
                _local_url(url)
            object.__setattr__(self, "worker_urls", tuple(sorted(self.worker_urls)))
        text(self.model, "served model")
        text(self.runtime_revision, "runtime revision")
        parameters = thaw_json(metadata(self.parameters))
        allowed = {"max_tokens", "temperature", "top_p", "top_k", "min_p", "repetition_penalty",
                   "presence_penalty", "frequency_penalty", "seed", "parallel_tool_calls",
                   "chat_template_kwargs", "reasoning_effort"}
        if set(parameters) - allowed:
            raise ValueError("unsupported local-chat generation parameters")
        if "max_tokens" in parameters and (type(parameters["max_tokens"]) is not int or parameters["max_tokens"] < 1):
            raise ValueError("max_tokens must be a positive integer")
        if "reasoning_effort" in parameters:
            text(parameters["reasoning_effort"], "reasoning effort")
        # JSON validation above already excludes NaN/Infinity. These numeric
        # extensions retain the pinned server's range rules, without coercion.
        for name in ("min_p", "repetition_penalty", "presence_penalty", "frequency_penalty"):
            if name in parameters and type(parameters[name]) not in (int, float):
                raise ValueError(f"{name} must be a number")
        for name, minimum, maximum in (("temperature", 0, 2), ("top_p", 0, 1)):
            if name in parameters:
                value = parameters[name]
                if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                    raise ValueError(f"invalid {name}")
        if "top_k" in parameters and (type(parameters["top_k"]) is not int
                or not (parameters["top_k"] == -1 or parameters["top_k"] >= 1)):
            raise ValueError("top_k must be -1 (disabled) or a positive integer")
        if "seed" in parameters and (type(parameters["seed"]) is not int or parameters["seed"] < 0):
            raise ValueError("invalid seed")
        if "parallel_tool_calls" in parameters and type(parameters["parallel_tool_calls"]) is not bool:
            raise ValueError("invalid parallel_tool_calls")
        if "chat_template_kwargs" in parameters and type(parameters["chat_template_kwargs"]) is not dict:
            raise ValueError("chat_template_kwargs must be an object")
        object.__setattr__(self, "parameters", metadata(parameters))
        if self.prices is not None and (type(self.prices) is not TokenPrices or self.prices.model != self.model):
            raise ValueError("price snapshot must name the served model")

    def as_dict(self) -> dict:
        row = {"schema": SCHEMA, "url": self.url, "model": self.model,
                "runtime_revision": self.runtime_revision, "parameters": thaw_json(self.parameters),
                "prices": None if self.prices is None else self.prices.as_dict()}
        if self.tokens is not None:
            row.update(schema="behave.local-chat/6", tokens=self.tokens.as_dict())
        if self.routing != "none":
            row.update(schema="behave.local-chat/7", routing=self.routing,
                       tokens=None if self.tokens is None else self.tokens.as_dict())
        if self.worker_urls:
            row.update(schema="behave.local-chat/8", worker_urls=list(self.worker_urls))
        return row

    @classmethod
    def from_dict(cls, value: object) -> LocalChatEndpoint:
        pooled = type(value) is dict and value.get("schema") == "behave.local-chat/8"
        routed = pooled or (type(value) is dict and value.get("schema") == "behave.local-chat/7")
        extended = routed or (type(value) is dict and value.get("schema") == "behave.local-chat/6")
        row = fields(value, {"schema", "url", "model", "runtime_revision", "parameters", "prices"}
                     | ({"tokens"} if extended else set()) | ({"routing"} if routed else set())
                     | ({"worker_urls"} if pooled else set()))
        version(row["schema"], "behave.local-chat/8" if pooled else "behave.local-chat/7" if routed else "behave.local-chat/6" if extended else SCHEMA)
        if pooled and (type(row["worker_urls"]) is not list or not row["worker_urls"]):
            raise ValueError("pooled profiles require explicit worker URLs")
        if routed and row["routing"] != "attempt":
            raise ValueError("routed profiles must select attempt affinity")
        if extended and not routed and row["tokens"] is None:
            raise ValueError("native profiles require token capture")
        return cls(row["url"], row["model"], row["runtime_revision"], row["parameters"],
                   None if row["prices"] is None else TokenPrices.from_dict(row["prices"]),
                   NativeChatTokens.from_dict(row["tokens"]) if extended and row["tokens"] is not None else None,
                   row["routing"] if routed else "none", tuple(row["worker_urls"]) if pooled else ())

    def request_url(self, request: ModelRequest, *, worker_url: str | None = None) -> str:
        """Use a coordinator assignment without rewriting native JSON extensions.

        Shared ModelRelay balances complete attempts and supplies worker_url.
        Unsupervised standalone calls retain their historical fixed-set affinity.
        The approved pool remains part of the endpoint identity in both cases.
        """
        if worker_url is not None:
            if worker_url not in self.worker_urls:
                raise ValueError("assigned engine is not in the endpoint's worker pool")
            return worker_url
        if not self.worker_urls:
            return self.url
        key = self.headers(request)["X-SMG-Routing-Key"]
        return max(self.worker_urls, key=lambda url: BlobRef.of(
            canonical_json({"attempt": key, "worker": url})).sha256)

    def headers(self, request: ModelRequest) -> dict[str, str]:
        """SMG consistent-hash affinity; never bind a conversation to a GPU ordinal.

        Loop/relay call IDs are ``attempt_id:turn:dispatch``. Validate that
        contract before removing the changing suffix. The endpoint identity
        namespaces sessions across policy/weight revisions. Cache reuse still
        depends on the router policy, live worker set and available KV memory.
        """
        if self.routing == "none":
            return {}
        suffix = f":{request.turn}:{request.dispatch}"
        if not request.call_id.endswith(suffix) or request.call_id == suffix:
            raise ValueError("attempt routing requires a canonical model call id")
        attempt = request.model_policy.get("routing_key", request.call_id[:-len(suffix)])
        text(attempt, "rollout routing key")
        key = BlobRef.of(canonical_json({"endpoint": self.identity, "attempt": attempt})).sha256
        return {"X-SMG-Routing-Key": key}

    @property
    def identity(self) -> str:
        return BlobRef.of(canonical_json(self.as_dict())).sha256

    def command(self, *, python: str | None = None, package: str | None = None) -> ModelCommand:
        bootstrap = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
                     "from behave.agent.providers.chat_worker import main; main()")
        return ModelCommand(
            (sys.executable if python is None else python, "-I", "-B", "-c", bootstrap,
             str(Path(__file__).resolve().parents[3]) if package is None else package,
             canonical_json(self.as_dict()).decode("utf-8")),
            self.as_dict()["schema"], network="host",
        )

    def model_policy(self, *, command: ModelCommand | None = None) -> dict:
        return {"model": self.model, "provider": self.identity,
                "transport": (self.command() if command is None else command).identity,
                "generation_only": True,
                **({"native_continuation": True} if self.tokens is not None
                   and self.tokens.continuation is not None else {})}

    def accepts_model(self, model: object) -> bool:
        return model == self.model
