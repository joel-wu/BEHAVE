"""HTTP rejection classification shared by all provider wire formats."""

import re

from ..model import Charge, ModelFailure
from ..retry import retry_after_seconds

_QUOTA = {"insufficient_quota", "billing_hard_limit_reached", "billing_error",
          "credit_balance_too_low", "quota_exceeded", "usage_limit_reached",
          "spend_limit_reached", "insufficient_balance", "credit_balance_exhausted",
          "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
          "organization_usage_limit_exceeded", "enforced_spend_limit_reached"}
_CONTEXT = {"context_length_exceeded", "context_window_exceeded", "prompt_too_long"}
# SGLang's OpenAI error response has a numeric code and BadRequestError type,
# not a machine-readable context code. Match its explicit length failures,
# never a generic mention of "context" in another request/configuration error.
_LOCAL_CONTEXT = re.compile(
    r"(?:Requested token count exceeds the model's maximum context length of [0-9]+ tokens\."
    r"|The input \([0-9]+ tokens\) is longer than the model's context length \([0-9]+ tokens\)\."
    r"|Input length \([0-9]+ tokens\) exceeds the maximum allowed length \([0-9]+ tokens\)\.)"
)
# Older Messages errors use invalid_request_error for both insufficient credit
# and caller-set spend limits. Only documented prefixes identify these cases.
_ANTHROPIC_BILLING = re.compile(
    r"(?:Your credit balance is too low to access the (?:Anthropic|Claude) API\."
    r"|You have reached your specified (?:workspace )?API usage limits\b)"
)


def http_failure(provider: str, status: int, body: dict | None, raw: bytes, usage: dict,
                 charge: Charge | None, retry_after: str | None = None) -> ModelFailure:
    error = body.get("error") if type(body) is dict else None
    if error is None and type(body) is dict and body.get("object") == "error":
        error = body
    codes = {str(error.get(key, "")) for key in ("code", "type")} if type(error) is dict else set()
    message = error.get("message") if type(error) is dict else None
    if provider == "anthropic" and type(error) is dict:
        details = error.get("details")
        if type(details) is dict and type(details.get("error_code")) is str:
            codes.add(details["error_code"])
    billing_message = (provider == "anthropic" and "invalid_request_error" in codes
                       and type(message) is str and _ANTHROPIC_BILLING.match(message) is not None)
    # Fireworks and Anthropic explicitly document 402 as a billing rejection,
    # including gateways which provide no parseable JSON envelope.
    payment_required = status == 402 and provider in {"anthropic", "fireworks", "deepseek"}
    if 400 <= status < 500 and (codes & _QUOTA or billing_message or payment_required):
        return ModelFailure("permanent", f"{provider}-quota-exhausted", usage, raw, charge)
    local_context = (provider == "chat" and "BadRequestError" in codes
                     and type(message) is str and _LOCAL_CONTEXT.match(message) is not None)
    if status in {400, 413, 422} and (codes & _CONTEXT or local_context):
        return ModelFailure("context_limit", f"{provider}-context-limit", usage, raw, charge)
    kind = ("retryable" if status == 429 and type(error) is dict else
            "permanent" if 400 <= status < 500 and status not in {408, 429} else "unknown")
    try:
        delay = retry_after_seconds(retry_after) if kind != "permanent" else 0
    except ValueError:
        return ModelFailure("permanent", f"{provider}-retry-wait-unrepresentable", usage, raw, charge)
    return ModelFailure(kind, f"{provider}-http-{status}", usage, raw, charge, delay)
