"""Pure admission from sealed evidence. No execution, score rewriting or culling.

Content hashes authenticate byte identity, not the identity of an external
reviewer. Journals, policies and operator attestations must come from trusted
supervisors; generated task code never receives their write capability.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import Event, load_chain
from behave.agent.process_worker import worker_receipt
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, fields, parse_json, thaw_json
from behave.data.published import TaskManifest
from behave.task_execution.comparison import COMPARISON_BACKEND, ComparisonInputs

from .checks import load_check
from .contract import validate_contract_revision
from .drafts import Reconstruction, draft_closure, load_draft, load_scope, load_source_artifact
from .process_evidence import verify_process_phases
from .release_records import HumanApproval, ReleaseDisposition, ReleasePolicy, ReviewPackage
from .reviews import DraftCheck
from .source import SourceReview, review_inputs, review_revision, validate_review
from .synthesis_records import SynthesisPlan, SynthesisResult, coordinator_for


class _Evidence:
    """Verify reads and retain the explicit closure without accumulating bodies."""
    def __init__(self, reader, *, verify_history=True):
        self.reader = reader
        self.verify_history = verify_history
        self.references = {}

    def read(self, ref):
        data = read_verified(self.reader, ref)
        self.references[ref] = None
        return data

    def record(self, ref):
        return self.read(ref)

    def chain(self, head):
        events = load_chain(self, head, verify_artifacts=self.verify_history)
        self.references.update(dict.fromkeys(ref for event in events for ref in event.artifacts))
        return events


def _require(condition, detail):
    if not condition:
        raise RecordError("admission-evidence-mismatch", detail)


def _attempt(reader, head, role, expected_files, *, protected_files=None):
    events = reader.chain(head)
    _require(events and events[0].kind == "initialized" and events[-1].kind == "terminal",
             "review role has no complete sealed attempt")
    config = parse_json(reader.record(BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    spec = AttemptSpec.from_dict(config["spec"])
    _require(spec.role == role, "wrong role supplied as independent review")
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, BlobRef.from_dict(config["workspace"])))
    _require(snapshot == WorkspaceSnapshot(tuple(f.reference for f in expected_files)),
             "reviewer input view differs from the pinned draft")
    protected_files = expected_files if protected_files is None else protected_files
    _require(set(spec.read_only_paths) >= {f.reference.path for f in protected_files},
             "reviewer could edit its evidence inputs")
    terminal = thaw_json(events[-1].payload)
    _require(terminal["reason"] == "final_action", "reviewer did not submit a report")
    return terminal["final_action"], spec, config


def _submitted_action(reader, head):
    """Consume the trusted role owner's terminal receipt, not its transcript."""
    terminal = Event.from_bytes(reader.record(head))
    _require(terminal.kind == "terminal" and terminal.payload["reason"] == "final_action",
             "review role did not submit a report")
    return thaw_json(terminal.payload)["final_action"]


@dataclass(frozen=True, slots=True)
class Admission:
    disposition: ReleaseDisposition
    task: TaskManifest | None
    closure: tuple[BlobRef, ...]


