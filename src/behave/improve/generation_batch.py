"""Targeted generation using the existing round controller and role scheduler.

Each child is a generation-only batch on the same weights and training feedback.
Only the parent releases serving and enters the shared learning handoff.
"""

import os
from dataclasses import replace

from behave.agent.journal import load_chain
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, thaw_json
from behave.core.storage import publication_map
from behave.data.release_records import ReleasePolicy, ReleaseRequest

from .materials import FrozenMaterials, MaterialRequest
from .round_data import admitted_training_manifest, committed_round_data, prepare_round_data


def target_settings(value):
    row = fields(value, {"admitted_tasks", "max_batches"} | ({"proposal_tasks"} & value.keys()))
    if any(type(row[key]) is not int or row[key] < 1 for key in row):
        raise ValueError("generation target and batch budget must be positive runtime integers")
    return row


class _Inputs:
    """Overlay newly owned receipts on the caller's existing input stores."""

    def __init__(self, objects, reader, publish):
        self.objects, self.reader, self.publisher = objects, reader, publish

    def owned(self, ref):
        return (self.objects.directory / ref.sha256).is_file()

    def read(self, ref):
        return (self.objects if self.owned(ref) else self.reader).read(ref)

    def publish(self, destination, refs):
        refs = tuple(refs)
        if len(refs) >= 128:
            # Large feedback closures mostly belong to upstream stores. List
            # only this phase's local CAS instead of probing each missing hash
            # on NFS. Never retain the inventory across coordinator transfers.
            wanted = {ref.sha256 for ref in refs}
            try:
                with os.scandir(self.objects.directory) as entries:
                    local_digests = {entry.name for entry in entries
                                     if entry.name in wanted and entry.is_file()}
            except (FileNotFoundError, NotADirectoryError):
                local_digests = set()
            ownership = ((ref, ref.sha256 in local_digests) for ref in refs)
        else:
            # Small transfers should not enumerate a potentially large CAS.
            ownership = publication_map(lambda ref: (ref, self.owned(ref)), refs)
        local, external = [], []
        for ref, owned in ownership:
            (local if owned else external).append(ref)
        destination.publish_owned(self.objects, tuple(local))
        if external:
            if self.publisher is None:
                destination.publish(self.reader, tuple(external))
            else:
                self.publisher(destination, tuple(external))


def assigned_acquisitions(runtime, remaining, batch, memory, history, inputs, objects, pending=()):
    """Divide a batch objective, never multiply it by the number of Analysts."""
    templates = runtime.acquisitions[:remaining]
    if not templates:
        raise ValueError("targeted generation requires at least one Analyst assignment")
    result = []
    for index, template in enumerate(templates):
        count = remaining // len(templates) + (index < remaining % len(templates))
        guidance = read_verified(inputs, template.job.inputs.guidance).decode()
        if runtime.acquisition_limits is None:
            guidance += (f"\n\nGeneration batch {batch + 1}. This assignment targets about {count} useful "
                         "task proposals. This is a proposal objective, not an admission guarantee. "
                         "Submit fewer if the sources or evidence do not support more.\n")
        else:
            guidance += f"\n\nGeneration batch {batch + 1}. Follow the assigned proposal target.\n"
        if batch:
            guidance += ("This is replenishment using the same training feedback. Read the prior "
                         "construction history for submitted proposals and failure reasons. "
                         "Do not repeat already constructed tasks. Propose new useful tasks "
                         "rather than rerunning candidates or rewriting completed results.\n")
        prior = template.job.inputs
        job = replace(template.job, inputs=replace(prior, guidance=objects.put(guidance.encode()),
            proposal_target=count if runtime.acquisition_limits is not None else prior.proposal_target,
            experience=memory if batch else prior.experience,
            pending_experience=pending if batch else prior.pending_experience,
            construction_history=history if batch else prior.construction_history))
        result.append(replace(template, job=job))
    return tuple(result)


def _batch_details(reader, head, result):
    """Read coordinator records and publication metadata, not role/token traces."""
    events = load_chain(reader, head, verify_artifacts=False)
    feedback = next(thaw_json(e.payload) for e in events if e.kind == "round_feedback")
    boundary = next(thaw_json(e.payload) for e in events if e.kind == "round_materials")
    material = FrozenMaterials.from_bytes(read_verified(reader, BlobRef.from_dict(boundary["materials"])))
    request = MaterialRequest.from_bytes(read_verified(reader, material.request))
    data, _ = committed_round_data(reader, BlobRef.from_dict(result["data"]))
    admitted = admitted_training_manifest(reader, data.release)
    stages = [thaw_json(e.payload) for e in events if e.kind == "round_stage"]
    proposals = sum(len(s["outcome"]["materials"]["materials"]) for s in stages
                    if s["name"].startswith("acquisition:") and s["outcome"]["status"] == "completed")
    selection = result.get("construction_selection")
    packages = (tuple(BlobRef.from_dict(item["package"]) for item in selection["selected"])
                if selection is not None else
                tuple(BlobRef.from_dict(s["outcome"]["package"]) for s in stages
                      if s["name"].startswith("construction:") and s["outcome"].get("package") is not None))
    return feedback, material, request, packages, {
        "proposed": proposals,
        "attempted": sum(s["name"].startswith("construction:") for s in stages),
        "admitted": 0 if admitted is None else len(admitted.tasks),
    }


