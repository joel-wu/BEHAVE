"""Source acquisition with workspace tools and a structured final handoff.

Search strategy and scratch work belong to the agent. The controller pins the
permitted library, candidate-side feedback and selected construction objectives.
"""

from collections import Counter
from dataclasses import dataclass, replace

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import load_chain
from behave.agent.lifetime import AttemptControl
from behave.agent.process_worker import worker_receipt
from behave.agent.prompting import render, role_messages
from behave.agent.replay import Tape
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, FileRef, read_verified
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, thaw_json, version
from behave.data.phase_journal import seal_phase, verify_process_seal
from behave.data.role import RoleJob, input_closure, process_billing
from behave.data.selection_records import SourceInventory
from behave.data.synthesis_records import PhaseReceipt

from .acquisition_records import AcquisitionInputs
from .experience import (
    ExperiencePolicy,
    bounded_views,
    delta_files,
    experience_files,
    experience_snapshot,
    load_delta,
    load_experience,
    memory_value,
    merge_payload,
)
from .library import LibraryIndex

COMPACT_MODES = {"compact-v1", "compact-v2"}


def _memory_view(inputs, reader):
    policy = ExperiencePolicy.from_dict(inputs.experience_policy)
    legacy = inputs.experience_mode in COMPACT_MODES and not inputs.experience_proposals
    if not legacy:
        memory_value(reader, inputs.experience, policy)
    refs = [inputs.guidance]
    if inputs.experience is not None:
        refs.extend((inputs.experience, *(f.reference.content for f in load_experience(reader, inputs.experience))))
    for ref in inputs.experience_proposals:
        refs.extend((ref, *(f.reference.content for f in load_delta(reader, ref, policy.delta_bytes))))
    guidance = read_verified(reader, inputs.guidance)
    files = [FileData(FileRef("acquisition_guidance.md", inputs.guidance), guidance)]
    if inputs.experience_mode in COMPACT_MODES:
        payload = merge_payload(reader, inputs.experience, inputs.experience_proposals, policy,
                                compact_legacy=legacy)
        files.append(FileData(FileRef("memory_input.json", BlobRef.of(payload)), payload))
    return None, {"observations": []}, tuple(files), tuple(dict.fromkeys(refs))


def _experience_submission(inputs, arguments):
    incremental = inputs.experience_mode == "delta-v1"
    key = "experience_delta" if incremental else "experience"
    if inputs.experience_mode == "compact-v2":
        fields(arguments, {"construction", "solver"})
        value = arguments
    elif inputs.experience_mode == "compact-v1":
        fields(arguments, {"experience"})
        value = arguments[key]
    else:
        fields(arguments, {"materials", key})
        value = arguments[key]
    if inputs.experience_policy is None:
        files = experience_files(value)
    else:
        policy = ExperiencePolicy.from_dict(inputs.experience_policy)
        files = (delta_files(value, policy.delta_bytes) if incremental else
                 bounded_views(value, policy.memory_bytes))
    return key, files


