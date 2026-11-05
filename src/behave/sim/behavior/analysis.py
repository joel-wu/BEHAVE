"""Audit a concrete transaction record into a Behavior input trace.

This is the frontend-specific side of ``core.analysis``.  It is intentionally small:
the common coordinator owns ordering and the Behavior exact instance owns replay and
symbolic audits.  This adapter only interprets the task's endpoint bindings, checks the
raw transaction/output/hit record against that replay, and returns the core's typed
four-way audit result.
"""

from __future__ import annotations

from ..core.analysis import RecordAudit, audit_environment_assumptions
from ..core.catalogue import CatalogueManifest
from ..core.compare import observed_transfers_of
from ..core.instance import BoundedInstance
from ..core.refusal import Refused
from ..core.run_record import RunResult
from ..core.stimulus import Completion
from .instance import BehaviorInstance

__all__ = ["audit_record"]


def audit_record(result, exact, task=None):
    """Return the Behavior trace denoted by one ``RunResult``.

    ``task`` is the concrete evaluation's admitted task.  The public ``api.analyse``
    binds it when selecting this adapter; callers invoking the adapter directly must
    provide it explicitly.
    """
    if type(result) is not RunResult:
        return RecordAudit.refused(
            "behavior-record-not-a-run-result",
            f"the concrete entry is a {type(result).__name__}, not a RunResult")
    if type(exact) is not BoundedInstance \
            or type(exact.instance) is not BehaviorInstance:
        return RecordAudit.refused(
            "behavior-record-exact-instance-invalid",
            "the Behavior record audit requires a bound BehaviorInstance")
    if task is None or getattr(getattr(task, "contract", None), "digest", None) \
            != exact.domain.task_contract:
        return RecordAudit.refused(
            "behavior-record-task-unbound",
            "the Behavior record audit was not bound to the exact concrete task")
    expected_memory = exact.instance.memory_environment
    actual_memory = result.stimulus.memory_environment
    if actual_memory is not None:
        # The relation binds the run expanded to its memory horizon; the
        # concrete run carries that same run with its ready policy unexpanded.
        horizon = exact.instance.memory_horizon
        actual_memory = actual_memory.materialize(
            exact.domain.bound if horizon is None else horizon)
    same_memory = (
        expected_memory is None and actual_memory is None
        or expected_memory is not None and actual_memory is not None
        and expected_memory.digest == actual_memory.digest
        and expected_memory.as_dict() == actual_memory.as_dict()
    )
    if not same_memory:
        return RecordAudit.refused(
            "behavior-record-memory-environment-mismatch",
            "the concrete run and exact Behavior relation use different external-"
            "memory environments")
    if not result.ran:
        return RecordAudit.refused(
            "behavior-record-run-unavailable",
            f"the raw run has no trace: {result.run_failure or 'no reason recorded'}")
    if result.trace.completion is Completion.INCOMPLETE:
        return RecordAudit.refused(
            "behavior-record-run-incomplete",
            "the raw run is incomplete, so it cannot establish a complete semantic "
            "trace")

    environment = audit_environment_assumptions(
        task.contract, result.stimulus, result.trace, artifact_kind="behavior")
    if environment is not None:
        return environment

    signature = getattr(exact.instance.proc, "signature", None)
    try:
        from .task_boundary import (
            TaskBoundaryError,
            accepted_input_valuation,
            full_output_streams,
            project_source_output_streams,
            task_signature_boundary,
        )

        boundary = task_signature_boundary(task.contract, signature)
    except (Refused, TypeError, ValueError) as exc:
        refusal = getattr(exc, "refusal", None)
        return RecordAudit.refused(
            (refusal.kind if refusal is not None
             else "behavior-record-input-binding-mismatch"),
            (refusal.why if refusal is not None else str(exc)))

    rows = []
    if not boundary.inputs:
        # A free-running Behavior step is one non-reset event slot.
        rows = [{} for slot in result.trace.stimulus.slots if not slot.reset]
    else:
        input_endpoints = {row.endpoint for row in boundary.inputs}
        if len(result.trace.records) != len(result.trace.stimulus.slots):
            return RecordAudit.refused(
                "behavior-record-slot-count-mismatch",
                "the raw record and stimulus have different slot counts")
        for slot, record in zip(
                result.trace.stimulus.slots, result.trace.records):
            if slot.reset:
                continue
            accepted = {
                endpoint: payload for endpoint, payload in record.accepted
                if endpoint in input_endpoints
            }
            try:
                row = accepted_input_valuation(boundary, accepted)
            except TaskBoundaryError as exc:
                return RecordAudit.refused(exc.code, str(exc))
            if row is not None:
                rows.append(row)

    wrong = exact.validate(rows)
    if wrong:
        return RecordAudit.outside(str(wrong[0]))
    replayed, witness = exact.instance.replay_witness(
        rows, exact.domain.digest())
    if replayed.broken:
        return RecordAudit.refused(
            "behavior-record-exact-replay-failed",
            f"the exact Behavior could not replay the decoded record: "
            f"{replayed.broken}")
    if witness is None:
        return RecordAudit.refused(
            "behavior-record-witness-unavailable",
            "the exact Behavior replay completed without a complete finite witness")
    manifest = exact.instance.catalogue_manifest()
    if type(manifest) is not CatalogueManifest:
        return RecordAudit.refused(
            "behavior-record-catalogue-unavailable",
            "the exact Behavior did not retain the CatalogueManifest frozen before "
            "generation")
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
            "the raw Behavior goal channel contains values other than exact Boolean "
            "bits: " + ", ".join(unreadable[:8]))
    required_bits = {
        goal_id for goal_id, goal in manifest.items()
        if goal.provider == "python-structural"
        and goal.evidence == "slot-record"
    }
    recorded_bits = {goal for goal, _reached in raw_goal_values}
    missing_bits = sorted(required_bits - recorded_bits)
    if missing_bits:
        return RecordAudit.refused(
            "behavior-record-hit-observation-unavailable",
            "the raw run does not expose a Boolean observation for every Behavior "
            "goal in the frozen manifest: " + ", ".join(missing_bits[:8]))
    undeclared = sorted(set(replayed.hits) - set(manifest))
    if undeclared:
        return RecordAudit.refused(
            "behavior-record-exact-hit-outside-catalogue",
            "the exact Behavior emitted goal IDs outside the manifest frozen before "
            "generation: " + ", ".join(undeclared[:8]))

    raw_batches = [
        {row.endpoint: payload}
        for row in boundary.outputs
        for payload in observed_transfers_of(
            task, result.trace, row.endpoint)
    ]
    exact_transactions = [
        transaction for step in replayed.outputs
        for transaction in step.transactions
    ]
    try:
        raw_outputs = project_source_output_streams(boundary, raw_batches)
        exact_outputs = full_output_streams(boundary, exact_transactions)
    except TaskBoundaryError as exc:
        return RecordAudit.refused(exc.code, str(exc))
    if raw_outputs != exact_outputs:
        return RecordAudit.inconsistent(
            "the raw and exact Behavior output transaction streams differ by "
            "endpoint")

    raw_hits = {goal for goal, reached in raw_goal_values if reached}
    if exact.instance.transaction_goals is not None:
        # This reading consumes the raw accepted/output record, not the IR
        # witness. The same endpoint streams were independently checked above.
        report = exact.instance.transaction_goals.native_monitor.check(result.stimulus, result.trace)
        if report.unresolved:
            return RecordAudit.refused(
                "behavior-record-transaction-observation-unreadable",
                "a declared transaction predicate is unreadable in the raw record")
        raw_hits.update(hit.goal for hit in report.hits)
        raw_hits.update(violation.goal for violation in report.violations)
    if raw_hits != set(replayed.hits):
        return RecordAudit.inconsistent(
            "the raw and exact Behavior goal-hit sets differ")
    return RecordAudit.admitted(witness)
