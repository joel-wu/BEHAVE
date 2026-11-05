"""Anthropic Messages with unchanged thinking signatures and ordered tool turns."""

from behave.core.records import canonical_json, parse_json, thaw_json

from ..model import ModelFailure, ModelReply
from .hosted_usage import usage_of
from .http_errors import http_failure
from .wire import function_name, history, tool_call, tool_output, verified_continuation


def output(items, *, truncated=False):
    if type(items) is not list:
        raise ValueError("content must be an array")
    texts, calls = [], []
    for item in items:
        kind = item["type"]
        if kind == "text":
            if type(item["text"]) is not str:
                raise ValueError("invalid text")
            texts.append(item["text"])
        elif kind == "tool_use":
            arguments = canonical_json(item["input"]).decode("utf-8")
            calls.append(tool_call(item["id"], item["name"], arguments, truncated=truncated))
        elif kind == "thinking":
            if any(type(item[key]) is not str for key in ("thinking", "signature")):
                raise ValueError("invalid signed thinking")
        elif kind == "redacted_thinking":
            if type(item["data"]) is not str:
                raise ValueError("invalid redacted thinking")
        else:
            raise ValueError("server-side tools and unknown content are unsupported")
    return "".join(texts), tuple(calls)


def request_body(endpoint, request):
    system, messages = [], []
    for message in history(endpoint, request):
        role = message["role"]
        if role == "system":
            if messages:
                raise ValueError("Messages requires initial system instructions")
            system.append({"type": "text", "text": message["content"]})
            continue
        if role == "assistant":
            blocks = verified_continuation(message, "anthropic", output)
        elif role == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": message["call_id"],
                       "content": tool_output(message)}]
        else:
            blocks = [{"type": "text", "text": message["content"]}]
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": list(blocks)})
    params = thaw_json(endpoint.parameters)
    if "max_output_tokens" in params:
        params["max_tokens"] = params.pop("max_output_tokens")
    if "thinking" in params:
        params["thinking"] = {"type": params["thinking"]}
        if "thinking_budget_tokens" in params:
            params["thinking"]["budget_tokens"] = params.pop("thinking_budget_tokens")
        elif params["thinking"]["type"] == "enabled":
            # Native manual thinking requires a number. In the absence of an
            # operator-selected cap, use the available output budget. The broker
            # recalculates this after counting the actual input.
            maximum = params.get("max_tokens", request.context_tokens)
            if maximum is None or maximum <= 1024:
                raise ValueError("manual thinking requires an output budget above 1024")
            params["thinking"]["budget_tokens"] = maximum - 1
    if "reasoning_effort" in params:
        params["output_config"] = {"effort": params.pop("reasoning_effort")}
    body = {"model": endpoint.model, "messages": messages, "stream": False, **params}
    # The API advances this cache point with tool turns, without rewriting
    # signed thinking or adding markers to the model-visible conversation.
    body["cache_control"] = {"type": "ephemeral"}
    if endpoint.billing_tier is not None:
        body["service_tier"] = "standard_only"
    if system:
        body["system"] = system
    if request.tools:
        body["tools"] = [{"name": function_name(t["name"]), "description": t["description"],
                          "input_schema": thaw_json(t["parameters"])} for t in request.tools]
    return canonical_json(body)


def response(endpoint, status, raw, *, retry_after=None):
    try:
        body = parse_json(raw)
    except ValueError:
        return (http_failure("anthropic", status, None, raw, {}, None, retry_after) if status != 200
                else ModelFailure("unknown", "anthropic-response-not-json", raw=raw))
    usage, charge = usage_of(endpoint, body)
    if body.get("model") is not None and not endpoint.accepts_model(body["model"]):
        charge = None
    if status != 200:
        return http_failure("anthropic", status, body, raw, usage, charge, retry_after)
    try:
        if not endpoint.accepts_model(body.get("model")):
            return ModelFailure("unknown", "anthropic-model-mismatch", usage, raw)
        stop = body["stop_reason"]
        if body.get("type") != "message" or body.get("role") != "assistant" or stop not in {
                "end_turn", "stop_sequence", "tool_use", "refusal", "max_tokens", "model_context_window_exceeded"}:
            raise ValueError("unsupported message result")
        truncated = stop in {"max_tokens", "model_context_window_exceeded"}
        content, calls = output(body["content"], truncated=truncated)
        # Dispatch complete native tool blocks, including observed Sonnet
        # responses that contain tool_use but label the stop as end_turn.
        # Preserve the native stop label in raw evidence, not as an extra veto
        # on an otherwise complete call. Truncated calls remain non-executable.
        if calls and stop == "refusal":
            raise ValueError("refusal contains tool calls")
        if stop == "model_context_window_exceeded":
            finish_reason = "context_limit"
        elif stop == "max_tokens":
            finish_reason = "length"
        else:
            finish_reason = "tool_calls" if calls else "stop"
        return ModelReply(content, calls, usage, raw, charge,
                          finish_reason=finish_reason,
                          continuation={"provider": "anthropic", "items": body["content"], "truncated": truncated})
    except (ValueError, TypeError, KeyError, AttributeError):
        return ModelFailure("unknown", "anthropic-response-invalid", usage, raw, charge)