def acquisition_view(inputs, reader):
    if inputs.experience_mode in {*COMPACT_MODES, "merge-v1"}:
        return _memory_view(inputs, reader)
    index = LibraryIndex(reader, inputs.library)
    feedback = WorkspaceSnapshot.from_bytes(read_verified(reader, inputs.feedback))
    facts_file = feedback.file("feedback.json")
    if facts_file is None or feedback.file("brief.md") is None:
        raise RecordError("acquisition-feedback-invalid", "safe feedback requires facts and their brief")
    facts = parse_json(read_verified(reader, facts_file.content))
    version(facts["schema"], "behave.train-feedback/1")
    if inputs.task_ids is None:
        source_files = feedback.files
        files = tuple(FileData(file, read_verified(reader, file.content)) for file in source_files)
    else:
        from .feedback import render_brief, select_feedback

        facts = select_feedback(facts, inputs.task_ids)
        paths = {path for row in facts["observations"] for path in row["attachments"]}
        # Shared, safe window totals do not reveal another shard's candidate files.
        if "window" in facts:
            paths.add("window.json")
        source_files = tuple(file for file in feedback.files if file.path in paths)
        files = tuple(FileData(file, read_verified(reader, file.content)) for file in source_files)
        files += tuple(FileData(FileRef(name, BlobRef.of(data)), data) for name, data in (
            ("feedback.json", canonical_json(facts)), ("brief.md", render_brief(facts))))
    feedback_refs = (facts_file.content, *(file.content for file in source_files))
    if inputs.feedback_view == "compact-v1":
        from .analyst_feedback import compact_feedback

        files = tuple(file for file in files if file.reference.path != "feedback.json") + compact_feedback(facts)
    guidance = read_verified(reader, inputs.guidance)
    guidance.decode("utf-8")
    sources = {FileRef("catalog.jsonl", index.library.catalog)}
    sources.update(file for unit in index.library.units for file in (unit.content, *unit.notices))
    feedback_paths = {file.reference.path for file in files}
    if inputs.proposal_target is not None:
        guidance += (f"\n\nThis assignment targets about {inputs.proposal_target} useful task proposals. "
                     "This is a proposal objective, not an admission guarantee. Submit fewer if unsupported.\n").encode()
        if inputs.proposal_target == 0:
            guidance += b"This shard contributes analysis/experience only; submit materials=[] and do not search for new sources.\n"
    files += (FileData(FileRef("acquisition_guidance.md", BlobRef.of(guidance)), guidance),)
    files += tuple(FileData(FileRef("library/" + file.path, file.content), read_verified(reader, file.content))
                   for file in sorted(sources, key=lambda file: file.path))
    if inputs.experience is not None:
        if inputs.experience_policy is not None:
            memory_value(reader, inputs.experience, ExperiencePolicy.from_dict(inputs.experience_policy))
        files += load_experience(reader, inputs.experience)
    pending_refs = []
    for ref in inputs.pending_experience:
        pending_refs.extend((ref, *(f.reference.content for f in load_delta(reader, ref,
            ExperiencePolicy.from_dict(inputs.experience_policy).delta_bytes))))
    if inputs.construction_history is not None:
        from .construction import history_file

        files += (history_file(reader, inputs.construction_history),)
    for proposal_index, reference in enumerate(inputs.experience_proposals):
        files += tuple(FileData(FileRef(f"experience/proposals/{proposal_index}/{file.reference.path.rsplit('/', 1)[-1]}",
                                       file.reference.content), file.data)
                       for file in load_experience(reader, reference))
    WorkspaceSnapshot(tuple(file.reference for file in files))
    closure = tuple(dict.fromkeys((*index.library.closure, inputs.feedback, inputs.guidance,
                                  *(() if inputs.experience is None else (inputs.experience,)),
                                  *inputs.experience_proposals, *pending_refs,
                                  *feedback_refs, *(file.reference.content for file in files
                                                    if file.reference.path not in feedback_paths
                                                    and file.reference.path != "acquisition_guidance.md"))))
    return index, facts, files, closure


def _materials(arguments, index, observation_ids):
    row = fields(arguments, {"materials"})
    materials, seen = [], set()
    for position, value in enumerate(array(row["materials"])):
        path = f"materials[{position}]"
        item = fields(value, {"id", "rationale", "objective", "observation_ids"}
                      | {key for key in ("proposal_id", "supporting_source_ids") if key in value})
        identifier = text(item["id"], f"{path}.id")
        proposal = text(item["proposal_id"], f"{path}.proposal_id") if "proposal_id" in item else identifier
        if identifier not in index.units:
            raise RecordError("acquisition-source-invalid",
                f"{path}.id={identifier!r} is not a permitted source ID. Copy the source id from "
                "library/catalog.jsonl; put your new task label in proposal_id, not id.")
        if proposal in seen:
            field = "proposal_id" if "proposal_id" in item else "id"
            raise RecordError("acquisition-proposal-duplicate",
                f"{path}.{field}={proposal!r} repeats an earlier proposal. Use a distinct proposal_id "
                "for each task; different proposals may use the same source id.")
        for offset, supporting in enumerate(array(item.get("supporting_source_ids", []))):
            field = f"{path}.supporting_source_ids[{offset}]"
            if text(supporting, field) not in index.units:
                raise RecordError("acquisition-source-invalid",
                    f"{field}={supporting!r} is not a permitted source ID. Copy supporting source "
                    "ids from library/catalog.jsonl; use [] when no supporting source is needed.")
        text(item["rationale"], f"{path}.rationale")
        text(item["objective"], f"{path}.objective")
        observations = array(item["observation_ids"])
        for offset, observation in enumerate(observations):
            field = f"{path}.observation_ids[{offset}]"
            text(observation, field)
            if observation not in observation_ids:
                raise RecordError("acquisition-observation-invalid",
                    f"{field}={observation!r} is not an assigned observation ID. Copy observation "
                    "ids from feedback.json; use [] for related-capability exploration without a cited observation.")
        seen.add(proposal)
        materials.append(item)
    return materials


