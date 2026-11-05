"""Historical discovery records and verified replay, not a live acquisition entry.
"""

from dataclasses import dataclass, replace

from behave.agent.admission import AdmissionPolicy
from behave.agent.journal import load_chain
from behave.agent.process_worker import worker_receipt
from behave.agent.prompting import render, role_messages
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, ToolUnavailable, current_invocation, object_schema
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, FileRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json

from .drafts import SourceRecord
from .evidence import evidence_page
from .ingest import FetchPlan, FetchPolicy, acquire_source, verify_acquisition
from .phase_journal import verify_process_seal
from .role import DiscoveryInputs, RoleJob, input_closure, process_billing
from .selection import require
from .selection_records import SourceInventory, canonical, decode, record_bytes
from .source import load_review_guidance, settled_source_run
from .synthesis_records import PhaseReceipt


def discovery_inputs(inputs, reader):
    guidance = load_review_guidance(reader, inputs.guidance)
    policy = FetchPolicy.from_bytes(record_bytes(reader, inputs.fetch_policy))
    files = tuple(FileData(FileRef(name, BlobRef.of(body)), body) for name, body in (
        ("guidance.md", guidance), ("fetch-policy.json", policy.to_bytes())))
    return guidance, policy, files


def fetched_sources(reader, events, inputs):
    """Re-derive all successful sources and bind each download to its tool request."""
    pending, found, requests, attempts = None, [], 0, []
    for event in events:
        row = thaw_json(event.payload)
        if event.kind == "tool_requested":
            pending = row
            if row["call"]["name"] == "fetch_source":
                requests += 1
        elif event.kind == "tool_settled":
            require(pending is not None and (row["turn"], row["call_id"]) ==
                    (pending["turn"], pending["call"]["call_id"]), "unbound discovery tool outcome")
            call, pending = pending["call"], None
            if call["name"] != "fetch_source" or row["interrupted"] is not None:
                continue
            result = parse_json(record_bytes(reader, BlobRef.from_dict(row["result"])))
            if "head" not in result:
                continue
            require(requests <= inputs.max_fetches and not row["terminal"], "discovery exceeded its fetch budget")
            head = BlobRef.from_dict(result["head"])
            observed = verify_acquisition(reader, head)
            require(observed.as_dict() == result, "download outcome differs from its acquisition")
            plan = FetchPlan.from_bytes(canonical_json(fields(call["arguments"], {"plan"})["plan"]))
            first = load_chain(reader, head)[0]
            require(thaw_json(first.payload) == {"plan": BlobRef.of(plan.to_bytes()).as_dict(),
                "policy": inputs.fetch_policy.as_dict()}, "download changed the requested plan or host policy")
            require(observed.cleanup.quiescent, "discovery download cleanup is unresolved")
            attempts.append(observed)
            if observed.source is not None and observed.source not in found:
                found.append(observed.source)
    return tuple(found), tuple(attempts), requests


def source_page(reader, sources, arguments):
    row = fields(arguments, {"candidate", "path", "offset"})
    number = row["candidate"]
    if type(number) is not int or not 0 <= number < len(sources):
        return {"error": "discovery-candidate-invalid"}, ()
    source = SourceRecord.from_bytes(record_bytes(reader, sources[number]))
    file = next((f for f in source.selected.files if f.path == row["path"]), None)
    if file is None:
        return {"error": "discovery-file-not-acquired"}, ()
    result, payload = evidence_page(b"", (file.content,), reader, 1, row["offset"])
    return result, () if payload is None else (file.content,)


