"""Declare source state and own the procedure's physical storage layout.

Reset witnesses authenticate initial values; read_initial only checks them.
Task-owned boundary bindings may replace declared fields. Dynamic state stays
distinct from reset-only constants. ``declare_state`` fixes source names and
value contracts, spending the caller's source budget. After bounded planning,
``bind_entry`` installs physical leaves and entry facts on the existing lowerer.
``bind_input_views`` then gives written parameters private copies at step entry.
Persistent product allocation and writeback live here too, using the existing
value builders. The lowerer retains source evaluation, attribute-presence guards
and command placement. These phases do not choose reachability or construct an
execution backend.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..core.value_type import ValueType, as_value_type
from ..symbolic import ir
from ..symbolic import ranges
from ..symbolic import typecheck as types_mod
from .extract import LoweringError, _initial_state, _model_arrays, class_bound_names
from .lower_mappings import MappingBuilder
from .lower_returns import (
    OptionSequence, _BatchBuilder, _OptionBuilder, _PositionalBuilder, _RecordBuilder,
)
from .method_binding import method_parameters
from .source_batches import record_batch_payload
from .source_bytes import contains_byte_sequence
from ..symbolic.value_contract import (
    optional_sequence,
    SCALAR,
    UNKNOWN,
    ValueContract,
    contract_of_value,
    representation_value,
)
from .source_values import SourceValueBudgetExceeded
from ..symbolic.state_projection import SourceProductBinding, StateProjectionError
from .state_strings import finite_string_state_domains
from ..symbolic.value_binding import SolverLeafPath, ValueBinding, ValueBindingError
from ..symbolic.value_layout import layout_of_value_type


class ProcedureStorage:
    """Storage shared by every path and helper activation of one procedure.

    Allocation, fixed representation types and length carriers outlive flow
    facts. This owner neither evaluates source nor emits commands, and cannot
    be restored from a branch snapshot. ``temporaries`` records allocation
    history; final Procedure locals are derived from the emitted IR instead.
    """

    __slots__ = (
        "temporaries", "_next", "_reserved", "arrays", "shapes",
        "declaration_contracts", "storage_contracts", "lengths", "capacities",
        "length_images", "mutable_sequence_lengths", "state_length_variables",
        "state_names", "state_presence", "structured_bindings",
        "structured_input_bindings", "structured_input_views", "state_products",
        "_carriers", "_carrier_ranges",
    )

    def __init__(self, arrays=None, *, reserved_names=()):
        self.arrays = dict(arrays or {})
        self.shapes = {str(name): tuple(shape) for name, shape in self.arrays.items()}
        self.temporaries = []
        self._next = 0
        self._reserved = set(reserved_names) | set(self.arrays)
        # Source declarations retain recursive Python carriers. Physical
        # backing storage can be wider than a value's current logical shape.
        self.declaration_contracts = {}
        self.storage_contracts = {}
        self.lengths = {}
        self.capacities = {}
        # Empty path = outer length; nested paths name per-element carriers.
        self.length_images = {}
        self.mutable_sequence_lengths = set()
        self.state_length_variables = set()
        self.state_names = {}
        self.state_presence = {}
        # Source state, public parameters and writable parameter copies have
        # separate namespaces, even when their source spellings coincide.
        self.structured_bindings = {}
        self.structured_input_bindings = {}
        self.structured_input_views = {}
        self.state_products = {}
        # Physical carriers survive branch snapshots and helper scopes. A name
        # may have several carrier forms; occurrence facts, not this registry,
        # select the live source meaning. Source planning caches do not live here.
        self._carriers = {}
        # Whole-activation scalar envelopes on physical leaves are not path
        # facts. Graph returns may be visited before predecessor assignments.
        self._carrier_ranges = {}

    @property
    def carrier_ranges(self):
        return MappingProxyType(self._carrier_ranges)

    def widen_carrier_range(self, name, interval):
        """Join source-planned envelopes; do not install branch-local bounds."""
        previous = self._carrier_ranges.get(name)
        self._carrier_ranges[name] = interval if previous is None else previous.join(interval)

    def carrier(self, kind, name):
        """Find an allocated representation without claiming it is live here."""
        return self._carriers.get(kind, {}).get(name)

    def require_carrier(self, kind, name):
        """Read a representation whose allocation the caller already required."""
        return self._carriers.get(kind, {})[name]

    def bind_carrier(self, name, value):
        """Publish an allocated carrier; never evaluate source or alter flow facts."""
        self._carriers.setdefault(type(value), {})[name] = value

    def carrier_names(self, kind):
        """Read-only allocated-name view for lifetime and presence restoration."""
        return self._carriers.setdefault(kind, {}).keys()

    def retire_carrier(self, kind, name):
        """End an explicit temporary binding, not a branch or helper snapshot."""
        return self._carriers.get(kind, {}).pop(name, None)

    def returned_names(self, result, result_length=None):
        """Physical leaves that escape an inlined helper through its return."""
        names = {result}
        if result_length is not None:
            names.add(result_length)

        def add(holder):
            if holder is None:
                return
            if type(holder) is str:
                names.add(holder)
            elif isinstance(holder, _BatchBuilder):
                names.add(holder.count)
                names.update(holder.buffers.values())
                if holder.form is not None:
                    names.add(holder.form)
            elif isinstance(holder, _PositionalBuilder):
                names.update(holder.names)
            elif isinstance(holder, _RecordBuilder):
                for leaf in holder.leaves.values():
                    add(leaf)
            elif isinstance(holder, _OptionBuilder):
                names.add(holder.present)
                add(holder.payload)

        for kind in (_BatchBuilder, _PositionalBuilder, _RecordBuilder, _OptionBuilder):
            add(self.carrier(kind, result))
        if result in self.lengths:
            names.add(self.lengths[result])
        return frozenset(names)

    def fresh(self):
        while True:
            self._next += 1
            name = f"_t{self._next}"
            if name not in self._reserved:
                self.temporaries.append(name)
                return name

    def state_name(self, name):
        """Physical state identity, independent of lexical parameter bindings."""
        return self.state_names.get(name, name)

    def require_shape(self, name, shape, node):
        """Keep one recursive IR type for every lowered variable name."""
        shape = tuple(shape)
        previous = self.shapes.get(name)
        if previous is not None and previous != shape:
            raise LoweringError(
                node, f"{name} changes representation shape from {previous} "
                      f"to {shape}", code="value-representation-changes")
        self.shapes[name] = shape

    def register_sequence(self, name, length, capacity):
        """Bind a stable length carrier and return its intrinsic finite bounds.

        The caller intersects these bounds with its current path facts. The
        physical capacity itself cannot narrow at a branch or change on reuse.
        """
        if type(capacity) is not int or capacity < 0:
            raise ValueError("a dynamic sequence capacity is a natural number")
        previous_length = self.lengths.get(name)
        previous_capacity = self.capacities.get(name)
        if previous_length is not None and previous_length != length:
            raise ValueError(f"the logical-length carrier of {name} changes")
        if previous_capacity is not None and previous_capacity != capacity:
            raise ValueError(f"the sequence capacity of {name} changes")
        self.lengths[name] = length
        self.capacities[name] = capacity
        image = self.length_images.setdefault(name, {})
        previous = image.get(())
        if previous is not None and previous != length:
            raise ValueError(f"the root length carrier of {name} changes")
        image[()] = length
        return ranges.Bounds(0, capacity)

    def retire_root_length(self, name):
        """Drop a construction-only outer length after an exact binding."""
        self.lengths.pop(name, None)
        self.capacities.pop(name, None)
        image = self.length_images.get(name)
        if image is not None:
            image.pop((), None)
            if not image:
                self.length_images.pop(name, None)


@dataclass(frozen=True, slots=True)
class StateDeclarations:
    """One pass's source facts; declared shapes are read-only to later phases."""

    source_state: tuple[str, ...]
    input_variables: set[str]
    state_names: dict[str, str]
    state_sources: dict[str, str]
    dynamic_state: frozenset[str]
    boundary_initial: dict
    construction_values: dict
    declared_value_types: dict[str, ValueType]
    structured_initial: dict
    ir_initial: dict
    model_arrays: Mapping[str, tuple[int, ...]]
    declaration_contracts: dict[str, ValueContract]
    step_writes: set[str]


