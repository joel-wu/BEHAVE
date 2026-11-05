"""Continue settled checks, reviews or repairs without rewriting prior runs."""

from dataclasses import dataclass, replace

from behave.agent.journal import Event, load_chain
from behave.agent.process_worker import completed_attempt
from behave.agent.replay import Tape
from behave.agent.trajectory import CostLedger
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore

from .synthesis_records import PhaseReceipt, SynthesisPlan, SynthesisResult, role_attempt


@dataclass(frozen=True)
class Continuation:
    head: BlobRef
    plan: SynthesisPlan
    configuration: dict
    prefix: tuple[Event, ...]
    phases: tuple[PhaseReceipt, ...]
    billing: CostLedger
    draft: BlobRef
    rtl_findings: BlobRef | None = None
    rtl_recheck: BlobRef | None = None
    repair_revision: int | None = None
    artifacts: tuple[BlobRef, ...] = ()

    def contains(self, revision, role):
        return any((phase.revision, phase.role) == (revision, role) for phase in self.phases)

    def completed(self, run, revision, role):
        if not self.contains(revision, role):
            return None
        return completed_attempt(role_attempt(self.plan, revision, role),
            run.journal("revisions", f"r{revision}", role), namespace="construction")

    def findings_for(self, draft, revision=None):
        for event in self.prefix:
            if (event.kind == "phase_requested" and event.payload["revision"] == revision
                    and event.payload["role"] == "source_review"):
                value = event.payload["inputs"].get("rtl_findings")
                return None if value is None else BlobRef.from_dict(thaw_json(value))
        return self.rtl_findings if draft == self.draft else None


