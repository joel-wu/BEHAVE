"""Saved checkpoint measurements: paths, serving settings and preparation.

This is metadata composition only: no model loading, Slurm submission, scoring,
or checkpoint discovery. Existing settings, archive identities and preparation
records are retained; entry points import this single implementation.
"""

import fcntl
import json
import os
import re
import signal
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event

from behave.runtime.jobs import wait_control
from behave.runtime.records import write_json

from .resources import execution_cpu_ids, node_capacity, scoring_cpu_plan, validate_generation_resources  # noqa: F401


def read(path):
    return json.loads(Path(path).read_text())


def checkpoint_directory(root, entry):
    config = read(Path(root) / "configuration.json")
    return Path(config.get("archive_root", root)) / entry["model_line"] / f"update-{entry['update']:03d}"


def measurement_directory(root, entry, seed):
    directory = checkpoint_directory(root, entry)
    if entry.get("measurement_revision"):
        revision = entry["measurement_revision"]
        if not revision or Path(revision).name != revision or revision in {".", ".."}:
            raise ValueError("measurement revision must be a simple directory name")
        directory = directory / "revisions" / revision
    return directory / f"eval-seed-{seed:02d}"


def scoring_directory(root, entry, seed):
    name = read(Path(root) / "configuration.json").get("scoring_pass", "baseline")
    if type(name) is not str or not name or name in {".", ".."} or "\0" in name or Path(name).name != name:
        raise ValueError("scoring_pass must be a single directory name")
    return measurement_directory(root, entry, seed) / "scoring" / name


def completed_scoring(root, entry, seed):
    """Reuse only a receipt for this measurement's requested frozen score pass.

    A legacy marker without an output identity is not proof of this pass. The
    ordinary scorer can still reconcile its retained control and journals; this
    lookup never reloads weights, rewrites results or regenerates candidates.
    """
    output = scoring_directory(root, entry, seed)
    marker = output.parent.parent / "scoring.json"
    if not marker.exists():
        return False
    recorded = read(marker)
    saved = recorded.get("output")
    return (recorded.get("state") == "completed" and type(saved) is str and bool(saved)
            and Path(saved).resolve() == output.resolve())


def requested_seeds(root, entry):
    config = read(Path(root) / "configuration.json")
    return entry["inference_seeds"] if "inference_seeds" in entry else config["inference_seeds"]


def snapshot_directory(root, *, configuration=None):
    root = Path(root)
    config = read(root / "configuration.json") if configuration is None else configuration
    return Path(config.get("archive_root", root)) / config.get("code_directory", "code") / "snapshot"


def control_directory(root, entry=None):
    root = Path(root)
    directory = (root if entry is None else generation_directory(root, entry)) / "control"
    launch_id = read(root / "configuration.json").get("launch_id")
    return directory if launch_id is None else directory / launch_id


def generation_directory(root, entry):
    config = read(Path(root) / "configuration.json")
    if config.get("independent_measurement"):
        seeds = requested_seeds(root, entry)
        if len(seeds) != 1:
            raise ValueError("independent generation requires exactly one inference seed")
        return measurement_directory(root, entry, seeds[0])
    return checkpoint_directory(root, entry)


def serving_spec(template, configuration, handoff, seed):
    from behave.serving.spec import ServingSpec

    spec = ServingSpec.from_dict(template["spec"])
    return replace(spec, tensor_parallel=configuration["resources"]["tensor_parallel_per_engine"],
        context_length=configuration["attempt"]["context_tokens"], seed=seed,
        model_path=handoff["model_path"], model_revision=handoff["model_revision"],
        startup_seconds=1800, lifetime_seconds=None, cleanup_seconds=300,
        native_arguments=("--watchdog-timeout", "1800"))