def declare_state(model, fn, constants, resolver, _decl_owner, construction, *,
                  signature_input_bindings, dynamic_state, boundary_initial,
                  value_types, read_initial, source_value_budget) -> StateDeclarations:
    """Reconcile reset evidence and task-owned entry state before IR layout."""
    # A construction-only scalar field is a constant of this procedure.  The
    # representation contract later records exactly the same fact; making it
    # available to value lowering here prevents the two stages from disagreeing
    # (for example, ``self.width`` being called folded in the contract while
    # the body still reads an unbounded IR variable named ``width``).
    construction_fold = _initial_state(
        model, constants, typed=True, owner=_decl_owner,
        resolver=resolver)
    # The source fold and the isolated construction answer the same question:
    # what each source state field holds after construction and reset.  The
    # fold is available to syntax-only callers; ``read_initial`` is the exact
    # run-time answer supplied by the production instance builder.  Merge them
    # once, before any representation decision, so state initialization,
    # sequence planning, carrier checks and constant folding cannot each see a
    # different subset of the construction facts.
    source_state = model.state_contract()
    # Public input leaves keep their declared identities. Source attributes
    # occupy another namespace; only collisions need a different IR spelling.
    # Qualifying with `self.` cannot collide with a Python local or temporary.
    input_variables = set(method_parameters(fn)) | {
        variable for binding in signature_input_bindings.values()
        for variable in binding.solver_variables
    }
    state_names = {}
    for name in source_state:
        variable = name
        while variable in input_variables:
            variable = f"self.{variable}"
        state_names[name] = variable
    state_sources = {variable: name for name, variable in state_names.items()}
    if type(dynamic_state) not in (tuple, list, set, frozenset) \
            or any(type(name) is not str or not name for name in dynamic_state):
        raise TypeError("dynamic_state names are exact nonempty strings")
    dynamic_state = frozenset(dynamic_state)
    unknown_dynamic = sorted(dynamic_state - set(source_state))
    if unknown_dynamic:
        raise LoweringError(
            fn, f"the external transition product names state absent from the "
                f"source contract: {unknown_dynamic[:4]}",
            code="dynamic-state-outside-source-contract")
    if boundary_initial is None:
        boundary_initial = {}
    elif type(boundary_initial) is not dict:
        raise TypeError("boundary_initial must be an exact state mapping")
    else:
        boundary_initial = dict(boundary_initial)
    unknown_boundary = sorted(set(boundary_initial) - set(source_state))
    if unknown_boundary:
        raise LoweringError(
            fn, "the closed-loop boundary initializes state absent from the "
                f"source contract: {unknown_boundary[:4]}",
            code="boundary-initial-outside-source-contract")
    construction_values = dict(construction_fold)
    witnessed_exact = construction.exact_values()
    declared_value_types = _validate_declared_value_types(
        value_types, source_state, witnessed_exact, fn)
    try:
        string_domains = finite_string_state_domains(
            model, resolver, witnessed_exact,
            exclude=set(declared_value_types) | set(dynamic_state),
            budget=source_value_budget)
    except SourceValueBudgetExceeded as exc:
        raise LoweringError(
            exc.node, "source string-state planning exceeded its aggregate work budget",
            code="source-plan-budget-exceeded") from None
    declared_value_types.update({name: ValueType.enumeration(members)
                                 for name, members in string_domains.items()
                                 if name in source_state})
    structured_initial = {
        name: witnessed_exact[name] for name in declared_value_types
    }
    ir_initial = construction.values()
    ir_initial.update({
        name: representation_value(value)
        for name, value in witnessed_exact.items()
        if name in source_state and contains_byte_sequence(contract_of_value(value))
    })
    # ``unsupported`` is relative to IR state.  Booleans, integers and their
    # recursively fixed arrays are carried exactly; a finite mapping can still
    # be an exact reset-only constant.  Discard only fields the isolation could
    # not transport at all.
    for name in construction.unsupported():
        if name not in witnessed_exact:
            construction_values.pop(name, None)
    for name, value in (read_initial or {}).items():
        if name not in source_state:
            continue
        if name in witnessed_exact and not construction.matches(name, value):
            raise LoweringError(
                fn, f"the supplied initial value of {name}, {value!r}, "
                    f"disagrees with the authenticated construction value "
                    f"{witnessed_exact[name]!r}",
                code="initial-state-disagrees")
    for name, value in witnessed_exact.items():
        if name not in source_state:
            continue
        if name in construction_values and not construction.matches(
                name, construction_values[name]):
            raise LoweringError(
                fn, f"{name} starts at {construction_values[name]!r} by "
                    f"reading the source and {value!r} by constructing the "
                    "model",
                code="initial-state-disagrees")
        construction_values[name] = value
    # Some state belongs to the closed-loop environment rather than to the
    # source reset.  External memories are the canonical case: concrete replay
    # binds a ReferenceMemory after reset, while exact analysis binds the same
    # finite image as an array.  Keep this distinct from ``read_initial``, which
    # only checks the reset result and must never override it.
    construction_values.update(boundary_initial)
    ir_initial.update(boundary_initial)
    structured_initial.update({
        name: value for name, value in boundary_initial.items()
        if name in declared_value_types
    })
    syntactic_arrays = _model_arrays(model, _decl_owner, resolver=resolver)
    model_arrays = dict(syntactic_arrays)
    for name, value in construction_values.items():
        if name not in source_state:
            continue
        if name in declared_value_types:
            # The explicit ValueType, not this runtime value, owns its shape.
            continue
        contract = contract_of_value(value)
        if contract.is_sequence and contract.physical_shape is not None:
            model_arrays[name] = contract.physical_shape
    declaration_contracts = _declaration_value_contracts(
        {name: value for name, value in construction_values.items()
         if name not in declared_value_types},
        {name: shape for name, shape in model_arrays.items()
         if name not in declared_value_types}, fn)
    declaration_contracts.update({
        name: source_contract_of_value_type(value_type)
        for name, value_type in declared_value_types.items()
    })
    # A selected multi-clock handler may read state written by another handler
    # between its own invocations.  Those fields are dynamic at this boundary
    # even when this one procedure never writes them, so they must not be
    # folded to their construction values.
    step_writes = set(model.step_state_contract()) | set(dynamic_state)
    non_ir_step_state = sorted(
        (step_writes & set(witnessed_exact))
        - set(ir_initial) - set(declared_value_types)
        - {name for name, value in witnessed_exact.items()
           if value is None or contract_of_value(value).is_record
           or optional_sequence(contract_of_value(value))
           or contract_of_value(value).is_mapping
           or record_batch_payload(contract_of_value(value)) is not None})
    if non_ir_step_state:
        first = non_ir_step_state[0]
        raise LoweringError(
            model.cls,
            f"the state {first!r} starts as {witnessed_exact[first]!r}, "
            "which is not a Boolean, integer, or recursively finite fixed "
            "sequence carried by SolverIR",
            code="state-value-not-ir-carriable")
    return StateDeclarations(
        source_state=source_state,
        input_variables=input_variables,
        state_names=state_names,
        state_sources=state_sources,
        dynamic_state=dynamic_state,
        boundary_initial=boundary_initial,
        construction_values=construction_values,
        declared_value_types=declared_value_types,
        structured_initial=structured_initial,
        ir_initial=ir_initial,
        model_arrays=MappingProxyType(model_arrays),
        declaration_contracts=declaration_contracts,
        step_writes=step_writes)


