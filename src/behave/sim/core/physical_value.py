"""Canonical packed-pin representation of a declared recursive value.

A :class:`ValueType` describes a logical value, while a signal-level artifact
offers one bit-vector port for each task field.  This module is the single
declaration-driven bridge between them.  Scalar leaves are concatenated in
logical layout order with the first leaf in the least-significant bits; array
indices are increasing and record fields use their canonical declared order.

The codec is closed and versioned.  Concrete replay, admission and symbolic RTL
analysis must all consume this definition rather than independently flattening
or guessing a runtime value's shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from .frozen import FrozenMap
from .record_value import FrozenValueMap, freeze_record_value
from .value_type import ValueType, as_value_type

__all__ = [
    "PHYSICAL_VALUE_LAYOUT_VERSION",
    "PackedLeaf",
    "PhysicalValueError",
    "is_valid_pattern",
    "pack_value",
    "packed_leaves",
    "packed_signed",
    "packed_width",
    "unpack_signal_value",
    "unpack_value",
    "zero_value",
]


PHYSICAL_VALUE_LAYOUT_VERSION = "packed-value/1"


class PhysicalValueError(ValueError):
    """A logical value or packed pattern does not inhabit its declaration."""

    __slots__ = ("code",)

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PackedLeaf:
    """One scalar leaf's declared slice in a packed physical port."""

    path: tuple[str | int, ...]
    value_type: ValueType
    offset: int
    width: int

    @property
    def identity(self):
        return (
            PHYSICAL_VALUE_LAYOUT_VERSION,
            self.path,
            self.value_type.identity,
            self.offset,
            self.width,
        )


def _leaf_width(value_type):
    if value_type.kind == "boolean":
        return 1
    if value_type.kind == "integer":
        return value_type.carrier.width
    if value_type.kind == "enumeration":
        return max(1, (len(value_type.members) - 1).bit_length())
    raise TypeError("only scalar ValueType leaves have one packed width")


def packed_leaves(value_type):
    """Return scalar slices in canonical least-significant-first order."""

    value_type = as_value_type(value_type)
    leaves = []
    offset = 0

    def visit(current, path):
        nonlocal offset
        if current.kind in {"boolean", "integer", "enumeration"}:
            width = _leaf_width(current)
            leaves.append(PackedLeaf(path, current, offset, width))
            offset += width
            return
        if current.kind == "array":
            for index in range(current.cells):
                visit(current.element, (*path, index))
            return
        for name, child in current.fields:
            visit(child, (*path, name))

    visit(value_type, ())
    return tuple(leaves)


def packed_width(value_type):
    """Number of physical bits, without materializing every array element."""

    return _packed_width(as_value_type(value_type))


def _packed_width(value_type):
    if value_type.kind in {"boolean", "integer", "enumeration"}:
        return _leaf_width(value_type)
    if value_type.kind == "array":
        if value_type.cells == 0:
            return 0  # no visit to the element type in the leaf-wise definition
        return value_type.cells * _packed_width(value_type.element)
    return sum(_packed_width(child) for _name, child in value_type.fields)


def packed_signed(value_type):
    """Signedness expected of the physical port carrying this value.

    A lone Integer preserves its declared signedness.  Every structured value
    is a concatenation of independently typed leaves and is therefore an
    unsigned bit-vector at the physical boundary.
    """

    value_type = as_value_type(value_type)
    return (value_type.carrier.signed
            if value_type.kind == "integer" else False)


def _mapping(value, path):
    if type(value) is dict:
        return value
    if type(value) in (FrozenMap, FrozenValueMap):
        return dict(value.items())
    raise PhysicalValueError(
        "physical-value-record-shape",
        f"{path} must be an exact string-keyed record")


def _logical_at(value_type, value, path):
    current_type = value_type
    current = value
    at = "value"
    for part in path:
        if current_type.kind == "array":
            if type(current) not in (tuple, list) \
                    or len(current) != current_type.cells:
                raise PhysicalValueError(
                    "physical-value-array-shape",
                    f"{at} must contain exactly {current_type.cells} cells")
            current = current[part]
            current_type = current_type.element
            at += f"[{part}]"
        else:
            mapping = _mapping(current, at)
            names = {name for name, _child in current_type.fields}
            if set(mapping) != names:
                raise PhysicalValueError(
                    "physical-value-record-shape",
                    f"{at} fields differ from {sorted(names)!r}")
            current = mapping[part]
            current_type = dict(current_type.fields)[part]
            at += f".{part}"
    return current_type, current


