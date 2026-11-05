"""Solver-free validation of a compiler-owned procedure.

This layer owns structural/type checks, semantic binding and invocation-local
validation evidence. It cannot construct a plan, formula or proof. Planning
may retain derived plans on the same private owner; only the planner reads or
populates that cache, and its keys include the complete entry ranges.
"""

from __future__ import annotations

from . import ir
from . import typecheck as value_types
from .procedure_contract import ANALYSIS_ERROR_CODES, ProcedureContract, ProcedureContractError

__all__ = ["EncodingError", "build_procedure_contract", "bind_procedure_contract", "validate_procedure"]

_PROCEDURE_VALIDATION_TOKEN = object()


class EncodingError(Exception):
    """A step that cannot be encoded, with the reason and which kind of reason.

    ``kind`` is `range`, `shape` or `type` where the frontend left something
    unanswered, `capability` where the representation language cannot carry it,
    `resource` where a finite construction ran out of its declared budget, `goal`
    where the query names something the step does not encode, and `None` for
    everything else -- an operation with no primitive. That distinction
    decides which column a refusal belongs in and therefore
    whose work item it is, and it was available at the moment of the refusal and thrown
    away, so a reader had to recover it by matching on the message or by encoding a
    second time in collecting mode.

    Free here, and a second encoding is not free. `verify_encoding` is for the case
    where all the faults are wanted rather than the first, which is a reporting need
    and not one the verification path has.
    """

    def __init__(self, message, kind=None):
        super().__init__(message)
        self.kind = kind


def build_procedure_contract(proc, resolved=None):
    """Construct the one semantic contract implied by an admitted procedure."""
    from . import ir, typecheck as types

    if resolved is None:
        resolved, faults = types.of_procedure(proc)
        if faults:
            raise ProcedureContractError(faults[0])
    else:
        resolved = dict(resolved)

    def context(names, role):
        missing = [name for name in names if name not in resolved]
        if missing:
            raise ProcedureContractError(f"the {role} context has no type for {missing[0]!r}")
        return tuple((name, resolved[name]) for name in names)

    state_types = context(proc.state, "state")
    input_types = context(proc.inputs, "input")
    local_types = context(proc.locals, "local")
    output_types = []
    for name, variable in proc.output_vars.items():
        if variable not in resolved:
            raise ProcedureContractError(f"the output context has no type for {name!r}")
        kind = resolved[variable]
        # Lowered batch outputs are fixed arrays whose outer dimension is
        # representation capacity.  The public output context owns the value
        # of one active transaction, not the padding buffer around it.  A
        # legacy hand-built scalar output remains scalar here.
        if isinstance(kind, types.Array):
            kind = kind.elem
        output_types.append((name, kind))

    signature = getattr(proc, "signature", None)
    input_domains = () if signature is None else signature.inputs
    output_domains = () if signature is None else signature.outputs
    value_bindings = list(getattr(proc, "value_bindings", ()))
    if signature is not None:
        expected_inputs = tuple(
            variable for binding in signature.input_bindings for variable in binding.solver_variables
        )
        if len(expected_inputs) != len(set(expected_inputs)):
            raise ProcedureContractError(
                "the task input bindings share SolverIR variables", code="value-binding-solver-path-duplicate"
            )
        if tuple(proc.inputs) != expected_inputs:
            raise ProcedureContractError("the typed input context disagrees with the finite input domain")
        if tuple(proc.outputs) != signature.output_names:
            raise ProcedureContractError("the typed output context disagrees with the finite output domain")
        for role, bindings, values in (
            ("input", signature.input_bindings, input_types),
            ("output", signature.output_bindings, tuple(output_types)),
        ):
            declared = dict(values)
            for binding in bindings:
                for variable, kind in binding.solver_types():
                    if declared.get(variable) != kind:
                        semantic = "integer-valued " if binding.value_type.kind == "integer" else ""
                        raise ProcedureContractError(
                            f"the task's {semantic}{role} value "
                            f"{binding.name!r} binds "
                            f"{variable!r} as {kind}, but the procedure gives "
                            f"it {declared.get(variable)!r}",
                            code=f"signature-{role}s-mismatch",
                        )
                key = (role, binding.name)
                if not any((bound_role, bound.name) == key for bound_role, bound in value_bindings):
                    value_bindings.append((role, binding))

    codes = {command.code for command in ir.walk(proc.body) if isinstance(command, ir.Fail)}
    analysis = codes & ANALYSIS_ERROR_CODES
    artifact = codes - analysis
    return ProcedureContract(
        state_types,
        input_types,
        local_types,
        tuple(output_types),
        input_domains,
        output_domains,
        artifact,
        analysis,
        value_bindings,
    )


def bind_procedure_contract(proc, resolved=None):
    """Bind or validate the procedure's write-once semantic contract."""
    contract = build_procedure_contract(proc, resolved)
    if proc.semantics is None:
        proc.semantics = contract
    elif proc.semantics != contract:
        raise ProcedureContractError("the procedure changed after its semantic contract was bound")
    return proc.semantics


class _ProcedureValidation:
    """Local evidence that a verifier checked its owned, unchanged procedure.

    The value is deliberately private, identity-bound, and not pickleable.
    Planning and a private search worker reuse it for their same in-memory
    ``Procedure``. Public one-shot calls still validate the complete
    representation themselves; caller-owned mutable IR is never cached.
    """

    __slots__ = ("_implicit_plans", "_procedure", "_token", "compact_arrays", "graph", "spent", "types")

    def __init__(self, procedure, spent, token, *, types, compact_arrays=False):
        if token is not _PROCEDURE_VALIDATION_TOKEN:
            raise TypeError("procedure validation evidence is verifier-owned")
        self._procedure = procedure
        self._token = token
        self.spent = spent
        self.compact_arrays = compact_arrays
        self.types = types
        self._implicit_plans = {}
        # The public property checks both seals. An admitted worker-owned
        # procedure is unchanged for this token's lifetime; reading its graph
        # on every cone or resumed query must not rehash the entire body.
        self.graph = procedure.graph

    def __reduce__(self):
        raise TypeError("procedure validation evidence is invocation-local")


def _checked_procedure_validation(validation, procedure):
    if (
        type(validation) is not _ProcedureValidation
        or validation._token is not _PROCEDURE_VALIDATION_TOKEN
        or validation._procedure is not procedure
    ):
        raise TypeError("procedure validation evidence belongs to another procedure")
    return validation


def _procedure_validation(proc, *, compact_arrays=False):
    """Refuse an oversized, over-deep, or cyclic procedure before encoding.

    This is intentionally solver-free.  Callers that must construct boundary
    symbols before :func:`encode_step` (bounded unrolling and exact probes) use
    the same seam so an invalid public ``Procedure`` cannot consume Z3 memory
    or Python recursion first.
    """
    try:
        spent = ir.validate_procedure_representation(proc, compact_arrays=compact_arrays)
    except ir.RepresentationStructureError as exc:
        raise EncodingError(f"{exc.code}: {exc}", kind="capability") from None
    resolved, faults = value_types.of_procedure(proc)
    if faults:
        raise EncodingError(f"the procedure is not semantically typed: {faults[0]}", kind="type")
    try:
        bind_procedure_contract(proc, resolved)
    except ProcedureContractError as exc:
        raise EncodingError(f"the procedure has no complete semantic contract: {exc}", kind="type") from None
    return _ProcedureValidation(proc, spent, _PROCEDURE_VALIDATION_TOKEN, types=resolved, compact_arrays=compact_arrays)


def validate_procedure(proc):
    """Validate a public procedure and return its representation cost."""

    return _procedure_validation(proc).spent
