"""Bounded readiness GET, supervised in a short-lived trusted service."""

import http.client
import sys

from behave.core.http import read_body


def main() -> None:
    # The parent supervises the complete probe against one absolute deadline.
    connection = http.client.HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=None)
    try:
        connection.request("GET", "/v1/models")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("readiness HTTP status")
        sys.stdout.buffer.write(read_body(response))
    finally:
        connection.close()
