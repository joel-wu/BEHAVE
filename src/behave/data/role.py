"""Closed process inputs for construction roles; no executable role callbacks.

Manifests reference verified content, never mutable source paths. The fixed worker
uses the existing role views; a reader's model does not receive the private draft
closure used by the trusted loader. These records grant no release authority.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptResult, AttemptSpec
from behave.agent.journal import AttemptJournal, load_chain
from behave.agent.model_ledger import validate_provider_configuration
from behave.agent.process_model import ModelCommand, ProcessModelBackend
from behave.agent.process_worker import (
    WORKER_LAUNCH_FIELDS,
    WorkerChannels,
    settled_attempt,
    supervise_worker,
    worker_receipt,
)
from behave.agent.trajectory import CostLedger, trajectory_of
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json, version
from behave.core.storage import DirectoryStore
from behave.improve.acquisition_records import AcquisitionInputs
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.testbench import TBPolicy

from .construction import run_author, run_reader
from .drafts import draft_closure, load_draft, load_scope, scope_closure
from .role_policy import RolePolicy
from .source import review_check, review_inputs, run_source_review
from .synthesis_records import PhaseReceipt

if TYPE_CHECKING:
    from behave.isolation.model import ResourceLimits


@dataclass(frozen=True, slots=True)
class AuthorInputs:
    task_id: str
    scope: BlobRef
    judge_policy: BlobRef
    parent: BlobRef | None = None
    feedback: str = ""
    check: BlobRef | None = None
    source_review: BlobRef | None = None
    reader_findings: BlobRef | None = None
    construction_experience: BlobRef | None = None
    role: ClassVar[str] = "task_author"

    def __post_init__(self) -> None:
        text(self.task_id, "task id")
        if type(self.scope) is not BlobRef or type(self.judge_policy) is not BlobRef:
            raise TypeError("author requires pinned scope and judge policy")
        if self.parent is not None and type(self.parent) is not BlobRef:
            raise TypeError("parent must be a draft reference")
        if type(self.feedback) is not str:
            raise TypeError("feedback must be text")
        for ref in (self.check, self.source_review, self.reader_findings, self.construction_experience):
            if ref is not None and type(ref) is not BlobRef:
                raise TypeError("repair evidence requires immutable references")
        if (self.check is not None and self.parent is None) or (self.source_review is not None and self.check is None):
            raise ValueError("repair evidence requires its parent draft and check")
        if self.reader_findings is not None and (self.parent is None or self.check is not None):
            raise ValueError("findings-only repair requires a parent and no comparison")

    def as_dict(self) -> dict:
        row = {"role": self.role, "task_id": self.task_id, "scope": self.scope.as_dict(),
                "judge_policy": self.judge_policy.as_dict(), "feedback": self.feedback,
                "parent": None if self.parent is None else self.parent.as_dict()}
        if self.check is not None:
            row.update(check=self.check.as_dict(), source_review=None if self.source_review is None
                       else self.source_review.as_dict())
        if self.reader_findings is not None:
            row["reader_findings"] = self.reader_findings.as_dict()
        if self.construction_experience is not None:
            row["construction_experience"] = self.construction_experience.as_dict()
        return row


@dataclass(frozen=True, slots=True)
class ReaderInputs:
    draft: BlobRef
    parent: BlobRef | None = None
    feedback: str = ""
    role: ClassVar[str] = "spec_reader"

    def __post_init__(self) -> None:
        if type(self.draft) is not BlobRef:
            raise TypeError("reader requires a draft reference")
        if self.parent is not None and type(self.parent) is not BlobRef:
            raise TypeError("reader repair requires a reconstruction reference")
        if type(self.feedback) is not str:
            raise TypeError("reader feedback must be text")

    def as_dict(self) -> dict:
        row = {"role": self.role, "draft": self.draft.as_dict()}
        if self.parent is not None or self.feedback:
            row.update(parent=None if self.parent is None else self.parent.as_dict(), feedback=self.feedback)
        return row


@dataclass(frozen=True, slots=True)
class SourceReviewInputs:
    draft: BlobRef
    guidance: BlobRef
    check: BlobRef | None = None
    reconstruction: BlobRef | None = None
    rtl_findings: BlobRef | None = None
    role: ClassVar[str] = "source_reviewer"

    def __post_init__(self) -> None:
        if type(self.draft) is not BlobRef or type(self.guidance) is not BlobRef:
            raise TypeError("source review requires pinned draft and guidance")
        if self.check is not None and type(self.check) is not BlobRef:
            raise TypeError("source review requires a pinned check")
        if self.reconstruction is not None and type(self.reconstruction) is not BlobRef:
            raise TypeError("source review requires a reconstruction reference")
        if self.rtl_findings is not None and type(self.rtl_findings) is not BlobRef:
            raise TypeError("source review requires a pinned RTL finding")

    def as_dict(self) -> dict:
        row = {"role": self.role, "draft": self.draft.as_dict(), "guidance": self.guidance.as_dict()}
        if self.check is not None:
            row["check"] = self.check.as_dict()
        if self.reconstruction is not None:
            row["reconstruction"] = self.reconstruction.as_dict()
        if self.rtl_findings is not None:
            row["rtl_findings"] = self.rtl_findings.as_dict()
        return row


@dataclass(frozen=True, slots=True)
class ScopeInputs:
    source: BlobRef
    guidance: BlobRef
    parent: BlobRef | None = None
    feedback: str = ""
    selection: BlobRef | None = None
    brief: BlobRef | None = None
    role: ClassVar[str] = "scope_author"

    def __post_init__(self) -> None:
        if any(type(ref) is not BlobRef for ref in (self.source, self.guidance)):
            raise TypeError("scope author requires pinned source and guidance")
        if self.parent is not None and type(self.parent) is not BlobRef:
            raise TypeError("scope parent requires a pinned proposal")
        if type(self.feedback) is not str:
            raise TypeError("scope feedback must be text")
        if self.selection is not None and type(self.selection) is not BlobRef:
            raise TypeError("scope selection requires a sealed screening receipt")
        if self.brief is not None and type(self.brief) is not BlobRef:
            raise TypeError("construction brief requires a pinned document")

    def as_dict(self) -> dict:
        row = {"role": self.role, "source": self.source.as_dict(), "guidance": self.guidance.as_dict(),
               "parent": None if self.parent is None else self.parent.as_dict(), "feedback": self.feedback}
        if self.selection is not None:
            row["selection"] = self.selection.as_dict()
        if self.brief is not None:
            row["brief"] = self.brief.as_dict()
        return row


@dataclass(frozen=True, slots=True)
class ScopeReviewInputs:
    proposal: BlobRef
    guidance: BlobRef
    role: ClassVar[str] = "scope_reviewer"

    def __post_init__(self) -> None:
        if any(type(ref) is not BlobRef for ref in (self.proposal, self.guidance)):
            raise TypeError("scope reviewer requires pinned proposal and guidance")

    def as_dict(self) -> dict:
        return {"role": self.role, "proposal": self.proposal.as_dict(), "guidance": self.guidance.as_dict()}


@dataclass(frozen=True, slots=True)
class RtlInputs:
    draft: BlobRef
    parent: BlobRef | None = None
    check: BlobRef | None = None
    role: ClassVar[str] = "rtl_author"

    def __post_init__(self):
        if type(self.draft) is not BlobRef:
            raise TypeError("RTL author requires a frozen draft")
        if (self.parent is None) != (self.check is None):
            raise ValueError("RTL repair requires both parent and sealed check")
        if any(ref is not None and type(ref) is not BlobRef for ref in (self.parent, self.check)):
            raise TypeError("RTL repair requires immutable references")

    def as_dict(self):
        return {"role": self.role, "draft": self.draft.as_dict(),
                "parent": None if self.parent is None else self.parent.as_dict(),
                "check": None if self.check is None else self.check.as_dict()}


@dataclass(frozen=True, slots=True)
class SelectionInputs:
    inventory: BlobRef
    guidance: BlobRef
    role: ClassVar[str] = "source_selector"

    def __post_init__(self):
        if any(type(ref) is not BlobRef for ref in (self.inventory, self.guidance)):
            raise TypeError("source selection requires pinned inventory and guidance")

    def as_dict(self):
        return {"role": self.role, "inventory": self.inventory.as_dict(), "guidance": self.guidance.as_dict()}


@dataclass(frozen=True, slots=True)
class DiscoveryInputs:
    guidance: BlobRef
    fetch_policy: BlobRef
    max_fetches: int = defaults.SYN_MAX_FETCHES
    role: ClassVar[str] = "source_discoverer"

    def __post_init__(self):
        if any(type(ref) is not BlobRef for ref in (self.guidance, self.fetch_policy)):
            raise TypeError("discovery requires pinned guidance and operator fetch policy")
        if type(self.max_fetches) is not int or self.max_fetches < 1:
            raise ValueError("discovery fetch count must be positive")

    def as_dict(self):
        return {"role": self.role, "guidance": self.guidance.as_dict(),
                "fetch_policy": self.fetch_policy.as_dict(), "max_fetches": self.max_fetches}


ConstructionInputs = (AuthorInputs | ReaderInputs | SourceReviewInputs | ScopeInputs | ScopeReviewInputs
                      | RtlInputs | SelectionInputs | DiscoveryInputs | AcquisitionInputs)


def _inputs(value: object) -> ConstructionInputs:
    role = value.get("role") if type(value) is dict else None
    if role == AcquisitionInputs.role:
        return AcquisitionInputs.from_dict(value)
    if role == DiscoveryInputs.role:
        row = fields(value, {"role", "guidance", "fetch_policy", "max_fetches"})
        return DiscoveryInputs(BlobRef.from_dict(row["guidance"]), BlobRef.from_dict(row["fetch_policy"]),
                               row["max_fetches"])
    if role == SelectionInputs.role:
        row = fields(value, {"role", "inventory", "guidance"})
        return SelectionInputs(BlobRef.from_dict(row["inventory"]), BlobRef.from_dict(row["guidance"]))
    if role == AuthorInputs.role:
        extended = "check" in value
        row = fields(value, {"role", "task_id", "scope", "judge_policy", "parent", "feedback"}
                     | ({"check", "source_review"} if extended else set())
                     | ({"reader_findings"} if "reader_findings" in value else set())
                     | ({"construction_experience"} if "construction_experience" in value else set()))
        return AuthorInputs(row["task_id"], BlobRef.from_dict(row["scope"]), BlobRef.from_dict(row["judge_policy"]),
                            None if row["parent"] is None else BlobRef.from_dict(row["parent"]), row["feedback"],
                            BlobRef.from_dict(row["check"]) if extended else None,
                            BlobRef.from_dict(row["source_review"]) if row.get("source_review") is not None else None,
                            BlobRef.from_dict(row["reader_findings"]) if "reader_findings" in row else None,
                            BlobRef.from_dict(row["construction_experience"]) if "construction_experience" in row else None)
    if role == ReaderInputs.role:
        row = fields(value, {"role", "draft"} | ({"parent", "feedback"} if "parent" in value else set()))
        return ReaderInputs(BlobRef.from_dict(row["draft"]),
                            None if row.get("parent") is None else BlobRef.from_dict(row["parent"]),
                            row.get("feedback", ""))
    if role == RtlInputs.role:
        row = fields(value, {"role", "draft", "parent", "check"})
        return RtlInputs(BlobRef.from_dict(row["draft"]),
                         None if row["parent"] is None else BlobRef.from_dict(row["parent"]),
                         None if row["check"] is None else BlobRef.from_dict(row["check"]))
    if role == SourceReviewInputs.role:
        row = fields(value, {"role", "draft", "guidance"} | ({"check"} if "check" in value else set())
                     | ({"reconstruction"} if "reconstruction" in value else set())
                     | ({"rtl_findings"} if "rtl_findings" in value else set()))
        return SourceReviewInputs(BlobRef.from_dict(row["draft"]), BlobRef.from_dict(row["guidance"]),
                                  BlobRef.from_dict(row["check"]) if "check" in row else None,
                                  BlobRef.from_dict(row["reconstruction"]) if "reconstruction" in row else None,
                                  BlobRef.from_dict(row["rtl_findings"]) if "rtl_findings" in row else None)
    if role == ScopeInputs.role:
        row = fields(value, {"role", "source", "guidance", "parent", "feedback"}
                     | ({"selection"} if "selection" in value else set())
                     | ({"brief"} if "brief" in value else set()))
        return ScopeInputs(BlobRef.from_dict(row["source"]), BlobRef.from_dict(row["guidance"]),
                           None if row["parent"] is None else BlobRef.from_dict(row["parent"]), row["feedback"],
                           BlobRef.from_dict(row["selection"]) if "selection" in row else None,
                           BlobRef.from_dict(row["brief"]) if "brief" in row else None)
    if role == ScopeReviewInputs.role:
        row = fields(value, {"role", "proposal", "guidance"})
        return ScopeReviewInputs(BlobRef.from_dict(row["proposal"]), BlobRef.from_dict(row["guidance"]))
    raise ValueError("unknown construction role")


@dataclass(frozen=True, slots=True)
class RoleJob:
    inputs: ConstructionInputs
    attempt: AttemptSpec
    model: ModelCommand
    python_limits: ResourceLimits | None = None
    cocotb_policy: TBPolicy | None = None
    log_bytes: int = defaults.LOG_RETAIN_BYTES
    shell_limits: ResourceLimits | None = None
    output_preview_bytes: int | None = defaults.SHELL_OUTPUT_PREVIEW_BYTES

    def __post_init__(self) -> None:
        if type(self.inputs) not in {AuthorInputs, ReaderInputs, SourceReviewInputs, ScopeInputs, ScopeReviewInputs,
                                    RtlInputs, SelectionInputs, DiscoveryInputs, AcquisitionInputs}:
            raise TypeError("construction requires closed role inputs")
        if type(self.attempt) is not AttemptSpec or self.attempt.role != self.inputs.role:
            raise ValueError("attempt and construction role differ")
        self.policy.check_attempt(self.attempt)

    @property
    def policy(self) -> RolePolicy:
        return RolePolicy(self.model, self.python_limits, self.cocotb_policy, self.log_bytes, self.shell_limits,
                          self.output_preview_bytes)

    def check_execution(self) -> None:
        """Historical records remain decodable without granting retired roles execution."""
        if type(self.inputs) in {DiscoveryInputs, SelectionInputs}:
            raise ValueError("legacy discovery/selection agents are retired; use Bash acquisition over the source library")
        self.policy.check_execution()

    def to_bytes(self) -> bytes:
        revision = 2 if type(self.inputs) is AuthorInputs and self.inputs.check is not None else 1
        if type(self.inputs) in {ScopeInputs, ScopeReviewInputs}:
            revision = 3
        if type(self.inputs) is AuthorInputs and self.inputs.reader_findings is not None:
            revision = 4
        if type(self.inputs) is RtlInputs:
            revision = 5
        if type(self.inputs) is SelectionInputs:
            revision = 6
        if type(self.inputs) is ScopeInputs and self.inputs.selection is not None:
            revision = 7
        if type(self.inputs) is DiscoveryInputs:
            revision = 8
        if self.shell_limits is not None or (type(self.inputs) is ScopeInputs and self.inputs.brief is not None):
            revision = 10
        if type(self.inputs) is AcquisitionInputs:
            revision = 11
        if type(self.inputs) is AuthorInputs and self.inputs.construction_experience is not None:
            revision = 12
        return canonical_json({"schema": f"behave.construction-role-job/{revision}", "inputs": self.inputs.as_dict(),
                               "attempt": self.attempt.as_dict(), **self.policy.as_dict()})

    @classmethod
    def from_bytes(cls, payload: bytes) -> RoleJob:
        value = parse_json(payload)
        policy_keys = {"model", "python_limits", "cocotb_policy", "log_bytes"}
        if "shell_limits" in value:
            policy_keys.add("shell_limits")
        if "output_preview_bytes" in value:
            policy_keys.add("output_preview_bytes")
        row = fields(value, {"schema", "inputs", "attempt"} | policy_keys)
        if row["schema"] not in {f"behave.construction-role-job/{n}" for n in range(1, 13)}:
            version(row["schema"], "behave.construction-role-job/12")
        policy = RolePolicy.from_dict({key: row[key] for key in policy_keys})
        result = cls(_inputs(row["inputs"]), AttemptSpec.from_dict(row["attempt"]), policy.model,
                     policy.python_limits, policy.cocotb_policy, policy.log_bytes, policy.shell_limits,
                     policy.output_preview_bytes)
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "role job must be canonical")
        return result


def planned_job(plan, revision: int, role: str, inputs: AuthorInputs | ReaderInputs | SourceReviewInputs) -> RoleJob:
    from .synthesis_records import role_attempt

    policy = getattr(plan.processes, role)
    return RoleJob(inputs, role_attempt(plan, revision, role), policy.model,
                   policy.python_limits, policy.cocotb_policy, policy.log_bytes, policy.shell_limits,
                   policy.output_preview_bytes)


def input_closure(job: RoleJob, reader: ContentReader) -> tuple[BlobRef, ...]:
    """Preflight and pin all role inputs before dispatch, including repair ancestry."""
    job.check_execution()
    inputs = job.inputs
    if type(inputs) is AcquisitionInputs:
        from behave.improve.acquisition import acquisition_view

        _, _, _, refs = acquisition_view(inputs, reader)
    elif type(inputs) is ScopeInputs:
        from .scope import scope_inputs

        _, refs = scope_inputs(reader, inputs.source, inputs.guidance, inputs.parent,
                              feedback=inputs.feedback, selection=inputs.selection, brief=inputs.brief)
    elif type(inputs) is ScopeReviewInputs:
        from .scope import scope_review_inputs

        scope_review_inputs(reader, inputs.proposal, inputs.guidance)
        refs = (*scope_closure(reader, inputs.proposal), inputs.guidance)
    elif type(inputs) is AuthorInputs:
        scope, source, _ = load_scope(reader, inputs.scope)
        if JudgePolicy.from_bytes(read_verified(reader, inputs.judge_policy)).analysis is not None:
            raise ValueError("construction requires a concrete-only judge policy")
        refs = (inputs.scope, scope.source, inputs.judge_policy, *(f.content for f in source.selected.files))
        refs += scope_closure(reader, inputs.scope)
        if inputs.construction_experience is not None:
            read_verified(reader, inputs.construction_experience).decode("utf-8")
            refs += (inputs.construction_experience,)
        if inputs.parent is not None:
            previous = load_draft(reader, inputs.parent)
            if previous.record.scope != inputs.scope or previous.record.task.task_id != inputs.task_id:
                raise RecordError("draft-parent-mismatch", "repair cannot silently change task or scope")
            refs += draft_closure(reader, inputs.parent)
        from .repair_evidence import repair_closure

        refs += repair_closure(inputs, reader)
    else:
        refs = draft_closure(reader, inputs.draft)
        if type(inputs) is RtlInputs:
            from .rtl import role_inputs

            _, _, extra, _ = role_inputs(reader, inputs)
            refs += extra
        if type(inputs) is SourceReviewInputs:
            review_inputs(reader, inputs.draft, inputs.guidance, check=inputs.check,
                          reconstruction=inputs.reconstruction, rtl_findings=inputs.rtl_findings)
            refs += (inputs.guidance,)
            if inputs.rtl_findings is not None:
                from .source import rtl_review_files

                refs += tuple(file.reference.content for file in
                              rtl_review_files(reader, inputs.draft, inputs.rtl_findings))
            if inputs.reconstruction is not None:
                refs += (inputs.reconstruction,)
            if inputs.check is not None:
                report, _, source = review_check(reader, inputs.draft, inputs.check)
                # The reviewer reads the report and diagnostics, not the
                # coordinator's sealed event chain (owned by admission).
                refs += (inputs.check, report.reconstruction,
                         *(f.reference.content for f in source.files), *report.comparison.result.closure)
        if type(inputs) is ReaderInputs and inputs.parent is not None:
            from .construction import reader_inputs
            from .drafts import Reconstruction

            _, files = reader_inputs(reader, inputs.draft, inputs.parent)
            refs += (inputs.parent, *(f.reference.content for f in files))
            parent = Reconstruction.from_bytes(read_verified(reader, inputs.parent))
            if parent.workspace is not None:
                refs += (parent.workspace,)
    return tuple(dict.fromkeys(refs))


def _require(condition, detail):
    if not condition:
        raise RecordError("synthesis-process-mismatch", detail)


def _record(reader, ref):
    return read_verified(reader, ref)


def _configuration(reader, events, kind):
    _require(events and events[0].kind == kind, "unexpected auxiliary ledger initialization")
    row = fields(thaw_json(events[0].payload), {"configuration"})
    return parse_json(_record(reader, BlobRef.from_dict(row["configuration"])))


def _policy_identity(row):
    return AdmissionPolicy(**fields(row, set(AdmissionPolicy.__dataclass_fields__))).identity


def process_billing(reader: ContentReader, phase: PhaseReceipt, expected_job: RoleJob,
                    configuration: Mapping) -> CostLedger:
    """Validate one phase's sealed lifetime and auxiliary identities before reduction."""
    _require(phase.process is not None, "fixed role lacks a process receipt")
    supervision = load_chain(reader, phase.process.supervision)
    receipt = worker_receipt(supervision, namespace="construction")
    CleanupState(receipt["cleanup"]).require_quiescent("construction")
    launch = fields(thaw_json(supervision[0].payload), set(WORKER_LAUNCH_FIELDS))
    job = RoleJob.from_bytes(_record(reader, BlobRef.from_dict(launch["job"])))
    _require(job == expected_job, "worker manifest differs from its planned phase inputs or capabilities")
    model_policy = configuration["model_admission"]
    development_policy = configuration["development_admission"] if job.cocotb_policy is not None else None
    _require((launch["model_admission"], launch["development_admission"]) == (model_policy, development_policy),
             "process resource policies differ from the coordinator")
    for channel, policy in (("provider", model_policy), ("development", development_policy)):
        _require((launch[channel] is None) == (policy is None), "relay path disagrees with admission policy")
        if policy is None:
            _require(getattr(phase.process, channel) is None, "unconfigured relay has an auxiliary ledger")
    role = load_chain(reader, phase.head)
    if role:
        settings = _configuration(reader, role, "initialized")
        spec = AttemptSpec.from_dict(settings["spec"])
        _require(replace(spec, read_only_paths=job.attempt.read_only_paths) == job.attempt
                 and set(spec.read_only_paths) >= set(job.attempt.read_only_paths),
                 "role policy differs from the supervised attempt")
    view = trajectory_of(role, reader)
    provider = load_chain(reader, phase.process.provider)
    if model_policy is not None:
        # A failed supervisor can stop before creating any relay/worker. Missing
        # provider evidence after a worker ran is never interpreted as free usage.
        if not provider:
            _require(not role and receipt["status"] == "supervisor-failed", "provider-owned usage lacks its ledger")
        else:
            validate_provider_configuration(_configuration(reader, provider, "broker_initialized"),
                                            spec=job.attempt, command=job.model, admission=model_policy,
                                            mismatch_code="synthesis-process-mismatch")
    development = load_chain(reader, phase.process.development)
    if development_policy is not None:
        if not development:
            _require(not role and receipt["status"] == "supervisor-failed", "development execution lacks its ledger")
        else:
            config = fields(_configuration(reader, development, "development_initialized"),
                            {"schema", "attempt", "policy", "admission"})
            version(config["schema"], "behave.development-relay/1")
            _require(AttemptSpec.from_dict(config["attempt"]) == job.attempt
                     and canonical_json(config["policy"]) == job.cocotb_policy.to_bytes()
                     and _policy_identity(config["admission"]) == development_policy,
                     "development ledger belongs to another invocation policy")
    return view.billing if model_policy is None else trajectory_of(provider, reader).billing


