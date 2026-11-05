"""Verified local replay for historical source screening; fresh roles are retired.
"""

from dataclasses import dataclass, replace

from behave.agent.admission import AdmissionPolicy
from behave.agent.journal import load_chain
from behave.agent.loop import run_attempt
from behave.agent.process_worker import worker_receipt
from behave.agent.prompting import render, role_messages
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, file_tools, object_schema
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, FileRef, read_verified
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, thaw_json

from .drafts import SourceRecord, source_closure
from .evidence import evidence_page, evidence_tool
from .phase_journal import verify_process_seal
from .role import RoleJob, SelectionInputs, input_closure, process_billing
from .selection_records import (
    SourceAssessment,
    SourceInventory,
    SourceSelection,
    SourceSpan,
    canonical,
    content_key,
    decode,
    record_bytes,
)
from .source import load_review_guidance, settled_source_run
from .synthesis_records import PhaseReceipt


@dataclass(frozen=True, slots=True)
class SelectionConfiguration:
    job: RoleJob
    model_admission: AdmissionPolicy | None = None

    def __post_init__(self):
        if type(self.job) is not RoleJob or type(self.job.inputs) is not SelectionInputs:
            raise TypeError("source screening requires a fixed selection job")
        if self.model_admission is not None and type(self.model_admission) is not AdmissionPolicy:
            raise TypeError("selection admission requires AdmissionPolicy")

    def to_bytes(self):
        return canonical_json({"schema": "behave.selection-configuration/1", "job": parse_json(self.job.to_bytes()),
            "model_admission": None if self.model_admission is None else self.model_admission.as_dict()})

    @classmethod
    def from_bytes(cls, payload):
        row = decode(payload, "behave.selection-configuration/1", {"job", "model_admission"})
        policy = row["model_admission"]
        return canonical(payload, cls(RoleJob.from_bytes(canonical_json(row["job"])), None if policy is None else
                                      AdmissionPolicy(**fields(policy, set(AdmissionPolicy.__dataclass_fields__)))))

    def preflight(self, reader):
        input_closure(self.job, reader)


@dataclass(frozen=True, slots=True)
class SelectionResult:
    configuration: BlobRef
    status: str
    code: str
    decision: BlobRef | None
    phase: PhaseReceipt

    def __post_init__(self):
        if type(self.configuration) is not BlobRef or self.decision is not None and type(self.decision) is not BlobRef:
            raise TypeError("selection result requires immutable references")
        if self.status not in {"selected", "no_selection", "unfinished", "interrupted"}:
            raise ValueError("unknown selection outcome")
        text(self.code, "selection result code")
        if (self.decision is not None) != (self.status in {"selected", "no_selection"}):
            raise ValueError("completed selection needs a decision; interruption is not rejection")
        if (type(self.phase) is not PhaseReceipt or self.phase.role != "source_selector"
                or self.phase.revision != 0 or self.phase.process is None):
            raise TypeError("selection result requires a supervised selector receipt")

    def to_bytes(self):
        return canonical_json({"schema": "behave.selection-result/1", "configuration": self.configuration.as_dict(),
            "status": self.status, "code": self.code, "decision": None if self.decision is None else self.decision.as_dict(),
            "phase": self.phase.as_dict()})

    @classmethod
    def from_bytes(cls, payload):
        row = decode(payload, "behave.selection-result/1", {"configuration", "status", "code", "decision", "phase"})
        return canonical(payload, cls(BlobRef.from_dict(row["configuration"]), row["status"], row["code"],
            None if row["decision"] is None else BlobRef.from_dict(row["decision"]), PhaseReceipt.from_dict(row["phase"])))


def select_source(*, configuration: SelectionConfiguration, reader, run: JournalDirectory,
                  control=None, model_pool=None) -> SelectionResult:
    """Replay a settled historical run using its local evidence; never launch a role."""
    if type(configuration) is not SelectionConfiguration or type(run) is not JournalDirectory:
        raise TypeError("source screening requires fixed configuration and an owned run directory")
    with settled_source_run(run, configuration, "selection", control=control, model_pool=model_pool) as head:
        from .selection import load_selection

        result, _ = load_selection(run.objects, head)
        verify_process_seal(run, result.phase)
        actual, _ = run.seal("revisions", "r0", "source_selector")
        if actual != result.phase.head:
            raise RecordError("selection-phase-mismatch", "selection journal changed after termination")
        return result


