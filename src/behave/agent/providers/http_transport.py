"""One bounded HTTP exchange. No SDK retries, proxies, redirects or env reads.

The enclosing trusted-service process supplies the absolute lifetime bound;
socket timeouts alone would not bound a server which keeps trickling bytes.
"""

from __future__ import annotations

import http.client
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from behave.core.http import read_body

from ..model import ModelFailure


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    raw: bytes
    retry_after: str | None


def _failure(provider, kind, code, phase, error):
    # Record transport facts, never headers, credentials or request/response
    # content. Existing failure codes and uncertain-charge semantics stay intact.
    raw = json.dumps({"transport_error": {"phase": phase, "exception": type(error).__name__,
                                          "errno": getattr(error, "errno", None)}}).encode()
    return ModelFailure(kind, f"{provider}-{code}", raw=raw)


def post_json(url: str, packet: bytes, *, headers: dict[str, str], timeout: int | None,
              provider: str) -> HttpResponse | ModelFailure:
    parts = urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = cls(parts.hostname, parts.port, timeout=timeout)
    try:
        connection.connect()
    except OSError as error:
        connection.close()
        return _failure(provider, "retryable", "connect-failed", "connect", error)
    phase = "send_request"
    try:
        connection.request("POST", parts.path, body=packet,
                           headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
        phase = "response_headers"
        response = connection.getresponse()
        phase = "response_body"
        raw = read_body(response)
        return HttpResponse(response.status, raw, response.getheader("Retry-After"))
    except (OSError, http.client.HTTPException) as error:
        return _failure(provider, "unknown", "exchange-interrupted", phase, error)
    finally:
        connection.close()
