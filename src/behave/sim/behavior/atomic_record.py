"""Decode one concrete multi-clock run into the atomic Behavior trace domain.

This is the task-bound record adapter.  It does not choose a schedule or alter the
concrete result: the exact instance already retains the schedule selected from the
concrete stimulus pool.  Accepted endpoint batches are mapped through the same
``TaskProcedureBoundary`` used by generation and lowering; silence stays silence and a
partial procedure input product is a typed refusal.
"""

from __future__ import annotations

from ..core.analysis import RecordAudit, audit_environment_assumptions
from ..core.catalogue import CatalogueManifest
from ..core.compare import observed_transfers_of
from ..core.instance import BoundedInstance, endpoint_batch_rows
from ..core.refusal import Refused
from ..core.run_record import RunResult
from ..core.stimulus import Completion
from ..symbolic import typecheck as types
from .atomic_exact import AtomicBehaviorInstance, _input_cells, _invoke_name
from .task_boundary import TaskBoundaryError, accepted_input_valuation

__all__ = ["audit_atomic_record"]


def _at_path(value, path):
    for index in path:
        value = value[index]
    return value


def _row_inputs(component, logical):
    """Encode a complete logical endpoint product into atomic scalar row cells."""

    try:
        physical = component.proc.semantics.validate_inputs(logical)
    except ValueError as exc:
        raise TaskBoundaryError(
            "behavior-boundary-input-value-invalid",
            f"an accepted input product is outside its procedure domain: {exc}") \
            from None
    cells = {}
    for cell, variable, path, leaf_kind, _rng in _input_cells(component):
        value = _at_path(physical[variable], path)
        # Atomic rows are the common finite integer carrier.  Preserve the exact
        # Boolean distinction at the logical boundary and use its declared 0/1
        # image only here.
        if leaf_kind == types.BOOLEAN:
            if type(value) is not bool:
                raise TaskBoundaryError(
                    "behavior-boundary-input-value-invalid",
                    "a Boolean input leaf is not an exact Boolean")
            value = int(value)
        elif type(value) is not int:
            raise TaskBoundaryError(
                "behavior-boundary-input-value-invalid",
                "an Integer input leaf is not an exact integer")
        cells[cell] = value
    return cells


def _raw_output_streams(instance, task, trace):
    projections = {
        endpoint: fields
        for component in instance.components.values()
        for endpoint, fields in component.output_endpoints
    }
    streams = {endpoint: [] for endpoint in projections}
    for endpoint, fields in projections.items():
        for payload in observed_transfers_of(task, trace, endpoint):
            if len(payload) != len(fields):
                raise TaskBoundaryError(
                    "behavior-boundary-output-payload-arity",
                    "an accepted output payload has another field arity",
                    endpoint=endpoint, expected=len(fields),
                    got=len(payload))
            streams[endpoint].append({
                public: value
                for (public, _logical), value in zip(fields, payload)
            })
    return streams


def _exact_output_streams(instance, outputs):
    projections = {
        endpoint: tuple(public for public, _logical in fields)
        for component in instance.components.values()
        for endpoint, fields in component.output_endpoints
    }
    streams = {endpoint: [] for endpoint in projections}
    for slot, output in enumerate(outputs):
        for transaction in endpoint_batch_rows(output):
            endpoint, payload = next(iter(transaction.items()))
            if endpoint not in projections:
                raise TaskBoundaryError(
                    "behavior-boundary-output-endpoint-unknown",
                    "the exact atomic replay emitted an undeclared endpoint",
                    slot=slot, endpoint=endpoint)
            expected = set(projections[endpoint])
            if set(payload) != expected:
                raise TaskBoundaryError(
                    "behavior-boundary-output-product-incomplete",
                    "the exact atomic endpoint payload has another field product",
                    slot=slot, endpoint=endpoint,
                    missing=sorted(expected - set(payload)),
                    extra=sorted(set(payload) - expected))
            streams[endpoint].append(dict(payload))
    return streams


