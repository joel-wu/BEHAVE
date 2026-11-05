"""Provider-independent history checks and raw tool-argument handling."""

import re

from behave.core.records import canonical_json, fields, parse_json, text, thaw_json

from ..model import ToolCall


def function_name(value: object) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("invalid wire function name")
    return value


def history(endpoint, request):
    if (request.model_policy.get("provider"), request.model_policy.get("model")) != (endpoint.identity, endpoint.model):
        raise ValueError("model policy differs from endpoint")
    messages = [thaw_json(message) for message in request.messages]
    pending = set()
    for message in messages:
        role = message.get("role")
        if role == "tool":
            fields(message, {"role", "call_id", "content"})
            if message["call_id"] not in pending:
                raise ValueError("unbound or duplicate tool result")
            pending.remove(message["call_id"])
            continue
        if pending:
            raise ValueError("missing tool results")
        if role in {"system", "user"}:
            fields(message, {"role", "content"})
        elif role == "assistant":
            fields(message, {"role", "content", "calls", "continuation"})
            calls = tuple(ToolCall.from_dict(call) for call in message["calls"])
            pending = {call.call_id for call in calls}
            if len(pending) != len(calls):
                raise ValueError("duplicate tool call id")
        else:
            raise ValueError("unknown message role")
        if type(message["content"]) is not str:
            raise ValueError("non-text input is unsupported")
    if pending:
        raise ValueError("unresolved tool calls")
    return messages


def tool_call(identity, name, arguments, *, truncated=False):
    text(identity, "tool call id")
    # Declaration syntax constrains our tool inventory, not what a model can
    # get wrong. Preserve unknown names so the shared loop can return ordinary
    # tool-not-available feedback rather than aborting an otherwise valid turn.
    name = text(name, "tool name")
    if type(arguments) is not str:
        raise ValueError("tool arguments must be raw JSON text")
    error = "response-output-limit" if truncated else None
    try:
        parsed = parse_json(arguments.encode("utf-8"))
    except ValueError:
        parsed, error = {}, error or "tool-arguments-invalid-json-object"
    return ToolCall(identity, name, {} if error else parsed, arguments, error)


def verified_continuation(message, provider, decoder):
    native = fields(message["continuation"], {"provider", "items", "truncated"})
    if native["provider"] != provider or type(native["truncated"]) is not bool:
        raise ValueError("wrong native continuation")
    content, calls = decoder(native["items"], truncated=native["truncated"])
    if content != message["content"] or [c.as_dict() for c in calls] != message["calls"]:
        raise ValueError("native continuation disagrees with normalized message")
    return native["items"]


def tool_output(message):
    return canonical_json(message["content"]).decode("utf-8")
