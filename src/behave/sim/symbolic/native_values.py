"""Exact, finite images of plain native values.

The construction witness, sealed state contract and replay audit all need the
same answer to one question: which Python values can cross a verifier boundary
without losing type, contents or identity-sensitive behavior?  This module is
that authority.  Membership is by exact type; cycles, subclasses, opaque
objects, and NaN-bearing set members or mapping keys are outside the domain.
"""

from __future__ import annotations

import math
import struct

from behave.core import runtime_defaults as defaults

__all__ = [
    "DEFAULT_WIRE_BYTES",
    "DEFAULT_WIRE_NODES",
    "NativeValueError",
    "NativeValueLimit",
    "ValueWireEncoder",
    "WireDecoder",
    "WireEncoder",
    "clone",
    "freeze",
    "from_wire",
    "thaw",
    "typed_equal",
    "wire_image",
]


# Optional operator budgets. Carrier membership itself has no byte/node quota;
# exact types, tree shape and canonical decoding remain independently checked.
DEFAULT_WIRE_BYTES = defaults.SIM_NATIVE_VALUE_WIRE_BYTES
DEFAULT_WIRE_NODES = defaults.SIM_NATIVE_VALUE_WIRE_NODES
_WIRE_NODE_BYTES = 32


class NativeValueError(ValueError):
    """A value has no faithful finite image in the admitted carrier set."""


class NativeValueLimit(NativeValueError):
    """A value exceeded an explicit carrier-size bound."""


def _image_contains_nan(image):
    """Whether a bounded frozen image contains a binary64 NaN."""
    pending = [image]
    while pending:
        item = pending.pop()
        kind = item[0]
        if kind == "float":
            value = struct.unpack("<d", item[1])[0]
            if math.isnan(value):
                return True
        elif kind in ("list", "tuple", "set", "frozenset"):
            pending.extend(item[1])
        elif kind == "dict":
            for key, value in item[1]:
                pending.extend((key, value))
    return False


def _freeze(value, path, seen_mutable, consume, consume_scalar=None, *,
            reject_shared=True):
    consume()
    kind = type(value)
    if value is None:
        return ("none",)
    if kind in (bool, int, str, bytes):
        if consume_scalar is not None:
            consume_scalar(value)
        return (kind.__name__, value)
    if kind is float:
        if consume_scalar is not None:
            consume_scalar(value)
        return ("float", struct.pack("<d", value))
    if kind is bytearray:
        if consume_scalar is not None:
            consume_scalar(value)
        identity = id(value)
        if reject_shared and identity in seen_mutable:
            raise NativeValueError(
                "a repeated mutable object has no tree-shaped finite image")
        if reject_shared:
            seen_mutable.add(identity)
        return ("bytearray", bytes(value))
    if kind not in (list, tuple, set, frozenset, dict):
        raise NativeValueError(
            f"a value of exact type {kind.__name__} has no finite image")

    path = set() if path is None else path
    identity = id(value)
    if identity in path:
        raise NativeValueError("a cyclic value has no finite image")
    if kind in (list, set, dict):
        if reject_shared and identity in seen_mutable:
            raise NativeValueError(
                "a repeated mutable object has no tree-shaped finite image")
        if reject_shared:
            seen_mutable.add(identity)
    path.add(identity)
    try:
        if kind is dict:
            pairs = []
            for key, item in value.items():
                frozen_key = _freeze(
                    key, path, seen_mutable, consume, consume_scalar,
                    reject_shared=reject_shared)
                if _image_contains_nan(frozen_key):
                    raise NativeValueError(
                        "a NaN-bearing mapping key is identity-sensitive")
                pairs.append((frozen_key,
                              _freeze(item, path, seen_mutable, consume,
                                      consume_scalar,
                                      reject_shared=reject_shared)))
            return ("dict", tuple(pairs))
        if kind in (set, frozenset):
            frozen = []
            for item in value:
                image = _freeze(
                    item, path, seen_mutable, consume, consume_scalar,
                    reject_shared=reject_shared)
                if _image_contains_nan(image):
                    raise NativeValueError(
                        "a NaN-bearing set member is identity-sensitive")
                frozen.append(image)
            items = tuple(sorted(frozen, key=repr))
            return (kind.__name__, items)
        return (kind.__name__, tuple(
            _freeze(item, path, seen_mutable, consume, consume_scalar,
                    reject_shared=reject_shared)
            for item in value))
    finally:
        path.discard(identity)


