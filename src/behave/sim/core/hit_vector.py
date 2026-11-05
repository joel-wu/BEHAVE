"""Compact, catalogue-bound structural hit vectors.

The goal catalogue fixes both the universe and its canonical order.  A slot therefore
needs two bitsets -- reached and unreadable -- rather than one repeated goal string per
bit.  Every remaining catalogue position is false.  This representation is bounded by
``O(|Gamma| / 8)`` for sparse and dense designs alike.
"""

from __future__ import annotations

import base64
import binascii
from types import MappingProxyType

from .frozen import Frozen

__all__ = ["HIT_VECTOR_ENCODING", "HitVector", "HitVectorCodec"]


HIT_VECTOR_ENCODING = "slot-hits-bitset/1"
_CODEC_TOKEN = object()
# Exact byte decoding, shared by sparse and dense vectors in catalogue order.
_SET_BITS = tuple(tuple((bit, 1 << bit) for bit in range(8) if value & (1 << bit))
                  for value in range(256))


def _reached_union(vectors):
    """Reached names from one trace's already-validated, common-catalogue vectors.

    OR the packed bits before expanding names. Unreadable positions never count
    as reached; the original vectors retain all three-valued slot evidence.
    """
    return _bit_union(vectors, "_true")


def _unreadable_union(vectors):
    """Unknown names without expanding every reached goal of every slot."""
    return _bit_union(vectors, "_unknown")


def _bit_union(vectors, field):
    # Both callers supply validated vectors under one frozen catalogue.
    vectors = iter(vectors)
    first = next(vectors, None)
    if first is None:
        return frozenset()
    bits = getattr(first, field)
    reached = int.from_bytes(bits, "little")
    for vector in vectors:
        reached |= int.from_bytes(getattr(vector, field), "little")
    return frozenset(
        first.catalogue[byte * 8 + bit]
        for byte, value in enumerate(reached.to_bytes(len(bits), "little"))
        for bit, _ in _SET_BITS[value])


def _catalogue(value, path):
    if type(value) not in (tuple, list):
        raise TypeError(f"{path} must be a finite tuple or list")
    names = tuple(value)
    if any(type(name) is not str or not name for name in names):
        raise TypeError(f"{path} must contain exact nonempty strings")
    if names != tuple(sorted(names)) or len(names) != len(set(names)):
        raise ValueError(f"{path} must name each goal once in canonical order")
    return names


def _names(value, path):
    if type(value) is dict or type(value) in (tuple, list, set, frozenset):
        names = tuple(value)
    else:
        raise TypeError(f"{path} must be a finite set or sequence of goal names")
    if any(type(name) is not str or not name for name in names):
        raise TypeError(f"{path} must contain exact nonempty strings")
    if len(names) != len(set(names)):
        raise ValueError(f"{path} names one goal more than once")
    return frozenset(names)


def _wire_bits(value, size, path):
    if type(value) is not str:
        raise TypeError(f"{path} must be a base64 string")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError(f"{path} is not canonical base64") from exc
    expected = (size + 7) // 8
    if len(raw) != expected:
        raise ValueError(
            f"{path} has {len(raw)} bytes for a {size}-goal catalogue")
    if size % 8 and raw and raw[-1] & ~((1 << (size % 8)) - 1):
        raise ValueError(f"{path} sets padding bits outside the catalogue")
    if base64.b64encode(raw).decode("ascii") != value:
        raise ValueError(f"{path} is not canonical base64")
    return raw


