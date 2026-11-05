"""Contracts checked at the source-to-IR boundary.

Lowering owns construction and calls these checks at their required phases.
This module validates goal accounting, entry representation and sharing; it
does not choose a lowerer, parse source, allocate a budget or decide a score.
Entry finalization may fill only the state values justified by its type and
definite-assignment proofs. Checks must not be deferred to optional analysis.
"""

from __future__ import annotations

from . import entry_state, lower_context, step_alias
from ..symbolic import ir, primitives
from ..symbolic import typecheck as types_mod
from ..symbolic.capabilities import SCALAR_BITS
from ..symbolic.capabilities import scalar_magnitude_bits
from .extract import LoweringError
from .goals import goal_id
from ..symbolic.value_contract import contract_contains
from .source_values import SourceValueBudgetExceeded


def check_goals(fn, goal_plan, low, body):
    """Require complete coverage/value/safety producers and registered IR operations."""
    _check_producers(fn, goal_plan.coverage_sites, body)
    missing_value_goals = {
        binding.id for binding in (*goal_plan.operation_goals, *goal_plan.read_goals)
    } - ir.goals_of(body)
    if missing_value_goals:
        raise LoweringError(fn, "a declared source value goal has no evaluation-edge producer",
                            code="source-value-goals-unaccounted")
    # Source-only operations must be eliminated here, not handed to an encoder.
    stray = ir.unlowered(body)
    if stray:
        op, expr = stray[0]
        kind = ("a source-level operation the frontend did not eliminate"
                if op in primitives.SOURCE_OPERATIONS else
                "an operation no layer declares")
        raise LoweringError(
            fn, f"{len(stray)} operations are not SolverIR primitives, the first "
                f"being {op}, {kind}: {expr!r:.60}", code="primitive-unregistered")
    (missing, undeclared), discharged = safety_audit(goal_plan.safety_sites, low, body)
    if missing:
        raise LoweringError(
            fn, f"{len(missing)} declared safety goals are neither produced nor "
                f"discharged, the first being {missing[0]}", code="safety-goals-unaccounted")
    if undeclared:
        raise LoweringError(
            fn, f"{len(undeclared)} safety goals were produced that the source "
                f"declares nothing for, the first being {undeclared[0]}", code="safety-goals-undeclared")
    return discharged


def finalize_entry_state(proc, model, low, *, fn, initial, active_products,
                         state_names, inherited):
    """Infer types, materialize justified entry values and reject unsafe reads."""
    # Representation types belong to the procedure, not a backend's inference.
    try:
        proc.types, type_faults = types_mod.of_procedure(proc)
    except Exception as exc:                                  # noqa: BLE001
        raise LoweringError(
            fn, f"the lowered procedure's representation types cannot be "
                f"derived: {type(exc).__name__}: {exc}",
            code="type-analysis-failed") from None
    if type_faults:
        raise LoweringError(
            fn, f"the lowered procedure has inconsistent representation "
                f"types: {type_faults[0]}",
            code="type-inconsistent")

    # Late attributes have a separate existence bit. Their inactive physical
    # payload comes from the inferred type and cannot escape a guarded read.
    # The existing CFG entry-dead proof is still used for other hidden entry
    # values; a missing untyped payload is never manufactured from zero.
    entry = entry_state.plan(proc, set(proc.state) - set(initial))
    presence_payloads = {
        variable
        for name in low.storage.state_presence
        for variable in (active_products[name].solver_variables
                         if name in active_products else (state_names[name],))
    }
    needed_fallbacks = {
        name for name in inherited
        if not set(active_products[name].solver_variables
                   if name in active_products else (state_names[name],))
        <= set(entry.entry_dead)
    }
    if needed_fallbacks:
        raise LoweringError(
            model.cls,
            f"the late field {min(needed_fallbacks)!r} has a class lookup fallback; "
            "instance absence alone does not represent that attribute read",
            code="state-class-fallback-unsupported")
    unrepresented = set(entry.unsafe) - presence_payloads
    if unrepresented:
        first = min(unrepresented)
        raise LoweringError(
            model.cls,
            f"the state {first!r} is absent after construction and a normal "
            "step can observe that absence or complete without defining it",
            code="state-uninitialized-at-entry")
    if entry.untyped:
        first = entry.untyped[0]
        raise LoweringError(
            model.cls,
            f"the entry-dead state {first!r} has no inferred SolverIR type "
            "from which to construct its representation value",
            code="state-type-unresolved")
    proc.initial.update(dict(entry.values))
    proc.entry_dead = entry.entry_dead
    for name in sorted(presence_payloads - set(proc.initial)):
        value = entry_state._canonical(proc.types.get(name))
        if value is None:
            raise LoweringError(model.cls, f"the absent field payload {name!r} has no type",
                                code="state-type-unresolved")
        proc.initial[name] = value
    if presence_payloads:
        # Physical payloads now exist, but only the guarded source read can
        # expose one. Recheck ordinary locals from that exact entry context.
        entry = entry_state.plan(proc, set(proc.state) - set(proc.initial))

    # All source paths that retain an integer meet here, after construction
    # values and entry-dead fillers have joined the procedure but before any
    # range or solver consumer can allocate a term for them.
    _enforce_scalar_capability(proc, fn)

    # Check definitions on the production CFG, which preserves source paths.
    # Reconstructing a graph from the secondary predicated-term oracle would
    # lose return-path relations and falsely reject legal source.
    if proc.graph is not None:
        # Reuse the entry plan's graph analysis and exact initialized start set.
        undefined = entry.undefined_uses
        if undefined:
            raise LoweringError(
                fn, f"the lowered procedure may read a variable before every "
                    f"path defines it: {undefined[0]}",
                code="variable-may-be-unassigned")


