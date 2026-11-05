"""Native training boundary adapter; construction runs in control Python.

No optimizer or sampler is duplicated here. The original native binding stays
fixed; a completed acquisition selects a new immutable rollout binding. Small
handoff records are saved before the next rollout and referenced by checkpoints.
"""

import os
import time
from pathlib import Path

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore, install_exclusive
from behave.data.published import load_published_task
from behave.data.published.files import task_files
from behave.isolation.service import run_service

from .plan import TrainingManifest, TrainingPlan
from .pool import definition_refs
from .slime_rollout import read_binding
from .trainer import NativeAnchor, active_events


def retain_legacy_feedback_bindings(job, events, destination):
    """Import authenticated resume links already recorded by legacy jobs.

    Older non-Syn prefixes omitted feedback_parent. Their immutable binding
    still names the exact parent checkpoint. Copy that binding and sealed
    receipt closure, never invent a journal event or reread native tensors.
    """
    from behave.core.runtime_locations import runtime_path

    from .slime_launch import import_parent
    seen=set()
    while events:
        job=runtime_path(job)
        if job in seen:raise ValueError('feedback job ancestry repeats')
        seen.add(job)
        binding_path=job/'artifacts/plan/binding.json'
        if not binding_path.is_file():break
        binding=parse_json(binding_path.read_bytes())
        payload=canonical_json(binding)
        expected=thaw_json(active_events(events)[0].payload['binding'])
        if BlobRef.of(payload).as_dict()!=expected:
            binding=read_binding(binding_path);payload=canonical_json(binding)
        if BlobRef.of(payload).as_dict()!=expected:
            # A restart may have imported this checkpoint without producing
            # an update. Its local binding is not the owner of these events.
            continuation=job/'continuation.json'
            row=parse_json(continuation.read_bytes()) if continuation.is_file() else {}
            head=BlobRef.of(events[-1].to_bytes()).as_dict()
            if (events[-1].kind=='checkpoint_saved' and binding.get('parent_checkpoint')==head
                    and row.get('checkpoint')==head
                    and row.get('first_update')==events[-1].payload['round']+1
                    and row.get('job')):
                job=Path(row['job'])
                continue
            raise ValueError('legacy feedback binding identity differs')
        destination.put(payload)
        parent=binding.get('parent_checkpoint')
        if parent is None:break
        reader=DirectoryStore(binding['objects']);head=BlobRef.from_dict(parent)
        anchor=import_parent(reader,head,destination,route='task')
        first=min(e.payload['round'] for e in active_events(events) if e.kind=='round_scored')
        if anchor.step+1!=first:raise ValueError('legacy feedback parent is not the preceding update')
        continuation=job/'continuation.json'
        if not continuation.is_file():break
        row=parse_json(continuation.read_bytes())
        if row['checkpoint']!=parent:raise ValueError('legacy continuation and binding disagree')
        job=Path(row['job']);events=load_chain(reader,head)


def restore(source, events):
    for event in reversed(active_events(events)):
        if event.kind == "round_scored" and event.payload.get("acquisition") is not None:
            activate(source, BlobRef.from_dict(thaw_json(event.payload["acquisition"])))
            return
    if source.native.anchor is not None:
        # A new job has an empty local journal but still inherits the parent's
        # acquisition, not just its optimizer and task cooldowns.
        inputs = DirectoryStore(read_binding(source.path)["objects"])
        for event in reversed(active_events(load_chain(inputs, source.native.anchor.head))):
            if event.kind == "round_scored" and event.payload.get("acquisition") is not None:
                reference = BlobRef.from_dict(thaw_json(event.payload["acquisition"]))
                source.run.objects.put(inputs.read(reference))
                activate(source, reference)
                return


