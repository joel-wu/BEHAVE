"""Finite logical value contracts, independent of source syntax and solving.

Frontends infer these immutable shapes; state projection and lowering consume
one shared algebra for products, optional values and bounded sequences. Host
carrier distinctions remain explicit because reconstruction observes them.
No AST traversal, source lookup, lowering or solver construction belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    'INCOMPATIBLE',
    'NONE',
    'SCALAR',
    'UNKNOWN',
    'ValueContract',
    'contract_at_record_path',
    'contract_contains',
    'contract_is_deeply_immutable',
    'contract_of_collection_elements',
    'contract_of_collection_lookup',
    'contract_of_value',
    'contract_with_record_path',
    'contracts_may_overlap',
    'finite_cardinality_add',
    'finite_cardinality_product',
    'fixed_positional_contracts',
    'join_contracts',
    'narrow_none',
    'optional_sequence',
    'representation_value',
    'sequence_elements_are_deeply_immutable',
]


def _cardinalities(values):
    values = tuple(values)
    if any(type(value) is not int for value in values):
        raise TypeError("source cardinalities must be exact integers")
    if any(value < 0 for value in values):
        raise ValueError("source cardinalities must be nonnegative")
    return values


def finite_cardinality_add(*terms):
    """Exact sum of finite logical cardinalities, without materialization."""
    return sum(_cardinalities(terms))


def finite_cardinality_product(*factors):
    """Exact product of finite logical cardinalities, without materialization."""
    product = 1
    for factor in _cardinalities(factors):
        product *= factor
    return product


@dataclass(frozen=True)
class ValueContract:
    """A recursive source-value representation contract.

    ``kind`` is ``unknown``, ``incompatible``, ``none``, ``scalar``,
    ``option``, ``sequence``, ``mapping`` or ``record``. ``mapping`` records
    integer-key dictionary cardinality and its value contract. ``option`` is
    the finite source sum ``None | payload``; a definitely present payload
    continues to use its ordinary contract directly.
    A sequence records its Python carrier, its logical length interval, and
    the contract of one element.  ``element=None`` means that an empty
    sequence has not observed an element yet; it is not the same as observing
    an element whose contract is unknown. A fixed positional sequence instead
    has ``components`` and no homogeneous ``element``; each position is lowered
    separately, preserving heterogeneous source products.
    """

    kind: str
    carrier: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    element: ValueContract | None = None
    observed_carriers: frozenset[str] = frozenset()
    unproved_carrier: bool = False
    fields: tuple[tuple[str, ValueContract], ...] = ()
    payload: ValueContract | None = None
    components: tuple[ValueContract, ...] | None = None

    @classmethod
    def positional(cls, carrier, components):
        """A fixed positional product, retaining unequal component contracts.

        Equal components use the ordinary homogeneous sequence. Mixed products
        (for example an enumerate index and an array item) are projected into
        separate IR values; they must not become heterogeneous SolverIR arrays.
        """
        components = tuple(components)
        element = None
        for child in components:
            element = _element_join(element, child)
        if element is not INCOMPATIBLE:
            return cls.sequence(carrier, len(components), len(components),
                                element)
        observed = frozenset() if carrier is None else frozenset({carrier})
        return cls._sequence_from_evidence(
            observed, carrier is None, len(components), len(components),
            components=components)

    @classmethod
    def option(cls, payload):
        """The finite source sum ``None | payload``.

        Nested options add no source value--Python has one ``None`` singleton--
        so construction is idempotent.  Unknown evidence is not a payload
        proof and cannot be hidden behind a tag.
        """
        if not isinstance(payload, cls) or not payload.is_known \
                or payload.is_none:
            raise ValueError("an Option payload needs a known non-None contract")
        return payload if payload.is_option else cls("option", payload=payload)

    @classmethod
    def record(cls, fields):
        """A fixed-schema source record, independent of field order.

        Records are eliminated by the frontend into parallel SolverIR values;
        they are not SolverIR arrays.  Sorting gives control-flow joins one
        canonical schema while source evaluation still follows literal order.
        """
        items = tuple(sorted((str(name), contract)
                             for name, contract in dict(fields).items()))
        if not items or any(not name or not isinstance(contract, cls)
                            for name, contract in items):
            raise ValueError("invalid record contract")
        return cls("record", fields=items)

    @classmethod
    def mapping(cls, minimum, maximum, element=None):
        """A finite integer-key dictionary, bounded by live entries, not keys.

        An empty dictionary has no value evidence yet. Fixed string-key
        records retain their separate product contract.
        """
        if type(minimum) is not int or type(maximum) is not int \
                or not 0 <= minimum <= maximum:
            raise ValueError("invalid mapping cardinality")
        return cls("mapping", carrier="dict", minimum=minimum,
                   maximum=maximum, element=element)

    @classmethod
    def sequence(cls, carrier, minimum, maximum, element=None):
        minimum, maximum = int(minimum), int(maximum)
        if minimum < 0 or maximum < minimum:
            raise ValueError("invalid sequence length interval")
        if carrier not in ("list", "tuple", "range", "bytes", "bytearray", None):
            raise ValueError(f"unknown sequence carrier {carrier!r}")
        observed = frozenset() if carrier is None else frozenset({carrier})
        return cls._sequence_from_evidence(
            observed, carrier is None, minimum, maximum, element)

    @classmethod
    def _sequence_from_evidence(cls, observed, unproved, minimum, maximum,
                                element=None, *, components=None):
        """Build a sequence while retaining all carrier evidence.

        ``carrier`` is the public, proved answer: it exists only when every
        observation names the same concrete Python carrier.  The evidence is
        retained separately so joining an unproved arm with list and then
        tuple cannot forget the list and depend on grouping order.
        """
        observed = frozenset(observed)
        if not observed <= {"list", "tuple", "range", "bytes", "bytearray"}:
            raise ValueError(f"unknown sequence carriers {observed!r}")
        minimum, maximum = int(minimum), int(maximum)
        if minimum < 0 or maximum < minimum:
            raise ValueError("invalid sequence length interval")
        carrier = (next(iter(observed))
                   if len(observed) == 1 and not unproved else None)
        if components is not None and components and all(
                child == components[0] for child in components):
            element, components = components[0], None
        return cls("sequence", carrier, minimum, maximum, element,
                   observed, bool(unproved), components=components)

    def with_interval(self, minimum, maximum, element):
        """Retain the container's carrier evidence with new size facts."""
        if self.is_mapping:
            return ValueContract.mapping(minimum, maximum, element)
        if not self.is_sequence:
            raise ValueError("only a sequence has a length interval")
        return type(self)._sequence_from_evidence(
            self.observed_carriers, self.unproved_carrier,
            minimum, maximum, element,
            components=(self.components if minimum == maximum == self.exact_length
                        else None))

    @classmethod
    def fixed(cls, shape, carrier=None):
        """Build a contract from an already proved recursive array shape."""
        shape = tuple(int(part) for part in shape)
        if not shape:
            return SCALAR
        child = cls.fixed(shape[1:])
        return cls.sequence(carrier, shape[0], shape[0], child)

    @property
    def is_known(self):
        return self.kind in ("none", "scalar", "option", "sequence", "record", "mapping")

    @property
    def is_none(self):
        return self.kind == "none"

    @property
    def is_option(self):
        return self.kind == "option"

    @property
    def is_sequence(self):
        return self.kind == "sequence"

    @property
    def is_record(self):
        return self.kind == "record"

    @property
    def is_mapping(self):
        return self.kind == "mapping"

    @property
    def field_contracts(self):
        return dict(self.fields)

    @property
    def exact_length(self):
        if not self.is_sequence or self.minimum != self.maximum:
            return None
        return self.maximum

    def component(self, index):
        """Contract at a proved fixed position; no invented homogeneous type."""
        return self.element if self.components is None else self.components[index]

    @property
    def physical_shape(self):
        """The fixed storage shape, even when this level has a live length."""
        if self.kind == "scalar":
            return ()
        if not self.is_sequence:
            return None
        if self.maximum == 0 and self.element is None:
            return (0,)
        # Logical lengths live in companion values; the data image therefore
        # uses each descendant's maximum capacity as well.  Using
        # ``fixed_shape`` here made a bounded ragged value appear to have no
        # physical representation even though every dimension was finite.
        child = None if self.element is None else self.element.physical_shape
        return None if child is None else (self.maximum,) + child

    @property
    def fixed_shape(self):
        """The exact value shape, requiring every sequence length to be fixed."""
        if self.kind == "scalar":
            return ()
        if not self.is_sequence or self.exact_length is None:
            return None
        if self.maximum == 0 and self.element is None:
            return (0,)
        child = None if self.element is None else self.element.fixed_shape
        return None if child is None else (self.maximum,) + child

    @property
    def has_dynamic_descendant(self):
        """Whether a nested sequence needs a per-cell logical length."""
        if self.is_option:
            return (self.payload is not None
                    and self.payload.has_dynamic_descendant)
        if self.components is not None:
            return any(child.has_dynamic_descendant or (
                child.is_sequence and child.exact_length is None)
                       for child in self.components)
        if not self.is_sequence or self.element is None:
            return False
        if self.element.is_sequence and self.element.exact_length is None:
            return True
        return self.element.has_dynamic_descendant


