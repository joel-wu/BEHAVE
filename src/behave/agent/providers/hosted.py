"""Fixed hosted-provider destinations and public, hash-bound launch policies.

Profiles do not choose tools or execute actions remotely. Generation parameters
are closed; retries, total deadlines, accounting and tools belong to the shared
agent runtime. Credentials are resolved only inside the trusted broker process.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from behave.core.artifacts import BlobRef
from behave.core.records import array, canonical_json, fields, metadata, text, thaw_json, version

from ..billing import TokenPrices
from ..process_model import ModelCommand
from ..spending import SpendPolicy
from .credentials import CredentialRef
from .destinations import DESTINATIONS
from .rate_limit import RequestRate

SCHEMA = "behave.hosted-provider/6"


def parameters_for(provider: str, value: object) -> MappingProxyType:
    params = thaw_json(metadata(value))
    allowed = {"max_output_tokens", "temperature", "top_p"}
    allowed |= {"openai": {"parallel_tool_calls", "reasoning_effort", "verbosity"},
                "anthropic": {"thinking", "thinking_budget_tokens", "reasoning_effort"},
                "fireworks": {"parallel_tool_calls", "reasoning_effort"},
                "deepseek": {"thinking", "reasoning_effort"},
                "openrouter": {"reasoning", "provider"}}[provider]
    limit = params.get("max_output_tokens")
    if set(params) - allowed:
        raise ValueError("invalid hosted generation parameters")
    if "max_output_tokens" in params and (type(limit) is not int or limit < 1):
        raise ValueError("max_output_tokens must be a positive integer when provided")
    for name, upper in (("temperature", 2 if provider != "anthropic" else 1), ("top_p", 1)):
        if name in params and (type(params[name]) not in (int, float)
                              or not math.isfinite(params[name]) or not 0 <= params[name] <= upper):
            raise ValueError("invalid sampling parameter")
    if "parallel_tool_calls" in params and type(params["parallel_tool_calls"]) is not bool:
        raise ValueError("parallel_tool_calls must be boolean")
    if "verbosity" in params and params["verbosity"] not in {"low", "medium", "high"}:
        raise ValueError("invalid verbosity")
    if "reasoning_effort" in params and params["reasoning_effort"] not in {
            "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("invalid reasoning effort")
    if "reasoning" in params:
        reasoning = params["reasoning"]
        if type(reasoning) is not dict or set(reasoning) - {"enabled", "exclude", "effort", "max_tokens"}:
            raise ValueError("invalid OpenRouter reasoning parameters")
        for name in ("enabled", "exclude"):
            if name in reasoning and type(reasoning[name]) is not bool:
                raise ValueError("reasoning switches must be boolean")
        if "effort" in reasoning and reasoning["effort"] not in {
                "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("invalid reasoning effort")
        if "max_tokens" in reasoning and (type(reasoning["max_tokens"]) is not int or reasoning["max_tokens"] < 1):
            raise ValueError("invalid reasoning token budget")
    if "provider" in params:
        routing = params["provider"]
        if type(routing) is not dict or set(routing) - {"order", "allow_fallbacks", "require_parameters"}:
            raise ValueError("invalid OpenRouter provider routing")
        if "order" in routing and (type(routing["order"]) is not list or not routing["order"]
                or any(type(name) is not str or not name for name in routing["order"])):
            raise ValueError("provider order must contain names")
        for name in ("allow_fallbacks", "require_parameters"):
            if name in routing and type(routing[name]) is not bool:
                raise ValueError("routing switches must be boolean")
    thinking = params.get("thinking")
    budget = params.get("thinking_budget_tokens")
    if thinking is not None and thinking not in {"enabled", "disabled", "adaptive"}:
        raise ValueError("invalid thinking mode")
    if thinking == "enabled":
        if budget is not None and (type(budget) is not int or budget < 1024 or (limit is not None and budget >= limit)):
            raise ValueError("thinking budget must fit inside output limit")
    elif budget is not None:
        raise ValueError("thinking budget requires enabled thinking")
    return metadata(params)


@dataclass(frozen=True, slots=True)
class HostedEndpoint:
    provider: str
    model: str
    runtime_revision: str
    credential: CredentialRef
    parameters: MappingProxyType = field(default_factory=lambda: metadata({}))
    prices: TokenPrices | None = None
    response_models: tuple[str, ...] = ()
    spending: SpendPolicy | None = None
    billing_tier: str | None = None
    rate_limit: RequestRate | None = None

    def __post_init__(self) -> None:
        if self.provider not in DESTINATIONS or type(self.credential) is not CredentialRef:
            raise ValueError("invalid hosted provider or credential reference")
        text(self.model, "model")
        text(self.runtime_revision, "provider deployment revision")
        if self.rate_limit is not None and type(self.rate_limit) is not RequestRate:
            raise ValueError('invalid provider request-rate policy')
        object.__setattr__(self, "parameters", parameters_for(self.provider, self.parameters))
        if self.prices is not None and (type(self.prices) is not TokenPrices or self.prices.model != self.model):
            raise ValueError("prices must name this model")
        if type(self.response_models) is not tuple or len(set(self.response_models)) != len(self.response_models):
            raise ValueError("response model aliases must be distinct")
        for model in self.response_models:
            text(model, "response model")
        if self.billing_tier is not None and self.billing_tier != {"openai": "default", "anthropic": "standard"}.get(self.provider):
            raise ValueError("only explicit standard hosted billing tiers are supported")
        if self.spending is not None:
            if (type(self.spending) is not SpendPolicy or self.prices is None
                    or self.spending.currency != self.prices.currency):
                raise ValueError("spending control requires explicit matching prices and currency")
            if self.provider == "anthropic" and (self.prices.cache_write_5m_per_million is None
                                                or self.prices.cache_write_1h_per_million is None):
                raise ValueError("Anthropic reservation requires both cache-write prices")
            if "max_output_tokens" in self.parameters:
                self.prices.reservation(self.spending.maximum_input_tokens, self.parameters["max_output_tokens"])

    @property
    def url(self) -> str:
        return DESTINATIONS[self.provider]

    def accepts_model(self, model: object) -> bool:
        return model == self.model or model in self.response_models

    def as_dict(self) -> dict:
        return {"schema": SCHEMA, "provider": self.provider, "model": self.model,
                "runtime_revision": self.runtime_revision, "credential": self.credential.as_dict(),
                "parameters": thaw_json(self.parameters),
                "prices": None if self.prices is None else self.prices.as_dict(),
                "response_models": list(self.response_models),
                "spending": None if self.spending is None else self.spending.as_dict(), "billing_tier": self.billing_tier,
                **({} if self.rate_limit is None else {'rate_limit': self.rate_limit.as_dict()})}

    @classmethod
    def from_dict(cls, value: object) -> HostedEndpoint:
        row = fields(value, {"schema", "provider", "model", "runtime_revision", "credential",
                             "parameters", "prices", "response_models", "spending", "billing_tier"}
                             | ({'rate_limit'} if type(value) is dict and 'rate_limit' in value else set()))
        version(row["schema"], SCHEMA)
        return cls(row["provider"], row["model"], row["runtime_revision"],
                   CredentialRef.from_dict(row["credential"]), row["parameters"],
                   None if row["prices"] is None else TokenPrices.from_dict(row["prices"]),
                   tuple(array(row["response_models"])),
                   None if row["spending"] is None else SpendPolicy.from_dict(row["spending"]), row["billing_tier"],
                   None if 'rate_limit' not in row else RequestRate.from_dict(row['rate_limit']))

    @property
    def identity(self) -> str:
        return BlobRef.of(canonical_json(self.as_dict())).sha256

    def command(self) -> ModelCommand:
        bootstrap = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
                     "from behave.agent.providers.hosted_worker import main; main()")
        return ModelCommand((sys.executable, "-I", "-B", "-c", bootstrap,
                             str(Path(__file__).resolve().parents[3]),
                             canonical_json(self.as_dict()).decode("utf-8")), SCHEMA, network="host")

    def model_policy(self) -> dict:
        return {"model": self.model, "provider": self.identity, "transport": self.command().identity,
                "generation_only": True}