def material_source_ids(materials):
    """The source union, distinct from the number of proposed tasks."""
    return tuple(dict.fromkeys(identifier for item in materials
                               for identifier in (item["id"], *item.get("supporting_source_ids", ()))))


def _record(index, materials, experience=None, *, delta=None, library=None):
    sources = tuple(BlobRef.of(index.source_record(identifier).to_bytes())
                    for identifier in material_source_ids(materials))
    inventory = None if not sources else BlobRef.of(SourceInventory(sources).to_bytes())
    row = {"schema": "behave.acquired-materials/2", "library": (library if index is None else index.reference).as_dict(),
           "materials": materials, "inventory": None if inventory is None else inventory.as_dict()}
    if experience is not None:
        row.update(schema="behave.acquired-materials/3", experience=experience.as_dict())
    if delta is not None:
        row.update(schema="behave.acquired-materials/4", experience_delta=delta.as_dict())
    return row


def _receipt(record):
    reply = {"materials": BlobRef.of(canonical_json(record)).as_dict(), "inventory": record["inventory"]}
    if "experience" in record:
        reply["experience"] = record["experience"]
    if "experience_delta" in record:
        reply["experience_delta"] = record["experience_delta"]
    return reply


def material_submission_schema(*, incremental=False, consolidation=False, flat=False):
    material = object_schema({
        "proposal_id": {"type": "string", "description": "Your distinct label for this proposed task; not a source ID."},
        "id": {"type": "string", "description": "Existing source ID copied from library/catalog.jsonl."},
        "rationale": {"type": "string"},
        "supporting_source_ids": {"type": "array", "items": {"type": "string"}},
        "objective": {"type": "string"}, "observation_ids": {"type": "array", "items": {"type": "string"}}})
    experience = object_schema({"construction": {"type": "string"}, "solver": {"type": "string"}})
    if consolidation:
        return experience if flat else object_schema({"experience": experience})
    return object_schema({"materials": {"type": "array", "items": material},
        "experience_delta" if incremental else "experience": experience})


