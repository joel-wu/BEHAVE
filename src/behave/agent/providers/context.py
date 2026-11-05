"""Count the actual rendered request and spend only its remaining context.

Counting is a non-generating provider operation. No character estimates, local
retokenization of hosted reasoning, or changes to the conversation happen here.
The common agent loop owns whole-turn eviction when the input no longer fits.
"""

from dataclasses import replace

from behave.core.records import canonical_json, parse_json, thaw_json

from ..model import ModelFailure, ModelReply
from .http_errors import http_failure
from .http_transport import post_json


def _tokenize(url, body, *, headers, timeout, provider):
    result = post_json(url, canonical_json(body), headers=headers, timeout=timeout, provider=provider)
    if isinstance(result, ModelFailure):
        # No generation was dispatched. Retrying a failed count cannot duplicate it.
        return replace(result, kind="retryable") if result.kind == "unknown" else result
    try:
        response = parse_json(result.raw)
    except ValueError:
        response = None
    if result.status != 200:
        failure = http_failure(provider, result.status, response, result.raw, {}, None, result.retry_after)
        return replace(failure, kind="retryable") if failure.kind == "unknown" else failure
    return response, result.raw


def count_input(url, body, *, headers, timeout, provider, field):
    decoded = _tokenize(url, body, headers=headers, timeout=timeout, provider=provider)
    if isinstance(decoded, ModelFailure):
        return decoded
    response, raw = decoded
    count = response.get(field) if isinstance(response, dict) else None
    if type(count) is not int or count < 0:
        return ModelFailure("permanent", f"{provider}-input-token-count-invalid", raw=raw)
    return count


def encode_input(url, body, *, headers, timeout, provider):
    decoded = _tokenize(url, body, headers=headers, timeout=timeout, provider=provider)
    if isinstance(decoded, ModelFailure):
        return decoded
    response, raw = decoded
    tokens = response.get("tokens") if isinstance(response, dict) else None
    if type(tokens) is not list or any(type(token) is not int or token < 0 for token in tokens):
        return ModelFailure("permanent", f"{provider}-input-token-ids-invalid", raw=raw)
    return tokens


def limit_output(packet, request, input_tokens, *, field, automatic_thinking=False):
    """Return the wire body and its recorded budget, or ask the loop to evict."""
    available = request.context_tokens - input_tokens
    declared = packet.get(field)
    maximum = available if declared is None else min(available, declared)
    budget = {"limit": request.context_tokens, "input_tokens": input_tokens,
              "max_output_tokens": max(0, maximum)}
    thinking = packet.get("thinking") or {}
    if automatic_thinking and thinking.get("type") == "enabled":
        if maximum < 1025:
            return ModelFailure("context_limit", "context-input-full", {"context_budget": budget})
        thinking = {**thinking, "budget_tokens": maximum - 1}
        packet = {**packet, "thinking": thinking}
    minimum = thinking.get("budget_tokens", 0) + 1 if thinking.get("type") == "enabled" else 1
    if maximum < minimum:
        return ModelFailure("context_limit", "context-input-full", {"context_budget": budget})
    return canonical_json({**packet, field: maximum}), budget


def record_budget(reply, budget):
    """Keep real usage and billing even if a backend violates the requested cap."""
    usage = {**thaw_json(reply.usage), "context_budget": budget}
    if isinstance(reply, ModelReply):
        tokens = reply.token_data
        if tokens is not None and tokens.get("schema") == "behave.token-trace/1":
            actual = {"input_tokens": len(tokens["input_ids"]),
                      "output_tokens": len(tokens["response_ids"])}
        else:
            actual = usage.get("normalized")
        if actual is None:
            return ModelFailure("permanent", "context-usage-unavailable", usage, reply.raw, reply.charge)
        if actual["input_tokens"] + actual["output_tokens"] > budget["limit"]:
            return ModelFailure("permanent", "context-budget-exceeded", usage, reply.raw, reply.charge)
        # Native `length` describes the requested generation cap. When that
        # cap is the entire remaining window, the loop must evict a whole old
        # turn now instead of repeatedly continuing in the same nearly-full
        # context. An independently smaller output cap is still plain length.
        if (reply.finish_reason == "length"
                and budget["input_tokens"] + budget["max_output_tokens"] == budget["limit"]):
            reply = replace(reply, finish_reason="context_limit")
    return replace(reply, usage=usage)
