"""Bind one transaction-step Behavior procedure to task-owned endpoint fields.

The task owns endpoint identity and recursive field types; the source owns the
logical parameter/result spellings.  This module performs that one name projection
without serialising independent endpoints or flattening their logical values to an
integer row.  A caller decides which same-slot endpoint batches form one procedure
step, using the retained endpoint projections below.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..core.contract import Direction, TaskContract
from ..core.record_value import freeze_record_value, thaw_record_value
from ..core.refusal import Refusal, Refused
from .extract import _output_names, source_model
from ..symbolic.procedure_contract import ProcedureSignature
from ..symbolic.value_binding import SolverLeafPath, ValueBinding, ValueBindingError
from ..symbolic.value_layout import layout_of_value_type

__all__ = [
    "EndpointProjection",
    "TaskBoundaryError",
    "TaskProcedureBoundary",
    "accepted_input_valuation",
    "accepted_output_streams",
    "full_output_streams",
    "lower_task_behavior",
    "project_source_output_streams",
    "task_call_inputs",
    "task_clock_domains",
    "task_input_projections",
    "task_method_boundary",
    "task_procedure_boundary",
    "task_signature_boundary",
]


class TaskBoundaryError(ValueError):
    """A concrete endpoint batch cannot denote one complete procedure call."""

    __slots__ = ("code", "detail")

    def __init__(self, code, message, **detail):
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class EndpointProjection:
    """Task field order as ``(public field, logical procedure name)`` pairs."""

    endpoint: str
    fields: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if type(self.endpoint) is not str or not self.endpoint:
            raise TypeError("an endpoint projection needs a nonempty endpoint")
        if (type(self.fields) is not tuple
                or any(type(row) is not tuple or len(row) != 2
                       or any(type(name) is not str or not name for name in row)
                       for row in self.fields)
                or len({public for public, _logical in self.fields})
                != len(self.fields)
                or len({logical for _public, logical in self.fields})
                != len(self.fields)):
            raise TypeError(
                "an endpoint projection needs distinct public/logical field pairs")


@dataclass(frozen=True, slots=True)
class TaskProcedureBoundary:
    """The exact typed procedure signature and endpoint-preserving projections."""

    signature: ProcedureSignature
    inputs: tuple[EndpointProjection, ...]
    outputs: tuple[EndpointProjection, ...]

    def __post_init__(self):
        if type(self.signature) is not ProcedureSignature:
            raise TypeError("a task procedure boundary needs a ProcedureSignature")
        if type(self.inputs) is not tuple or type(self.outputs) is not tuple \
                or any(type(item) is not EndpointProjection
                       for item in self.inputs + self.outputs):
            raise TypeError("task endpoint projections are exact tuples")


def accepted_input_valuation(boundary, accepted):
    """Map one same-slot accepted endpoint batch to one logical input valuation.

    Silence maps to ``None``.  Any nonempty batch must cover every input endpoint
    needed by the ordinary procedure call.  This is the shared semantic rule used by
    generation and concrete-record audit: independently accepted endpoints are not
    serialized, and absent values are never invented.
    """

    if type(boundary) is not TaskProcedureBoundary:
        raise TypeError("an accepted input batch needs one TaskProcedureBoundary")
    if type(accepted) is not dict:
        try:
            accepted = dict(accepted)
        except (TypeError, ValueError):
            raise TypeError(
                "an accepted input batch is an endpoint/payload mapping") from None
    expected = {row.endpoint: row for row in boundary.inputs}
    unknown = sorted(set(accepted) - set(expected))
    if unknown:
        raise TaskBoundaryError(
            "behavior-boundary-input-endpoint-unknown",
            "an accepted input batch names an endpoint outside its procedure boundary",
            endpoints=unknown)
    active = {name: payload for name, payload in accepted.items()
              if payload is not None}
    if not active:
        return None
    missing = sorted(set(expected) - set(active))
    if missing:
        raise TaskBoundaryError(
            "behavior-boundary-partial-input-batch",
            "one ordinary Behavior call needs the complete same-slot input endpoint "
            "batch",
            accepted=sorted(active), missing=missing)
    logical = {}
    for endpoint in sorted(active):
        projection = expected[endpoint]
        payload = active[endpoint]
        if type(payload) not in (tuple, list) \
                or len(payload) != len(projection.fields):
            raise TaskBoundaryError(
                "behavior-boundary-input-payload-arity",
                "an accepted endpoint payload has another field arity",
                endpoint=endpoint,
                expected=len(projection.fields),
                got=(len(payload) if type(payload) in (tuple, list)
                     else type(payload).__name__))
        for (_public, name), value in zip(projection.fields, payload):
            logical[name] = thaw_record_value(freeze_record_value(
                value, f"accepted[{endpoint}].{_public}"))
    if set(logical) != set(boundary.signature.input_names):
        raise TaskBoundaryError(
            "behavior-boundary-input-binding-incomplete",
            "the complete endpoint batch does not cover the procedure input valuation",
            missing=sorted(set(boundary.signature.input_names) - set(logical)),
            extra=sorted(set(logical) - set(boundary.signature.input_names)))
    return logical


def accepted_output_streams(boundary, batches):
    """Project concrete endpoint-tagged output batches into per-endpoint streams."""

    if type(boundary) is not TaskProcedureBoundary:
        raise TypeError("accepted outputs need one TaskProcedureBoundary")
    expected = {row.endpoint: row for row in boundary.outputs}
    streams = {endpoint: [] for endpoint in expected}
    for batch_index, batch in enumerate(batches):
        if type(batch) is not dict:
            try:
                batch = dict(batch)
            except (TypeError, ValueError):
                raise TypeError(
                    "an accepted output batch is an endpoint/payload mapping") \
                    from None
        unknown = sorted(set(batch) - set(expected))
        if unknown:
            raise TaskBoundaryError(
                "behavior-boundary-output-endpoint-unknown",
                "an accepted output batch names an endpoint outside its procedure "
                "boundary",
                batch=batch_index, endpoints=unknown)
        for endpoint, payload in batch.items():
            if payload is None:
                continue
            projection = expected[endpoint]
            if type(payload) not in (tuple, list) \
                    or len(payload) != len(projection.fields):
                raise TaskBoundaryError(
                    "behavior-boundary-output-payload-arity",
                    "an accepted output payload has another field arity",
                    batch=batch_index, endpoint=endpoint,
                    expected=len(projection.fields),
                    got=(len(payload) if type(payload) in (tuple, list)
                         else type(payload).__name__))
            streams[endpoint].append({
                logical: thaw_record_value(freeze_record_value(
                    value, f"outputs[{batch_index}][{endpoint}].{public}"))
                for (public, logical), value in zip(
                    projection.fields, payload)
            })
    return streams


def project_source_output_streams(boundary, batches):
    """Project native endpoint streams into the task's logical output values."""

    streams = accepted_output_streams(boundary, batches)
    try:
        return {
            endpoint: [
                boundary.signature.project_source_output_fields(transaction)
                for transaction in transactions
            ]
            for endpoint, transactions in streams.items()
        }
    except ValueError as exc:
        raise TaskBoundaryError(
            "behavior-boundary-source-output-invalid",
            f"a native Behavior output is outside its declared logical map: {exc}") \
            from None


