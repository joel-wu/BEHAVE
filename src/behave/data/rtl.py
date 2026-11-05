"""Retry a settled B/R no-result check without resampling its RTL author.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import Event, load_chain
from behave.agent.loop import run_attempt
from behave.agent.process_worker import completed_attempt
from behave.agent.prompting import render, role_messages
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.trajectory import CostLedger
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import (
    ArtifactSelection,
    BlobRef,
    ContentReader,
    FileData,
    FileRef,
    SourceArtifact,
    read_verified,
)
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, thaw_json, version
from behave.task_execution.comparison import COMPARISON_BACKEND, ComparisonInputs
from behave.task_execution.feedback import comparison_feedback

from .checks import ConcreteCheck, check_cleanup, check_sources, load_check
from .drafts import TaskDraft, draft_closure, load_draft, load_source_artifact, load_workspace
from .evidence import evidence_files
from .phase_journal import RevisionPhases, verify_process_seal
from .role import RoleJob, RtlInputs, process_billing
from .role_policy import RtlBuildPolicy
from .synthesis import execute_role
from .synthesis_records import PhaseReceipt, SynthesisResult


@dataclass(frozen=True)
class RtlContinuation:
    prefix: tuple[Event, ...]
    phases: tuple[PhaseReceipt, ...]
    ledgers: tuple[CostLedger, ...]

    def contains(self, revision, role):
        return any((p.revision, p.role) == (revision, role) for p in self.phases)

    def completed(self, run, revision):
        journal = run.journal("revisions", f"r{revision}", "rtl_author")
        config = BlobRef.from_dict(thaw_json(journal.load()[0].payload)["configuration"])
        from behave.agent.attempt import AttemptSpec

        spec = AttemptSpec.from_dict(parse_json(read_verified(run.objects, config))["spec"])
        return completed_attempt(spec, journal, namespace="construction")


def load_rtl_continuation(reader, configuration):
    from .role_policy import RtlBuildPolicy
    from .rtl import verify_rtl

    head = BlobRef.from_dict(configuration["continuation_from"])
    events = load_chain(reader, head)
    if not events or events[-1].kind != "rtl_finished":
        raise RecordError("continuation-not-settled", "RTL continuation needs a terminal branch")
    old = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    if old["run_id"] == configuration["run_id"]:
        raise RecordError("continuation-run-id", "RTL continuation needs a new run_id")
    policy = lambda row: {k: v for k, v in row.items() if k not in {"schema", "run_id", "continuation_from"}}
    if canonical_json(policy(old)) != canonical_json(policy(configuration)):
        raise RecordError("continuation-runtime-mismatch", "RTL continuation reuses its draft and runtime policy")
    result, ledgers, _ = verify_rtl(reader, head, run_id=old["run_id"],
        policy=RtlBuildPolicy.from_dict(old["policy"]), draft=BlobRef.from_dict(old["draft"]), resources=old,
        continuation_from=None if "continuation_from" not in old else BlobRef.from_dict(old["continuation_from"]))
    # verify_rtl binds this status to score=None and quiescent cleanup.
    if result.status != "check_unavailable":
        raise RecordError("continuation-check-not-ready", "only a settled, quiescent RTL no-result check can be retried")
    revision = result.phases[-1].revision
    boundary = next(i for i, e in enumerate(events) if e.kind == "phase_requested"
                    and (e.payload["revision"], e.payload["role"]) == (revision, "rtl_check"))
    return RtlContinuation(events[:boundary], result.phases[:-1], ledgers)


def verify_rtl_continuation(reader, configuration, events):
    if "continuation_from" not in configuration:
        return None
    continuation = load_rtl_continuation(reader, configuration)
    if len(events) < len(continuation.prefix) or any(
            (old.kind, thaw_json(old.payload), old.artifacts) != (new.kind, thaw_json(new.payload), new.artifacts)
            for old, new in zip(continuation.prefix[1:], events[1:len(continuation.prefix)], strict=True)):
        raise RecordError("continuation-prefix-mismatch", "RTL continuation changed inherited history")
    return continuation


def seed_rtl_continuation(reader, run, configuration):
    references = {}

    class Reads:
        def read(self, ref):
            payload = read_verified(reader, ref)
            references[ref] = None
            return payload

    tracked = Reads()
    continuation = load_rtl_continuation(tracked, configuration)
    journals = []
    for phase in continuation.phases:
        path = ("revisions", f"r{phase.revision}", phase.role)
        journals.append((path, load_chain(tracked, phase.head)))
        if phase.process is not None:
            for channel in ("supervision", "provider", "development"):
                journals.append(((*path[:-1], phase.role + "_" + channel),
                                 load_chain(tracked, getattr(phase.process, channel))))
    run.objects.publish(tracked, tuple(references))
    config = run.objects.put(canonical_json(configuration))
    tape = Tape(run.journal("rtl"))
    tape.record("rtl_started", {"configuration": config.as_dict()}, (config, *references))
    for event in continuation.prefix[1:]:
        tape.record(event.kind, thaw_json(event.payload), event.artifacts)
    for path, events in journals:
        tape = Tape(run.journal(*path))
        for event in events:
            tape.record(event.kind, thaw_json(event.payload), event.artifacts)
    return continuation


@dataclass(frozen=True, slots=True)
class RtlCandidate:
    draft: BlobRef
    rtl: SourceArtifact | None
    workspace: BlobRef
    parent: BlobRef | None = None
    findings: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.draft) is not BlobRef or type(self.workspace) is not BlobRef:
            raise TypeError("RTL submission requires a draft and workspace")
        if self.parent is not None and type(self.parent) is not BlobRef:
            raise TypeError("RTL parent requires an immutable candidate")
        if self.rtl is not None and type(self.rtl) is not SourceArtifact:
            raise TypeError("RTL selection requires SourceArtifact")
        if type(self.findings) is not tuple:
            raise TypeError("RTL findings must be a tuple")
        for finding in self.findings:
            text(finding, "RTL finding")
        if self.rtl is None and not self.findings:
            raise ValueError("an empty RTL submission requires findings")

    def to_bytes(self):
        return canonical_json({"schema": "behave.rtl-candidate/1", "draft": self.draft.as_dict(),
            "rtl": None if self.rtl is None else self.rtl.as_dict(), "workspace": self.workspace.as_dict(),
            "parent": None if self.parent is None else self.parent.as_dict(), "findings": list(self.findings)})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "draft", "rtl", "workspace", "parent", "findings"})
        version(row["schema"], "behave.rtl-candidate/1")
        result = cls(BlobRef.from_dict(row["draft"]),
            None if row["rtl"] is None else SourceArtifact.from_dict(row["rtl"]),
            BlobRef.from_dict(row["workspace"]), None if row["parent"] is None else BlobRef.from_dict(row["parent"]),
            tuple(array(row["findings"])))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "RTL candidate must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class RtlBuildResult:
    draft: BlobRef
    status: str
    code: str
    candidates: tuple[BlobRef, ...]
    checks: tuple[BlobRef, ...]
    phases: tuple[PhaseReceipt, ...]

    def __post_init__(self):
        if type(self.draft) is not BlobRef:
            raise TypeError("RTL build requires a frozen draft")
        if self.status not in {"passed", "revision_limit", "unfinished", "review_requested", "check_unavailable",
                               "cleanup_unconfirmed", "interrupted"}:
            raise ValueError("unknown RTL build status")
        text(self.code, "RTL build code")
        for refs in (self.candidates, self.checks):
            if type(refs) is not tuple or any(type(r) is not BlobRef for r in refs):
                raise TypeError("RTL build requires immutable references")
        if type(self.phases) is not tuple or any(type(p) is not PhaseReceipt or
                p.role not in {"rtl_author", "rtl_check"} for p in self.phases):
            raise TypeError("RTL build requires its own phase receipts")
        if len({(p.revision, p.role) for p in self.phases}) != len(self.phases):
            raise ValueError("duplicated RTL phase")
        if not len(self.checks) <= len(self.candidates) <= len(self.checks) + 1:
            raise ValueError("RTL candidate/check history is incomplete")
        if self.status == "passed" and (not self.checks or len(self.candidates) != len(self.checks)):
            raise ValueError("passing RTL requires a completed comparison")

    def to_bytes(self):
        return canonical_json({"schema": "behave.rtl-build-result/1", "draft": self.draft.as_dict(),
            "status": self.status, "code": self.code, "candidates": [r.as_dict() for r in self.candidates],
            "checks": [r.as_dict() for r in self.checks], "phases": [p.as_dict() for p in self.phases]})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "draft", "status", "code", "candidates", "checks", "phases"})
        version(row["schema"], "behave.rtl-build-result/1")
        result = cls(BlobRef.from_dict(row["draft"]), row["status"], row["code"],
            tuple(BlobRef.from_dict(r) for r in array(row["candidates"])),
            tuple(BlobRef.from_dict(r) for r in array(row["checks"])),
            tuple(PhaseReceipt.from_dict(r) for r in array(row["phases"])))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "RTL build result must be canonical")
        return result


RTL_VERSION = "rtl-construction/1"


def _record(reader, ref, cls):
    return cls.from_bytes(read_verified(reader, ref))


def _file(path, data):
    return FileData(FileRef(path, BlobRef.of(data)), data)


def frozen_files(reader, draft):
    loaded = load_draft(reader, draft)
    task = loaded.record.task
    files = (*loaded.reader_files, *loaded.source(task.reference_behavior).files)
    if any(f.reference.path.startswith(".rtl/") for f in files):
        raise RecordError("rtl-reserved-path", "task files overlap RTL role context")
    return (*files, _file(".rtl/scope.json", read_verified(reader, loaded.record.scope)),
            _file(".rtl/run_policy.json", read_verified(reader, task.run_policy)))


def load_candidate(reader, ref, *, draft):
    record = _record(reader, ref, RtlCandidate)
    if record.draft != draft:
        raise RecordError("rtl-draft-mismatch", "RTL submission targets another task revision")
    files = load_workspace(reader, record.workspace)
    frozen = frozen_files(reader, draft)
    if any(f not in files for f in frozen):
        raise RecordError("rtl-frozen-input-changed", "RTL workspace changed a frozen task input")
    if record.rtl is not None:
        selected = record.rtl.files
        if any(f not in tuple(x.reference for x in files) for f in selected):
            raise RecordError("rtl-snapshot-mismatch", "selected RTL differs from its workspace")
        if any(f.path in {x.reference.path for x in frozen} for f in selected):
            raise RecordError("rtl-frozen-input-selected", "RTL cannot select frozen task inputs")
        # Reuse the task's public/private separation rules without replacing its
        # draft or pretending the original author submitted this candidate.
        loaded = load_draft(reader, draft)
        TaskDraft(loaded.record.scope, replace(loaded.record.task, reference_rtl=record.rtl), record.workspace)
    return record, files


def comparison_inputs(reader, draft, candidate):
    record, _ = load_candidate(reader, candidate, draft=draft)
    if record.rtl is None or record.findings:
        raise RecordError("rtl-review-requested", "unresolved findings cannot be certified by a comparison")
    loaded = load_draft(reader, draft)
    task = loaded.record.task
    return ComparisonInputs("rtl", load_source_artifact(reader, record.rtl),
        loaded.source(task.reference_behavior), loaded.file(task.contract.path),
        read_verified(reader, task.run_policy), loaded.judge_policy)


def role_inputs(reader, inputs):
    """The same view is used by dispatch, cold recovery and exported admission."""
    frozen = frozen_files(reader, inputs.draft)
    initial, refs, evidence = frozen, (), ()
    if inputs.parent is not None:
        previous, initial = load_candidate(reader, inputs.parent, draft=inputs.draft)
        refs = (inputs.parent, previous.workspace, *(f.reference.content for f in initial))
        check = load_check(reader, inputs.check)
        expected = comparison_inputs(reader, inputs.draft, inputs.parent)
        if (read_verified(reader, check.inputs) != canonical_json(expected.as_dict())
                or check.result.score != 0 or not check.result.cleanup.quiescent):
            raise RecordError("rtl-repair-mismatch", "repair requires a clean failed comparison of this parent")
        from behave.agent.journal import load_chain

        events = load_chain(reader, inputs.check)
        refs += tuple(r for e in events for r in (*e.artifacts, BlobRef.of(e.to_bytes())))
        refs += check.result.closure
        evidence = evidence_files(".rtl/evidence", canonical_json(check.as_dict()), check.result.evidence, reader)
        _, feedback_files = comparison_feedback(check.result, reader, prefix=".rtl/comparison")
        evidence += feedback_files
        # Keep the previous workspace, but only the latest comparison view.
        initial = (*(file for file in initial if not file.reference.path.startswith(
            (".rtl/evidence/", ".rtl/comparison/"))), *evidence)
    return (*frozen, *evidence), initial, refs, ()


def submission_tool(inputs):
    bundle = object_schema({"files": {"type": "array", "items": {"type": "string"}},
                           "entry_file": {"type": "string"}, "entry_symbol": {"type": "string"}})

    def submit(workspace, arguments):
        row = fields(arguments, {"rtl", "findings"})
        source = None
        if row["rtl"] is not None:
            selection = ArtifactSelection.from_dict(row["rtl"])
            source = SourceArtifact(tuple(_file(p, workspace.read(p)).reference for p in selection.files),
                                    selection.entry_file, selection.entry_symbol)
        root, closure = workspace.checkpoint()
        record = RtlCandidate(inputs.draft, source, root, inputs.parent, tuple(array(row["findings"])))
        ref = workspace.store.put(record.to_bytes())
        try:
            load_candidate(workspace.store, ref, draft=inputs.draft)
        except RecordError as error:
            if error.code not in {"rtl-frozen-input-selected", "draft-public-private-overlap"}:
                raise
            return ToolResult({"error": error.code})
        return ToolResult({"rtl_candidate": ref.as_dict()}, (*closure, ref), True)

    return Tool("submit_rtl", render("synthesis/rtl_author/submit"),
        object_schema({"rtl": {"anyOf": [bundle, {"type": "null"}]},
                       "findings": {"type": "array", "items": {"type": "string"}}}),
        submit, RTL_VERSION + ":" + BlobRef.of(canonical_json(inputs.as_dict())).sha256, terminal=True)


def run_rtl(*, inputs, reader, journal, backend, spec, development_tools=(), control=None, model_admission=None):
    if spec.role != "rtl_author" or any(t.terminal for t in development_tools):
        raise ValueError("RTL construction requires its role and nonterminal development tools")
    frozen, initial, refs, evidence = role_inputs(reader, inputs)
    from .drafts import draft_closure

    journal.objects.publish(reader, (*draft_closure(reader, inputs.draft), *refs))
    protected = tuple(sorted({*spec.read_only_paths, *(f.reference.path for f in frozen)}))
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=initial, tools=(*development_tools, submission_tool(inputs)),
        control=control, model_admission=model_admission,
        initial_messages=role_messages("synthesis/rtl_author", next_step=render(
            "synthesis/rtl_author/repair" if inputs.parent else "synthesis/rtl_author/first")))


def configuration(run_id: str, policy: RtlBuildPolicy, draft: BlobRef, resources: Mapping,
                  continuation_from: BlobRef | None = None) -> dict:
    row = {"schema": "behave.rtl-build/1", "run_id": run_id, "policy": policy.as_dict(),
            "draft": draft.as_dict(), "comparison": COMPARISON_BACKEND,
            **{key: resources[key] for key in ("model_admission", "check_admission", "development_admission")}}
    if continuation_from is not None:
        row.update(schema="behave.rtl-build/2", continuation_from=continuation_from.as_dict())
    return row


def job_for(run_id: str, policy: RtlBuildPolicy, revision: int, inputs: RtlInputs) -> RoleJob:
    if type(revision) is not int or not 0 <= revision < policy.max_revisions:
        raise ValueError("RTL job exceeds its revision budget")
    spec = replace(policy.attempt, attempt_id=f"{run_id}:r{revision}:rtl_author:{policy.attempt.attempt_id}")
    execution = policy.execution
    return RoleJob(inputs, spec, execution.model, execution.python_limits, execution.cocotb_policy,
                   execution.log_bytes, execution.shell_limits, execution.output_preview_bytes)


def verify_terminal(run: JournalDirectory, result: RtlBuildResult, *, check_pool: ResourcePool | None = None) -> None:
    for phase in result.phases:
        if phase.process is not None:
            verify_process_seal(run, phase)
        head, _ = run.seal("revisions", f"r{phase.revision}", phase.role)
        if head != phase.head:
            raise RecordError("rtl-phase-mismatch", "RTL journal changed after termination")
        if phase.role == "rtl_check" and check_pool is not None:
            check_pool.quarantine(check_cleanup(run.journal("revisions", f"r{phase.revision}", phase.role)))


def build_rtl(*, run_id: str, policy: RtlBuildPolicy, draft: BlobRef, reader: ContentReader, run: JournalDirectory,
              cancelled: Callable[[], bool] = lambda: False, model_pool: ResourcePool | None = None,
              check_pool: ResourcePool | None = None, development_pool: ResourcePool | None = None,
              continuation_from: BlobRef | None = None) -> RtlBuildResult:
    """A separate child directory owns process/check history and all repair workspaces."""
    with run.owner():
        objects = run.objects
        resources = {key: None if pool is None else pool.policy.identity for key, pool in (
            ("model_admission", model_pool), ("check_admission", check_pool),
            ("development_admission", development_pool))}
        settings = configuration(run_id, policy, draft, resources, continuation_from)
        continuation = None
        if continuation_from is not None:
            from .rtl import seed_rtl_continuation

            pinned = objects if run.journal("rtl").load() else reader
            continuation = seed_rtl_continuation(pinned, run, settings)
        config = objects.put(canonical_json(settings))
        tape = Tape(run.journal("rtl"))
        if tape.events:
            tape.record("rtl_started", {"configuration": config.as_dict()})
        else:
            refs = draft_closure(reader, draft)
            objects.publish(reader, refs)
            tape.record("rtl_started", {"configuration": config.as_dict()}, (config, *refs))
        if tape.events[-1].kind == "rtl_finished":
            ref = BlobRef.from_dict(fields(thaw_json(tape.events[-1].payload), {"result"})["result"])
            result = RtlBuildResult.from_bytes(read_verified(objects, ref))
            verify_terminal(run, result, check_pool=check_pool)
            return result
        candidates: list[BlobRef] = []
        checks: list[BlobRef] = []
        phases = RevisionPhases(run, tape, process_roles=frozenset({"rtl_author"}))

        def finish(status, code):
            result = RtlBuildResult(draft, status, code, tuple(candidates), tuple(checks), tuple(phases.completed))
            ref = objects.put(result.to_bytes())
            tape.record("rtl_finished", {"result": ref.as_dict()}, (ref,))
            tape.finish()
            return result

        try:
            for revision in range(policy.max_revisions):
                parent = candidates[-1] if candidates else None
                previous_check = phases.completed[-1].head if checks else None
                inputs = RtlInputs(draft, parent, previous_check)
                phases.begin(revision, "rtl_author", inputs.as_dict())
                if continuation is not None and continuation.contains(revision, "rtl_author"):
                    outcome = continuation.completed(run, revision)
                else:
                    outcome = execute_role(job_for(run_id, policy, revision, inputs), run=run, revision=revision,
                        role="rtl_author", cancelled=cancelled, model_pool=model_pool, development_pool=development_pool)
                if outcome.final_action is None:
                    phases.settle()
                    return finish("unfinished", "rtl-author-" + outcome.reason)
                candidate = BlobRef.from_dict(fields(thaw_json(outcome.final_action), {"rtl_candidate"})["rtl_candidate"])
                record, files = load_candidate(objects, candidate, draft=draft)
                if record.parent != parent:
                    raise RecordError("rtl-parent-mismatch", "RTL author changed the repair lineage")
                candidates.append(candidate)
                phases.settle(candidate.as_dict(),
                        (candidate, record.workspace, *(f.reference.content for f in files)))
                if record.findings:
                    return finish("review_requested", "rtl-task-review-requested")
                journal = phases.begin(revision, "rtl_check", {"candidate": candidate.as_dict()})
                report = check_sources(comparison_inputs(objects, draft, candidate),
                    journal=journal, admission=check_pool, cancelled=cancelled)
                ref = objects.put(canonical_json(report.as_dict()))
                checks.append(ref)
                phases.settle(ref.as_dict(), (ref,))
                if not report.result.cleanup.quiescent:
                    return finish("cleanup_unconfirmed", report.result.code)
                if report.result.score is None:
                    return finish("check_unavailable", report.result.code)
                if report.result.score == 1:
                    return finish("passed", "finite-rtl-comparison-agrees")
            return finish("revision_limit", "rtl-revision-budget-exhausted")
        except AttemptInterrupted as error:
            if phases.active is not None:
                phases.settle()
            return finish("interrupted", error.code)


def _require(condition, detail):
    if not condition:
        raise RecordError("rtl-evidence-mismatch", detail)


def _read(reader, ref):
    return read_verified(reader, ref)


def verify_rtl(reader: ContentReader, head: BlobRef, *, run_id: str, policy: RtlBuildPolicy,
               draft: BlobRef, resources: Mapping, continuation_from: BlobRef | None = None
               ) -> tuple[RtlBuildResult, tuple[CostLedger, ...], SourceArtifact | None]:
    """Return verified result, all role ledgers and optional passing artifact; never execute."""
    events = load_chain(reader, head)
    _require(events and events[0].kind == "rtl_started" and events[-1].kind == "rtl_finished",
             "auxiliary RTL branch is not terminal")
    config = parse_json(_read(reader, BlobRef.from_dict(fields(thaw_json(events[0].payload), {"configuration"})["configuration"])))
    _require(canonical_json(config) == canonical_json(configuration(run_id, policy, draft, resources, continuation_from)),
             "RTL policy, task or resource identity changed")
    from .rtl import verify_rtl_continuation

    continuation = verify_rtl_continuation(reader, config, events)
    result = RtlBuildResult.from_bytes(_read(reader, BlobRef.from_dict(fields(thaw_json(events[-1].payload), {"result"})["result"])))
    _require(result.draft == draft, "RTL result targets another draft")
    middle = events[1:-1]
    _require(len(middle) == 2 * len(result.phases), "RTL phase handoffs are incomplete")
    candidates, checks, ledgers = [], [], []
    previous = check_head = None
    last_report = last_candidate = None
    expected_role, revision = "rtl_author", 0
    for index, phase in enumerate(result.phases):
        request, settled = middle[2 * index:2 * index + 2]
        _require((request.kind, settled.kind) == ("phase_requested", "phase_settled"), "RTL handoff order changed")
        row = fields(thaw_json(request.payload), {"revision", "role", "inputs"})
        outcome = fields(thaw_json(settled.payload), {"phase", "output"})
        _require((phase.revision, phase.role) == (revision, expected_role) and revision < policy.max_revisions,
                 "RTL phase exceeds its order or budget")
        _require((row["revision"], row["role"], outcome["phase"]) == (revision, expected_role, phase.as_dict()),
                 "RTL coordinator and receipt differ")
        output = None if outcome["output"] is None else BlobRef.from_dict(outcome["output"])
        if expected_role == "rtl_author":
            inputs = RtlInputs(draft, previous, check_head)
            _require(row["inputs"] == inputs.as_dict(), "RTL repair inputs changed")
            job = job_for(run_id, policy, revision, inputs)
            inherited = continuation is not None and continuation.contains(revision, "rtl_author")
            ledgers.append(continuation.ledgers[len(ledgers)] if inherited else process_billing(reader, phase, job, config))
            role_events = load_chain(reader, phase.head)
            if role_events and not inherited:
                settings = parse_json(_read(reader, BlobRef.from_dict(thaw_json(role_events[0].payload)["configuration"])))
                frozen, initial, _, _ = role_inputs(reader, inputs)
                expected_paths = tuple(sorted({*job.attempt.read_only_paths, *(f.reference.path for f in frozen)}))
                _require(settings["spec"] == replace(job.attempt, read_only_paths=expected_paths).as_dict(),
                         "RTL attempt changed its frozen input protections")
                snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, BlobRef.from_dict(settings["workspace"])))
                _require(snapshot == WorkspaceSnapshot(tuple(f.reference for f in initial)), "RTL input view changed")
                tool = submission_tool(inputs)
                _require(tool.declaration() in settings["tools"], "RTL submission contract changed")
            if output is None:
                _require(index == len(result.phases) - 1 and result.status in {"unfinished", "interrupted"},
                         "empty RTL submission cannot continue or pass")
                if role_events and role_events[-1].kind == "terminal":
                    _require(role_events[-1].payload["final_action"] is None, "RTL terminal output was omitted")
                continue
            _require(role_events and role_events[-1].kind == "terminal", "RTL handoff has no terminal submission")
            terminal = thaw_json(role_events[-1].payload)
            _require(terminal["reason"] == "final_action" and terminal["final_action"] == {"rtl_candidate": output.as_dict()},
                     "RTL handoff differs from submitted candidate")
            last_candidate, _ = load_candidate(reader, output, draft=draft)
            _require(last_candidate.parent == previous, "RTL parent lineage changed")
            candidates.append(output)
            previous, expected_role = output, "rtl_check"
        else:
            _require(phase.process is None and row["inputs"] == {"candidate": previous.as_dict()}, "RTL check subject changed")
            if output is None:
                _require(index == len(result.phases) - 1 and result.status == "interrupted", "empty RTL check cannot pass")
                # An unresolved dispatch must never be published or counted as clean.
                _require(not any(e.kind == "check_requested" for e in load_chain(reader, phase.head)),
                         "RTL comparison has an unresolved dispatch")
                continue
            last_report = load_check(reader, phase.head)
            _require(ConcreteCheck.from_dict(parse_json(_read(reader, output))) == last_report,
                     "RTL summary differs from sealed comparison")
            start = load_chain(reader, phase.head)[0].payload
            _require(start["backend"] == COMPARISON_BACKEND and start["admission"] == resources["check_admission"],
                     "RTL comparison policy changed")
            expected = comparison_inputs(reader, draft, previous)
            _require(_read(reader, last_report.inputs) == canonical_json(expected.as_dict()), "RTL comparison inputs changed")
            for ref in last_report.result.closure:
                read_verified(reader, ref)
            checks.append(output)
            check_head = phase.head
            expected_role, revision = "rtl_author", revision + 1
    _require((tuple(candidates), tuple(checks)) == (result.candidates, result.checks), "RTL summary dropped revision evidence")
    if result.status in {"passed", "revision_limit", "check_unavailable", "cleanup_unconfirmed"}:
        _require(last_report is not None and expected_role == "rtl_author", "RTL outcome lacks its final check")
        outcome = last_report.result
        expected_status = ("cleanup_unconfirmed" if not outcome.cleanup.quiescent else
                           "check_unavailable" if outcome.score is None else
                           "passed" if outcome.score == 1 else "revision_limit")
        _require(result.status == expected_status, "RTL status differs from actual comparison")
        if result.status == "revision_limit":
            _require(revision == policy.max_revisions, "RTL budget was not exhausted")
    elif result.status == "review_requested":
        _require(last_candidate is not None and bool(last_candidate.findings) and expected_role == "rtl_check",
                 "RTL review request lacks findings")
    return result, tuple(ledgers), last_candidate.rtl if result.status == "passed" else None


def synthesis_rtl(reader, plan, result, resources, events):
    """Enforce planned branch presence and bind it to the last reviewed draft."""
    expected = plan.rtl is not None and result.status == "review_ready"
    from .contract import effective_draft

    latest = effective_draft(reader, result.drafts, result.phases) if expected else None
    _require((result.rtl_head is not None) == expected, "planned RTL evidence is missing or unplanned")
    branch = [(e.kind, thaw_json(e.payload)) for e in events if e.kind.startswith("rtl_")]
    wanted = ([] if not expected else [("rtl_requested", {"draft": latest.as_dict()}),
                                      ("rtl_settled", {"head": result.rtl_head.as_dict()})])
    _require(branch == wanted, "RTL branch differs from its coordinator handoff")
    if not expected:
        return None, (), None
    previous = None
    inherited_authors = 0
    if resources.get("continuation_from") is not None:
        old_events = load_chain(reader, BlobRef.from_dict(resources["continuation_from"]))
        old_result = SynthesisResult.from_bytes(_read(reader, BlobRef.from_dict(thaw_json(old_events[-1].payload)["result"])))
        if old_result.rtl_head is not None:
            old_rtl = rtl_summary(reader, old_result.rtl_head)
            if old_rtl["status"] == "check_unavailable":
                previous, inherited_authors = old_result.rtl_head, old_rtl["candidates"]
    built, ledgers, rtl = verify_rtl(reader, result.rtl_head, run_id=plan.run_id, policy=plan.rtl,
                                    draft=latest, resources=resources, continuation_from=previous)
    # The enclosing synthesis continuation already accounts for the old branch.
    return built, ledgers[inherited_authors:], rtl


def rtl_summary(reader, head):
    """Small operator view, after the enclosing synthesis evidence has been verified."""
    events = load_chain(reader, head)
    _require(events and events[-1].kind == "rtl_finished", "RTL summary requires a terminal branch")
    ref = BlobRef.from_dict(fields(thaw_json(events[-1].payload), {"result"})["result"])
    result = RtlBuildResult.from_bytes(_read(reader, ref))
    return {"head": head.as_dict(), "result": ref.as_dict(), "status": result.status, "code": result.code,
            "candidates": len(result.candidates), "checks": len(result.checks)}


def construction_status(reader, result):
    """Operator status includes a configured RTL branch, not just the B/B gate."""
    if result.rtl_head is not None:
        rtl = rtl_summary(reader, result.rtl_head)
        if rtl["status"] != "passed":
            return "rtl_" + rtl["status"], rtl["code"]
    return result.status, result.code