def verify_preparation_directory(directory, *, retained=()):
    # Only files produced by prepare_measurement are allowed. Even an empty
    # control directory means submission may have started: never recreate it.
    # Explicit startup recovery separately validates its pinned historical
    # controls and archive before allowing those two reserved directories.
    if set(retained) - {"control", "startup_failures"}:
        raise ValueError("unknown retained startup directory")
    allowed = {"manifest.json", "serving.json", "rollout", *retained}
    rollout_allowed = {"settings.json", "sampling.json", "trained"}
    for path, names in ((directory, allowed), (directory / "rollout", rollout_allowed),
                        (directory / "rollout/trained", {"configuration.json"})):
        if path.is_symlink():
            raise ValueError("possible execution artifacts; refusing preparation resume")
        if not path.exists():
            continue
        for child in path.iterdir():
            if child.name in names:
                continue
            # An interrupted atomic JSON write can retain its uncommitted
            # staging file. Ignore only the shared writer's exact name shape;
            # never read, promote or delete these bytes during recovery.
            staging = any(name.endswith(".json") and re.fullmatch(
                rf"\.{re.escape(name)}\.[a-z0-9_]{{8}}\.tmp", child.name) for name in names)
            if not (staging and child.is_file() and not child.is_symlink()):
                raise ValueError("possible execution artifacts; refusing preparation resume")


def measurement_identity(root, entry, seed, handoff, *, configuration=None):
    """Compose identity before publishing files or moving a failed startup."""
    root = Path(root)
    config = read(root / "configuration.json") if configuration is None else configuration
    inputs = read(root / "task_index.json")
    identity = {"model_line": entry["model_line"], "update": entry["update"],
        "inference_seed": seed, "checkpoint": handoff, "setting": config["setting"],
        "sampling": read(root / "sampling.json"), "attempt": config["attempt"],
        "tensor_parallel": config["resources"]["tensor_parallel_per_engine"],
        "release": inputs["release"],
        "code": read(snapshot_directory(root, configuration=config).parent / "snapshot.json")["sha256"]}
    if config.get("generation_recovery") is not None:
        identity["generation_recovery"] = config["generation_recovery"]
    return identity


def measurement_files(root, entry, seed, handoff, *, configuration=None):
    """The existing preparation recipe as data; no files or workers are changed."""
    root = Path(root)
    config = read(root / "configuration.json") if configuration is None else configuration
    inputs = read(root / "task_index.json")
    directory = measurement_directory(root, entry, seed)
    spec = serving_spec(read(root / "serving_template.json"), config, handoff, seed)
    settings = {"provider": "sglang", "model": config["model"], "setting": config["setting"],
        "task_objects": inputs["objects"], "release": inputs["release"],
        "tasks": [row["task"] for row in inputs["tasks"]], "attempt": config["attempt"],
        "attempt_namespace": str(directory), "score_on_submit": False,
        "workers": config["resources"]["rollout_workers"],
        "model_workers": config["resources"]["rollout_workers"], "judge_workers": 12,
        "cpus": config["resources"]["generation_cpus"],
        "maximum_input_tokens": config["attempt"]["context_tokens"],
        "output_preview_bytes": config["output_preview_bytes"], "process_limit": None}
    settings["isolation_startup_slots"] = config.get("isolation_startup_slots")
    if config.get("generation_recovery") is not None:
        recovery = config["generation_recovery"]
        original = read(Path(recovery["source"]) / "settings.json")
        for key in ("model", "setting", "release", "tasks", "attempt", "score_on_submit",
                    "maximum_input_tokens", "output_preview_bytes"):
            if settings.get(key) != original.get(key):
                raise ValueError(f"generation recovery changes {key}")
        settings["attempt_namespace"] = original["attempt_namespace"]
        settings["generation_recovery"] = recovery
    return {"rollout/settings.json": settings, "rollout/sampling.json": read(root / "sampling.json"),
            "rollout/trained/configuration.json": {key: handoff[key] for key in ("model_path", "model_revision")},
            "serving.json": {"spec": spec.as_dict()},
            "manifest.json": measurement_identity(root, entry, seed, handoff, configuration=config)}