def source_contract_of_value_type(value_type):
    """The source representation implied by an explicit ValueType.

    This is a declaration projection, never a reading of a construction
    value.  Fixed arrays use the source subset's canonical mutable list
    carrier; a tuple-valued construction presented for such state is
    rejected at the boundary rather than silently assigned list semantics.
    """
    if value_type.kind in ("boolean", "integer", "enumeration"):
        return SCALAR
    if value_type.kind == "array":
        return ValueContract.sequence(
            "list", value_type.cells, value_type.cells,
            source_contract_of_value_type(value_type.element))
    return ValueContract.record({
        name: source_contract_of_value_type(child)
        for name, child in value_type.fields
    })


def _declaration_value_contracts(values, array_shapes, node):
    """One recursive representation contract for every source field.

    Constructed values prove both Python carrier and recursive shape.  Static
    declaration analysis is only a fallback for a field absent from that
    authenticated boundary; it cannot veto the value Python actually creates.
    Step assignments are checked against the resulting contract when they are
    lowered.
    """
    contracts = {
        name: contract_of_value(value) for name, value in values.items()
    }
    for name, shape in array_shapes.items():
        declared = ValueContract.fixed(shape)
        concrete = contracts.get(name, UNKNOWN)
        if concrete is UNKNOWN:
            contracts[name] = declared
            continue
    return contracts