def freeze(value):
    """Return a hashable, type-tagged, lossless image of ``value``."""
    try:
        return _freeze(value, set(), set(), lambda: None)
    except RecursionError:
        raise NativeValueLimit(
            "the native-value carrier is nested too deeply") from None


def thaw(image):
    """Reconstruct a fresh native value from a trusted frozen image."""
    kind = image[0]
    if kind == "none":
        return None
    payload = image[1]
    if kind in ("bool", "int", "str", "bytes"):
        return payload
    if kind == "float":
        return struct.unpack("<d", payload)[0]
    if kind == "bytearray":
        return bytearray(payload)
    if kind == "list":
        return [thaw(item) for item in payload]
    if kind == "tuple":
        return tuple(thaw(item) for item in payload)
    if kind == "set":
        return {thaw(item) for item in payload}
    if kind == "frozenset":
        return frozenset(thaw(item) for item in payload)
    if kind == "dict":
        return {thaw(key): thaw(item) for key, item in payload}
    raise NativeValueError(f"unknown native-value image tag {kind!r}")


def clone(value):
    """A deep, type-preserving copy, or ``NativeValueError``."""
    image = freeze(value)
    try:
        return thaw(image)
    except RecursionError:
        # ``freeze`` and ``thaw`` traverse the same tree, but their Python
        # frame costs are not identical.  A value near the interpreter's
        # recursion ceiling must remain a typed carrier refusal rather than
        # escape the audit as a host exception after it was frozen.
        raise NativeValueLimit(
            "the native-value carrier is nested too deeply") from None


def typed_equal(left, right):
    """Exact native-value equality; unsupported values compare unequal."""
    try:
        return freeze(left) == freeze(right)
    except NativeValueError:
        return False


def _image_to_wire(image):
    """Turn a trusted frozen image into JSON-native protocol data."""
    kind = image[0]
    if kind == "none":
        return {"t": "none"}
    payload = image[1]
    if kind in ("bool", "int", "str"):
        return {"t": kind, "v": payload}
    if kind in ("float", "bytes", "bytearray"):
        return {"t": kind, "v": payload.hex()}
    if kind in ("list", "tuple", "set", "frozenset"):
        return {"t": kind, "v": [
            _image_to_wire(item) for item in payload
        ]}
    if kind == "dict":
        return {"t": "dict", "v": [
            [_image_to_wire(key), _image_to_wire(item)]
            for key, item in payload
        ]}
    raise NativeValueError(f"unknown native-value image tag {kind!r}")


def _json_string_bytes(value):
    """Bytes used by ``json.dumps`` for one string with ``ensure_ascii``.

    Counting without first materialising the escaped string is important: a
    scalar that is going to be refused must not allocate its oversized wire
    representation merely to discover its size.
    """
    size = 2  # opening and closing quote
    short_escapes = {8, 9, 10, 12, 13}
    for char in value:
        code = ord(char)
        if char in {'"', "\\"} or code in short_escapes:
            size += 2
        elif code < 0x20:
            size += 6
        elif code < 0x80:
            size += 1
        elif code <= 0xFFFF:
            size += 6
        else:
            size += 12
    return size


def _integer_wire_bytes(value):
    magnitude = abs(value)
    if magnitude == 0:
        digits = 1
    else:
        # 30103 / 100000 is a slight upper approximation of log10(2).
        digits = (magnitude.bit_length() * 30103) // 100000 + 1
    return digits + (value < 0)