def prepare_measurement(root, entry, seed, handoff, *, resume_preparation=False):
    root = Path(root)
    directory = measurement_directory(root, entry, seed)
    output = directory / "rollout"
    if resume_preparation:
        verify_preparation_directory(directory)
    identity = measurement_identity(root, entry, seed, handoff)
    if (directory / "manifest.json").exists():
        prior = read(directory / "manifest.json")
        if prior == identity and not resume_preparation:
            return output
        # Only an unexecuted preparation can adopt a repaired code snapshot.
        # Keep its complete former configuration; never rewrite executed data.
        if prior != identity and (resume_preparation or {k:v for k,v in prior.items() if k != "code"} !=
                {k:v for k,v in identity.items() if k != "code"}
                or (directory / "generation.json").exists() or (output / "run").exists()):
            raise ValueError("measurement identity changed; refusing overwrite")
        if prior != identity:
            write_json(directory / "preparations" / f"{prior['code']}.json", {
                "manifest": prior, "settings": read(output / "settings.json"),
                "serving": read(directory / "serving.json"), "state": "never_launched"})
    for name, value in measurement_files(root, entry, seed, handoff).items():
        path = directory / name
        if resume_preparation and path.is_symlink():
            raise ValueError("prepared measurement file cannot be a symlink")
        if resume_preparation and path.exists():
            if read(path) != value:
                raise ValueError("prepared measurement contents changed")
        else:
            write_json(path, value)

    return output


def prepare_dispatch(root, assignments, *, code_directory, probe_request, launch_id,
                     tensor_parallel=1, revisions=None):
    """Partition the remaining measurements over distinct full-node owners.

    This writes scheduling metadata only. Existing generation, archives, model
    sampling and scoring remain the common entry points. Explicit revisions
    retain failed attempts in place instead of overwriting their evidence.
    """
    root = Path(root).resolve()
    source_config, source_queue = read(root / "configuration.json"), read(root / "queue.json")
    prior_control = control_directory(root)
    if (prior_control / "status.json").exists() and read(prior_control / "status.json")["state"] in {"starting", "supervising"}:
        raise ValueError("original dispatcher must be stopped before partitioning")
    _, gpu_capacity = node_capacity(source_config["resources"])
    if type(tensor_parallel) is not int or tensor_parallel < 1 or gpu_capacity % tensor_parallel:
        raise ValueError("tensor parallel size must divide allocated_gpus")
    planned_resources = {**source_config["resources"],
        "tensor_parallel_per_engine": tensor_parallel, "engines": gpu_capacity // tensor_parallel,
        "device_groups": [",".join(str(i) for i in range(start, start + tensor_parallel))
                          for start in range(0, gpu_capacity, tensor_parallel)]}
    validate_generation_resources(planned_resources)
    owners, allocations = {}, set()
    for name, assignment in assignments.items():
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("lane name must be a simple directory name")
        allocation = assignment["allocation"]
        if type(allocation) is not int or allocation <= 0 or allocation in allocations:
            raise ValueError("each lane must own a distinct positive allocation")
        allocations.add(allocation)
        for line in assignment["model_lines"]:
            if line in owners:
                raise ValueError("model line assigned twice")
            owners[line] = name
    queues = {name: [] for name in assignments}
    covered, completed = [], []
    for original in source_queue["checkpoints"]:
        entry = dict(original)
        pending = []
        for seed in requested_seeds(root, entry):
            output = measurement_directory(root, entry, seed)
            generation = output / "generation.json"
            key = [entry["model_line"], entry["update"], seed]
            if generation.exists() and read(generation)["state"] == "completed" and completed_scoring(root, entry, seed):
                completed.append({"key": key, "directory": str(output)})
            else:
                pending.append(seed)
        if not pending:
            continue
        if entry["model_line"] not in owners:
            raise ValueError(f"unassigned pending model line: {entry['model_line']}")
        entry["inference_seeds"] = pending
        revision = (revisions or {}).get(f"{entry['model_line']}/{entry['update']}")
        if revision:
            entry["measurement_revision"] = revision
        queues[owners[entry["model_line"]]].append(entry)
        for seed in pending:
            covered.append({"key": [entry["model_line"], entry["update"], seed],
                "lane": owners[entry["model_line"]], "directory": str(measurement_directory(root, entry, seed))})
    manifest = {"schema": "behave.eval-dispatch/1", "archive_root": str(root),
        "launch_id": launch_id, "tensor_parallel": tensor_parallel,
        "completed_reused": completed, "measurements": covered, "lanes": {}}
    destination = root / "dispatch" / launch_id
    destination.mkdir(parents=True, exist_ok=False)
    for name, assignment in assignments.items():
        lane = destination / name
        resources = {**planned_resources, "allocation": assignment["allocation"]}
        config = {**source_config, "archive_root": str(root), "code_directory": code_directory,
            "launch_id": f"{launch_id}-{name}", "resources": resources,
            "serving_probe_request": str(Path(probe_request).resolve())}
        write_json(lane / "configuration.json", config)
        write_json(lane / "queue.json", {**source_queue, "checkpoints": queues[name]})
        for file in ("task_index.json", "sampling.json", "judge_policy.json", "serving_template.json"):
            (lane / file).symlink_to(root / file)
        manifest["lanes"][name] = {"root": str(lane), "allocation": assignment["allocation"],
            "checkpoints": len(queues[name]), "measurements": sum(len(row["inference_seeds"]) for row in queues[name])}
    write_json(destination / "manifest.json", manifest)
    return manifest


