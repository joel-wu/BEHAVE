"""Logical views of fixed-capacity SolverIR sequence state.

SolverIR stores a bounded sequence in a fixed array and carries each dynamic
logical length in a companion state variable.  A source audit must compare the
logical sequence, not the unused padding.  This module records that boundary
explicitly and reconstructs the source value without inferring compiler names.
"""

from __future__ import annotations

from dataclasses import dataclass

from .native_values import NativeValueError, freeze
from .value_contract import optional_sequence, ValueContract, contract_contains, contract_of_value
from .uniform_array import UniformArray

__all__ = [
    "ABSENT_STATE",
    "DYNAMIC_SEQUENCE_BINDING_VERSION",
    "DynamicSequenceBinding",
    "SourceProductBinding",
    "StateAbsentBinding",
    "StatePresenceBinding",
    "StateProjectionError",
    "StateVariableBinding",
    "preserved_on_reset",
]


DYNAMIC_SEQUENCE_BINDING_VERSION = "dynamic-sequence-binding/1"


class StateProjectionError(ValueError):
    """A logical state value cannot be reconstructed from its IR image."""


def _native_sequence(items, carrier):
    if carrier in {"bytes", "bytearray"}:
        if any(type(item) is not int or not 0 <= item <= 255 for item in items):
            raise StateProjectionError("a source byte lies outside 0..255")
        return bytes(items) if carrier == "bytes" else bytearray(items)
    return tuple(items) if carrier == "tuple" else items


# Attribute absence is neither Python None nor an inactive numeric payload.
# This sentinel is only a reconstructed logical value, never serialized in IR.
ABSENT_STATE = object()


@dataclass(frozen=True, slots=True)
class StateAbsentBinding:
    """A field never created or read in the represented source horizon.

    No payload type is invented for an attribute that does not exist. The
    source-state contract still names it, so native audit checks its absence.
    """

    name: str

    @property
    def identity(self):
        return ("state-absent-binding/1", self.name)

    @property
    def solver_variables(self):
        return ()

    def reconstruct(self, solver_state):
        return ABSENT_STATE


def preserved_on_reset(proc):
    """Late attributes untouched by reset retain existence and value.

    Lowering only installs presence bindings when reset cannot write the
    attribute. Reset mutates the same native instance; it is not construction.
    """
    if proc.contract is None:
        return ()
    return tuple(variable for binding in proc.contract.projections
                 if isinstance(binding, StatePresenceBinding)
                 for variable in binding.solver_variables)


@dataclass(frozen=True, slots=True)
class StatePresenceBinding:
    """Existence of a source attribute, composed with its ordinary value view."""

    value: object
    present_variable: str

    def __post_init__(self):
        if type(self.value) not in (
                StateVariableBinding, SourceProductBinding, DynamicSequenceBinding):
            raise StateProjectionError("attribute presence needs an exact value binding")
        if not self.present_variable or self.present_variable in self.value.solver_variables:
            raise StateProjectionError("attribute presence needs a separate state variable")

    @property
    def name(self):
        return self.value.name

    @property
    def identity(self):
        return ("state-presence-binding/1", self.value.identity, self.present_variable)

    @property
    def solver_variables(self):
        return (self.present_variable, *self.value.solver_variables)

    def reconstruct(self, solver_state):
        try:
            present = solver_state[self.present_variable]
        except KeyError:
            raise StateProjectionError("attribute presence state is absent") from None
        if type(present) is not bool:
            raise StateProjectionError("attribute presence state is not Boolean")
        return self.value.reconstruct(solver_state) if present else ABSENT_STATE


@dataclass(frozen=True, slots=True)
class StateVariableBinding:
    """A source field carried unchanged in one differently named IR variable.

    This changes identity, not representation or integer bounds. Structured
    layouts and dynamic sequence lengths retain their existing bindings.
    """

    name: str
    data_variable: str

    @property
    def identity(self):
        return ("state-variable-binding/1", self.name, self.data_variable)

    @property
    def solver_variables(self):
        return (self.data_variable,)

    def reconstruct(self, solver_state):
        try:
            return solver_state[self.data_variable]
        except KeyError:
            raise StateProjectionError(
                f"SolverIR state {self.data_variable!r} is absent") from None


