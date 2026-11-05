"""Construction roles composed from the shared attempt and workspace runtime.

These are author/reader attempts, not a release supervisor. The trusted caller
selects their model/policy, owns their journals and supplies bounded development
tools. Submitted source is never imported in this control plane.
"""

from __future__ import annotations

from dataclasses import replace

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptResult, AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.lifetime import AttemptControl
from behave.agent.loop import run_attempt
from behave.agent.model import ModelBackend
from behave.agent.prompting import render, role_messages
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.workspace import Workspace
from behave.core.artifacts import (
    ArtifactSelection,
    BlobRef,
    ContentReader,
    FileData,
    FileRef,
    SourceArtifact,
    read_verified,
)
from behave.core.records import RecordError, array, canonical_json, fields, text
from behave.data.published import TaskManifest
from behave.task_execution.judge_policy import JudgePolicy

from .contract import contract_guidance
from .drafts import (
    Reconstruction,
    TaskDraft,
    load_draft,
    load_scope,
    load_source_artifact,
    load_workspace,
    scope_closure,
)
from .escalation import ScopeEscalation

CONTEXT = ".construction/"
_BUNDLE_SCHEMA = object_schema({"files": {"type": "array", "items": {"type": "string"}},
                                "entry_file": {"type": "string"}, "entry_symbol": {"type": "string"}})


def _file(path: str, payload: bytes) -> FileData:
    return FileData(FileRef(path, BlobRef.of(payload)), payload)


def _capture(workspace: Workspace, value: object) -> SourceArtifact:
    selection = ArtifactSelection.from_dict(value)
    files = tuple(_file(path, workspace.read(path)).reference for path in selection.files)
    return SourceArtifact(files, selection.entry_file, selection.entry_symbol)


def _feedback(handler):
    """Only malformed role selections are feedback; storage failures still abort."""
    def run(workspace, arguments):
        try:
            return handler(workspace, arguments)
        except RecordError as error:
            if error.code not in {"draft-reserved-path", "draft-public-private-overlap",
                                  "reconstruction-findings-required"}:
                raise
            return ToolResult({"error": error.code})
    return run


def draft_tool(*, task_id: str, scope: BlobRef, judge_policy: bytes, parent: BlobRef | None = None) -> Tool:
    text(task_id, "task id")
    if JudgePolicy.from_bytes(judge_policy).analysis is not None:
        raise ValueError("construction requires a concrete-only judge policy")
    binding = canonical_json({"task_id": task_id, "scope": scope.as_dict(),
                              "judge_policy": BlobRef.of(judge_policy).as_dict(),
                              "parent": None if parent is None else parent.as_dict()})

    def submit(workspace: Workspace, arguments: dict) -> ToolResult:
        request = fields(arguments, {"draft", "scope_findings"})
        findings = tuple(array(request["scope_findings"]))
        if request["draft"] is None:
            if not findings:
                return ToolResult({"error": "scope-findings-required"})
            report = ScopeEscalation(task_id, scope, parent, findings)
            ref = workspace.store.put(report.to_bytes())
            return ToolResult({"scope_escalation": ref.as_dict()}, (ref,), True)
        if findings:
            return ToolResult({"error": "draft-and-scope-request-are-exclusive"})
        row = fields(request["draft"], {"spec", "contract", "behavior", "run_policy", "public_files", "rtl", "adaptations"})
        from .spec_format import validate

        spec_bytes = workspace.read(row["spec"])
        try:
            validate(spec_bytes.decode("utf-8"))
        except ValueError as error:
            return ToolResult({"error": "draft-spec-format", "detail": str(error),
                               "path": row["spec"]})
        primary = _capture(workspace, row["behavior"])
        rtl = None if row["rtl"] is None else _capture(workspace, row["rtl"])
        ref = lambda path: _file(path, workspace.read(path)).reference
        task = TaskManifest(task_id, ref(row["spec"]), ref(row["contract"]), primary,
                            ref(row["run_policy"]).content, workspace.store.put(judge_policy),
                            tuple(ref(path) for path in array(row["public_files"])), rtl)
        root, closure = workspace.checkpoint()
        proposal, _, _ = load_scope(workspace.store, scope)
        inherited = proposal.adaptations
        if parent is not None:
            previous = load_draft(workspace.store, parent).record
            if previous.adaptations is not None:
                inherited = previous.adaptations
        adaptations = inherited if row["adaptations"] is None else tuple(array(row["adaptations"]))
        draft = TaskDraft(scope, task, root, parent, adaptations)
        identity = workspace.store.put(draft.to_bytes())
        # Validate the entire declared input closure before acknowledging a draft.
        load_draft(workspace.store, identity)
        return ToolResult({"draft": identity.as_dict()},
                          (*closure, identity, scope, task.judge_policy, *(() if parent is None else (parent,))), True)

    schema = object_schema({"spec": {"type": "string"}, "contract": {"type": "string", "description":
                        "Workspace-relative path to the TaskContract JSON file."},
                    "behavior": _BUNDLE_SCHEMA, "run_policy": {"type": "string", "description":
                        "Workspace-relative path to a JSON file; use null for runtime generation settings."},
                    "public_files": {"type": "array", "items": {"type": "string"}},
                    "rtl": {"anyOf": [_BUNDLE_SCHEMA, {"type": "null"}]},
                    "adaptations": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}})
    schema = object_schema({"draft": {"anyOf": [schema, {"type": "null"}]},
                            "scope_findings": {"type": "array", "items": {"type": "string"}}})
    return Tool("submit_draft", render("synthesis/task_author/submit"),
                schema, _feedback(submit), "draft/4:" + BlobRef.of(binding).sha256, terminal=True)