def scoring_quality(output, expected_tasks):
    """Process exit is not a verdict: require a complete, settled score report."""
    paths = sorted(Path(output).glob("progress-*.json"))
    counts = {"requested": 0, "no_result": 0, "pending_judgment": 0}
    reports, all_settled = 0, True
    try:
        for path in paths:
            for record in read(path):
                summary = record.get("report", {}).get("summary", {})
                rows = summary.get("candidates", [])
                requested = summary.get("requested", 0)
                reports += 1
                counts["requested"] += requested
                counts["no_result"] += summary.get("no_result", 0)
                counts["pending_judgment"] += summary.get("pending_judgment", 0)
                all_settled = all_settled and (
                    record.get("state") == "completed" and len(rows) == requested
                    and all(row.get("status") in {"scored", "no_submission"} for row in rows))
    except (OSError, ValueError, TypeError, AttributeError):
        return {"complete": False, "reason": "unreadable-scoring-report", **counts}
    complete = (reports > 0 and all_settled and counts["requested"] == expected_tasks
                and counts["no_result"] == counts["pending_judgment"] == 0)
    return {"complete": complete, "expected_tasks": expected_tasks, **counts}


def score(root, entry, seed, *, repository, cwd=None):
    from behave.eval.scoring import launch, prepare

    root = Path(root)
    config = read(root / "configuration.json")
    directory = measurement_directory(root, entry, seed)
    output = scoring_directory(root, entry, seed)
    resources = config["resources"]
    capacity, _ = node_capacity(resources)
    _, cpu_ids = scoring_cpu_plan(resources["scoring_cpus"], cpu_ids=resources.get("scoring_cpu_ids"),
        allocated_cpus=capacity, allocated_cpu_ids=resources.get("allocated_cpu_ids"))
    if cpu_ids is not None and set(cpu_ids).intersection(resources.get("generation_cpu_ids", ())):
        raise ValueError("scoring and generation CPU IDs must be disjoint")
    # Running from the study snapshot pins both fresh scoring and resumed orchestration.
    if not (output / "plan.json").exists():
        prepare(output, sources=[directory / "rollout"], repository=repository, workers=config["resources"]["scoring_workers"],
            policy=root / "judge_policy.json", all_submitted=True, retry_testbench=True, search_seconds=0,
            isolation_startup_slots=config.get("isolation_startup_slots"))
    launch(output, resources["allocation"], resources["scoring_cpus"], cpu_ids=cpu_ids,
        allocated_cpus=capacity, allocated_cpu_ids=resources.get("allocated_cpu_ids"), cwd=cwd)
    result = wait_control(output / "control/shard-0", allow_failure=True)
    quality = scoring_quality(output, config["inputs"]["tasks"])
    state = result["state"]
    if state == "completed" and not quality["complete"]:
        state = "needs_recovery"
    write_json(directory / "scoring.json", {"state": state, "process_state": result["state"],
        "returncode": result.get("returncode"), "output": str(output), "quality": quality})
    return state == "completed"


