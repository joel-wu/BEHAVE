"""Task-owned recursive value types.

``FiniteType`` says which integer values fit through one finite carrier.  It
does not say that a value is Boolean, a record, an enumeration, or a fixed
array.  Those are semantic facts shared by the task contract and the source
frontend, and neither a candidate artifact nor a concrete runtime value may
choose them.

This module is deliberately independent of Behavior's representation.  A
``ValueType`` describes the logical value.  The Behavior frontend separately
binds each logical scalar leaf to a SolverIR variable/index path; keeping the
two paths separate is what permits, for example, an array of records to use a
structure-of-arrays representation without changing the task type.
"""

from __future__ import annotations

from .frozen import Frozen

__all__ = [
    "BOOLEAN_VALUE_TYPE",
    "VALUE_TYPE_VERSION",
    "ValueType",
    "ValueTypeError",
    "as_value_type",
]


VALUE_TYPE_VERSION = "value-type/1"


class ValueTypeError(ValueError):
    """A recursive type declaration or one of its values is malformed."""

    __slots__ = ("code",)

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ValueType(Frozen):
    """One closed recursive logical value type.

    Construct values through the named class methods.  Enumeration ordinals
    are defined by the declared member order.  The resulting ``tag_map`` is
    nevertheless stored and serialized explicitly: no consumer is permitted
    to sort names, use a host-language enum value, or mint a different tag.
    """

    __slots__ = (
        "carrier",
        "cells",
        "element",
        "fields",
        "kind",
        "members",
        "tag_map",
    )

    _KINDS = frozenset({"boolean", "integer", "array", "record", "enumeration"})

    def __init__(self, kind, *, carrier=None, cells=None, element=None,
                 fields=(), members=(), tag_map=()):
        # The import is intentionally local.  ``core.contract`` re-exports
        # ValueType for task declarations, while FiniteType predates this
        # module; importing the whole contract while it is defining Field
        # would turn that compatibility surface into an import cycle.
        from .contract import FiniteType

        if kind not in self._KINDS:
            raise ValueTypeError(
                "value-type-kind-invalid", f"unknown value type kind {kind!r}")
        self.kind = kind
        self.carrier = carrier
        self.cells = cells
        self.element = element
        self.fields = tuple(fields)
        self.members = tuple(members)
        self.tag_map = tuple(tag_map)

        if kind == "boolean":
            if any(value not in (None, (), 0) for value in (
                    carrier, cells, element, self.fields, self.members,
                    self.tag_map)):
                raise ValueTypeError(
                    "value-type-boolean-malformed",
                    "a Boolean type has no integer carrier or child schema")
        elif kind == "integer":
            if type(carrier) is not FiniteType:
                raise ValueTypeError(
                    "value-type-integer-carrier-invalid",
                    "an Integer value type needs one exact FiniteType carrier")
            if cells is not None or element is not None or self.fields \
                    or self.members or self.tag_map:
                raise ValueTypeError(
                    "value-type-integer-malformed",
                    "an Integer value type has only its finite carrier")
        elif kind == "array":
            if type(cells) is not int or type(cells) is bool or cells < 0:
                raise ValueTypeError(
                    "value-type-array-length-invalid",
                    "a fixed array needs a natural-number cell count")
            if type(element) is not ValueType:
                raise ValueTypeError(
                    "value-type-array-element-invalid",
                    "a fixed array element needs one exact ValueType")
            if carrier is not None or self.fields or self.members or self.tag_map:
                raise ValueTypeError(
                    "value-type-array-malformed",
                    "a fixed array has only its count and element type")
        elif kind == "record":
            if carrier is not None or cells is not None or element is not None \
                    or self.members or self.tag_map:
                raise ValueTypeError(
                    "value-type-record-malformed",
                    "a record type has only its named field types")
            names = tuple(name for name, _value_type in self.fields)
            if (not names or any(type(name) is not str or not name for name in names)
                    or len(names) != len(set(names))
                    or any(type(child) is not ValueType
                           for _name, child in self.fields)):
                raise ValueTypeError(
                    "value-type-record-fields-invalid",
                    "a record needs unique nonempty names and exact ValueTypes")
            if names != tuple(sorted(names)):
                raise ValueTypeError(
                    "value-type-record-fields-not-canonical",
                    "record fields are stored in canonical name order")
        else:
            if carrier is not None or cells is not None or element is not None \
                    or self.fields:
                raise ValueTypeError(
                    "value-type-enumeration-malformed",
                    "an enumeration has only its members and stable tag map")
            if (not self.members
                    or any(type(member) is not str or not member
                           for member in self.members)
                    or len(self.members) != len(set(self.members))):
                raise ValueTypeError(
                    "value-type-enumeration-members-invalid",
                    "an enumeration needs unique nonempty members")
            canonical = tuple((member, index)
                              for index, member in enumerate(self.members))
            if (any(type(row) not in (tuple, list) or len(row) != 2
                    or type(row[0]) is not str
                    or type(row[1]) is not int or type(row[1]) is bool
                    for row in self.tag_map)
                    or len({row[0] for row in self.tag_map})
                    != len(self.tag_map)):
                raise ValueTypeError(
                    "value-type-enumeration-tags-invalid",
                    "enumeration tags are unique exact string/integer pairs")
            if self.tag_map != canonical:
                raise ValueTypeError(
                    "value-type-enumeration-tags-invalid",
                    "enumeration tags are the explicit ordinals of declared "
                    "member order")
        self._freeze()

    @classmethod
    def boolean(cls):
        return BOOLEAN_VALUE_TYPE

    @classmethod
    def integer(cls, carrier):
        return cls("integer", carrier=carrier)

    @classmethod
    def array(cls, cells, element):
        return cls("array", cells=cells, element=as_value_type(element))

    @classmethod
    def record(cls, fields):
        items = tuple(fields.items()) if type(fields) is dict else tuple(fields)
        normalized = []
        seen = set()
        for item in items:
            if type(item) not in (tuple, list) or len(item) != 2:
                raise ValueTypeError(
                    "value-type-record-fields-invalid",
                    "a record field is one name/type pair")
            name, child = item
            if type(name) is not str or not name or name in seen:
                raise ValueTypeError(
                    "value-type-record-fields-invalid",
                    "record field names are unique nonempty strings")
            seen.add(name)
            normalized.append((name, as_value_type(child)))
        return cls("record", fields=tuple(sorted(normalized)))

    @classmethod
    def enumeration(cls, members, tag_map=None):
        members = tuple(members)
        # Member order is itself a declaration, so deriving its ordinal map is
        # deterministic rather than inference from a runtime enum.  The map is
        # materialized below and travels in every identity/wire image.
        canonical = tuple((member, index)
                          for index, member in enumerate(members))
        if tag_map is None:
            tags = canonical
        elif type(tag_map) is dict:
            if set(tag_map) != set(members):
                raise ValueTypeError(
                    "value-type-enumeration-tags-invalid",
                    "an explicit enumeration tag map covers exactly its members")
            tags = tuple((member, tag_map[member]) for member in members)
        else:
            try:
                rows = tuple(tag_map)
            except TypeError:
                raise ValueTypeError(
                    "value-type-enumeration-tags-invalid",
                    "an explicit enumeration tag map is a finite pair sequence") \
                    from None
            if any(type(row) not in (tuple, list) or len(row) != 2
                   for row in rows):
                raise ValueTypeError(
                    "value-type-enumeration-tags-invalid",
                    "an explicit enumeration tag map is a finite pair sequence")
            names = tuple(row[0] for row in rows)
            if len(names) != len(set(names)) or set(names) != set(members):
                raise ValueTypeError(
                    "value-type-enumeration-tags-invalid",
                    "an explicit enumeration tag map covers each member once")
            supplied = dict(rows)
            tags = tuple((member, supplied[member]) for member in members)
        return cls("enumeration", members=members, tag_map=tags)

    @property
    def finite_carrier(self):
        """The scalar integer carrier, where this type has exactly one."""
        from .contract import FiniteType

        if self.kind == "integer":
            return self.carrier
        if self.kind == "enumeration":
            return FiniteType(max(1, (len(self.members) - 1).bit_length()))
        return None

    @property
    def bounds(self):
        """Compatibility projection for an integer-valued scalar only."""
        if self.kind != "integer":
            raise AttributeError("only an Integer ValueType has scalar bounds")
        return self.carrier.bounds

    @property
    def width(self):
        if self.kind != "integer":
            raise AttributeError("only an Integer ValueType has one scalar width")
        return self.carrier.width

    @property
    def signed(self):
        if self.kind != "integer":
            raise AttributeError(
                "only an Integer ValueType has scalar signedness")
        return self.carrier.signed

    def admits(self, value):
        """Whether ``value`` has the exact declared recursive host shape."""
        if self.kind == "boolean":
            return type(value) is bool
        if self.kind == "integer":
            return self.carrier.admits(value)
        if self.kind == "enumeration":
            return type(value) is str and value in self.members
        if self.kind == "array":
            return (type(value) in (tuple, list) and len(value) == self.cells
                    and all(self.element.admits(item) for item in value))
        # Task values are frozen on every core-record boundary.  Accept the two
        # closed internal mapping carriers in addition to the public dict, but
        # never an arbitrary Mapping whose lookup can execute caller code.
        from .frozen import FrozenMap
        from .record_value import FrozenValueMap

        return (type(value) in (dict, FrozenMap, FrozenValueMap)
                and set(value) == {name for name, _child in self.fields}
                and all(child.admits(value[name])
                        for name, child in self.fields))

    def faults(self, value, *, path="value"):
        """Describe every local reason ``value`` is outside this type.

        ``admits`` is the membership predicate used by semantic code.  This
        companion is deliberately diagnostic: it preserves the recursive path
        and, for integer leaves, the actual finite range.  Keeping the two on
        the same type authority avoids flattening a structured declaration to
        the unhelpful message "outside array/record declaration".
        """
        if self.kind == "boolean":
            return () if type(value) is bool else (
                f"{path}={value!r} is not a Boolean",)
        if self.kind == "integer":
            if self.carrier.admits(value):
                return ()
            low, high = self.carrier.bounds
            return (f"{path}={value!r} outside {low}..{high}",)
        if self.kind == "enumeration":
            if type(value) is str and value in self.members:
                return ()
            return (f"{path}={value!r} is not one of {self.members!r}",)
        if self.kind == "array":
            if type(value) not in (tuple, list):
                return (f"{path}={value!r} is not a fixed array",)
            if len(value) != self.cells:
                return (
                    f"{path} has {len(value)} cells rather than {self.cells}",)
            return tuple(
                fault
                for index, item in enumerate(value)
                for fault in self.element.faults(
                    item, path=f"{path}[{index}]")
            )

        from .frozen import FrozenMap
        from .record_value import FrozenValueMap

        carriers = (dict, FrozenMap, FrozenValueMap)
        if type(value) not in carriers:
            return (f"{path}={value!r} is not a record",)
        declared = {name for name, _child in self.fields}
        present = set(value)
        faults = []
        missing = sorted(declared - present)
        extra = sorted(present - declared)
        if missing:
            faults.append(f"{path} is missing record fields {missing!r}")
        if extra:
            faults.append(f"{path} has undeclared record fields {extra!r}")
        for name, child in self.fields:
            if name in present:
                faults.extend(child.faults(
                    value[name], path=f"{path}.{name}"))
        return tuple(faults)

    @property
    def identity(self):
        if self.kind == "boolean":
            body = ("boolean",)
        elif self.kind == "integer":
            body = ("integer", self.carrier.width, self.carrier.signed)
        elif self.kind == "array":
            body = ("array", self.cells, self.element.identity)
        elif self.kind == "record":
            body = ("record", tuple(
                (name, child.identity) for name, child in self.fields))
        else:
            body = ("enumeration", self.members, self.tag_map)
        return (VALUE_TYPE_VERSION,) + body

    def as_dict(self):
        if self.kind == "boolean":
            return {"version": VALUE_TYPE_VERSION, "kind": "boolean"}
        if self.kind == "integer":
            return {"version": VALUE_TYPE_VERSION, "kind": "integer",
                    "carrier": self.carrier.as_dict()}
        if self.kind == "array":
            return {"version": VALUE_TYPE_VERSION, "kind": "array",
                    "cells": self.cells, "element": self.element.as_dict()}
        if self.kind == "record":
            return {"version": VALUE_TYPE_VERSION, "kind": "record",
                    "fields": [
                        {"name": name, "type": child.as_dict()}
                        for name, child in self.fields
                    ]}
        return {"version": VALUE_TYPE_VERSION, "kind": "enumeration",
                "members": list(self.members),
                "tag_map": [[name, tag] for name, tag in self.tag_map]}

    @classmethod
    def from_dict(cls, value):
        from .contract import FiniteType

        if type(value) is not dict or value.get("version") != VALUE_TYPE_VERSION:
            raise ValueTypeError(
                "value-type-image-invalid",
                "a ValueType image has the supported explicit version")
        kind = value.get("kind")
        if kind == "boolean" and set(value) == {"version", "kind"}:
            return BOOLEAN_VALUE_TYPE
        if kind == "integer" and set(value) == {
                "version", "kind", "carrier"}:
            return cls.integer(FiniteType.from_dict(value["carrier"]))
        if kind == "array" and set(value) == {
                "version", "kind", "cells", "element"}:
            got = cls.array(value["cells"], cls.from_dict(value["element"]))
            if got.as_dict() != value:
                raise ValueTypeError(
                    "value-type-image-not-canonical",
                    "an array ValueType image is not canonical")
            return got
        if kind == "record" and set(value) == {
                "version", "kind", "fields"}:
            rows = value["fields"]
            if type(rows) is not list or any(
                    type(row) is not dict or set(row) != {"name", "type"}
                    or type(row["name"]) is not str for row in rows):
                raise ValueTypeError(
                    "value-type-image-invalid",
                    "record ValueType fields have exact name/type images")
            got = cls.record(tuple(
                (row["name"], cls.from_dict(row["type"])) for row in rows))
            if got.as_dict() != value:
                raise ValueTypeError(
                    "value-type-image-not-canonical",
                    "a record ValueType image is not in canonical field order")
            return got
        if kind == "enumeration" and set(value) == {
                "version", "kind", "members", "tag_map"}:
            members, rows = value["members"], value["tag_map"]
            if type(members) is not list or type(rows) is not list \
                    or any(type(member) is not str for member in members) \
                    or any(type(row) is not list or len(row) != 2
                           or type(row[0]) is not str or type(row[1]) is not int
                           or type(row[1]) is bool for row in rows):
                raise ValueTypeError(
                    "value-type-image-invalid",
                    "an enumeration image has exact members and integer tags")
            got = cls.enumeration(tuple(members), tuple(map(tuple, rows)))
            if got.as_dict() != value:
                raise ValueTypeError(
                    "value-type-image-not-canonical",
                    "an enumeration ValueType image is not canonical")
            return got
        raise ValueTypeError(
            "value-type-image-invalid",
            "a ValueType image has exactly the fields for its declared kind")

    def __eq__(self, other):
        return type(other) is ValueType and self.identity == other.identity

    def __hash__(self):
        return hash(self.identity)

    def __reduce__(self):
        return _value_type_from_identity, (self.identity,)

    def __repr__(self):
        if self.kind == "integer":
            # Preserve the established finite-carrier spelling in boundary
            # diagnostics while identity remains the explicit ValueType.
            return repr(self.carrier)
        if self.kind == "boolean":
            return "BooleanValueType()"
        if self.kind == "array":
            return f"FixedArrayValueType({self.cells}, {self.element!r})"
        if self.kind == "record":
            return f"RecordValueType({dict(self.fields)!r})"
        return f"EnumerationValueType({self.members!r}, {dict(self.tag_map)!r})"


def _value_type_from_identity(identity):
    version, kind, *body = identity
    if version != VALUE_TYPE_VERSION:
        raise ValueError("unsupported pickled ValueType identity")
    from .contract import FiniteType

    if kind == "boolean":
        return BOOLEAN_VALUE_TYPE
    if kind == "integer":
        width, signed = body
        return ValueType.integer(FiniteType(width, signed))
    if kind == "array":
        cells, element = body
        return ValueType.array(cells, _value_type_from_identity(element))
    if kind == "record":
        return ValueType.record(tuple(
            (name, _value_type_from_identity(child))
            for name, child in body[0]))
    if kind == "enumeration":
        members, tags = body
        return ValueType.enumeration(members, tags)
    raise ValueError("unknown pickled ValueType kind")


def as_value_type(value):
    """Return an exact ValueType; FiniteType is the legacy Integer shorthand."""
    if type(value) is ValueType:
        return value
    from .contract import FiniteType

    if type(value) is FiniteType:
        return ValueType.integer(value)
    raise TypeError("a value schema is an exact ValueType or legacy FiniteType")


# Constructed without the public classmethod to avoid reading the module global
# before it exists.
BOOLEAN_VALUE_TYPE = ValueType("boolean")