def run_target_round(*, round_id, base_checkpoint, library, training_manifest, parents, runtime,
                     reader, run, release_model, cancelled, feedback_from, acquisition_from,
                     scope_parents, publish_feedback):
    from .round import finish_round, run_round

    target = target_settings(runtime.generation_target)
    if acquisition_from is not None or scope_parents:
        raise ValueError("resume a target batch in its own directory; frozen single-batch retries use the original entry")
    if runtime.construction.scope is not None:
        raise ValueError("target batches use direct Analyst-to-Builder construction")
    request = {"round_id": round_id, "base_checkpoint": base_checkpoint.as_dict(),
        "library": library.as_dict(), "training_manifest": training_manifest.as_dict(),
        "parents": {name: parent.record() for name, parent in parents.items()}, "runtime": runtime.record(),
        **({"feedback_from": feedback_from.as_dict()} if feedback_from is not None else {})}
    with run.owner():
        objects, tape = run.objects, Tape(run.journal("round"))
        ref = objects.put(canonical_json(request))
        tape.record("round_requested", {"request": ref.as_dict()}, (ref,))
        if tape.events[-1].kind == "round_settled":
            return thaw_json(tape.events[-1].payload)
        inputs = _Inputs(objects, reader, publish_feedback)

        def stage(name, operation, child, journal):
            if tape.cursor < len(tape.events):
                row = thaw_json(tape.take("round_stage").payload)
                if row["name"] != name:
                    raise RecordError("round-stage-order", "target batch stage order changed")
                return row
            outcome = operation()
            if outcome.get("status") == "unfinished":
                return {"name": name, "outcome": outcome}
            head, closure = child.seal(journal)
            objects.publish_owned(child.objects, closure)
            row = {"name": name, "head": head.as_dict(), "outcome": outcome}
            tape.record("round_stage", row, closure)
            return row

        counts, documents, acquisitions, packages = [], {}, [], []
        memory = history = None
        pending = ()
        memory_fields = {}
        admitted = 0
        for batch in range(target["max_batches"]):
            if admitted >= target["admitted_tasks"]:
                break
            if cancelled():
                return {"status": "unfinished", "stage": f"generation:{batch}"}
            assigned = assigned_acquisitions(runtime,
                target.get("proposal_tasks", target["admitted_tasks"] - admitted),
                batch, memory, history, inputs, objects, pending)
            child_runtime = replace(runtime, acquisitions=assigned, generation_target=None,
                generation_only=True, keep_serving=True, acquired_updates=0, raw_updates=0,
                construction_target=target["admitted_tasks"] - admitted)
            child = JournalDirectory(run.directory / "batches" / f"batch_{batch:03d}", objects=objects)
            row = stage(f"generation:{batch}", lambda batch=batch, child_runtime=child_runtime,
                child=child, feedback_from=feedback_from: run_round(
                round_id=f"{round_id}-b{batch}", base_checkpoint=base_checkpoint, library=library,
                training_manifest=training_manifest, parents=parents, runtime=child_runtime,
                reader=inputs, run=child, release_model=release_model, cancelled=cancelled,
                feedback_from=feedback_from, publish_feedback=inputs.publish), child, "round")
            if row["outcome"]["status"] != "completed":
                return {"status": "unfinished", "stage": row}
            result = row["outcome"]
            feedback, material, context, batch_packages, count = _batch_details(
                objects, BlobRef.from_dict(row["head"]), result)
            feedback_from = BlobRef.from_dict(feedback["request"])
            acquisitions.extend(context.acquisitions)
            documents.update((file.content, file) for file in material.documents)
            packages.extend(batch_packages)
            counts.append({"batch": batch, **count})
            admitted += count["admitted"]
            memory = None if result.get("experience") is None else BlobRef.from_dict(result["experience"])
            history = BlobRef.from_dict(result["construction_history"])
            pending = tuple(BlobRef.from_dict(ref) for ref in result.get("experience_pending_changes", ()))
            memory_fields = ({"experience_pending_changes": result["experience_pending_changes"]}
                             if "experience_pending_changes" in result else {})

        checkpoint = parents["task"].checkpoint or base_checkpoint
        material_request = MaterialRequest(round_id, checkpoint, library, context.feedback,
            runtime.model.identity, tuple(acquisitions), feedback_request=feedback_from)
        material_ref = objects.put(FrozenMaterials(objects.put(material_request.to_bytes()),
            tuple(documents.values())).to_bytes())
        boundary = tape.record("round_materials", {"materials": material_ref.as_dict(),
            **({"experience": memory.as_dict()} if memory is not None else {})},
            (material_ref, BlobRef.of(material_request.to_bytes())))
        for event in tape.events[:boundary.sequence + 1]:
            objects.put(event.to_bytes())
        rules = ReleasePolicy.from_bytes(read_verified(inputs, runtime.release_policy))
        policy = objects.put(replace(rules, review_model_policy=runtime.model_policy).to_bytes())
        publication = ReleaseRequest(round_id, policy, tuple(packages)) if packages else None
        released = None if runtime.keep_serving else release_model()
        child = JournalDirectory(run.directory / "data", objects=objects)
        row = stage("data", lambda: prepare_round_data(materials=material_ref, publication=publication,
            reader=objects, run=child, material_boundary=BlobRef.of(boundary.to_bytes())).as_dict(), child, "round_data")
        data, _ = committed_round_data(objects, BlobRef.from_dict(row["head"]))
        tape.record("round_construction_history", {"history": history.as_dict()}, (history,))
        task_runtime = runtime.task
        summary = {"target": target["admitted_tasks"], "max_batches": target["max_batches"],
            **({"proposal_tasks": target["proposal_tasks"]} if "proposal_tasks" in target else {}),
            "batches": counts, "proposed": sum(c["proposed"] for c in counts),
            "attempted": sum(c["attempted"] for c in counts), "admitted": admitted,
            "unsuccessful": sum(c["proposed"] for c in counts) - admitted,
            "target_met": admitted >= target["admitted_tasks"],
            "stop_reason": "target_met" if admitted >= target["admitted_tasks"] else "batch_budget_exhausted"}
        return finish_round(round_id=round_id, base_checkpoint=base_checkpoint, parents=parents,
            runtime=runtime, task_runtime=task_runtime, objects=objects, run=run, tape=tape, stage=stage,
            data=data, history=history, memory=None if memory is None else memory.as_dict(),
            training_manifest=training_manifest, input_reader=inputs, publish_inputs=inputs.publish,
            release_model=release_model if runtime.keep_serving else lambda: released,
            cancelled=cancelled, result_fields={"generation": summary, **memory_fields}, combine_training_pool=True)