def require(condition, detail):
    if not condition:
        raise RecordError("selection-evidence-mismatch", detail)


@dataclass(frozen=True, slots=True)
class SelectionView:
    inventory: SourceInventory
    sources: tuple[SourceRecord, ...]
    documents: tuple[tuple[int, FileRef], ...]
    index: bytes
    files: tuple[FileData, ...]
    closure: tuple[BlobRef, ...]

    @property
    def references(self):
        return tuple(file.content for _, file in self.documents)


def selection_view(reader, inputs):
    inventory = SourceInventory.from_bytes(record_bytes(reader, inputs.inventory))
    sources = tuple(SourceRecord.from_bytes(record_bytes(reader, ref)) for ref in inventory.sources)
    guidance = load_review_guidance(reader, inputs.guidance)
    documents, rows, seen = [], [], {}
    for number, (ref, source) in enumerate(zip(inventory.sources, sources, strict=True)):
        selected = []
        for file in source.selected.files:
            documents.append((number, file))
            selected.append({**file.as_dict(), "document": len(documents)})
        key = content_key(source)
        rows.append({"candidate": number, "source": ref.as_dict(), "source_id": source.source_id,
            "origin": source.origin, "revision": source.revision, "license": source.license,
            "entry_file": source.selected.entry_file, "entry_symbol": source.selected.entry_symbol,
            "files": selected, "content_key": key, "duplicate_of": seen.get(key)})
        seen.setdefault(key, number)
    index = canonical_json({"schema": "behave.source-selection-view/1",
                                            "inventory": inputs.inventory.as_dict(), "candidates": rows})
    files = tuple(FileData(FileRef(path, BlobRef.of(body)), body) for path, body in (
        ("inventory.json", index), ("guidance.md", guidance)))
    refs = [inputs.inventory, inputs.guidance]
    if inventory.discovery is not None:
        from behave.agent.journal import load_chain

        from .discovery import discovery_inventory

        require(discovery_inventory(reader, inventory.discovery) == inventory,
                "inventory differs from its complete discovery result")
        refs.extend(ref for event in load_chain(reader, inventory.discovery)
                    for ref in (BlobRef.of(event.to_bytes()), *event.artifacts))
    # Each remote source retains its acquisition proof; local imports remain local imports.
    for ref in inventory.sources:
        refs.extend(source_closure(reader, ref))
    return SelectionView(inventory, sources, tuple(documents), index, files, tuple(dict.fromkeys(refs)))


def inspected_spans(reader, events, view):
    """Pages actually returned to this role, not claims in its final report."""
    spans, pending = {}, None
    for event in events:
        row = thaw_json(event.payload)
        if event.kind == "tool_requested":
            pending = row
        elif event.kind == "tool_settled":
            require(pending is not None and (row["turn"], row["call_id"]) ==
                    (pending["turn"], pending["call"]["call_id"]), "unbound inspection tool result")
            call, pending = pending["call"], None
            if call["name"] != "read_source" or row["interrupted"] is not None:
                continue
            result = parse_json(record_bytes(reader, BlobRef.from_dict(row["result"])))
            if "content" not in result:
                continue  # Invalid requests and non-text files are not inspection.
            args = fields(call["arguments"], {"document", "offset"})
            expected, _ = evidence_page(view.index, view.references, reader, args["document"], args["offset"])
            require(result == expected and not row["terminal"], "inspection page differs from its pinned source")
            if args["document"] == 0 or not result["content"]:
                continue  # Metadata pages and empty/EOF pages do not inspect code.
            candidate, file = view.documents[args["document"] - 1]
            start = args["offset"]
            spans.setdefault((candidate, file), []).append((start, start + len(result["content"])))
    return spans


def _covered(ranges, start, end):
    for low, high in sorted(ranges):
        if low > start:
            break
        start = max(start, high)
        if start >= end:
            return True
    return False


def validate_selection(selection, inputs, view, spans):
    require((selection.inventory, selection.guidance) == (inputs.inventory, inputs.guidance),
            "selection changed inventory or guidance")
    require(tuple(a.source for a in selection.assessments) == view.inventory.sources,
            "selection must disposition every candidate exactly once in inventory order")
    for number, assessment in enumerate(selection.assessments):
        source = view.sources[number]
        if assessment.disposition == "unreviewed":
            require(not any(candidate == number for candidate, _ in spans),
                    "unreviewed source has returned code pages; use deferred if assessment is incomplete")
        for citation in assessment.citations:
            require(citation.file in source.selected.files and _covered(
                spans.get((number, citation.file), ()), citation.start, citation.end),
                "citation must be fully covered by returned pages of this candidate's pinned file")
        if assessment.disposition == "selected":
            require(any(c.file.path == source.selected.entry_file for c in assessment.citations),
                    "selected source requires inspected entry-file evidence")