def full_output_streams(boundary, transactions):
    """Split each complete procedure result into endpoint-identified streams.

    Ordinary transaction-step Behavior returns one flat logical output valuation.
    Several task output endpoints therefore denote a *full output product*: every
    transaction contains every endpoint's fields.  Splitting by the frozen projection
    preserves endpoint identity while allowing each concrete ready/valid stream to be
    delayed independently.
    """

    if type(boundary) is not TaskProcedureBoundary:
        raise TypeError("full outputs need one TaskProcedureBoundary")
    projections = {row.endpoint: row for row in boundary.outputs}
    expected = {
        logical for row in boundary.outputs for _public, logical in row.fields
    }
    streams = {endpoint: [] for endpoint in projections}
    for index, transaction in enumerate(transactions):
        if type(transaction) is not dict or set(transaction) != expected:
            raise TaskBoundaryError(
                "behavior-boundary-output-product-incomplete",
                "one flattened Behavior result does not cover exactly the complete "
                "output endpoint product",
                transaction=index,
                missing=sorted(expected - set(transaction)
                               if type(transaction) is dict else expected),
                extra=sorted(set(transaction) - expected)
                      if type(transaction) is dict else [])
        for endpoint, projection in projections.items():
            streams[endpoint].append({
                logical: thaw_record_value(freeze_record_value(
                    transaction[logical],
                    f"transactions[{index}].{logical}"))
                for _public, logical in projection.fields
            })
    return streams


