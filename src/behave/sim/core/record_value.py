"""The finite value carrier shared by immutable core records.

Core records cross process, JSON and digest boundaries.  Letting each record copy its
values independently is not harmless: ``int(value)`` identifies ``True`` and ``1``,
``str(key)`` identifies distinct mapping keys, a shallow tuple still contains live
lists, and the standard JSON encoder admits non-finite numbers.  This module is the one
authority for that boundary.

The semantic carrier is JSON-like: exact ``None``, ``bool``, ``int``, finite ``float``
and ``str`` scalars; string-keyed mappings; and finite ordered sequences.  A Python list
and tuple are two input carriers for the same semantic sequence and both freeze to a
tuple.  No other conversion is performed.  In particular subclasses and arbitrary
iterables are refused rather than interpreted through caller-owned methods.
"""

from __future__ import annotations

import collections.abc
import hashlib
import json
import math
import struct
from collections import OrderedDict
from types import MappingProxyType

from behave.core import runtime_defaults as defaults

from .frozen import FrozenMap

__all__ = [
    "FrozenValueMap",
    "canonical_json",
    "freeze_record_value",
    "record_pairs",
    "same_record_value",
    "thaw_record_value",
    "write_canonical_json",
]

_UNINDEXED = object()
_PROJECTED = object()


class RecordRowDecoder:
    """Preserve aliases while freezing a reader-owned, unmodified row sequence.

    Only identical objects share a result, never merely equal values. Hold the
    source alongside its immutable projection so id reuse cannot alias rows.
    The cache belongs to one decode; eviction changes allocation, not evidence.
    """

    def __init__(self, decode):
        self.decode = decode
        self.capacity = defaults.SIM_RECORD_PROJECTION_CACHE_ENTRIES
        self.recent = OrderedDict()

    def __call__(self, row):
        identity = id(row)
        if identity in self.recent:
            self.recent.move_to_end(identity)
            return self.recent[identity][1]
        result = self.decode(row)
        if self.capacity:
            self.recent[identity] = row, result
            if len(self.recent) > self.capacity:
                self.recent.popitem(last=False)
        return result


def _immutable_identity_value(value):
    """Prove a cheap, immutable JSON projection; otherwise use eager validation.

    The limits select an optimization, not the accepted value domain. In
    particular, Unicode encoding and Python's large-integer conversion errors
    must still be raised by the constructor, as they were before deferral.
    """
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        kind = type(item)
        if item is None or kind is bool:
            continue
        if kind is int and item.bit_length() <= 2048:
            continue  # below Python's minimum configurable decimal digit limit
        if kind is str and item.isascii():
            continue
        if kind is float and math.isfinite(item):
            continue
        if kind is tuple and depth < 64:
            pending.extend((child, depth + 1) for child in item)
            continue
        return False
    return True


