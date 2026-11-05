"""Immutable SolverIR type contexts, finite domains and concrete value checks.

This is a data boundary, independent of either frontend and of symbolic proof
construction. Building it from a procedure and proving output bounds belong to
the compiler, not to readers of an already bound semantic contract.
"""

from __future__ import annotations

from ..core.contract import FiniteType
from ..core.schema import TransactionSchema
from ..core.frozen import Frozen
from ..core.value_type import ValueType, as_value_type
from .value_binding import ValueBinding, ValueBindingError

__all__ = [
    "ANALYSIS_ERROR_CODES", "OutputContractError", "ProcedureContract",
    "ProcedureContractError", "ProcedureSignature",
]

# These terminate the represented analysis rather than report an artifact
# exception.  They still belong to the procedure's complete error alphabet,
# but are kept in a distinct context so a consumer cannot score one as a model
# failure merely because both travel through the same IR ``Fail`` command.
ANALYSIS_ERROR_CODES = frozenset({"CapacityExceeded", "Unwound"})

class ProcedureContractError(ValueError):
    """A procedure cannot be given one complete immutable semantic contract."""

    __slots__ = ("code",)

    def __init__(self, message, code="procedure-contract-inconsistent"):
        super().__init__(message)
        self.code = code

class ProcedureContract(Frozen):
    """The four typed contexts, finite boundary domains and error alphabet.

    The source-state :class:`ir.StateContract` answers a different question:
    which Python fields are carried, folded or constant.  This record owns the
    semantic interface of the SolverIR procedure itself.  Keeping the records
    separate prevents either one from silently supplying facts the other does
    not state.

    Current task schemas carry :class:`FiniteType`, whose admitted host values
    are exact integers.  ``input_domains`` and ``output_domains`` deliberately
    retain those objects instead of translating width/signedness into an
    ad-hoc Behavior type.  This is the binding seam for the future shared task
    ``ValueType``/flattening contract: it can replace the domain value without
    changing how the four semantic contexts are owned.
    """

    __slots__ = (
        "_input_bindings",
        "_output_bindings",
        "analysis_errors",
        "artifact_errors",
        "input_domains",
        "input_types",
        "local_types",
        "output_domains",
        "output_types",
        "state_types",
        "value_bindings",
    )

    def __init__(self, state_types, input_types, local_types, output_types,
                 input_domains=(), output_domains=(), artifact_errors=(),
                 analysis_errors=(), value_bindings=()):
        from . import types

        self.state_types = self._context(state_types, "state", types)
        self.input_types = self._context(input_types, "input", types)
        self.local_types = self._context(local_types, "local", types)
        self.output_types = self._context(output_types, "output", types)
        names = [name for context in (
            self.state_types, self.input_types, self.local_types)
                 for name, _kind in context]
        if len(names) != len(set(names)):
            raise ProcedureContractError(
                "a variable appears in more than one typed procedure context")
        self.value_bindings = self._bindings(value_bindings)
        self.input_domains = self._domains(
            input_domains, self.input_types, "input", self.value_bindings)
        self.output_domains = self._domains(
            output_domains, self.output_types, "output", self.value_bindings)
        for role, domains in (("input", self.input_domains),
                              ("output", self.output_domains)):
            declared = dict(domains)
            for bound_role, binding in self.value_bindings:
                if declared and bound_role == role \
                        and declared.get(binding.name) \
                        != binding.value_type:
                    raise ProcedureContractError(
                        f"the {role} binding for {binding.name!r} disagrees "
                        "with its logical ValueType domain",
                        code="value-binding-type-mismatch")
        self.artifact_errors = self._errors(
            artifact_errors, "artifact error")
        self.analysis_errors = self._errors(
            analysis_errors, "analysis error")
        overlap = set(self.artifact_errors) & set(self.analysis_errors)
        if overlap:
            raise ProcedureContractError(
                f"error codes have two meanings: {sorted(overlap)}")
        contexts = {
            role: dict(getattr(self, f"{role}_types"))
            for role in ("state", "input", "local", "output")
        }
        used_solver_paths = set()
        for role, binding in self.value_bindings:
            declared = contexts[role]
            for variable, kind in binding.solver_types():
                if variable not in declared:
                    raise ProcedureContractError(
                        f"the {role} value {binding.name!r} binds undeclared "
                        f"SolverIR variable {variable!r}",
                        code="value-binding-variable-undeclared")
                if declared[variable] != kind:
                    raise ProcedureContractError(
                        f"the {role} value {binding.name!r} binds {variable!r} "
                        f"as {kind}, but its typed context declares "
                        f"{declared[variable]}",
                        code="value-binding-type-mismatch")
            for _logical, solver in binding.leaf_bindings:
                key = (role, solver)
                if key in used_solver_paths:
                    raise ProcedureContractError(
                        f"the {role} SolverIR leaf {solver.identity!r} is bound "
                        "by two logical values",
                        code="value-binding-solver-path-duplicate")
                used_solver_paths.add(key)
        self._input_bindings = tuple(binding for role, binding in self.value_bindings
                                     if role == "input")
        self._output_bindings = tuple(binding for role, binding in self.value_bindings
                                      if role == "output")
        self._freeze()

    @staticmethod
    def _context(values, role, types):
        out = []
        seen = set()
        for name, kind in values:
            if type(name) is not str or not name or name in seen:
                raise ProcedureContractError(
                    f"the {role} type context needs unique nonempty names")
            if not types.is_type(kind):
                raise ProcedureContractError(
                    f"the {role} {name!r} has no SolverIR semantic type")
            seen.add(name)
            out.append((name, kind))
        return tuple(out)

    @staticmethod
    def _domains(values, context, role, bindings):
        values = tuple(values)
        normalized = []
        seen = set()
        for name, domain in values:
            if type(name) is not str or not name or name in seen:
                raise ProcedureContractError(
                    f"the {role} domain needs unique nonempty names")
            try:
                domain = as_value_type(domain)
            except TypeError:
                raise ProcedureContractError(
                    f"the {role} domain {name!r} is not an exact ValueType") \
                    from None
            seen.add(name)
            normalized.append((name, domain))
        role_bindings = tuple(binding for bound_role, binding in bindings
                              if bound_role == role)
        physical = {
            variable for binding in role_bindings
            for variable in binding.solver_variables
        }
        expected = ({binding.name for binding in role_bindings}
                    | {name for name, _kind in context
                       if name not in physical})
        if values and seen != expected:
            raise ProcedureContractError(
                f"the {role} finite domain does not cover its logical values")
        return tuple(normalized)

    @staticmethod
    def _errors(values, what):
        out = tuple(sorted(values))
        if any(type(code) is not str or not code for code in out):
            raise ProcedureContractError(
                f"a {what} code is not a nonempty string")
        if len(out) != len(set(out)):
            raise ProcedureContractError(f"duplicate {what} code")
        return out

    @staticmethod
    def _bindings(values):
        out = []
        seen = set()
        for item in values:
            if type(item) not in (tuple, list) or len(item) != 2:
                raise ProcedureContractError(
                    "a procedure value binding is one role/ValueBinding pair")
            role, binding = item
            if role not in ("state", "input", "local", "output"):
                raise ProcedureContractError(
                    f"unknown procedure value-binding role {role!r}")
            if type(binding) is not ValueBinding:
                raise ProcedureContractError(
                    "a procedure value binding uses one exact ValueBinding")
            key = (role, binding.name)
            if key in seen:
                raise ProcedureContractError(
                    f"the {role} logical value {binding.name!r} is bound twice")
            seen.add(key)
            out.append((role, binding))
        return tuple(sorted(out, key=lambda item: (item[0], item[1].name)))

    @property
    def error_codes(self):
        """Stable symbolic alphabet, with normal completion at index zero."""
        return ("",) + self.artifact_errors + self.analysis_errors

    def context(self, role):
        try:
            return dict(getattr(self, f"{role}_types"))
        except AttributeError:
            raise ValueError(f"unknown procedure type context {role!r}") from None

    def value_layout(self, role, name):
        """The shared flatten/reconstruct layout of one declared role value.

        Finite task domains refine Integer leaves at the input/output boundary;
        state and local Integers retain their mathematical carrier.  All roles
        pass through the same recursive layout constructor, which is also the
        extension seam for source records and enumerations once lowering binds
        their leaves to SolverIR names.
        """
        from .value_layout import layout_of_type, layout_of_value_type

        binding = self.value_binding(role, name, required=False)
        if binding is not None:
            return binding.layout

        context = self.context(role)
        try:
            kind = context[name]
        except KeyError:
            raise ValueError(
                f"{name!r} is absent from the {role} type context") from None
        domains = {
            "input": dict(self.input_domains),
            "output": dict(self.output_domains),
        }.get(role, {})
        domain = domains.get(name)
        if type(domain) is ValueType:
            return layout_of_value_type(domain)
        return layout_of_type(kind, carrier=domain)

    def value_binding(self, role, name, *, required=True):
        """The declared logical-to-SolverIR binding for one role value."""
        for bound_role, binding in self.value_bindings:
            if bound_role == role and binding.name == name:
                return binding
        if required:
            raise ValueError(
                f"{name!r} has no declared {role} ValueBinding")
        return None

    def logical_value(self, role, name, solver_values):
        """Reconstruct one declared logical value from SolverIR storage."""
        binding = self.value_binding(role, name, required=False)
        if binding is None:
            # The old scalar/ordinary-array path is already an explicit typed
            # context.  It is the compatibility case, not a record schema
            # inferred from the runtime object.
            return solver_values[name]
        return binding.reconstruct(solver_values)

    def bind_logical_inputs(self, values):
        """Flatten declared logical inputs to the procedure's SolverIR context."""
        input_bindings = self._input_bindings
        if not input_bindings:
            return dict(values)
        expected = {binding.name for binding in input_bindings}
        if set(values) != expected:
            missing = sorted(expected - set(values))
            extra = sorted(set(values) - expected)
            raise ValueError(
                f"the logical inputs do not match their ValueBindings "
                f"(missing={missing}, extra={extra})")
        out = {}
        for binding in input_bindings:
            try:
                materialized = binding.solver_values(values[binding.name])
            except (ValueBindingError, ValueError) as exc:
                raise ValueError(f"input {binding.name!r}: {exc}") from None
            overlap = set(out) & set(materialized)
            if overlap:
                raise ValueError(
                    f"logical inputs share SolverIR variables: {sorted(overlap)}")
            out.update(materialized)
        return out

    def solver_input_bounds(self):
        """Finite range of each physical input variable, from logical types.

        This projection is intentionally declaration-only.  Array leaves that
        share one SolverIR variable contribute the hull of their declared
        carriers; no concrete input value is inspected to choose a width.
        """
        from . import types

        out = {}
        bound_logical = set()
        for binding in self._input_bindings:
            bound_logical.add(binding.name)
            leaves = {leaf.path: leaf for leaf in binding.layout.leaves()}
            for logical, solver in binding.leaf_bindings:
                leaf = leaves[logical]
                pair = ((0, 1) if leaf.semantic_type == types.BOOLEAN else
                        leaf.carrier.bounds)
                known = out.get(solver.variable)
                out[solver.variable] = (
                    pair if known is None else
                    (min(known[0], pair[0]), max(known[1], pair[1])))
        for name, value_type in self.input_domains:
            if name in bound_logical:
                continue
            if value_type.kind != "integer":
                raise ProcedureContractError(
                    f"unbound logical input {name!r} has no scalar SolverIR "
                    "range projection",
                    code="value-binding-variable-undeclared")
            out[name] = value_type.bounds
        return tuple((name, out[name]) for name, _kind in self.input_types
                     if name in out)

    def validate_inputs(self, values):
        """Validate concrete inputs against this procedure's finite domain."""
        bindings = self._input_bindings
        expected = ({binding.name for binding in bindings} if bindings else
                    {name for name, _kind in self.input_types})
        if set(values) != expected:
            missing = sorted(expected - set(values))
            extra = sorted(set(values) - expected)
            raise ValueError(
                f"the inputs do not match the finite procedure domain "
                f"(missing={missing}, extra={extra})")
        domains = dict(self.input_domains)
        for name in sorted(expected):
            value = values[name]
            try:
                self.value_layout("input", name).flatten(value)
            except ValueError as exc:
                raise ValueError(f"input {name!r}: {exc}") from None
            domain = domains.get(name)
            if domain is not None and not domain.admits(value):
                raise ValueError(
                    f"input {name!r} is outside its declared {domain!r}")
        return self.bind_logical_inputs(values)

    def validate_output(self, count, buffers, capacity=None):
        """Validate one concrete active output prefix against this contract."""
        if self.output_domains:
            _validate_output_values(
                self.output_domains, count, buffers, capacity=capacity)

    def reconstruct_outputs(self, count, physical_buffers, capacity=None):
        """Reconstruct logical transaction buffers from physical leaf buffers.

        The executable procedure stores a batch around every public SolverIR output
        value.  ``ValueBinding`` describes the value *inside* one batch cell; this
        method removes the batch dimension and applies that same binding once per
        active transaction.  It is the concrete inverse used by replay and wire
        serialization, not a schema inferred from the returned Python objects.
        """
        if type(count) is not int or count < 0:
            raise ValueError("the output count is not a natural number")
        physical_buffers = dict(physical_buffers)
        bindings = self._output_bindings
        if not bindings:
            self.validate_output(
                count, physical_buffers, capacity=capacity)
            logical = {}
            for name, buffer in physical_buffers.items():
                length = _fixed_sequence_length(buffer)
                if length is None:
                    logical[name] = (() if count == 0 else (buffer,))
                else:
                    logical[name] = tuple(
                        buffer[index] for index in range(count))
            return logical
        expected = {
            variable for binding in bindings
            for variable in binding.solver_variables
        }
        if set(physical_buffers) != expected:
            missing = sorted(expected - set(physical_buffers))
            extra = sorted(set(physical_buffers) - expected)
            raise ValueError(
                "the physical output buffers do not match their ValueBindings "
                f"(missing={missing}, extra={extra})")

        def cell(variable, index):
            buffer = physical_buffers[variable]
            length = _fixed_sequence_length(buffer)
            if length is None or index >= length:
                raise ValueError(
                    f"physical output {variable!r} has no transaction cell "
                    f"{index}")
            return buffer[index]

        if capacity is None:
            lengths = {
                _fixed_sequence_length(value)
                for value in physical_buffers.values()
            }
            if None in lengths or len(lengths) != 1:
                raise ValueError(
                    "the physical output buffers have no common capacity")
            storage = next(iter(lengths))
            capacity = storage
        else:
            storage = max(capacity, 1)
        if count > capacity:
            raise ValueError(
                f"the output count {count} exceeds its capacity {capacity}")
        for variable, buffer in physical_buffers.items():
            length = _fixed_sequence_length(buffer)
            if length != storage:
                raise ValueError(
                    f"physical output {variable!r} has {length!r} storage "
                    f"cells for logical capacity {capacity}")
        rows = {binding.name: [] for binding in bindings}
        for index in range(count):
            for binding in bindings:
                physical = {
                    variable: cell(variable, index)
                    for variable in binding.solver_variables
                }
                rows[binding.name].append(binding.reconstruct(physical))
        logical = {name: tuple(values) for name, values in rows.items()}
        _validate_active_output_values(self.output_domains, count, logical)
        return logical

    def __eq__(self, other):
        return type(other) is ProcedureContract and self.identity == other.identity

    def __hash__(self):
        return hash(self.identity)

    @property
    def identity(self):
        return (
            self.state_types,
            self.input_types,
            self.local_types,
            self.output_types,
            self.input_domains,
            self.output_domains,
            self.artifact_errors,
            self.analysis_errors,
            self.value_bindings,
        )

    def __reduce__(self):
        return type(self), self.identity