UNKNOWN = ValueContract("unknown")


INCOMPATIBLE = ValueContract("incompatible")


NONE = ValueContract("none")


SCALAR = ValueContract("scalar")


def contract_at_record_path(contract, path):
    """Return the contract stored at one fixed record-field path.

    A path is source structure, not a SolverIR storage path: every component
    is one exact string field selected from a proved record schema.  ``None``
    means that the path is not present in that schema.  Keeping lookup here
    gives flow refinement and lowering one recursive contract operation rather
    than separate nested-dict walkers.
    """
    if not isinstance(contract, ValueContract):
        raise TypeError("a record path needs a ValueContract")
    current = contract
    for field in tuple(path):
        if type(field) is not str or not field or not current.is_record:
            return None
        current = current.field_contracts.get(field)
        if current is None:
            return None
    return current


def contract_with_record_path(contract, path, replacement):
    """Replace one fixed record-field contract, preserving its whole schema."""
    path = tuple(path)
    if not isinstance(contract, ValueContract) or not contract.is_record \
            or not path or not isinstance(replacement, ValueContract):
        return None
    field = path[0]
    if type(field) is not str or not field:
        return None
    fields = contract.field_contracts
    child = fields.get(field)
    if child is None:
        return None
    if len(path) == 1:
        fields[field] = replacement
    else:
        nested = contract_with_record_path(child, path[1:], replacement)
        if nested is None:
            return None
        fields[field] = nested
    return ValueContract.record(fields)


