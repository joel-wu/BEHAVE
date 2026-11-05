"""Trusted credential-owning broker. One remote call, no workspace or tools."""

from __future__ import annotations

import sys
from dataclasses import replace

from behave.core.records import parse_json, thaw_json

from ..model import ModelFailure, ModelReply, ModelRequest
from ..process_model import encode_model_outcome
from ..spending import SpendAccount
from . import chat_wire, messages_wire, responses_wire
from .chat_prefill import count_prompt
from .context import count_input, limit_output, record_budget
from .credentials import load_credential, redact_reflection
from .hosted import HostedEndpoint
from .http_transport import post_json
from .rate_limit import wait_for_request


def request_body(endpoint: HostedEndpoint, request: ModelRequest) -> bytes:
    if endpoint.provider == "openai":
        return responses_wire.request_body(endpoint, request)
    if endpoint.provider == "anthropic":
        return messages_wire.request_body(endpoint, request)
    params = thaw_json(endpoint.parameters)
    if "max_output_tokens" in params:
        params["max_tokens"] = params.pop("max_output_tokens")
    if endpoint.provider == "deepseek" and "thinking" in params:
        params["thinking"] = {"type": params["thinking"]}
    return chat_wire.request_body(endpoint, request, parameters=params)


def response(endpoint, status, raw, *, retry_after=None):
    if endpoint.provider == "openai":
        return responses_wire.response(endpoint, status, raw, retry_after=retry_after)
    if endpoint.provider == "anthropic":
        return messages_wire.response(endpoint, status, raw, retry_after=retry_after)
    return chat_wire.response(endpoint, status, raw, retry_after=retry_after, provider=endpoint.provider)


def exchange(endpoint: HostedEndpoint, request: ModelRequest) -> ModelReply | ModelFailure:
    try:
        packet = request_body(endpoint, request)
    except (ValueError, TypeError, KeyError, AttributeError):
        return ModelFailure("permanent", "provider-request-invalid")
    try:
        secret = load_credential(endpoint.credential)
    except (OSError, ValueError, UnicodeError):
        return ModelFailure("permanent", "provider-credential-unavailable")
    budget = None
    prefill = None

    def with_count(outcome):
        if prefill is None:
            return outcome
        return replace(outcome, usage={**thaw_json(outcome.usage), "input_count": {
            "provider_response": parse_json(prefill.raw),
            "charge": None if prefill.charge is None else prefill.charge.as_dict()}})

    if request.context_tokens is not None:
        body = parse_json(packet)
        if endpoint.provider == "openai":
            keys = ("model", "input", "instructions", "tools", "tool_choice", "parallel_tool_calls",
                    "text", "reasoning", "truncation")
            count_url, output_field = endpoint.url + "/input_tokens", "max_output_tokens"
        elif endpoint.provider == "anthropic":
            keys = ("model", "messages", "system", "tools", "tool_choice", "thinking", "output_config")
            count_url, output_field = endpoint.url + "/count_tokens", "max_tokens"
        else:
            prefill = count_prompt(endpoint, request, body, secret)
            if isinstance(prefill, ModelFailure):
                return prefill
            counted = prefill.usage["normalized"]["input_tokens"]
            output_field = "max_tokens"
        if endpoint.provider in {"openai", "anthropic"}:
            wait_for_request(endpoint)
            counted = count_input(count_url, {key: body[key] for key in keys if key in body},
                                  headers=secret.authorization(endpoint.provider), timeout=request.timeout_seconds,
                                  provider=endpoint.provider, field="input_tokens")
        if isinstance(counted, ModelFailure):
            return redact_reflection(secret, counted)
        limited = limit_output(body, request, counted, field=output_field,
            automatic_thinking=endpoint.provider == "anthropic" and endpoint.parameters.get("thinking") == "enabled"
                and "thinking_budget_tokens" not in endpoint.parameters)
        if isinstance(limited, ModelFailure):
            return with_count(limited)
        packet, budget = limited
    elif endpoint.provider == "anthropic" and "max_output_tokens" not in endpoint.parameters:
        return ModelFailure("permanent", "anthropic-output-budget-required")

    def invoke(_):
        wait_for_request(endpoint)
        result = post_json(endpoint.url, packet, headers=secret.authorization(endpoint.provider),
                           timeout=request.timeout_seconds, provider=endpoint.provider)
        outcome = (result if type(result) is ModelFailure else
                   response(endpoint, result.status, result.raw, retry_after=result.retry_after))
        if budget is not None:
            outcome = record_budget(outcome, budget)
        return redact_reflection(secret, with_count(outcome))

    if endpoint.spending is None:
        return invoke(request)
    if budget is None and "max_output_tokens" not in endpoint.parameters:
        return ModelFailure("permanent", "spending-output-bound-unavailable")
    ceiling = endpoint.prices.reservation(
        budget["input_tokens"] if budget is not None else endpoint.spending.maximum_input_tokens,
        budget["max_output_tokens"] if budget is not None else endpoint.parameters["max_output_tokens"])
    return redact_reflection(secret, with_count(SpendAccount(endpoint.spending).dispatch(request, ceiling, invoke)))


def main() -> None:
    endpoint = HostedEndpoint.from_dict(parse_json(sys.argv[1].encode("utf-8")))
    # ProcessModelBackend already checked its configured request budget.
    raw = sys.stdin.buffer.read()
    request = ModelRequest.from_dict(parse_json(raw))
    sys.stdout.buffer.write(encode_model_outcome(request, exchange(endpoint, request)))