def _fixed_sequence_length(value):
    """Length of one concrete fixed-array carrier, if it is one."""
    from .uniform_array import UniformArray

    return (len(value) if type(value) in (list, tuple, UniformArray) else None)

def _validate_active_output_values(domains, count, buffers):
    """Validate exactly the logical transaction prefix a step emitted."""
    domains = tuple(domains)
    names = tuple(name for name, _finite in domains)
    if set(buffers) != set(names):
        missing = sorted(set(names) - set(buffers))
        extra = sorted(set(buffers) - set(names))
        raise ValueError(
            f"the output buffers do not match the finite procedure domain "
            f"(missing={missing}, extra={extra})")
    for name, finite in domains:
        cells = buffers[name]
        length = _fixed_sequence_length(cells)
        if length is None or length < count:
            raise ValueError(
                f"output {name!r} has {length!r} cells for {count} "
                "active transactions")
        for index in range(count):
            value = cells[index]
            if not finite.admits(value):
                raise ValueError(
                    f"output {name}[{index}]={value!r} is outside {finite}")

def _validate_output_values(domains, count, buffers, capacity=None):
    """The one concrete finite-output rule shared by source and procedure data."""
    domains = tuple(domains)
    names = tuple(name for name, _finite in domains)
    if type(count) is not int or count < 0:
        raise ValueError(f"the output count is {count!r}, not a natural number")
    if set(buffers) != set(names):
        missing = sorted(set(names) - set(buffers))
        extra = sorted(set(buffers) - set(names))
        raise ValueError(
            f"the output buffers do not match the finite procedure domain "
            f"(missing={missing}, extra={extra})")
    if capacity is not None \
            and (type(capacity) is not int or capacity < 0):
        raise ValueError("the output capacity is not a natural number")
    if capacity is None:
        physical = {
            _fixed_sequence_length(buffers[name])
            if _fixed_sequence_length(buffers[name]) is not None else 1
            for name in names
        }
        if len(physical) != 1:
            raise ValueError(
                "the output buffers do not declare one common capacity")
        capacity = next(iter(physical))
    if count > capacity:
        raise ValueError(
            f"the output count {count} exceeds its capacity {capacity}")
    for name, finite in domains:
        cells = buffers[name]
        length = _fixed_sequence_length(cells)
        physical = length if length is not None else 1
        if physical != max(capacity, 1):
            raise ValueError(
                f"output {name!r} has {physical} storage cells for "
                f"logical capacity {capacity}")
    _validate_active_output_values(domains, count, buffers)