def reconstruction_tool(draft: BlobRef) -> Tool:
    def submit(workspace, arguments):
        row = fields(arguments, {"behavior", "findings"})
        source = None if row["behavior"] is None else _capture(workspace, row["behavior"])
        root, closure = workspace.checkpoint()
        record = Reconstruction(draft, source, tuple(array(row["findings"])), root)
        ref = workspace.store.put(record.to_bytes())
        return ToolResult({"reconstruction": ref.as_dict()},
                          (ref, *closure), True)
    return Tool("submit_reconstruction", render("synthesis/spec_reader/submit"),
                object_schema({"behavior": {"anyOf": [_BUNDLE_SCHEMA, {"type": "null"}]},
                               "findings": {"type": "array", "items": {"type": "string"}}}),
                _feedback(submit), "reconstruction/5:" + draft.sha256, terminal=True)


def run_author(*, task_id: str, scope: BlobRef, reader: ContentReader, journal: AttemptJournal,
               backend: ModelBackend, spec: AttemptSpec, judge_policy: bytes, parent: BlobRef | None = None,
               feedback: str = "", development_tools: tuple[Tool, ...] = (),
               repair_files: tuple[FileData, ...] = (),
               construction_experience: BlobRef | None = None,
               control: AttemptControl | None = None, model_admission: ResourcePool | None = None) -> AttemptResult:
    """Jointly edit the task and interface; preserve source/intent provenance."""
    proposal, source, source_files = load_scope(reader, scope)
    journal.objects.publish(reader, scope_closure(reader, scope))
    context = (_file(CONTEXT + "scope.json", proposal.to_bytes()),
               _file(CONTEXT + "source.json", source.to_bytes()),
               *contract_guidance(CONTEXT),
               *(_file(CONTEXT + "source/" + f.reference.path, f.data) for f in source_files))
    from .author_check import check_guidance

    context += check_guidance(CONTEXT)
    if proposal.brief is not None:
        context += (_file(CONTEXT + "construction_brief.json", read_verified(reader, proposal.brief)),)
    if construction_experience is not None:
        experience = read_verified(reader, construction_experience)
        experience.decode("utf-8")
        journal.objects.put(experience)
        context += (_file("experience/construction.md", experience),)
    initial = context
    if proposal.contract is not None:
        # A prior interface proposal is an editable starting point, not the task's oracle.
        initial += (FileData(proposal.contract, read_verified(reader, proposal.contract.content)),)
    if parent is not None:
        previous = load_draft(reader, parent)
        if previous.record.scope != scope or previous.record.task.task_id != task_id:
            raise RecordError("draft-parent-mismatch", "ordinary repair cannot silently change task or scope")
        # Store parent identity as well as its source files when moving stores.
        journal.objects.put(previous.record.to_bytes())
        initial = previous.files
        if any(f not in initial for f in context):
            raise RecordError("draft-context-mismatch", "parent workspace changed its protected source/scope")
    if spec.role != "task_author" or any(t.terminal for t in development_tools):
        raise ValueError("author requires task_author role and nonterminal development tools")
    repairs = {f.reference.path: f for f in repair_files}
    # Repair evidence belongs to one attempt; do not inherit stale reports or
    # unmediated comparison files when the next revision receives only feedback.
    repair_roots = tuple(CONTEXT + name + "/" for name in
                         ("check", "comparison", "reader_findings", "source_review"))
    initial = (*tuple(f for f in initial if f.reference.path not in repairs
                     and not f.reference.path.startswith(repair_roots)), *repair_files)
    protected = tuple(sorted({*spec.read_only_paths, *(f.reference.path for f in context), *repairs}))
    tools = (*development_tools,
             draft_tool(task_id=task_id, scope=scope, judge_policy=judge_policy, parent=parent))
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=initial, tools=tools, control=control, model_admission=model_admission,
        initial_messages=role_messages("synthesis/task_author", task_id=task_id, feedback=feedback))


