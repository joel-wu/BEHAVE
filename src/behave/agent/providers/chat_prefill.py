"""Exact server-rendered prompt counts for hosted Chat providers.

Fireworks has no public standalone counter. Its Chat API accepts max_tokens=0
and returns prompt_tokens after applying its actual chat/tool template. This
prefill is billable: it has a separate receipt in the same spend account, and
its native response is attached to the generated reply for trace inspection.
DeepSeek and OpenRouter require max_tokens >= 1. Their counting replies are separately
charged and saved, but is never executed or added to the agent conversation.
"""

from dataclasses import replace

from behave.core.records import canonical_json

from ..model import ModelFailure, ModelReply
from ..spending import SpendAccount
from .chat_wire import response
from .credentials import redact_reflection
from .http_transport import post_json
from .rate_limit import wait_for_request


def count_prompt(endpoint, request, body, secret):
    counting = replace(request, call_id=request.call_id + ":input-count")
    output_tokens = 1 if endpoint.provider in {"deepseek", "openrouter"} else 0
    packet = canonical_json({**body, "max_tokens": output_tokens})

    def invoke(_):
        wait_for_request(endpoint)
        result = post_json(endpoint.url, packet, headers=secret.authorization(endpoint.provider),
                           timeout=request.timeout_seconds, provider=endpoint.provider)
        outcome = (result if isinstance(result, ModelFailure) else
                   response(endpoint, result.status, result.raw,
                            retry_after=result.retry_after, provider=endpoint.provider))
        if isinstance(outcome, ModelReply):
            usage = outcome.usage.get("normalized")
            if usage is None or usage["output_tokens"] > output_tokens or outcome.calls or (
                    output_tokens == 0 and outcome.content):
                code = "fireworks-prefill-not-empty" if output_tokens == 0 else f"{endpoint.provider}-prefill-invalid"
                outcome = ModelFailure("permanent", code, outcome.usage,
                                       outcome.raw, outcome.charge)
        # Credential reflection must never enter the spending journal either.
        return redact_reflection(secret, outcome)

    if endpoint.spending is None:
        return invoke(counting)
    ceiling = endpoint.prices.reservation(endpoint.spending.maximum_input_tokens, output_tokens)
    # Replayed receipts bypass invoke; do not propagate an old unsafe envelope.
    return redact_reflection(secret, SpendAccount(endpoint.spending).dispatch(counting, ceiling, invoke))
