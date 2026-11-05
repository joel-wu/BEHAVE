"""One recursive flatten/reconstruct contract for structured Behavior values.

SolverIR itself carries Boolean, mathematical Integer and recursive fixed-array
values.  Source records and enumerations are finite products/sums that a
frontend may eliminate into those leaves, but that elimination is sound only
when every boundary uses the same injective map.  This module is that map.

The contract is intentionally independent of source syntax and of the exact
encoder.  It can therefore be shared by input admission, initial-state
construction and output reconstruction.  A layout does not by itself admit a
new source form: lowering must attach the declared layout and bind every leaf
to a SolverIR variable.  Until that binding exists, the existing typed
capability refusals remain the honest boundary.

Ordinary :class:`IntegerLayout` leaves denote mathematical integers.  An
optional :class:`~behave.sim.core.contract.FiniteType` refines the finite
boundary carrier without changing arithmetic denotation.  Boolean has its own
exact host carrier and cannot acquire integer meaning from ``FiniteType(1)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.contract import FiniteType
from ..core.value_type import as_value_type
from . import types

__all__ = [
    "BOOLEAN_LAYOUT",
    "VALUE_LAYOUT_VERSION",
    "BooleanLayout",
    "EnumerationLayout",
    "FixedArrayLayout",
    "IntegerLayout",
    "LeafContract",
    "RecordLayout",
    "ValueLayout",
    "ValueLayoutError",
    "layout_of_type",
    "layout_of_value_type",
]


VALUE_LAYOUT_VERSION = "behavior-value-layout/1"


class ValueLayoutError(ValueError):
    """A value or flattened image disagrees with its declared layout."""

    __slots__ = ("code",)

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LeafContract:
    """One scalar leaf in a structured value's canonical order."""

    path: tuple[str | int, ...]
    semantic_type: object
    carrier: FiniteType | None = None

    def __post_init__(self):
        if type(self.path) is not tuple or any(
                type(part) not in (str, int) or type(part) is bool
                or (type(part) is str and not part)
                or (type(part) is int and part < 0)
                for part in self.path):
            raise TypeError("a value-layout leaf has a malformed path")
        if self.semantic_type not in (types.BOOLEAN, types.INTEGER):
            raise TypeError("a value-layout leaf must have one scalar type")
        if self.carrier is not None and type(self.carrier) is not FiniteType:
            raise TypeError("a value-layout leaf carrier is an exact FiniteType")
        if self.semantic_type == types.BOOLEAN and self.carrier is not None:
            raise TypeError(
                "a Boolean leaf cannot be reinterpreted through an integer carrier")


class ValueLayout:
    """Closed protocol implemented by the five immutable layout constructors."""

    __slots__ = ()

    def flatten(self, value):
        raise NotImplementedError

    def _reconstruct_at(self, values, index):
        raise NotImplementedError

    def leaves(self, path=()):
        raise NotImplementedError

    @property
    def identity(self):
        raise NotImplementedError

    @property
    def finite(self):
        return all(leaf.semantic_type == types.BOOLEAN
                   or leaf.carrier is not None for leaf in self.leaves())

    def reconstruct(self, flattened):
        """Reconstruct exactly one value and reject trailing or missing leaves."""
        if type(flattened) not in (tuple, list):
            raise ValueLayoutError(
                "value-layout-flat-image-not-sequence",
                "a flattened value is an exact tuple or list of scalar leaves",
            )
        values = tuple(flattened)
        value, consumed = self._reconstruct_at(values, 0)
        if consumed != len(values):
            raise ValueLayoutError(
                "value-layout-flat-arity-mismatch",
                f"the flattened value has {len(values)} leaves but the layout "
                f"consumes {consumed}",
            )
        return value


@dataclass(frozen=True, slots=True)
class BooleanLayout(ValueLayout):
    """The exact two-valued Boolean carrier."""

    def flatten(self, value):
        if type(value) is not bool:
            raise ValueLayoutError(
                "value-layout-boolean-mismatch",
                f"a Boolean layout cannot flatten {value!r}",
            )
        return (value,)

    def _reconstruct_at(self, values, index):
        if index >= len(values) or type(values[index]) is not bool:
            raise ValueLayoutError(
                "value-layout-boolean-image-invalid",
                "a Boolean flattened leaf is exactly True or False",
            )
        return values[index], index + 1

    def leaves(self, path=()):
        return (LeafContract(tuple(path), types.BOOLEAN),)

    @property
    def identity(self):
        return (VALUE_LAYOUT_VERSION, "boolean")


