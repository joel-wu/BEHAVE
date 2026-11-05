"""Bind procedure semantics and prove the compiler output boundary.

Immutable signature and value-domain records belong to the shared symbolic
layer. This module owns compiler checks over the IR, ranges and exact encoder;
reading a signature must not depend on these proof constructors.
"""

from __future__ import annotations

from ..symbolic.procedure_contract import (
    OutputContractError,
    ProcedureSignature,
    _project_source_output_value as _project_source_output_value,
    ANALYSIS_ERROR_CODES,
    ProcedureContract,
    ProcedureContractError,
    _fixed_sequence_length as _fixed_sequence_length,
    _validate_active_output_values as _validate_active_output_values,
    _validate_output_values as _validate_output_values,
)
from ..symbolic.value_binding import SolverLeafPath, ValueBinding, ValueBindingError as ValueBindingError
from ..symbolic.validation import build_procedure_contract, bind_procedure_contract

__all__ = [
    "ANALYSIS_ERROR_CODES",
    "OutputContractError",
    "ProcedureContract",
    "ProcedureContractError",
    "ProcedureSignature",
    "SolverLeafPath",
    "ValueBinding",
    "bind_procedure_contract",
    "build_procedure_contract",
    "output_contract_evidence",
    "output_range_evidence",
]


def output_range_evidence(proc, env):
    """Prove every normally returned transaction lies in ``proc.signature``.

    Source planning owns the batch shape; the signature owns each field's
    finite carrier.  The range transformer is already the authority used for
    the symbolic widths.  Its postcondition over-approximates every normal
    exit, so containment here establishes the task boundary without deleting
    illegal executions from the solver formula.
    """
    from ..symbolic import ranges
    from ..symbolic import typecheck as types

    signature = proc.signature
    if signature is None:
        return {}
    if getattr(proc, "boundary_complete", True) is not True:
        raise OutputContractError(
            "output-boundary-incomplete",
            "this analysis procedure does not contain the complete return boundary")
    expected = tuple(signature.output_names)
    public = {
        variable for binding in signature.output_bindings
        for variable in binding.solver_variables
    }
    if tuple(proc.outputs) != expected or set(proc.output_vars) != public:
        raise OutputContractError(
            "signature-outputs-mismatch",
            "the lowered output boundary does not match the task signature")

    # A finite carrier hull need not retain a loop's guarded value boundary.
    # Use its sealed source joins before resorting to whole-step SMT encoding.
    after = ranges.procedure_post(proc, dict(env), source_paths=True) or {}
    if proc.count is not None and proc.types.get(proc.count) != types.INTEGER:
        raise OutputContractError(
            "signature-outputs-mismatch",
            "the output count is not represented as one scalar value")
    count_range = (ranges.Range(1, 1) if proc.count is None
                   else after.get(proc.count))
    if count_range is None or count_range.lo is None or count_range.hi is None:
        raise OutputContractError(
            "output-count-range-unproved",
            "the transaction count has no proved finite range")
    if count_range.lo < 0:
        raise OutputContractError(
            "output-count-outside-capacity",
            f"the transaction count may be {count_range.lo}")

    capacity = getattr(proc, "output_capacity", None)
    if capacity is None:
        if proc.count is not None:
            raise OutputContractError(
                "signature-outputs-mismatch",
                "a counted output has no declared logical capacity")
        capacity = 1
    if count_range.hi > capacity:
        raise OutputContractError(
            "output-count-outside-capacity",
            f"the transaction count may reach {count_range.hi}, beyond the "
            f"declared capacity {capacity}")

    evidence = {}
    for binding in signature.output_bindings:
        leaves = {leaf.path: leaf for leaf in binding.layout.leaves()}
        solver_types = dict(binding.solver_types())
        rows = {}
        for logical, solver in binding.leaf_bindings:
            var = proc.output_vars[solver.variable]
            kind = proc.types.get(var)
            expected_kind = solver_types[solver.variable]
            physical = kind.cells if isinstance(kind, types.Array) else 0
            if not isinstance(kind, types.Array) or kind.elem != expected_kind \
                    or physical != max(capacity, 1):
                raise OutputContractError(
                    "signature-outputs-mismatch",
                    f"physical output {solver.variable!r} does not have its "
                    "declared batch/value type")
            leaf = leaves[logical]
            bounds = ((0, 1) if leaf.semantic_type == types.BOOLEAN else
                      leaf.carrier.bounds if leaf.carrier is not None else None)
            if bounds is None:
                raise OutputContractError(
                    "signature-outputs-mismatch",
                    f"output {binding.name!r} leaf {logical!r} has no finite "
                    "task carrier")
            if count_range.hi == 0:
                inferred_image = None
            else:
                inferred = after.get(var)
                if inferred is None or inferred.lo is None or inferred.hi is None:
                    raise OutputContractError(
                        "output-range-unproved",
                        f"output {binding.name!r} leaf {logical!r} has no proved "
                        "finite value range")
                if inferred.lo < bounds[0] or inferred.hi > bounds[1]:
                    raise OutputContractError(
                        "output-outside-contract",
                        f"output {binding.name!r} leaf {logical!r} may lie in "
                        f"[{inferred.lo}, {inferred.hi}], outside "
                        f"[{bounds[0]}, {bounds[1]}]")
                inferred_image = [inferred.lo, inferred.hi]
            rows[logical] = {
                "inferred": inferred_image,
                "declared": list(bounds),
                "method": "range",
            }
        evidence[binding.name] = (rows[()] if set(rows) == {()} else {
            "leaves": [
                {"path": list(path), **rows[path]}
                for path in (leaf.path for leaf in binding.layout.leaves())
            ],
            "method": "range",
        })
    return evidence


