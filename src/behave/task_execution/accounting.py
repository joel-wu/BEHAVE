"""One sealed accounting reader for task consumers, independent of eval/train.

The supervision record chooses the billing authority. Solver-received turns
are never substituted for provider-owned usage after a lost reply. Reads use
immutable prefixes, not live journals, and never dispatch work.
"""

from __future__ import annotations

from dataclasses import dataclass

from behave.agent.journal import Event, load_chain
from behave.agent.model_ledger import validate_provider_configuration
from behave.agent.process_worker import WORKER_LAUNCH_FIELDS
from behave.agent.trajectory import CostLedger, Trajectory, trajectory_of
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json, version

from .result import TaskResult
from .solver import SolverJob, bind_solver_policy

SCHEMA = "behave.attempt-accounting/3"
PHASES = ("solver", "supervision", "provider", "development", "judge")


@dataclass(frozen=True, slots=True)
class AttemptAccounting:
    trajectory: Trajectory
    billing: CostLedger
    billing_source: str
    job: BlobRef
    model_admission: str | None


@dataclass(frozen=True, slots=True)
class AccountingIdentity:
    """Supervised job/admission binding, not an audited trajectory or bill."""

    job: BlobRef
    model_admission: str | None


@dataclass(frozen=True, slots=True)
class _AccountingEvidence:
    raw: bytes
    row: dict
    identity: AccountingIdentity
    billing_source: str
    solver: tuple[Event, ...]
    provider: tuple[Event, ...]


def encode_accounting(*, attempt_id: str, heads: dict, trajectory: Trajectory,
                      billing: CostLedger, billing_source: str) -> bytes:
    if billing_source not in {"solver", "provider"}:
        raise ValueError("unknown billing source")
    fields(heads, set(PHASES))
    return canonical_json({
        "schema": SCHEMA, "attempt_id": attempt_id, "heads": heads, "billing_source": billing_source,
        "charges": [c.as_dict() for c in billing.charges], "cost_by_currency": thaw_json(billing.cost_by_currency()),
        "unpriced_dispatches": billing.unpriced_dispatches, "unresolved_dispatches": billing.unresolved_dispatches,
        "turns": [{"turn": t.turn, "request": t.request.as_dict(), "response": t.response.as_dict(),
                   "raw_response": t.raw_response.as_dict(), "training_status": t.training_status,
                   "exclusion": t.exclusion} for t in trajectory.turns],
    })


def _configuration(events: tuple[Event, ...], kind: str, reader: ContentReader) -> dict | None:
    if not events:
        return None
    if events[0].kind != kind:
        raise RecordError("accounting-identity-mismatch", "unexpected ledger initialization")
    row = fields(thaw_json(events[0].payload), {"configuration"})
    config = parse_json(read_verified(reader, BlobRef.from_dict(row["configuration"])))
    expected = {"schema", "spec", "messages", "tools", "workspace"} if kind == "initialized" else {
        "schema", "spec", "command", "admission"}
    if kind == "initialized" and type(config) is dict and "workspace_storage" in config:
        expected.add("workspace_storage")
        if config["workspace_storage"] != "local-final-only":
            raise RecordError("accounting-configuration-invalid", "unknown workspace storage mode")
    if kind == "initialized" and type(config) is dict and "runtime_turn_status" in config:
        expected.add("runtime_turn_status")
        if config["runtime_turn_status"] is not True:
            raise RecordError("accounting-configuration-invalid", "unknown runtime turn status mode")
    fields(config, expected)
    version(config["schema"], "behave.attempt/5" if kind == "initialized" else "behave.model-relay/1")
    return config