def run_role_process(
    job: RoleJob, *, reader: ContentReader, journal: AttemptJournal, supervision: AttemptJournal,
    cancelled: Callable[[], bool] | None = None,
    model_pool: ResourcePool | None = None, provider: AttemptJournal | None = None,
) -> AttemptResult:
    """Pin inputs, supervise a fixed role, then require its cleanup witness.

    A normally stopped author without a draft remains unfinished, not a zero
    score. Timeout/cancel/unknown dispatch raise AttemptInterrupted with durable
    evidence. Retrying this function only replays an already settled execution.
    """
    if type(job) is not RoleJob:
        raise TypeError("construction process requires RoleJob")
    job.check_execution()
    channels = WorkerChannels(journal, supervision, model_pool, provider)
    ingresses = (channels.model_ingress(job.attempt, job.model), None)
    packet = job.to_bytes()
    store = journal.objects
    # Once dispatched, local pinned content is authoritative. Never repair a
    # damaged local closure by silently fetching from a mutable upstream again.
    started = supervision.load()
    source = store if started else reader
    references = input_closure(job, source)
    if not started:
        store.publish(source, references)
    reference = store.put(packet)
    return settled_attempt(namespace="construction", spec=job.attempt, journal=journal, supervision=supervision,
        payload=channels.payload(reference), dependencies=(reference, *references),
        launch=lambda: supervise_worker(namespace="construction", module=__name__, attempt=job.attempt,
            model=job.model, journal=journal, reference=reference, log_bytes=job.log_bytes, cancelled=cancelled,
            ingresses=ingresses))


