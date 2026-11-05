"""Read-only synthesis handoff verification and aggregate role billing."""

from collections.abc import Mapping

from behave.agent.journal import Event, load_chain
from behave.agent.trajectory import CostLedger, billing_of, trajectory_of
from behave.core.artifacts import BlobRef, ContentReader
from behave.core.records import fields, thaw_json

from .drafts import Reconstruction
from .escalation import ScopeEscalation
from .role import (
    AuthorInputs,
    ReaderInputs,
    SourceReviewInputs,
    _configuration,
    _record,
    _require,
    planned_job,
    process_billing,
)
from .source import SourceReview
from .synthesis_records import SynthesisPlan, SynthesisResult, coordinator_for


def verify_process_phases(reader: ContentReader, plan: SynthesisPlan, result: SynthesisResult,
                          events: tuple[Event, ...], configuration: Mapping) -> tuple[CostLedger, ...]:
    """Bind all revisions, including rejected ones, to coordinator handoffs."""
    from .continuation import verify_continuation
    from .rtl import synthesis_rtl

    continuation = verify_continuation(reader, configuration, events)
    _, auxiliary_ledgers, _ = synthesis_rtl(reader, plan, result, configuration, events)
    if plan.processes is None:
        _require(all(p.process is None for p in result.phases), "callback plan carries unplanned process receipts")
    requested = [thaw_json(e.payload) for e in events if e.kind == "phase_requested"]
    settled = [thaw_json(e.payload) for e in events if e.kind == "phase_settled"]
    _require(len(requested) == len(settled) == len(result.phases), "phase handoffs are incomplete")
    outputs, ledgers = {}, [] if continuation is None else [continuation.billing]
    reused = {e.payload["revision"]: BlobRef.from_dict(thaw_json(e.payload)["draft"])
              for e in events if e.kind == "draft_reused"}
    for phase, request, outcome in zip(result.phases, requested, settled, strict=True):
        _require((request["revision"], request["role"]) == (phase.revision, phase.role)
                 and outcome["phase"] == phase.as_dict(), "phase order or receipt differs from coordinator history")
        _require(phase.revision < plan.max_revisions, "phase exceeds revision budget")
        if phase.revision in reused and (phase.revision, "author") not in outputs:
            previous_review = outputs.get((phase.revision - 1, "source_review"))
            _require(previous_review is not None, "draft reuse requires a previous review")
            review = SourceReview.from_bytes(_record(reader, previous_review))
            _require(not review.builder_feedback.strip() and review.implementer_feedback.strip(),
                     "draft reuse requires an implementer-only repair")
            _require(reused[phase.revision] == outputs.get((phase.revision - 1, "author")),
                     "reused draft changed")
            outputs[phase.revision, "author"] = reused[phase.revision]
        output = None if outcome["output"] is None else BlobRef.from_dict(outcome["output"])
        outputs[phase.revision, phase.role] = output
        if continuation is not None and continuation.contains(phase.revision, phase.role):
            continue  # The exact inherited prefix and its original billing were verified above.
        if phase.role == "check":
            _require(phase.process is None, "comparison is not a construction worker")
            continue
        if phase.role == "author":
            row = fields(request["inputs"], {"parent", "feedback"})
            parent = None if row["parent"] is None else BlobRef.from_dict(row["parent"])
            _require(parent == outputs.get((phase.revision - 1, "author")), "author parent changed across revisions")
            previous_reader = outputs.get((phase.revision - 1, "reader"))
            findings = (previous_reader if previous_reader is not None and
                        Reconstruction.from_bytes(_record(reader, previous_reader)).behavior is None else None)
            if plan.source_review is not None:
                feedback = ""
                if phase.revision:
                    review = SourceReview.from_bytes(_record(reader, outputs[phase.revision - 1, "source_review"]))
                    feedback = review.builder_feedback
                    _require(bool(feedback.strip()), "builder repair was not requested")
                _require(row["feedback"] == feedback, "builder feedback differs from the review")
                inputs = AuthorInputs(plan.task_id, plan.scope, plan.judge_policy, parent, feedback,
                                      construction_experience=plan.construction_experience)
            else:
                inputs = AuthorInputs(plan.task_id, plan.scope, plan.judge_policy, parent, row["feedback"],
                                      outputs.get((phase.revision - 1, "check")), None, findings,
                                      plan.construction_experience)
        elif phase.role == "reader":
            row = request["inputs"]
            parent, feedback = None, ""
            if phase.revision in reused:
                review = SourceReview.from_bytes(_record(reader, outputs[phase.revision - 1, "source_review"]))
                parent, feedback = outputs[phase.revision - 1, "reader"], review.implementer_feedback
            inputs = ReaderInputs(BlobRef.from_dict(row["draft"]), parent, feedback)
            _require(row == {k: v for k, v in inputs.as_dict().items() if k != "role"},
                     "reader repair received unplanned inputs")
        else:
            row = fields(request["inputs"], {"draft", "guidance", "check", "reconstruction"}
                         | ({"rtl_findings"} if "rtl_findings" in request["inputs"] else set()))
            _require(plan.source_review is not None, "source reviewer was not planned")
            inputs = SourceReviewInputs(BlobRef.from_dict(row["draft"]), plan.source_review.guidance,
                                       None if row["check"] is None else BlobRef.from_dict(row["check"]),
                                       BlobRef.from_dict(row["reconstruction"]),
                                       None if continuation is None else continuation.findings_for(
                                           BlobRef.from_dict(row["draft"]), phase.revision))
            _require(row.get("rtl_findings") == (None if inputs.rtl_findings is None else inputs.rtl_findings.as_dict()),
                     "review received another RTL finding")
            _require(row["guidance"] == inputs.guidance.as_dict(), "source review changed guidance")
            _require(inputs.check == outputs.get((phase.revision, "check")), "review received another check")
            _require(inputs.reconstruction == outputs.get((phase.revision, "reader")),
                     "review received another reconstruction")
        if phase.role != "author":
            _require(inputs.draft == outputs.get((phase.revision, "author")), "role received another revision's draft")
        ledger = (None if plan.processes is None else
                  process_billing(reader, phase, planned_job(plan, phase.revision, phase.role, inputs), configuration))
        role_events = load_chain(reader, phase.head)
        if output is not None:
            _require(role_events and role_events[-1].kind == "terminal", "handoff lacks a terminal role submission")
            terminal = thaw_json(role_events[-1].payload)
            name = {"author": "draft", "reader": "reconstruction", "source_review": "source_review"}[phase.role]
            if phase.role == "author" and output == result.escalation:
                escalation = ScopeEscalation.from_bytes(_record(reader, output))
                _require(phase == result.phases[-1] and result.status == "scope_review_requested"
                         and (escalation.task_id, escalation.scope, escalation.parent) ==
                         (plan.task_id, plan.scope, inputs.parent), "scope escalation changed its subject")
                name = "scope_escalation"
            _require(terminal["reason"] == "final_action" and terminal["final_action"] == {name: output.as_dict()},
                     "handoff differs from the role's submitted artifact")
        ledgers.append(trajectory_of(role_events, reader).billing if ledger is None else ledger)
    return (*ledgers, *auxiliary_ledgers)