def check_array_state_types(proc, model):
    """Require an executable type for every source state used as an array."""
    # Step aliasing and authenticated construction sharing are separate checks.
    if proc.types:
        used_as_array = {c.var for c in ir.walk(proc.body)
                         if isinstance(c, ir.Store)}
        used_as_array.update(
            node.array.name for node in ir.expression_nodes(proc.body)
            if isinstance(node, ir.Sel) and isinstance(node.array, ir.Var))
        for name in proc.state:
            if name in used_as_array and name not in proc.types:
                raise LoweringError(
                    model.cls,
                    f"the state {name!r} is used as an array whose shape "
                    f"the type pass cannot carry, so no backend can "
                    f"execute it",
                    code="state-type-unresolved")


def check_value_lengths(source, contract, image, *, record=False):
    """Require the logical-length carriers declared by a recursive value."""
    required = {path for path, _shape, _capacity
                in lower_context.dynamic_length_specs(contract)}
    if required - set(image):
        detail = ("a record value is missing one of its nested logical-length carriers"
                  if record else
                  "a bounded recursive value is missing one of its logical-length carriers")
        raise LoweringError(source, detail, code="nested-dynamic-sequence-unsupported")


def check_value_storage(source, contract, *, shape, image, storage, shape_of):
    """Validate backing storage before accepting logical-length metadata.

    ``shape_of`` is the caller's read-only IR shape lookup, used lazily only
    for recursive padding. This check receives no lowerer, emits no IR and
    does not resolve source expressions, allocate storage or change contracts.
    """
    # A dynamic sequence still has a fixed *physical* representation: a
    # bounded array plus its separate logical length.  Checking only
    # ``fixed_shape`` left that representation unaudited, because a
    # dynamic sequence has no single logical value shape.  A faulty
    # lowering could therefore return a scalar for a source list and be
    # accepted as long as the source length varied.  Reconcile the storage
    # shape instead; for scalars and fixed sequences it is the same check,
    # and for a bounded dynamic sequence it checks the declared capacity.
    physical = contract.physical_shape
    # A growing source sequence has one fixed backing array and a separate
    # logical-length companion.  Reusing the same local later may require a
    # larger backing array, while an earlier occurrence still denotes only
    # its smaller source interval.  That is an exact representation when
    # the outer capacity contains the interval and every element has the
    # same recursive shape; padding beyond the live length is unobservable.
    # Nested padding additionally needs the explicit storage contract and
    # every recursive length carrier; comparing maxima alone is not enough.
    # A control-flow join may allocate one backing array large enough for
    # every arm.  An individual arm can still have an exact, smaller
    # logical length; in that case ``_dynamic_sequence`` intentionally
    # returns ``None`` so consumers use the exact length, but the root
    # length image remains the proof that cells beyond it are padding.
    # The older outer-only representation remains valid without a separate
    # storage contract; recursive padding has the stronger authority below.
    padded_dynamic = (
        () in image
        and contract.is_sequence
        and physical is not None
        and len(shape) == len(physical)
        and shape[0] >= physical[0]
        and shape[1:] == physical[1:]
    )
    padded_recursive = (
        storage.is_sequence
        and storage.physical_shape == shape
        and contract_contains(storage, contract)
        and all(path in image and shape_of(image[path]) == length_shape
                for path, length_shape, _capacity in lower_context.dynamic_length_specs(storage))
    )
    if physical is not None and physical != shape \
            and not (padded_dynamic or padded_recursive):
        raise LoweringError(
            source, "the lowered value disagrees with its source storage "
                    f"contract: {shape} against {physical}",
            code="source-value-contract-disagrees")

    # Length metadata refines an already valid physical representation;
    # it cannot repair a scalar/array or recursive-shape disagreement.
    # Check it only after the backing value has passed that prerequisite,
    # so a faulty lowerer is rejected at the boundary it actually broke.
    check_value_lengths(source, contract, image)