def contract_of_value(value):
    """Contract of one already constructed plain Python value."""
    from .uniform_array import UniformArray

    if type(value) is UniformArray:
        element = (contract_of_value(value.base_value(0))
                   if value.length else None)
        for _index, item in value.sparse_items():
            element = _element_join(element, contract_of_value(item))
        return ValueContract.sequence("list", value.length, value.length, element)
    if value is None:
        return NONE
    if type(value) in (bytes, bytearray):
        return ValueContract.sequence(type(value).__name__, len(value), len(value), SCALAR)
    if type(value) in (bool, int, float, str):
        return SCALAR
    if type(value) is tuple:
        return ValueContract.positional("tuple", (contract_of_value(item) for item in value))
    if type(value) not in (list, tuple):
        if type(value) is dict and all(type(key) in (int, bool) for key in value):
            element = None
            for item in value.values():
                element = _element_join(element, contract_of_value(item))
            return ValueContract.mapping(len(value), len(value), element)
        if type(value) is dict and value and all(type(key) is str
                                                 for key in value):
            return ValueContract.record({
                key: contract_of_value(item) for key, item in value.items()
            })
        return UNKNOWN
    element = None
    for item in value:
        element = _element_join(element, contract_of_value(item))
    return ValueContract.sequence(
        "list" if type(value) is list else "tuple",
        len(value), len(value), element)