def audit_atomic_record(result, exact, task=None):
    """Audit one raw multi-clock run against its fixed atomic exact instance."""

    if type(result) is not RunResult:
        return RecordAudit.refused(
            "behavior-record-not-a-run-result",
            f"the concrete entry is a {type(result).__name__}, not a RunResult")
    if type(exact) is not BoundedInstance \
            or type(exact.instance) is not AtomicBehaviorInstance:
        return RecordAudit.refused(
            "behavior-atomic-record-exact-instance-invalid",
            "the atomic record audit requires a bound AtomicBehaviorInstance")
    if task is None or getattr(getattr(task, "contract", None), "digest", None) \
            != exact.domain.task_contract:
        return RecordAudit.refused(
            "behavior-record-task-unbound",
            "the atomic record audit is not bound to the exact concrete task")
    expected_memory = exact.instance.memory_environment
    actual_memory = result.stimulus.memory_environment
    same_memory = (
        expected_memory is None and actual_memory is None
        or expected_memory is not None and actual_memory is not None
        and expected_memory.digest == actual_memory.digest
        and expected_memory.as_dict() == actual_memory.as_dict()
    )
    if not same_memory:
        return RecordAudit.refused(
            "behavior-record-memory-environment-mismatch",
            "the concrete run and exact atomic Behavior relation use different "
            "external-memory environments")
    if not result.ran:
        return RecordAudit.refused(
            "behavior-record-run-unavailable",
            f"the raw run has no trace: {result.run_failure or 'no reason recorded'}")
    if result.trace.completion is Completion.INCOMPLETE:
        return RecordAudit.refused(
            "behavior-record-run-incomplete",
            "the raw run is incomplete and has no complete atomic trace")
    instance = exact.instance
    stimulus = result.trace.stimulus
    schedule = stimulus.clock_schedule
    if schedule is None or schedule.digest != instance.schedule.digest \
            or schedule.as_dict() != instance.schedule.as_dict():
        return RecordAudit.outside(
            "the raw run and atomic exact instance use different global schedules")
    if len(result.trace.records) != len(instance.schedule.slots):
        return RecordAudit.refused(
            "behavior-record-slot-count-mismatch",
            "the raw record has another number of global event slots")

    environment = audit_environment_assumptions(
        task.contract, result.stimulus, result.trace, artifact_kind="behavior")
    if environment is not None:
        return environment

    try:
        rows = atomic_rows_from_records(instance, result.trace.records)
    except TaskBoundaryError as exc:
        if exc.code == "behavior-atomic-record-inactive-domain":
            return RecordAudit.outside(str(exc))
        return RecordAudit.refused(exc.code, str(exc))
    except Refused as exc:
        if exc.refusal.kind == "behavior-atomic-record-acceptance-mismatch":
            return RecordAudit.inconsistent(exc.refusal.why)
        return RecordAudit.refused(exc.refusal.kind, exc.refusal.why, **exc.refusal.detail)

    return _audit_rows(result, exact, task, rows)


def atomic_rows_from_records(instance, records):
    """Map accepted transfers for both local guidance and bounded record audit."""
    if len(records) != len(instance.schedule.slots):
        raise TaskBoundaryError("behavior-record-slot-count-mismatch",
                                "the record has another number of global slots")
    rows = []
    observed_invocations = []
    all_inputs = {
        endpoint
        for component in instance.components.values()
        for endpoint, _fields in component.input_endpoints
    }
    for position, (event, record) in enumerate(zip(
            instance.schedule.slots, records)):
        row = {name: bounds[0] for name, bounds in instance.contract().items()}
        observed = {}
        accepted_inputs = {
            endpoint: payload for endpoint, payload in record.accepted
            if endpoint in all_inputs
        }
        for domain, component in instance.components.items():
            ordinary = domain in event.events and domain not in event.resets
            domain_endpoints = {
                endpoint for endpoint, _fields in component.input_endpoints
            }
            batch = {
                endpoint: payload
                for endpoint, payload in accepted_inputs.items()
                if endpoint in domain_endpoints
            }
            logical = accepted_input_valuation(component.boundary, batch)
            if logical is not None and not ordinary:
                raise TaskBoundaryError("behavior-atomic-record-inactive-domain",
                    f"slot {position} accepts input in inactive domain {domain!r}")
            if component.boundary.inputs:
                invoke = logical is not None
                observed[domain] = invoke
            else:
                # A clock-only handler has no accepted input transfer from which
                # to observe invocation.  Its old-state readiness is derived by
                # the exact semantic product below, never guessed from the edge.
                invoke = False
            row[_invoke_name(domain)] = int(invoke)
            if logical is not None:
                row.update(_row_inputs(component, logical))
        rows.append(row)
        observed_invocations.append(observed)

    return instance.rows_from_record(rows, observed_invocations)