def load_continuation(reader, head, *, verify_history=True):
    """Authenticate the previous outcome before choosing a continuation boundary."""
    from .process_evidence import synthesis_billing, synthesis_costs
    from .reviews import DraftCheck
    from .rtl import synthesis_rtl

    events = load_chain(reader, head, verify_artifacts=verify_history)
    if not events or events[-1].kind != "synthesis_finished":
        raise RecordError("continuation-not-settled", "continuation needs a settled synthesis run")
    configuration = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(events[0].payload["configuration"]))))
    result = SynthesisResult.from_bytes(read_verified(reader, BlobRef.from_dict(thaw_json(events[-1].payload["result"]))))
    plan = SynthesisPlan.from_bytes(read_verified(reader, result.plan))
    billing = (synthesis_billing if verify_history else synthesis_costs)(reader, head)
    rtl_findings = rtl_recheck = None
    repair_revision = None
    if result.status == "check_unavailable" and result.checks:
        report = DraftCheck.from_bytes(read_verified(reader, result.checks[-1]))
        if report.comparison.result.score is not None or not report.comparison.result.cleanup.quiescent:
            raise RecordError("continuation-check-not-ready", "only a settled, quiescent no-result check can be retried")
        # A B2-only repair retains the draft and its unresolved RTL findings.
        for event in reversed(events):
            if event.kind == "phase_requested" and event.payload["role"] == "source_review":
                inputs = thaw_json(event.payload)["inputs"]
                if inputs["draft"] == result.drafts[-1].as_dict() and inputs.get("rtl_findings") is not None:
                    rtl_findings = BlobRef.from_dict(inputs["rtl_findings"])
                break
        role = "check"
    elif result.status == "revision_limit":
        # Replay the completed prefix to recover the last author's workspace,
        # B2 and the exact recipient-specific feedback. Only the next revision
        # is new work; completed comparisons and model calls remain reusable.
        role = None
        repair_revision = len(result.drafts)
        if plan.source_review is not None:
            from .source import SourceReview

            settled = next(event for event in reversed(events) if event.kind == "phase_settled")
            review_ref = BlobRef.from_dict(thaw_json(settled.payload)["output"])
            review = SourceReview.from_bytes(read_verified(reader, review_ref))
            if review.quoted_feedback_field is not None:
                # Older submit handlers accepted this malformed empty string.
                # Return to Reviewer to correct it; never auto-approve the task
                # or send quote characters to an author as repair instructions.
                role, repair_revision = "source_review", None
    elif (result.status == "unfinished" and result.drafts
          and (result.code == "review-unresolved-without-repair"
               or result.code.startswith("source-review-"))):
        # The pair and its comparison are complete. Ask the existing Reviewer
        # to finish its decision in a new attempt, not Builder to start over.
        role = "source_review"
    elif result.status == "review_ready" and result.rtl_head is not None:
        rtl, _, _ = synthesis_rtl(reader, plan, result, configuration, events)
        if rtl.status == "check_unavailable":
            rtl_recheck = result.rtl_head
            role = None  # Keep every completed B1/B2/check/review phase.
        elif rtl.status == "review_requested" and plan.source_review is not None:
            rtl_findings = rtl.candidates[-1]
            role = "source_review"
        else:
            raise RecordError("continuation-no-rtl-findings", "RTL continuation needs a no-result check or task-review request")
    else:
        raise RecordError("continuation-not-supported", "continue an unavailable check, unfinished review or exhausted repair")
    revision = len(result.drafts) - 1
    boundary = (len(events) - 1 if repair_revision is not None else
                next(i for i, event in enumerate(events) if event.kind == "rtl_requested") if rtl_recheck else
                next(i for i, event in enumerate(events) if event.kind == "phase_requested"
                     and (event.payload["revision"], event.payload["role"]) == (revision, role)))
    prefix = events[:boundary]
    inherited = {(event.payload["phase"]["revision"], event.payload["phase"]["role"])
                 for event in prefix if event.kind == "phase_settled"}
    phases = tuple(phase for phase in result.phases if (phase.revision, phase.role) in inherited)
    artifacts = tuple(dict.fromkeys(ref for event in events
                                    for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
    return Continuation(head, plan, configuration, prefix, phases, billing, result.drafts[-1],
                        rtl_findings, rtl_recheck, repair_revision, artifacts)


def validate_plan(continuation, plan):
    previous = continuation.plan
    if plan.run_id == previous.run_id:
        raise RecordError("continuation-run-id", "a continuation needs a new run_id")
    if replace(plan, run_id=previous.run_id, max_revisions=previous.max_revisions) != previous:
        raise RecordError("continuation-plan-mismatch", "continuation reuses the task, roles and comparison policy")
    if any(phase.revision >= plan.max_revisions for phase in continuation.phases):
        raise RecordError("continuation-budget", "revision budget does not contain the inherited prefix")
    if continuation.repair_revision is not None and plan.max_revisions <= continuation.repair_revision:
        raise RecordError("continuation-budget", "increase runtime max_revisions to continue the saved repair")


def validate_configuration(continuation, plan, configuration):
    validate_plan(continuation, plan)
    runtime = lambda row: {key: value for key, value in row.items()
                          if key not in {"plan", "continuation_from"}}
    if canonical_json(runtime(configuration)) != canonical_json(runtime(continuation.configuration)):
        raise RecordError("continuation-runtime-mismatch", "continuation cannot silently change execution policy")


def verify_continuation(reader, configuration, events):
    reference = configuration.get("continuation_from")
    if reference is None:
        return None
    continuation = load_continuation(reader, BlobRef.from_dict(reference))
    plan = SynthesisPlan.from_bytes(read_verified(reader, BlobRef.from_dict(configuration["plan"])))
    validate_configuration(continuation, plan, configuration)
    if len(events) < len(continuation.prefix) or any(
            (old.kind, thaw_json(old.payload), old.artifacts) !=
            (new.kind, thaw_json(new.payload), new.artifacts)
            for old, new in zip(continuation.prefix[1:], events[1:len(continuation.prefix)], strict=True)):
        raise RecordError("continuation-prefix-mismatch", "continuation changed inherited phase history")
    return continuation


def seed_continuation(reader, run, head, plan, configuration):
    """Copy verified journal prefixes into a new run, with their original receipts."""
    references = {}

    class Reads:
        def read(self, ref):
            payload = read_verified(reader, ref)
            references[ref] = None
            return payload

    tracked = Reads()
    owned = isinstance(reader, DirectoryStore)
    continuation = load_continuation(tracked, head, verify_history=not owned)
    references.update(dict.fromkeys(continuation.artifacts))
    validate_configuration(continuation, plan, configuration)
    # Load every inherited role and relay journal before publishing the closed input set.
    journals = []
    for phase in continuation.phases:
        path = ("revisions", f"r{phase.revision}", phase.role)
        journals.append((path, load_chain(tracked, phase.head, verify_artifacts=not owned)))
        if phase.process is not None:
            for channel in ("supervision", "provider", "development"):
                journals.append(((*path[:-1], phase.role + "_" + channel),
                                 load_chain(tracked, getattr(phase.process, channel), verify_artifacts=not owned)))
    if owned:
        # These immutable files were already committed by the previous owner.
        # Preserve them with the ordinary owner-to-owner transfer, not another
        # full transcript readback before a saved task can continue.
        run.objects.publish_owned(reader, tuple(references))
    else:
        run.objects.publish(tracked, tuple(references))
    config_ref = run.objects.put(canonical_json(configuration))
    plan_ref = run.objects.put(plan.to_bytes())
    tape = Tape(run.journal("synthesis"))
    tape.record("synthesis_started", {"configuration": config_ref.as_dict()},
                tuple(dict.fromkeys((config_ref, plan_ref, head, *references))))
    for event in continuation.prefix[1:]:
        tape.record(event.kind, thaw_json(event.payload), event.artifacts)
    for path, events in journals:
        tape = Tape(run.journal(*path))
        for event in events:
            tape.record(event.kind, thaw_json(event.payload), event.artifacts)
    return continuation
