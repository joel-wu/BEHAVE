"""Prepare Behavior source and bounded representation facts for lowering.

``prepare_source`` fixes the entry, interface, goals and construction evidence.
``plan_representation`` derives finite sequence capacities and entry ranges.
``PreparationCache`` belongs to one lowering invocation, not a persistent store.
``prepare_lowering`` composes these phases before IR emission.
All phases spend the caller's budget without creating IR commands or resetting
the allowance. Results retain the original evidence and source occurrences.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from . import lower_state, source_intrinsics
from ..symbolic import ranges
from ..symbolic import typecheck as types_mod
from ..symbolic.capabilities import SCALAR_BITS
from ..symbolic.capabilities import scalar_magnitude_bits
from .construction import ConstructionFacts, ConstructionUnavailable, construction_facts
from .extract import (
    LoweringError,
    ScopeResolver,
    SourceModel,
    _output_names,
    _walk_function_scope,
    source_model,
)
from .method_binding import method_kind, method_parameters
from .source_sequences import SequencePlan, SequencePlanError, plan_sequences
from ..symbolic.value_contract import ValueContract
from .source_values import SourceValueBudgetExceeded

if TYPE_CHECKING:
    from .source_plan import SourcePlan
    from ..symbolic.value_binding import ValueBinding


@dataclass(frozen=True, slots=True)
class PreparedSource:
    """One admitted source context; members retain their existing identities."""

    model: SourceModel
    input_widths: tuple[int, ...] | None
    outputs: Sequence[str] | None
    signature_input_bindings: dict[str, ValueBinding]
    signature_output_bindings: dict[str, ValueBinding]
    resolver: ScopeResolver
    goal_plan: SourcePlan
    constants: dict
    declaration_owner: Callable[[ast.AST], object]
    construction: ConstructionFacts


_FORBIDDEN_REFLECTION_ATTRIBUTES = frozenset({
    "__globals__", "__builtins__", "__code__", "__defaults__",
    "__kwdefaults__", "__closure__", "__dict__", "__class__",
    "__mro__", "__bases__", "__base__", "__func__", "__self__",
    "__subclasses__", "__setattr__", "__getattribute__", "__getattr__",
    "__delattr__", "__reduce__", "__reduce_ex__", "__init_subclass__",
    "__set_name__",
})


def prepare_source(source, file, method, *, outputs, input_widths, signature,
                   construction, _analysis_cache, cls, operation_goals,
                   source_rules, source_value_budget) -> PreparedSource:
    """Prepare one pass with the caller-owned cache and aggregate budget."""
    # Which class and which method, decided in one place and refused where the contract
    # does not cover it, rather than by a walk that took the first match while Python
    # binds the last. `certified_subset` is asked separately: a model that steps once
    # per clock domain is read here and not certified yet, which is the shape six of the
    # population have and the shape the event-slot instantiation will want.
    try:
        if _analysis_cache is None:
            model = source_model(
                source, file, step=method, cls=cls,
                source_value_budget=source_value_budget)
        else:
            model = _analysis_cache.source_model(
                source, file, method, cls, source_value_budget)
    except SourceValueBudgetExceeded as exc:
        raise LoweringError(
            exc.node, "source representation planning exceeded its aggregate "
                      "work budget",
            code="source-plan-budget-exceeded") from None
    outside = model.certified_subset()
    if outside:
        raise LoweringError(model.cls, outside[0], code="multi-clock-not-instantiated")
    for helper in (*model.steps.values(), *model.helpers.values()):
        if method_kind(helper).startswith("property"):
            raise LoweringError(
                helper, "properties execute and emit native goals, but exact lowering "
                        "does not yet represent descriptor reads and writes",
                code="property-lowering-unsupported")
        if method_kind(helper) == "classmethod":
            raise LoweringError(
                helper, "classmethods execute concretely, but the exact "
                        "frontend has no class-object binding representation yet",
                code="class-method-lowering-unsupported")
    tree, fn = model.tree, model.step
    input_widths = validate_input_widths(fn, input_widths)
    signature_input_bindings = {}
    signature_output_bindings = {}
    if signature is not None:
        params = method_parameters(fn)
        missing = tuple(name for name in params
                        if name not in signature.input_names)
        extra = tuple(name for name in signature.input_names
                      if name not in params)
        if missing or extra:
            raise LoweringError(
                fn,
                "the process inputs do not match the task signature "
                f"(missing={missing}, extra={extra})",
                code="signature-inputs-mismatch")
        declared_outputs = tuple(signature.output_names)
        if outputs is not None and tuple(outputs) != declared_outputs:
            raise LoweringError(
                fn,
                "the requested outputs do not match the task signature "
                f"({tuple(outputs)!r} against {declared_outputs!r})",
                code="signature-outputs-mismatch")
        outputs = list(declared_outputs)
        signature_input_bindings = {
            binding.name: binding for binding in signature.input_bindings
        }
        signature_output_bindings = {
            binding.name: binding for binding in signature.output_bindings
        }
    # One declaration authority, constructed before any consumer.  The flat
    # dictionary remains only for legacy expression helpers; every occurrence
    # that can be ambiguous is resolved by ``resolver`` below.
    try:
        resolver = ScopeResolver(
            model, source_value_budget=source_value_budget,
            _library_trees=(None if _analysis_cache is None
                            else _analysis_cache.library_trees))
        from .source_plan import plan_source

        goal_plan = plan_source(model, source, file, resolver=resolver,
                                source_rules=source_rules)
        if operation_goals:
            goal_plan = goal_plan.with_operation_goals(operation_goals)
        if _analysis_cache is not None:
            _analysis_cache.declare_goals(goal_plan.manifest, model.step)
    except SourceValueBudgetExceeded as exc:
        raise LoweringError(
            exc.node, "source representation planning exceeded its aggregate "
                      "work budget",
            code="source-plan-budget-exceeded") from None
    # Static subset failures precede the executable witness.  Besides giving
    # the precise reason, this avoids spawning an isolated construction for a
    # source that is already outside the certified language.
    _reject_source_environment_access(tree)
    invalid_table = _first_invalid_table_read(model, resolver)
    if invalid_table is not None:
        raise LoweringError(
            invalid_table,
            f"the module table {invalid_table.id} is not a closed read-only "
            "binding",
            code="module-table-not-static")
    constants = dict(resolver.lib_consts)
    constants.update(resolver.model_consts)
    constants.update(resolver.self_consts)
    # One occurrence-aware constant authority serves value lowering and every
    # declaration-time structural query.  A bare name resolves in its module,
    # ``self.X`` in the chosen class, and a constructor parameter in that
    # constructor rather than in a coincidentally same-named outer scope.
    _decl_owner = resolver.occurrence_owner()

    # Construction is executable source semantics.  Assignment syntax can
    # describe a value that is never created (for example, a field assigned
    # only in an untaken branch), so it cannot establish the step boundary.
    # The same isolated, repeatable record owns the entry values here and the
    # object-sharing check performed after representation types are known.
    if construction is None:
        construction_key = (source, file, model.cls.name)
        if (_analysis_cache is not None
                and _analysis_cache.construction_key == construction_key):
            construction = _analysis_cache.construction
        else:
            try:
                construction = construction_facts(
                    source, file, cls_name=model.cls.name)
            except ConstructionUnavailable as exc:
                raise LoweringError(
                    model.cls,
                    "the construction could not be run and witnessed, so no "
                    f"Behavior step boundary is established ({exc})",
                    code="state-sharing-unverified") from None
            if _analysis_cache is not None:
                _analysis_cache.construction_key = construction_key
                _analysis_cache.construction = construction
    elif type(construction) is not ConstructionFacts:
        raise TypeError("construction must be exact ConstructionFacts")
    return PreparedSource(
        model=model,
        input_widths=input_widths,
        outputs=outputs,
        signature_input_bindings=signature_input_bindings,
        signature_output_bindings=signature_output_bindings,
        resolver=resolver,
        goal_plan=goal_plan,
        constants=constants,
        declaration_owner=_decl_owner,
        construction=construction)


def _reject_source_environment_access(tree):
    """Refuse syntax outside the closed source execution environment."""
    from .source_syntax import future_annotations_import

    for node in ast.walk(tree):
        if future_annotations_import(node):
            continue
        if isinstance(node, ast.ImportFrom) and (
                node.level or
                node.module not in source_intrinsics.APPROVED_MODULES):
            name = ("a relative import" if node.level else
                    repr(node.module))
            raise LoweringError(
                node,
                f"{name} is outside the approved source-module boundary",
                code="source-import-unapproved")
        if isinstance(node, ast.Import):
            unapproved = next(
                (alias.name for alias in node.names
                 if alias.name not in source_intrinsics.APPROVED_MODULES),
                None,
            )
            if unapproved is not None:
                raise LoweringError(
                    node,
                    f"{unapproved!r} is outside the approved source-module "
                    "boundary",
                    code="source-import-unapproved")
        if isinstance(node, ast.Attribute) \
                and node.attr in _FORBIDDEN_REFLECTION_ATTRIBUTES:
            raise LoweringError(
                node,
                f"the source reaches {node.attr}, an attribute of the "
                "execution machinery itself",
                code="reflection-attribute-forbidden")


def _first_invalid_table_read(model, resolver):
    """The first reachable read of a disqualified module collection.

    The construction witness freezes the module and therefore fails as soon
    as reset mutates such a collection.  Resolve the source declaration first
    so that the failure names the violated read-only table contract instead of
    being reported later as a generic construction failure.
    """
    queue, seen = [model.step], set()
    while queue:
        owner = queue.pop()
        if id(owner) in seen:
            continue
        seen.add(id(owner))
        for node in _walk_function_scope(owner):
            if (isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id in resolver.invalid_tables
                    and resolver.resolves_model_module(owner, node.id)):
                return node
            if not isinstance(node, ast.Call):
                continue
            target = None
            if isinstance(node.func, ast.Name):
                kind, target = resolver.resolve_call(owner, node.func.id)
                if kind not in {"nested", "module", "alias"}:
                    target = None
            elif (isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == "self"):
                kind, target = resolver.resolve_method(owner, node.func.attr)
                if kind != "method":
                    target = None
            if target is not None:
                queue.append(target)
    return None


def _validate_scalar_width(width, where, what="an input"):
    """One Behavior scalar width, before any shift or range is constructed."""
    if type(width) is not int or width < 1:
        raise LoweringError(
            where, f"{what} width is {width!r}, not a positive integer",
            code="input-width-invalid")
    if width > SCALAR_BITS:
        raise LoweringError(
            where, f"{what} width is {width}, over the reported scalar "
                   f"capability of {SCALAR_BITS} bits",
            code="input-width-exceeds-capability")
    return width


def validate_input_widths(fn, input_widths):
    """Validate the optional positional width contract without ``zip`` loss."""
    if input_widths is None:
        return None
    widths = tuple(input_widths)
    params = method_parameters(fn)
    if len(widths) != len(params):
        raise LoweringError(
            fn, f"the step has {len(params)} inputs but was given "
                f"{len(widths)} widths",
            code="input-width-count")
    return tuple(_validate_scalar_width(width, fn, f"input {name}")
                 for name, width in zip(params, widths))


def validate_input_bounds(fn, input_bounds):
    """Validate a complete named input contract before range construction."""
    if input_bounds is None:
        return None
    try:
        bounds = dict(input_bounds)
    except (TypeError, ValueError):
        raise LoweringError(
            fn, "input bounds are not a name-to-pair mapping",
            code="input-bound-invalid") from None
    params = method_parameters(fn)
    if set(bounds) != set(params):
        missing = sorted(set(params) - set(bounds))
        extra = sorted(set(bounds) - set(params))
        raise LoweringError(
            fn, f"input bounds do not name exactly the step inputs "
                f"(missing {missing}, extra {extra})",
            code="input-bound-names")
    out = {}
    for name in params:
        pair = bounds[name]
        if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                or type(pair[0]) is not int or type(pair[1]) is not int
                or pair[1] < pair[0]):
            raise LoweringError(
                fn, f"input {name} has invalid bounds {pair!r}",
                code="input-bound-invalid")
        lo, hi = pair
        magnitude = scalar_magnitude_bits(lo, hi)
        if magnitude > SCALAR_BITS:
            raise LoweringError(
                fn, f"input {name} needs {magnitude} magnitude bits, over the "
                    f"reported scalar capability of {SCALAR_BITS}",
                code="input-bound-exceeds-capability")
        out[name] = (lo, hi)
    return out


class PreparationCache:
    """Immutable frontend results shared by the two lowering passes.

    This cache lives for one :func:`lower_source` call. A row is
    reused only under its complete invocation-local request key; it is neither
    persistent nor a substitute for a task or artifact identity.  In particular,
    the authenticated construction is one authority consumed by both passes,
    rather than two independently acquired snapshots of executable source.
    Model and dependency ASTs retain occurrence identity; each pass still gets
    a fresh scope resolver and its own domain-dependent IR lowering.
    """

    __slots__ = (
        "_model", "_sequence_plan", "_sequence_request", "_source_key",
        "alias_results", "construction", "construction_key", "goal_manifest", "library_trees")

    def __init__(self):
        self.alias_results = {}
        self.construction = None
        self.construction_key = None
        self.goal_manifest = None
        self.library_trees = {}
        self._source_key = None
        self._model = None
        self._sequence_request = None
        self._sequence_plan = None

    def source_model(self, source, file, method, cls, budget):
        # SequencePlan keys are source occurrences, not just line numbers. Both
        # passes must use this same read-only AST to consume one retained plan.
        key = (source, file, method, cls)
        if key != self._source_key:
            model = source_model(source, file, step=method, cls=cls,
                                 source_value_budget=budget)
            self._source_key, self._model = key, model
        return self._model

    def plan_sequences(self, model, resolver, outputs, *, dynamic_state, budget, **facts):
        # Resolver is fresh for each pass: its declaration-time reset constants
        # must not inherit the previous pass's arbitrary-state folding policy.
        # All other resolver facts follow from the same source model. The full
        # request includes the external dynamic-state policy as well as ranges,
        # construction/shape facts, typed inputs/outputs and the actual bound.
        request = (model, tuple(outputs), frozenset(dynamic_state), facts)
        if request != self._sequence_request:
            plan = plan_sequences(model, resolver, outputs, budget=budget, **facts)
            self._sequence_request, self._sequence_plan = request, plan
        return self._sequence_plan

    def declare_goals(self, manifest, node):
        """Keep the pre-lowering declaration across both range passes."""
        if self.goal_manifest is None:
            self.goal_manifest = manifest
        elif self.goal_manifest != manifest:
            raise LoweringError(
                node, "range planning changed the source goal declaration",
                code="goals-declaration-changed")


@dataclass(frozen=True, slots=True)
class PlannedRepresentation:
    """One pass's occurrence-bound plan and resolved lowering entry facts."""

    params: list[str]
    sequence_plan: SequencePlan
    boundary_contracts: dict[str, ValueContract]
    declaration_contracts: dict[str, ValueContract]
    boundary_arrays: dict[str, types_mod.Array]
    model_arrays: Mapping[str, tuple[int, ...]]
    outputs: list[str]
    env: dict[str, ranges.Range]