def load_synthesis(reader: ContentReader, synthesis_head: BlobRef) -> tuple[SynthesisResult, CostLedger]:
    """Return the terminal result and billing from the same verified history.

    This is full offline verification, not the trusted live cost projection.
    No records or payload cache survive the call.
    """
    events = load_chain(reader, synthesis_head)
    _require(events and events[0].kind == "synthesis_started" and events[-1].kind == "synthesis_finished",
             "billing requires a terminal synthesis prefix")
    config = _configuration(reader, events, "synthesis_started")
    terminal = fields(thaw_json(events[-1].payload), {"result"})
    result = SynthesisResult.from_bytes(_record(reader, BlobRef.from_dict(terminal["result"])))
    plan = SynthesisPlan.from_bytes(_record(reader, result.plan))
    _require(config["plan"] == result.plan.as_dict() and config["coordinator"] == coordinator_for(plan),
             "synthesis configuration differs from the result")
    return result, CostLedger.combine(verify_process_phases(reader, plan, result, events, config))


def synthesis_billing(reader: ContentReader, synthesis_head: BlobRef) -> CostLedger:
    """Sum actual role invocations from an exported terminal run, without resampling."""
    return load_synthesis(reader, synthesis_head)[1]


def synthesis_costs(reader: ContentReader, synthesis_head: BlobRef) -> CostLedger:
    """Cost projection of a trusted completed run, without replaying its inputs.

    The live coordinator already owns the role/check receipts. Full lineage
    verification remains in synthesis_billing and independent admission.
    """
    events = load_chain(reader, synthesis_head, verify_artifacts=False)
    config = _configuration(reader, events, "synthesis_started")
    result = SynthesisResult.from_bytes(_record(reader,
        BlobRef.from_dict(thaw_json(events[-1].payload)["result"])))
    if result.rtl_head is not None:
        return synthesis_billing(reader, synthesis_head)
    inherited, ledgers = (), []
    if config.get("continuation_from") is not None:
        head = BlobRef.from_dict(config["continuation_from"])
        previous = load_chain(reader, head, verify_artifacts=False)
        old = SynthesisResult.from_bytes(_record(reader,
            BlobRef.from_dict(thaw_json(previous[-1].payload)["result"])))
        inherited = old.phases
        ledgers.append(synthesis_costs(reader, head))
    for phase in result.phases:
        if phase.role == "check" or phase in inherited:
            continue
        head = (phase.process.provider if phase.process is not None
                and config["model_admission"] is not None else phase.head)
        ledgers.append(billing_of(load_chain(reader, head, verify_artifacts=False), reader))
    return CostLedger.combine(tuple(ledgers))