def _generate(root, schedule, *, cwd=None):
    from behave.runtime.jobs import status_of

    root = Path(root)
    read(root / "configuration.json")  # Validate configuration before starting the queue.
    queue = read(root / "queue.json")["checkpoints"]
    queued_scores = set()
    for index, entry in enumerate(queue):
        directory = checkpoint_directory(root, entry)
        seeds = requested_seeds(root, entry)
        finished = [seed for seed in seeds if
            (measurement_directory(root, entry, seed) / "generation.json").exists()
            and read(measurement_directory(root, entry, seed) / "generation.json")["state"] == "completed"]
        # Archived measurements keep their original code identity. Do not
        # repeat generation merely to resume or redistribute scoring.
        if len(finished) == len(seeds):
            for seed in seeds:
                if not completed_scoring(root, entry, seed):
                    schedule(root, entry, seed)
            continue
        handoff = resolve_checkpoint(entry)
        while handoff is None:
            write_json(root / "progress.json", {"state": "waiting_for_checkpoint", "checkpoint": entry})
            time.sleep(30)
            handoff = resolve_checkpoint(entry)
        if (directory / "checkpoint.json").exists() and read(directory / "checkpoint.json") != handoff:
            raise ValueError("checkpoint changed after admission")
        write_json(directory / "checkpoint.json", handoff)
        for seed in requested_seeds(root, entry):
            # A repaired observer may use a newer snapshot for unfinished
            # seeds. Completed generations retain their original identity.
            if seed not in finished:
                prepare_measurement(root, entry, seed, handoff)
        submit_checkpoint(root, index, cwd=cwd)
        while True:
            for seed in requested_seeds(root, entry):
                marker = measurement_directory(root, entry, seed) / "generation.json"
                key = (index, seed)
                if marker.exists() and read(marker)["state"] == "completed" and key not in queued_scores:
                    schedule(root, entry, seed)
                    queued_scores.add(key)
            status = status_of(control_directory(root, entry))
            write_json(root / "progress.json", {"state": "generating", "checkpoint": entry,
                "position": index + 1, "checkpoints": len(queue), "control_state": status["state"],
                "scoring_passes_queued": len(queued_scores)})
            if status["state"] == "completed":
                if any((index, seed) not in queued_scores for seed in requested_seeds(root, entry)):
                    raise RuntimeError("checkpoint exited without all seed archives")
                break
            if status["state"] in {"failed", "timed-out", "launch-error"}:
                write_json(root / "progress.json", {"state": "paused_for_recovery", "checkpoint": entry,
                    "reason": "Old-owner fencing and interrupted-case reconciliation required; no automatic resampling."})
                return 1
            time.sleep(20)
    return 0


def run_stages(roots, *, repository, cwd=None, progress_directory=None):
    """One GPU queue and one independent, bounded CPU queue across stage roots.

    Recovery stages retain their original identities and launch receipts. Only
    generation failure blocks the next stage; scores drain after all generation
    has been dispatched. One scoring pass at a time preserves its CPU budget.
    """
    from behave.task_execution.run_store import RunStore

    roots = [Path(root).resolve() for root in roots]
    if len(set(roots)) != len(roots):
        raise ValueError("duplicate stage roots")
    # Lock every stage before observing/submitting work. A foreground restart
    # and a legacy detached coordinator cannot both own the same queue. Locks
    # do not prove that remote workers from an interrupted owner have stopped.
    with ExitStack() as owners:
        for root in sorted(roots):
            owners.enter_context(RunStore(root / "owner").owner())
        return _run_stages(roots, repository=repository, cwd=cwd, progress_directory=progress_directory)


