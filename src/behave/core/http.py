"""Bounded HTTP message framing, shared by trusted provider and source services."""

import http.client


class HttpBodyLimit(http.client.HTTPException):
    def __init__(self, partial: bytes):
        super().__init__("HTTP response exceeds its byte limit")
        self.partial = partial


def read_body(response: http.client.HTTPResponse, limit: int | None = None) -> bytes:
    """Read a complete, unambiguous body, optionally bounding public downloads.

    The enclosing process must supply an absolute deadline. A socket timeout
    alone does not bound slow trickles. read(amt) tolerates premature EOF, so
    Content-Length completion needs an explicit check. Chunked reads request a
    byte beyond the cap to ensure that a successful read reaches its terminator.
    """
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("HTTP body limit must be a nonnegative integer or None")
    lengths = response.headers.get_all("Content-Length", [])
    transfers = response.headers.get_all("Transfer-Encoding", [])
    if (len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
            or (lengths and (not lengths[0].isascii() or not lengths[0].isdecimal()))
            or (transfers and transfers[0].lower() != "chunked")):
        raise http.client.HTTPException("unsupported HTTP response framing")
    raw = response.read() if limit is None else response.read(limit + 1)
    if limit is not None and len(raw) > limit:
        raise HttpBodyLimit(raw[:limit])
    if response.length is not None and response.length != 0:
        raise http.client.IncompleteRead(raw, response.length)
    return raw