def _declared_source_value_matches(value_type, value):
    """Exact source-host carrier check for one declared state value."""
    if value_type.kind == "boolean":
        return type(value) is bool
    if value_type.kind == "integer":
        return value_type.carrier.admits(value)
    if value_type.kind == "enumeration":
        return type(value) is str and value in value_type.members
    if value_type.kind == "array":
        # SolverIR arrays reconstruct to lists.  Admitting a tuple here would
        # erase a source-observable carrier distinction at the state boundary.
        return (type(value) is list and len(value) == value_type.cells
                and all(_declared_source_value_matches(value_type.element, item)
                        for item in value))
    return (type(value) is dict
            and set(value) == {name for name, _child in value_type.fields}
            and all(_declared_source_value_matches(child, value[name])
                    for name, child in value_type.fields))


def _validate_declared_value_types(value_types, source_state, exact_values, where):
    """Validate an explicit source-state type context without value inference."""
    if value_types is None:
        return {}
    if type(value_types) is not dict:
        raise LoweringError(
            where, "value_types is an exact source-state name/type mapping",
            code="value-type-context-invalid")
    out = {}
    for name, declaration in value_types.items():
        if type(name) is not str or not name or name in out:
            raise LoweringError(
                where, "value_types needs unique nonempty source-state names",
                code="value-type-context-invalid")
        if name not in source_state:
            raise LoweringError(
                where, f"the declared value type {name!r} is not source state",
                code="value-type-name-undeclared")
        try:
            value_type = as_value_type(declaration)
        except (TypeError, ValueError) as exc:
            raise LoweringError(
                where, f"the declared value type {name!r} is invalid: {exc}",
                code="value-type-context-invalid") from None
        if name not in exact_values:
            raise LoweringError(
                where, f"the declared structured state {name!r} has no exact "
                       "authenticated construction value",
                code="declared-state-value-unavailable")
        value = exact_values[name]
        if not _declared_source_value_matches(value_type, value):
            raise LoweringError(
                where, f"the construction value of {name!r} disagrees with "
                       "its declared recursive ValueType",
                code="declared-state-value-mismatch")
        try:
            layout_of_value_type(value_type).flatten(value)
        except ValueError as exc:
            raise LoweringError(
                where, f"the construction value of {name!r} cannot be bound: "
                       f"{exc}",
                code="declared-state-value-mismatch") from None
        out[name] = value_type
    return out