def _native_scalar_wire_bytes(value):
    """Conservative JSON payload bytes for one exact native scalar."""
    kind = type(value)
    if kind is bool:
        return 4 if value else 5
    if kind is int:
        return _integer_wire_bytes(value)
    if kind is str:
        return _json_string_bytes(value)
    if kind in (bytes, bytearray):
        return 2 * len(value) + 2  # hexadecimal payload plus quotes
    if kind is float:
        return 18  # sixteen hexadecimal digits plus quotes
    raise NativeValueError(
        f"a value of exact type {kind.__name__} has no wire scalar image")


def _encoded_scalar_wire_bytes(kind, payload):
    """The same charge, before a wire scalar is decoded or copied."""
    if kind == "bool":
        return 4 if payload else 5
    if kind == "int":
        return _integer_wire_bytes(payload)
    if kind == "str":
        return _json_string_bytes(payload)
    if kind in ("float", "bytes", "bytearray"):
        # These payloads are already hexadecimal JSON strings.  Charge them
        # before ``bytes.fromhex`` allocates the native value.
        return len(payload) + 2
    raise NativeValueError(f"unknown native-value scalar tag {kind!r}")


class WireEncoder:
    """Encode values under aggregate native-value node and byte budgets."""

    __slots__ = ("_remaining_bytes", "_remaining_nodes")

    def __init__(self, max_nodes, *, max_bytes=DEFAULT_WIRE_BYTES):
        if max_nodes is not None and (type(max_nodes) is not int or max_nodes < 1):
            raise ValueError("a native-value node budget must be positive")
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
            raise ValueError("a native-value byte budget must be positive")
        self._remaining_nodes = max_nodes
        self._remaining_bytes = max_bytes

    def _consume_node(self):
        if self._remaining_nodes is not None:
            self._remaining_nodes -= 1
            if self._remaining_nodes < 0:
                raise NativeValueLimit(
                    "the native-value carrier exceeds its node budget")
        self._consume_bytes(_WIRE_NODE_BYTES)

    def _consume_bytes(self, amount):
        if self._remaining_bytes is not None:
            self._remaining_bytes -= amount
            if self._remaining_bytes < 0:
                raise NativeValueLimit(
                    "the native-value carrier exceeds its wire byte budget")

    def _consume_scalar(self, value):
        if self._remaining_bytes is not None:
            self._consume_bytes(_native_scalar_wire_bytes(value))

    def encode(self, value):
        """Return the JSON-native exact encoding of one value."""
        try:
            return _image_to_wire(_freeze(
                value, set(), set(), self._consume_node,
                self._consume_scalar))
        except RecursionError:
            raise NativeValueLimit(
                "the native-value carrier is nested too deeply") from None


class ValueWireEncoder(WireEncoder):
    """Encode one task-level value tree, where Python alias identity is irrelevant.

    State and audit carriers use :class:`WireEncoder`: two paths to one mutable
    Python object can affect a later step and therefore cannot be collapsed into a
    tree.  A completed Behavior return is different.  Its contract observes only the
    recursively typed value at each transaction field, so repeated references are
    copied into equal subtrees.  Cycles and values outside the finite carrier remain
    refused.
    """

    def encode(self, value):
        try:
            return _image_to_wire(_freeze(
                value, set(), set(), self._consume_node,
                self._consume_scalar, reject_shared=False))
        except RecursionError:
            raise NativeValueLimit(
                "the native-value carrier is nested too deeply") from None


