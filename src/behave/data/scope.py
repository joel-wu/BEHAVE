"""Bounded pre-author plans/results, distinct from task and release approval.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.journal import load_chain
from behave.agent.lifetime import AttemptControl
from behave.agent.loop import run_attempt
from behave.agent.prompting import render, role_messages
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.trajectory import CostLedger
from behave.agent.workspace import WorkspaceSnapshot
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, read_verified
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, thaw_json, version

from .contract import contract_guidance
from .drafts import ScopeProposal, load_scope, load_source_record, scope_closure, source_closure
from .phase_journal import seal_phase, verify_process_seal
from .role import RoleJob, ScopeInputs, ScopeReviewInputs, input_closure, process_billing
from .role_policy import RolePolicy
from .source import (
    CapabilityAssessment,
    load_review_guidance,
    parse_assessments,
    validate_assessments,
    validate_report_fields,
)
from .synthesis import execute_role
from .synthesis_records import PhaseReceipt


@dataclass(frozen=True, slots=True)
class ScopePlan:
    run_id: str
    author: RoleJob
    reviewer: AttemptSpec
    reviewer_policy: RolePolicy
    max_revisions: int = defaults.SYN_SCOPE_REVISIONS

    def __post_init__(self):
        text(self.run_id, "scope run id")
        if type(self.author) is not RoleJob or type(self.author.inputs) is not ScopeInputs:
            raise TypeError("scope planning requires a fixed scope-author job")
        if type(self.reviewer) is not AttemptSpec or self.reviewer.role != "scope_reviewer":
            raise TypeError("scope planning requires an independent reviewer")
        if type(self.reviewer_policy) is not RolePolicy:
            raise TypeError("scope planning requires a fixed review policy")
        self.reviewer_policy.check_attempt(self.reviewer)
        if type(self.max_revisions) is not int or self.max_revisions < 1:
            raise ValueError("scope revision limit must be positive")

    def job(self, revision: int, role: str, inputs: ScopeInputs | ScopeReviewInputs) -> RoleJob:
        if type(revision) is not int or not 0 <= revision < self.max_revisions:
            raise ValueError("scope job exceeds its revision budget")
        if role == "scope_author" and type(inputs) is ScopeInputs:
            attempt, policy = self.author.attempt, self.author.policy
        elif role == "scope_reviewer" and type(inputs) is ScopeReviewInputs:
            attempt, policy = self.reviewer, self.reviewer_policy
        else:
            raise ValueError("unknown scope planning role")
        attempt = replace(attempt, attempt_id=f"{self.run_id}:r{revision}:{role}:{attempt.attempt_id}")
        return RoleJob(inputs, attempt, policy.model, policy.python_limits, policy.cocotb_policy, policy.log_bytes,
                       policy.shell_limits, policy.output_preview_bytes)

    def to_bytes(self):
        return canonical_json({"schema": "behave.scope-plan/1", "run_id": self.run_id,
            "author": parse_json(self.author.to_bytes()), "reviewer": self.reviewer.as_dict(),
            "reviewer_policy": self.reviewer_policy.as_dict(), "max_revisions": self.max_revisions})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "run_id", "author", "reviewer", "reviewer_policy", "max_revisions"})
        version(row["schema"], "behave.scope-plan/1")
        result = cls(row["run_id"], RoleJob.from_bytes(canonical_json(row["author"])),
                     AttemptSpec.from_dict(row["reviewer"]), RolePolicy.from_dict(row["reviewer_policy"]),
                     row["max_revisions"])
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "scope plan must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class ScopeResult:
    plan: BlobRef
    status: str
    code: str
    scopes: tuple[BlobRef, ...]
    reviews: tuple[BlobRef, ...]
    phases: tuple[PhaseReceipt, ...]

    def __post_init__(self):
        if type(self.plan) is not BlobRef:
            raise TypeError("scope result requires its plan reference")
        if self.status not in {"review_ready", "revision_limit", "unfinished", "interrupted"}:
            raise ValueError("unknown scope result status")
        text(self.code, "scope result code")
        for refs in (self.scopes, self.reviews):
            if type(refs) is not tuple or any(type(ref) is not BlobRef for ref in refs):
                raise TypeError("scope result requires immutable references")
        if type(self.phases) is not tuple or any(type(p) is not PhaseReceipt or p.process is None
                or p.role not in {"scope_author", "scope_reviewer"} for p in self.phases):
            raise TypeError("scope result requires fixed-role phase receipts")
        if len({(p.revision, p.role) for p in self.phases}) != len(self.phases):
            raise ValueError("duplicated scope phase")
        if not len(self.reviews) <= len(self.scopes) <= len(self.reviews) + 1:
            raise ValueError("scope/review history is incomplete")
        if self.status == "review_ready" and (not self.scopes or len(self.scopes) != len(self.reviews)):
            raise ValueError("review-ready scope requires a completed independent review")

    def to_bytes(self):
        return canonical_json({"schema": "behave.scope-result/1", "plan": self.plan.as_dict(),
            "status": self.status, "code": self.code, "scopes": [r.as_dict() for r in self.scopes],
            "reviews": [r.as_dict() for r in self.reviews], "phases": [p.as_dict() for p in self.phases]})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "plan", "status", "code", "scopes", "reviews", "phases"})
        version(row["schema"], "behave.scope-result/1")
        result = cls(BlobRef.from_dict(row["plan"]), row["status"], row["code"],
                     tuple(BlobRef.from_dict(r) for r in array(row["scopes"])),
                     tuple(BlobRef.from_dict(r) for r in array(row["reviews"])),
                     tuple(PhaseReceipt.from_dict(r) for r in array(row["phases"])))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "scope result must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class ScopeConfiguration:
    plan: ScopePlan
    model_admission: AdmissionPolicy | None = None

    def __post_init__(self):
        if type(self.plan) is not ScopePlan:
            raise TypeError("scope command requires a ScopePlan")
        if self.model_admission is not None and type(self.model_admission) is not AdmissionPolicy:
            raise TypeError("scope command requires an AdmissionPolicy")

    def to_bytes(self):
        return canonical_json({"schema": "behave.scope-configuration/1", "plan": parse_json(self.plan.to_bytes()),
            "model_admission": None if self.model_admission is None else self.model_admission.as_dict()})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "plan", "model_admission"})
        version(row["schema"], "behave.scope-configuration/1")
        policy = row["model_admission"]
        result = cls(ScopePlan.from_bytes(canonical_json(row["plan"])), None if policy is None else
                     AdmissionPolicy(**fields(policy, set(AdmissionPolicy.__dataclass_fields__))))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "scope configuration must be canonical")
        return result

    def preflight(self, reader):
        self.plan.reviewer_policy.check_execution()
        input_closure(self.plan.author, reader)


def _file(path, payload):
    return FileData(FileRef(path, BlobRef.of(payload)), payload)


def scope_inputs(reader, source, guidance, parent=None, *, feedback="", selection=None, brief=None):
    record, files = load_source_record(reader, source)
    body = load_review_guidance(reader, guidance)
    initial = (_file(".scope/source.json", record.to_bytes()), _file(".scope/guidance.md", body),
               *contract_guidance(".scope/"),
               *(_file(".scope/source/" + f.reference.path, f.data) for f in files))
    refs = (*source_closure(reader, source), guidance)
    if brief is not None:
        initial += (_file(".scope/construction_brief.json", read_verified(reader, brief)),)
        refs += (brief,)
    if selection is not None:
        from .selection import selection_closure

        refs += selection_closure(reader, selection, source=source)
    if parent is not None:
        previous, _, _ = load_scope(reader, parent)
        if (previous.source, previous.selection, previous.brief) != (source, selection, brief):
            raise RecordError("scope-source-mismatch", "reopening cannot silently change the pinned source")
        refs += scope_closure(reader, parent)
        initial += (_file(".scope/parent.json", previous.to_bytes()),)
        if previous.contract is not None:
            initial += (FileData(previous.contract, read_verified(reader, previous.contract.content)),)
    if feedback:
        initial += (_file(".scope/feedback.json", feedback.encode()),)
    return initial, tuple(dict.fromkeys(refs))


@dataclass(frozen=True, slots=True)
class ScopeReview:
    proposal: BlobRef
    guidance: BlobRef
    assessments: tuple[CapabilityAssessment, ...]
    findings: tuple[str, ...]

    def __post_init__(self):
        validate_report_fields(self.proposal, self.guidance, self.assessments, self.findings)

    @property
    def supported(self):
        return bool(self.assessments) and not self.findings and all(a.conclusion == "supported" for a in self.assessments)

    def to_bytes(self):
        return canonical_json({"schema": "behave.scope-review/1", "proposal": self.proposal.as_dict(),
            "guidance": self.guidance.as_dict(), "assessments": [a.as_dict() for a in self.assessments],
            "findings": list(self.findings)})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "proposal", "guidance", "assessments", "findings"})
        version(row["schema"], "behave.scope-review/1")
        result = cls(BlobRef.from_dict(row["proposal"]), BlobRef.from_dict(row["guidance"]),
                     tuple(CapabilityAssessment.from_dict(a) for a in array(row["assessments"])),
                     tuple(array(row["findings"])))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "scope review must be canonical")
        return result


def scope_review_inputs(reader: ContentReader, proposal: BlobRef, guidance: BlobRef):
    scope, source, source_files = load_scope(reader, proposal)
    if scope.contract is None:
        raise RecordError("scope-contract-missing", "pre-author review requires an explicit contract")
    files = (_file("review/guidance.md", load_review_guidance(reader, guidance)),
             *contract_guidance("review/"),
             _file("review/source.json", source.to_bytes()), _file("proposal/scope.json", scope.to_bytes()),
             _file("proposal/contract.json", read_verified(reader, scope.contract.content)),
             *(_file("source/" + f.reference.path, f.data) for f in source_files))
    if scope.brief is not None:
        files += (_file("proposal/construction_brief.json", read_verified(reader, scope.brief)),)
    return scope, files


def scope_submission_schema():
    return object_schema({"intent": {"type": "string"}, "capabilities": {"type": "array", "items": {"type": "string"}},
                          "adaptations": {"type": "array", "items": {"type": "string"}},
                          "contract": {"type": "string", "description":
                              "Workspace-relative path to a written TaskContract JSON file, e.g. contract.json."}})


def scope_review_submission_schema():
    citation = object_schema({"path": {"type": "string"}, "first": {"type": "integer", "minimum": 1},
                              "last": {"type": "integer", "minimum": 1}})
    assessment = object_schema({"capability": {"type": "integer", "minimum": 0},
        "conclusion": {"enum": ["supported", "unsupported", "unclear"]}, "rationale": {"type": "string"},
        "citations": {"type": "array", "items": citation}})
    return object_schema({"assessments": {"type": "array", "items": assessment},
                          "findings": {"type": "array", "items": {"type": "string"}}})


def run_scope_proposal(*, source, guidance, reader, journal, backend, spec, parent=None, feedback="",
                       selection=None, brief=None, control=None, model_admission=None, development_tools=()):
    if spec.role != "scope_author":
        raise ValueError("scope proposal requires scope_author role")
    initial, refs = scope_inputs(reader, source, guidance, parent, feedback=feedback, selection=selection, brief=brief)
    journal.objects.publish(reader, refs)

    def submit(workspace, arguments):
        from behave.sim import api

        row = fields(arguments, {"intent", "capabilities", "adaptations", "contract"})
        try:
            payload = workspace.read(row["contract"])
            api.TaskContract.from_dict(parse_json(payload))
            proposal = ScopeProposal(source, row["intent"], tuple(array(row["capabilities"])),
                tuple(array(row["adaptations"])), FileRef(row["contract"], BlobRef.of(payload)), parent, selection, brief)
            packet = proposal.to_bytes()
        except (RecordError, ValueError, TypeError, api.Refused) as error:
            return ToolResult({"error": getattr(error, "code", "scope-arguments-invalid"),
                "detail": str(error),
                "hint": "Follow the submit_scope argument schema: capabilities and adaptations must be "
                        "JSON arrays of strings. contract must name a workspace-relative JSON file, "
                        "not inline text. For TaskContract errors, read .scope/task_contract.md "
                        "and the generated contract examples."})
        workspace.store.put(payload)
        ref = workspace.store.put(packet)
        closure = scope_closure(workspace.store, ref)
        return ToolResult({"scope": ref.as_dict()}, closure, True)

    identity = {"source": source.as_dict(), "guidance": guidance.as_dict(),
                "parent": None if parent is None else parent.as_dict()}
    if selection is not None:
        identity["selection"] = selection.as_dict()
    if brief is not None:
        identity["brief"] = brief.as_dict()
    binding = BlobRef.of(canonical_json(identity)).sha256
    final = Tool("submit_scope", render("synthesis/scope_author/submit"),
        scope_submission_schema(),
        submit, "scope/1:" + binding, terminal=True)
    protected = tuple(sorted({*spec.read_only_paths, *(f.reference.path for f in initial
                                                    if f.reference.path.startswith(".scope/"))}))
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=initial, tools=(*development_tools, final), control=control, model_admission=model_admission,
        initial_messages=role_messages("synthesis/scope_author"))


def run_scope_review(*, proposal, guidance, reader, journal, backend, spec, control=None, model_admission=None,
                     development_tools=()):
    if spec.role != "scope_reviewer":
        raise ValueError("scope review requires scope_reviewer role")
    scope, files = scope_review_inputs(reader, proposal, guidance)
    by_path = {f.reference.path: f.reference for f in files}

    def submit(workspace, arguments):
        row = fields(arguments, {"assessments", "findings"})
        try:
            review = ScopeReview(proposal, guidance, parse_assessments(row["assessments"], by_path),
                                 tuple(array(row["findings"])))
            validate_assessments(review.assessments, scope, files, prefixes={"source", "proposal"})
            payload = review.to_bytes()
        except (KeyError, ValueError, TypeError, UnicodeError) as error:
            return ToolResult({"error": getattr(error, "code", "scope-review-arguments-invalid"),
                               "detail": str(error)})
        ref = workspace.store.put(payload)
        return ToolResult({"scope_review": ref.as_dict()}, (ref,), True)

    binding = BlobRef.of(canonical_json({"proposal": proposal.as_dict(), "guidance": guidance.as_dict()})).sha256
    final = Tool("submit_scope_review", render("synthesis/scope_reviewer/submit"),
        scope_review_submission_schema(),
        submit, "scope-review/1:" + binding, terminal=True)
    protected = tuple(sorted({*spec.read_only_paths, *by_path}))
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=files, tools=(*development_tools, final),
        control=control, model_admission=model_admission,
        initial_messages=role_messages("synthesis/scope_reviewer"))


def _require(condition, detail):
    if not condition:
        raise RecordError("scope-evidence-mismatch", detail)


def _read(reader, ref):
    return read_verified(reader, ref)


def load_scope_preparation(reader, head: BlobRef) -> tuple[ScopeResult, CostLedger]:
    events = load_chain(reader, head)
    _require(events and events[0].kind == "scope_started" and events[-1].kind == "scope_finished",
             "scope preparation must have a terminal sealed history")
    config = fields(parse_json(_read(reader, BlobRef.from_dict(fields(
        thaw_json(events[0].payload), {"configuration"})["configuration"]))),
        {"coordinator", "plan", "model_admission", "development_admission"})
    _require(config["coordinator"] == "behave.scope-preparation/1" and config["development_admission"] is None,
             "unsupported scope coordinator or development capabilities")
    plan = ScopePlan.from_bytes(_read(reader, BlobRef.from_dict(config["plan"])))
    result = ScopeResult.from_bytes(_read(reader, BlobRef.from_dict(fields(
        thaw_json(events[-1].payload), {"result"})["result"])))
    _require(config["plan"] == result.plan.as_dict(), "scope result belongs to a different plan")
    middle = events[1:-1]
    _require(len(middle) == 2 * len(result.phases) and bool(result.phases), "incomplete scope phase history")
    inputs, ledgers, scopes, reviews = plan.author.inputs, [], [], []
    supported = False
    for index, phase in enumerate(result.phases):
        revision, role = index // 2, ("scope_author", "scope_reviewer")[index % 2]
        requested, settled = middle[2 * index:2 * index + 2]
        _require((phase.revision, phase.role) == (revision, role) and revision < plan.max_revisions,
                 "scope phase order or revision budget changed")
        _require(requested.kind == "scope_phase_requested" and settled.kind == "scope_phase_settled",
                 "scope handoff lacks request/settlement")
        req = fields(thaw_json(requested.payload), {"revision", "role", "job"})
        done = fields(thaw_json(settled.payload), {"phase", "output"})
        expected_inputs = inputs if role == "scope_author" else ScopeReviewInputs(scopes[-1], inputs.guidance)
        job = plan.job(revision, role, expected_inputs)
        _require((req["revision"], req["role"]) == (revision, role)
                 and req["job"] == BlobRef.of(job.to_bytes()).as_dict() and done["phase"] == phase.as_dict(),
                 "scope phase differs from its planned role or inputs")
        ledgers.append(process_billing(reader, phase, job, config))
        chain = load_chain(reader, phase.head)
        if done["output"] is None:
            _require(index == len(result.phases) - 1 and result.status in {"unfinished", "interrupted"},
                     "a missing scope output cannot advance the workflow")
            break
        output = BlobRef.from_dict(done["output"])
        _require(chain and chain[-1].kind == "terminal", "scope output lacks a terminal role")
        terminal = thaw_json(chain[-1].payload)
        key = "scope" if role == "scope_author" else "scope_review"
        _require(terminal["reason"] == "final_action" and terminal["final_action"] == {key: output.as_dict()},
                 "scope handoff differs from the role's submitted output")
        if role == "scope_author":
            scope, _, _ = load_scope(reader, output)
            _require((scope.source, scope.parent, scope.selection, scope.brief) ==
                     (inputs.source, inputs.parent, inputs.selection, inputs.brief)
                     and scope.contract is not None,
                     "scope author changed source, ancestry or omitted the contract")
            scopes.append(output)
        else:
            review = ScopeReview.from_bytes(_read(reader, output))
            _require((review.proposal, review.guidance) == (scopes[-1], inputs.guidance),
                     "scope review targets another proposal or guidance")
            scope, files = scope_review_inputs(reader, scopes[-1], inputs.guidance)
            _require(chain[0].kind == "initialized", "scope reviewer lacks its initial view")
            settings = parse_json(_read(reader, BlobRef.from_dict(thaw_json(chain[0].payload)["configuration"])))
            snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, BlobRef.from_dict(settings["workspace"])))
            _require(snapshot == WorkspaceSnapshot(tuple(f.reference for f in files)),
                     "scope reviewer did not receive the independent source/proposal view")
            spec = AttemptSpec.from_dict(settings["spec"])
            _require(set(spec.read_only_paths) >= {f.reference.path for f in files},
                     "scope reviewer could modify the evidence inputs")
            validate_assessments(review.assessments, scope, files, prefixes={"source", "proposal"})
            reviews.append(output)
            supported = review.supported
            _require(not supported or index == len(result.phases) - 1, "a supported scope must terminate this run")
            inputs = replace(inputs, parent=scopes[-1], feedback=review.to_bytes().decode())
    _require(tuple(scopes) == result.scopes and tuple(reviews) == result.reviews, "scope artifacts changed")
    if result.status == "review_ready":
        _require(supported, "review-ready scope lacks supported independent evidence")
    elif result.status == "revision_limit":
        _require(not supported and len(reviews) == plan.max_revisions, "revision limit was not reached")
    else:
        _require(not supported, "supported scope was mislabeled as unfinished")
    return result, CostLedger.combine(tuple(ledgers))


def preparation_closure(reader, head: BlobRef, *, scope: BlobRef) -> tuple[BlobRef, ...]:
    result, _ = load_scope_preparation(reader, head)
    _require(result.status == "review_ready" and result.scopes[-1] == scope,
             "authoring requires this run's final reviewed scope, not an earlier proposal")
    events = load_chain(reader, head)
    return tuple(dict.fromkeys(ref for event in events for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))


def prepare_scope(*, plan: ScopePlan, reader, run: JournalDirectory, control=None, model_pool=None) -> ScopeResult:
    if type(plan) is not ScopePlan or type(run) is not JournalDirectory:
        raise TypeError("scope preparation requires ScopePlan and JournalDirectory")
    control = AttemptControl() if control is None else control
    if type(control) is not AttemptControl:
        raise TypeError("scope preparation requires AttemptControl")
    if model_pool is not None and type(model_pool) is not ResourcePool:
        raise TypeError("scope admission requires ResourcePool")
    plan_bytes = plan.to_bytes()
    with run.owner():
        objects = run.objects
        plan_ref = objects.put(plan_bytes)
        configuration = objects.put(canonical_json({"coordinator": "behave.scope-preparation/1",
            "plan": plan_ref.as_dict(), "model_admission": None if model_pool is None else model_pool.policy.identity,
            "development_admission": None}))
        tape = Tape(run.journal("scope"))
        started = bool(tape.events)
        if started:
            tape.record("scope_started", {"configuration": configuration.as_dict()})
        else:
            refs = input_closure(plan.author, reader)
            objects.publish(reader, refs)
            tape.record("scope_started", {"configuration": configuration.as_dict()}, (configuration, plan_ref, *refs))
        if tape.events[-1].kind == "scope_finished":
            from .scope import load_scope_preparation

            head, _ = run.seal("scope")
            result, _ = load_scope_preparation(objects, head)
            for phase in result.phases:
                verify_process_seal(run, phase)
                phase_head, _ = run.seal("revisions", f"r{phase.revision}", phase.role)
                if phase_head != phase.head:
                    raise RecordError("scope-phase-mismatch", "role journal changed after termination")
            return result
        scopes, reviews, phases = [], [], []
        active = None

        def phase(revision, role, inputs):
            nonlocal active
            active = (revision, role)
            job = plan.job(revision, role, inputs)
            job_ref = objects.put(job.to_bytes())
            tape.record("scope_phase_requested", {"revision": revision, "role": role, "job": job_ref.as_dict()}, (job_ref,))
            return execute_role(job, run=run, revision=revision, role=role, cancelled=control.cancelled,
                                model_pool=model_pool, development_pool=None)

        def settle(output=None, refs=()):
            nonlocal active
            revision, role = active
            receipt, closure = seal_phase(run, revision, role, process_backed=True)
            phases.append(receipt)
            tape.record("scope_phase_settled", {"phase": receipt.as_dict(),
                        "output": None if output is None else output.as_dict()}, (*closure, *refs))
            active = None

        def finish(status, code):
            result = ScopeResult(plan_ref, status, code, tuple(scopes), tuple(reviews), tuple(phases))
            ref = objects.put(result.to_bytes())
            tape.record("scope_finished", {"result": ref.as_dict()}, (ref,))
            tape.finish()
            return result

        inputs = plan.author.inputs
        try:
            for revision in range(plan.max_revisions):
                authored = phase(revision, "scope_author", inputs)
                if authored.final_action is None:
                    settle()
                    return finish("unfinished", "scope-author-" + authored.reason)
                ref = BlobRef.from_dict(fields(thaw_json(authored.final_action), {"scope"})["scope"])
                scope, _, _ = load_scope(objects, ref)
                if ((scope.source, scope.parent, scope.selection, scope.brief) !=
                        (inputs.source, inputs.parent, inputs.selection, inputs.brief)
                        or scope.contract is None):
                    raise RecordError("scope-handoff-mismatch", "scope author changed source or ancestry")
                scopes.append(ref)
                settle(ref, scope_closure(objects, ref))
                reviewed = phase(revision, "scope_reviewer", ScopeReviewInputs(ref, inputs.guidance))
                if reviewed.final_action is None:
                    settle()
                    return finish("unfinished", "scope-reviewer-" + reviewed.reason)
                review_ref = BlobRef.from_dict(fields(thaw_json(reviewed.final_action), {"scope_review"})["scope_review"])
                review = ScopeReview.from_bytes(read_verified(objects, review_ref))
                if (review.proposal, review.guidance) != (ref, inputs.guidance):
                    raise RecordError("scope-handoff-mismatch", "reviewer answered another scope or guidance")
                _, files = scope_review_inputs(objects, ref, inputs.guidance)
                validate_assessments(review.assessments, scope, files, prefixes={"source", "proposal"})
                reviews.append(review_ref)
                settle(review_ref, (review_ref,))
                if review.supported:
                    return finish("review_ready", "scope-review-supported")
                inputs = replace(inputs, parent=ref, feedback=review.to_bytes().decode())
            return finish("revision_limit", "scope-revision-budget-exhausted")
        except AttemptInterrupted as error:
            if active is not None:
                settle()
            return finish("interrupted", error.code)