# A sequence-element descent is distinct from named record/payload paths.
# Both layout allocation and expression lowering use this same identity.
SEQUENCE_ELEMENT = object()


def contract_at_dynamic_path(contract, path):
    """The recursive contract named by one dynamic-length path."""
    current = contract
    for part in path:
        if part is SEQUENCE_ELEMENT:
            if not current.is_sequence or current.element is None:
                return UNKNOWN
            current = current.element
        elif part == ("payload",):
            if not current.is_option or current.payload is None:
                return UNKNOWN
            current = current.payload
        elif (isinstance(part, tuple) and len(part) == 2
              and part[0] == "field"):
            if not current.is_record:
                return UNKNOWN
            current = current.field_contracts.get(part[1], UNKNOWN)
        else:
            return UNKNOWN
    return current

def bind_product(low, name, contract, node):
    """Share the local product carrier, but retain its leaves across calls."""
    if contract.is_none:
        return SourceProductBinding(name, contract, ())
    if contract.is_mapping:
        builder = MappingBuilder.allocate(low, contract, node)
        low.storage.bind_carrier(low.storage.state_name(name), builder)
    elif contract.is_record:
        builder = _RecordBuilder.allocate(low, contract, node)
        low.storage.bind_carrier(low.storage.state_name(name), builder)
    elif optional_sequence(contract):
        builder = OptionSequence.allocate(low, contract, node)
        low.storage.bind_carrier(low.storage.state_name(name), builder)
    elif contract.is_sequence:
        builder = _BatchBuilder(
            low.storage.fresh(), {field: low.storage.fresh() for field, _ in contract.element.fields},
            contract.maximum)
        low.storage.bind_carrier(low.storage.state_name(name), builder)
        # The ordinary batch allocator owns column types and shapes. Its
        # initialization command is not a step: native construction below
        # supplies these persistent values, including the logical count.
        builder.initialize(low, contract.element, node)
    else:
        builder = _OptionBuilder.allocate(low, contract, node)
        low.storage.bind_carrier(low.storage.state_name(name), builder)

    def leaves(value, path=()):
        if isinstance(value, OptionSequence):
            yield path + ('present',), value.present.name
            yield path + ('payload',), value.payload.name
        elif isinstance(value, MappingBuilder):
            for part, variable in (("length", value.count), ("keys", value.keys),
                                   ("values", value.values)):
                yield path + (part,), variable
        elif isinstance(value, _OptionBuilder):
            yield path + ("present",), value.present
            yield from leaves(value.payload, path + ("payload",))
        elif isinstance(value, _RecordBuilder):
            for child_path, child in value.leaves.items():
                yield from leaves(child, path + child_path)
        elif isinstance(value, _BatchBuilder):
            yield path + ("length",), value.count
            for field, variable in value.buffers.items():
                yield path + ("elements", field), variable
        elif value is not None:
            yield path, value

    try:
        return SourceProductBinding(name, contract, tuple(leaves(builder)))
    except StateProjectionError as exc:
        raise LoweringError(
            node, f"the persistent product has no exact fixed leaf layout: {exc}",
            code="state-product-layout-unsupported") from None


