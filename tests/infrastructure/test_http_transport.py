"""Framing checks use the real stdlib HTTP parser, without sockets."""

import http.client
import io

import pytest

from behave.core.http import HttpBodyLimit, read_body


def response(headers: bytes, body: bytes):
    class Wire:
        def makefile(self, *args):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body)

    parsed = http.client.HTTPResponse(Wire())
    parsed.begin()
    return parsed


@pytest.mark.parametrize(("headers", "body", "expected"), [
    (b"Content-Length: 2\r\n", b"{}", b"{}"),
    (b"Content-Length: 0\r\n", b"", b""),
    (b"Transfer-Encoding: chunked\r\n", b"2\r\n{}\r\n0\r\n\r\n", b"{}"),
    (b"", b"{}", b"{}"),
])
def test_complete_bodies_include_exact_limit(headers, body, expected):
    assert read_body(response(headers, body), 2) == expected
    assert read_body(response(headers, body)) == expected


@pytest.mark.parametrize(("headers", "body"), [
    (b"Content-Length: 3\r\n", b"{}"),
    (b"Transfer-Encoding: chunked\r\n", b"2\r\n{}\r\n"),
    (b"Transfer-Encoding: chunked\r\n", b"3\r\n{}"),
    (b"Content-Length: -1\r\n", b"{}"),
    (b"Content-Length: invalid\r\n", b"{}"),
    (b"Content-Length: 2\r\nContent-Length: 3\r\n", b"{}"),
    (b"Transfer-Encoding: chunked\r\nContent-Length: 2\r\n", b"2\r\n{}\r\n0\r\n\r\n"),
    (b"Transfer-Encoding: gzip\r\n", b"{}"),
])
@pytest.mark.parametrize("limit", [None, 128])
def test_partial_or_ambiguous_message_is_not_a_complete_json_reply(headers, body, limit):
    with pytest.raises(http.client.HTTPException):
        read_body(response(headers, body), limit)


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
def test_unlimited_provider_body_retains_the_complete_payload(framing):
    payload = b"x" * ((1 << 20) + 1)
    headers, body = b"", payload
    if framing == "length":
        headers = f"Content-Length: {len(payload)}\r\n".encode()
    elif framing == "chunked":
        headers = b"Transfer-Encoding: chunked\r\n"
        body = f"{len(payload):x}\r\n".encode() + payload + b"\r\n0\r\n\r\n"
    assert read_body(response(headers, body)) == payload


@pytest.mark.parametrize(("headers", "body"), [
    (b"Content-Length: 4\r\n", b"abcd"),
    (b"Transfer-Encoding: chunked\r\n", b"4\r\nabcd\r\n0\r\n\r\n"),
    (b"", b"abcd"),
])
def test_body_limit_retains_only_the_bounded_prefix(headers, body):
    with pytest.raises(HttpBodyLimit) as failure:
        read_body(response(headers, body), 2)
    assert failure.value.partial == b"ab"


def test_empty_source_body_can_use_an_exhausted_aggregate_budget():
    from behave.core.http import HttpBodyLimit as SharedLimit
    from behave.core.http import read_body as shared_read

    assert read_body is shared_read and HttpBodyLimit is SharedLimit
    assert shared_read(response(b"Content-Length: 0\r\n", b""), 0) == b""
    with pytest.raises(SharedLimit):
        shared_read(response(b"Content-Length: 1\r\n", b"a"), 0)