def admit_package(*, package: BlobRef, policy: BlobRef, reader: ContentReader,
                  approval: BlobRef | None = None, verify_history: bool = True) -> Admission:
    """Apply the explicit policy to current evidence; insufficient evidence stays pending.

    The automatic policy records weaker assurance than a human-reviewed benchmark.
    Neither mode converts finite agreement into a proof of source correctness.
    A human approval cannot override a failed or stale executable/reviewer gate.
    A trusted live owner may consume sealed handoffs without rereading old model
    request/response bodies. The same final checks and complete retained closure
    still apply; independent cold verification defaults to the full history.
    """
    evidence = _Evidence(reader, verify_history=verify_history)
    proposed = ReviewPackage.from_bytes(evidence.record(package))
    if proposed.construction_prefix is not None:
        if verify_history:
            from .supervisor import verify_construction_prefix

            verify_construction_prefix(evidence, proposed.construction_prefix, synthesis_head=proposed.synthesis_head)
        else:
            prefix = evidence.chain(proposed.construction_prefix)
            _require(prefix and prefix[-1].kind == "construction_stage"
                     and thaw_json(prefix[-1].payload) ==
                     {"stage": "synthesis", "head": proposed.synthesis_head.as_dict()},
                     "package differs from the completed construction handoff")
    rules = ReleasePolicy.from_bytes(evidence.record(policy))
    evidence.record(rules.guidance)
    evidence.record(rules.judge_policy)
    if rules.scope_review is not None:
        evidence.record(rules.scope_review.guidance)
    events = evidence.chain(proposed.synthesis_head)
    _require(events and events[0].kind == "synthesis_started" and events[-1].kind == "synthesis_finished",
             "package needs a terminal synthesis journal")
    config = parse_json(evidence.record(BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    result = SynthesisResult.from_bytes(evidence.record(BlobRef.from_dict(thaw_json(events[-1].payload)["result"])))
    _require(BlobRef.from_dict(config["plan"]) == result.plan, "result names another synthesis plan")
    plan = SynthesisPlan.from_bytes(evidence.record(result.plan))
    if plan.preparation is not None:
        from .scope import preparation_closure

        preparation_closure(evidence, plan.preparation, scope=plan.scope)
    _require(config["coordinator"] == coordinator_for(plan), "unsupported synthesis coordinator")
    # Auxiliary ledgers are part of the evidence, including earlier rejected
    # revisions. A clean final draft cannot erase an unverified worker lifetime.
    if verify_history:
        verify_process_phases(evidence, plan, result, events, config)
    else:
        # The owning coordinator already checked role manifests, input views
        # and submissions. Keep every old file, but consume its completion and
        # cleanup receipts rather than reconstructing/costing every old call.
        for phase in result.phases:
            if phase.process is not None:
                receipt = worker_receipt(evidence.chain(phase.process.supervision), namespace="construction")
                CleanupState(receipt["cleanup"]).require_quiescent("construction")
    from .rtl import synthesis_rtl

    rtl_result, _, auxiliary_rtl = synthesis_rtl(evidence, plan, result, config, events)
    scope, source, _ = load_scope(evidence, plan.scope)
    latest = result.drafts[-1] if result.drafts else None
    loaded = None if latest is None else load_draft(evidence, latest)
    if loaded is not None:
        _require(loaded.record.scope == plan.scope and loaded.record.task.task_id == plan.task_id,
                 "draft is outside the planned source/scope/task")
        draft_closure(evidence, latest)
    reasons = []
    contract_revision = None
    revised_loaded = None
    if proposed.source_review_head is not None:
        terminal = Event.from_bytes(evidence.record(proposed.source_review_head))
        action = terminal.payload.get("final_action")
        if action is not None and "source_review" in action:
            submitted = SourceReview.from_bytes(evidence.record(BlobRef.from_dict(dict(action["source_review"]))))
            contract_revision = submitted.contract_revision
    if rtl_result is not None and rtl_result.status != "passed":
        reasons.append("auxiliary-rtl-" + rtl_result.status)
    if rules.scope_review is not None:
        if plan.preparation is None:
            reasons.append("scope-review-missing")
        else:
            from .scope import ScopePlan, load_scope_preparation

            prepared, _ = load_scope_preparation(evidence, plan.preparation)
            preparation = ScopePlan.from_bytes(evidence.record(prepared.plan))
            if (preparation.author.inputs.guidance != rules.scope_review.guidance
                    or preparation.reviewer.model_policy != rules.scope_review.model_policy):
                reasons.append("scope-review-policy-stale")
    if result.status != "review_ready":
        reasons.append("construction-" + result.status)
    if config["comparison"] != COMPARISON_BACKEND or plan.judge_policy != rules.judge_policy:
        reasons.append("comparison-policy-stale")
    if rules.require_license_metadata and source.license is None:
        reasons.append("source-license-metadata-missing")

    if result.status == "review_ready":
        _require(loaded is not None and bool(result.checks), "ready construction lacks a draft/check")
        report = DraftCheck.from_bytes(evidence.record(result.checks[-1]))
        reconstruction = Reconstruction.from_bytes(evidence.record(report.reconstruction))
        _require(report.draft == latest == reconstruction.draft, "check/reconstruction belongs to another draft")
        _require(reconstruction.behavior is not None, "findings-only report has no concrete comparison")
        task = loaded.record.task
        _require(task.judge_policy == plan.judge_policy, "draft changed the trusted judge policy")
        checked_inputs = parse_json(evidence.record(report.comparison.inputs))
        expected = ComparisonInputs("behavior", load_source_artifact(evidence, reconstruction.behavior),
            loaded.source(task.reference_behavior), loaded.file(task.contract.path),
            evidence.read(task.run_policy), loaded.judge_policy,
            reference_visible=checked_inputs.get("reference_visible", False),
            spec=loaded.file(task.spec.path) if "spec" in checked_inputs else None)
        _require(checked_inputs == expected.as_dict(),
                 "check inputs differ from the submitted artifacts or policies")
        revision = len(result.drafts) - 1
        phases = {p.role: p for p in result.phases if p.revision == revision}
        expected_roles = {"author", "reader", "check"} | ({"source_review"} if plan.source_review else set())
        if any(e.kind == "draft_reused" and e.payload["revision"] == revision for e in events):
            expected_roles.remove("author")
        _require(set(phases) == expected_roles, "ready revision lacks its planned phase receipts")
        # These receipts must themselves be bound to the coordinator's handoffs.
        settled = [thaw_json(e.payload) for e in events if e.kind == "phase_settled"]
        for role, ref in (("author", latest), ("reader", report.reconstruction), ("check", result.checks[-1])):
            if role == "author" and role not in phases:
                continue
            _require(any(row["phase"] == phases[role].as_dict() and row["output"] == ref.as_dict() for row in settled),
                     "result phase is not a committed coordinator handoff")
        if verify_history:
            from .construction import reader_inputs

            reader_request = next(thaw_json(e.payload)["inputs"] for e in events if e.kind == "phase_requested"
                                  and e.payload["revision"] == revision and e.payload["role"] == "reader")
            parent = reader_request.get("parent")
            _, initial = reader_inputs(evidence, latest, None if parent is None else BlobRef.from_dict(parent))
            action, _, _ = _attempt(evidence, phases["reader"].head, "spec_reader", initial,
                                   protected_files=loaded.reader_files)
        else:
            action = _submitted_action(evidence, phases["reader"].head)
        _require(fields(action, {"reconstruction"})["reconstruction"] == report.reconstruction.as_dict(),
                 "independent reader did not submit this reconstruction")
        check_events = evidence.chain(phases["check"].head)
        _require(check_events and check_events[0].kind == "check_started"
                 and check_events[-1].kind == "check_finished", "comparison did not finish")
        _require(check_events[0].payload["inputs"] == report.comparison.inputs.as_dict()
                 and check_events[0].payload["backend"] == config["comparison"], "comparison identity changed")
        _require(BlobRef.of(check_events[-1].to_bytes()) == report.comparison.event
                 and thaw_json(check_events[-1].payload)["result"] == report.comparison.result.as_dict(),
                 "reported score is not the sealed comparison result")
        _require(load_check(evidence, phases["check"].head) == report.comparison,
                 "comparison summary is not its settled outcome")
        if ((report.comparison.result.score != 1 and contract_revision is None)
                or not report.comparison.result.cleanup.quiescent):
            reasons.append("concrete-check-not-ready")
        # A planned joint review below must inspect this exact reconstruction
        # and comparison. Without it, the reader's findings remain unresolved.
        if reconstruction.findings and plan.source_review is None:
            reasons.append("reader-findings-unresolved")

    if proposed.source_review_head is None:
        reasons.append("source-review-missing")
    elif latest is None:
        raise RecordError("admission-evidence-mismatch", "source review has no draft to review")
    else:
        # Read the report first to recover its *actual* guidance, even when stale.
        # A planned final phase is already in the synthesis closure. External
        # standalone review records still need their separate closure retained.
        review_events = (evidence.chain(proposed.source_review_head) if verify_history or plan.source_review is None
                         else (Event.from_bytes(evidence.record(proposed.source_review_head)),))
        action = (thaw_json(review_events[-1].payload)["final_action"]
                  if review_events and review_events[-1].kind == "terminal" else None)
        if action is None:
            reasons.append("source-review-unfinished")
        else:
            ref = BlobRef.from_dict(fields(action, {"source_review"})["source_review"])
            review = SourceReview.from_bytes(evidence.record(ref))
            _require(review.draft == latest, "source review targets a stale draft")
            if plan.source_review is not None:
                receipts = [p for p in result.phases if p.role == "source_review" and p.revision == len(result.drafts) - 1]
                _require(len(receipts) == 1 and receipts[0].head == proposed.source_review_head,
                         "source review is not the planned revision's sealed phase")
                _require(any(thaw_json(e.payload) == {"phase": receipts[0].as_dict(), "output": ref.as_dict()}
                             for e in events if e.kind == "phase_settled"), "source review handoff changed")
                _require(review.guidance == plan.source_review.guidance, "source review changed planned guidance")
                final_outputs = {thaw_json(e.payload)["phase"]["role"]: thaw_json(e.payload)["output"]
                                 for e in events if e.kind == "phase_settled"
                                 and e.payload["phase"]["revision"] == len(result.drafts) - 1}
                _require(review.reconstruction is not None
                         and review.reconstruction.as_dict() == final_outputs.get("reader")
                         and (None if review.check is None else review.check.as_dict()) == final_outputs.get("check"),
                         "source review did not inspect the final reconstruction/check")
                request = next(thaw_json(event.payload)["inputs"] for event in events
                               if event.kind == "phase_requested" and event.payload["role"] == "source_review"
                               and event.payload["revision"] == len(result.drafts) - 1)
                _require(request.get("rtl_findings") == (None if review.rtl_findings is None else review.rtl_findings.as_dict()),
                         "source review changed its RTL findings")
            _, _, inputs = review_inputs(evidence, latest, review.guidance, check=review.check,
                                         reconstruction=review.reconstruction, rtl_findings=review.rtl_findings)
            if verify_history or plan.source_review is None:
                action, reviewer, settings = _attempt(evidence, proposed.source_review_head, "source_reviewer", inputs)
            else:
                action = _submitted_action(evidence, proposed.source_review_head)
                reviewer, settings = plan.source_review.reviewer, None
            if verify_history and plan.source_review is not None:
                from .continuation import verify_continuation

                continuation = verify_continuation(evidence, config, events)
                inherited = continuation is not None and continuation.contains(len(result.drafts) - 1, "source_review")
                template = plan.source_review.reviewer
                # Inherited phase identity was authenticated with its original
                # plan by verify_process_phases; it is not a fresh model call.
                expected_id = (reviewer.attempt_id if inherited else
                               f"{plan.run_id}:r{len(result.drafts) - 1}:source_review:{template.attempt_id}")
                expected_paths = tuple(sorted({*template.read_only_paths, *(f.reference.path for f in inputs)}))
                _require(reviewer == replace(template, attempt_id=expected_id, read_only_paths=expected_paths),
                         "source reviewer differs from the planned attempt")
            _require(action["source_review"] == ref.as_dict(), "source review output changed")
            binding = review_revision(latest, review.guidance, review.check, review.reconstruction, review.rtl_findings)
            if settings is not None:
                _require(any(t["name"] == "submit_source_review" and t["revision"] == binding
                             for t in settings["tools"]), "source reviewer used an unknown submission contract")
            validate_review(review, scope, inputs)
            if review.contract_revision is not None:
                revised_loaded = validate_contract_revision(evidence, latest, review.reconstruction,
                                                            review.contract_revision)
                repaired = review.contract_revision.comparison.result
                if repaired.score != 1 or not repaired.cleanup.quiescent:
                    reasons.append("contract-repair-check-not-ready")
            if review.guidance != rules.guidance or reviewer.model_policy != rules.review_model_policy:
                reasons.append("source-review-policy-stale")
            if not review.supported:
                reasons.append("source-review-findings-unresolved")

    if approval is not None:
        attestation = HumanApproval.from_bytes(evidence.record(approval))
        _require(attestation.package == package and attestation.policy == policy,
                 "human approval belongs to another package or release policy")
    elif rules.mode == "reviewed-benchmark":
        reasons.append("human-approval-required")
    final_draft = latest if revised_loaded is None else review.contract_revision.draft
    disposition = ReleaseDisposition(package, policy, scope.source, plan.task_id, final_draft,
        "pending" if reasons else "include", tuple(dict.fromkeys(reasons)) or ("release-policy-satisfied",), approval)
    task = (None if loaded is None else loaded.record.task) if revised_loaded is None else revised_loaded.record.task
    if auxiliary_rtl is not None:
        task = replace(task, reference_rtl=auxiliary_rtl)
    return Admission(disposition, task, tuple(evidence.references))