def assign_product(low, target, value, source, actual):
    """Write an already evaluated record/Option, preserving RHS snapshots."""
    if not (isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name) and target.value.id == "self"
            and target.attr in low.storage.state_products):
        return None
    product = low.storage.state_products[target.attr]
    physical = low.storage.state_name(target.attr)
    if product.contract.is_none:
        if not actual.is_none:
            raise LoweringError(target, "a None-only state changes its representation",
                                code="state-value-contract-disagrees")
        return low._define_state_attribute(target, ir.Skip())
    if product.contract.is_record:
        # Python evaluates the whole RHS before rebinding the record.
        # Snapshot all leaves before any old state leaf is overwritten
        # (including swaps and recursive record/array leaves).
        command = low.storage.require_carrier(_RecordBuilder, physical).assign(
            low, low._capture(value), source)
    else:
        command = low.storage.require_carrier(_OptionBuilder, physical).assign(
            low, value, source, actual=actual)
    low.flow.contracts[physical] = actual
    return low._define_state_attribute(target, command)


def bind_entry(low, prepared, declarations, planned, *,
               fixed, static_fields, state_ranges) -> set[str]:
    """Install entry storage on the caller's lowerer, in allocation order.

    This follows source declaration and bounded planning; it neither constructs
    another lowerer nor creates a budget. Return the inherited late attributes
    needed by final source-state projection. No step body is emitted here.
    """
    model = prepared.model
    fn = model.step
    goal_plan = prepared.goal_plan
    signature_input_bindings = prepared.signature_input_bindings
    state_names = declarations.state_names
    source_state = declarations.source_state
    construction_values = declarations.construction_values
    step_writes = declarations.step_writes
    declared_value_types = declarations.declared_value_types
    params = planned.params
    declaration_contracts = planned.declaration_contracts
    boundary_contracts = planned.boundary_contracts
    low.storage.state_names = state_names
    low.static_fields.update(static_fields)
    for name in sorted((set(step_writes) | set(model.step_state_reads()))
                       & set(source_state) - set(construction_values) - set(fixed)):
        low.storage.state_presence[name] = low.storage.fresh()
    inherited = low.storage.state_presence.keys() & class_bound_names(model.cls)
    reset_writes = (set(model.reachable_state_writes([model.reset]))
                    if model.reset is not None else set())
    if reset_writes & low.storage.state_presence.keys():
        raise LoweringError(
            model.reset,
            "a field absent after construction can be created by a later reset; "
            "that reset needs an explicit state transition",
            code="state-conditional-reset-unsupported")
    for binding in goal_plan.operation_goals:
        low.operation_goals.setdefault(binding.site, []).append(binding)
    for binding in goal_plan.read_goals:
        low.read_goals.setdefault(binding.site, []).append(binding)
    for name, value in construction_values.items():
        low._remember_source_scalar_type(
            state_names.get(name, name), types_mod.of_value(value))
    for name in sorted(declared_value_types):
        value_type = declared_value_types[name]
        low._remember_source_scalar_type(
            state_names[name],
            (types_mod.BOOLEAN if value_type.kind == "boolean" else
             types_mod.INTEGER if value_type.kind in {
                 "integer", "enumeration"} else None))
        try:
            # Preserve the established scalar/ordinary-array representation
            # exactly where the logical paths are already SolverIR index paths.
            # A record component is a logical string path and therefore needs
            # explicit compiler-owned leaves; it never acquires a spelling by
            # concatenating field names.
            try:
                binding = ValueBinding.direct(name, value_type)
                if state_names[name] != name:
                    binding = ValueBinding(name, value_type, tuple(
                        (logical, SolverLeafPath(state_names[name], solver.indices))
                        for logical, solver in binding.leaf_bindings))
            except ValueBindingError as exc:
                if exc.code != "value-binding-structured-path-undeclared":
                    raise
                binding = ValueBinding(name, value_type, tuple(
                    (leaf.path, SolverLeafPath(low.storage.fresh()))
                    for leaf in layout_of_value_type(value_type).leaves()))
        except ValueBindingError as exc:
            raise LoweringError(
                fn, f"the declared value {name!r} cannot be bound to "
                    f"SolverIR leaves: {exc}",
                code=exc.code) from None
        for variable, kind in binding.solver_types():
            shape = types_mod.shape_of(kind)
            low.storage.shapes[variable] = shape
            low.flow.contracts[variable] = (
                ValueContract.fixed(shape) if shape else SCALAR)
            if shape:
                low.flow.lengths[variable] = shape
        low.storage.structured_bindings[name] = binding
    for name, contract in sorted(declaration_contracts.items()):
        if contract.is_mapping and name in low.static_fields:
            # Read-only dictionaries already have the exact frozen-table
            # selector; they do not need a second mutable state carrier.
            continue
        if name in low.storage.structured_bindings or not (
                contract.is_none or contract.is_option or contract.is_record
                or optional_sequence(contract)
                or contract.is_mapping
                or (contract.is_sequence and record_batch_payload(contract) is not None)):
            continue
        low.storage.state_products[name] = bind_product(low, name, contract, fn)
    state_solver_paths = {
        solver
        for binding in low.storage.structured_bindings.values()
        for _logical, solver in binding.leaf_bindings
    }
    for name, binding in sorted(signature_input_bindings.items()):
        overlap = sorted(
            (solver.identity for _logical, solver in binding.leaf_bindings
             if solver in state_solver_paths), key=repr)
        if overlap:
            raise LoweringError(
                fn, f"the input value {name!r} shares physical SolverIR "
                    f"leaves with source state: {overlap!r}",
                code="value-binding-solver-path-duplicate")
        for variable, kind in binding.solver_types():
            shape = types_mod.shape_of(kind)
            known = low.storage.shapes.get(variable)
            if known is not None and known != shape:
                raise LoweringError(
                    fn, f"the input binding gives {variable!r} shape "
                        f"{shape!r}, conflicting with {known!r}",
                    code="value-binding-type-mismatch")
            low.storage.shapes[variable] = shape
            low.flow.contracts[variable] = (
                ValueContract.fixed(shape) if shape else SCALAR)
            if shape:
                low.flow.lengths[variable] = shape
        low.storage.structured_input_bindings[name] = binding
        leaf_contracts = {
            leaf.path: leaf for leaf in binding.layout.leaves()
        }
        physical_bounds = {}
        for logical, solver in binding.leaf_bindings:
            leaf = leaf_contracts[logical]
            bounds = ((0, 1) if leaf.semantic_type == types_mod.BOOLEAN else
                      leaf.carrier.bounds)
            current = physical_bounds.get(solver.variable)
            physical_bounds[solver.variable] = (
                bounds if current is None else
                (min(current[0], bounds[0]), max(current[1], bounds[1])))
        for variable, (lo, hi) in physical_bounds.items():
            value = ranges.Range(lo, hi)
            low.flow.ranges[variable] = value
            low.flow.bounds[variable] = ranges.Bounds.of(value)
    structured_range_names = {
        variable
        for binding in (*low.storage.structured_bindings.values(), *low.storage.state_products.values())
        for variable in binding.solver_variables
    }
    for name, value in (state_ranges or {}).items():
        if name not in structured_range_names:
            continue
        low.flow.ranges[name] = value
        low.flow.bounds[name] = ranges.Bounds.of(value)
    low.storage.declaration_contracts = {
        state_names[name]: contract for name, contract in declaration_contracts.items()
    }
    for source_name, contract in declaration_contracts.items():
        name = state_names[source_name]
        shape = contract.physical_shape
        if shape is not None:
            low.storage.shapes[name] = shape
        entry_contract = boundary_contracts.get(source_name, contract)
        low.flow.contracts[name] = entry_contract
        if shape:
            # A construction-defined state field exists at step entry.  Its
            # physical shape is therefore a live flow fact, not merely the
            # later type-invariance postcondition in representation_shapes.
            # Syntax-only declarations remain outside lengths until a value
            # actually defines them.
            low.flow.lengths[name] = shape
        if contract.is_sequence and contract.carrier is not None:
            low.flow.carriers[name] = contract.carrier
        if source_name in step_writes and source_name not in low.storage.state_products:
            holders = low._register_dynamic_state(name, contract, fn)
            for path, holder in holders.items():
                if low.storage.shapes.get(holder, ()):
                    continue
                logical = contract_at_dynamic_path(entry_contract, path)
                if logical.is_sequence:
                    low.flow.bounds[holder] = ranges.Bounds(logical.minimum, logical.maximum)
                    low.flow.ranges[holder] = ranges.Range(logical.minimum, logical.maximum)
    for name in params:
        if name not in low.storage.structured_input_bindings:
            low._remember_source_scalar_type(name, types_mod.INTEGER)
        if name in low.storage.structured_input_bindings:
            continue
        low.storage.shapes[name] = ()
    return inherited