def check_step_aliases(proc, low, prepared, declarations, planned, contexts, *,
                       root_renames, source_value_budget, analysis_cache):
    """Check source sharing using final IR types and the invocation-local cache."""
    model = prepared.model
    fn = model.step
    held = contexts.held
    boundary_arrays = planned.boundary_arrays
    active_products = contexts.active_products
    state_names = declarations.state_names
    boundary_contracts = planned.boundary_contracts
    typed_arrays = {name for name, kind in proc.types.items()
                    if isinstance(kind, types_mod.Array)}
    # The alias analysis speaks source names.  Output buffers are IR-only
    # arrays and must not make a same-spelled scalar local look mutable; a
    # reserved source local, conversely, is renamed in the IR and must be
    # mapped back before asking the source analysis about it.
    array_vars = (typed_arrays - held) | set(boundary_arrays)
    array_vars.update(name for name, binding in active_products.items()
                      if binding.contract.is_record or binding.contract.is_sequence
                      or binding.contract.is_mapping or
                      (binding.contract.is_option and
                       binding.contract.payload.is_record))
    array_vars |= {source for source, internal in root_renames.items()
                   if internal in typed_arrays}
    array_vars |= {source for source, internal in state_names.items()
                   if internal in typed_arrays}
    if array_vars:
        source_array_types = {
            name: kind for name, kind in proc.types.items()
            if isinstance(kind, types_mod.Array) and name not in held
        }
        source_array_types.update({
            source: proc.types[internal]
            for source, internal in root_renames.items()
            if isinstance(proc.types.get(internal), types_mod.Array)
        })
        source_array_types.update(boundary_arrays)
        source_state_contracts = {
            name: contract for name, contract in boundary_contracts.items()
            if contract.is_sequence
        }
        source_state_contracts.update({
            name: binding.contract for name, binding in active_products.items()
        })
        try:
            if analysis_cache is None:
                hazard = step_alias.step_alias_hazard(
                    model, array_vars, low.resolver,
                    loop_spans=low.source_loop_spans,
                    array_types=source_array_types,
                    state_contracts=source_state_contracts,
                    borrowed_arguments=low.borrowed_arguments,
                    source_value_budget=source_value_budget)
            else:
                alias_key = step_alias.alias_query_identity(
                    model, array_vars, low.source_loop_spans,
                    source_array_types, state_contracts=source_state_contracts,
                    borrowed_arguments=low.borrowed_arguments)
                if alias_key in analysis_cache.alias_results:
                    hazard = analysis_cache.alias_results[alias_key]
                else:
                    hazard = step_alias.step_alias_hazard(
                        model, array_vars, low.resolver,
                        loop_spans=low.source_loop_spans,
                        array_types=source_array_types,
                        state_contracts=source_state_contracts,
                        borrowed_arguments=low.borrowed_arguments,
                        source_value_budget=source_value_budget)
                    analysis_cache.alias_results[alias_key] = hazard
        except SourceValueBudgetExceeded as exc:
            raise LoweringError(
                exc.node,
                "source value planning exceeded the procedure budget during "
                "lowering",
                code="source-plan-budget-exceeded") from None
        if hazard is not None:
            if hazard.kind == "mutation":
                detail = (
                    f"the step binds {hazard.other!r} and "
                    f"{hazard.written!r} to one mutable object and then "
                    f"writes through {hazard.written!r}")
            elif hazard.kind == "retained":
                detail = (
                    f"the step retains one mutable object through distinct "
                    f"state locations rooted at {hazard.other!r} and "
                    f"{hazard.written!r}")
            else:
                detail = (
                    f"the alias analysis cannot establish the effect of "
                    f"{hazard.written!r} relative to {hazard.other!r}")
            raise LoweringError(
                fn, detail + ": the IR copies arrays by value where Python "
                "shares references",
                code="array-assigned-by-reference")


