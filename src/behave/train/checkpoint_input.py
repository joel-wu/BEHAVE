"""Describe the native checkpoint and HF export of a completed training job.

This is an operator handoff, not another checkpoint serializer or model server.
The existing learning ledger verifies updates; the existing native inventory
checks the cold-load file list and sizes. The HF handoff records file metadata,
not content hashes, and does not replay training-log audits or require a separate
cold-resume phase. Native loading owns tensor validation.
"""

from pathlib import Path

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.file_input import read_regular_file
from behave.core.records import parse_json, thaw_json
from behave.runtime.models import (  # noqa: F401 -- legacy exports
    CheckpointFile,
    model_handoff,
    periodic_handoff,
)
from behave.train.checkpoint_storage import job_checkpoint_directory, resolve_checkpoint
from behave.train.slime_checkpoint import checkpoint_inventory
from behave.train.slime_stage import load_learning_report
from behave.train.trainer import NativeAnchor


def prepare(job: Path, *, workers: int | None = None) -> dict:
    # Existing operator callers may pass workers; metadata needs no reader pool.
    job = job.resolve()
    runtime = parse_json(read_regular_file(job / "runtime.json"))
    if runtime["state"] != "completed":
        raise ValueError("training job has not completed; no Syn checkpoint is ready")
    artifacts = job / "artifacts"
    plan = artifacts / "plan"
    config_bytes = read_regular_file(job / "configuration.json")
    config = parse_json(config_bytes)
    first, final = config["initial_updates"], config["initial_updates"] + config["resume_updates"]
    evidence = parse_json(read_regular_file(plan / "result.json"))
    if not evidence["native_cycle_completed"]:
        raise ValueError("native training cycle is incomplete")
    continuation = job / "continuation.json"
    begin = parse_json(read_regular_file(continuation))["first_update"] if continuation.exists() else 0
    phases = [("initial", begin, first)]
    if config["resume_updates"]:
        phases.append(("resume", first, final))
    reports = []
    for phase, begin, end in phases:
        receipt = parse_json(read_regular_file(plan / f"{phase}.result.json"))
        stage = JournalDirectory(plan / "learning" / phase)
        report = load_learning_report(stage.objects, BlobRef.from_dict(receipt["head"]))
        if (
            report != receipt["report"]
            or report["status"] != "completed"
            or report["route"] != "task"
            or report["first_update"] != begin
            or report["stop_update"] != end
        ):
            raise ValueError("training phase disagrees with its requested update range")
        reports.append(receipt["head"])
    anchor = NativeAnchor.load(stage.objects, BlobRef.from_dict(report["checkpoint"]))
    checkpoint = job_checkpoint_directory(job, step=anchor.step, files=anchor.files, checkpoint=anchor.head)
    if anchor.step != final - 1 or checkpoint_inventory(checkpoint, anchor.step) != anchor.files:
        raise ValueError("archived native checkpoint differs from the completed learning stage")
    binding = parse_json(read_regular_file(plan / "binding.json"))
    return {
        "schema": "behave.slime-checkpoint-input/1",
        "job": str(job),
        "configuration": BlobRef.of(config_bytes).as_dict(),
        "learning_heads": reports,
        "checkpoint": anchor.head.as_dict(),
        "checkpoint_objects": str(stage.objects.directory),
        "checkpoint_directory": str(checkpoint),
        "step": anchor.step,
        **model_handoff(artifacts / "final_hf", anchor.head),
        "training_objects": binding["objects"],
        "training_manifest": binding["manifest"],
    }


def prepare_round(previous: Path) -> dict:
    """Continue a completed Syn Task pass using its owned learning receipts.

    Read the settled handoff, checkpoint inventory and HF file metadata. Do not
    replay task construction/publication or reread transcripts and weight bytes.
    The native loader remains responsible for optimizer and sampler restoration.
    """
    previous = previous.resolve()
    result = parse_json(read_regular_file(previous / "result.json"))
    if result["status"] != "completed" or result["learning"] is None:
        raise ValueError("the previous Syn round has no completed learning handoff")
    learning = JournalDirectory(previous / "round/learning")
    events = learning.journal("paired_learning").load(verify_artifacts=False)
    if (
        not events
        or events[-1].kind != "paired_learning_settled"
        or BlobRef.of(events[-1].to_bytes()).as_dict() != result["learning"]
    ):
        raise ValueError("Syn result differs from its settled learning receipt")
    settled = thaw_json(events[-1].payload)
    if settled["parents"] != result["parents"]:
        raise ValueError("Syn learning parents differ from the completed round")
    index, task = next(
        (i, row)
        for i, row in reversed(tuple(enumerate(settled["passes"])))
        if row["route"] == "task" and row["head"] is not None
    )
    parent = result["parents"]["task"]
    anchor = NativeAnchor.load(learning.objects, BlobRef.from_dict(parent["checkpoint"]))
    checkpoint = resolve_checkpoint(parent["directory"], step=anchor.step, files=anchor.files, checkpoint=anchor.head)
    if anchor.route != "task" or checkpoint_inventory(checkpoint, anchor.step) != anchor.files:
        raise ValueError("Syn native checkpoint differs from its completed Task pass")
    plan = learning.directory / "plans" / f"pass_{index}"
    binding = parse_json(read_regular_file(plan / "binding.json"))
    if binding["manifest"] != result["training_manifest"]:
        raise ValueError("Syn Task pass did not train the returned cumulative pool")
    return {
        "schema": "behave.slime-checkpoint-input/1",
        "previous_round": str(previous),
        "configuration": BlobRef.of(read_regular_file(previous / "configuration.json")).as_dict(),
        "learning_heads": [task["head"]],
        "checkpoint": anchor.head.as_dict(),
        "checkpoint_objects": str(learning.objects.directory),
        "checkpoint_directory": str(checkpoint),
        "step": anchor.step,
        **model_handoff(plan / f"hf_{anchor.step}", anchor.head),
        "training_objects": binding["objects"],
        "training_manifest": result["training_manifest"],
        "parents": result["parents"],
        "binding": str(plan / "binding.json"),
        "feedback_round": str(Path(binding["run_directory"]) / f"round_{anchor.step:08d}"),
    }