def _optional_inputs_unsupported(parameters):
    return Refused(Refusal(
        "behavior-boundary-optional-inputs-unsupported",
        "unmapped or ambiguous optional parameters need a default-preserving "
        "source boundary; this capability limit is not a candidate fault",
        parameters=sorted(parameters)))


def _endpoint_projections(contract, input_names, output_names, *,
                          clock=None, output_fallback=False,
                          directions=(Direction.IN, Direction.OUT), optional_inputs=()):
    source_sets = {
        Direction.IN: set(input_names),
        Direction.OUT: set(output_names),
    }
    by_name = {Direction.IN: {}, Direction.OUT: {}}
    projections = {Direction.IN: [], Direction.OUT: []}
    endpoints = tuple(
        endpoint for endpoint in contract.endpoints
        if endpoint.direction in directions
        and (clock is None or endpoint.clock == clock))
    for endpoint in endpoints:
        source_names = source_sets[endpoint.direction]
        logical = []
        for field in endpoint.fields:
            name, candidates = _field_binding_name(field, source_names)
            if name is None:
                matches = set(candidates) & source_names
                if (endpoint.direction is Direction.IN and len(matches) > 1
                        and matches & set(optional_inputs)):
                    # A required argument and its optional physical-name alias
                    # need not both be supplied. Ambiguity here is not proof of
                    # a missing required argument; do not assign candidate zero.
                    raise _optional_inputs_unsupported(matches & set(optional_inputs))
                if (output_fallback and endpoint.direction is Direction.OUT
                        and not output_names and len(candidates) == 1):
                    name = candidates[0]
                else:
                    raise Refused(Refusal(
                        "behavior-boundary-field-binding-unresolved",
                        "a task field does not map uniquely by its declared name or "
                        "port to the Behavior procedure boundary",
                        endpoint=endpoint.name, field=field.name,
                        candidates=list(candidates),
                        source=sorted(source_names)))
            if name in by_name[endpoint.direction]:
                raise Refused(Refusal(
                    "behavior-boundary-field-binding-duplicate",
                    "two task endpoint fields map to one Behavior value",
                    endpoint=endpoint.name, field=name))
            by_name[endpoint.direction][name] = field.type
            logical.append((field.name, name))
        projections[endpoint.direction].append(
            EndpointProjection(endpoint.name, tuple(logical)))
    return by_name, projections


def _field_binding_name(field, available):
    """Return the unique task-declared spelling present at a source boundary."""

    candidates = tuple(dict.fromkeys(
        name for name in (field.port, field.name)
        if type(name) is str and name))
    selected = tuple(name for name in candidates if name in available)
    return (selected[0] if len(selected) == 1 else None), candidates


def _check_input_names(source_inputs, bound_inputs, *, optional_inputs=(),
                       allow_defaults=False):
    from ..core.failure import CandidateFault

    missing = sorted(set(source_inputs) - set(bound_inputs))
    extra = sorted(set(bound_inputs) - set(source_inputs))
    if missing or extra:
        if not extra and set(missing) <= set(optional_inputs):
            if allow_defaults:
                return
            raise _optional_inputs_unsupported(missing)
        raise CandidateFault(Refusal(
            "behavior-boundary-input-binding-incomplete",
            "the task input fields and Behavior parameters differ",
            missing=missing, extra=extra))


def task_call_inputs(contract, process_inputs, *, extra_keywords=False,
                     optional_inputs=()):
    """Names supplied to a concrete keyword call, never a positional ABI.

    Explicit parameters retain the existing public-field/port projection.
    A **kwargs entry receives remaining fields by the declared port spelling,
    matching the transaction reference adapter (or field name without a port).
    The ordinary projection checker still rejects collisions, missing required
    parameters and ambiguous aliases; catching keywords does not guess them.
    """
    # Omit unbound optional keywords, allowing Python to use the original
    # definition-time default. Do not manufacture a value or a new task field.
    spellings = {name for endpoint in contract.inputs() for field in endpoint.fields
                 for name in (field.name, field.port) if name}
    names = [name for name in process_inputs
             if name not in optional_inputs or name in spellings]
    if extra_keywords:
        for endpoint in contract.inputs():
            for field in endpoint.fields:
                _selected, candidates = _field_binding_name(field, process_inputs)
                if not any(name in process_inputs for name in candidates):
                    name = field.port or field.name
                    if name not in names:
                        names.append(name)
    return tuple(names)