def activate(source, reference):
    if source._acquisition_state == reference:
        return
    state = parse_json(source.run.objects.read(reference))
    original=read_binding(Path(state["training_binding"]))
    replacement=source.binding.get('self_improvement',{}).get('binding_overrides',{}).get(state['training_binding'])
    binding=original if replacement is None else read_binding(Path(replacement))
    if replacement is not None:
        from .pool import resume_pool
        old_reader=DirectoryStore(original['objects']);new_reader=DirectoryStore(binding['objects'])
        before=TrainingManifest.from_bytes(old_reader.read(BlobRef.from_dict(original['manifest'])))
        after=TrainingManifest.from_bytes(new_reader.read(BlobRef.from_dict(binding['manifest'])))
        if {k for k,_ in before.tasks}!={k for k,_ in after.tasks}:
            raise ValueError('Sim refresh must preserve acquired task membership')
        # The unchanged parent sampler observations belong to the same tasks.
        mapped,changes=resume_pool(source._parent_pool,after.tasks,old_reader,new_reader)
        if changes['changed_tasks']:raise ValueError('Sim refresh changed task contents')
        source._parent_pool=mapped
    # Native update identity and stopping/configuration remain the original
    # binding. Only the authorized task plan and its input store are replaced.
    plan_binding = {key: binding[key] for key in ("objects", "plan", "manifest")}
    prepared = read_binding(source.path)
    recovery = prepared.get("rollout_recovery")
    if recovery is not None and all(prepared[key] == binding[key] for key in ("plan", "manifest")):
        plan_binding["objects"] = prepared["objects"]
    source.binding = {**source.binding, **plan_binding}
    source._acquisition_state = reference
    inputs = DirectoryStore(source.binding["objects"])
    source._pool_tasks = TrainingManifest.from_bytes(inputs.read(BlobRef.from_dict(binding["manifest"]))).tasks
    source._pool_definitions = definition_refs(inputs, tuple(ref for _, ref in source._pool_tasks))
    source._pool_definitions_published = False