@dataclass(frozen=True, slots=True)
class PreparedLowering:
    """One pass's completed preparation, handed to the IR emitter.

    This is not an execution context: it owns no worker, solver, cache or budget.
    Source occurrences and construction evidence retain their identities. The
    pass-local resolver is prepared here; branch facts and physical allocation
    belong to the emitter. Derived layouts never write back into declarations.
    """

    source: PreparedSource
    declarations: lower_state.StateDeclarations
    representation: PlannedRepresentation
    fixed: Mapping[str, int | bool]
    static_fields: Mapping[str, object]
    reserved_names: frozenset[str]


def prepare_lowering(source, file, method, *, outputs, input_widths, state_ranges,
                     read_initial, signature, value_types, dynamic_state, bound,
                     boundary_initial, construction, _analysis_cache, cls,
                     operation_goals, source_rules, source_value_budget) -> PreparedLowering:
    """Finish source/declaration/planning work with the caller's one budget."""
    prepared = prepare_source(
        source, file, method, outputs=outputs, input_widths=input_widths,
        signature=signature, construction=construction, _analysis_cache=_analysis_cache,
        cls=cls, operation_goals=operation_goals, source_rules=source_rules,
        source_value_budget=source_value_budget)
    model = prepared.model
    fn = model.step
    signature_input_bindings = prepared.signature_input_bindings
    resolver = prepared.resolver
    constants, _decl_owner = prepared.constants, prepared.declaration_owner
    construction = prepared.construction

    declarations = lower_state.declare_state(
        model, fn, constants, resolver, _decl_owner, construction,
        signature_input_bindings=signature_input_bindings,
        dynamic_state=dynamic_state, boundary_initial=boundary_initial,
        value_types=value_types, read_initial=read_initial,
        source_value_budget=source_value_budget)
    state_names = declarations.state_names
    dynamic_state = declarations.dynamic_state
    construction_values = declarations.construction_values
    declared_value_types = declarations.declared_value_types
    ir_initial = declarations.ir_initial
    step_writes = declarations.step_writes

    # Alias validation belongs after emission, when evaluation order and final
    # physical types are known. Preparation only derives entry facts.
    fixed = dict(resolver.class_consts)
    fixed.update({name: value
                  for name, value in construction_values.items()
                  if type(value) in (bool, int) and name not in step_writes})
    # The declaration pass above legitimately used the reset value to fix
    # shapes and carriers.  From this point onward we lower an arbitrary step:
    # state owned by another component ranges over its closed envelope and
    # must not be folded back to that reset value.
    resolver.mark_externally_dynamic_state(dynamic_state)
    from .source_rom import freeze_value

    static_fields = {}
    field_values = dict(resolver.class_tables)
    field_values.update({name: value for name, value in construction_values.items()
                         if (type(value) is dict
                             or (type(value) in (list, tuple)
                                 and name not in ir_initial))
                         and name not in step_writes
                         and name not in declared_value_types})
    # Reset-only label tables have exact source values but no numeric-array
    # image. Use the same frozen selector as closed class/module tables; keep
    # ordinary numeric arrays on their existing state representation.
    for name, value in field_values.items():
        frozen = freeze_value(value)
        if frozen is not None:
            static_fields[name] = frozen.to_plain(budget=source_value_budget, node=fn)
    resolver.static_field_values = {name: field_values[name] for name in static_fields}
    planned = plan_representation(
        prepared, declarations, state_ranges=state_ranges, bound=bound,
        _analysis_cache=_analysis_cache, source_value_budget=source_value_budget)
    params = planned.params
    outputs = planned.outputs
    # Source identifiers are not a reserved compiler namespace. Freeze the
    # occupied names before entry/output allocation, including not-yet-written
    # locals and task-owned physical leaves that have no source Name node.
    reserved_names = {part.id for part in ast.walk(fn) if isinstance(part, ast.Name)}
    reserved_names.update((*params, *outputs, *state_names.values()))
    for binding in (*prepared.signature_input_bindings.values(),
                    *prepared.signature_output_bindings.values()):
        reserved_names.update(binding.solver_variables)
    return PreparedLowering(
        prepared, declarations, planned,
        MappingProxyType(fixed), MappingProxyType(static_fields),
        frozenset(reserved_names))