def _run_stages(roots, *, repository, cwd=None, progress_directory=None):
    resource_keys = ("allocation", "scoring_cpus", "scoring_workers", "scoring_cpu_ids",
                     "allocated_cpus", "allocated_cpu_ids")
    resources = []
    for root in roots:
        declared = read(root / "configuration.json")["resources"]
        resources.append({**declared, "allocated_cpus": node_capacity(declared)[0]})
    if any(any(item.get(key) != resources[0].get(key) for key in resource_keys) for item in resources):
        raise ValueError("stages must share one allocation and scoring resource budget")
    progress = None if progress_directory is None else Path(progress_directory) / "progress.json"
    futures = {root: [] for root in roots}
    queued, generated, paused = {}, [], None
    with ThreadPoolExecutor(max_workers=1) as cpu:
        scoring_error = None

        def score_one(root, entry, seed):
            nonlocal scoring_error
            if scoring_error is not None:
                raise RuntimeError("Scoring lane paused after observer failure") from scoring_error
            try:
                return score(root, entry, seed, repository=repository, cwd=cwd)
            except Exception as exc:
                # An observer error does not prove its remote worker stopped.
                # Keep GPUs moving, but do not oversubscribe that CPU owner.
                scoring_error = exc
                raise

        def schedule(root, entry, seed):
            if completed_scoring(root, entry, seed):
                return
            key = scoring_directory(root, entry, seed).resolve()
            if key not in queued:
                queued[key] = cpu.submit(score_one, root, entry, seed)
            future = queued[key]
            if future not in futures[root]:
                futures[root].append(future)

        for root in roots:
            if progress is not None:
                write_json(progress, {"state": "generating", "stage": str(root),
                    "generation_stages_completed": len(generated)})
            if _generate(root, schedule, cwd=cwd):
                paused = root
                break
            generated.append(root)
            write_json(root / "progress.json", {"state": "generation_completed",
                "generation_completed": True, "scoring_passes_queued": len(futures[root])})
        if progress is not None:
            write_json(progress, {"state": "paused_for_recovery" if paused else "scoring",
                "stage": str(paused) if paused else None, "generation_completed": paused is None,
                "generation_stages_completed": len(generated)})
        successful = paused is None
        for root in generated:
            results, errors = [], []
            for future in futures[root]:
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001 -- reconcile failures from an independent scoring owner
                    # A scoring launch/observer failure must not strand the GPU
                    # or turn a missing judgment into a successful evaluation.
                    results.append(False)
                    errors.append(f"{type(exc).__name__}: {exc}")
            settled = all(results)
            write_json(root / "progress.json", {"state": "completed" if settled else "scoring_needs_recovery",
                "generation_completed": True, **({"scoring_errors": errors} if errors else {})})
            successful = successful and settled
    if progress is not None:
        write_json(progress, {"state": "paused_for_recovery" if paused else
            ("completed" if successful else "scoring_needs_recovery"),
            "generation_completed": paused is None, "generation_stages_completed": len(generated),
            "stages": [str(root) for root in roots], "paused_stage": str(paused) if paused else None})
    return 0 if successful else 1


def run(root, *, repository, cwd=None):
    return run_stages([root], repository=repository, cwd=cwd)


def launch(root, *, cwd=None):
    from behave.eval.controller import launch as launch_controller

    return launch_controller(root, "checkpoint", cwd=cwd)


def submit_checkpoint(root, index, *, cwd=None):
    from behave.runtime.jobs import status_of, submit

    root = Path(root)
    config = read(root / "configuration.json")
    entry = read(root / "queue.json")["checkpoints"][index]
    control = control_directory(root, entry)
    if (control / "launch.json").exists():
        return status_of(control)
    resources = config["resources"]
    validate_generation_resources(resources)
    allocated_cpus, allocated_gpus = node_capacity(resources)
    mask = hex(sum(1 << cpu for cpu in resources["generation_cpu_ids"]))
    # Keep the frozen snapshot's established entry. New snapshots delegate to
    # the package; old snapshots must not be upgraded by the current launcher.
    bootstrap = ("import sys;from pathlib import Path;sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
        "from tools.eval_checkpoint_study import node;raise SystemExit(node(Path(sys.argv[1]),int(sys.argv[2])))")
    snapshot = snapshot_directory(root)
    argv = ["srun", f"--jobid={resources['allocation']}", "--overlap", "--exact", "--propagate=NONE",
        "--nodes=1", "--ntasks=1", f"--cpus-per-task={allocated_cpus}", f"--gpus={allocated_gpus}",
        f"--cpu-bind=mask_cpu:{mask}", "--kill-on-bad-exit=1", sys.executable,
        "-I", "-B", "-u", "-c", bootstrap, str(snapshot), str(snapshot / "src"), str(root), str(index)]
    return submit(control, argv, cwd=Path.cwd() if cwd is None else Path(cwd),
        wall_seconds=None, cleanup_seconds=300,
        metadata={"purpose": "checkpoint evaluation, all inference seeds", "allocation": resources["allocation"],
                  "checkpoint": entry, "effective_cpu_affinity": resources["generation_cpu_ids"],
                  "allocated_cpus": allocated_cpus, "allocated_gpus": allocated_gpus})