def worker_main() -> None:
    root, objects, digest, size, endpoint, _ = sys.argv[1:]
    store = DirectoryStore(objects)
    job = RoleJob.from_bytes(read_verified(store, BlobRef(digest, int(size))))
    journal = AttemptJournal(root, store)
    try:
        job.check_execution()
        from behave.agent.relay_wire import RelayModelBackend
        from behave.task_execution.shell import development_shell

        shell = development_shell(job.shell_limits, output_preview_bytes=job.output_preview_bytes)
        if type(job.inputs) in {AuthorInputs, ReaderInputs, SourceReviewInputs}:
            from .author_check import behavior_check_shell
            from .drafts import TaskDraft

            policy = (job.inputs.judge_policy if type(job.inputs) is AuthorInputs else
                      TaskDraft.from_bytes(read_verified(store, job.inputs.draft)).task.judge_policy)
            tools = (behavior_check_shell(shell, read_verified(store, policy)),)
        else:
            tools = (shell.tool(),)
        backend = ProcessModelBackend(job.model) if not endpoint else RelayModelBackend(endpoint)
        common = {"reader": store, "journal": journal, "backend": backend, "spec": job.attempt}
        inputs = job.inputs
        if type(inputs) is AcquisitionInputs:
            from behave.improve.acquisition import run_acquisition

            run_acquisition(inputs=inputs, development_tools=tools, **common)
        elif type(inputs) is AuthorInputs:
            from .repair_evidence import repair_files

            run_author(task_id=inputs.task_id, scope=inputs.scope, parent=inputs.parent, feedback=inputs.feedback,
                       judge_policy=read_verified(store, inputs.judge_policy),
                       construction_experience=inputs.construction_experience,
                       development_tools=tools, repair_files=repair_files(inputs, store), **common)
        elif type(inputs) is ReaderInputs:
            run_reader(draft=inputs.draft, parent=inputs.parent, feedback=inputs.feedback,
                       development_tools=tools, **common)
        elif type(inputs) is RtlInputs:
            from .rtl import run_rtl

            run_rtl(inputs=inputs, development_tools=tools, **common)
        elif type(inputs) is ScopeInputs:
            from .scope import run_scope_proposal

            run_scope_proposal(source=inputs.source, guidance=inputs.guidance, parent=inputs.parent,
                               feedback=inputs.feedback, selection=inputs.selection, brief=inputs.brief,
                               development_tools=tools, **common)
        elif type(inputs) is ScopeReviewInputs:
            from .scope import run_scope_review

            run_scope_review(proposal=inputs.proposal, guidance=inputs.guidance, development_tools=tools, **common)
        else:
            run_source_review(draft=inputs.draft, guidance=inputs.guidance, check=inputs.check,
                              reconstruction=inputs.reconstruction, rtl_findings=inputs.rtl_findings,
                              development_tools=tools, cpu_endpoint=shell.cpu_endpoint, **common)
    except AttemptInterrupted as error:
        sys.stdout.buffer.write(canonical_json({"interruption": error.code}))
        raise SystemExit(2) from None
    except Exception as error:  # noqa: BLE001 -- exception messages can contain credentials
        sys.stdout.buffer.write(canonical_json({"interruption": f"construction-worker-{type(error).__name__}"}))
        raise SystemExit(2) from None