class HitVector(Frozen):
    """One complete three-valued vector relative to a frozen goal catalogue.

    Iteration retains the established ``(goal, value)`` interface and yields only true
    or unreadable positions.  Consumers interpret every absent catalogue goal as false.
    The stored form remains two immutable bitsets and one shared catalogue tuple.
    """

    __slots__ = ("_true", "_unknown", "catalogue")

    def __init__(self, catalogue, true_bits, unknown_bits, *, _token=None):
        self.catalogue = (catalogue if _token is _CODEC_TOKEN
                          else _catalogue(catalogue, "hit-vector.catalogue"))
        size = len(self.catalogue)
        if type(true_bits) is not bytes or type(unknown_bits) is not bytes:
            raise TypeError("hit-vector bitsets must be exact bytes")
        expected = (size + 7) // 8
        if len(true_bits) != expected or len(unknown_bits) != expected:
            raise ValueError("hit-vector bitsets do not match the catalogue size")
        if size % 8:
            mask = ~((1 << (size % 8)) - 1)
            if (true_bits[-1] | unknown_bits[-1]) & mask:
                raise ValueError("hit-vector sets padding bits outside the catalogue")
        if any(left & right for left, right in zip(true_bits, unknown_bits)):
            raise ValueError("one structural goal cannot be both reached and unreadable")
        self._true = true_bits
        self._unknown = unknown_bits
        self._freeze()

    @classmethod
    def from_names(cls, catalogue, reached=(), unreadable=()):
        return HitVectorCodec(catalogue).vector(reached, unreadable)

    @classmethod
    def from_compact_dict(cls, catalogue, value):
        return HitVectorCodec(catalogue).decode(value)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {
                "encoding", "catalogue", "true", "unknown"}:
            raise TypeError("a standalone hit-vector has no closed wire shape")
        if value["encoding"] != HIT_VECTOR_ENCODING:
            raise ValueError("a standalone hit-vector uses an unsupported encoding")
        return cls.from_compact_dict(value["catalogue"], {
            "true": value["true"], "unknown": value["unknown"]})

    def as_compact_dict(self):
        return {
            "true": base64.b64encode(self._true).decode("ascii"),
            "unknown": base64.b64encode(self._unknown).decode("ascii"),
        }

    def as_dict(self):
        return {
            "encoding": HIT_VECTOR_ENCODING,
            "catalogue": list(self.catalogue),
            **self.as_compact_dict(),
        }

    def __iter__(self):
        # Skip false positions without scanning every catalogue name. A fixed
        # byte lookup keeps the dense case linear too, without big-integer copies.
        for byte, (reached, unreadable) in enumerate(zip(self._true, self._unknown)):
            base = byte * 8
            for bit, mask in _SET_BITS[reached | unreadable]:
                yield self.catalogue[base + bit], True if reached & mask else None

    def __len__(self):
        return sum(byte.bit_count() for byte in self._true + self._unknown)

    def __eq__(self, other):
        return type(other) is HitVector and (
            self.catalogue == other.catalogue
            and self._true == other._true
            and self._unknown == other._unknown)

    def __repr__(self):
        return f"HitVector({len(self.catalogue)} goals, {len(self)} explicit)"


class HitVectorCodec(Frozen):
    """Validate a catalogue once, then encode or decode every slot against it."""

    __slots__ = ("_positions", "catalogue")

    def __init__(self, catalogue):
        self.catalogue = _catalogue(catalogue, "hit-vector.catalogue")
        self._positions = MappingProxyType({
            name: index for index, name in enumerate(self.catalogue)})
        self._freeze()

    def vector(self, reached=(), unreadable=()):
        reached = _names(reached, "hit-vector.reached")
        unreadable = _names(unreadable, "hit-vector.unreadable")
        overlap = reached & unreadable
        if overlap:
            raise ValueError(
                f"structural goals are both reached and unreadable: {sorted(overlap)[:8]}")
        outside = {name for name in reached | unreadable
                   if name not in self._positions}
        if outside:
            raise ValueError(
                f"hit-vector names goals outside its catalogue: {sorted(outside)[:8]}")
        true_bits = bytearray((len(self.catalogue) + 7) // 8)
        unknown_bits = bytearray(len(true_bits))
        for names, target in ((reached, true_bits), (unreadable, unknown_bits)):
            for name in names:
                index = self._positions[name]
                target[index // 8] |= 1 << (index % 8)
        return HitVector(
            self.catalogue, bytes(true_bits), bytes(unknown_bits),
            _token=_CODEC_TOKEN)

    def decode(self, value):
        if type(value) is not dict or set(value) != {"true", "unknown"}:
            raise TypeError(
                "a compact hit-vector must contain exactly true and unknown bitsets")
        return HitVector(
            self.catalogue,
            _wire_bits(value["true"], len(self.catalogue), "hit-vector.true"),
            _wire_bits(value["unknown"], len(self.catalogue), "hit-vector.unknown"),
            _token=_CODEC_TOKEN,
        )

    def from_bits(self, reached: bytes, unreadable: bytes | None = None):
        """Build from catalogue-ordered bytes, with the ordinary bitset checks."""
        return HitVector(
            self.catalogue, reached,
            bytes((len(self.catalogue) + 7) // 8) if unreadable is None else unreadable,
            _token=_CODEC_TOKEN,
        )