def plan_representation(prepared: PreparedSource, declarations: lower_state.StateDeclarations, *,
                        state_ranges, bound, _analysis_cache,
                        source_value_budget) -> PlannedRepresentation:
    """Plan capacities and entry ranges without confusing S_0 with later state."""
    model = prepared.model
    fn = model.step
    resolver = prepared.resolver
    outputs = prepared.outputs
    input_widths = prepared.input_widths
    signature_input_bindings = prepared.signature_input_bindings
    signature_output_bindings = prepared.signature_output_bindings
    constants = prepared.constants
    source_state = declarations.source_state
    state_names = declarations.state_names
    input_variables = declarations.input_variables
    construction_values = declarations.construction_values
    step_writes = declarations.step_writes
    dynamic_state = declarations.dynamic_state
    declaration_contracts = declarations.declaration_contracts
    declared_value_types = declarations.declared_value_types
    model_arrays = dict(declarations.model_arrays)
    # Source representation planning is part of this task-domain lowering,
    # not a syntax-only prepass.  Give it the same finite input/state facts
    # that the retained lowerer consumes so a while-built sequence is sized
    # from the declared domain rather than from a frontend iteration guess.
    params = list(method_parameters(fn))
    task_ranges = {}
    for name, width in zip(params, input_widths or []):
        task_ranges[name] = ranges.Range(0, (1 << width) - 1)
    task_ranges.update({
        state_names[name]: ranges.Range(value, value)
        for name, value in construction_values.items()
        if (name in source_state and name not in step_writes
            and type(value) is int)
    })
    boundary_names = input_variables | set(state_names.values())
    task_ranges.update({
        name: value for name, value in (state_ranges or {}).items()
        if name in boundary_names
    })
    # Mutable scalar state has an exact value only at S_0.  Source planning
    # closes that seed through the bounded transition before using it to size
    # loops; the retained lowering must never mistake it for the value at
    # every step.  Keep the seed on the planning side of this boundary and
    # expose only the closed ``state_ranges`` above to executable lowering.
    # A StatePlan range is the join of values reachable anywhere in the
    # horizon.  It is an invariant available to executable lowering, not the
    # source value at S_0.  Seeding source closure with that join asks every
    # invocation to start in every later state at once.  Besides losing
    # precision, that can make a terminating counter look as though it starts
    # past its ``while`` bound.  Source closure has its own transition
    # interpreter below: start it from the witnessed construction value and
    # let it derive S_1, ..., S_N itself.
    sequence_ranges = {
        name: task_ranges[name]
        for name in params if name in task_ranges
    }
    if bound is not None:
        # A finite source-plan closure starts at S_0, so construction values
        # are its exact seed.  With no horizon there is no such closure here:
        # a mutable field missing from the fixpoint is unknown, not forever
        # equal to its reset value.  Reintroducing that value would prune
        # outputs and operations that become reachable only on a later step.
        sequence_ranges.update({
            f"self.{name}": interval
            for name, value in construction_values.items()
            if name in source_state
            if name not in dynamic_state
            if (interval := ranges.range_of_value(value)) is not None
        })
    # A selected handler in a multi-clock product is different: another
    # handler may change ``dynamic_state`` between two of its activations, so
    # construction is not that handler's complete entry set.  Those fields
    # deliberately take the externally closed envelope.
    sequence_ranges.update({
        f"self.{name}": state_ranges[state_names[name]]
        for name in dynamic_state
        if state_names[name] in (state_ranges or {})
    })
    # Sequence planning consumes the same construction facts as value
    # lowering.  In particular, a constructor-default dimension is known
    # before capacities and helper return contracts are chosen; planning it
    # earlier forced the generic unrolling ceiling into otherwise fixed shapes.
    sequence_request = {
        # Construction fixes representation, but only fields unwritten by the
        # step are values of an arbitrary invocation. Mutable state is seeded
        # and closed through sequence_ranges, not folded to its reset value.
        "declarations": {name: value for name, value in construction_values.items()
                         if name not in step_writes},
        "declaration_contracts": declaration_contracts,
        "input_contracts": {name: lower_state.source_contract_of_value_type(binding.value_type)
                            for name, binding in signature_input_bindings.items()},
        "declared_output_contracts": {
            name: lower_state.source_contract_of_value_type(binding.value_type)
            for name, binding in signature_output_bindings.items()},
        "input_ranges": sequence_ranges, "state_bound": bound}
    try:
        if _analysis_cache is None:
            sequence_plan = plan_sequences(
                model, resolver, outputs or (), budget=source_value_budget,
                **sequence_request)
        else:
            sequence_plan = _analysis_cache.plan_sequences(
                model, resolver, outputs or (), dynamic_state=dynamic_state,
                budget=source_value_budget, **sequence_request)
    except SequencePlanError as exc:
        raise LoweringError(exc.node, exc.what, code=exc.code) from None
    boundary_contracts = sequence_plan.state_contracts
    declaration_contracts = sequence_plan.state_storage_contracts
    outside_source = sorted(set(declaration_contracts) - set(source_state))
    if outside_source:
        raise LoweringError(
            model.cls,
            f"the source plan holds state outside the source contract: {outside_source[:4]}",
            code="state-outside-the-source-contract")
    for name, contract in declaration_contracts.items():
        if (name not in declared_value_types
                and contract.physical_shape is not None):
            model_arrays[name] = contract.physical_shape
    boundary_arrays = {
        name: types_mod.of_shape(contract.physical_shape)
        for name, contract in declaration_contracts.items()
        if contract.physical_shape
    }
    for name, contract in declaration_contracts.items():
        if contract.is_option and contract.payload.physical_shape:
            boundary_arrays[name] = types_mod.of_shape(contract.payload.physical_shape)
    outputs = (list(outputs) if outputs else
               list(sequence_plan.output_fields) or _output_names(fn))
    env = {name: ranges.Range(value, value) for name, value in constants.items()}
    env.update(task_ranges)
    # The source planner has already closed the state/input envelope for this
    # exact bounded root activation.  Install that envelope in the retained
    # lowering as well.  Otherwise it can lower a branch that no state in the
    # plan can enter, then ask the plan for helper/batch facts that correctly
    # do not exist on that unreachable branch.
    root_ranges = sequence_plan.initial_ranges(
        fn, sequence_plan.root_context)
    for source_name, interval in root_ranges.items():
        if source_name.startswith("self."):
            name = source_name[len("self."):]
            if name not in source_state:
                continue
            name = state_names[name]
        elif source_name in params:
            name = source_name
        else:
            continue
        current = env.get(name)
        if current is not None:
            lo = max(current.lo, interval.lo)
            hi = min(current.hi, interval.hi)
            if lo > hi:
                raise LoweringError(
                    fn, f"the source plan range for {source_name!r} does not "
                        "overlap the retained lowering range",
                    code="source-range-plan-inconsistent")
            interval = ranges.Range(lo, hi)
        env[name] = interval
    return PlannedRepresentation(
        params=params,
        sequence_plan=sequence_plan,
        boundary_contracts=boundary_contracts,
        declaration_contracts=declaration_contracts,
        boundary_arrays=boundary_arrays,
        model_arrays=MappingProxyType(model_arrays),
        outputs=outputs,
        env=env)