def _product_paths(contract, path=()):
    if optional_sequence(contract):
        return (path + ('present',), path + ('payload',))
    if contract.is_none:
        return ()
    if contract.is_option:
        return (path + ("present",),) + _product_paths(
            contract.payload, path + ("payload",))
    if contract.is_record:
        return tuple(part for field, child in contract.fields
                     for part in _product_paths(child, path + (field,)))
    if contract.is_mapping:
        if contract.element is not None and contract.element.kind != "scalar":
            raise StateProjectionError("mapping values need scalar leaves")
        return tuple(path + (part,) for part in ("length", "keys", "values"))
    if _record_sequence(contract):
        if any(child.fixed_shape is None for _, child in contract.element.fields):
            raise StateProjectionError("record-sequence fields need fixed leaf layouts")
        return (path + ("length",),) + tuple(
            path + ("elements", field) for field, _ in contract.element.fields)
    if contract.fixed_shape is None:
        raise StateProjectionError("a product state leaf needs a fixed representation")
    return (path,)


def _record_sequence(contract):
    return (contract.is_sequence and contract.carrier == "list"
            and contract.element is not None and contract.element.is_record)


def _product_zero(contract):
    if contract.is_none or contract.is_option:
        return None
    if contract.is_record:
        return {field: _product_zero(child) for field, child in contract.fields}
    if contract.is_sequence:
        values = [_product_zero(contract.element)
                  for _ in range(contract.exact_length)]
        return _native_sequence(values, contract.carrier)
    return 0