def task_clock_domains(contract, source_domains):
    """Select task-owned clocks, not every source helper sharing a prefix."""
    declared = tuple(sorted(clock.name for clock in contract.clocks))
    available = tuple(sorted(source_domains))
    if not available:
        if len(declared) <= 1:
            return ()
    elif len(declared) >= 2 and set(declared) <= set(available):
        return declared
    raise Refused(Refusal(
        "behavior-boundary-clock-domains-mismatch",
        "the Behavior needs a handler for every declared task clock domain",
        behavior=list(available), task=list(declared)))


def task_input_projections(contract, process_inputs, *, clock=None, optional_inputs=()):
    """Check the concrete call interface without inferring Python return values.

    Admission knows the method parameters. Output names may depend on executed
    Python code; concrete replay checks every produced row against the task with
    the same field-name rule. An exact procedure signature is a separate analysis
    requirement, not a prerequisite for executing a source artifact.
    """
    if type(contract) is not TaskContract:
        raise TypeError("an input projection needs one exact TaskContract")
    if (type(process_inputs) is not tuple
            or any(type(name) is not str or not name for name in process_inputs)
            or len(set(process_inputs)) != len(process_inputs)):
        raise TypeError("procedure inputs are distinct nonempty names")
    if clock is not None and (type(clock) is not str or not clock):
        raise TypeError("a procedure clock is absent or a nonempty name")
    try:
        by_name, projections = _endpoint_projections(
            contract, process_inputs, (), clock=clock, directions=(Direction.IN,),
            optional_inputs=optional_inputs)
    except Refused as exc:
        if exc.refusal.kind != "behavior-boundary-field-binding-unresolved":
            raise
        # Unlike an inferred output layout, every method parameter is known.
        # Zero or two declared spellings is a call-interface mismatch. Keep
        # task-side duplicate bindings and all other failures distinct.
        from ..core.failure import CandidateFault

        raise CandidateFault(Refusal(
            "behavior-boundary-input-binding-incomplete",
            "a task input field needs exactly one declared spelling in the "
            "Behavior parameter list",
            **exc.refusal.as_dict().get("detail", {}))) from exc
    _check_input_names(process_inputs, by_name[Direction.IN],
                       optional_inputs=optional_inputs, allow_defaults=True)
    return tuple(projections[Direction.IN])


def task_signature_boundary(contract, signature):
    """Recover endpoint-preserving projections from a frozen task signature."""

    if type(contract) is not TaskContract:
        raise TypeError("a signature boundary needs one exact TaskContract")
    if type(signature) is not ProcedureSignature:
        raise TypeError("a signature boundary needs one exact ProcedureSignature")
    by_name, projections = _endpoint_projections(
        contract, signature.input_names, signature.output_names)
    if set(by_name[Direction.IN]) != set(signature.input_names) \
            or set(by_name[Direction.OUT]) != set(signature.output_names):
        raise Refused(Refusal(
            "behavior-boundary-signature-binding-incomplete",
            "the task endpoints do not cover the frozen procedure signature",
            missing_inputs=sorted(
                set(signature.input_names) - set(by_name[Direction.IN])),
            missing_outputs=sorted(
                set(signature.output_names) - set(by_name[Direction.OUT]))))
    return TaskProcedureBoundary(
        signature,
        tuple(projections[Direction.IN]),
        tuple(projections[Direction.OUT]),
    )


def _binding(name, value_type, fresh):
    """Give every logical leaf one deterministic, collision-free IR path."""

    try:
        return ValueBinding.direct(name, value_type)
    except ValueBindingError as exc:
        if exc.code != "value-binding-structured-path-undeclared":
            raise
    # Structured inputs and outputs use the same declaration-owned leaf map.
    # Lowering adds the transaction-capacity dimension around output variables;
    # it does not mint a second record layout or derive fields from returned values.
    return ValueBinding(name, value_type, tuple(
        (leaf.path, SolverLeafPath(next(fresh)))
        for leaf in layout_of_value_type(value_type).leaves()))


