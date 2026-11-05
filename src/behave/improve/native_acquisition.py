"""Acquire tasks on the trainer's live engines, then return its next task plan.

This is the control-Python side of slime_acquisition, not another learning loop.
It reuses the standalone Syn runtime and the shared generation controller.
"""

import sys
from dataclasses import replace
from pathlib import Path

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore, install_exclusive
from behave.runtime.records import command_scope
from behave.train.plan import TrainingManifest
from behave.train.slime_data import prepare_task_plan

from .feedback import prepare as prepare_feedback
from .feedback import prepare_window
from .input_stores import Inputs
from .learning_round import LearningParent
from .library import open_library
from .round import run_round
from .runtime_plan import continue_experience, round_runtime
from .training_connection import model_command


def install(path, value):
    payload = canonical_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not install_exclusive(path.parent, path.name, payload) and path.read_bytes() != payload:
        raise ValueError(f"acquisition inputs changed: {path}")


def execute(request_path, endpoint_path, response_path, *, cancelled=lambda: False):
    request = parse_json(request_path.read_bytes())
    output = request_path.parent
    study_root = Path(request["study"]).parent
    stage = request["stage"]
    runtime = parse_json((study_root / stage["runtime"]).read_bytes())
    configuration = parse_json((study_root / "native_configuration.json").read_bytes())
    binding = request["binding"]
    anchor = BlobRef.from_dict(request["parent_checkpoint"])
    parent = LearningParent(anchor, Path(request["native_directory"]))
    # The shared round records both routes even when Raw learning is disabled.
    # Its unchanged parent is the base model, not a nonexistent checkpoint path.
    parents = {"task": parent, "raw": LearningParent(None, Path(configuration["model_path"]))}
    receipt = request["feedback_receipt"]
    binding_path = output / "feedback_binding.json"
    install(binding_path, binding)
    # Existing frozen requests keep their single-update meaning on recovery.
    if "feedback_window" in request:
        feedback = prepare_window(tuple((item["update"], Path(item["receipt"]["objects"]).parent)
            for item in request["feedback_window"]), binding_path, output / "feedback")
    else:
        feedback = prepare_feedback(Path(receipt["objects"]).parent, binding_path, output / "feedback")
    library, library_objects = open_library(study_root / "library")
    run = JournalDirectory(output / "round")
    stores = [run.objects, DirectoryStore(binding["objects"]),
        DirectoryStore(request["native_objects"]), library_objects,
        DirectoryStore(feedback["objects"]), *(DirectoryStore(path) for path in feedback["source_objects"])]
    previous = request["previous"]
    if previous is not None:
        stores.append(DirectoryStore(previous["objects"]))
    inputs = Inputs(*stores)
    endpoint = replace(LocalChatEndpoint.from_dict(parse_json(endpoint_path.read_bytes())),
                       tokens=None, parameters=runtime["sampling"])
    # The transient address is not part of persistent role identity. A resumed
    # native trainer reauthenticates the same saved parent before updating it.
    profile = {"model": endpoint.model, "runtime_revision": endpoint.runtime_revision,
               "parameters": thaw_json(endpoint.parameters)}
    connection = output / "connection.json"
    temporary = output / "connection.pending.json"
    temporary.write_bytes(canonical_json({"checkpoint": anchor.as_dict(), "endpoint": endpoint.as_dict(),
        "control_python": sys.executable, "control_package": str(Path(__file__).resolve().parents[2])}))
    temporary.replace(connection)
    command = model_command(connection, profile, anchor.as_dict())
    policy = {"model": endpoint.model, "provider": command.identity, "transport": command.identity,
              "generation_only": True, "checkpoint": anchor.as_dict()}
    handoff = {"training_objects": binding["objects"], "training_manifest": binding["manifest"],
        "model_path": configuration["model_path"], "model_revision": configuration["model_revision"],
        "feedback_imported": True}
    guides = {name: run.objects.put((study_root / path).read_bytes())
              for name, path in runtime["guides"].items()}
    # No nested learner is launched. Do not make its unused recorded runtime
    # depend on a temporary path which can change after native recovery.
    env = {}
    plan = round_runtime(runtime, handoff, inputs, run.objects, endpoint, policy,
                         configuration, env, guides, model=command, binding=binding)
    plan = replace(plan, generation_only=True, keep_serving=True, acquired_updates=0, raw_updates=0)
    if previous is not None:
        plan = continue_experience(plan, previous, inputs, run.objects)
    # Migration happens once, before Analysts read bounded active memory. Its
    # own immutable role receipt supports replay without re-running the model.
    from behave.agent.lifetime import AttemptControl

    from .experience_merge import adopt_legacy_memory, compact_legacy_memory
    bootstrap = runtime.get("experience_bootstrap")
    if bootstrap is None:
        memory, migration = compact_legacy_memory(configuration=plan.acquisitions[0],
            reader=inputs, objects=run.objects, run=JournalDirectory(output / "memory_bootstrap"),
            control=AttemptControl(cancelled=cancelled))
    else:
        memory, migration = adopt_legacy_memory(configuration=plan.acquisitions[0],
            reader=DirectoryStore(bootstrap["objects"]), objects=run.objects,
            head=BlobRef.from_dict(bootstrap["head"]))
    if migration is not None:
        plan = replace(plan, acquisitions=tuple(replace(config, job=replace(config.job,
            inputs=replace(config.job.inputs, experience=memory))) for config in plan.acquisitions))
    result = run_round(round_id=runtime["round_id"], base_checkpoint=anchor,
        library=library, training_manifest=BlobRef.from_dict(binding["manifest"]),
        parents=parents, runtime=plan, reader=inputs, run=run, cancelled=cancelled,
        feedback_from=BlobRef.from_dict(feedback["request"]), publish_feedback=inputs.publish,
        release_model=lambda: {"owner": "native-training", "released": False})
    if result["status"] != "completed":
        raise RecordError("acquisition-incomplete", f"saved stage: {result.get('stage')}")
    head, _ = run.seal("round")
    # Publish the transition separately before preparing/rebinding the learner.
    # If preparation is interrupted, run_round reuses its completed receipt.
    install(output / "generation.json", {"head": head.as_dict(), "result": result,
        **({"experience_bootstrap": migration.as_dict()} if migration is not None else {})})
    manifest = TrainingManifest.from_bytes(run.objects.read(BlobRef.from_dict(result["training_manifest"])))
    prepared = prepare_task_plan(output / "training_plan", configuration, run.objects, manifest)
    state = {"schema": "behave.native-acquisition/1", "completed_updates": request["completed_updates"],
        "parent_checkpoint": anchor.as_dict(), "round_head": head.as_dict(),
        "objects": str(run.objects.directory), "training_manifest": prepared["manifest"],
        "training_binding": str((output / "training_plan/binding.json").resolve()),
        "experience": result.get("experience"), "construction_history": result.get("construction_history"),
        **({"experience_pending_changes": result["experience_pending_changes"]}
           if "experience_pending_changes" in result else {}),
        "generation": result.get("generation"), "task_count": len(manifest.tasks)}
    install(response_path, state)
    return state


def main():
    request, endpoint, response = map(Path, sys.argv[1:])
    with command_scope() as cancelled:
        execute(request, endpoint, response, cancelled=cancelled)