def _encode_leaf(value_type, value, path):
    if value_type.kind == "boolean":
        if type(value) is not bool:
            raise PhysicalValueError(
                "physical-value-boolean-invalid",
                f"{path} must be an exact Boolean")
        return int(value)
    if value_type.kind == "integer":
        if not value_type.carrier.admits(value):
            raise PhysicalValueError(
                "physical-value-integer-outside-carrier",
                f"{path} is outside {value_type.carrier!r}")
        return value & ((1 << value_type.carrier.width) - 1)
    if type(value) is not str or value not in value_type.members:
        raise PhysicalValueError(
            "physical-value-enumeration-invalid",
            f"{path} is not a declared enumeration member")
    return dict(value_type.tag_map)[value]


def pack_value(value_type, value):
    """Encode one exact logical value as its nonnegative port bit pattern."""

    value_type = as_value_type(value_type)
    if value_type.kind in {"boolean", "integer", "enumeration"}:
        # A scalar is already its only leaf: no path traversal or layout objects.
        return _encode_leaf(value_type, value, "value")
    width = _packed_width(value_type)
    if width == 0:
        raise PhysicalValueError(
            "physical-value-carrier-empty",
            "an empty recursive value has no physical port bit")
    pattern = 0
    for leaf in packed_leaves(value_type):
        leaf_type, scalar = _logical_at(value_type, value, leaf.path)
        raw = _encode_leaf(leaf_type, scalar, "value" + "".join(
            f"[{part}]" if type(part) is int else f".{part}"
            for part in leaf.path))
        pattern |= raw << leaf.offset
    return pattern


def _decode_leaf(value_type, raw):
    if value_type.kind == "boolean":
        return bool(raw)
    if value_type.kind == "integer":
        width = value_type.carrier.width
        if value_type.carrier.signed and raw & (1 << (width - 1)):
            return raw - (1 << width)
        return raw
    by_tag = {tag: member for member, tag in value_type.tag_map}
    try:
        return by_tag[raw]
    except KeyError:
        raise PhysicalValueError(
            "physical-value-enumeration-tag-invalid",
            f"packed enumeration tag {raw} is not declared") from None


def _reconstruct(value_type, leaves, prefix=()):
    if value_type.kind in {"boolean", "integer", "enumeration"}:
        return leaves[prefix]
    if value_type.kind == "array":
        return tuple(_reconstruct(value_type.element, leaves, (*prefix, index))
                     for index in range(value_type.cells))
    return freeze_record_value({
        name: _reconstruct(child, leaves, (*prefix, name))
        for name, child in value_type.fields
    })


def unpack_value(value_type, pattern):
    """Decode one valid port bit pattern to an immutable logical value."""

    value_type = as_value_type(value_type)
    width = _packed_width(value_type)
    return _unpack(value_type, pattern, width)


def unpack_signal_value(value_type, pattern):
    """Decode a simulator scalar or canonical packed pattern using one layout.

    Only a signed Integer port admits a negative mathematical presentation;
    recursive values remain unsigned concatenations. Logical decoding and all
    invalid-pattern errors are shared with ``unpack_value``.
    """
    value_type = as_value_type(value_type)
    width = _packed_width(value_type)
    if (type(pattern) is int and value_type.kind == "integer"
            and value_type.carrier.signed and -(1 << (width - 1)) <= pattern < 0):
        pattern += 1 << width
    return _unpack(value_type, pattern, width)


def _unpack(value_type, pattern, width):
    if width == 0:
        raise PhysicalValueError(
            "physical-value-carrier-empty",
            "an empty recursive value has no physical port bit")
    if type(pattern) is not int \
            or not 0 <= pattern < (1 << width):
        raise PhysicalValueError(
            "physical-value-pattern-outside-width",
            f"a {width}-bit physical value needs an integer pattern in range")
    if value_type.kind in {"boolean", "integer", "enumeration"}:
        return _decode_leaf(value_type, pattern)
    values = {}
    for leaf in packed_leaves(value_type):
        raw = (pattern >> leaf.offset) & ((1 << leaf.width) - 1)
        values[leaf.path] = _decode_leaf(leaf.value_type, raw)
    return _reconstruct(value_type, values)


def is_valid_pattern(value_type, pattern):
    """Whether ``pattern`` decodes without an unused enumeration tag."""

    try:
        unpack_value(value_type, pattern)
    except (PhysicalValueError, TypeError):
        return False
    return True


def zero_value(value_type):
    """The logical value represented by the all-zero physical pattern."""

    return unpack_value(value_type, 0)