def run_discovery(*, inputs, reader, journal, backend, spec, control=None, model_admission=None,
                  development_tools=()):
    from behave.agent.loop import run_attempt

    if spec.role != "source_discoverer":
        raise ValueError("discovery requires source_discoverer role")
    guidance, policy, files = discovery_inputs(inputs, reader)
    journal.objects.publish(reader, (inputs.guidance, inputs.fetch_policy))

    def fetch(workspace, arguments):
        try:
            plan = FetchPlan.from_bytes(canonical_json(fields(arguments, {"plan"})["plan"]))
            policy.check(plan)
        except (RecordError, ValueError, TypeError):
            return ToolResult({"error": "discovery-fetch-plan-invalid"})
        _, _, count = fetched_sources(workspace.store, journal.load(), inputs)
        if count > inputs.max_fetches:
            return ToolResult({"error": "discovery-fetch-budget-exhausted"})
        invocation = current_invocation()
        if invocation is None:
            raise ToolUnavailable("discovery-invocation-missing")
        # Each durable call owns a distinct acquisition. No in-memory counter,
        # implicit HTTP retry, or mutable workspace path chooses that identity.
        root = journal.directory.parent / (journal.directory.name + "_fetches") / BlobRef.of(invocation.to_bytes()).sha256
        run = JournalDirectory(root)
        result = acquire_source(plan, policy, run=run)
        _, closure = run.seal("acquisition")
        workspace.store.publish(run.objects, closure)
        if not result.cleanup.quiescent:
            raise ToolUnavailable("discovery-fetch-cleanup-unconfirmed", artifacts=closure)
        return ToolResult(result.as_dict(), closure)

    def read(workspace, arguments):
        sources, _, _ = fetched_sources(workspace.store, journal.load(), inputs)
        content, refs = source_page(workspace.store, sources, arguments)
        return ToolResult(content, refs)

    def listing(workspace, arguments):
        fields(arguments, set())
        sources, _, _ = fetched_sources(workspace.store, journal.load(), inputs)
        rows = []
        for i, ref in enumerate(sources):
            source = SourceRecord.from_bytes(record_bytes(workspace.store, ref))
            rows.append({"candidate": i, "source": ref.as_dict(), "files": [f.as_dict() for f in source.selected.files],
                         "entry_file": source.selected.entry_file, "entry_symbol": source.selected.entry_symbol})
        return ToolResult({"candidates": rows})

    def submit(workspace, arguments):
        fields(arguments, set())
        sources, _, _ = fetched_sources(workspace.store, journal.load(), inputs)
        # Screening, not discovery, owns selected/rejected dispositions. Preserve
        # every successfully acquired source, including ones the agent disliked.
        return ToolResult({"sources": [ref.as_dict() for ref in sources]}, sources, True)

    # The caller supplies the prompt as a pinned input; this module fixes only
    # capabilities. Prompt approval remains a separate author decision.
    revision = "source-discovery/1:" + BlobRef.of(canonical_json(inputs.as_dict())).sha256
    remote_file = object_schema({"path": {"type": "string"}, "url": {"type": "string"},
        "expected": {"type": ["object", "null"], "properties": {
            "sha256": {"type": "string"}, "size_bytes": {"type": "integer"}}}})
    plan_schema = object_schema({"schema": {"const": "behave.source-fetch-plan/1"},
        "source_id": {"type": "string"}, "origin": {"type": "string"},
        "revision": {"type": ["string", "null"]}, "license": {"type": ["string", "null"]},
        "files": {"type": "array", "items": remote_file}, "entry_file": {"type": "string"},
        "entry_symbol": {"type": "string"}})
    tools = (
        *development_tools,
        Tool("fetch_source", render("synthesis/legacy/source_discoverer/tools/fetch_source"),
             object_schema({"plan": plan_schema}), fetch, revision),
        Tool("list_sources", render("synthesis/legacy/source_discoverer/tools/list_sources"),
             object_schema({}), listing, revision),
        Tool("read_source", render("synthesis/legacy/source_discoverer/tools/read_acquired_source"),
             object_schema({"candidate": {"type": "integer"}, "path": {"type": "string"},
                            "offset": {"type": "integer"}}), read, revision),
        Tool("submit_sources", render("synthesis/legacy/source_discoverer/submit"),
             object_schema({}), submit, revision, terminal=True),
    )
    protected = tuple(sorted({*spec.read_only_paths, *(f.reference.path for f in files)}))
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=files, tools=tools, control=control, model_admission=model_admission,
        initial_messages=role_messages("synthesis/legacy/source_discoverer", guidance=guidance.decode("utf-8"),
            inputs_json=canonical_json({"fetch_policy": parse_json(policy.to_bytes()),
                "max_fetches": inputs.max_fetches}).decode("utf-8")))


@dataclass(frozen=True, slots=True)
class DiscoveryConfiguration:
    job: RoleJob
    model_admission: AdmissionPolicy | None = None

    def __post_init__(self):
        if type(self.job) is not RoleJob or type(self.job.inputs) is not DiscoveryInputs:
            raise TypeError("discovery requires a fixed discovery role job")
        if self.model_admission is not None and type(self.model_admission) is not AdmissionPolicy:
            raise TypeError("discovery admission requires AdmissionPolicy")

    def to_bytes(self):
        return canonical_json({"schema": "behave.discovery-configuration/1", "job": parse_json(self.job.to_bytes()),
            "model_admission": None if self.model_admission is None else self.model_admission.as_dict()})

    @classmethod
    def from_bytes(cls, payload):
        row = decode(payload, "behave.discovery-configuration/1", {"job", "model_admission"})
        policy = row["model_admission"]
        return canonical(payload, cls(RoleJob.from_bytes(canonical_json(row["job"])), None if policy is None else
            AdmissionPolicy(**fields(policy, set(AdmissionPolicy.__dataclass_fields__)))))

    def preflight(self, reader):
        input_closure(self.job, reader)