def load_selection(reader, head):
    events = load_chain(reader, head)
    require(tuple(e.kind for e in events) == (
        "selection_started", "selection_requested", "selection_settled", "selection_finished"),
        "selection requires a completed request/settlement history")
    configuration = BlobRef.from_dict(fields(thaw_json(events[0].payload), {"configuration"})["configuration"])
    config = SelectionConfiguration.from_bytes(record_bytes(reader, configuration))
    job = config.job
    result = SelectionResult.from_bytes(record_bytes(reader, BlobRef.from_dict(fields(
        thaw_json(events[-1].payload), {"result"})["result"])))
    require(result.configuration == configuration, "selection outcome changed configuration")
    requested = fields(thaw_json(events[1].payload), {"job"})
    settled = fields(thaw_json(events[2].payload), {"phase", "decision"})
    require(requested["job"] == BlobRef.of(job.to_bytes()).as_dict() and settled == {
        "phase": result.phase.as_dict(), "decision": None if result.decision is None else result.decision.as_dict()},
        "selection phase differs from the planned job or submitted artifact")
    billing = process_billing(reader, result.phase, job, {
        "model_admission": None if config.model_admission is None else config.model_admission.identity,
        "development_admission": None})
    view = selection_view(reader, job.inputs)
    role = load_chain(reader, result.phase.head)
    if role:
        settings = parse_json(record_bytes(reader, BlobRef.from_dict(thaw_json(role[0].payload)["configuration"])))
        snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, BlobRef.from_dict(settings["workspace"])))
        require(snapshot == WorkspaceSnapshot(tuple(f.reference for f in view.files)),
                "selector received another inventory or guidance")
        require(set(settings["spec"]["read_only_paths"]) >= {f.reference.path for f in view.files},
                "selector could edit its evidence inputs")
    spans = inspected_spans(reader, role, view)
    if result.decision is not None:
        require(role and role[-1].kind == "terminal", "selection lacks a terminal role")
        terminal = thaw_json(role[-1].payload)
        require(terminal["reason"] == "final_action" and terminal["final_action"] == {"selection": result.decision.as_dict()},
                "selection differs from the role's final action")
        decision = SourceSelection.from_bytes(record_bytes(reader, result.decision))
        validate_selection(decision, job.inputs, view, spans)
        require((result.status, result.code) == (
            "selected" if decision.selected else "no_selection", "selection-complete"),
            "selection status differs from its decision")
    elif result.status == "unfinished":
        require(role and role[-1].kind == "terminal", "unfinished selection lacks a stopped role")
        terminal = thaw_json(role[-1].payload)
        require(terminal["final_action"] is None and result.code == "selection-" + terminal["reason"],
                "normal stop was mislabeled")
    else:
        require(not role or role[-1].kind != "terminal", "interruption incorrectly hides a normally stopped role")
        receipt = worker_receipt(load_chain(reader, result.phase.process.supervision), namespace="construction")
        errors = receipt["supervision_errors"]
        code = receipt["interruption"] or (errors[0] if errors else "construction-worker-" + receipt["status"])
        require(result.code == code, "interruption differs from its lifetime owner's outcome")
    return result, billing


def selection_closure(reader, head, *, source):
    result, _ = load_selection(reader, head)
    require(result.status == "selected", "scope handoff requires an actual selection")
    decision = SourceSelection.from_bytes(record_bytes(reader, result.decision))
    require(source in decision.selected, "scope handoff names a different source")
    return tuple(dict.fromkeys(ref for e in load_chain(reader, head) for ref in (BlobRef.of(e.to_bytes()), *e.artifacts)))