class FrozenValueMap(collections.abc.Mapping):
    """A recursively immutable semantic mapping with stable public key order.

    A run envelope is also a public JSON document, so freezing must not reorder an
    already-valid record.  Places that need a canonical order (pair records and JSON
    digests) request it explicitly through :func:`record_pairs` or
    :func:`canonical_json`.
    """

    __slots__ = ("_index", "_pairs", "_projected", "_hash")

    def __init__(self, pairs, *, _token=None):
        if hasattr(self, "_pairs"):
            raise AttributeError("a frozen record-value mapping cannot be reinitialized")
        pairs = tuple(pairs)
        object.__setattr__(self, "_pairs", pairs)
        object.__setattr__(self, "_index", _UNINDEXED)
        object.__setattr__(self, "_projected", _token is _PROJECTED)
        object.__setattr__(self, "_hash", None)

    def __getitem__(self, key):
        if type(key) is str:
            index = self._index
            if index is _UNINDEXED:
                # Only queried maps need an index. Large frozen witnesses are
                # often serialized by pairs without ever looking up their keys.
                # Mutable pair wrappers/foreign keys keep the original scan;
                # hashing caller-owned keys is not part of carrier validation.
                index = None
                if all(type(pair) is tuple and len(pair) == 2 and type(pair[0]) is str
                       for pair in self._pairs):
                    first = {}
                    for name, value in self._pairs:
                        first.setdefault(name, value)
                    index = MappingProxyType(first)
                # Concurrent readers can publish only the same read-only index;
                # the ordered bindings and nested values never change here.
                object.__setattr__(self, "_index", index)
            if index is not None:
                return index[key]
        for name, value in self._pairs:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return iter(name for name, _ in self._pairs)

    def __len__(self):
        return len(self._pairs)

    def __setattr__(self, name, value):
        raise AttributeError("a frozen record-value mapping cannot be changed")

    def __delattr__(self, name):
        raise AttributeError("a frozen record-value mapping cannot be changed")

    def __hash__(self):
        if not self._projected:
            return hash(frozenset(self._pairs))
        # A checked projection is recursively immutable. Shared query domains
        # need not traverse the same state for every goal; public wrappers may
        # still contain caller-owned values and deliberately keep eager hashing.
        if self._hash is None:
            object.__setattr__(self, "_hash", hash(frozenset(self._pairs)))
        return self._hash

    def __eq__(self, other):
        return (type(other) is FrozenValueMap and len(self) == len(other)
                and all(key in other and same_record_value(value, other[key])
                        for key, value in self._pairs))

    def pairs(self):
        return self._pairs

    def __repr__(self):
        return f"FrozenValueMap({dict(self._pairs)!r})"

    def __reduce__(self):
        # Stimuli cross spawn-process boundaries.  Reconstruct through the public
        # constructor instead of asking pickle to assign the write-protected slot.
        return (_projected_map if self._projected else FrozenValueMap, (self._pairs,))


def _projected_map(pairs):
    # Only the validated projection and trusted process transport use this.
    # Directly constructed wrappers can still contain caller-owned lists.
    return FrozenValueMap(pairs, _token=_PROJECTED)


_MAPPING_TYPES = (dict, FrozenMap, FrozenValueMap)
_ATOM_TYPES = (bool, int, str, type(None))


def _mapping_items(value, path):
    """Return mapping items without accepting an arbitrary mapping implementation.

    The tuple/list-of-pairs form is the canonical public representation used by the
    older core records.  It is accepted only where a caller explicitly asks for a
    mapping through :func:`record_pairs`; nested pair-shaped sequences remain ordinary
    sequences.
    """
    if value is None:
        return ()
    kind = type(value)
    if kind is FrozenValueMap:
        # Mapping.items() would re-look up every key in this tuple-backed map.
        return value.pairs()
    if kind is dict:
        return value.items()
    if kind in _MAPPING_TYPES:
        return tuple(value.items())
    if kind not in (tuple, list):
        raise TypeError(
            f"{path} must be a string-keyed mapping or a sequence of key/value pairs, "
            f"not {kind.__name__}")
    items = []
    for index, pair in enumerate(value):
        if type(pair) not in (tuple, list) or len(pair) != 2:
            raise TypeError(f"{path}[{index}] is not a key/value pair")
        items.append((pair[0], pair[1]))
    return tuple(items)