def reader_inputs(reader: ContentReader, draft: BlobRef, parent: BlobRef | None = None):
    """A blind initial view, or that same public task plus the implementer's own files."""
    from .author_check import check_guidance

    loaded = load_draft(reader, draft)
    public = (*loaded.reader_files, *check_guidance(CONTEXT))
    if parent is None:
        return loaded, public
    previous = Reconstruction.from_bytes(read_verified(reader, parent))
    if previous.draft != draft:
        raise RecordError("reconstruction-draft-mismatch", "repair cannot inherit code from another public task")
    if previous.workspace is not None:
        return loaded, load_workspace(reader, previous.workspace)
    own = () if previous.behavior is None else load_source_artifact(reader, previous.behavior).files
    return loaded, (*public, *(f for f in own if f.reference.path not in {p.reference.path for p in public}))


def run_reader(*, draft: BlobRef, reader: ContentReader, journal: AttemptJournal, backend: ModelBackend,
               spec: AttemptSpec, control: AttemptControl | None = None,
               model_admission: ResourcePool | None = None,
               parent: BlobRef | None = None, feedback: str = "",
               development_tools: tuple[Tool, ...] = ()) -> AttemptResult:
    """Fresh spec-only context; no author files, transcript, source or primary model."""
    loaded, initial = reader_inputs(reader, draft, parent)
    if spec.role != "spec_reader" or any(t.terminal for t in development_tools):
        raise ValueError("reader requires spec_reader role and nonterminal development tools")
    from .author_check import check_guidance

    guidance = check_guidance(CONTEXT)
    public = loaded.reader_files
    protected = tuple(sorted({*spec.read_only_paths, *(f.reference.path for f in (*public, *guidance))}))
    task = loaded.record.task
    messages = role_messages("synthesis/spec_reader", spec_path=task.spec.path, contract_path=task.contract.path)
    if feedback:
        messages += ({"role": "user", "content": render("synthesis/spec_reader/repair", feedback=feedback)},)
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=initial, tools=(*development_tools, reconstruction_tool(draft)),
        control=control, model_admission=model_admission,
        initial_messages=messages)