def representation_value(value):
    """Erase Python sequence carriers at the SolverIR value boundary.

    Contracts retain list-versus-tuple because source operations observe it.
    SolverIR represents both as recursive value arrays, whose concrete carrier
    is a list.  This conversion is therefore used only at that boundary, not
    while source expressions are interpreted.
    """
    from .uniform_array import UniformArray

    if type(value) is UniformArray:
        return value
    if type(value) in (list, tuple, bytes, bytearray):
        return [representation_value(item) for item in value]
    return value


def contract_is_deeply_immutable(contract):
    """Whether every value described by ``contract`` is immutable.

    The admitted scalar domain contains only immutable plain values.  A tuple
    is immutable only when all values reachable through it are immutable;
    lists remain mutable regardless of their elements.  This is the exact
    condition under which copying a SolverIR value preserves Python's
    reference semantics.
    """
    if contract.kind in ("none", "scalar"):
        return True
    if contract.is_option:
        return (contract.payload is not None
                and contract_is_deeply_immutable(contract.payload))
    if not contract.is_sequence or contract.carrier not in {"tuple", "bytes"}:
        return False
    if contract.maximum == 0:
        return True
    if contract.components is not None:
        return all(contract_is_deeply_immutable(child)
                   for child in contract.components)
    return (contract.element is not None
            and contract_is_deeply_immutable(contract.element))


def fixed_positional_contracts(contract):
    """Contracts yielded by one safe fixed positional unpack, if any.

    A call-site ``*value`` exposes the elements of ``value`` as independent
    arguments.  SolverIR can do that exactly when source planning proves a
    fixed list-or-tuple length and copying each element preserves Python's
    alias semantics.  Empty sequences need no element evidence.
    """
    if not isinstance(contract, ValueContract) \
            or not contract.is_sequence \
            or contract.carrier not in {"list", "tuple"}:
        return None
    size = contract.exact_length
    if size is None:
        return None
    if size == 0:
        return ()
    if contract.components is not None:
        return (contract.components if all(contract_is_deeply_immutable(child)
                                          for child in contract.components) else None)
    if (contract.element is None
            or not contract_is_deeply_immutable(contract.element)):
        return None
    return (contract.element,) * size


def sequence_elements_are_deeply_immutable(contract):
    """Whether copying the elements of a sequence preserves alias behavior."""
    if not contract.is_sequence:
        return False
    if contract.maximum == 0:
        return True
    if contract.components is not None:
        return all(contract_is_deeply_immutable(child) for child in contract.components)
    return (contract.element is not None
            and contract_is_deeply_immutable(contract.element))


def contract_of_collection_elements(value):
    """Contract shared by every element of a closed source collection.

    Read-only module tables are not themselves SolverIR values: a mapping, in
    particular, has no array-value representation.  An indexed read still has
    a value contract, however.  Join every possible payload here so source
    planning and value lowering ask the same representation question.  Empty
    collections and unsupported carriers provide no evidence.
    """
    if type(value) is dict:
        items = value.values()
    elif type(value) in (list, tuple):
        items = value
    else:
        return UNKNOWN
    result = None
    for item in items:
        result = _element_join(result, contract_of_value(item))
    return UNKNOWN if result is None else result


def contract_of_collection_lookup(value, key=None, *, key_known=False):
    """Contract of one indexed read from a closed source collection."""
    if key_known:
        try:
            return contract_of_value(value[key])
        except (IndexError, KeyError, TypeError):
            # The operation fails before producing a value.  Its explicit
            # safety guard, rather than a fabricated result type, owns that
            # path.
            return UNKNOWN
    return contract_of_collection_elements(value)