def _project(value, path, active, *, json_view=False, validate_only=False, memo=None):
    # Freezing, detached JSON projection and validation-only encoding share the
    # same carrier rules. The last mode does not materialize a second graph.
    kind = type(value)
    if kind is FrozenValueMap and value._projected and not json_view:
        return value
    if kind in _ATOM_TYPES:
        return value
    if kind is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if kind not in (*_MAPPING_TYPES, list, tuple):
        raise TypeError(
            f"{path} contains a {kind.__name__}, outside the finite record carrier")

    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} contains a reference cycle")
    if memo is not None and kind is dict and identity in memo:
        memo.move_to_end(identity)
        return memo[identity][1]
    active.add(identity)
    try:
        if kind in _MAPPING_TYPES:
            pairs = []
            seen = set()
            for key, item in _mapping_items(value, path):
                if type(key) is not str:
                    raise TypeError(f"{path} has a non-string key {key!r}")
                if key in seen:
                    raise ValueError(f"{path} names {key!r} more than once")
                seen.add(key)
                # Exact immutable atoms need neither a recursive call nor an
                # error-path string. Containers and floats keep full validation.
                projected = item if type(item) in _ATOM_TYPES else _project(
                    item, f"{path}.{key}", active,
                    json_view=json_view, validate_only=validate_only, memo=memo)
                if not validate_only:
                    pairs.append((key, projected))
            if validate_only:
                return value
            result = dict(pairs) if json_view else _projected_map(pairs)
            if memo is not None and kind is dict:
                # Retain the input alongside its detached copy so a recycled id
                # cannot hit this invocation-local, bounded index.
                memo[identity] = value, result
                if len(memo) > defaults.SIM_RECORD_PROJECTION_CACHE_ENTRIES:
                    memo.popitem(last=False)
            return result
        if validate_only:
            previous = None  # a valid atom, not a foreign-object sentinel to skip
            for index, item in enumerate(value):
                # This synchronous validation pass has no writer callbacks.
                # Consecutive aliases name the same already-checked subtree;
                # retain one reference, not an index of the entire trace.
                if item is not previous and type(item) not in _ATOM_TYPES:
                    _project(item, f"{path}[{index}]", active, validate_only=True)
                previous = item
            return value
        if kind is tuple:
            # A tuple alone proves nothing about its children. Validate every
            # child and copy only after the first mutable/non-JSON branch changes.
            # The JSON encoder already encodes tuples exactly like lists.
            changed = None
            for index, item in enumerate(value):
                projected = item if type(item) in _ATOM_TYPES else _project(
                    item, f"{path}[{index}]", active, json_view=json_view, memo=memo)
                if changed is None and projected is not item:
                    changed = list(value[:index])
                if changed is not None:
                    changed.append(projected)
            return value if changed is None else tuple(changed)
        values = (item if type(item) in _ATOM_TYPES else
                  _project(item, f"{path}[{index}]", active, json_view=json_view, memo=memo)
                  for index, item in enumerate(value))
        return list(values) if json_view else tuple(values)
    except RecursionError:
        raise ValueError(f"{path} is nested too deeply") from None
    finally:
        active.discard(identity)


def freeze_record_value(value, path="record"):
    """Detach ``value`` into the exact, recursively immutable finite carrier."""
    memo = OrderedDict() if defaults.SIM_RECORD_PROJECTION_CACHE_ENTRIES else None
    return _project(value, path, set(), memo=memo)


def record_pairs(value, path="record"):
    """Freeze one string-keyed mapping as canonical sorted pairs.

    Unlike ``dict(value)`` this rejects duplicate pair rows, non-string keys and values
    outside the finite carrier.  It never stringifies or otherwise coerces a key.
    """
    items = _mapping_items(value, path)
    pairs = []
    seen = set()
    active = set()
    identity = id(value)
    if value is not None:
        active.add(identity)
    try:
        for key, item in items:
            if type(key) is not str:
                raise TypeError(f"{path} has a non-string key {key!r}")
            if key in seen:
                raise ValueError(f"{path} names {key!r} more than once")
            seen.add(key)
            pairs.append((key, item if type(item) in _ATOM_TYPES else
                          _project(item, f"{path}.{key}", active)))
    finally:
        active.discard(identity)
    return tuple(sorted(pairs))


def thaw_record_value(value):
    """Return a detached ordinary JSON-shaped view of a frozen record value."""
    kind = type(value)
    if kind is FrozenValueMap:
        return {key: thaw_record_value(item) for key, item in value.pairs()}
    if kind is tuple:
        return [thaw_record_value(item) for item in value]
    if value is None or kind in (bool, int, float, str):
        return value
    # This is an internal invariant, not another permissive serialization boundary.
    raise TypeError(f"{kind.__name__} is not a frozen record value")