class OutputContractError(Exception):
    """A lowered output boundary the task signature cannot justify."""

    __slots__ = ("code",)

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _project_source_output_value(value_type, value, *, path):
    """Canonical logical value of one declared source-output subtree."""
    from .value_layout import ValueLayoutError

    if value_type.kind == "boolean":
        if type(value) is not bool:
            raise ValueLayoutError(
                "value-layout-boolean-mismatch",
                f"source output {path} is not an exact Boolean")
        return value
    if value_type.kind == "integer":
        if type(value) is not int:
            raise ValueLayoutError(
                "value-layout-integer-mismatch",
                f"source output {path} is not an exact integer")
        carrier = value_type.carrier
        if carrier.admits(value):
            return value
        if carrier.signed and 0 <= value < (1 << carrier.width):
            sign = 1 << (carrier.width - 1)
            return value - (1 << carrier.width) if value & sign else value
        low, high = carrier.bounds
        raise ValueLayoutError(
            "value-layout-integer-outside-source-image",
            f"source output {path}={value!r} is outside both {low}..{high} "
            f"and the {carrier.width}-bit physical pattern domain")
    if value_type.kind == "enumeration":
        if type(value) is not str or value not in value_type.members:
            raise ValueLayoutError(
                "value-layout-enum-member-invalid",
                f"source output {path} is not a declared enumeration member")
        return value
    if value_type.kind == "array":
        if type(value) not in (tuple, list) or len(value) != value_type.cells:
            raise ValueLayoutError(
                "value-layout-array-shape-mismatch",
                f"source output {path} needs exactly {value_type.cells} cells")
        return [
            _project_source_output_value(
                value_type.element, item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if type(value) is not dict or set(value) != {
            name for name, _child in value_type.fields}:
        raise ValueLayoutError(
            "value-layout-record-schema-mismatch",
            f"source output {path} does not have its declared record fields")
    return {
        name: _project_source_output_value(
            child, value[name], path=f"{path}.{name}")
        for name, child in value_type.fields
    }


class ProcedureSignature(Frozen):
    """Named logical value types and their explicit SolverIR leaf bindings.

    A legacy ``FiniteType`` row is accepted as the exact shorthand for
    ``ValueType.integer`` bound directly to the same-named scalar.  Structured
    rows use a ``ValueBinding``; a record passed without one is refused rather
    than assigned compiler-generated leaf names.
    """

    __slots__ = (
        "_input_bindings", "_inputs", "_output_bindings", "_outputs")

    def __init__(self, inputs, outputs):
        self._inputs, self._input_bindings = self._fields(inputs, "input")
        self._outputs, self._output_bindings = self._fields(outputs, "output")
        self._freeze()

    @staticmethod
    def _fields(fields, direction):
        out, bindings = [], []
        seen = set()
        for item in fields:
            if type(item) not in (tuple, list) or len(item) != 2:
                raise TypeError(
                    f"a procedure {direction} row is a name/type or "
                    "name/ValueBinding pair")
            name, declaration = item
            if not isinstance(name, str) or not name or name in seen:
                raise ValueError(
                    f"a procedure {direction} needs a unique nonempty name")
            if type(declaration) is ValueBinding:
                binding = declaration
                if binding.name != name:
                    raise ValueError(
                        f"procedure field {name!r} is paired with binding "
                        f"{binding.name!r}")
                value_type = binding.value_type
            else:
                value_type = as_value_type(declaration)
                try:
                    binding = ValueBinding.direct(name, value_type)
                except ValueBindingError as exc:
                    raise TypeError(
                        f"structured procedure field {name!r} needs explicit "
                        f"SolverIR leaf paths: {exc}") from None
            seen.add(name)
            out.append((name, value_type))
            bindings.append(binding)
        return tuple(out), tuple(bindings)

    @classmethod
    def from_schema(cls, schema, input_names=None, output_names=None):
        if not isinstance(schema, TransactionSchema):
            raise TypeError("a procedure signature needs a TransactionSchema")

        def project(endpoints, selected):
            if selected is not None:
                selected = tuple(selected)
                missing = tuple(name for name in selected
                                if name not in endpoints)
                if missing:
                    raise ValueError(
                        f"procedure fields are absent from the task schema: "
                        f"{missing}")
                endpoints = {name: endpoints[name] for name in selected}
            return tuple(
                (name, FiniteType(endpoint.width, endpoint.signed))
                for name, endpoint in endpoints.items())

        return cls(project(schema.inputs(), input_names),
                   project(schema.outputs(), output_names))

    @property
    def input_names(self):
        return tuple(name for name, _finite in self._inputs)

    @property
    def output_names(self):
        return tuple(name for name, _finite in self._outputs)

    @property
    def inputs(self):
        return self._inputs

    @property
    def outputs(self):
        return self._outputs

    @property
    def input_bindings(self):
        return self._input_bindings

    @property
    def output_bindings(self):
        return self._output_bindings

    @property
    def value_bindings(self):
        return (tuple(("input", binding)
                      for binding in self.input_bindings)
                + tuple(("output", binding)
                        for binding in self.output_bindings))

    def input_bounds(self, names=None):
        fields = dict(self._inputs)
        wanted = self.input_names if names is None else tuple(names)
        missing = [name for name in wanted if name not in fields]
        if missing:
            raise ValueError(
                f"step inputs are absent from the task signature: {missing}")
        out = {}
        for name in wanted:
            value_type = fields[name]
            if value_type.kind != "integer":
                raise ValueError(
                    f"step input {name!r} is structured or non-Integer and "
                    "has no single scalar bounds pair")
            out[name] = value_type.bounds
        return out

    def output_type(self, name):
        try:
            return dict(self._outputs)[name]
        except KeyError:
            raise ValueError(
                f"{name!r} is not an output of this signature") from None

    def project_source_transaction(self, transaction):
        """Apply the source-to-IR output map to one native transaction.

        A signed hardware field is often returned by a Behavior model as its
        nonnegative wire pattern.  SolverIR gives that field one mathematical
        signed value.  This is the concrete ``low_Y`` boundary: it preserves
        already-logical values, decodes an in-width signed pattern, and
        recursively canonicalizes declared arrays and records.  No value
        outside those two exact source readings is masked into the domain.
        """
        if type(transaction) is not dict:
            raise ValueError("a source output transaction is an exact mapping")
        fields = dict(self._outputs)
        if set(transaction) != set(fields):
            missing = sorted(set(fields) - set(transaction))
            extra = sorted(set(transaction) - set(fields))
            raise ValueError(
                "a source output transaction does not match its signature "
                f"(missing={missing}, extra={extra})")
        return self.project_source_output_fields(transaction)

    def project_source_output_fields(self, fields):
        """Canonicalize any declared subset of one native output valuation.

        Concrete endpoint streams may be delayed independently, so record audit sees
        one endpoint's fields at a time.  The value map is field-local; applying it to
        each stream is therefore the same ``low_Y`` projection as applying
        :meth:`project_source_transaction` to the complete product.
        """

        if type(fields) is not dict:
            raise ValueError("source output fields form an exact mapping")
        declared = dict(self._outputs)
        unknown = sorted(set(fields) - set(declared))
        if unknown:
            raise ValueError(
                "source output fields are absent from the signature: "
                f"{unknown}")
        return {
            name: _project_source_output_value(declared[name], value,
                                               path=name)
            for name, value in fields.items()
        }

    def validate_output(self, count, buffers, capacity=None):
        """Validate one concrete active output prefix against the task types.

        Padding cells are representation storage and not transactions.  Only
        indices below ``count`` are therefore checked, while every active row
        must carry every declared field and each value must belong to that
        field's exact finite carrier.
        """
        _validate_output_values(
            self._outputs, count, buffers, capacity=capacity)

    def as_dict(self):
        def image(fields, bindings):
            # Preserve the established scalar wire image byte-for-byte.  A
            # structured field carries both its logical type and the explicit
            # logical-to-physical binding; omitting the latter would give two
            # different record representations one public identity.
            return {
                name: (value_type.carrier.as_dict()
                       if value_type.kind == "integer"
                       else {
                           "value_type": value_type.as_dict(),
                           "binding": binding.as_dict(),
                       })
                for (name, value_type), binding in zip(fields, bindings)
            }
        return {
            "inputs": image(self._inputs, self._input_bindings),
            "outputs": image(self._outputs, self._output_bindings),
        }

    def __eq__(self, other):
        return (type(other) is ProcedureSignature
                and self._inputs == other._inputs
                and self._outputs == other._outputs
                and self._input_bindings == other._input_bindings
                and self._output_bindings == other._output_bindings)

    def __hash__(self):
        return hash((self._inputs, self._outputs,
                     self._input_bindings, self._output_bindings))

    def __reduce__(self):
        return type(self), (
            tuple((name, binding) for name, binding in zip(
                self.input_names, self.input_bindings)),
            tuple((name, binding) for name, binding in zip(
                self.output_names, self.output_bindings)),
        )

    def __repr__(self):
        return (f"ProcedureSignature({len(self._inputs)} inputs, "
                f"{len(self._outputs)} outputs)")

_AUTHORITY_TOKEN = object()

class OutputBoundaryError(ValueError):
    """The frontend cannot justify one task-owned output boundary."""

    __slots__ = ("code", "node")

    def __init__(self, code, message, node=None):
        super().__init__(message)
        self.code = str(code)
        self.node = node

class _OutputBoundaryAuthority:
    """Immutable binding between local output evidence and one procedure."""

    __slots__ = (
        "_body_seal",
        "_capacity",
        "_count",
        "_output_vars",
        "_rows",
        "_signature",
    )

    def __init__(self, body_seal, signature, capacity, count, output_vars,
                 rows, *, _token=None):
        if _token is not _AUTHORITY_TOKEN:
            raise TypeError("output-boundary authority is frontend-constructed")
        object.__setattr__(self, "_body_seal", str(body_seal))
        object.__setattr__(self, "_signature", signature)
        object.__setattr__(self, "_capacity", capacity)
        object.__setattr__(self, "_count", count)
        object.__setattr__(self, "_output_vars", tuple(output_vars))
        object.__setattr__(self, "_rows", None if rows is None else tuple(rows))

    @classmethod
    def from_procedure(cls, proc, rows):
        from .ir import command_fingerprint

        return cls(
            command_fingerprint(proc.body), proc.signature,
            proc.output_capacity, proc.count,
            tuple(sorted(proc.output_vars.items())), rows,
            _token=_AUTHORITY_TOKEN,
        )

    def __setattr__(self, name, value):
        raise AttributeError("an output-boundary authority is immutable")

    def validate(self, proc):
        from .ir import command_fingerprint

        if command_fingerprint(proc.body) != self._body_seal:
            raise OutputBoundaryError(
                "output-boundary-body-changed",
                "the procedure body changed after its output proof was sealed",
            )
        if (proc.signature != self._signature
                or proc.output_capacity != self._capacity
                or proc.count != self._count
                or tuple(sorted(proc.output_vars.items())) != self._output_vars):
            raise OutputBoundaryError(
                "output-boundary-contract-changed",
                "the procedure output boundary changed after it was sealed",
            )
        if getattr(proc, "boundary_complete", True) is not True:
            raise OutputBoundaryError(
                "output-boundary-incomplete",
                "this procedure no longer contains its complete output boundary",
            )

    def evidence(self):
        if self._rows is None:
            return None
        paths = {name: {path for path, _inferred, _declared in leaves}
                 for name, leaves in self._rows}
        if any(paths.get(binding.name, set()) !=
               {leaf.path for leaf in binding.layout.leaves()}
               for binding in self._signature.output_bindings):
            # This API certifies the complete logical output contract. Partial
            # certificates serve bounds() but must not bypass the exact check
            # of the still-unproved components.
            return None
        evidence = {}
        for name, leaves in self._rows:
            rows = [
                {
                    "path": list(path),
                    "inferred": (None if inferred is None
                                 else list(inferred)),
                    "declared": list(declared),
                    "method": "frontend-write-range",
                }
                for path, inferred, declared in leaves
            ]
            evidence[name] = (
                {key: value for key, value in rows[0].items()
                 if key != "path"}
                if len(rows) == 1 and rows[0]["path"] == [] else
                {"leaves": rows, "method": "frontend-write-range"}
            )
        return evidence

    def bounds(self):
        """Componentwise finite carriers proved for the encoded boundary."""

        bounds = {"#count": (0, self._capacity)}
        if self._rows is None:
            return bounds
        rows = {
            name: {path: inferred for path, inferred, _declared in leaves}
            for name, leaves in self._rows
        }
        unproved = set()
        for binding in self._signature.output_bindings:
            inferred = rows[binding.name]
            for logical, solver in binding.leaf_bindings:
                if logical not in inferred:
                    unproved.add(solver.variable)
                    continue
                pair = inferred[logical]
                # Output storage is initialized to zero.  Cells at and beyond
                # the active count retain that padding value, so its physical
                # component carrier includes zero even when every emitted
                # logical value is strictly positive.
                pair = ((0, 0) if pair is None else
                        (min(0, pair[0]), max(0, pair[1])))
                previous = bounds.get(solver.variable)
                bounds[solver.variable] = (
                    pair if previous is None else
                    (min(previous[0], pair[0]),
                     max(previous[1], pair[1])))
        # One encoded array carrier covers every leaf stored in that variable.
        # A proof for only some of those leaves cannot certify the whole array.
        for variable in unproved:
            bounds.pop(variable, None)
        return bounds

    def __reduce__(self):
        return _restore_output_boundary, (
            self._body_seal, self._signature, self._capacity, self._count,
            self._output_vars, self._rows,
        )

def _restore_output_boundary(body_seal, signature, capacity, count,
                             output_vars, rows):
    return _OutputBoundaryAuthority(
        body_seal, signature, capacity, count, output_vars, rows,
        _token=_AUTHORITY_TOKEN,
    )

def output_boundary_evidence(proc):
    """Validate and return sealed local evidence, if the frontend produced it."""
    authority = _validated_output_boundary(proc)
    return None if authority is None else authority.evidence()

def output_boundary_bounds(proc):
    """Return sealed component carriers for the encoded output boundary."""

    authority = _validated_output_boundary(proc)
    return None if authority is None else authority.bounds()

def _validated_output_boundary(proc):
    """Return one validated frontend authority without duplicating its gate."""

    authority = getattr(proc, "_output_boundary", None)
    if authority is None:
        return None
    if type(authority) is not _OutputBoundaryAuthority:
        raise OutputBoundaryError(
            "output-boundary-authority-invalid",
            "the procedure carries an invalid output-boundary authority",
        )
    authority.validate(proc)
    return authority
