"""Stateless OpenAI Responses: full native output replay, including reasoning."""

from behave.core.records import canonical_json, parse_json, thaw_json

from ..model import ModelFailure, ModelReply
from .hosted_usage import usage_of
from .http_errors import http_failure
from .wire import function_name, history, tool_call, tool_output, verified_continuation


def output(items, *, truncated=False):
    if type(items) is not list:
        raise ValueError("output must be an array")
    texts, calls = [], []
    for item in items:
        kind = item["type"]
        if kind == "message":
            if item.get("role") != "assistant" or type(item["content"]) is not list:
                raise ValueError("invalid output message")
            for part in item["content"]:
                if part["type"] not in {"output_text", "refusal"}:
                    raise ValueError("unsupported output content")
                value = part["text"] if part["type"] == "output_text" else part["refusal"]
                if type(value) is not str:
                    raise ValueError("invalid output text")
                texts.append(value)
        elif kind == "function_call":
            calls.append(tool_call(item["call_id"], item["name"], item["arguments"], truncated=truncated))
        elif kind != "reasoning":
            raise ValueError("server-side tools and unknown output items are unsupported")
    return "".join(texts), tuple(calls)


def request_body(endpoint, request):
    inputs = []
    for message in history(endpoint, request):
        role = message["role"]
        if role == "assistant":
            inputs.extend(verified_continuation(message, "openai", output))
        elif role == "tool":
            inputs.append({"type": "function_call_output", "call_id": message["call_id"],
                           "output": tool_output(message)})
        else:
            inputs.append({"role": role, "content": message["content"]})
    params = thaw_json(endpoint.parameters)
    if "reasoning_effort" in params:
        params["reasoning"] = {"effort": params.pop("reasoning_effort")}
    if "verbosity" in params:
        params["text"] = {"verbosity": params.pop("verbosity")}
    body = {"model": endpoint.model, "input": inputs, "store": False, "stream": False,
            "include": ["reasoning.encrypted_content"], **params}
    if endpoint.billing_tier is not None:
        body["service_tier"] = "default"
    if request.tools:
        body["tools"] = [{"type": "function", "name": function_name(t["name"]),
                          "description": t["description"], "parameters": thaw_json(t["parameters"]),
                          "strict": True} for t in request.tools]
    return canonical_json(body)


def response(endpoint, status, raw, *, retry_after=None):
    try:
        body = parse_json(raw)
    except ValueError:
        return (http_failure("openai", status, None, raw, {}, None, retry_after) if status != 200
                else ModelFailure("unknown", "openai-response-not-json", raw=raw))
    usage, charge = usage_of(endpoint, body)
    if body.get("model") is not None and not endpoint.accepts_model(body["model"]):
        charge = None
    if status != 200:
        return http_failure("openai", status, body, raw, usage, charge, retry_after)
    try:
        if not endpoint.accepts_model(body.get("model")):
            return ModelFailure("unknown", "openai-model-mismatch", usage, raw)
        truncated = body.get("status") == "incomplete"
        if (body.get("status") not in {"completed", "incomplete"} or body.get("error") is not None
                or truncated and (body.get("incomplete_details") or {}).get("reason") != "max_output_tokens"):
            raise ValueError("response has no usable generation")
        content, calls = output(body["output"], truncated=truncated)
        return ModelReply(content, calls, usage, raw, charge,
                          finish_reason="length" if truncated else "tool_calls" if calls else "stop",
                          continuation={"provider": "openai", "items": body["output"], "truncated": truncated})
    except (ValueError, TypeError, KeyError, AttributeError):
        return ModelFailure("unknown", "openai-response-invalid", usage, raw, charge)