def same_record_value(left, right):
    """Exact equality in the frozen carrier (so ``True`` is not ``1``)."""
    if type(left) is not type(right):
        return False
    if type(left) is FrozenValueMap:
        # Mapping insertion order is retained in the public JSON projection, but it
        # is not part of the semantic record value.  Comparing the stored pair tuple
        # would make two independently decoded copies disagree merely because their
        # producers inserted the same fields in a different order (and would disagree
        # with ``FrozenValueMap.__eq__`` itself).
        return len(left) == len(right) and all(
            key in right and same_record_value(value, right[key])
            for key, value in left.items())
    if type(left) is tuple:
        return len(left) == len(right) and all(
            same_record_value(a, b) for a, b in zip(left, right))
    if type(left) is float:
        return struct.pack(">d", left) == struct.pack(">d", right)
    return left == right


def canonical_json(value):
    """Encode one finite semantic value canonically and without JSON coercions."""
    # Validation does not need a detached copy of the whole trace or schedule.
    # Encoding completes synchronously and the returned string owns its bytes;
    # only immutable mapping wrappers need an adapter at their point of use.
    _project(value, "record", set(), validate_only=True)
    return _encode_json_projection(value, default=_json_mapping)


def write_canonical_json(value, stream, *, record_paths=()):
    """Write the canonical value synchronously without retaining encoded output.

    Validate the entire finite carrier before the first write. ``record_paths``
    names arrays whose items are independent encoding units; None in a path
    traverses every array element. These are schema boundaries, not size limits.
    Encode each record in one call without collecting the complete trace string.
    Unspecified schemas retain the general scalar-fragment streaming encoder.
    The caller owns atomic publication on I/O or encoding failure.
    """
    _project(value, "record", set(), validate_only=True)
    _write_json_projection(value, stream, record_paths=record_paths, default=_json_mapping)


def _write_json_projection(value, stream, *, record_paths=(), default=None):
    """Internal writer for an already validated/owned record projection."""
    encoder = _json_encoder(default=default)
    if not record_paths:
        stream.writelines(encoder.iterencode(value))
        return
    layout = {}
    for path in record_paths:
        node = layout
        for part in path:
            node = node.setdefault(part, {})
    _write_record_projection(value, stream, layout, encoder.encode)


class _DigestWriter:
    def __init__(self):
        self.digest = hashlib.sha256()

    def write(self, text):
        self.digest.update(text.encode("utf-8"))

    def writelines(self, chunks):
        for text in chunks:
            self.write(text)


def _digest_json_projection(value, *, record_paths=(), default=None):
    """Same UTF-8 identity bytes, without retaining the entire encoded trace."""
    stream = _DigestWriter()
    _write_json_projection(value, stream, record_paths=record_paths, default=default)
    return stream.digest.hexdigest()


def _write_record_projection(value, stream, layout, encode):
    """Encode a validated projection at its declared record boundaries."""
    kind = type(value)
    if layout is None:
        stream.write(encode(value))
    elif kind in _MAPPING_TYPES:
        stream.write("{")
        for index, (name, item) in enumerate(sorted(_mapping_items(value, "record"))):
            if index:
                stream.write(",")
            stream.write(encode(name))
            stream.write(":")
            _write_record_projection(item, stream, layout.get(name), encode)
        stream.write("}")
    elif kind in (list, tuple):
        stream.write("[")
        child = layout.get(None)
        for index, item in enumerate(value):
            if index:
                stream.write(",")
            _write_record_projection(item, stream, child, encode)
        stream.write("]")
    else:
        stream.write(encode(value))


def _json_mapping(value):
    if type(value) in (FrozenMap, FrozenValueMap):
        return dict(_mapping_items(value, "record"))
    raise TypeError(f"{type(value).__name__} is not a JSON record mapping")


def _json_encoder(*, default=None):
    return json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False, default=default)


def _encode_json_projection(value, *, default=None):
    """Encode an already validated JSON projection (internal only).

    Generic callers must use ``canonical_json``. Closed records such as
    AuditedTrace and NativeEventPlan validate their fields and build this
    projection themselves; they need not recursively copy it again to encode it.
    An owning record may supply an exact-type adapter for its immutable children;
    tuples already have the same JSON encoding as lists. This is not a trust rule
    for caller-created frozen wrappers.
    """
    return _json_encoder(default=default).encode(value)