def check_construction_sharing(proc, model, construction, *, state_names, active_structured, active_products, boundary_initial):
    """Check authenticated entry sharing after boundary-owned fields are rebound."""
    # Construction invariants apply to every admitted model. For value-array
    # state, also reject shared or unobservable mutable entry objects.
    shared_pairs = construction.shared_pairs
    opaque_fields = construction.opaque_fields
    for left, right in shared_pairs:
        if left in boundary_initial or right in boundary_initial:
            # Rebinding a boundary-owned field after reset severs its
            # construction-time alias before the first analyzed step.
            continue
        ends = (proc.types.get(state_names.get(left, left)),
                proc.types.get(state_names.get(right, right)))
        scalar_ends = (left not in active_structured
                       and right not in active_structured
                       and left not in active_products
                       and right not in active_products
                       and proc.types and all(
            kind is None or isinstance(kind, types_mod._Value)
            for kind in ends))
        if not scalar_ends:
            raise LoweringError(
                model.cls,
                f"after construction, {left!r} and {right!r} share one "
                f"mutable object, an alias the IR cannot carry: a write "
                f"through one name would move only one of the two",
                code="state-aliased-in-construction")
    for name in opaque_fields:
        if name in boundary_initial:
            continue
        if (name in active_structured
                or name in active_products
                or isinstance(proc.types.get(state_names.get(name, name)), types_mod.Array)):
            raise LoweringError(
                model.cls,
                f"the state {name!r} holds an object whose graph cannot "
                f"be walked faithfully, so its sharing is unattested",
                code="state-graph-unverified")


def _enforce_scalar_capability(proc, where):
    """Refuse every scalar value the completed procedure would retain.

    Source integers reach SolverIR through several legitimate authorities:
    literals, owner-resolved constants, construction-folded fields and finite
    ROM cells.  Checking those sources separately makes each new lowering rule
    a new bypass.  The completed IR is their common boundary, so one iterative
    walk checks every ``Lit``; the same check covers the recursively carried
    initial state.  The encoder repeats the literal check as a defensive API
    boundary for hand-built procedures.
    """
    for expr in ir.expression_nodes(proc.body):
        if (isinstance(expr, ir.Lit) and type(expr.value) is int) \
                and scalar_magnitude_bits(expr.value) > SCALAR_BITS:
            magnitude = scalar_magnitude_bits(expr.value)
            raise LoweringError(
                where,
                f"a retained scalar literal needs {magnitude} magnitude "
                f"bits, over the reported capability of {SCALAR_BITS}",
                code="scalar-value-exceeds-capability")

    pending = list(proc.initial.values())
    seen = set()
    while pending:
        value = pending.pop()
        if type(value) is int:
            magnitude = scalar_magnitude_bits(value)
            if magnitude > SCALAR_BITS:
                raise LoweringError(
                    where,
                    f"an initial scalar needs {magnitude} magnitude bits, "
                    f"over the reported capability of {SCALAR_BITS}",
                    code="scalar-value-exceeds-capability")
            continue
        if type(value) not in (list, tuple):
            continue
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        pending.extend(value)


def safety_audit(sites, low, body):
    """Reconcile source declarations with emitted or discharged failures.

    Declarations were fixed before lowering. A missing guard on a reached
    operation is an audit error, not an unreachable goal. A source-total or
    unreachable occurrence may be discharged, but keeps its public goal.
    Analysis resource limits are not artifact failures and are excluded.
    """
    from .declared import unaccounted
    from ..symbolic.procedure_contract import ANALYSIS_ERROR_CODES

    declared = {site.id: site for site in sites}
    limits = {c.goal for c in ir.walk(body)
              if isinstance(c, ir.Fail) and c.goal
              and c.code in ANALYSIS_ERROR_CODES}
    produced = {g for g in ir.goals_of(body)
                if str(g).startswith("safety:")} - limits
    discharged = dict(low.discharged)
    for key, site in declared.items():
        if key in produced or key in discharged:
            continue
        if site.node is not None and id(site.node) not in low.reached:
            discharged[key] = "no bounded execution reaches this operation"
    audit = unaccounted(declared, produced, discharged)
    # ``low.discharged`` records occurrence-local proofs.  A helper source
    # site can be safe in one inline context and guarded in another, while its
    # public identifier is shared.  The Procedure-level table contains only
    # goals with no produced occurrence; a live occurrence takes precedence.
    public_discharged = {
        goal: reason for goal, reason in discharged.items()
        if goal not in produced
    }
    return audit, public_discharged


def _check_producers(fn, sites, body):
    """Require a mark for every source-declared coverage outcome.

    Unreachable outcomes keep guarded marks. A missing producer is a lowering
    error, not evidence that the corresponding source goal is unreachable.
    """
    marked = ir.goals_of(body)
    missing = sorted(g for s in sites for g in
                     (goal_id(s, o) for o in s.outcomes)
                     if g not in marked)
    if missing:
        raise LoweringError(
            fn, f"{len(missing)} declared goals are neither produced nor "
                f"accounted for, the first being {missing[0]}", code="goals-unaccounted")