def bind_input_views(low, fn, params, outputs, attributes, entry):
    """Give writable source parameters private storage; public inputs stay read-only.

    Return the extended entry command and its lexical renames. Allocate on the
    existing lowerer after output buffers and ROMs, before emitting the body.
    """
    # An attribute of ``self`` and a local of the same name are two variables
    # in the source and one here, since an attribute lowers to a variable
    # named after it. The step then reads what it wrote: a model that copies
    # ``self.valid`` into ``valid``, sets ``self.valid``, and tests ``valid``
    # tests the new value rather than the old one. Outputs are reserved the
    # same way, a local of an output's name being an array here and a value
    # there. Renaming the local is what keeps the reserved names reserved.
    reserved = set(outputs) | attributes
    root_renames = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) \
                and n.id in reserved \
                and n.id not in low.storage.structured_input_bindings \
                and n.id not in root_renames:
            root_renames[n.id] = low.storage.fresh()
    # Procedure inputs belong to the public, read-only IR context.  Python
    # parameters are local bindings and may be rebound or mutated.  Give each
    # written parameter a private by-value view initialized once at entry;
    # namespace collisions use the same mechanism.
    parameter_locals = (
        set(low.resolver.written_parameters(fn)) | (set(params) & reserved))
    structured_rebound = sorted(
        set(low.storage.structured_input_bindings) & parameter_locals)
    for name in structured_rebound:
        public = low.storage.structured_input_bindings[name]
        replacements = {
            variable: low.storage.fresh() for variable in public.solver_variables}
        try:
            local = ValueBinding(name, public.value_type, tuple(
                (logical, SolverLeafPath(
                    replacements[solver.variable], solver.indices))
                for logical, solver in public.leaf_bindings))
        except ValueBindingError as exc:
            raise LoweringError(
                fn, f"the private view of input {name!r} cannot retain its "
                    f"declared layout: {exc}", code=exc.code) from None
        low.storage.structured_input_views[name] = local
        leaves = local.leaf_bindings
        if (local.value_type.kind in ("boolean", "integer", "enumeration")
                and len(leaves) == 1 and leaves[0][0] == ()
                and leaves[0][1].indices == ()):
            # The ordinary name-writing paths (loop targets, destructuring and
            # chained scalar assignment) use lexical renaming.  Point them at
            # the same private cell as the structured read/write protocol.
            root_renames[name] = leaves[0][1].variable
        public_types = dict(public.solver_types())
        for public_variable, private_variable in replacements.items():
            kind = public_types[public_variable]
            low._remember_source_scalar_type(private_variable, kind)
            shape = types_mod.shape_of(kind)
            low.storage.shapes[private_variable] = shape
            low.flow.contracts[private_variable] = (
                ValueContract.fixed(shape) if shape else SCALAR)
            if shape:
                low.flow.lengths[private_variable] = shape
            if public_variable in low.flow.ranges:
                low.flow.ranges[private_variable] = low.flow.ranges[public_variable]
            if public_variable in low.flow.bounds:
                low.flow.bounds[private_variable] = low.flow.bounds[public_variable]
            entry = ir.Seq(
                entry,
                ir.Assign(private_variable, ir.Var(public_variable)))
    for name in params:
        if name not in parameter_locals or name in low.storage.structured_input_views:
            continue
        if name not in root_renames:
            root_renames[name] = low.storage.fresh()
        local = root_renames[name]
        low.storage.shapes[local] = ()
        low.flow.contracts[local] = SCALAR
        low._remember_source_scalar_type(local, low.source_scalar_types.get(name))
        if name in low.flow.ranges:
            low.flow.ranges[local] = low.flow.ranges[name]
        if name in low.flow.bounds:
            low.flow.bounds[local] = low.flow.bounds[name]
        entry = ir.Seq(entry, ir.Assign(local, ir.Var(name)))
    return entry, root_renames