def load_acquired_materials(inputs, reader, head):
    """Check a completed handoff without rerunning the agent's shell commands."""
    index, facts, files, _ = acquisition_view(inputs, reader)
    events = load_chain(reader, head)
    settings = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, BlobRef.from_dict(settings["workspace"])))
    if snapshot != WorkspaceSnapshot(tuple(file.reference for file in files)):
        raise RecordError("acquisition-input-mismatch", "acquisition received a different input view")
    pending, submitted = None, None
    for event in events:
        payload = thaw_json(event.payload)
        if event.kind == "tool_requested":
            pending = payload
        elif event.kind == "tool_settled":
            if pending is None or (payload["turn"], payload["call_id"]) != (
                    pending["turn"], pending["call"]["call_id"]):
                raise RecordError("acquisition-history-invalid", "unbound acquisition tool outcome")
            call, pending = pending["call"], None
            terminal_tool = "submit_experience" if inputs.experience_mode in COMPACT_MODES else "submit_materials"
            if call["name"] != terminal_tool or payload["interrupted"] is not None:
                continue
            result = parse_json(read_verified(reader, BlobRef.from_dict(payload["result"])))
            if "error" in result or call["argument_error"] is not None:
                continue
            arguments = call["arguments"]
            if inputs.experience_mode is not None:
                key, files = _experience_submission(inputs, arguments)
                materials = _materials({"materials": arguments.get("materials", [])}, index,
                                       {row["id"] for row in facts["observations"]})
                ref = BlobRef.of(experience_snapshot(files).to_bytes())
                actual = (load_delta(reader, ref, ExperiencePolicy.from_dict(inputs.experience_policy).delta_bytes)
                          if key == "experience_delta" else load_experience(reader, ref))
                if tuple(sorted(files, key=lambda f: f.reference.path)) != actual:
                    raise RecordError("acquisition-experience-mismatch", "experience differs from submission")
                submitted = _record(index, materials, ref if key == "experience" else None,
                                    delta=ref if key == "experience_delta" else None, library=inputs.library)
                read_verified(reader, BlobRef.of(canonical_json(submitted)))
                if not payload["terminal"] or result != _receipt(submitted):
                    raise RecordError("acquisition-history-invalid", "submission differs from pinned material")
                if materials:
                    read_verified(reader, BlobRef.from_dict(submitted["inventory"]))
                    for identifier in material_source_ids(materials):
                        source = index.source_record(identifier)
                        read_verified(reader, BlobRef.of(source.to_bytes()))
                        for file in source.selected.files:
                            read_verified(reader, file.content)
                continue
            # Old journals submitted materials only. New attempts require both
            # experience views, but archived receipts keep their original form.
            extra = {"experience"} if "experience" in arguments else set()
            fields(arguments, {"materials", *extra})
            materials = _materials({"materials": arguments["materials"]}, index,
                                   {row["id"] for row in facts["observations"]})
            experience = None
            if extra:
                files = experience_files(arguments["experience"])
                experience = BlobRef.of(experience_snapshot(files).to_bytes())
                if load_experience(reader, experience) != tuple(sorted(files, key=lambda f: f.reference.path)):
                    raise RecordError("acquisition-experience-mismatch", "experience differs from submission")
            submitted = _record(index, materials, experience)
            read_verified(reader, BlobRef.of(canonical_json(submitted)))
            if submitted["inventory"] is not None:
                read_verified(reader, BlobRef.from_dict(submitted["inventory"]))
            for identifier in material_source_ids(materials):
                source = index.source_record(identifier)
                read_verified(reader, BlobRef.of(source.to_bytes()))
                for file in source.selected.files:
                    read_verified(reader, file.content)
            if not payload["terminal"] or result != _receipt(submitted):
                raise RecordError("acquisition-history-invalid", "submission differs from pinned material")
    terminal = thaw_json(events[-1].payload)
    if terminal["reason"] != "final_action":
        if submitted is not None:
            raise RecordError("acquisition-history-invalid", "unfinished attempt contains accepted material")
        return None
    if submitted is None or terminal["final_action"] != _receipt(submitted):
        raise RecordError("acquisition-history-invalid", "final action differs from acquired material")
    return submitted