def _element_join(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return join_contracts(left, right)


def join_contracts(left, right):
    """Join two control-flow alternatives without inventing a type."""
    if left == right:
        return left
    if INCOMPATIBLE in (left, right):
        return INCOMPATIBLE
    if UNKNOWN in (left, right):
        # UNKNOWN is an observed value for which this analysis has no
        # representation proof, not an unvisited path.  Joining it with a
        # known, different observation must therefore fail closed.  Returning
        # UNKNOWN here made the algebra order-dependent: joining scalar and
        # sequence first retained their conflict, while joining either with
        # UNKNOWN first erased it.  Absence is represented separately by
        # ``None`` in element joins and by missing map entries.
        return INCOMPATIBLE
    if left.is_none or left.is_option or right.is_none or right.is_option:
        left_payload = (None if left.is_none else
                        left.payload if left.is_option else left)
        right_payload = (None if right.is_none else
                         right.payload if right.is_option else right)
        payload = _element_join(left_payload, right_payload)
        if payload is INCOMPATIBLE:
            return INCOMPATIBLE
        return NONE if payload is None else ValueContract.option(payload)
    if left.kind == right.kind == "scalar":
        return SCALAR
    if left.is_mapping and right.is_mapping:
        element = _element_join(left.element, right.element)
        return (INCOMPATIBLE if element is INCOMPATIBLE else
                ValueContract.mapping(min(left.minimum, right.minimum),
                                      max(left.maximum, right.maximum), element))
    if left.is_record and right.is_record:
        if set(left.field_contracts) != set(right.field_contracts):
            return INCOMPATIBLE
        fields = {}
        for name in left.field_contracts:
            got = join_contracts(
                left.field_contracts[name], right.field_contracts[name])
            if got is INCOMPATIBLE:
                return INCOMPATIBLE
            fields[name] = got
        return ValueContract.record(fields)
    if left.is_sequence and right.is_sequence:
        observed = left.observed_carriers | right.observed_carriers
        if len(observed) > 1:
            return INCOMPATIBLE
        if left.components is not None or right.components is not None:
            if left.exact_length is None or left.exact_length != right.exact_length:
                return INCOMPATIBLE
            components = tuple(join_contracts(left.component(i) or UNKNOWN,
                                              right.component(i) or UNKNOWN)
                               for i in range(left.exact_length))
            if INCOMPATIBLE in components:
                return INCOMPATIBLE
            return ValueContract._sequence_from_evidence(
                observed, left.unproved_carrier or right.unproved_carrier,
                left.minimum, left.maximum, components=components)
        element = _element_join(left.element, right.element)
        if element is INCOMPATIBLE:
            return INCOMPATIBLE
        return ValueContract._sequence_from_evidence(
            observed,
            left.unproved_carrier or right.unproved_carrier,
            min(left.minimum, right.minimum),
            max(left.maximum, right.maximum),
            element)
    return INCOMPATIBLE


def contract_contains(outer, inner):
    """Whether ``outer`` soundly represents every value in ``inner``.

    This is the refinement relation used between a joined plan and one
    concrete source occurrence.  It is deliberately distinct from
    :func:`join_contracts`: widening two alternatives is not evidence that two
    independently produced contracts agree.
    """
    if outer in (UNKNOWN, INCOMPATIBLE) \
            or inner in (UNKNOWN, INCOMPATIBLE):
        return False
    if outer.is_none:
        return inner.is_none
    if outer.is_option:
        if inner.is_none:
            return True
        payload = inner.payload if inner.is_option else inner
        return (outer.payload is not None
                and contract_contains(outer.payload, payload))
    if inner.is_none or inner.is_option:
        return False
    if outer.kind == inner.kind == "scalar":
        return True
    if outer.is_mapping and inner.is_mapping:
        return (outer.minimum <= inner.minimum and outer.maximum >= inner.maximum
                and (inner.maximum == 0 or (outer.element is not None
                     and inner.element is not None
                     and contract_contains(outer.element, inner.element))))
    if outer.is_record and inner.is_record:
        return (set(outer.field_contracts) == set(inner.field_contracts)
                and all(contract_contains(
                    outer.field_contracts[name], inner.field_contracts[name])
                        for name in outer.field_contracts))
    if not (outer.is_sequence and inner.is_sequence):
        return False
    if outer.components is not None or inner.components is not None:
        return (outer.exact_length is not None
                and outer.exact_length == inner.exact_length
                and (outer.carrier is None or outer.carrier == inner.carrier)
                and all(contract_contains(outer.component(i) or UNKNOWN,
                                          inner.component(i) or UNKNOWN)
                        for i in range(outer.exact_length)))
    if (outer.carrier is not None
            and (inner.carrier is None
                 or outer.carrier != inner.carrier)):
        return False
    if outer.minimum > inner.minimum or outer.maximum < inner.maximum:
        return False
    if inner.maximum == 0:
        # Empty values have no live elements. A reused buffer can retain an
        # element storage type, but that is not an additional source value.
        return True
    if outer.element is None:
        return inner.element is None
    if inner.element is None:
        return True
    return contract_contains(outer.element, inner.element)


def contracts_may_overlap(left, right):
    """Whether two contracts can denote at least one common source value.

    This is the relation used to select finite source-summary alternatives
    from an IR range.  It is symmetric: an interval envelope may overlap
    several exact source activations, all of which must then be retained.
    ``UNKNOWN`` cannot prove disjointness and therefore overlaps every
    non-incompatible contract.
    """
    if INCOMPATIBLE in (left, right):
        return False
    if UNKNOWN in (left, right):
        return True
    if left.is_none:
        return right.is_none or right.is_option
    if right.is_none:
        return left.is_option
    if left.is_option and right.is_option:
        # Both contain Python's same ``None`` singleton.
        return True
    if left.is_option:
        return (left.payload is not None
                and contracts_may_overlap(left.payload, right))
    if right.is_option:
        return (right.payload is not None
                and contracts_may_overlap(left, right.payload))
    if left.kind == right.kind == "scalar":
        return True
    if left.is_mapping and right.is_mapping:
        lower, upper = max(left.minimum, right.minimum), min(left.maximum, right.maximum)
        return (lower <= upper and (lower == 0 or
                (left.element is not None and right.element is not None
                 and contracts_may_overlap(left.element, right.element))))
    if left.is_record and right.is_record:
        return (set(left.field_contracts) == set(right.field_contracts)
                and all(contracts_may_overlap(
                    left.field_contracts[name], right.field_contracts[name])
                        for name in left.field_contracts))
    if not (left.is_sequence and right.is_sequence):
        return False
    if left.components is not None or right.components is not None:
        return (left.exact_length is not None
                and left.exact_length == right.exact_length
                and (left.carrier is None or right.carrier is None
                     or left.carrier == right.carrier)
                and all(contracts_may_overlap(left.component(i) or UNKNOWN,
                                             right.component(i) or UNKNOWN)
                        for i in range(left.exact_length)))
    if (left.carrier is not None and right.carrier is not None
            and left.carrier != right.carrier):
        return False
    if left.maximum < right.minimum or right.maximum < left.minimum:
        return False
    if left.element is None or right.element is None:
        return True
    return contracts_may_overlap(left.element, right.element)


def optional_sequence(contract):
    """Whether a fixed sequence has scalar-or-absent cells."""
    return (contract.is_sequence and contract.exact_length is not None
            and contract.exact_length > 0 and contract.element is not None
            and (contract.element.is_none or
                 contract.element.is_option and contract.element.payload == SCALAR))


def narrow_none(contract: ValueContract, *, is_none: bool):
    """Refine one value on the selected arm of an ``is None`` test.

    ``INCOMPATIBLE`` denotes an unreachable arm.  On the present arm an option
    unwraps to its payload, which is the only contract a payload operation may
    consume.
    """
    if contract in (UNKNOWN, INCOMPATIBLE):
        return contract
    if is_none:
        return NONE if contract.is_none or contract.is_option else INCOMPATIBLE
    if contract.is_none:
        return INCOMPATIBLE
    if contract.is_option:
        return contract.payload if contract.payload is not None else INCOMPATIBLE
    return contract