@dataclass(frozen=True, slots=True)
class SourceProductBinding:
    """A source product and its existing tag/payload/count/column leaves.

    Paths name semantic product components, not guessed compiler variable
    names. An absent option never exposes its inactive payload to the audit.
    """

    name: str
    contract: ValueContract
    leaves: tuple[tuple[tuple[str, ...], str], ...]

    def __post_init__(self):
        paths = tuple(path for path, _variable in self.leaves)
        variables = tuple(variable for _path, variable in self.leaves)
        if paths != _product_paths(self.contract) or len(set(variables)) != len(variables):
            raise StateProjectionError("product state leaves do not match its source contract")

    @property
    def identity(self):
        return ("source-product-state/1", self.name, self.contract, self.leaves)

    @property
    def solver_variables(self):
        return tuple(variable for _path, variable in self.leaves)

    def solver_values(self, value):
        if not contract_contains(self.contract, contract_of_value(value)):
            raise StateProjectionError("initial product state disagrees with its contract")
        variables = dict(self.leaves)
        values = {}

        def bind(contract, value, path):
            if contract.is_none:
                return
            if optional_sequence(contract):
                values[variables[path + ('present',)]] = [item is not None for item in value]
                values[variables[path + ('payload',)]] = [0 if item is None else item for item in value]
            elif contract.is_option:
                values[variables[path + ("present",)]] = value is not None
                bind(contract.payload,
                     _product_zero(contract.payload) if value is None else value,
                     path + ("payload",))
            elif contract.is_record:
                for field, child in contract.fields:
                    bind(child, value[field], path + (field,))
            elif contract.is_mapping:
                if any(type(key) is not int or type(item) is not int
                       for key, item in value.items()):
                    raise StateProjectionError("mapping keys and values must be integers")
                padding = [0] * (max(1, contract.maximum) - len(value))
                values[variables[path + ("length",)]] = len(value)
                values[variables[path + ("keys",)]] = list(value) + padding
                values[variables[path + ("values",)]] = list(value.values()) + padding
            elif _record_sequence(contract):
                values[variables[path + ("length",)]] = len(value)
                for field, child in contract.element.fields:
                    column = [physical(record[field]) for record in value]
                    column.extend(physical(_product_zero(child)) for _ in range(
                        max(contract.maximum, 1) - len(value)))
                    values[variables[path + ("elements", field)]] = column
            else:
                values[variables[path]] = physical(value)

        def physical(item):
            return ([physical(child) for child in item]
                    if type(item) in (tuple, list, bytes, bytearray) else item)

        bind(self.contract, value, ())
        return values

    def reconstruct(self, solver_state):
        variables = dict(self.leaves)

        def native(value, contract):
            if not contract.is_sequence:
                return value
            if type(value) not in (list, tuple, UniformArray) or len(value) != contract.exact_length:
                raise StateProjectionError("product state sequence has the wrong shape")
            items = [native(item, contract.element) for item in value]
            return _native_sequence(items, contract.carrier)

        def read(path):
            try:
                return solver_state[variables[path]]
            except KeyError:
                raise StateProjectionError("product state has an absent physical leaf") from None

        def build(contract, path):
            if optional_sequence(contract):
                tags, payloads = read(path + ('present',)), read(path + ('payload',))
                if any(type(column) not in (list, tuple, UniformArray)
                       or len(column) != contract.exact_length for column in (tags, payloads)):
                    raise StateProjectionError('optional sequence column has the wrong shape')
                if any(type(tag) is not bool for tag in tags):
                    raise StateProjectionError('optional sequence tags must be Boolean')
                return _native_sequence([payload if tag else None
                                         for tag, payload in zip(tags, payloads)], contract.carrier)
            if contract.is_none:
                return None
            if contract.is_option:
                present = read(path + ("present",))
                if type(present) is not bool:
                    raise StateProjectionError("an option state presence tag is not Boolean")
                return build(contract.payload, path + ("payload",)) if present else None
            if contract.is_record:
                return {field: build(child, path + (field,))
                        for field, child in contract.fields}
            if contract.is_mapping:
                count, keys, values = (read(path + (part,))
                                       for part in ("length", "keys", "values"))
                if type(count) is not int or not contract.minimum <= count <= contract.maximum:
                    raise StateProjectionError("mapping length is outside its contract")
                if any(type(column) not in (list, tuple, UniformArray)
                       or len(column) != max(1, contract.maximum) for column in (keys, values)):
                    raise StateProjectionError("mapping column has the wrong shape")
                pairs = [(keys[i], values[i]) for i in range(count)]
                if any(type(key) is not int or type(value) is not int for key, value in pairs) \
                        or len({key for key, _ in pairs}) != count:
                    raise StateProjectionError("mapping has invalid or duplicate live keys")
                return dict(pairs)
            if _record_sequence(contract):
                count = read(path + ("length",))
                if type(count) is not int or not contract.minimum <= count <= contract.maximum:
                    raise StateProjectionError("record-sequence length is outside its contract")
                columns = {field: read(path + ("elements", field))
                           for field, _ in contract.element.fields}
                if any(type(column) not in (list, tuple, UniformArray)
                       or len(column) != max(contract.maximum, 1)
                       for column in columns.values()):
                    raise StateProjectionError("record-sequence column has the wrong shape")
                return [{field: native(columns[field][index], child)
                         for field, child in contract.element.fields}
                        for index in range(count)]
            return native(read(path), contract)

        return build(self.contract, ())

    def equivalent(self, left, right):
        """Compare through the product layout, retaining exact leaf carriers.

        Record fields are named components, not dictionary iteration positions.
        Order-sensitive source operations still require a proved insertion
        order in lowering; this projection must not invent one. Options and
        sequence leaves retain presence, type, length and element order.
        """
        def canonical(contract, image):
            if contract.is_option:
                return (image if image == ("none",) else
                        canonical(contract.payload, image))
            if contract.is_record:
                if image[0] != "dict":
                    raise StateProjectionError("a product record is not a mapping")
                fields = dict(image[1])
                if set(fields) != {("str", name) for name, _ in contract.fields}:
                    raise StateProjectionError("a product record has different fields")
                return ("dict", tuple(
                    (("str", name), canonical(child, fields["str", name]))
                    for name, child in contract.fields))
            if _record_sequence(contract):
                if image[0] != "list":
                    raise StateProjectionError("a record sequence is not a list")
                return ("list", tuple(canonical(contract.element, item)
                                      for item in image[1]))
            return image

        try:
            # Freeze whole values first, retaining the native domain's cycle
            # and shared-mutable-object checks across sibling record fields.
            return (canonical(self.contract, freeze(left))
                    == canonical(self.contract, freeze(right)))
        except (NativeValueError, StateProjectionError):
            return False