def run_acquisition(*, inputs, reader, journal, backend, spec, control=None, model_admission=None,
                    development_tools=()):
    from behave.agent.loop import run_attempt

    if spec.role != inputs.role:
        raise ValueError("acquisition requires the source_acquirer role")
    index, facts, files, closure = acquisition_view(inputs, reader)
    journal.objects.publish(reader, closure)
    observation_ids = {row["id"] for row in facts["observations"]}
    revision = "source-acquisition/6:" + BlobRef.of(canonical_json(inputs.as_dict())).sha256

    def submit(workspace, arguments):
        try:
            key, memory_files = _experience_submission(inputs, arguments)
            materials = _materials({"materials": arguments.get("materials", [])}, index, observation_ids)
        except (RecordError, TypeError) as error:
            return ToolResult({"error": getattr(error, "code", "acquisition-materials-invalid"),
                               "message": str(error)})
        identifiers = material_source_ids(materials)
        inventory = index.inventory(identifiers, workspace.store) if materials else None
        for file in memory_files:
            workspace.store.put(file.data)
        experience = workspace.store.put(experience_snapshot(memory_files).to_bytes())
        record = _record(index, materials, experience if key == "experience" else None,
                         delta=experience if key == "experience_delta" else None, library=inputs.library)
        ref = workspace.store.put(canonical_json(record))
        sources = () if inventory is None else SourceInventory.from_bytes(read_verified(workspace.store, inventory)).sources
        artifacts = (ref, experience, *(file.reference.content for file in memory_files),
                     *(() if inventory is None else (inventory,)), *sources,
                     *(file.content for identifier in identifiers for file in index.source_record(identifier).selected.files))
        return ToolResult(_receipt(record), tuple(dict.fromkeys(artifacts)), True)

    compact = inputs.experience_mode in COMPACT_MODES
    incremental = inputs.experience_mode == "delta-v1"
    flat = inputs.experience_mode == "compact-v2"
    tool_prompt = "submit_compact_flat" if flat else "submit_compact" if compact else "submit" if incremental else "legacy_submit"
    tools = (() if compact else development_tools) + (
        Tool("submit_experience" if compact else "submit_materials", render("synthesis/source_acquirer/" + tool_prompt),
             material_submission_schema(incremental=incremental, consolidation=compact, flat=flat), submit, revision, terminal=True),)
    protected = tuple(sorted({*spec.read_only_paths, *(file.reference.path for file in files)}))
    guidance = next(file.data.decode() for file in files if file.reference.path == "acquisition_guidance.md")
    if compact:
        policy = ExperiencePolicy.from_dict(inputs.experience_policy)
        spec = replace(spec, max_turns=policy.max_turns, max_retries=0,
                       request_timeout_seconds=policy.request_seconds, context_tokens=policy.context_tokens,
                       context_reset_message=None)
        messages = ({"role": "system", "content": guidance +
            f"\nBoth output views together must fit {policy.memory_bytes} UTF-8 bytes."},
            {"role": "user", "content": next(f.data.decode() for f in files if f.reference.path == "memory_input.json")})
    elif incremental:
        policy = ExperiencePolicy.from_dict(inputs.experience_policy)
        messages = role_messages("synthesis/source_acquirer", guidance=guidance +
            f"\nBoth experience_delta views together must fit {policy.delta_bytes} UTF-8 bytes. Empty strings mean no new finding.")
    else:
        messages = role_messages("synthesis/source_acquirer", guidance=guidance,
            system_template="synthesis/source_acquirer/legacy_system", user_template="synthesis/source_acquirer/legacy_user")
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=files, tools=tools, control=control, model_admission=model_admission,
        initial_messages=messages)


@dataclass(frozen=True, slots=True)
class AcquisitionConfiguration:
    job: RoleJob
    model_admission: AdmissionPolicy | None = None

    def __post_init__(self):
        if type(self.job) is not RoleJob or type(self.job.inputs) is not AcquisitionInputs:
            raise TypeError("acquisition requires its fixed role job")
        if self.model_admission is not None and type(self.model_admission) is not AdmissionPolicy:
            raise TypeError("acquisition admission requires AdmissionPolicy")

    def to_bytes(self):
        return canonical_json({"schema": "behave.acquisition-configuration/1",
            "job": parse_json(self.job.to_bytes()),
            "model_admission": None if self.model_admission is None else self.model_admission.as_dict()})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "job", "model_admission"})
        version(row["schema"], "behave.acquisition-configuration/1")
        policy = row["model_admission"]
        result = cls(RoleJob.from_bytes(canonical_json(row["job"])), None if policy is None else
            AdmissionPolicy(**fields(policy, set(AdmissionPolicy.__dataclass_fields__))))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "acquisition configuration must be canonical")
        return result

    def preflight(self, reader):
        input_closure(self.job, reader)