def _load_evidence(reader: ContentReader, result: TaskResult, *, verify_artifacts: bool) -> _AccountingEvidence:
    raw = read_verified(reader, result.accounting)
    row = fields(parse_json(raw), {"schema", "attempt_id", "heads", "charges", "cost_by_currency",
                                  "unpriced_dispatches", "unresolved_dispatches", "turns", "billing_source"})
    version(row["schema"], SCHEMA)
    heads = {phase: None if value is None else BlobRef.from_dict(value)
             for phase, value in fields(row["heads"], set(PHASES)).items()}
    if row["attempt_id"] != result.attempt_id or heads["solver"] != result.solver_head:
        raise RecordError("accounting-identity-mismatch", "accounting belongs to another solver prefix")

    supervision = load_chain(reader, heads["supervision"], verify_artifacts=verify_artifacts)
    if not supervision or supervision[0].kind != "solver_requested":
        raise RecordError("accounting-identity-mismatch", "accounting lacks its solver launch")
    launch = fields(thaw_json(supervision[0].payload), set(WORKER_LAUNCH_FIELDS))
    job_ref = BlobRef.from_dict(launch["job"])
    job = SolverJob.from_bytes(read_verified(reader, job_ref), reader)
    if (job.attempt.attempt_id, job.task.task_id, job.task.release, job.task.revision) != (
            result.attempt_id, result.task_id, result.release, result.revision):
        raise RecordError("accounting-identity-mismatch", "launch belongs to another task or attempt")
    source = "solver" if launch["model_admission"] is None else "provider"
    if launch["model_admission"] is not None:
        text(launch["model_admission"], "model admission identity")
    if row["billing_source"] != source or (source == "solver" and heads["provider"] is not None):
        raise RecordError("accounting-source-mismatch", "billing source differs from supervised execution")

    solver = load_chain(reader, heads["solver"], verify_artifacts=verify_artifacts)
    provider = load_chain(reader, heads["provider"], verify_artifacts=verify_artifacts)
    solver_config = _configuration(solver, "initialized", reader)
    provider_config = _configuration(provider, "broker_initialized", reader)
    if (solver_config is not None and
            canonical_json(solver_config["spec"]) != canonical_json(bind_solver_policy(job.task, job.attempt).as_dict())):
        raise RecordError("accounting-identity-mismatch", "ledger belongs to another attempt policy")
    # Even a pre-dispatch cancellation has an initialized provider ledger.
    # Its absence is missing evidence, not evidence of a free invocation.
    if source == "provider" and provider_config is None:
        raise RecordError("accounting-source-mismatch", "provider-owned execution lacks its ledger")
    if provider_config is not None:
        validate_provider_configuration(provider_config, spec=job.attempt, command=job.model,
                                        admission=launch["model_admission"], mismatch_code="accounting-identity-mismatch")
    return _AccountingEvidence(raw, row, AccountingIdentity(job_ref, launch["model_admission"]),
                               source, solver, provider)


def load_accounting_identity(reader: ContentReader, result: TaskResult) -> AccountingIdentity:
    """Check sealed launch/policy identity without loading conversation payloads.

    For fenced recovery of producer-owned records, not billing or training-token
    verification. Journal hashes, result/job bindings and ledger configurations
    are checked; use load_accounting for the full trajectory and cost audit.
    """
    return _load_evidence(reader, result, verify_artifacts=False).identity


def accounting_artifacts(reader: ContentReader, result: TaskResult) -> tuple[BlobRef, ...]:
    """Inventory sealed evidence without rereading conversation payloads.

    This includes every accounting phase, not just the current task journal:
    a rejudgment keeps the original solver/provider heads in its accounting.
    Transfer these explicit references before committing an independent copy.
    Full trajectory and billing verification remains ``load_accounting``.
    """
    evidence = _load_evidence(reader, result, verify_artifacts=False)
    refs = [result.accounting]
    for phase in PHASES:
        if phase == "solver":
            events = evidence.solver
        elif phase == "provider":
            events = evidence.provider
        else:
            head = evidence.row["heads"][phase]
            events = load_chain(reader, None if head is None else BlobRef.from_dict(head),
                                verify_artifacts=False)
        for event in events:
            refs.extend((BlobRef.of(event.to_bytes()), *event.artifacts))
    return tuple(dict.fromkeys(refs))


def load_accounting(reader: ContentReader, result: TaskResult) -> AttemptAccounting:
    """Audit payloads, exact billing and trajectory against sealed execution."""
    evidence = _load_evidence(reader, result, verify_artifacts=True)
    view = trajectory_of(evidence.solver, reader)
    billing = (view.billing if evidence.billing_source == "solver"
               else trajectory_of(evidence.provider, reader).billing)
    expected = encode_accounting(attempt_id=result.attempt_id, heads=evidence.row["heads"], trajectory=view,
                                 billing=billing, billing_source=evidence.billing_source)
    if evidence.raw != expected:
        raise RecordError("accounting-summary-mismatch", "summary differs from the sealed execution history")
    return AttemptAccounting(view, billing, evidence.billing_source,
                             evidence.identity.job, evidence.identity.model_admission)
