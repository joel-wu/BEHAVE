"""Bounded author/reader/check revisions ending in a review package, not a release.

Agents choose edits/tools within a role. The coordinator owns immutable
handoffs. Each phase keeps its own journal; replay calls the existing recovery
mechanism, never blindly reruns an unsettled model/tool/check request. Trusted
callers must supply bounded model backends/tools (not arbitrary Python callbacks
claimed to be cancellable). Distributed fencing and release policy are separate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from behave.agent.admission import AdmissionPolicy, ResourcePool, shared_pools, validate_policies
from behave.agent.attempt import AttemptInterrupted, AttemptResult
from behave.agent.lifetime import AttemptControl
from behave.agent.model import ModelBackend
from behave.agent.process_worker import worker_receipt
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json, version
from behave.task_execution.comparison import COMPARISON_BACKEND
from behave.task_execution.feedback import comparison_feedback
from behave.task_execution.judge_policy import JudgePolicy

from .checks import check_cleanup
from .construction import run_author, run_reader
from .contract import effective_draft, validate_contract_revision
from .drafts import Reconstruction, draft_closure, load_draft, load_scope, scope_closure
from .escalation import ScopeEscalation
from .evidence import evidence_files
from .phase_journal import RevisionPhases, auxiliary_path, verify_process_seal
from .release_records import ReviewPackage
from .reviews import compare_reconstruction
from .role import AuthorInputs, ReaderInputs, RoleJob, SourceReviewInputs, input_closure, planned_job, run_role_process
from .source import SourceReview, load_review_guidance, review_inputs, run_source_review, validate_review
from .synthesis_records import SynthesisPlan, SynthesisResult, coordinator_for, role_attempt


@dataclass(frozen=True, slots=True)
class SynthesisConfiguration:
    plan: SynthesisPlan
    model_admission: AdmissionPolicy | None = None
    check_admission: AdmissionPolicy | None = None
    development_admission: AdmissionPolicy | None = None

    def __post_init__(self) -> None:
        if type(self.plan) is not SynthesisPlan or self.plan.processes is None:
            raise ValueError("synthesis command requires fixed process roles")
        validate_policies((self.model_admission, self.check_admission, self.development_admission))
        # Historical configurations remain decodable. New execution is checked
        # at preflight/dispatch and only grants the shared Bash capability.

    def to_bytes(self) -> bytes:
        return canonical_json({"schema": "behave.synthesis-configuration/1", "plan": parse_json(self.plan.to_bytes()),
            **{key: None if getattr(self, key) is None else getattr(self, key).as_dict()
               for key in ("model_admission", "check_admission", "development_admission")}})

    def pools(self):
        return shared_pools(**{key: getattr(self, key) for key in
                              ("model_admission", "check_admission", "development_admission")})

    @classmethod
    def from_bytes(cls, payload: bytes) -> SynthesisConfiguration:
        row = fields(parse_json(payload), {"schema", "plan", "model_admission", "check_admission", "development_admission"})
        version(row["schema"], "behave.synthesis-configuration/1")
        policies = {key: None if row[key] is None else AdmissionPolicy(**fields(
            row[key], set(AdmissionPolicy.__dataclass_fields__)))
            for key in ("model_admission", "check_admission", "development_admission")}
        result = cls(SynthesisPlan.from_bytes(canonical_json(row["plan"])), **policies)
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "synthesis configuration must be canonical")
        return result

    def preflight(self, reader: ContentReader) -> None:
        """Verify pinned source/policy inputs without creating a run or dispatching work."""
        plan = self.plan
        if self.development_admission is not None:
            raise ValueError("Syn uses Bash runtime limits, not a separate Cocotb admission pool")
        for role in (plan.processes.author, plan.processes.reader,
                     *(() if plan.processes.source_review is None else (plan.processes.source_review,)),
                     *(() if plan.rtl is None else (plan.rtl.execution,))):
            role.check_execution()
        if plan.preparation is not None:
            from .scope import preparation_closure

            preparation_closure(reader, plan.preparation, scope=plan.scope)
        input_closure(planned_job(plan, 0, "author", AuthorInputs(plan.task_id, plan.scope, plan.judge_policy,
            construction_experience=plan.construction_experience)), reader)
        if plan.source_review is not None:
            load_review_guidance(reader, plan.source_review.guidance)


def execute_role(job: RoleJob, *, run: JournalDirectory, revision: int, role: str,
                 cancelled: Callable[[], bool], model_pool: ResourcePool | None,
                 development_pool: ResourcePool | None) -> AttemptResult:
    if development_pool is not None:
        raise ValueError("Syn development runs in Bash; configure role shell_limits instead of a development pool")
    supervision = run.journal(*auxiliary_path(revision, role, "supervision"))
    try:
        return run_role_process(job, reader=run.objects, journal=run.journal("revisions", f"r{revision}", role),
            supervision=supervision, cancelled=cancelled, model_pool=model_pool,
            provider=None if model_pool is None else run.journal(*auxiliary_path(revision, role, "provider")))
    finally:
        cleanup = CleanupState.UNKNOWN
        try:
            events = supervision.load()
            cleanup = (CleanupState.COMPLETE if not events else
                       CleanupState(worker_receipt(events, namespace="construction")["cleanup"]))
        except (AttemptInterrupted, RecordError):
            pass  # A cold pending/corrupt owner is not permission to reuse capacity.
        finally:
            if model_pool is not None:
                model_pool.quarantine(cleanup)


def _verify_terminal_phases(run: JournalDirectory, result: SynthesisResult) -> BlobRef | None:
    """Recovery and packaging enforce the same seal, regardless of resource pools."""
    from .rtl import synthesis_rtl

    plan = SynthesisPlan.from_bytes(read_verified(run.objects, result.plan))
    events = run.journal("synthesis").load()
    config = BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])
    from behave.core.records import parse_json

    synthesis_rtl(run.objects, plan, result, parse_json(read_verified(run.objects, config)), events)
    review_head = None
    for phase in result.phases:
        if phase.process is not None:
            verify_process_seal(run, phase)
        head, _ = run.seal("revisions", f"r{phase.revision}", phase.role)
        if head != phase.head:
            raise RecordError("synthesis-phase-mismatch", "phase journal changed after termination")
        if phase.role == "source_review" and phase.revision == len(result.drafts) - 1:
            review_head = head
    if result.rtl_head is not None:
        from .rtl import RtlBuildResult, verify_terminal

        child = JournalDirectory(run.directory / "rtl_build")
        head, _ = child.seal("rtl")
        if head != result.rtl_head:
            raise RecordError("synthesis-phase-mismatch", "auxiliary RTL branch changed after termination")
        events = child.journal("rtl").load()
        ref = BlobRef.from_dict(thaw_json(events[-1].payload)["result"])
        verify_terminal(child, RtlBuildResult.from_bytes(read_verified(child.objects, ref)))
    return review_head


def seal_review_package(run: JournalDirectory) -> BlobRef:
    """Seal the terminal run and its latest planned review, including pending runs.

    No review is inferred from an earlier revision, and no role is executed.
    Separate standalone reviews may still be packaged explicitly by a caller.
    Publication always reapplies its own evidence-bound admission policy.
    """
    with run.owner():
        events = run.journal("synthesis").load()
        if not events or events[-1].kind != "synthesis_finished":
            raise RecordError("synthesis-not-terminal", "only a terminal synthesis run can be packaged")
        ref = BlobRef.from_dict(fields(thaw_json(events[-1].payload), {"result"})["result"])
        result = SynthesisResult.from_bytes(read_verified(run.objects, ref))
        review_head = _verify_terminal_phases(run, result)
        head, _ = run.seal("synthesis")
        return run.objects.put(ReviewPackage(head, review_head).to_bytes())


def _recover_terminal(run: JournalDirectory, tape: Tape, plan: SynthesisPlan, plan_ref: BlobRef,
                      config: dict, check_admission: ResourcePool | None) -> SynthesisResult:
    """Recheck all child seals and cleanup even when a result already exists."""
    objects = run.objects
    reference = BlobRef.from_dict(fields(thaw_json(tape.events[-1].payload), {"result"})["result"])
    result = SynthesisResult.from_bytes(read_verified(objects, reference))
    if result.plan != plan_ref:
        raise RecordError("synthesis-plan-mismatch", "terminal package belongs to another plan")
    _verify_terminal_phases(run, result)
    if check_admission is not None:
        for receipt in result.phases:
            if receipt.role == "check":
                journal = run.journal("revisions", f"r{receipt.revision}", "check")
                check_admission.quarantine(check_cleanup(journal))
        if result.rtl_head is not None:
            from .rtl import synthesis_rtl

            auxiliary, _, _ = synthesis_rtl(objects, plan, result, config, tape.events)
            for receipt in auxiliary.phases:
                if receipt.role == "rtl_check":
                    child = JournalDirectory(run.directory / "rtl_build")
                    journal = child.journal("revisions", f"r{receipt.revision}", "rtl_check")
                    check_admission.quarantine(check_cleanup(journal))
    return result


def synthesize(*, plan: SynthesisPlan, reader: ContentReader, run: JournalDirectory,
               author_backend: ModelBackend | None = None, reader_backend: ModelBackend | None = None,
               source_reviewer_backend: ModelBackend | None = None,
               development_tools: tuple[Tool, ...] = (), control: AttemptControl | None = None,
               model_admission: ResourcePool | None = None,
               check_admission: ResourcePool | None = None,
               development_admission: ResourcePool | None = None,
               continuation_from: BlobRef | None = None) -> SynthesisResult:
    """Execute/recover one bounded run. A terminal outcome never silently reopens.

    Planned reviewers see B1, B2 and every completed concrete check, including
    mismatches. Reviewers route repair to the builder or implementer; mismatches
    alone never imply that the builder is wrong. The older unreviewed utility loop retains
    bounded revisions. No result stops without changing the task.
    """
    if type(plan) is not SynthesisPlan or type(run) is not JournalDirectory:
        raise TypeError("synthesis requires SynthesisPlan and JournalDirectory")
    if development_admission is not None:
        raise ValueError("Syn development runs in Bash; configure role shell_limits instead of development_admission")
    if plan.processes is not None:
        if any(b is not None for b in (author_backend, reader_backend, source_reviewer_backend)) or development_tools:
            raise ValueError("fixed process roles cannot mix callback backends or tools")
    else:
        if author_backend is None or reader_backend is None:
            raise ValueError("callback execution requires author and reader backends")
        if (plan.source_review is None) != (source_reviewer_backend is None):
            raise ValueError("source reviewer backend and planned source review must be supplied together")
    if type(development_tools) is not tuple or any(type(t) is not Tool or t.terminal for t in development_tools):
        raise TypeError("development tools must be nonterminal Tool values")
    for pool in (model_admission, check_admission, development_admission):
        if pool is not None and type(pool) is not ResourcePool:
            raise TypeError("admission requires ResourcePool")
    control = control if control is not None else AttemptControl()
    if type(control) is not AttemptControl:
        raise TypeError("synthesis control requires AttemptControl")
    with run.owner():
        objects = run.objects
        plan_bytes = plan.to_bytes()
        plan_ref = objects.put(plan_bytes)
        config = {"coordinator": coordinator_for(plan), "plan": plan_ref.as_dict(),
            "comparison": COMPARISON_BACKEND, "tools": [t.declaration() for t in development_tools],
            "model_admission": None if model_admission is None else model_admission.policy.identity,
            "check_admission": None if check_admission is None else check_admission.policy.identity}
        if plan.processes is not None:
            config["development_admission"] = None if development_admission is None else development_admission.policy.identity
        continuation = None
        if continuation_from is not None:
            from .continuation import seed_continuation

            config["continuation_from"] = continuation_from.as_dict()
            pinned = objects if run.journal("synthesis").load() else reader
            continuation = seed_continuation(pinned, run, continuation_from, plan, config)
        configuration = objects.put(canonical_json(config))
        tape = Tape(run.journal("synthesis"))
        resumed = bool(tape.events)
        if resumed:
            # Authenticate the requested configuration before reading its inputs.
            # The start event already owns their verified closure; never refetch
            # upstream bytes or use an upstream copy to hide local corruption.
            tape.record("synthesis_started", {"configuration": configuration.as_dict()})
        inputs = objects if resumed else reader
        preparation_refs = ()
        if plan.preparation is not None:
            from .scope import preparation_closure

            preparation_refs = preparation_closure(inputs, plan.preparation, scope=plan.scope)
        scope, source, source_files = load_scope(inputs, plan.scope)
        judge_policy = read_verified(inputs, plan.judge_policy)
        if JudgePolicy.from_bytes(judge_policy).analysis is not None:
            raise ValueError("synthesis runs require concrete-only checks")
        guidance = (() if plan.source_review is None else
                    (load_review_guidance(inputs, plan.source_review.guidance),))
        if not resumed:
            scope_refs = scope_closure(inputs, plan.scope)
            objects.publish(inputs, scope_refs)
            objects.publish(inputs, preparation_refs)
            if plan.construction_experience is not None:
                objects.publish(inputs, (plan.construction_experience,))
            source_refs = tuple(objects.put(payload) for payload in (
                scope.to_bytes(), source.to_bytes(), *(f.data for f in source_files), judge_policy, *guidance))
            tape.record("synthesis_started", {"configuration": configuration.as_dict()},
                        (plan_ref, configuration, *source_refs, *scope_refs, *preparation_refs,
                         *(() if plan.construction_experience is None else (plan.construction_experience,))))
        if tape.events[-1].kind == "synthesis_finished":
            return _recover_terminal(run, tape, plan, plan_ref, config, check_admission)
        drafts: list[BlobRef] = []
        checks: list[BlobRef] = []
        process_roles = frozenset({"author", "reader", "source_review"}) if plan.processes is not None else frozenset()
        phases = RevisionPhases(run, tape, process_roles=process_roles)
        rtl_active = False

        def finish(status, code, escalation=None):
            nonlocal rtl_active
            rtl_head = None
            if status == "review_ready" and plan.rtl is not None:
                from .rtl import build_rtl

                rtl_active = True
                rtl_draft = effective_draft(objects, drafts, phases.completed)
                tape.record("rtl_requested", {"draft": rtl_draft.as_dict()})
                child = JournalDirectory(run.directory / "rtl_build")
                build_rtl(run_id=plan.run_id, policy=plan.rtl, draft=rtl_draft, reader=objects, run=child,
                    cancelled=cancelled, model_pool=model_admission, check_pool=check_admission,
                    development_pool=development_admission,
                    continuation_from=None if continuation is None else continuation.rtl_recheck)
                rtl_head, closure = child.seal("rtl")
                objects.publish(child.objects, closure)
                tape.record("rtl_settled", {"head": rtl_head.as_dict()}, closure)
                rtl_active = False
            result = SynthesisResult(plan_ref, status, code, tuple(drafts), tuple(checks),
                                     tuple(phases.completed), escalation, rtl_head)
            reference = objects.put(result.to_bytes())
            tape.record("synthesis_finished", {"result": reference.as_dict()}, (reference,))
            tape.finish()
            return result

        cancelled = control.cancelled

        feedback = ""
        review_files = ()
        previous_check = previous_review = previous_findings = None
        implementer_parent = None
        implementer_feedback = ""
        reuse_draft = False

        def process(revision, role, inputs):
            if continuation is not None and continuation.contains(revision, role):
                return continuation.completed(run, revision, role)
            return execute_role(planned_job(plan, revision, role, inputs), run=run, revision=revision, role=role,
                                cancelled=cancelled, model_pool=model_admission, development_pool=development_admission)

        try:
            for revision in range(plan.max_revisions):
                parent = drafts[-1] if drafts else None
                if reuse_draft:
                    draft = parent
                    drafts.append(draft)
                    tape.record("draft_reused", {"revision": revision, "draft": draft.as_dict()}, (draft,))
                else:
                    journal = phases.begin(revision, "author", {"parent": None if parent is None else parent.as_dict(),
                                                      "feedback": feedback})
                    if plan.processes is not None or (continuation is not None and continuation.contains(revision, "author")):
                        authored = process(revision, "author", AuthorInputs(plan.task_id, plan.scope, plan.judge_policy,
                            parent, feedback, previous_check, previous_review, previous_findings,
                            plan.construction_experience))
                    else:
                        authored = run_author(task_id=plan.task_id, scope=plan.scope, reader=objects, journal=journal,
                            backend=author_backend, spec=role_attempt(plan, revision, "author"), judge_policy=judge_policy,
                            parent=parent, feedback=feedback, development_tools=development_tools,
                            repair_files=review_files,
                            construction_experience=plan.construction_experience,
                            control=control, model_admission=model_admission)
                    if authored.final_action is None:
                        phases.settle()
                        return finish("unfinished", "author-" + authored.reason)
                    if "scope_escalation" in authored.final_action:
                        ref = BlobRef.from_dict(fields(thaw_json(authored.final_action), {"scope_escalation"})["scope_escalation"])
                        request = ScopeEscalation.from_bytes(read_verified(objects, ref))
                        if (request.task_id, request.scope, request.parent) != (plan.task_id, plan.scope, parent):
                            raise RecordError("synthesis-escalation-mismatch", "scope escalation changed its subject")
                        phases.settle(ref.as_dict(), (ref,))
                        return finish("scope_review_requested", "author-requested-scope-review", ref)
                    draft = BlobRef.from_dict(fields(thaw_json(authored.final_action), {"draft"})["draft"])
                    loaded = load_draft(objects, draft)
                    if (loaded.record.scope, loaded.record.parent, loaded.record.task.task_id) != (
                            plan.scope, parent, plan.task_id):
                        raise RecordError("synthesis-draft-mismatch", "author changed the task lineage")
                    closure = draft_closure(objects, draft)
                    drafts.append(draft)
                    phases.settle(draft.as_dict(), closure)

                reader_inputs = ReaderInputs(draft, implementer_parent, implementer_feedback)
                reader_request = {key: value for key, value in reader_inputs.as_dict().items() if key != "role"}
                journal = phases.begin(revision, "reader", reader_request)
                if plan.processes is not None or (continuation is not None and continuation.contains(revision, "reader")):
                    reconstructed = process(revision, "reader", reader_inputs)
                else:
                    reconstructed = run_reader(draft=draft, reader=objects, journal=journal, backend=reader_backend,
                        spec=role_attempt(plan, revision, "reader"), control=control, model_admission=model_admission,
                        parent=implementer_parent, feedback=implementer_feedback, development_tools=development_tools)
                if reconstructed.final_action is None:
                    phases.settle()
                    return finish("unfinished", "reader-" + reconstructed.reason)
                reconstruction = BlobRef.from_dict(fields(thaw_json(reconstructed.final_action),
                                                          {"reconstruction"})["reconstruction"])
                record = Reconstruction.from_bytes(read_verified(objects, reconstruction))
                if record.draft != draft:
                    raise RecordError("synthesis-reader-mismatch", "reader answered a different draft")
                phases.settle(reconstruction.as_dict(),
                        (reconstruction, *(() if record.behavior is None else
                                           (f.content for f in record.behavior.files))))
                if record.behavior is None and plan.source_review is None:
                    feedback = canonical_json({"draft": draft.as_dict(), "reader_findings": reconstruction.as_dict(),
                        "instruction": "Read .construction/reader_findings/0.blob and resolve the specification's ambiguities "
                                       "against the pinned source and scope. No implementation or score was "
                                       "produced. The revised draft requires a fresh independent reader."}).decode("utf-8")
                    review_files = evidence_files(".construction/reader_findings", record.to_bytes(), (), objects)
                    previous_check = previous_review = None
                    previous_findings = reconstruction
                    continue
                previous_findings = None

                check_ref = None
                outcome = None
                if record.behavior is not None:
                    journal = phases.begin(revision, "check", {"draft": draft.as_dict(),
                                                     "reconstruction": reconstruction.as_dict()})
                    report = compare_reconstruction(draft=draft, reconstruction=reconstruction, reader=objects,
                        journal=journal, admission=check_admission, cancelled=cancelled)
                    check_ref = objects.put(report.to_bytes())
                    checks.append(check_ref)
                    phases.settle(check_ref.as_dict(), (check_ref,))
                    outcome = report.comparison.result
                    if not outcome.cleanup.quiescent:
                        return finish("cleanup_unconfirmed", "comparison-cleanup-" + outcome.cleanup.value)
                    # B1 is authored here, not an immutable external oracle.
                    # Its preparation/execution errors need the same repair
                    # path as mismatches; infrastructure/verifier failures do not.
                    if outcome.score is None and outcome.origin != "reference":
                        return finish("check_unavailable", outcome.code)
                if plan.source_review is not None:
                    review_plan = plan.source_review
                    rtl_findings = None if continuation is None else continuation.findings_for(draft, revision)
                    review_request = {"draft": draft.as_dict(),
                        "guidance": review_plan.guidance.as_dict(),
                        "check": None if check_ref is None else check_ref.as_dict(),
                        "reconstruction": reconstruction.as_dict()}
                    if rtl_findings is not None:
                        review_request["rtl_findings"] = rtl_findings.as_dict()
                    journal = phases.begin(revision, "source_review", review_request)
                    if plan.processes is not None or (continuation is not None and continuation.contains(revision, "source_review")):
                        reviewed = process(revision, "source_review",
                                           SourceReviewInputs(draft, review_plan.guidance, check_ref, reconstruction, rtl_findings))
                    else:
                        reviewed = run_source_review(draft=draft, guidance=review_plan.guidance, reader=objects,
                            journal=journal, backend=source_reviewer_backend,
                            spec=role_attempt(plan, revision, "source_review"), control=control,
                            model_admission=model_admission, check=check_ref, reconstruction=reconstruction,
                            rtl_findings=rtl_findings, development_tools=development_tools)
                    if reviewed.final_action is None:
                        phases.settle()
                        return finish("unfinished", "source-review-" + reviewed.reason)
                    review_ref = BlobRef.from_dict(fields(thaw_json(reviewed.final_action),
                                                         {"source_review"})["source_review"])
                    review = SourceReview.from_bytes(read_verified(objects, review_ref))
                    if (review.draft, review.guidance, review.check, review.reconstruction, review.rtl_findings) != (
                            draft, review_plan.guidance, check_ref, reconstruction, rtl_findings):
                        raise RecordError("synthesis-review-mismatch", "source reviewer answered another draft, check or policy")
                    _, _, files = review_inputs(objects, draft, review_plan.guidance, check=check_ref,
                                                reconstruction=reconstruction, rtl_findings=rtl_findings)
                    validate_review(review, scope, files)
                    phases.settle(review_ref.as_dict(), (review_ref,))
                    if review.contract_revision is not None:
                        validate_contract_revision(objects, draft, reconstruction, review.contract_revision)
                        outcome = review.contract_revision.comparison.result
                    # This bound review adjudicates B2's claims. Its unresolved
                    # findings, not the original report's nonempty list, gate
                    # acceptance after a successful independent comparison.
                    if outcome is not None and outcome.score == 1 and review.supported:
                        return finish("review_ready", "source-and-finite-comparison-agree")
                    if not review.builder_feedback.strip() and not review.implementer_feedback.strip():
                        return finish("unfinished", "review-unresolved-without-repair")
                    feedback = review.builder_feedback
                    reuse_draft = not feedback.strip()
                    # A changed draft always receives a fresh blind implementation.
                    implementer_parent = reconstruction if reuse_draft else None
                    implementer_feedback = review.implementer_feedback if reuse_draft else ""
                    previous_check = previous_review = previous_findings = None
                    review_files = ()
                    continue
                if outcome.score == 1 and not record.findings:
                    return finish("review_ready", "finite-comparison-agrees")
                reply, comparison_files = comparison_feedback(outcome, objects, prefix=".construction/comparison")
                feedback = canonical_json({"draft": draft.as_dict(), "check": check_ref.as_dict(),
                    "comparison": report.comparison.outcome, "code": outcome.code,
                    "feedback": reply,
                    "reader_findings": list(record.findings),
                    "instruction": "Inspect the pinned source and spec to resolve this discrepancy. A mismatch "
                                   "does not establish which implementation is wrong; preserve the declared scope. "
                                   "Read .construction/comparison/report.md and its linked diagnostics with Bash."}
                    ).decode("utf-8")
                review_files = (*evidence_files(".construction/check", report.to_bytes(), outcome.evidence, objects),
                                *comparison_files)
                previous_check, previous_review = check_ref, None
            return finish("revision_limit", "semantic-revision-budget-exhausted")
        except AttemptInterrupted as error:
            # Preserve the active phase's settled prefix and unknown requests. A
            # terminated run records the interruption, never resets phase budgets.
            if rtl_active:
                raise  # Keep pending child work/accounting; never erase an unsettled branch.
            if phases.active is not None:
                phases.settle()
            return finish("interrupted", error.code)