def _audit_rows(result, exact, task, rows):
    instance = exact.instance
    wrong = exact.validate(rows)
    if wrong:
        return RecordAudit.outside(str(wrong[0]))
    replayed, witness = instance.replay_witness(rows, exact.domain.digest())
    if replayed.broken or witness is None:
        return RecordAudit.refused(
            "behavior-record-exact-replay-failed",
            "the atomic exact instance could not replay the decoded record: "
            f"{replayed.broken or 'no witness'}")
    manifest = instance.catalogue_manifest()
    if type(manifest) is not CatalogueManifest:
        return RecordAudit.refused(
            "behavior-record-catalogue-unavailable",
            "the atomic exact instance did not retain its frozen catalogue")

    raw_goal_values = [
        (goal, reached)
        for record in result.trace.records
        for goal, reached in record.hits
    ]
    unreadable = sorted({goal for goal, reached in raw_goal_values
                         if type(reached) is not bool})
    if unreadable:
        return RecordAudit.refused(
            "behavior-record-hit-observation-unreadable",
            "the raw atomic goal channel contains a non-Boolean observation: "
            + ", ".join(unreadable[:8]))
    required = {
        goal_id for goal_id, goal in manifest.items()
        if goal.provider == "python-structural"
        and goal.evidence == "slot-record"
    }
    recorded = {goal for goal, _reached in raw_goal_values}
    missing = sorted(required - recorded)
    if missing:
        return RecordAudit.refused(
            "behavior-record-hit-observation-unavailable",
            "the raw run omits declared atomic goal bits: "
            + ", ".join(missing[:8]))
    undeclared = sorted(set(replayed.hits) - set(manifest))
    if undeclared:
        return RecordAudit.refused(
            "behavior-record-exact-hit-outside-catalogue",
            "the atomic exact replay emitted undeclared goals: "
            + ", ".join(undeclared[:8]))
    try:
        raw_outputs = _raw_output_streams(instance, task, result.trace)
        exact_outputs = _exact_output_streams(instance, replayed.outputs)
    except (TaskBoundaryError, TypeError, ValueError) as exc:
        code = getattr(exc, "code", "behavior-atomic-output-envelope-invalid")
        return RecordAudit.refused(code, str(exc))
    if raw_outputs != exact_outputs:
        return RecordAudit.inconsistent(
            "the raw and exact atomic output streams differ by endpoint")
    raw_hits = {goal for goal, reached in raw_goal_values if reached}
    if instance.observations is not None:
        report = instance.observations.native.check(result.stimulus, result.trace)
        if report.unresolved:
            return RecordAudit.refused("behavior-atomic-transaction-observation-unreadable",
                                      "the raw atomic transaction monitor has unreadable observations")
        raw_hits.update(hit.goal for hit in report.hits)
        raw_hits.update(violation.goal for violation in report.violations)
    if raw_hits != set(replayed.hits):
        return RecordAudit.inconsistent(
            "the raw and exact atomic goal-hit sets differ")
    return RecordAudit.admitted(witness)