def retain_feedback_parent(source, events):
    """Carry an authenticated ancestor journal through the existing save closure.

    The normal importer copies event artifacts. Listing the parent's journal and
    its artifacts once per job therefore also retains earlier ancestors, without
    copying optimizer files or introducing another checkpoint format.
    """
    anchor = source.native.anchor
    if anchor is None or any(event.payload.get("feedback_parent") is not None
                             for event in active_events(events)):
        return {}, ()
    inputs = DirectoryStore(read_binding(source.path)["objects"])
    parent = load_chain(inputs, anchor.head)
    references = tuple(dict.fromkeys(ref for event in parent
        for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
    source.run.objects.publish_owned(inputs, references)
    return {"feedback_parent": anchor.head.as_dict()}, references


def feedback_window(source, events, *, after_updates, completed_updates):
    """Use only committed updates, including an inherited pre-restart prefix."""
    needed = set(range(after_updates, completed_updates))
    if not needed:
        raise ValueError("acquisition needs a nonempty completed feedback window")
    found, visited = {}, set()

    def collect(history, reader, *, end):
        active = active_events(history)
        # An uncommitted tail cannot publish ancestry for this feedback window.
        boundary = max((index + 1 for index, event in enumerate(active)
                        if event.kind == "checkpoint_saved" and event.payload["round"] < end), default=0)
        committed = active[:boundary]
        for index, saved in enumerate(active):
            step = saved.payload.get("round")
            if saved.kind != "checkpoint_saved" or step not in needed or step in found:
                continue
            scored = active[index - 1] if index else None
            if (scored is None or scored.kind != "round_scored" or scored.payload["round"] != step
                    or scored.payload["binding"] != saved.payload["binding"]
                    or thaw_json(saved.payload["score"]) != BlobRef.of(scored.to_bytes()).as_dict()):
                raise RecordError("acquisition-feedback-invalid", "checkpoint has no matching scored receipt")
            receipt = parse_json(reader.read(BlobRef.from_dict(thaw_json(scored.payload["receipt"]))))
            found[step] = {"update": step + 1, "receipt": receipt}
        parents = {BlobRef.from_dict(thaw_json(event.payload["feedback_parent"]))
                   for event in committed if event.kind == "round_scored"
                   and event.payload.get("feedback_parent") is not None}
        if not parents and needed - found.keys() and committed:
            # Explicitly imported legacy bindings are authenticated by the
            # existing scored/checkpoint journal identity, not by their path.
            ref=BlobRef.from_dict(thaw_json(committed[0].payload['binding']))
            if (reader.directory/ref.sha256).is_file():
                binding=parse_json(reader.read(ref))
                if binding.get('parent_checkpoint') is not None:
                    parents.add(BlobRef.from_dict(binding['parent_checkpoint']))
        if len(parents) > 1:
            raise RecordError("acquisition-feedback-invalid", "job has conflicting feedback parents")
        if needed - found.keys() and parents:
            head = parents.pop()
            if head in visited:
                raise RecordError("acquisition-feedback-invalid", "feedback ancestry repeats a checkpoint")
            visited.add(head)
            parent = NativeAnchor.load(reader, head)
            first_step = min(event.payload["round"] for event in committed
                             if event.kind == "round_scored")
            if parent.route != "task" or parent.step + 1 != first_step:
                raise RecordError("acquisition-feedback-invalid", "feedback parent is not the preceding update")
            collect(load_chain(reader, head), reader, end=parent.step + 1)

    collect(events, source.run.objects, end=completed_updates)
    if (needed - found.keys() and source.native.anchor is not None
            and source.native.anchor.head not in visited):
        inputs = DirectoryStore(read_binding(source.path)["objects"])
        visited.add(source.native.anchor.head)
        collect(load_chain(inputs, source.native.anchor.head), inputs, end=source.native.anchor.step + 1)
    missing = sorted(needed - found.keys())
    if missing:
        raise RecordError("acquisition-feedback-incomplete",
            f"missing committed feedback for updates {[step + 1 for step in missing]}; do not truncate the window")
    return [found[step] for step in sorted(found)]


def before_rollout(source, rollout_id, events, endpoint):
    settings = source.binding.get("self_improvement")
    if settings is None:
        return
    restore(source, events)
    study_path = Path(settings["study"])
    study = parse_json(study_path.read_bytes())
    stage = next((item for item in study["stages"] if item.get("after_updates") == rollout_id), None)
    if stage is None:
        return
    active = active_events(events)
    if not active and source.native.anchor is not None:
        inputs = DirectoryStore(read_binding(source.path)["objects"])
        events = load_chain(inputs, source.native.anchor.head)
        active = active_events(events)
        source.run.objects.publish_owned(inputs, tuple(ref for event in events for ref in event.artifacts))
    if not active or active[-1].kind != "checkpoint_saved" or active[-1].payload["round"] != rollout_id - 1:
        raise RecordError("acquisition-checkpoint-missing", "save the completed update before acquiring tasks")
    anchor = BlobRef.of(active[-1].to_bytes())
    for event in events:
        source.run.objects.put(event.to_bytes())
    # Study-owned, not job-owned: a node/job restart must find a handoff which
    # completed after the last optimizer checkpoint but before the next update.
    directory = study_path.parent / "acquisitions" / f"update_{rollout_id:08d}" / anchor.sha256
    directory.mkdir(parents=True, exist_ok=True)
    response = directory / "response.json"
    if response.is_file():
        state = parse_json(response.read_bytes())
        if state["parent_checkpoint"] != anchor.as_dict():
            raise ValueError("saved acquisition belongs to another parent checkpoint")
        activate(source, source.run.objects.put(canonical_json(state)))
        return
    previous = None if source._acquisition_state is None else parse_json(
        source.run.objects.read(source._acquisition_state))
    scored = next(event for event in reversed(active) if event.kind == "round_scored")
    receipt = parse_json(source.run.objects.read(BlobRef.from_dict(thaw_json(scored.payload["receipt"]))))
    request = {"study": str(study_path), "stage": stage, "completed_updates": rollout_id,
        "binding": source.binding, "parent_checkpoint": anchor.as_dict(),
        "native_objects": str(source.run.objects.directory), "native_directory": active[-1].payload["native_directory"],
        "feedback_receipt": receipt, "previous": previous}
    payload = canonical_json(request)
    request_path = directory / "request.json"
    if not request_path.exists():
        # Do not reinterpret an already-frozen single-update request on recovery.
        request["feedback_window"] = feedback_window(source, active,
            after_updates=0 if previous is None else previous["completed_updates"],
            completed_updates=rollout_id)
        payload = canonical_json(request)
    # Reuse the frozen acquisition inputs on a new job as well. Transient
    # control-Python and endpoint paths come from the current owner below.
    install_exclusive(directory, request_path.name, payload)
    # Ephemeral connection state is separate from immutable stage inputs.
    # The control worker atomically rewrites its role connection before dispatch.
    endpoint_path = directory / "endpoint.json"
    temporary = directory / "endpoint.pending.json"
    temporary.write_bytes(canonical_json(endpoint.as_dict()))
    temporary.replace(endpoint_path)
    bootstrap = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
                 "from behave.improve.native_acquisition import main; main()")
    environment = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
    for name in ("BEHAVE_EDA_ROOT", "BEHAVE_WORK_DIR", "BEHAVE_SCRATCH_DIR", "TMPDIR",
                 "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON"):
        if name in os.environ:
            environment[name] = os.environ[name]
    seconds = settings.get("seconds")
    result = run_service((source.binding["control_python"], "-I", "-B", "-c", bootstrap,
        source.binding["control_package"], str(request_path), str(endpoint_path), str(response)),
        cwd=directory, environment=environment, network="host",
        output_bytes=source.binding.get("round_log_bytes"),
        deadline=None if seconds is None else time.monotonic() + seconds,
        cleanup_seconds=source.binding.get("cleanup_seconds"))
    # Keep each invocation's logs without replacing an interrupted attempt.
    log_dir = directory / "invocations" / str(time.time_ns())
    log_dir.mkdir(parents=True)
    (log_dir / "stdout.log").write_bytes(result.stdout)
    (log_dir / "stderr.log").write_bytes(result.stderr)
    (log_dir / "status.json").write_bytes(canonical_json({"status": result.status,
        "returncode": result.returncode, "elapsed_seconds": result.elapsed_seconds,
        "cleanup_complete": result.cleanup_state.quiescent}))
    if result.status != "exited" or result.returncode != 0 or not result.cleanup_state.quiescent:
        raise RecordError("acquisition-incomplete", f"retained acquisition at {directory}; no new training update")
    state = parse_json(response.read_bytes())
    activate(source, source.run.objects.put(canonical_json(state)))


def rollout_binding(source, directory):
    """Pass the active immutable plan through the existing rollout process ABI."""
    if getattr(source, "_acquisition_state", None) is None:
        return source.path
    path = directory / "binding.json"
    install_exclusive(directory, path.name, canonical_json(source.binding))
    return path


PLAN_FIELDS = ("objects", "plan", "manifest")


def committed_plan(binding, parent, reader):
    """Resolve the acquisition used by the first uncommitted native round."""
    if parent is None:
        raise ValueError("acquisition recovery requires a native checkpoint")
    anchor = NativeAnchor.load(reader, parent)
    settings = binding["self_improvement"]
    study_path = Path(settings["study"])
    study = parse_json(study_path.read_bytes())
    step = anchor.step + 1
    if any(stage.get("after_updates") == step for stage in study["stages"]):
        response = study_path.parent / "acquisitions" / f"update_{step:08d}" / parent.sha256 / "response.json"
        if not response.is_file():
            raise ValueError("partial rollout has no committed acquisition response")
        state = parse_json(response.read_bytes())
        if state.get("parent_checkpoint") != parent.as_dict() or state.get("completed_updates") != step:
            raise ValueError("acquisition response differs from the resumed checkpoint")
    else:
        reference = next((event.payload["acquisition"] for event in reversed(active_events(load_chain(reader, parent)))
                          if event.kind == "round_scored" and event.payload.get("acquisition") is not None), None)
        if reference is None:
            raise ValueError("native checkpoint carries no committed acquisition")
        state = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(reference))))
    if state.get("schema") != "behave.native-acquisition/1":
        raise ValueError("unknown acquisition response schema")
    original = read_binding(Path(state["training_binding"]))
    if state["training_manifest"] != original["manifest"]:
        raise ValueError("acquisition response and approved manifest disagree")
    replacement = settings.get("binding_overrides", {}).get(state["training_binding"])
    if replacement is not None:
        # A Sim-refresh override has its own task-revision checks in activate.
        # Partial on-policy handoff must not change either revision or judge.
        raise ValueError("partial acquisition handoff cannot change task/judge revisions")
    return state, original


