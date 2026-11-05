"""Build final IR contexts and the initial image after body lowering.

Declaration and entry binding stay in lower_state. These phases use the retained
body to select physical variables and materialize their existing contracts; they
do not infer reachability, check goals or choose an execution backend. The caller
checks goal accounting between collect_contexts and initial_image.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..symbolic import ir
from . import lower_state
from ..symbolic import typecheck as types_mod
from .extract import LoweringError
from .method_binding import method_parameters
from .source_bytes import contains_byte_sequence
from ..symbolic.value_contract import UNKNOWN, ValueContract, representation_value
from ..symbolic.state_projection import (
    DynamicSequenceBinding,
    StateAbsentBinding,
    StatePresenceBinding,
    StateProjectionError,
    StateVariableBinding,
)
from ..symbolic.value_binding import ValueBindingError


@dataclass(frozen=True, slots=True)
class ProcedureContexts:
    """Retained physical contexts and source views; members are not deep-copied."""

    state: list[str]
    inputs: list[str]
    locals_: list[str]
    held: set[str]
    active_structured: dict
    active_products: dict
    absent_state: set[str]


def collect_contexts(low, prepared, declarations, *, body, reachable_bodies,
                     fixed, inherited, output_vars) -> ProcedureContexts:
    """Keep written logical state and live physical leaves, not scratch history."""
    model = prepared.model
    fn = model.step
    resolver = prepared.resolver
    step_writes = declarations.step_writes
    dynamic_state = declarations.dynamic_state
    state_names = declarations.state_names
    logical_inputs = list(method_parameters(fn))
    inputs = []
    for name in logical_inputs:
        binding = low.storage.structured_input_bindings.get(name)
        if binding is None:
            inputs.append(name)
        else:
            inputs.extend(binding.solver_variables)
    # A resolved method reference names executable code, whether called
    # directly or captured in a local alias. It is not a persistent data cell.
    attributes = {
        n.attr for owner in reachable_bodies for n in resolver.execution_nodes(owner)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name) and n.value.id == "self"
        and n.attr not in fixed and n.attr not in low.static_fields
        and resolver.resolve_method(resolver.scope_of(n), n.attr)[0] != "method"
    }
    # A field the step carries is logical source state.  An explicitly bound
    # structured field is not also a SolverIR variable: only the physical leaf
    # variables named by its immutable binding enter the executable context.
    # Keeping both names would collapse the logical/physical distinction and
    # invite later passes to infer a second representation for the source
    # object.  A scalar/flat undeclared field retains the exact old path.
    # The source state context is a property of the procedure, not of the
    # paths reached at one particular analysis depth.  A bounded source plan
    # may prove the only write to a field unreachable at this depth; the step
    # then carries that field unchanged rather than deleting it from the
    # state product.  Include the call-graph write contract explicitly so a
    # pruned helper or arm still has this identity component.
    logical_state = sorted(attributes | step_writes)
    assigned = {c.var for c in ir.walk(body)
                if isinstance(c, (ir.Assign, ir.Store))}
    retained_variables = assigned | variable_reads(body)
    # A bounded plan can prune every creation/read of a late attribute. Its
    # absence is still observable to source audit, but there is no payload to
    # type or initialize. Keep that logical field through an explicit empty
    # projection instead of manufacturing an integer zero for it.
    absent_state = {
        name for name, present in low.storage.state_presence.items()
        if name in attributes and name not in inherited and name not in dynamic_state
        and name not in low.storage.state_products and name not in low.storage.structured_bindings
        and not ({state_names[name], present} & retained_variables)
    }
    for name in absent_state:
        low.storage.state_presence.pop(name)
    active_structured = {
        name: low.storage.structured_bindings[name]
        for name in logical_state if name in low.storage.structured_bindings
    }
    active_products = {
        name: low.storage.state_products[name]
        for name in logical_state if name in low.storage.state_products
    }
    structured_variables = {
        variable
        for binding in (*active_structured.values(), *active_products.values())
        for variable in binding.solver_variables
    }
    state = sorted(
        {low.storage.state_name(name) for name in
         set(logical_state) - set(active_structured) - set(active_products) - absent_state}
        | structured_variables | set(low.storage.state_length_variables)
        | set(low.storage.state_presence.values()))
    # A name the body assigns that is neither state, input nor output is a
    # local. Declaring them is what lets the well-formedness check reject an
    # assignment to a name nothing introduced.
    held = set(output_vars.values())
    assigned.update(c.index for c in ir.walk(body) if isinstance(c, ir.For))
    # The variables holding outputs are subtracted from both halves, not only
    # from what the body assigns. A temporary that ended up holding an output
    # port was declared twice, once as a local and once as that port's
    # variable, and a variable in two roles is exactly what the
    # well-formedness check exists to refuse. Thirty-four of the models that
    # lower were in that shape.
    # Declare the bindings the final executable term actually carries.  The
    # lowering's scratch-name ledger also contains speculative temporaries
    # whose expressions folded away (and source loop names removed by an empty
    # or constant expansion).  Retaining those names as untyped Procedure
    # locals made the four-context semantic contract incomplete even though no
    # execution could read or write them.  ``Assign``/``Store`` targets and
    # retained ``For`` indices are the IR's complete binding forms, so derive
    # the local context from those forms rather than compiler allocation
    # history.
    locals_ = sorted(assigned - set(state) - set(inputs) - held)
    return ProcedureContexts(
        state=state,
        inputs=inputs,
        locals_=locals_,
        held=held,
        active_structured=active_structured,
        active_products=active_products,
        absent_state=absent_state)


def initial_image(low, prepared, declarations, planned, contexts):
    """Materialize reset values and exact types without guessing absent state."""
    model = prepared.model
    fn = model.step
    ir_initial = declarations.ir_initial
    state_names = declarations.state_names
    state_sources = declarations.state_sources
    structured_initial = declarations.structured_initial
    construction_values = declarations.construction_values
    declaration_contracts = planned.declaration_contracts
    state = contexts.state
    active_structured = contexts.active_structured
    active_products = contexts.active_products
    # What the state holds before the first step. Folded from the source here, and
    # overridden by what constructing the model actually leaves when a caller supplies
    # it: the initial state is *defined* as what the harness's model holds after
    # `reset`, so folding is the approximation and construction is the definition.
    # Where both can say they have to agree, since a disagreement is either a folding
    # rule that is wrong or a `reset` whose answer depends on something.
    initial = {}
    for source_name, value in ir_initial.items():
        name = state_names.get(source_name, source_name)
        if name not in set(state):
            continue
        contract = declaration_contracts.get(source_name, UNKNOWN)
        try:
            initial[name] = (
                physical_native_value(value, contract)
                if contract.is_sequence else representation_value(value))
        except ValueError as exc:
            raise LoweringError(
                fn, f"the initial state {name!r} has no value in its planned "
                    f"physical representation: {exc}",
                code="initial-state-representation-disagrees") from None
    for name, image in low.storage.length_images.items():
        source_name = state_sources.get(name, name)
        value = ir_initial.get(source_name)
        if type(value) not in (list, tuple):
            if not any(holder in low.storage.state_length_variables
                       for holder in image.values()):
                continue
            raise LoweringError(
                fn, f"the dynamic state {name!r} has no sequence initial "
                    "value", code="initial-state-representation-disagrees")
        contract = declaration_contracts.get(source_name, UNKNOWN)
        for path, holder in image.items():
            if holder not in low.storage.state_length_variables:
                continue
            try:
                initial[holder] = native_length_at(value, contract, path)
            except ValueError as exc:
                raise LoweringError(
                    fn, f"the dynamic state {name!r} has no initial length "
                        f"image: {exc}",
                    code="initial-state-representation-disagrees") from None
    for name, binding in active_structured.items():
        try:
            initial.update(binding.solver_values(structured_initial[name]))
        except (ValueBindingError, ValueError) as exc:
            raise LoweringError(
                fn, f"the initial logical state {name!r} cannot be materialized "
                    f"through its declared SolverIR binding: {exc}",
                code="declared-state-value-mismatch") from None
    for name, binding in active_products.items():
        if name in construction_values:
            initial.update(binding.solver_values(construction_values[name]))
    initial.update((variable, False) for variable in low.storage.state_presence.values())
    declared_types = {
        variable: kind
        for binding in active_structured.values()
        for variable, kind in binding.solver_types()
    }
    declared_types.update((variable, types_mod.BOOLEAN)
                          for variable in low.storage.state_presence.values())
    for binding in low.storage.structured_input_bindings.values():
        for variable, kind in binding.solver_types():
            previous = declared_types.get(variable)
            if previous is not None and previous != kind:
                raise LoweringError(
                    fn, f"SolverIR variable {variable!r} has conflicting "
                        "state/input binding types",
                    code="value-binding-type-mismatch")
            declared_types[variable] = kind
    for binding in low.storage.structured_input_views.values():
        for variable, kind in binding.solver_types():
            previous = declared_types.get(variable)
            if previous is not None and previous != kind:
                raise LoweringError(
                    fn, f"SolverIR variable {variable!r} has conflicting "
                        "private input-view types",
                    code="value-binding-type-mismatch")
            declared_types[variable] = kind
    for name in state:
        if name in declared_types:
            continue
        if name in low.storage.state_length_variables:
            shape = low.storage.shapes.get(name, ())
            declared_types[name] = (
                types_mod.of_shape(shape) if shape else types_mod.INTEGER)
            continue
        contract = low.storage.declaration_contracts.get(name, UNKNOWN)
        concrete = types_mod.of_value(initial[name]) if name in initial else None
        if concrete is not None:
            declared_types[name] = concrete
        elif contract.kind == "scalar":
            # The source scalar union includes both int and bool. Without a
            # construction value, use an exact definition if known; otherwise
            # let the ordinary graph type pass infer it, never guess integer.
            if name in low.source_scalar_types:
                declared_types[name] = low.source_scalar_types[name]
        elif contract.physical_shape is not None:
            declared_types[name] = types_mod.of_shape(
                contract.physical_shape)
    return initial, declared_types


def zero_native_contract(contract):
    """Canonical host value for one finite recursive storage contract."""
    if contract.kind == "scalar":
        return 0
    if not contract.is_sequence or contract.maximum is None:
        raise ValueError("a sequence storage cell needs a finite contract")
    if contract.maximum and contract.element is None:
        raise ValueError("a nonempty sequence storage cell needs an element")
    child = (0 if contract.element is None else
             zero_native_contract(contract.element))
    return [child for _ in range(contract.maximum)]



def physical_native_value(value, contract):
    """Materialise one source value in its fixed-capacity SolverIR image."""
    from ..symbolic.uniform_array import UniformArray

    if contract.kind == "scalar":
        return representation_value(value)
    if not contract.is_sequence or contract.maximum is None:
        raise ValueError("a source state value has no sequence storage image")
    if type(value) is UniformArray:
        if (contract.minimum != len(value) or contract.maximum != len(value)
                or contract.element is None
                or contract.element.kind != "scalar"):
            raise ValueError(
                "a compact array is paired with another sequence contract")
        return value
    if type(value) not in (list, tuple, bytes, bytearray):
        raise ValueError("a sequence contract is paired with a non-sequence")
    if not contract.minimum <= len(value) <= contract.maximum:
        raise ValueError("a sequence value lies outside its length contract")
    if value and contract.element is None:
        raise ValueError("a nonempty sequence has no element contract")
    cells = [
        physical_native_value(item, contract.element)
        for item in value
    ]
    if len(cells) < contract.maximum:
        if contract.element is None:
            raise ValueError("sequence padding has no element contract")
        cells.extend(
            zero_native_contract(contract.element)
            for _ in range(contract.maximum - len(cells)))
    return cells



def dynamic_length_specs(contract, path=(), ancestors=()):
    """Yield ``(path, storage-shape, capacity)`` for every live length.

    A nested dynamic sequence needs one length for each element of every
    enclosing sequence.  Record fields do not add a storage dimension; their
    fixed field name is retained in the logical path so independent dynamic
    leaves never share a carrier.
    """
    if not isinstance(contract, ValueContract):
        return
    if contract.is_option:
        yield from dynamic_length_specs(
            contract.payload, path + (("payload",),), ancestors)
        return
    if contract.is_record:
        for field, child in contract.fields:
            yield from dynamic_length_specs(
                child, path + (("field", field),), ancestors)
        return
    if not contract.is_sequence:
        return
    if contract.exact_length is None:
        yield path, tuple(ancestors), contract.maximum
    if contract.element is not None:
        yield from dynamic_length_specs(
            contract.element, path + (lower_state.SEQUENCE_ELEMENT,),
            tuple(ancestors) + (contract.maximum,))



def zero_native_shape(shape):
    """Host-list zero image of one recursive integer metadata shape."""
    shape = tuple(shape)
    if not shape:
        return 0
    return [zero_native_shape(shape[1:]) for _ in range(shape[0])]



def native_length_at(value, contract, path):
    """Materialise one dynamic-length carrier from a constructed value."""
    if not path:
        if not contract.is_sequence or type(value) not in (list, tuple):
            raise ValueError("a dynamic length has no sequence value")
        return len(value)
    head, tail = path[0], path[1:]
    if head is lower_state.SEQUENCE_ELEMENT:
        if (not contract.is_sequence or contract.element is None
                or type(value) not in (list, tuple)):
            raise ValueError("a nested dynamic length has no sequence parent")
        child_shape = next(
            (shape[1:] for candidate, shape, _capacity
             in dynamic_length_specs(contract)
             if candidate == path), ())
        rows = [
            native_length_at(item, contract.element, tail)
            for item in value
        ]
        rows.extend(
            zero_native_shape(child_shape)
            for _ in range(contract.maximum - len(rows)))
        return rows
    if head == ("payload",):
        if not contract.is_option or contract.payload is None:
            raise ValueError("an optional length has no payload")
        return native_length_at(value, contract.payload, tail)
    if isinstance(head, tuple) and len(head) == 2 and head[0] == "field":
        if not contract.is_record or type(value) is not dict \
                or head[1] not in value:
            raise ValueError("a record length has no matching field")
        return native_length_at(
            value[head[1]], contract.field_contracts[head[1]], tail)
    raise ValueError("unknown dynamic-length path")



def variable_reads(body):
    """Read names from semantic expressions, including dynamic loop counts.

    The IR owns command operands and visits shared expression objects once;
    ``For.bound`` is only a literal diagnostic, not the count expression.
    """
    return {node.name for node in ir.expression_nodes(body) if isinstance(node, ir.Var)}


def bind_source_state(proc, low, prepared, declarations, planned, contexts):
    """Bind every logical field after entry, type and alias checks have passed."""
    model = prepared.model
    fn = model.step
    source_state = declarations.source_state
    state_names = declarations.state_names
    dynamic_state = declarations.dynamic_state
    construction_values = declarations.construction_values
    declaration_contracts = planned.declaration_contracts
    active_structured = contexts.active_structured
    active_products = contexts.active_products
    absent_state = contexts.absent_state
    bound_state_variables = {
        variable
        for binding in (*active_structured.values(), *active_products.values())
        for variable in binding.solver_variables
    }
    invented = sorted(
        set(proc.state) - set(state_names.values()) - bound_state_variables
        - set(low.storage.state_length_variables) - set(low.storage.state_presence.values()))
    if invented:
        raise LoweringError(
            model.cls,
            f"the lowering holds state the source never writes: {invented[:4]}",
            code="state-outside-the-source-contract")
    # **Every contract field has an explicit representation** (review 20): IR
    # state, or a constant recorded from `reset`. Refusing only invented state
    # left deletion open -- a step-written field missing from `proc.state`
    # slipped through, and the audit's implicit initial-value fallback then
    # passed it whenever the native value happened to sit at its initial. A
    # step-written field must be state; a reset-only field is a named constant;
    # a field with neither is refused, not defaulted.
    step_writes = set(model.step_state_contract()) | set(dynamic_state)
    # `proc.initial` is filtered to the step's own state; a reset-only field's
    # constant lives in the unfiltered fold of `reset`.
    reset_fold = construction_values
    representation = {}
    for field in source_state:
        if (state_names[field] in proc.state or field in active_structured
                or field in active_products or field in absent_state):
            representation[field] = ("state",)
        elif field in step_writes:
            raise LoweringError(
                model.cls,
                f"the step writes state {field!r} and the lowering does not "
                f"carry it",
                code="state-missing-from-the-lowering")
        elif field in reset_fold:
            representation[field] = ("constant", reset_fold[field])
        else:
            # Written only on the reset/__init__ closure with a value the fold
            # cannot read (a constructor parameter, typically): claimed folded
            # into the program -- and the claim is **checked below**, not
            # assumed.
            representation[field] = ("folded",)
    # **A non-state field must really be folded.** "Folded" claimed the value
    # was baked into the program; review 21 built a helper that reads `self.k`
    # and got IR reading an undeclared variable `k` while the map said
    # ("folded",) -- well-formedness caught it later, but a classification that
    # is not checked where it is made is a claim. Any contract field the body
    # still reads by name without carrying it as state is refused here, typed.
    carried = set(proc.state) | set(proc.inputs) | set(proc.locals)
    body_reads = variable_reads(proc.body)
    leaked = sorted(f for f in source_state
                    if state_names[f] not in proc.state and f not in active_structured
                    and f not in active_products
                    and state_names[f] in body_reads
                    and state_names[f] not in carried)
    if leaked:
        raise LoweringError(
            model.cls,
            f"the body reads state it does not carry, so nothing represents "
            f"it: {leaked[:4]}",
            code="state-read-but-not-carried")
    projections = list(active_products.values())
    projections.extend(StateAbsentBinding(name) for name in sorted(absent_state))
    for name in sorted(source_state):
        physical = state_names[name]
        image = low.storage.length_images.get(physical, {})
        state_lengths = {
            path: holder for path, holder in image.items()
            if holder in low.storage.state_length_variables
        }
        if not state_lengths and not (
                physical in proc.state and contains_byte_sequence(
                    declaration_contracts.get(name, UNKNOWN))):
            if (physical != name and physical in proc.state
                    and name not in active_structured):
                projections.append(StateVariableBinding(name, physical))
            continue
        by_depth = []
        for path, holder in state_lengths.items():
            if any(part is not lower_state.SEQUENCE_ELEMENT for part in path):
                raise LoweringError(
                    fn, f"the dynamic source state {name!r} uses a logical "
                        "path with no recursive sequence projection",
                    code="state-projection-unsupported")
            by_depth.append((len(path), holder))
        try:
            projection = DynamicSequenceBinding(
                name, physical, declaration_contracts.get(name, UNKNOWN),
                tuple(by_depth))
        except StateProjectionError as exc:
            raise LoweringError(
                fn, f"the dynamic source state {name!r} has no complete "
                    f"logical projection: {exc}",
                code="state-projection-incomplete") from None
        absent = sorted(set(projection.solver_variables) - set(proc.state))
        if absent:
            raise LoweringError(
                fn, f"the dynamic source state {name!r} binds undeclared "
                    f"SolverIR state {absent!r}",
                code="state-projection-variable-undeclared")
        projections.append(projection)
    by_name = {binding.name: binding for binding in projections}
    for name, present in low.storage.state_presence.items():
        binding = by_name.get(name, StateVariableBinding(name, state_names[name]))
        by_name[name] = StatePresenceBinding(binding, present)
    proc.contract = ir.StateContract(
        source_state, representation, tuple(by_name.values()))
