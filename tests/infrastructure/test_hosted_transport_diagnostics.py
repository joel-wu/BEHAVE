import http.client
import json

import pytest

from behave.agent.providers import http_transport


@pytest.mark.parametrize("phase", ["connect", "send_request", "response_headers", "response_body"])
def test_transport_diagnostics_preserve_failure_semantics_without_secrets(monkeypatch, phase):
    closed = []

    def step(name):
        if name == phase:
            raise OSError("do not publish exception text with secret-token")

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            step("connect")

        def request(self, *args, **kwargs):
            step("send_request")

        def getresponse(self):
            step("response_headers")
            return object()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(http_transport, "read_body", lambda _: step("response_body"))
    result = http_transport.post_json("https://fixture.invalid/v1", b"private-request",
        headers={"Authorization": "secret-token"}, timeout=None, provider="fixture")
    assert result.kind == ("retryable" if phase == "connect" else "unknown")
    assert result.code == ("fixture-connect-failed" if phase == "connect" else "fixture-exchange-interrupted")
    assert json.loads(result.raw)["transport_error"]["phase"] == phase
    assert b"secret-token" not in result.raw and b"private-request" not in result.raw
    assert closed == [True]