def task_method_boundary(source, file, contract, *, method="process", clock=None,
                         cls=None):
    """Project task endpoints onto one named Behavior method signature.

    Several input endpoints are supported.  Their fields remain grouped by endpoint;
    this function does not claim that partial endpoint acceptance is a complete source
    step.  The accepted-trace adapter makes that decision from a concrete same-slot
    batch and refuses a partial batch unless a future source boundary declares presence.

    ``clock`` selects the endpoints owned by one atomic domain.  With no clock, the
    ordinary transaction-step boundary consumes every task endpoint.
    """

    if type(source) is not str or type(file) is not str or not file:
        raise TypeError("a task procedure boundary needs exact source and file text")
    if type(contract) is not TaskContract:
        raise TypeError("a task procedure boundary needs one exact TaskContract")
    if type(method) is not str or not method:
        raise TypeError("a task procedure boundary needs a nonempty method name")
    if clock is not None and (type(clock) is not str or not clock):
        raise TypeError("a task procedure clock is absent or a nonempty name")
    model = source_model(source, file, cls=cls, step=method)
    source_inputs = tuple(model.inputs)
    source_outputs = tuple(_output_names(model.step))
    by_name, projections = _endpoint_projections(
        contract, source_inputs, source_outputs, clock=clock,
        output_fallback=True)

    _check_input_names(source_inputs, by_name[Direction.IN],
                       optional_inputs=model.optional_inputs)
    if source_outputs:
        missing_outputs = sorted(
            set(source_outputs) - set(by_name[Direction.OUT]))
        extra_outputs = sorted(
            set(by_name[Direction.OUT]) - set(source_outputs))
        if missing_outputs or extra_outputs:
            raise Refused(Refusal(
                "behavior-boundary-output-binding-incomplete",
                "the task output fields and Behavior result fields differ",
                missing=missing_outputs, extra=extra_outputs))
    else:
        source_outputs = tuple(by_name[Direction.OUT])

    # Structured record leaves need compiler-owned physical names.  Choose them
    # against every source spelling rather than reserving a user-visible prefix.
    occupied = {
        node.id for node in ast.walk(model.tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(model.tree) if isinstance(node, ast.Attribute)
    }

    def names():
        index = 0
        while True:
            candidate = f"_value{index}"
            index += 1
            if candidate in occupied:
                continue
            occupied.add(candidate)
            yield candidate

    fresh = names()
    input_rows = tuple(
        (name, _binding(name, by_name[Direction.IN][name], fresh))
        for name in source_inputs)
    output_rows = tuple(
        (name, _binding(name, by_name[Direction.OUT][name], fresh))
        for name in source_outputs)
    return TaskProcedureBoundary(
        ProcedureSignature(input_rows, output_rows),
        tuple(projections[Direction.IN]),
        tuple(projections[Direction.OUT]),
    )


def task_procedure_boundary(source, file, contract, *, cls=None):
    """Project all task endpoints onto the ordinary ``process`` method."""

    return task_method_boundary(source, file, contract, cls=cls)


def _memory_initial_image(
        source, file, contract, memory_environment, memory_horizon, *,
        construction=None, cls=None):
    """Return the exact compact entry image of task-owned memories."""

    if memory_environment is None:
        return None
    from ..core.memory import MemoryEnvironment
    from ..core.refusal import Refusal, Refused

    if type(memory_environment) is not MemoryEnvironment:
        raise TypeError("a Behavior memory binding needs one MemoryEnvironment")
    if type(memory_horizon) is not int or memory_horizon < 1:
        raise TypeError(
            "a Behavior memory binding needs its positive schedule horizon")
    faults = memory_environment.faults_against(
        contract.memories, memory_horizon)
    if faults:
        raise Refused(Refusal(
            "behavior-exact-memory-environment-outside-contract",
            "the concrete memory environment is outside the frozen task and "
            "analysis horizon",
            first=faults[0], count=len(faults)))
    from .construction import ConstructionFacts
    from .extract import source_model

    if construction is not None and type(construction) is not ConstructionFacts:
        raise TypeError("a memory boundary needs exact ConstructionFacts")
    source_state = frozenset(source_model(source, file, cls=cls).state_contract())
    missing = sorted(memory.name for memory in contract.memories
                     if memory.name not in source_state)
    if missing:
        raise Refused(Refusal(
            "behavior-exact-memory-field-unavailable",
            "the Behavior source does not expose every declared external memory as "
            "a finite state field",
            missing=missing))
    if construction is not None:
        projected = frozenset(construction.boundary_fields)
        expected = frozenset(memory.name for memory in contract.memories)
        if projected and projected != expected:
            raise Refused(Refusal(
                "behavior-exact-memory-construction-projection-mismatch",
                "the frozen construction projection and task memory boundary name "
                "different state",
                projected=sorted(projected), expected=sorted(expected)))
    from ..symbolic.uniform_array import UniformArray

    # The run record already stores explicit cells sparsely; every omitted cell is
    # the protocol's deterministic address function.  Preserve that exact shape at
    # the IR boundary instead of allocating the whole address space.
    compact = {}
    for memory in contract.memories:
        run = memory_environment.run(memory.name)
        if run is None:
            raise Refused(Refusal(
                "behavior-exact-memory-run-missing",
                "the concrete memory environment omits a declared memory",
                memory=memory.name))
        compact[memory.name] = UniformArray.memory(
            memory.depth, memory.name, memory.data_width, run.initial)
    return compact


def lower_task_behavior(source, file, contract, bound, *, artifact_identity=None,
                        memory_environment=None, memory_horizon=None,
                        construction=None, cls=None, prove_outputs=True,
                        local_guidance=False):
    """Lower the typed task boundary without a scalar physical-port surrogate.

    Local guidance retains the task/source representation horizon but pins
    state carriers at each audited seed position; it does not infer IR ranges
    over all histories first. Universal output admission must also be deferred.
    Analysis callers retain the default whole-domain proof requirements.
    """

    if type(bound) is not int or type(bound) is bool or bound < 1:
        raise TypeError("a task-bound Behavior lowering needs a positive bound")
    boundary = task_procedure_boundary(source, file, contract, cls=cls)
    from .instance import BehaviorInstance
    from .lowering import lower_source
    from .transaction_goals import TransactionQueries

    boundary_initial = _memory_initial_image(
        source, file, contract, memory_environment, memory_horizon,
        construction=construction, cls=cls)
    lowered = lower_source(
        source, file=file, outputs=boundary.signature.output_names,
        signature=boundary.signature, bound=bound,
        boundary_initial=boundary_initial,
        dynamic_state=tuple(
            memory.name for memory in contract.memories
            if memory_environment is not None),
        construction=construction, cls=cls, source_rules=contract.source_rules,
        prove_outputs=prove_outputs, local_guidance=local_guidance)
    proc = lowered.proc
    bind_input_framing(proc, boundary, contract)
    return (BehaviorInstance(
        proc, proc.state_ranges, source=source, file=file, bound=bound,
        _frontend_catalogue=lowered.catalogue,
        artifact_identity=artifact_identity, class_name=cls,
        source_rules=contract.source_rules,
        memory_interfaces=(contract.memories
                           if memory_environment is not None else ()),
        memory_environment=memory_environment,
        memory_horizon=memory_horizon,
        transaction_goals=(TransactionQueries(contract, boundary)
                           if (contract.observation_goals or contract.transaction_monitors
                               or any(rule.queryable("behavior") for rule in contract.latency_goals)) else None)),
            boundary)


def bind_input_framing(proc, boundary, contract):
    """Retain task preconditions with the scalar input projection that owns them."""
    projections = {projection.endpoint: projection.fields for projection in boundary.inputs}
    proc.input_framing = tuple(
        (endpoint.framing, projections[endpoint.name]) for endpoint in contract.inputs()
        if endpoint.framing is not None and endpoint.name in projections)
    if any(logical not in proc.inputs for framing, projection in proc.input_framing
           for public, logical in projection if public in framing.fields):
        raise Refused(Refusal("behavior-framing-input-missing",
                              "a frame marker has no scalar IR input binding"))