@contextmanager
def serving_port_lease(resources, *, lock_root=None, ephemeral_range=None):
    """Coordinate our launchers and fail before staging expensive weights.

    Socket checks alone cannot reserve a port through model loading. Keep an
    advisory lease for cooperating launchers, and avoid the host's ephemeral
    range so outgoing connections cannot acquire these ports while we load.
    Unrelated listeners are still caught by the bind check or the server.
    """
    if not resources.get("lease_serving_ports"):
        yield
        return
    ports = [resources[key] + index for key in ("port_first", "nccl_port_first")
             for index in range(resources["engines"])]
    lower, upper = (ephemeral_range if ephemeral_range is not None else
                   map(int, Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()))
    if (len(ports) != len(set(ports)) or any(not 1024 <= p <= 65535 or lower <= p <= upper for p in ports)):
        raise ValueError("serving ports overlap or fall inside the host ephemeral range")
    lock_root = Path(lock_root) if lock_root is not None else Path(f"/tmp/behave-eval-ports-{os.getuid()}")
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if lock_root.is_symlink() or lock_root.stat().st_uid != os.getuid():
        raise ValueError("unsafe serving port lock directory")
    with ExitStack() as locks:
        for port in sorted(ports):
            descriptor = os.open(lock_root / f"{port}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            stream = locks.enter_context(os.fdopen(descriptor, "r+"))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ExitStack() as sockets:
            for port in ports:
                sock = sockets.enter_context(socket.socket())
                # Match server bind semantics: closed TCP connections in
                # TIME_WAIT are not live owners. Active listeners still fail.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", port))
        yield


def node(root, index):
    from behave.core.lifecycle import CleanupState
    from behave.isolation.scratch import ScratchDirectory
    from behave.isolation.startup import startup_environment
    from behave.runtime.jobs import run_child
    from behave.runtime.snapshots import stage

    root = Path(root)
    config = read(root / "configuration.json")
    entry = read(root / "queue.json")["checkpoints"][index]
    validate_generation_resources(config["resources"])
    if config.get("generation_recovery") is not None:
        from behave.eval.generation import validate_recovery_runtime
        validate_recovery_runtime()
    directory = generation_directory(root, entry)
    handoff = read(checkpoint_directory(root, entry) / "checkpoint.json")
    os.environ.update(startup_environment(config.get("isolation_startup_slots")))
    os.sched_setaffinity(0, config["resources"]["generation_cpu_ids"])
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    clean = True
    with serving_port_lease(config["resources"]), ScratchDirectory(prefix="behave-checkpoint-eval-",
            cleanup=lambda: CleanupState.COMPLETE if clean else CleanupState.UNKNOWN,
            parent=Path("/tmp")) as scratch:
        write_json(directory / "node_status.json", {"state": "staging_weights",
            "host": socket.gethostname(), "scratch": str(scratch)})
        stage(snapshot_directory(root), scratch / "code")
        stage_weights(handoff, scratch / "weights")
        for seed in requested_seeds(root, entry):
            if stopped.is_set():
                return 143
            measurement = measurement_directory(root, entry, seed)
            output = measurement / "rollout"
            if (measurement / "generation.json").exists():
                if read(measurement / "generation.json")["state"] != "completed":
                    raise RuntimeError("interrupted measurement requires explicit recovery; no resampling")
                continue
            runtime = {"output": str(output), "source": str(output),
                "original_launch": str(measurement / "serving.json"),
                "serving_binding": str(scratch / "code/infra/model_runtimes/local-serving.json"),
                "scratch_root": str(scratch), "staged_weights": str(scratch / "weights"),
                "cpu_ids": config["resources"]["generation_cpu_ids"],
                "devices": config["resources"]["device_groups"], "cpu_threads": 6,
                "port_first": config["resources"].get("port_first", 32400),
                "nccl_port_first": config["resources"].get("nccl_port_first", 32600),
                "process_limit": None, "headers_seconds": 180,
                "isolation_startup_slots": config.get("isolation_startup_slots"),
                **({"serving_probe_request": config["serving_probe_request"]}
                   if config.get("serving_probe_request") else {}),
                "serving_limits": {"startup_seconds": 1800, "lifetime_seconds": None,
                                   "cleanup_seconds": 300, "log_bytes": None}}
            write_json(output / "runtime_settings.json", runtime)
            write_json(measurement / "generation.json", {"state": "running", "seed": seed})
            write_json(directory / "node_status.json", {"state": "generating", "seed": seed,
                "host": socket.gethostname(), "scratch": str(scratch)})
            clean = False
            bootstrap = ("import sys;from pathlib import Path;"
                "sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
                "from behave.eval.generation import worker;"
                "raise SystemExit(worker(Path(sys.argv[1])))")
            code = run_child([sys.executable, "-I", "-B", "-u", "-c", bootstrap,
                str(scratch / "code"), str(scratch / "code/src"), str(output)],
                measurement / "generation.log", stopped)
            cleanup = output / "cleanup.json"
            clean = cleanup.exists() and read(cleanup).get("cleanup_complete") is True
            done = code == 0 and clean and not stopped.is_set()
            if done:
                report = read(output / "report.json")
                done = report["requested"] == config["inputs"]["tasks"]
                # pending_judgment is normal for generation-only; all-no-result is not.
                done = done and report.get("no_result", 0) != report["requested"]
            write_json(output / "runtime.json", {"state": "archived" if done else "interrupted",
                "host": socket.gethostname(), "local_directory": str(output), "exit_code": code})
            write_json(measurement / "generation.json", {"state": "completed" if done else "interrupted",
                "seed": seed, "exit_code": code, "cleanup_complete": clean})
            if not done:
                return code or 1
        write_json(directory / "node_status.json", {"state": "completed", "host": socket.gethostname()})
    return 0


def checkpoint_ready(job, update):
    from behave.agent.run_store import JournalDirectory

    plan = Path(job) / "artifacts/plan"
    from behave.runtime.models import cold_model
    model = plan / f"hf_{update - 1}"
    if (not (model / "model.safetensors.index.json").exists()
            and not (model / "model.safetensors").is_file() and cold_model(model) is None):
        return False
    # AttemptJournal's ordinary load creates its directory. An export without
    # a commit journal is simply not ready; observation must not create it.
    journal = JournalDirectory(plan / "rounds/native").journal("rounds")
    if not journal.directory.is_dir():
        return False
    # An index can appear while HF export is still being written. Use the
    # existing small commit journal; never reread or hash the weight payloads.
    events = journal.load(verify_artifacts=False)
    return any(event.kind == "checkpoint_saved" and event.payload["round"] == update - 1
               for event in events)


def resolve_checkpoint(entry):
    from behave.runtime.models import periodic_handoff

    if entry.get("recorded_handoff") is not None:
        from behave.core.artifacts import BlobRef
        from behave.runtime.models import model_handoff
        if entry.get("recovery_ready") and not Path(entry["recovery_ready"]).is_file():
            return None
        saved = read(entry["recorded_handoff"])
        if saved["step"] + 1 != entry["update"]:
            raise ValueError("recorded HF handoff belongs to another update")
        current = model_handoff(Path(saved["model_path"]), BlobRef.from_dict(saved["checkpoint"]), materialize=False)
        if any(current[key] != saved[key] for key in current):
            raise ValueError("migrated HF inventory differs from its recorded handoff")
        return saved

    if entry["update"] == 0:
        if not Path(entry["model_path"]).is_dir():
            raise ValueError("base weights are unavailable")
        return {key: entry[key] for key in ("model_path", "model_revision")}
    candidates = [Path(p) for p in entry.get("jobs", [])]
    for parent in entry.get("job_roots", []):
        candidates.extend(p for p in sorted(Path(parent).iterdir()) if p.is_dir())
    # Compatibility paths may name the same resumed job. A path alias is not
    # another checkpoint owner; distinct real owners must still be rejected.
    ready = [p for p in dict.fromkeys(p.resolve() for p in candidates)
             if checkpoint_ready(p, entry["update"])]
    if not ready:
        return None
    if len(ready) != 1:
        raise ValueError(f"ambiguous checkpoint ownership: {entry}: {ready}")
    return periodic_handoff(ready[0], entry["update"], materialize=False)


def stage_weights(handoff, destination):
    """Stage on the compute node, preserving the recorded model identity."""
    from behave.core.artifacts import BlobRef
    from behave.runtime.models import model_handoff, restore_model
    from behave.serving.model_files import inspect_model_files

    destination = restore_model(Path(handoff['model_path']), destination=destination)
    inspect_model_files(destination)
    if 'weights' in handoff:
        local = model_handoff(destination, BlobRef.from_dict(handoff['weights']['checkpoint']))
        if any(local[key] != handoff[key] for key in ('weights', 'model_revision')):
            raise ValueError('staged HF differs from the recorded checkpoint')
    return destination