def _wire_image(encoded, consume_node, consume_scalar):
    """Validate protocol data and recover its frozen native-value image."""
    consume_node()
    if type(encoded) is not dict or type(encoded.get("t")) is not str:
        raise NativeValueError("malformed native-value wire image")
    kind = encoded["t"]
    payload = encoded.get("v")
    if kind == "none":
        if set(encoded) != {"t"}:
            raise NativeValueError("malformed native-value None")
        return ("none",)
    if set(encoded) != {"t", "v"}:
        raise NativeValueError("malformed native-value wire fields")
    scalar_types = {"bool": bool, "int": int, "str": str}
    if kind in scalar_types:
        if type(payload) is not scalar_types[kind]:
            raise NativeValueError(f"malformed native-value {kind}")
        consume_scalar(kind, payload)
        return (kind, payload)
    if kind in ("float", "bytes", "bytearray"):
        if type(payload) is not str:
            raise NativeValueError(f"malformed native-value {kind}")
        consume_scalar(kind, payload)
        try:
            raw = bytes.fromhex(payload)
        except ValueError:
            raise NativeValueError(
                f"malformed native-value {kind}") from None
        if kind == "float" and len(raw) != 8:
            raise NativeValueError("malformed native-value float")
        if raw.hex() != payload:
            raise NativeValueError(
                f"noncanonical native-value {kind} payload")
        return (kind, raw)
    if kind in ("list", "tuple", "set", "frozenset"):
        if type(payload) is not list:
            raise NativeValueError("malformed native-value sequence")
        children = tuple(
            _wire_image(item, consume_node, consume_scalar)
            for item in payload)
        return (kind, children)
    if kind == "dict":
        if type(payload) is not list:
            raise NativeValueError("malformed native-value mapping")
        pairs = []
        for pair in payload:
            if type(pair) is not list or len(pair) != 2:
                raise NativeValueError(
                    "malformed native-value mapping entry")
            pairs.append((
                _wire_image(pair[0], consume_node, consume_scalar),
                _wire_image(pair[1], consume_node, consume_scalar)))
        return ("dict", tuple(pairs))
    raise NativeValueError(f"unknown native-value wire tag {kind!r}")


class WireDecoder:
    """Decode values under aggregate native-value node and byte budgets."""

    __slots__ = ("_remaining_bytes", "_remaining_nodes")

    def __init__(self, max_nodes, *, max_bytes=DEFAULT_WIRE_BYTES):
        if max_nodes is not None and (type(max_nodes) is not int or max_nodes < 1):
            raise ValueError("a native-value node budget must be positive")
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
            raise ValueError("a native-value byte budget must be positive")
        self._remaining_nodes = max_nodes
        self._remaining_bytes = max_bytes

    def _consume_node(self):
        if self._remaining_nodes is not None:
            self._remaining_nodes -= 1
            if self._remaining_nodes < 0:
                raise NativeValueLimit(
                    "the native-value carrier exceeds its node budget")
        self._consume_bytes(_WIRE_NODE_BYTES)

    def _consume_bytes(self, amount):
        if self._remaining_bytes is not None:
            self._remaining_bytes -= amount
            if self._remaining_bytes < 0:
                raise NativeValueLimit(
                    "the native-value carrier exceeds its wire byte budget")

    def _consume_scalar(self, kind, payload):
        if self._remaining_bytes is not None:
            self._consume_bytes(_encoded_scalar_wire_bytes(kind, payload))

    def image(self, encoded):
        """Validate one wire value and return its canonical frozen image.

        Decoding and freezing again is intentional.  It rejects protocol trees
        that do not denote a native value faithfully, such as duplicate set
        members, colliding mapping keys, or an identity-sensitive NaN key.
        """
        try:
            image = _wire_image(
                encoded, self._consume_node, self._consume_scalar)
            value = thaw(image)
            canonical = freeze(value)
        except RecursionError:
            raise NativeValueLimit(
                "the native-value carrier is nested too deeply") from None
        except (TypeError, ValueError) as exc:
            if isinstance(exc, NativeValueError):
                raise
            raise NativeValueError(
                f"a wire image does not denote a native value: {exc}") \
                from None
        if canonical != image:
            raise NativeValueError(
                "a wire image is not a canonical native value")
        return canonical

    def decode(self, encoded):
        """Decode one validated wire value as a fresh exact native value."""
        return thaw(self.image(encoded))


def wire_image(encoded, *, max_nodes=DEFAULT_WIRE_NODES,
               max_bytes=DEFAULT_WIRE_BYTES):
    """Validate one wire value and return its canonical frozen image."""
    return WireDecoder(max_nodes, max_bytes=max_bytes).image(encoded)


def from_wire(encoded, *, max_nodes=DEFAULT_WIRE_NODES,
              max_bytes=DEFAULT_WIRE_BYTES):
    """Decode one validated wire value as a fresh exact native value."""
    return WireDecoder(max_nodes, max_bytes=max_bytes).decode(encoded)