@dataclass(frozen=True, slots=True)
class DynamicSequenceBinding:
    """One source sequence and its fixed data and logical-length state.

    ``length_variables`` maps a sequence nesting depth to the SolverIR state
    variable holding that level's logical length.  At depth zero it is a
    scalar; at deeper levels it is indexed by the enclosing element indices.
    Fixed-length levels need no entry.
    """

    name: str
    data_variable: str
    contract: ValueContract
    length_variables: tuple[tuple[int, str], ...]

    def __post_init__(self):
        if type(self.name) is not str or not self.name:
            raise StateProjectionError(
                "a dynamic source-state binding needs a nonempty name")
        if type(self.data_variable) is not str or not self.data_variable:
            raise StateProjectionError(
                "a dynamic source-state binding needs a data variable")
        if type(self.contract) is not ValueContract:
            raise StateProjectionError(
                "a dynamic source-state binding needs one ValueContract")

        expected = []
        current = self.contract
        depth = 0
        while current.is_sequence:
            if current.carrier == "range":
                raise StateProjectionError(
                    "a mutable sequence state binding cannot use range")
            if current.exact_length is None:
                expected.append(depth)
            if current.maximum == 0 and current.element is None:
                break
            if current.element is None:
                raise StateProjectionError(
                    "a nonempty sequence state has no element contract")
            current = current.element
            depth += 1
        if ((not self.contract.is_sequence or current.kind != "scalar")
                and not (current.is_sequence and current.maximum == 0
                         and current.element is None)):
            raise StateProjectionError(
                "dynamic state projection supports recursive sequences "
                "of scalar values")

        rows = tuple(self.length_variables)
        if any(type(row) not in (tuple, list) or len(row) != 2
               for row in rows):
            raise StateProjectionError(
                "a length binding is one depth/variable pair")
        normalized = []
        for depth, variable in rows:
            if type(depth) is not int or type(depth) is bool or depth < 0:
                raise StateProjectionError(
                    "a dynamic sequence depth is a natural number")
            if type(variable) is not str or not variable:
                raise StateProjectionError(
                    "a dynamic sequence length needs a state variable")
            normalized.append((depth, variable))
        normalized.sort()
        if len({depth for depth, _variable in normalized}) != len(normalized):
            raise StateProjectionError(
                "a sequence depth has two logical-length variables")
        if len({variable for _depth, variable in normalized}) != len(normalized):
            raise StateProjectionError(
                "one logical-length variable is bound at two depths")
        if [depth for depth, _variable in normalized] != expected:
            raise StateProjectionError(
                "logical-length variables do not cover every dynamic depth")
        if self.data_variable in {variable for _depth, variable in normalized}:
            raise StateProjectionError(
                "the data and logical length need distinct state variables")
        object.__setattr__(self, "length_variables", tuple(normalized))

    @property
    def identity(self):
        return (
            DYNAMIC_SEQUENCE_BINDING_VERSION,
            self.name,
            self.data_variable,
            self.contract,
            self.length_variables,
        )

    @property
    def solver_variables(self):
        return (self.data_variable,) + tuple(
            variable for _depth, variable in self.length_variables)

    @staticmethod
    def _read(values, variable, indices=()):
        try:
            value = values[variable]
        except (KeyError, TypeError):
            raise StateProjectionError(
                f"SolverIR state {variable!r} is absent") from None
        for index in indices:
            if type(value) not in (list, tuple, UniformArray) or not 0 <= index < len(value):
                raise StateProjectionError(
                    f"SolverIR state {variable!r} has no index {indices!r}")
            value = value[index]
        return value

    def reconstruct(self, solver_state):
        """Rebuild the logical sequence, discarding only inactive padding."""
        lengths = dict(self.length_variables)

        def build(data, contract, depth, indices):
            if contract.kind == "scalar":
                return data
            if not contract.is_sequence:
                raise StateProjectionError(
                    "a dynamic state binding reached a non-sequence node")
            if type(data) not in (list, tuple, UniformArray) \
                    or len(data) != contract.maximum:
                raise StateProjectionError(
                    "the fixed sequence backing has the wrong shape")
            if contract.exact_length is None:
                length = self._read(
                    solver_state, lengths[depth], indices)
                if type(length) is not int or type(length) is bool \
                        or not contract.minimum <= length <= contract.maximum:
                    raise StateProjectionError(
                        "a logical sequence length lies outside its contract")
            else:
                length = contract.exact_length
            if length and contract.element is None:
                raise StateProjectionError(
                    "a nonempty sequence has no element contract")
            items = [
                build(data[index], contract.element, depth + 1,
                      indices + (index,))
                for index in range(length)
            ]
            return _native_sequence(items, contract.carrier)

        data = self._read(solver_state, self.data_variable)
        return build(data, self.contract, 0, ())
