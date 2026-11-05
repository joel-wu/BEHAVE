"""Legacy source-only Behavior diagnostics' literal RTL-header reader.

The schema is explicitly candidate-owned and marked HEADER_REGEX. It is not a
task contract or public RTL admission: those use the isolated elaborator.
Nonliteral widths are refused; continued declarations retain their width and
signedness. No compiler result or symbolic relation is synthesized here.
"""

from __future__ import annotations

import re

from ..core.schema import Authority, Endpoint, InterfaceError, Reading, TransactionSchema

__all__ = ["InterfaceError", "read_ports"]

_HEADER = re.compile(r"module\s+\w+\s*(?:#\s*\(.*?\)\s*)?\((.*?)\);", re.DOTALL)
_PORT = re.compile(
    r"^\s*(?:(?P<dir>input|output|inout)\b)?"
    r"(?P<attrs>(?:\s*(?:wire|logic|reg|bit|signed|unsigned))*)"
    r"(?:\s*\[(?P<hi>[^:\]]+):(?P<lo>[^\]]+)\])?"
    r"\s*(?P<name>\w+)\s*$", re.DOTALL)


def _bound(text):
    text = text.strip()
    try:
        return int(text, 0)
    except ValueError:
        raise InterfaceError(f"a width bound that is not a literal: {text}")


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", text)


def read_ports(source, name="the candidate"):
    """The ports a design's header declares, as a schema it claims to realise.

    A port declared without a range is one bit. A port declared with literal
    bounds is as many bits as they span. Any other port stops the read.

    The header is read one comma-separated name at a time, and a name that
    states no direction of its own continues the declaration before it. That is
    the rule the language states, and it was applied to the direction alone: the
    range and the signedness belong to the declaration just as much, so
    ``input logic [7:0] a, b`` read `b` as one bit. Everything after the comma is
    the same declaration, so it carries the same everything.

    A name that does state a direction starts a declaration, and then what it
    does not say it does not have: `input [7:0] a, input b` is eight bits and
    one, which is what it means.
    """
    header = _HEADER.search(_strip_comments(source))
    if header is None:
        raise InterfaceError("no module header")
    endpoints, carried = [], None
    for piece in header.group(1).split(","):
        if not piece.strip():
            continue
        m = _PORT.match(piece)
        if m is None:
            raise InterfaceError(f"a port declaration this cannot read: "
                                 f"{piece.strip()}")
        if m.group("dir") is not None:
            carried = (m.group("dir"), m.group("hi"), m.group("lo"),
                       m.group("attrs") or "")
        elif carried is None:
            raise InterfaceError(f"{m.group('name')} has no direction")
        elif m.group("hi") is not None:
            # A range without a direction is a declaration this cannot place:
            # the language does not write one, so guessing whose it is would be
            # inventing a rule rather than following one.
            raise InterfaceError(f"a port declaration this cannot read: "
                                 f"{piece.strip()}")
        direction, hi, lo, attrs = carried
        if hi is None:
            width = 1
        else:
            width = abs(_bound(hi) - _bound(lo)) + 1
        endpoints.append(Endpoint(m.group("name"), direction, width,
                                  "signed" in attrs))
    if not endpoints:
        raise InterfaceError("a module header declaring no ports")
    return TransactionSchema(endpoints, source=f"read from {name}'s header",
                             authority=Authority.CANDIDATE,
                             reading=Reading.HEADER_REGEX)