def load_discovery(reader, head):
    events = load_chain(reader, head)
    require(tuple(e.kind for e in events) == ("discovery_started", "discovery_requested", "discovery_finished"),
            "discovery requires a settled role")
    config_ref = BlobRef.from_dict(fields(thaw_json(events[0].payload), {"configuration"})["configuration"])
    config = DiscoveryConfiguration.from_bytes(record_bytes(reader, config_ref))
    require(thaw_json(events[1].payload) == {"job": BlobRef.of(config.job.to_bytes()).as_dict()},
            "discovery job differs from its configuration")
    outcome = fields(thaw_json(events[-1].payload), {"phase", "status", "code"})
    phase = PhaseReceipt.from_dict(outcome["phase"])
    require(phase.role == "source_discoverer" and phase.revision == 0 and phase.process is not None,
            "discovery has another role's receipt")
    billing = process_billing(reader, phase, config.job, {
        "model_admission": None if config.model_admission is None else config.model_admission.identity,
        "development_admission": None})
    role = load_chain(reader, phase.head)
    _, _, files = discovery_inputs(config.job.inputs, reader)
    if role:
        settings = parse_json(record_bytes(reader, BlobRef.from_dict(thaw_json(role[0].payload)["configuration"])))
        snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, BlobRef.from_dict(settings["workspace"])))
        require(snapshot == WorkspaceSnapshot(tuple(f.reference for f in files)), "discovery received changed inputs")
    sources, attempts, _ = fetched_sources(reader, role, config.job.inputs)
    # Verify the text that was shown to the agent against pinned bytes too.
    prefix, pending = [], None
    for event in role:
        row = thaw_json(event.payload)
        if event.kind == "tool_requested":
            pending = row["call"]
        elif event.kind == "tool_settled" and pending is not None:
            if pending["name"] == "read_source" and row["interrupted"] is None and pending["argument_error"] is None:
                result = parse_json(record_bytes(reader, BlobRef.from_dict(row["result"])))
                if "content" in result:
                    prior, _, _ = fetched_sources(reader, prefix, config.job.inputs)
                    expected, _ = source_page(reader, prior, pending["arguments"])
                    require(result == expected, "discovery page differs from its acquired source")
            pending = None
        prefix.append(event)
    if role and role[-1].kind == "terminal":
        terminal = thaw_json(role[-1].payload)
        complete = terminal["reason"] == "final_action"
        expected_action = {"sources": [s.as_dict() for s in sources]} if complete else None
        require(terminal["final_action"] == expected_action, "discovery dropped or invented a source")
        require((outcome["status"], outcome["code"]) == (
            "completed" if complete else "unfinished", "discovery-" + terminal["reason"]),
            "discovery outcome differs from role termination")
    else:
        receipt = worker_receipt(load_chain(reader, phase.process.supervision), namespace="construction")
        errors = receipt["supervision_errors"]
        code = receipt["interruption"] or (errors[0] if errors else "construction-worker-" + receipt["status"])
        require(outcome["status"] == "interrupted" and outcome["code"] == code, "unbound discovery interruption")
    return {"schema": "behave.discovery-report/1", "head": head.as_dict(), **outcome,
        "sources": [s.as_dict() for s in sources], "acquisitions": [a.as_dict() for a in attempts],
        "publication_approved": False, "billing": {"known_estimates_by_currency": thaw_json(billing.cost_by_currency()),
            "unpriced_dispatches": billing.unpriced_dispatches, "unresolved_dispatches": billing.unresolved_dispatches}}


def discovery_inventory(reader, head):
    report = load_discovery(reader, head)
    require(report["status"] == "completed" and report["sources"], "screening requires completed nonempty discovery")
    return SourceInventory(tuple(BlobRef.from_dict(s) for s in report["sources"]), discovery=head)


def discover_sources(*, configuration, reader, run, control=None, model_pool=None):
    """Replay a settled historical run using its local evidence; never launch a role."""
    if type(configuration) is not DiscoveryConfiguration or type(run) is not JournalDirectory:
        raise TypeError("discovery requires fixed configuration and an owned run directory")
    with settled_source_run(run, configuration, "discovery", control=control, model_pool=model_pool) as head:
        report = load_discovery(run.objects, head)
        phase = PhaseReceipt.from_dict(report["phase"])
        verify_process_seal(run, phase)
        actual, _ = run.seal("revisions", "r0", phase.role)
        require(actual == phase.head, "discovery journal changed after settlement")
        return report
