"""One HTTP call in the existing hard-bounded model-broker process.

http.client does not read proxy credentials or follow redirects. Socket timeouts
are an inner guard; ProcessModelBackend owns the total deadline, output bound,
cancellation and process-tree cleanup (including slow trickling responses).
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace

from behave.core.records import canonical_json, parse_json, thaw_json

from ..model import ModelFailure, ModelRequest
from ..process_model import encode_model_outcome
from .chat_wire import request_body, response
from .context import count_input, limit_output, record_budget
from .http_transport import post_json
from .local_chat import LocalChatEndpoint


def _template_reasoning(parameters: dict) -> None:
    """Keep model-specific effort names in the server's template extension."""
    effort = parameters.pop("reasoning_effort")
    parameters["chat_template_kwargs"] = {
        "reasoning_effort": effort, "enable_thinking": effort != "none",
        **parameters.get("chat_template_kwargs", {}),
    }


def exchange(endpoint: LocalChatEndpoint, request: ModelRequest, *, worker_url: str | None = None):
    try:
        parameters = thaw_json(endpoint.parameters)
        if endpoint.tokens is not None:
            parameters.update(endpoint.tokens.parameters())
            if endpoint.tokens.runtime in {"sglang", "sglang-slime"} and "reasoning_effort" in parameters:
                # Pinned SGLang chat/tokenize schemas accept different effort
                # enums. Both render the same unrestricted template kwargs.
                _template_reasoning(parameters)
        packet = request_body(endpoint, request, parameters=parameters)
        headers = endpoint.headers(request)
        url = endpoint.request_url(request, worker_url=worker_url)
    except (ValueError, TypeError, KeyError):
        return ModelFailure("permanent", "chat-request-invalid")
    budget = None
    native_ids = None
    body = None
    if request.native_prefix is not None:
        from .native_history import continue_input

        if endpoint.tokens is None or endpoint.tokens.continuation is None:
            return ModelFailure("permanent", "native-continuation-not-configured")
        body = parse_json(packet)
        native_ids = continue_input(endpoint.tokens.continuation, request, body,
            url=url.removesuffix("/v1/chat/completions") + "/tokenize", headers=headers)
        if isinstance(native_ids, ModelFailure):
            return native_ids
        body["input_ids"] = native_ids
    if request.context_tokens is not None:
        # A native suffix can be far larger than the available window. Check
        # its actual length before serializing (and reparsing) the ID array.
        if body is None:
            body = parse_json(packet)
        count_body = {key: body[key] for key in (
            "model", "messages", "tools", "tool_choice", "chat_template_kwargs", "reasoning_effort",
            "parallel_tool_calls",
        ) if key in body}
        if endpoint.tokens is not None and endpoint.tokens.runtime == "vllm" and "reasoning_effort" in body:
            # vLLM's tokenize API accepts these as template kwargs, whereas its
            # chat API also derives them from the top-level reasoning option.
            _template_reasoning(count_body)
        count = len(native_ids) if native_ids is not None else count_input(
            url.removesuffix("/v1/chat/completions") + "/tokenize", count_body,
            headers=headers, timeout=request.timeout_seconds, provider="chat", field="count")
        if isinstance(count, ModelFailure):
            return count
        limited = limit_output(body, request, count, field="max_tokens")
        if isinstance(limited, ModelFailure):
            return limited
        packet, budget = limited
    elif body is not None:
        packet = canonical_json(body)
    result = post_json(url, packet, headers=headers, timeout=request.timeout_seconds,
                       provider="chat")
    reply = (result if type(result) is ModelFailure else
             response(endpoint, result.status, result.raw, retry_after=result.retry_after))
    if endpoint.tokens is not None:
        from .native_tokens import attach_tokens

        reply = attach_tokens(endpoint.tokens, request, reply)
    if budget is not None:
        reply = record_budget(reply, budget)
    if endpoint.routing == "attempt":
        # Record the destination actually used above, not just the pool policy.
        # Keep the provider response bytes and its usage fields unchanged.
        reply = replace(reply, usage={**thaw_json(reply.usage), "routing": {
            "url": url, "key": headers["X-SMG-Routing-Key"],
            **({"policy": "least-active-attempts"} if worker_url is not None else {}),
        }})
    return reply


def main() -> None:
    endpoint = LocalChatEndpoint.from_dict(parse_json(sys.argv[1].encode("utf-8")))
    # ProcessModelBackend already checked its configured request budget.
    raw = sys.stdin.buffer.read()
    request = ModelRequest.from_dict(parse_json(raw))
    sys.stdout.buffer.write(encode_model_outcome(request, exchange(
        endpoint, request, worker_url=os.environ.get("BEHAVE_LOCAL_CHAT_WORKER_URL"))))