def _position_output_evidence(proc, positions, interval_code):
    """Use an exact-closed position plan when its output hulls fit the task.

    The plan's symbolic closure proof establishes that every normal execution
    stays inside each recorded output carrier.  If those carriers are also
    contained in the task signature, no horizon-wide formula is needed.  A
    carrier that is too wide proves nothing either way, so the caller falls
    back to its exact run query rather than treating abstract imprecision as a
    source violation.
    """
    from ..symbolic import typecheck as types

    plan = positions.plan
    capacity = getattr(proc, "output_capacity", None)
    if capacity is None:
        if proc.count is not None:
            return None
        capacity = 1

    active_layers = []
    for layer in plan.output_layers:
        if not layer:
            continue
        count = layer.get("#count")
        if count is None or count.lo < 0 or count.hi > capacity:
            return None
        active_layers.append((layer, count))

    evidence = {}
    for binding in proc.signature.output_bindings:
        leaves = {leaf.path: leaf for leaf in binding.layout.leaves()}
        rows = {}
        for logical, solver in binding.leaf_bindings:
            leaf = leaves[logical]
            bounds = ((0, 1) if leaf.semantic_type == types.BOOLEAN else
                      leaf.carrier.bounds if leaf.carrier is not None else None)
            if bounds is None:
                raise OutputContractError(
                    "signature-outputs-mismatch",
                    f"output {binding.name!r} leaf {logical!r} has no finite "
                    "task carrier")
            inferred = []
            for layer, count in active_layers:
                if count.hi == 0:
                    continue
                carrier = layer.get(solver.variable)
                if carrier is None:
                    return None
                if carrier.lo < bounds[0] or carrier.hi > bounds[1]:
                    return None
                inferred.append((carrier.lo, carrier.hi))
            image = (None if not inferred else
                     [min(lo for lo, _hi in inferred),
                      max(hi for _lo, hi in inferred)])
            rows[logical] = {
                "inferred": image,
                "declared": list(bounds),
                "method": "position-closure",
                "bound": plan.bound,
                "plan": positions.identity,
                "range_issue": interval_code,
            }
        evidence[binding.name] = (rows[()] if set(rows) == {()} else {
            "leaves": [
                {"path": list(path), **rows[path]}
                for path in (leaf.path for leaf in binding.layout.leaves())
            ],
            "method": "position-closure",
            "bound": plan.bound,
            "plan": positions.identity,
            "range_issue": interval_code,
        })
    return evidence


