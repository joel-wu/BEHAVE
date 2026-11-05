"""Recover only an interrupted memory merge, never rerun its Analyst proposals."""

import re
from contextlib import ExitStack
from pathlib import Path

from behave.agent.journal import load_chain
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import install_exclusive
from behave.data.phase_journal import AUXILIARY, auxiliary_path, seal_process

from .acquisition_records import AcquisitionInputs


def _heads(child):
    paths = {"acquisition": ("acquisition",), "role": ("revisions", "r0", AcquisitionInputs.role)}
    paths.update({name: auxiliary_path(0, AcquisitionInputs.role, name) for name in AUXILIARY})
    result = {}
    for name, parts in paths.items():
        events = child.journal(*parts).load(verify_artifacts=False)
        result[name] = None if not events else BlobRef.of(events[-1].to_bytes()).as_dict()
    return result


def _memory_target(directory, study):
    root = next((path for path in (directory, *directory.parents)
                 if path.name in {"experience", "memory_bootstrap"}), None)
    if root is None or not root.is_relative_to(study.parent / "acquisitions"):
        raise ValueError("memory is outside this owner's acquisition study")
    suffix = directory.relative_to(root).as_posix()
    if suffix == "." or re.fullmatch(r"retries/[0-9]{3,}", suffix):
        return root, None
    prefix = "chunks" if root.name == "memory_bootstrap" else "batches"
    match = re.fullmatch(prefix + r"/([0-9]{4,})(?:/retries/[0-9]{3,})?", suffix)
    if match is None:
        raise ValueError("not a memory batch or retry directory")
    return root, int(match[1])