def selection_summary(reader, head):
    result, billing = load_selection(reader, head)
    config = SelectionConfiguration.from_bytes(record_bytes(reader, result.configuration))
    view = selection_view(reader, config.job.inputs)
    spans = inspected_spans(reader, load_chain(reader, result.phase.head), view)
    counts = {name: 0 for name in ("selected", "rejected", "deferred", "unreviewed")}
    sources = ()
    if result.decision is not None:
        decision = SourceSelection.from_bytes(record_bytes(reader, result.decision))
        sources = decision.selected
        for assessment in decision.assessments:
            counts[assessment.disposition] += 1
    return {"head": head.as_dict(), "result": parse_json(result.to_bytes()),
        "sources": [source.as_dict() for source in sources],
        # Preserve the single-source report field without choosing a hidden winner.
        "source": sources[0].as_dict() if len(sources) == 1 else None, "dispositions": counts,
        "candidates": len(view.sources), "inspected": len({candidate for candidate, _ in spans}),
        "unreported": len(view.sources) if result.decision is None else 0,
        "publication_approved": False, "billing": {"known_estimates_by_currency": thaw_json(billing.cost_by_currency()),
            "unpriced_dispatches": billing.unpriced_dispatches, "unresolved_dispatches": billing.unresolved_dispatches}}


def selected_scope_inputs(reader, head, *, guidance, source=None):
    """Hand off a selected source; batch selections require an explicit choice."""
    from .role import ScopeInputs

    result, _ = load_selection(reader, head)
    require(result.status == "selected", "scope handoff requires an actual selection")
    decision = SourceSelection.from_bytes(record_bytes(reader, result.decision))
    if source is None:
        require(len(decision.selected) == 1, "multiple selected sources require an explicit source")
        source = decision.selected[0]
    require(source in decision.selected, "scope handoff names a different source")
    return ScopeInputs(source, guidance, selection=head)


def run_selection(*, inputs, reader, journal, backend, spec, control=None, model_admission=None,
                  development_tools=()):
    if spec.role != "source_selector":
        raise ValueError("source selection requires source_selector role")
    view = selection_view(reader, inputs)
    journal.objects.publish(reader, view.closure)

    def submit(workspace, arguments):
        try:
            row = fields(arguments, {"assessments"})
            assessments = []
            for number, item in enumerate(array(row["assessments"])):
                item = fields(item, {"candidate", "disposition", "rationale", "citations"})
                if type(item["candidate"]) is not int or item["candidate"] != number or number >= len(view.sources):
                    raise ValueError("candidates must follow inventory order")
                files = {f.path: f for f in view.sources[number].selected.files}
                citations = []
                for value in array(item["citations"]):
                    value = fields(value, {"path", "start", "end"})
                    citations.append(SourceSpan(FileRef(value["path"], files[value["path"]].content),
                                                value["start"], value["end"]))
                assessments.append(SourceAssessment(view.inventory.sources[number], item["disposition"],
                                                    item["rationale"], tuple(citations)))
            selection = SourceSelection(inputs.inventory, inputs.guidance, tuple(assessments))
        except (RecordError, ValueError, TypeError, KeyError):
            return ToolResult({"error": "selection-arguments-invalid"})
        # This also replays prior pages after a cold restart, without mutable
        # handler-side counters that would disappear when tool calls replay.
        spans = inspected_spans(journal.objects, journal.load(), view)
        try:
            validate_selection(selection, inputs, view, spans)
            packet = selection.to_bytes()
        except RecordError as error:
            return ToolResult({"error": error.code, "detail": str(error)})
        ref = workspace.store.put(packet)
        return ToolResult({"selection": ref.as_dict()}, (ref, *view.closure), True)

    citation = object_schema({"path": {"type": "string"}, "start": {"type": "integer", "minimum": 0},
                              "end": {"type": "integer", "minimum": 1}})
    assessment = object_schema({"candidate": {"type": "integer", "minimum": 0},
        "disposition": {"enum": ["selected", "rejected", "deferred", "unreviewed"]},
        "rationale": {"type": "string"}, "citations": {"type": "array", "items": citation}})
    submit_tool = Tool("submit_selection", render("synthesis/legacy/source_selector/submit"),
        object_schema({"assessments": {"type": "array", "items": assessment}}), submit,
        "source-selection/2:" + inputs.inventory.sha256 + ":" + inputs.guidance.sha256, terminal=True)
    inspector = evidence_tool(name="read_source", description=render("synthesis/legacy/source_selector/tools/read_source"),
        revision="source-pages/1", document=view.index, references=view.references, reader=journal.objects)
    protected = tuple(sorted({*spec.read_only_paths, *(f.reference.path for f in view.files)}))
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=view.files, control=control, model_admission=model_admission,
        tools=(*(t for t in file_tools() if t.name in {"read_file", "list_files"}), *development_tools,
               inspector, submit_tool),
        initial_messages=role_messages("synthesis/legacy/source_selector"))