def output_contract_evidence(proc, env, bound=None, timeout_ms=None):
    """Establish the output signature without filtering illegal executions.

    Interval containment is the cheap path.  When joins lose a temporary's
    range, an exact query asks whether a normally completing admitted step (or
    bounded run) can violate the count or an active output cell.  The validity
    predicate is queried beside the transition formula, never conjoined into
    it: otherwise the query would prove only that the already-filtered program
    has no illegal output.
    """
    if proc.signature is None:
        return {}
    from ..symbolic.procedure_contract import (
        OutputBoundaryError,
        output_boundary_evidence,
    )

    try:
        local = output_boundary_evidence(proc)
    except OutputBoundaryError as exc:
        raise OutputContractError(exc.code, str(exc)) from None
    if local is not None:
        return local
    if bound is None:
        from ..symbolic import ranges

        unproved = ranges.unproved(proc, env)
        if unproved:
            raise OutputContractError(
                "output-domain-unresolved",
                "the state ranges are not an inductive invariant for: "
                + ", ".join(sorted(unproved)[:8]))
    try:
        return output_range_evidence(proc, env)
    except OutputContractError as interval_issue:
        if interval_issue.code not in {
                "output-count-range-unproved",
                "output-count-outside-capacity",
                "output-range-unproved",
                "output-outside-contract"}:
            raise
        interval_code = interval_issue.code

    import math
    import time

    import z3

    from . import budgets
    from ..symbolic import typecheck as types
    from ..symbolic.encoder import encode_step
    from ..symbolic.validation import EncodingError
    from .exact_solver import solve_exact
    from .residual import unroll
    from ..core.query import SolverAnswer
    from ..execution_control import check_execution
    from ..search_policy import remaining_solver_ms

    if timeout_ms is None:
        timeout_ms = budgets.QUERY_MS
    if type(timeout_ms) is not int or timeout_ms < 1:
        raise ValueError("an output-contract timeout must be a positive integer")
    deadline = time.monotonic() + remaining_solver_ms(timeout_ms) / 1000.0

    def remaining_budget():
        check_execution()
        remaining = math.ceil((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise OutputContractError(
                "output-domain-unresolved", "the output-contract deadline expired")
        return remaining_solver_ms(remaining)

    try:
        if bound is not None and (type(bound) is not int or bound < 1):
            raise ValueError("an output-contract bound must be a positive integer")
        if bound is None or (not proc.state and not proc.input_framing):
            # With no persistent state every invocation has this same input
            # relation. A counterexample at any finite position is therefore
            # already a one-step counterexample; UNSAT proves every position.
            # No position-carrier closure or horizon-wide unrolling is needed.
            # Framing also carries history outside proc.state. Stateful or
            # framed procedures still use the reachable finite run below.
            encoding = encode_step(proc, env=env, tag="@output-contract")
            violations = [
                (0, name, z3.And(encoding.ok, z3.Not(condition)))
                for name, condition in encoding.output_checks.items()
            ]
            formula = encoding.exact_formula()
        else:
            # A bounded claim is about one finite representation of the whole
            # run.  Planning each step independently can choose a wider carrier
            # after an update and then fail to connect it to the preceding
            # step.  The position plan derives all boundary layers first and
            # gives the unrolling one shared scalar representation.
            from ..symbolic.closure_proof import prepare_positions

            positions = prepare_positions(proc, env, bound, _closure_deadline=deadline)
            position_evidence = _position_output_evidence(
                proc, positions, interval_code)
            remaining_budget()
            if position_evidence is not None:
                return position_evidence
            run = unroll(proc, env, bound, plan=positions)
            violations = run.output_violations()
            formula = run.formula
    except EncodingError as exc:
        raise OutputContractError(
            "output-domain-unresolved",
            f"the exact output query could not encode the step: {exc}") from None

    invalid = [condition for _step, _name, condition in violations]
    if not invalid:
        raise OutputContractError(
            "output-domain-unresolved",
            "the exact output query produced no boundary checks")
    try:
        # Use the same exact Boolean/bit-vector query path as goal search.
        # Output validity remains the queried target, never a restriction on
        # executions. Preparation and every internal solver phase share the
        # original deadline; an unanswered proof still blocks this admission.
        solved = solve_exact(formula, z3.Or(invalid), remaining_budget())
    except z3.Z3Exception as exc:
        raise OutputContractError(
            "output-domain-unresolved",
            f"the exact output query failed: {exc}") from None
    remaining_budget()
    if solved.answer is SolverAnswer.SAT:
        if bound is None:
            # The fixed-point environment is a Cartesian over-approximation.
            # UNSAT over it proves all reachable states safe; SAT may combine
            # field values no execution reaches together and is therefore an
            # unresolved proof, not a counterexample to the source model.
            raise OutputContractError(
                "output-domain-unresolved",
                "the interval product admits an output violation, but no "
                "finite run bound was supplied to establish its reachability")
        model = solved.model
        step, name = next(
            (step, name) for step, name, condition in violations
            if z3.is_true(model.eval(condition, model_completion=True)))
        if name == "count":
            raise OutputContractError(
                "output-count-outside-capacity",
                f"a normal execution at step {step} has an invalid output count")
        raise OutputContractError(
            "output-outside-contract",
            f"a normal execution at step {step} emits {name} outside its "
            "declared finite type")
    if solved.answer is not SolverAnswer.UNSAT:
        raise OutputContractError(
            "output-domain-unresolved",
            f"the exact output query was unresolved: "
            f"{solved.reason or solved.answer.value}")

    evidence = {}
    for binding in proc.signature.output_bindings:
        rows = []
        for leaf in binding.layout.leaves():
            bounds = ((0, 1) if leaf.semantic_type == types.BOOLEAN else
                      leaf.carrier.bounds if leaf.carrier is not None else None)
            if bounds is None:
                raise OutputContractError(
                    "signature-outputs-mismatch",
                    f"output {binding.name!r} leaf {leaf.path!r} has no finite "
                    "task carrier")
            rows.append({
                "path": list(leaf.path),
                "inferred": None,
                "declared": list(bounds),
                "method": "exact",
                "bound": bound,
                "timeout_ms": timeout_ms,
                "range_issue": interval_code,
            })
        evidence[binding.name] = (
            {key: value for key, value in rows[0].items() if key != "path"}
            if len(rows) == 1 and rows[0]["path"] == [] else
            {"leaves": rows, "method": "exact", "bound": bound,
             "timeout_ms": timeout_ms, "range_issue": interval_code}
        )
    return evidence