def _verify_pending_batch(parent, ordinal, reference):
    """Only the uncommitted next batch can be fenced, with its exact inputs."""
    from .acquisition import AcquisitionConfiguration, load_acquisition_report
    from .experience import ExperiencePolicy, delta_batches
    from .experience_merge import batch_configuration

    events = parent.journal("acquisition").load()
    if (not events or events[0].kind != "memory_started" or
            any(event.kind != "memory_batch" for event in events[1:]) or len(events) - 1 != ordinal):
        raise RecordError("memory-recovery-context", "batch is not the next unfinished consolidation batch")
    config = AcquisitionConfiguration.from_bytes(read_verified(parent.objects,
        BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    inputs = config.job.inputs
    if inputs.experience_mode != "merge-v1":
        raise RecordError("memory-recovery-context", "parent is not a bounded memory consolidation")
    policy = ExperiencePolicy.from_dict(inputs.experience_policy)
    batches = delta_batches(parent.objects, inputs.experience, inputs.experience_proposals, policy)
    if ordinal >= len(batches):
        raise RecordError("memory-recovery-context", "batch is outside the recorded consolidation")
    prior = inputs.experience
    for index, event in enumerate(events[1:]):
        expected = batch_configuration(config, prior, batches[index], index)
        row = thaw_json(event.payload)
        head = BlobRef.from_dict(row["head"])
        child = load_chain(parent.objects, head)
        outcome = load_acquisition_report(parent.objects, head)
        if (row["configuration"] != BlobRef.of(expected.to_bytes()).as_dict() or
                child[0].payload["configuration"] != row["configuration"] or
                outcome != row["outcome"] or outcome["status"] != "completed"):
            raise RecordError("memory-recovery-context", "preceding memory batch is not an accepted result")
        prior = BlobRef.from_dict(outcome["materials"]["experience"])
    if reference != BlobRef.of(batch_configuration(config, prior, batches[ordinal], ordinal).to_bytes()):
        raise RecordError("memory-recovery-context", "pending memory batch changed its inputs")


def fence_memory(directory, owner_job):
    """Explicit operator recovery after the enclosing native owner was stopped.

    A process-tree kill may leave the inner supervisor journal unsealed. Use
    the enclosing owner's actual cleanup receipt, not absence of a PID, to
    authorize abandoning this exact unfinished merge. No old journal is edited.
    """
    directory, owner_job = Path(directory).resolve(), Path(owner_job).resolve()
    runtime = parse_json((owner_job / "runtime.json").read_bytes())
    node = parse_json((owner_job / "node_status.json").read_bytes())
    config = parse_json((owner_job / "configuration.json").read_bytes())
    study = Path(config["self_improvement"]["study"]).resolve()
    memory_root, batch = _memory_target(directory, study)
    root = next((p for p in directory.parents if (p / "request.json").is_file()), None)
    connection = {} if root is None else parse_json((root / "connection.json").read_bytes())
    if (node["state"] != "stopped" or runtime["state"] not in {"interrupted", "failed", "completed"}
            or not Path(connection.get("control_package", "/")).is_relative_to(runtime["local_directory"])):
        raise ValueError("memory's actual native owner has not stopped")
    phase = runtime["phase"]
    learning = JournalDirectory(owner_job / "artifacts/plan/learning" / phase)
    settlements = [e for e in learning.journal("learning").load(verify_artifacts=False)
                   if e.kind == "learning_process_settled"]
    if not settlements or settlements[-1].payload["cleanup"] != "complete":
        raise ValueError("native owner cleanup is not confirmed")
    child = JournalDirectory(directory)
    with ExitStack() as owners:
        parent = JournalDirectory(memory_root)
        if memory_root != directory:
            owners.enter_context(parent.owner())
        owners.enter_context(child.owner())
        events = child.journal("acquisition").load(verify_artifacts=False)
        if (not events or events[0].kind != "acquisition_started" or
                events[-1].kind == "acquisition_finished"):
            raise ValueError("fencing is only needed for an unfinished memory owner")
        reference = BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])
        if memory_root.name == "memory_bootstrap":
            from .acquisition import AcquisitionConfiguration
            configured = AcquisitionConfiguration.from_bytes(read_verified(child.objects, reference))
            if batch is not None:
                from .experience_merge import verify_pending_chunk
                configured = verify_pending_chunk(parent, batch, reference)
            request = parse_json((root / "request.json").read_bytes())
            source = configured.job.inputs
            if ((batch is None and source.experience_mode != "compact-v1") or
                    source.experience_proposals or source.experience is None or
                    source.experience.as_dict() != (request.get("previous") or {}).get("experience") or
                    configured.job.attempt.model_policy.get("checkpoint") != request["parent_checkpoint"]):
                raise RecordError("memory-recovery-context", "bootstrap differs from the saved native request")
        if batch is not None and memory_root.name != "memory_bootstrap":
            _verify_pending_batch(parent, batch, reference)
        elif batch is None and memory_root != directory:
            original = parent.journal("acquisition").load(verify_artifacts=False)
            if (not original or original[0].kind != "acquisition_started" or
                    original[0].payload["configuration"] != reference.as_dict()):
                raise RecordError("memory-recovery-context", "retry does not belong to the original memory job")
        record = {"schema": "behave.memory-recovery-fence/1", "owner_job": str(owner_job),
                  "owner_settlement": parse_json(settlements[-1].to_bytes()), "journals": _heads(child),
                  "configuration": thaw_json(events[0].payload)["configuration"]}
        payload = canonical_json(record)
        if not install_exclusive(directory, "recovery_fence.json", payload) and (
                directory / "recovery_fence.json").read_bytes() != payload:
            raise ValueError("memory recovery fence changed")
        return record


def memory_child(directory, configuration):
    """Keep failed attempts immutable and verify cleanup before a fresh worker.

    A merge restarts from the same submitted notes and prior memory, not from
    unsubmitted scratch text. Each failed attempt remains on disk for diagnosis.
    The normal acquisition API consumes/replays completed or pending children.
    """
    ordinal = 0
    while True:
        child = JournalDirectory(directory if ordinal == 0 else directory / "retries" / f"{ordinal:03d}")
        events = child.journal("acquisition").load(verify_artifacts=False)
        if not events:
            return child
        original = BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])
        if read_verified(child.objects, original) != configuration.to_bytes():
            raise RecordError("memory-recovery-context", "saved merge has different inputs")
        fence = child.directory / "recovery_fence.json"
        if events[-1].kind != "acquisition_finished" and fence.is_file():
            record = parse_json(fence.read_bytes())
            if (record["schema"] != "behave.memory-recovery-fence/1" or record["journals"] != _heads(child)
                    or record["configuration"] != original.as_dict()
                    or record["owner_settlement"]["kind"] != "learning_process_settled"
                    or record["owner_settlement"]["payload"]["cleanup"] != "complete"):
                raise RecordError("memory-recovery-fence", "memory changed or native cleanup is not confirmed")
            ordinal += 1
            continue
        if events[-1].kind != "acquisition_finished" or events[-1].payload["status"] != "interrupted":
            return child
        # Unknown cleanup is not permission to start another model/tool owner.
        seal_process(child, 0, AcquisitionInputs.role)
        ordinal += 1
