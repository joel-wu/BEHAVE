"""One public HTTPS GET, without proxies, cookies, credentials or hidden retries.

DNS results are checked as a set and the TCP socket uses a checked numeric
address. TLS still verifies the original hostname. The enclosing service's
deadline also bounds DNS, header parsing and slow trickles; socket timeouts alone
would not establish a wall-clock bound. Never call this in a candidate process.
"""

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from behave.core.artifacts import BlobRef
from behave.core.http import HttpBodyLimit, read_body
from behave.core.records import RecordError

from .fetch_records import public_url

REDIRECTS = {301, 302, 303, 307, 308}
_HEADERS = {"content-type", "content-length", "content-encoding", "etag", "last-modified"}
_TRANSITION = tuple(ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2002::/16"))


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and (
            address.ipv4_mapped is not None or any(address in net for net in _TRANSITION)):
        return False
    return (address.is_global and not address.is_multicast and not address.is_reserved
            and not address.is_loopback and not address.is_link_local and not address.is_unspecified)


def resolve(host: str) -> tuple[int, tuple]:
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not answers or any(family not in (socket.AF_INET, socket.AF_INET6)
            or not public_address(address[0]) or address[1] != 443 or (len(address) == 4 and address[3] != 0)
            for family, _, _, _, address in answers):
        raise RecordError("fetch-address-forbidden", "DNS did not resolve exclusively to public unicast addresses")
    # Prefer IPv4 when both families exist. No second lookup or invisible retry.
    family, _, _, _, address = min(answers, key=lambda a: (a[0], a[4][0]))
    return family, address


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("fetch deadline")
    return left


def connect(host: str, family: int, address: tuple, deadline: float) -> http.client.HTTPSConnection:
    context = ssl.create_default_context()
    sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    try:
        sock.settimeout(_remaining(deadline))
        sock.connect(address)
        secure = context.wrap_socket(sock, server_hostname=host)
    except BaseException:
        sock.close()
        raise
    try:
        connection = http.client.HTTPSConnection(host, timeout=_remaining(deadline), context=context)
        connection.sock = secure  # request() cannot resolve host again while this socket exists.
    except BaseException:
        secure.close()
        raise
    return connection


@dataclass(frozen=True, slots=True)
class Hop:
    code: str
    peer: str | None = None
    status: int | None = None
    headers: tuple[tuple[str, str], ...] = ()
    location: str | None = None
    body: bytes | None = None


def get(url: str, *, maximum: int, deadline: float) -> Hop:
    parsed = urlsplit(public_url(url))
    peer = status = None
    headers = ()
    connection = response = None
    try:
        family, address = resolve(parsed.hostname)
        peer = address[0]
        connection = connect(parsed.hostname, family, address, deadline)
        connection.sock.settimeout(_remaining(deadline))
        connection.request("GET", parsed.path or "/", headers={
            "Accept-Encoding": "identity", "User-Agent": "behave-source-fetch/1", "Connection": "close"})
        response = connection.getresponse()
        status = response.status
        pairs = [(name.lower(), value) for name, value in response.getheaders()]
        unique = {}
        for name, value in pairs:
            if name in _HEADERS | {"location", "transfer-encoding", "content-range"}:
                if name in unique or any(ord(c) < 32 or ord(c) == 127 for c in value):
                    return Hop("fetch-headers-invalid", peer, status)
                unique[name] = value
        headers = tuple(sorted((k, v) for k, v in unique.items() if k in _HEADERS))
        if status in REDIRECTS:
            return Hop("redirect", peer, status, headers, unique.get("location"))
        if status != 200:
            code = "fetch-http-unavailable" if status in {408, 425, 429} or status >= 500 else "fetch-http-status"
            return Hop(code, peer, status, headers)
        if unique.get("content-encoding", "identity").lower() != "identity":
            return Hop("fetch-content-encoding", peer, status, headers)
        if ("content-range" in unique or unique.get("transfer-encoding", "chunked").lower() != "chunked"
                or "transfer-encoding" in unique and "content-length" in unique):
            return Hop("fetch-headers-invalid", peer, status, headers)
        length = unique.get("content-length")
        if length is not None and (not length.isascii() or not length.isdecimal()):
            return Hop("fetch-headers-invalid", peer, status, headers)
        if length is not None and int(length) > maximum:
            return Hop("fetch-byte-limit", peer, status, headers)
        _remaining(deadline)
        body = read_body(response, maximum)
        return Hop("downloaded", peer, status, headers, body=body)
    except RecordError as error:
        return Hop(error.code, peer, status, headers)
    except HttpBodyLimit:
        return Hop("fetch-byte-limit", peer, status, headers)
    except http.client.IncompleteRead:
        return Hop("fetch-body-incomplete", peer, status, headers)
    except (TimeoutError, OSError, http.client.HTTPException, ValueError):
        return Hop("fetch-transport-failed", peer, status, headers)
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()


def observation(hop: Hop, redirect: str | None, content: BlobRef | None) -> dict:
    """Never persist arbitrary response headers or a credential-bearing Location."""
    return {"code": hop.code, "peer": hop.peer, "status": hop.status, "headers": dict(hop.headers),
            "location_digest": None if hop.location is None else BlobRef.of(hop.location.encode("utf-8")).as_dict(),
            "redirect": redirect, "content": None if content is None else content.as_dict()}
