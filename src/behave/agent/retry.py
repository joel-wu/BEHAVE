"""Journal-owned retry decisions, distinct from a remote outcome's certainty.

Only generation-only profiles may opt into repeating an uncertain HTTP call.
No tool execution or unpaired journal request is retried by this policy.
"""

from __future__ import annotations

import hashlib
import math
import time
from email.utils import parsedate_to_datetime

from behave.core import runtime_defaults as defaults

from .model import ModelFailure, ModelRequest
from .providers.destinations import DESTINATIONS

# Keep the same narrow transient-error list for every supported HTTP adapter.
# Import only the data registry: hosted profiles themselves depend on the
# process runtime, which uses this retry policy too.
_UNCERTAIN_TRANSPORT_CODES = frozenset(
    f"{provider}-{suffix}"
    for provider in ("chat", *DESTINATIONS)
    for suffix in ("exchange-interrupted", "http-408", "http-500", "http-502",
                   "http-503", "http-504", "http-529")
)


def retry_after_seconds(value: str | None, *, now: float | None = None) -> int:
    """Honor delta-seconds or HTTP-date without shortening the server's wait."""
    if value is None:
        return 0
    value = value.strip()
    if value.isascii() and value.isdigit():
        # A valid numeric header that exceeds Python's conversion capacity is
        # unrepresentable, not malformed. Propagate ValueError to the caller's
        # explicit stop path instead of silently shortening the server's wait.
        delay = int(value.lstrip("0") or "0")
    else:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return 0
            delay = math.ceil(date.timestamp() - (time.time() if now is None else now))
        except (ValueError, TypeError, OverflowError):
            return 0
    # Native clock capacity, not a separately chosen maximum wait.
    try:
        float(delay)
    except OverflowError as error:
        raise ValueError("retry delay exceeds native clock representation") from error
    return max(0, delay)


def may_retry(failure: ModelFailure, request: ModelRequest, *, uncertain_generation: bool) -> bool:
    if failure.kind == "retryable":
        return True
    if (failure.kind != "unknown" or not uncertain_generation
            or request.model_policy.get("generation_only") is not True):
        return False
    # Invalid JSON, model identity, malformed tool envelopes and arbitrary
    # worker crashes are NOT transient transport errors.
    return failure.code == "model-worker-timeout" or failure.code in _UNCERTAIN_TRANSPORT_CODES


def backoff_seconds(failure: ModelFailure, request: ModelRequest, *, retry_index: int | None = None) -> int:
    cap = defaults.AGENT_RETRY_BACKOFF_SECONDS
    # Context-window corrections are dispatches but not transport retries.
    index = request.dispatch if retry_index is None else retry_index
    base = min(cap, 2 ** min(index, cap.bit_length()))
    jitter = int.from_bytes(hashlib.sha256(request.call_id.encode()).digest()[:4], "big") % base
    return max(failure.retry_after_seconds, base + jitter)