def load_acquisition_report(reader, head):
    events = load_chain(reader, head)
    if events and events[0].kind == "bootstrap_started":
        from .experience_merge import load_bootstrap_report
        return load_bootstrap_report(reader, head)
    if events and events[0].kind == "memory_started":
        from .experience_merge import load_memory_report
        return load_memory_report(reader, head)
    if tuple(event.kind for event in events) != ("acquisition_started", "acquisition_requested", "acquisition_finished"):
        raise RecordError("acquisition-incomplete", "a settled acquisition stage is required")
    config_ref = BlobRef.from_dict(fields(thaw_json(events[0].payload), {"configuration"})["configuration"])
    config = AcquisitionConfiguration.from_bytes(read_verified(reader, config_ref))
    if thaw_json(events[1].payload) != {"job": BlobRef.of(config.job.to_bytes()).as_dict()}:
        raise RecordError("acquisition-job-mismatch", "acquisition changed its role job")
    outcome = fields(thaw_json(events[-1].payload), {"phase", "status", "code"})
    phase = PhaseReceipt.from_dict(outcome["phase"])
    if phase.role != AcquisitionInputs.role or phase.revision != 0 or phase.process is None:
        raise RecordError("acquisition-phase-mismatch", "acquisition has another role's receipt")
    billing = process_billing(reader, phase, config.job, {
        "model_admission": None if config.model_admission is None else config.model_admission.identity,
        "development_admission": None})
    role = load_chain(reader, phase.head)
    materials = None
    if role and role[-1].kind == "terminal":
        materials = load_acquired_materials(config.job.inputs, reader, phase.head)
        reason = role[-1].payload["reason"]
        expected = ("completed" if reason == "final_action" else "unfinished", "acquisition-" + reason)
    else:
        receipt = worker_receipt(load_chain(reader, phase.process.supervision), namespace="construction")
        errors = receipt["supervision_errors"]
        expected = ("interrupted", receipt["interruption"] or (
            errors[0] if errors else "construction-worker-" + receipt["status"]))
    if (outcome["status"], outcome["code"]) != expected:
        raise RecordError("acquisition-outcome-mismatch", "acquisition outcome differs from its role evidence")
    return {"schema": "behave.acquisition-report/1", "head": head.as_dict(), **outcome,
            "materials": materials, "publication_approved": False,
            "billing": {"known_estimates_by_currency": thaw_json(billing.cost_by_currency()),
                        "unpriced_dispatches": billing.unpriced_dispatches,
                        "unresolved_dispatches": billing.unresolved_dispatches}}


def acquire_materials(*, configuration, reader, run, control=None, model_pool=None):
    if type(configuration) is not AcquisitionConfiguration:
        raise TypeError("acquisition requires a fixed configuration")
    control = AttemptControl() if control is None else control
    if model_pool is not None and model_pool.policy != configuration.model_admission:
        raise ValueError("acquisition pool differs from its configured admission")
    if model_pool is None and configuration.model_admission is not None:
        model_pool = ResourcePool(configuration.model_admission)
    if configuration.job.inputs.experience_mode == "merge-v1":
        from .experience_merge import consolidate_memory
        return consolidate_memory(configuration=configuration, reader=reader, run=run,
                                  control=control, model_pool=model_pool)
    job = configuration.job
    with run.owner():
        objects, tape = run.objects, Tape(run.journal("acquisition"))
        reference = objects.put(configuration.to_bytes())
        if not tape.events:
            refs = input_closure(job, reader)
            objects.publish(reader, refs)
            tape.record("acquisition_started", {"configuration": reference.as_dict()}, (reference, *refs))
        else:
            tape.record("acquisition_started", {"configuration": reference.as_dict()})
        if tape.events[-1].kind == "acquisition_finished":
            head, _ = run.seal("acquisition")
            result = load_acquisition_report(objects, head)
            phase = PhaseReceipt.from_dict(result["phase"])
            verify_process_seal(run, phase)
            actual, _ = run.seal("revisions", "r0", phase.role)
            if actual != phase.head:
                raise RecordError("acquisition-phase-mismatch", "acquisition role changed after settlement")
            return result
        job_ref = objects.put(job.to_bytes())
        tape.record("acquisition_requested", {"job": job_ref.as_dict()}, (job_ref,))
        from behave.data.synthesis import execute_role

        try:
            attempt = execute_role(job, run=run, revision=0, role=AcquisitionInputs.role,
                                   cancelled=control.cancelled, model_pool=model_pool, development_pool=None)
            status = "completed" if attempt.reason == "final_action" else "unfinished"
            code = "acquisition-" + attempt.reason
        except AttemptInterrupted as error:
            status, code = "interrupted", error.code
        phase, closure = seal_phase(run, 0, AcquisitionInputs.role, process_backed=True)
        tape.record("acquisition_finished", {"phase": phase.as_dict(), "status": status, "code": code},
                    closure)
        tape.finish()
        head, _ = run.seal("acquisition")
        return load_acquisition_report(objects, head)