def load_target_round(reader, events, request):
    """Offline child-receipt verification, not a live end-of-run reread."""
    from .learning_round import load_learning_round
    from .round_data import load_round_data
    from .round_evidence import load_round

    result = thaw_json(events[-1].payload)
    stages = [thaw_json(e.payload) for e in events if e.kind == "round_stage"]
    batches = [s for s in stages if s["name"].startswith("generation:")]
    counts = []
    for index, stage in enumerate(batches):
        head = BlobRef.from_dict(stage["head"])
        child = load_round(reader, head)
        if stage["name"] != f"generation:{index}" or child != stage["outcome"]:
            raise RecordError("round-evidence-mismatch", "generation child differs from its receipt")
        *_, count = _batch_details(reader, head, child)
        counts.append({"batch": index, **count})
    if batches and any(result.get(key) != batches[-1]["outcome"].get(key)
                       for key in ("experience", "experience_pending_changes", "construction_history")):
        raise RecordError("round-evidence-mismatch", "combined generation changed its memory or backlog")
    summary = result["generation"]
    target = target_settings(request["runtime"]["generation_target"])
    admitted = sum(c["admitted"] for c in counts)
    if (summary["batches"] != counts or summary["admitted"] != admitted
            or summary["target"] != target["admitted_tasks"]
            or summary["max_batches"] != target["max_batches"]
            or len(counts) > target["max_batches"]):
        raise RecordError("round-evidence-mismatch", "generation counts differ from admitted tasks")
    data = load_round_data(reader, BlobRef.from_dict(result["data"]))
    manifest = admitted_training_manifest(reader, data.release)
    if (0 if manifest is None else len(manifest.tasks)) != admitted:
        raise RecordError("round-evidence-mismatch", "combined publication changed admitted tasks")
    if result["learning"] is not None:
        learning = load_learning_round(reader, BlobRef.from_dict(result["learning"]))
        if learning["parents"] != result["parents"] or learning["data"] != result["data"]:
            raise RecordError("round-evidence-mismatch", "learning differs from combined generation")
    return result