BOOLEAN_LAYOUT = BooleanLayout()


@dataclass(frozen=True, slots=True)
class IntegerLayout(ValueLayout):
    """A mathematical Integer, optionally refined by a finite task carrier."""

    carrier: FiniteType | None = None

    def __post_init__(self):
        if self.carrier is not None and type(self.carrier) is not FiniteType:
            raise TypeError("an Integer layout carrier is an exact FiniteType")

    def _check(self, value, what):
        if type(value) is not int:
            raise ValueLayoutError(
                "value-layout-integer-mismatch",
                f"an Integer {what} is an exact int, not {type(value).__name__}",
            )
        if self.carrier is not None and not self.carrier.admits(value):
            raise ValueLayoutError(
                "value-layout-integer-outside-carrier",
                f"Integer {value!r} is outside {self.carrier}",
            )
        return value

    def flatten(self, value):
        return (self._check(value, "value"),)

    def _reconstruct_at(self, values, index):
        if index >= len(values):
            raise ValueLayoutError(
                "value-layout-flat-arity-mismatch",
                "an Integer flattened leaf is missing",
            )
        return self._check(values[index], "flattened leaf"), index + 1

    def leaves(self, path=()):
        return (LeafContract(tuple(path), types.INTEGER, self.carrier),)

    @property
    def identity(self):
        domain = (None if self.carrier is None else
                  (self.carrier.width, self.carrier.signed))
        return (VALUE_LAYOUT_VERSION, "integer", domain)


def _layout(value, what):
    if type(value) not in (
            BooleanLayout, IntegerLayout, FixedArrayLayout,
            RecordLayout, EnumerationLayout):
        raise TypeError(f"{what} is not a closed ValueLayout")
    return value


@dataclass(frozen=True, slots=True, init=False)
class FixedArrayLayout(ValueLayout):
    """A fixed-length homogeneous recursive sequence layout."""

    cells: int
    element: ValueLayout

    def __init__(self, cells, element):
        if type(cells) is not int or cells < 0:
            raise ValueError("a fixed-array layout needs a natural cell count")
        object.__setattr__(self, "cells", cells)
        object.__setattr__(self, "element", _layout(element, "array element"))

    def flatten(self, value):
        if type(value) not in (tuple, list) or len(value) != self.cells:
            raise ValueLayoutError(
                "value-layout-array-shape-mismatch",
                f"a fixed array needs exactly {self.cells} cells",
            )
        return tuple(
            leaf for item in value for leaf in self.element.flatten(item))

    def _reconstruct_at(self, values, index):
        out = []
        for _position in range(self.cells):
            value, index = self.element._reconstruct_at(values, index)
            out.append(value)
        return out, index

    def leaves(self, path=()):
        path = tuple(path)
        return tuple(
            leaf
            for index in range(self.cells)
            for leaf in self.element.leaves(path + (index,))
        )

    @property
    def identity(self):
        return (VALUE_LAYOUT_VERSION, "array", self.cells,
                self.element.identity)


@dataclass(frozen=True, slots=True, init=False)
class RecordLayout(ValueLayout):
    """A fixed-schema product, canonicalized independently of field order."""

    fields: tuple[tuple[str, ValueLayout], ...]

    def __init__(self, fields):
        items = tuple(fields.items()) if type(fields) is dict else tuple(fields)
        out = []
        seen = set()
        for item in items:
            if type(item) not in (tuple, list) or len(item) != 2:
                raise TypeError("a record layout field is a name/layout pair")
            name, layout = item
            if type(name) is not str or not name or name in seen:
                raise ValueError(
                    "a record layout needs unique nonempty string fields")
            seen.add(name)
            out.append((name, _layout(layout, f"record field {name!r}")))
        if not out:
            raise ValueError("an empty record has no flattening schema")
        object.__setattr__(self, "fields", tuple(sorted(out)))

    def flatten(self, value):
        if type(value) is not dict or set(value) != {
                name for name, _layout_ in self.fields}:
            raise ValueLayoutError(
                "value-layout-record-schema-mismatch",
                "a record value must have exactly its declared fields",
            )
        return tuple(
            leaf
            for name, layout in self.fields
            for leaf in layout.flatten(value[name])
        )

    def _reconstruct_at(self, values, index):
        out = {}
        for name, layout in self.fields:
            out[name], index = layout._reconstruct_at(values, index)
        return out, index

    def leaves(self, path=()):
        path = tuple(path)
        return tuple(
            leaf
            for name, layout in self.fields
            for leaf in layout.leaves(path + (name,))
        )

    @property
    def identity(self):
        return (VALUE_LAYOUT_VERSION, "record",
                tuple((name, layout.identity) for name, layout in self.fields))


