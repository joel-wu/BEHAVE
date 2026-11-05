"""Lossless supported message history and strict, usage-first response decoding.

No SDK retries, JSON repair, context compaction, synthetic tool IDs or text
mining for function calls. A malformed argument string with a valid call ID
is model feedback; a response whose identity/structure cannot be bound is an
unknown outcome. Even the latter retains independently readable usage.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from behave.core.records import canonical_json, parse_json, thaw_json

from ..billing import TokenPrices, TokenUsage
from ..model import Charge, ModelFailure, ModelReply, ModelRequest, ToolCall
from .http_errors import http_failure
from .wire import function_name, history, tool_call, tool_output


class ChatEndpoint(Protocol):
    identity: str
    model: str
    parameters: Mapping
    prices: TokenPrices | None

    def accepts_model(self, model: object) -> bool: ...


def _message(value: dict) -> dict:
    # Common message structure and call/result pairing are checked by history().
    role = value["role"]
    if role in {"system", "user"}:
        return dict(value)
    if role == "tool":
        return {"role": role, "tool_call_id": value["call_id"], "content": tool_output(value)}
    if role == "assistant":
        continuation = value["continuation"]
        if (type(continuation) is not dict or set(continuation) - {"reasoning_content", "reasoning", "reasoning_details", "refusal"}
                or {"reasoning", "reasoning_content"} <= continuation.keys()
                or any(type(value) is not str for name, value in continuation.items() if name != "reasoning_details")
                or ("reasoning_details" in continuation and (
                    type(continuation["reasoning_details"]) is not list
                    or any(type(item) is not dict for item in continuation["reasoning_details"])))):
            raise ValueError("unsupported continuation fields")
        result = {"role": role, "content": value["content"], **continuation}
        calls = tuple(ToolCall.from_dict(call) for call in value["calls"])
        if calls:
            result["tool_calls"] = [
                {"id": call.call_id, "type": "function", "function": {
                    "name": call.name, "arguments": call.raw_arguments
                    if call.raw_arguments is not None else canonical_json(thaw_json(call.arguments)).decode("utf-8")}}
                for call in calls]
        return result
    raise ValueError("unsupported message role")


def request_body(endpoint: ChatEndpoint, request: ModelRequest, *, parameters: dict | None = None) -> bytes:
    messages = [_message(message) for message in history(endpoint, request)]
    body = {"model": endpoint.model, "messages": messages, "stream": False, "n": 1,
            **(thaw_json(endpoint.parameters) if parameters is None else parameters)}
    if request.tools:
        body["tools"] = [{"type": "function", "function": {
            "name": function_name(tool["name"]), "description": tool["description"],
            "parameters": thaw_json(tool["parameters"])}} for tool in request.tools]
    return canonical_json(body)


def _usage(body: dict, endpoint: ChatEndpoint) -> tuple[dict, object]:
    raw = body.get("usage")
    record = {"raw": raw, "normalized": None, "normalization_error": None,
              "pricing": None if endpoint.prices is None else endpoint.prices.identity}
    try:
        if type(raw) is not dict:
            raise ValueError("missing usage")
        prompt = raw.get("prompt_tokens_details")
        completion = raw.get("completion_tokens_details")
        prompt = {} if prompt is None else prompt
        completion = {} if completion is None else completion
        if type(prompt) is not dict or type(completion) is not dict:
            raise ValueError("invalid token details")
        reasoning = completion.get("reasoning_tokens")
        if raw.get("reasoning_tokens") is not None:
            if reasoning is not None and (type(reasoning) is not type(raw["reasoning_tokens"])
                                          or reasoning != raw["reasoning_tokens"]):
                raise ValueError("conflicting reasoning token counts")
            reasoning = raw["reasoning_tokens"]
        cached = prompt.get("cached_tokens", raw.get("prompt_cache_hit_tokens"))
        usage = TokenUsage(raw["prompt_tokens"], raw["completion_tokens"], cached, reasoning,
                           cache_write_tokens=prompt.get("cache_write_tokens", 0))
        total = raw.get("total_tokens")
        if total is not None and (type(total) is not int or total != usage.input_tokens + usage.output_tokens):
            raise ValueError("invalid total token count")
        record["normalized"] = usage.as_dict()
        charge = None if endpoint.prices is None else endpoint.prices.charge(usage)
        return record, charge
    except (ValueError, TypeError, KeyError, AttributeError):
        record["normalization_error"] = "usage-missing-or-invalid"
        return record, None


def response(endpoint: ChatEndpoint, status: int, raw: bytes,
             *, retry_after: str | None = None, provider: str = "chat") -> ModelReply | ModelFailure:
    try:
        body = parse_json(raw)
    except ValueError:
        if status != 200:
            return http_failure(provider, status, None, raw, {}, None, retry_after)
        return ModelFailure("unknown", "chat-response-not-json", raw=raw)
    return normalize_response(endpoint, status, raw, body, retry_after=retry_after, provider=provider)


def normalize_response(endpoint: ChatEndpoint, status: int, raw: bytes, body: dict,
                       *, retry_after: str | None = None, provider: str = "chat") -> ModelReply | ModelFailure:
    """Normalize a body obtained by strict parse_json(raw), retaining its wire bytes.

    Native training verification also inspects the token extension in this same
    body. Sharing the parsed record avoids decoding large token arrays twice.
    """
    usage, charge = _usage(body, endpoint)
    if provider == "openrouter":
        # Router receipts include the actual account debit, independent of
        # upstream list prices and cache tiers. Never add reasoning twice.
        usage["provider_name"] = body.get("provider")
        usage["response_id"] = body.get("id")
        try:
            cost = body["usage"]["cost"]
            if type(cost) not in {int, float, str}:
                raise ValueError("invalid router cost")
            charge = Charge(str(cost), "USD", "openrouter-usage-cost/1")
            usage["billing_source"] = "usage.cost"
        except (KeyError, TypeError, ValueError):
            charge = None
            usage["pricing_error"] = "openrouter-cost-missing-or-invalid"
    if body.get("model") is not None and not endpoint.accepts_model(body["model"]):
        charge = None
    if status != 200:
        return http_failure(provider, status, body, raw, usage, charge, retry_after)
    try:
        if not endpoint.accepts_model(body.get("model")):
            # A different model's price cannot be applied to this response.
            return ModelFailure("unknown", "chat-model-mismatch", usage, raw)
        choices = body["choices"]
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ValueError("expected exactly one choice")
        choice = choices[0]
        if type(choice.get("index")) is not int or choice["index"] != 0:
            raise ValueError("invalid choice index")
        finish = choice["finish_reason"]
        if finish not in {"stop", "tool_calls", "length"}:
            raise ValueError("unknown finish reason")
        message = choice["message"]
        # New vLLM uses `reasoning`, SGLang uses `reasoning_content`. Preserve
        # the chosen spelling for continuation; never guess between two values.
        null_only = {"audio", "function_call", "tools"}
        allowed = {"role", "content", "tool_calls", "reasoning_content", "reasoning", "reasoning_details", "refusal", "annotations", *null_only}
        if (type(message) is not dict or set(message) - allowed or message.get("role") != "assistant"
                or message.get("annotations") not in (None, [])
                or any(message.get(name) is not None for name in null_only)):
            raise ValueError("unsupported assistant message")
        content = message.get("content")
        if content is None:
            content = ""
        if type(content) is not str:
            raise ValueError("non-text response")
        native_calls = message.get("tool_calls")
        native_calls = [] if native_calls is None else native_calls
        if type(native_calls) is not list:
            raise ValueError("invalid tool calls")
        if (finish == "tool_calls" and not native_calls) or (finish == "stop" and native_calls):
            raise ValueError("finish reason disagrees with tool calls")
        calls = []
        for index, item in enumerate(native_calls):
            if (type(item) is not dict or set(item) - {"id", "type", "function", "index", "name"}
                    or item.get("name") is not None
                    or not {"id", "type", "function"} <= item.keys() or item["type"] != "function"):
                raise ValueError("unsupported call")
            # Fireworks includes a nullable top-level name for alternate tool
            # types. For function calls only function.name is authoritative;
            # a populated second name is not silently discarded or reconciled.
            # SGLang includes the streaming-style ordinal in full replies too.
            # It is redundant only when it agrees with the preserved list order;
            # never reorder calls or synthesize a missing public call ID.
            if item.get("index") is not None and (type(item["index"]) is not int or item["index"] != index):
                raise ValueError("tool call index disagrees with list order")
            function = item["function"]
            if type(function) is not dict or set(function) != {"name", "arguments"}:
                raise ValueError("invalid function call")
            calls.append(tool_call(item["id"], function["name"], function["arguments"], truncated=finish == "length"))
        continuation = {}
        for name in ("reasoning", "reasoning_content", "refusal"):
            if message.get(name) is not None:
                if type(message[name]) is not str:
                    raise ValueError("continuation must be text")
                continuation[name] = message[name]
        if {"reasoning", "reasoning_content"} <= continuation.keys():
            raise ValueError("ambiguous reasoning continuation")
        details = message.get("reasoning_details")
        if details is not None:
            if type(details) is not list or any(type(item) is not dict for item in details):
                raise ValueError("reasoning details must be an array of objects")
            continuation["reasoning_details"] = details
        return ModelReply(content, tuple(calls), usage, raw, charge,
                          finish_reason=finish, continuation=continuation)
    except (ValueError, TypeError, KeyError):
        return ModelFailure("unknown", "chat-response-invalid", usage, raw, charge)
