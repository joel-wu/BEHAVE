"""Lossless wire encoding for native bitmaps and their immutable mapping.

Bit i is source event i. Compression changes only storage, never the event
coordinates or interval boundaries. A bitmap's decoded width comes from its
prepared plan, not a transport resource limit.
"""

from __future__ import annotations

import base64
import struct
import zlib


def pack(data: bytes) -> str:
    return base64.b64encode(zlib.compress(data)).decode("ascii")


def unpack(value: str, *, size: int | None = None) -> bytes:
    if type(value) is not str:
        raise ValueError("native evidence needs a base64 string")
    encoded = base64.b64decode(value, validate=True)
    if base64.b64encode(encoded).decode("ascii") != value:
        raise ValueError("native evidence has noncanonical base64 padding")
    decoder = zlib.decompressobj()
    try:
        data = decoder.decompress(encoded, 0 if size is None else size + 1)
    except zlib.error as error:
        raise ValueError("invalid compressed native evidence") from error
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("native evidence is truncated, oversized or has trailing data")
    if size is not None and len(data) != size:
        raise ValueError("native event bitset has the wrong width")
    return data


def event_bytes(value: str, count: int) -> bytes:
    data = unpack(value, size=(count + 7) // 8)
    if count % 8 and data[-1] >> (count % 8):
        raise ValueError("native event bitset has nonzero padding")
    return data


def events_from_bytes(data: bytes) -> frozenset[int]:
    return frozenset(iter_event_indices(data))


def iter_event_indices(data: bytes):
    """Visit set bits in nonzero words, rather than all eight bits of each byte."""
    padded = data + b"\0" * (-len(data) % 8)
    for index, (word,) in enumerate(struct.iter_unpack("<Q", padded)):
        while word:
            low = word & -word
            yield index * 64 + low.bit_length() - 1
            word ^= low