def validate_active_binding(base, active, parent, reader):
    state, approved = committed_plan(base, parent, reader)
    if ({k: v for k, v in base.items() if k not in PLAN_FIELDS}
            != {k: v for k, v in active.items() if k not in PLAN_FIELDS}):
        raise ValueError("active acquisition changed more than its task plan")
    if any(active[k] != approved[k] for k in ("plan", "manifest")):
        raise ValueError("partial rollout differs from the committed acquired plan")
    active_reader = DirectoryStore(active["objects"])
    for key in ("plan", "manifest"):
        read_verified(active_reader, BlobRef.from_dict(active[key]))
    return state, approved


def prepare_handoff(binding, descriptor, objects):
    """Import an authenticated active plan into the new job's evidence store."""
    source = DirectoryStore(descriptor["objects"])
    record = parse_json(read_verified(source, BlobRef.from_dict(descriptor["record"])))
    if (record.get("schema") != "behave.rollout-handoff/1" or record.get("workers_stopped") is not True
            or record.get("checkpoint") != binding.get("parent_checkpoint")):
        raise ValueError("unfenced or mismatched acquisition handoff")
    active = record["source_binding"]
    base = read_binding(Path(record["source"]) / "artifacts/plan/binding.json")
    if base["self_improvement"] != binding["self_improvement"]:
        raise ValueError("acquisition handoff changed the study/settings")
    parent = BlobRef.from_dict(record["checkpoint"])
    state, approved = validate_active_binding(base, active, parent, objects)
    if record["manifest"] != approved["manifest"]:
        raise ValueError("handoff manifest differs from acquired tasks")
    if record.get("scored_receipt") is not None:
        from .slime_rollout import validate_scored_receipt

        receipt = parse_json(read_verified(source, BlobRef.from_dict(record["scored_receipt"])))
        validate_scored_receipt(receipt, round_id=record["round"],
                               manifest=record["manifest"], policy=record["policy"])
        if receipt.get("acquisition") != BlobRef.of(canonical_json(state)).as_dict():
            raise ValueError("scored receipt differs from the committed acquisition")
    reader = DirectoryStore(approved["objects"])
    manifest_ref, plan_ref = (BlobRef.from_dict(approved[k]) for k in ("manifest", "plan"))
    manifest = TrainingManifest.from_bytes(read_verified(reader, manifest_ref))
    plan = TrainingPlan.from_bytes(read_verified(reader, plan_ref), reader)
    if plan.manifest != manifest_ref:
        raise ValueError("acquired plan has a different task manifest")
    objects.publish_owned(reader, (manifest_ref, plan_ref, *definition_refs(reader, (r for _, r in manifest.tasks))))
    # Acquired jobs may pin their individual publication, not the combined
    # training release. Fresh workers need that release and its private judge
    # inputs too; task metadata/public input_blobs alone cannot start a task.
    publications = {}
    for job in plan.execution.jobs:
        publications.setdefault((job.task.release, job.task.task_id), job.task)
    references = []
    for (release, task_id), public in publications.items():
        task = load_published_task(reader, release, task_id)
        if task.public != public:
            raise ValueError("acquired solver inputs differ from their publication")
        record = task.manifest
        files = task_files(record)
        references.extend((release, public.revision, task.release.policy, record.run_policy,
                           record.judge_policy, *(file.content for file in files)))
    objects.publish_owned(reader, tuple(dict.fromkeys(references)))
    for job in plan.execution.jobs:
        for data in job.input_blobs():
            objects.put(data)
    objects.put(canonical_json(state))
    binding.update(plan=approved["plan"], manifest=approved["manifest"])