@dataclass(frozen=True, slots=True, init=False)
class EnumerationLayout(ValueLayout):
    """A finite named sum with an injective ordinal tag representation."""

    members: tuple[str, ...]
    tag_map: tuple[tuple[str, int], ...]
    carrier: FiniteType

    def __init__(self, members, tag_map=None):
        members = tuple(members)
        if (not members
                or any(type(member) is not str or not member for member in members)
                or len(members) != len(set(members))):
            raise ValueError(
                "an enumeration layout needs unique nonempty string members")
        canonical = tuple((member, index)
                          for index, member in enumerate(members))
        if tag_map is None:
            tag_map = canonical
        elif type(tag_map) is dict:
            tag_map = tuple((member, tag_map[member]) for member in members
                            if member in tag_map)
        else:
            supplied = dict(tag_map)
            tag_map = tuple((member, supplied[member]) for member in members
                            if member in supplied)
        if tag_map != canonical:
            raise ValueError(
                "enumeration tags must be the explicit ordinals of declared "
                "member order")
        width = max(1, (len(members) - 1).bit_length())
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "tag_map", tag_map)
        object.__setattr__(self, "carrier", FiniteType(width))

    def flatten(self, value):
        if type(value) is not str or value not in self.members:
            raise ValueLayoutError(
                "value-layout-enum-member-invalid",
                f"{value!r} is not a member of the declared enumeration",
            )
        return (dict(self.tag_map)[value],)

    def _reconstruct_at(self, values, index):
        if index >= len(values) or type(values[index]) is not int:
            raise ValueLayoutError(
                "value-layout-enum-tag-invalid",
                "an enumeration tag is an exact integer",
            )
        tag = values[index]
        inverse = {ordinal: member for member, ordinal in self.tag_map}
        if tag not in inverse:
            raise ValueLayoutError(
                "value-layout-enum-tag-invalid",
                f"enumeration tag {tag!r} has no declared member",
            )
        return inverse[tag], index + 1

    def leaves(self, path=()):
        return (LeafContract(tuple(path), types.INTEGER, self.carrier),)

    @property
    def identity(self):
        return (VALUE_LAYOUT_VERSION, "enumeration", self.members,
                self.tag_map)


def layout_of_value_type(value_type):
    """Build the one Behavior layout of a declared recursive ValueType.

    This conversion reads only the declaration.  In particular it never sees
    a concrete host value, so a runtime mapping cannot silently become a
    record and a runtime string cannot mint an enumeration member.
    """
    value_type = as_value_type(value_type)
    if value_type.kind == "boolean":
        return BOOLEAN_LAYOUT
    if value_type.kind == "integer":
        return IntegerLayout(value_type.carrier)
    if value_type.kind == "array":
        return FixedArrayLayout(
            value_type.cells, layout_of_value_type(value_type.element))
    if value_type.kind == "record":
        return RecordLayout(tuple(
            (name, layout_of_value_type(child))
            for name, child in value_type.fields))
    if value_type.kind == "enumeration":
        return EnumerationLayout(value_type.members, value_type.tag_map)
    raise TypeError(f"{value_type!r} is not a closed declared ValueType")


def layout_of_type(kind, carrier=None):
    """Lift one recursive SolverIR type into the shared value-layout contract.

    ``carrier`` refines every Integer leaf.  Passing one for Boolean is
    deliberately rejected: a one-bit integer carrier is not a Boolean type.
    """
    if kind == types.BOOLEAN:
        if carrier is not None:
            raise TypeError("a Boolean layout does not accept an integer carrier")
        return BOOLEAN_LAYOUT
    if kind == types.INTEGER:
        return IntegerLayout(carrier)
    if isinstance(kind, types.Array):
        return FixedArrayLayout(
            kind.cells, layout_of_type(kind.elem, carrier=carrier))
    raise TypeError(f"{kind!r} is not a recursive SolverIR value type")