def workload_limits(value):
    row = fields(value, {"max_tasks", "max_observations"})
    if any(type(v) is not int or v < 1 for v in row.values()):
        raise ValueError("Analyst workload limits must be positive integers")
    return row


def task_shards(facts, task_ids, limits):
    """Keep all updates of one task together, including incomplete-only tasks.

    Limits bound observed task/attempt counts, not admission or evidence availability.
    An individually oversized task owns a shard; its evidence is never truncated.
    Cancelled/unstarted-only tasks stay visible but do not create extra Analysts.
    """
    limits = workload_limits(limits)
    weights = Counter(row["task_id"] for row in facts["observations"])
    ordered = tuple(dict.fromkeys(task_ids))
    positions = {task: i for i, task in enumerate(ordered)}
    shards, loads = [], []
    for task in sorted(ordered, key=lambda t: (-weights[t], positions[t])):
        if not weights[task] and shards:
            i = min(range(len(shards)), key=lambda i: (len(shards[i]), i))
            shards[i].append(task)
            continue
        eligible = [i for i, shard in enumerate(shards)
                    if len(shard) < limits["max_tasks"]
                    and loads[i] + weights[task] <= limits["max_observations"]]
        if eligible:
            i = min(eligible, key=lambda i: (loads[i], len(shards[i]), i))
            shards[i].append(task)
            loads[i] += weights[task]
        else:
            shards.append([task])
            loads.append(weights[task])
    # An empty window can still support source exploration; do not fabricate tasks.
    return tuple(tuple(shard) for shard in shards) or ((),)


def feedback_task_ids(facts, *, include_incomplete=False):
    rows = [group for group in facts["tasks"] if group["complete"]]
    if "window" in facts or include_incomplete:
        rows = (*facts["tasks"], *facts["excluded_attempts"])
    return tuple(dict.fromkeys(row["task_id"] for row in rows))


def acquisition_assignments(templates, facts, task_ids, *, library, feedback, round_id, policy, model, limits=None):
    if limits is None:
        selected = tuple(None if len(templates) == 1 else task_ids[i::len(templates)]
                         for i in range(len(templates)))
    else:
        if not templates:
            raise ValueError("bounded acquisition requires an Analyst template")
        selected = task_shards(facts, task_ids, limits)
    targets = [t.job.inputs.proposal_target for t in templates]
    if any(n is not None for n in targets) and not all(n is not None for n in targets):
        raise ValueError("Analyst templates must agree on proposal-target accounting")
    total = sum(targets) if targets and targets[0] is not None else None
    result = []
    for i, tasks in enumerate(selected):
        template = templates[i % len(templates)]
        target = None if total is None else total // len(selected) + (i < total % len(selected))
        inputs = replace(template.job.inputs, library=library, feedback=feedback, task_ids=tasks,
                         proposal_target=target)
        if limits is not None:
            inputs = replace(inputs, feedback_view="compact-v1")
        job = replace(template.job, inputs=inputs, model=model,
                      attempt=replace(template.job.attempt, attempt_id=f"{round_id}:acquire:{i}", model_policy=policy))
        result.append(replace(template, job=job))
    return tuple(result)
