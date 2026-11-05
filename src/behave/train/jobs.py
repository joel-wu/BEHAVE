"""Node-side execution of a frozen training job.

Submission and scheduler policy stay outside this module. Installation, plan
preparation and native training use the existing shared runtime implementations.
The caller supplies the staged repository for split membership and EDA paths;
installed package locations and the current working directory are not inputs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from threading import Event

from behave.core import runtime_defaults as defaults
from behave.runtime.records import write_json

from .plan import check_training_selection


def staged(output: str) -> int:
    from behave.core.process_limits import configure_worker_process_limit
    from behave.core.resources import allocated_cpus
    from behave.runtime.jobs import node_heartbeat

    output = Path(output)
    configuration = json.loads((output / "configuration.json").read_text())
    selection = json.loads((output / "selection.json").read_text())
    limits = configure_worker_process_limit(configuration.get("process_limit"))
    os.sched_setaffinity(0, allocated_cpus(selection["cpus"]))
    with node_heartbeat(output / "node_status.json",
                        interval=configuration.get("heartbeat_seconds", defaults.JOB_HEARTBEAT_SECONDS)):
        return _staged(output, configuration, limits)


def _staged(output: Path, configuration: dict, limits: dict) -> int:
    from behave.runtime.snapshots import install_runtime, stage

    local = Path(mkdtemp(prefix="behave-training-", dir="/tmp"))
    local.chmod(0o700)
    began = time.monotonic()
    state = {"state": "preparing", "host": socket.gethostname(), "local_directory": str(local),
             "started_at": datetime.now(UTC).isoformat(), "process_limit": limits}
    write_json(output / "runtime.json", state)
    code = local / "checkout"
    installation = output / "artifacts/installation"
    try:
        stage(output / "snapshot/checkout", code)
        installation.mkdir(parents=True)
        installed = install_runtime(code, installation, configuration["native_python"])
    except Exception as error:
        state.update(state="interrupted", error=f"{type(error).__name__}: {error}",
                     elapsed_seconds=time.monotonic() - began,
                     finished_at=datetime.now(UTC).isoformat())
        write_json(output / "runtime.json", state)
        raise
    from behave.train.slime_launch import SlimeLayout

    installed["evidence"]["gpus"] = SlimeLayout(**configuration["layout"]).required_gpus
    installed["evidence"]["preparation_seconds"] = time.monotonic() - began
    installed["evidence"]["process_limit"] = limits
    write_json(installation / "installed.json", installed["evidence"])
    env = installed["environment"]
    bootstrap = ("import sys; from pathlib import Path; "
                 "from behave.train.jobs import execute; "
                 "raise SystemExit(execute(*sys.argv[2:],repository=Path(sys.argv[1])))")
    return subprocess.run((installed["evidence"]["control_python"], "-I", "-B", "-u", "-c", bootstrap,
        str(code), str(output), str(local)), cwd=local, env=env, check=False).returncode


def execute(output: str, local: str, *, repository: Path) -> int:
    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef
    from behave.core.lifecycle import CleanupState
    from behave.core.storage import DirectoryStore
    from behave.isolation.service import run_service
    from behave.runtime.models import release_model, stage_model
    from behave.train.plan import prepare_from_release as prepare
    from behave.train.slime_checkpoint import iteration, scored_rounds, training_steps
    from behave.train.slime_launch import environment, megatron_source
    from behave.train.slime_stage import run_learning

    output, local, repository = Path(output), Path(local), Path(repository).resolve()
    configuration = json.loads((output / "configuration.json").read_text())
    selection = json.loads((output / "selection.json").read_text())
    installed = json.loads((output / "artifacts/installation/installed.json").read_text())
    artifacts = output / "artifacts"
    artifacts.mkdir(exist_ok=True)
    from behave.train.checkpoint_storage import job_checkpoint_directory
    plan, checkpoints = artifacts / "plan", job_checkpoint_directory(output)
    native_prefix = Path(configuration["native_python"]).resolve().parents[1]
    configuration["native_python"] = installed["native_python"]
    if configuration.get("phase_timing", False):
        from behave.train.slime_hooks import HOOK_ARGUMENTS

        configuration["native_arguments"] = [*configuration["native_arguments"], *HOOK_ARGUMENTS]
    cleanup_seconds = configuration.get("cleanup_seconds")
    cache = Path(mkdtemp(prefix="bt-", dir="/tmp"))
    cache.chmod(0o700)
    state = {"state": "preparing", "host": socket.gethostname(), "local_directory": str(local),
             "artifacts": str(artifacts), "plan_directory": str(plan),
             "checkpoint_directory": str(checkpoints), "source_model": configuration["model_path"],
             "native_cache": str(cache), "phases": [], "timeline": [],
             "local_scratch_removed": False, "retained_scratch": [str(local), str(cache)],
             "preparation_seconds": installed.get("preparation_seconds")}
    began = time.monotonic()
    stopped = Event()
    continuation = (json.loads((output / "continuation.json").read_text())
                    if (output / "continuation.json").exists() else None)
    first_update = 0 if continuation is None else continuation["first_update"]
    previous = {sig: signal.signal(sig, lambda *_: stopped.set())
                for sig in (signal.SIGINT, signal.SIGTERM)}

    def publish(**changes):
        state.update(changes)
        state["updated_at"] = datetime.now(UTC).isoformat()
        state["elapsed_seconds"] = time.monotonic() - began
        state["timeline"].append({key: state.get(key) for key in
                                  ("state", "phase", "updated_at", "elapsed_seconds")})
        write_json(output / "runtime.json", state)

    try:
        publish(phase="stage_weights")
        staging_receipt = artifacts / "staged_model.json"
        stage_model(configuration["model_path"], local / "weights", receipt=staging_receipt)
        configuration["model_path"] = str(local / "weights")
        publish(phase="prepare_plan")
        parent = {} if continuation is None or continuation["checkpoint"] is None else {
            "parent_reader": DirectoryStore(continuation["objects"]),
            "parent_checkpoint": BlobRef.from_dict(continuation["checkpoint"])}
        if continuation is not None and "rollout_recovery" in continuation:
            configuration["rollout_recovery"] = continuation["rollout_recovery"]
        prepare(plan, configuration, Path(selection["release"]), selection["tasks"], output / "train.txt",
                repository=repository, **parent)
        from behave.train.checkpoint_storage import prepare_hf_storage
        prepare_hf_storage(output, plan, configuration["initial_updates"] + configuration["resume_updates"])
        publish(first_update=first_update)
        env = environment(native_prefix, cache, cuda_visible_devices=os.environ["CUDA_VISIBLE_DEVICES"],
                          cpu_threads=configuration.get("cpu_threads", defaults.TRAIN_CPU_THREADS),
                          build_jobs=configuration.get("build_jobs", defaults.TRAIN_BUILD_JOBS),
                          kernel_cache=configuration.get("kernel_cache"),
                          megatron_source=megatron_source(configuration))
        env.update(TORCHDYNAMO_DISABLE="1", NCCL_SOCKET_IFNAME="lo", GLOO_SOCKET_IFNAME="lo")
        if configuration.get("phase_timing", False):
            env["BEHAVE_TRAIN_TIMING_DIRECTORY"] = str(artifacts / "timing")
            env["BEHAVE_TRAIN_TIMING_MICROBATCHES"] = str(configuration.get("timing_microbatches", 0))
        env["BEHAVE_EDA_ROOT"] = str(repository / "external/oss-cad-suite")
        for name in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON", "BEHAVE_WORK_DIR", "BEHAVE_RUNTIME_LOCATIONS"):
            if name in os.environ:
                env[name] = os.environ[name]
        first = configuration["initial_updates"]
        final = first + configuration["resume_updates"]
        phases = [("initial", local / "weights" if continuation is None or continuation["load"] is None
                   else Path(continuation["load"]), first)]
        if configuration["resume_updates"]:
            phases.append(("resume", checkpoints, final))
        metrics = {}
        for phase, load, count in phases:
            publish(state="training", phase=phase)
            stage = JournalDirectory(plan / "learning" / phase)
            report = run_learning(plan=plan, run=stage, updates=count, load=load, save=checkpoints,
                cpus=selection["cpus"], environment=env, seconds=configuration.get("learning_seconds"),
                cancelled=stopped.is_set, cleanup_seconds=cleanup_seconds)
            head, _ = stage.seal("learning")
            receipt = {"phase": phase, "head": head.as_dict(), "report": report}
            state["phases"].append(receipt)
            write_json(plan / f"{phase}.result.json", receipt)
            publish()
            if report["status"] != "completed":
                raise RuntimeError(f"native {phase} did not finish cleanly; evidence is in {plan}")
            text = (stage.directory / "stdout").read_text() + (stage.directory / "stderr").read_text()
            metrics[phase] = training_steps(text, require_nonzero=False)
            write_json(plan / "training_metrics.json", metrics)
        rounds = scored_rounds(plan)
        if [row["round"] for row in rounds] != list(range(first_update, final)) or iteration(checkpoints) != final - 1:
            raise RuntimeError("completed rounds/checkpoint do not match the requested training continuation")
        weight_check = ("import json,sys; from pathlib import Path; "
            "from behave.train.slime_checkpoint import changed_decoder_weight; "
            "paths=list(map(Path,sys.argv[1:])); "
            "print(json.dumps([changed_decoder_weight(a,b,require_change=False) for a,b in zip(paths,paths[1:])]))")
        publish(state="checking_weights", phase=None)
        result = run_service((installed["native_python"], "-B", "-c", weight_check,
            str(local / "weights") if continuation is None else continuation["hf_parent"],
            *(str(plan / f"hf_{count-1}") for _, _, count in phases)),
            cwd=plan, environment=env, network="none", deadline=None, cancelled=stopped.is_set,
            output_bytes=None, cleanup_seconds=cleanup_seconds)
        if result.status != "exited" or result.returncode != 0 or not result.cleanup_state.quiescent:
            write_json(plan / "weight_check_error.json", {"status": result.status,
                "stdout": result.stdout.decode(errors="replace"), "stderr": result.stderr.decode(errors="replace")})
            raise RuntimeError("numerical weight update check failed")
        changes = json.loads(result.stdout)
        gradients = {phase: any(row["train/grad_norm"] > 0 for row in rows)
                     for phase, rows in metrics.items()}
        write_json(plan / "result.json", {"native_cycle_completed": True,
            "native_updates_verified": all(gradients.values()) and all(changes),
            "native_update_and_cold_resume": (continuation is not None or len(phases) > 1)
                                            and all(gradients.values()) and all(changes),
            "full_128k_capacity_verified": False, "rounds": rounds, "training_metrics": metrics,
            "nonzero_gradients": gradients, "weight_changes": changes})
        # Rollouts, logs and every saved checkpoint already live on shared
        # storage. The final alias stays inside that same durable directory.
        (artifacts / "final_hf").symlink_to(Path("plan") / f"hf_{final-1}", target_is_directory=True)
        # Only release the verified model copy after all native consumers and
        # the final weight check are quiescent. Keep installation/kernel caches
        # and uncertain data; neither success nor a path prefix permits rmtree.
        cleanup = CleanupState.combine(result.cleanup_state, *(
            CleanupState(row["report"]["process"]["cleanup"]) for row in state["phases"]))
        try:
            release_model(local / "weights", receipt=staging_receipt, cleanup=cleanup)
            scratch_release = {"state": "released", "receipt": str(staging_receipt),
                               "paths": [str(local / "weights")]}
        except Exception as error:  # noqa: BLE001 -- report cleanup failure without invalidating committed training.
            # Cleanup failure does not undo a completed, durably recorded update.
            scratch_release = {"state": "retained", "receipt": str(staging_receipt),
                               "error": f"{type(error).__name__}: {error}"}
        publish(state="completed", phase=None, scratch_release=scratch_release)
        return 0
    except Exception as error:
        publish(state="interrupted", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def retention_preflight(config):
    """Reject an unsupported retention policy before allocating work."""
    policy = config.get("checkpoint_retention")
    if policy is None:
        return {"mode": "disabled"}
    from behave.train.checkpoint_storage import retention_policy

    retention_policy(policy)
    if policy.get("mode", "archive") != "delete":
        raise ValueError("checkpoint retention requires mode 'delete'; archive retention is no longer supported")
    return {"policy": policy}


def review_snapshot(previous, snapshot, review):
    """Record exactly what was inherited and replaced, not just a version name."""
    current = json.loads((snapshot.parent / "snapshot.json").read_text())
    prior = (json.loads((previous / "snapshot/snapshot.json").read_text())
             if previous is not None else None)
    before = {} if prior is None else prior["files"]
    return {"schema": "behave.training-runtime-review/1", **review,
            "parent_snapshot_sha256": None if prior is None else prior["sha256"],
            "snapshot_sha256": current["sha256"],
            "changed_files": {name: {"before": before.get(name), "after": value}
                              for name, value in current["files"].items() if before.get(name) != value}}


def checkpoint_event(job, completed_updates):
    journal = Path(job) / "artifacts/plan/rounds/native/rounds"
    for path in sorted(journal.glob("*.json")):
        row = json.loads(path.read_text())
        if row["kind"] == "checkpoint_saved" and row["payload"]["round"] == completed_updates - 1:
            return path
    return None


def verify_checkpoint(job, completed_updates):
    """Require the exact committed boundary, including its actual state files."""
    from behave.train.slime_checkpoint import CheckpointFile, checkpoint_inventory, iteration

    event = checkpoint_event(job, completed_updates)
    if event is None:
        raise ValueError("boundary checkpoint is not committed")
    payload = json.loads(event.read_text())["payload"]
    from behave.train.checkpoint_storage import resolve_checkpoint

    root = resolve_checkpoint(payload["native_directory"], step=completed_updates - 1,
                              files=tuple(CheckpointFile.from_dict(row) for row in payload["files"]))
    if iteration(root) != completed_updates - 1:
        raise ValueError("native checkpoint already moved beyond the requested boundary")
    actual = checkpoint_inventory(root, completed_updates - 1)
    if actual != tuple(CheckpointFile.from_dict(row) for row in payload["files"]):
        raise ValueError("boundary checkpoint inventory differs from its commit")
    return event


def cleanup_confirmed(job, stopped):
    """A stopped wrapper alone is insufficient: require owned cleanup evidence."""
    try:
        node = json.loads((job / "node_status.json").read_text())
        runtime = json.loads((job / "runtime.json").read_text())
        result = json.loads((job / "artifacts/plan/initial.result.json").read_text())
        return (node["state"] == "stopped"
                and all(node[key] == stopped["wrapper"][key] for key in ("pid", "host", "started_at"))
                and runtime["host"] == node["host"] and runtime["state"] == "interrupted"
                and result["report"]["process"]["cleanup"] == "complete"
                and datetime.fromisoformat(node["finished_at"]) >= datetime.fromisoformat(stopped["requested_at"]))
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return False
    except OSError:
        return None  # Temporary shared-storage failure is not a cleanup verdict.


def storage_observation(read, poll_seconds):
    while True:
        try:
            return read()
        except OSError as error:
            print(f"Boundary storage observation failed; still waiting: {error}", flush=True)
            time.sleep(poll_seconds)


def require_live_allocation(job, allocation):
    """A requeued allocation does not resurrect its former training step."""
    node = json.loads((job / "node_status.json").read_text())
    response = subprocess.run(["scontrol", "show", "job", str(allocation), "-o"],
                              check=True, capture_output=True, text=True, timeout=15,
                              env={**os.environ, "TZ": "UTC"})
    row = dict(re.findall(r"\b(JobId|JobState|StartTime|NodeList)=([^\s]+)", response.stdout))
    if (row.get("JobId") != str(allocation) or row.get("JobState") != "RUNNING"
            or row.get("NodeList") != node["host"]):
        raise RuntimeError(f"parent allocation is no longer running on its recorded node: {row}")
    allocated_at = datetime.fromisoformat(row["StartTime"]).replace(tzinfo=UTC)
    if allocated_at > datetime.fromisoformat(node["started_at"]):
        raise RuntimeError("allocation restarted after the recorded trainer; explicit recovery required")


def wait_and_pause(job, allocation, completed_updates, *, poll_seconds, stop_script: Path,
                   before_stop=None):
    """Shared entry's opt-in checkpoint boundary, not a second training runner."""
    if completed_updates < 1 or poll_seconds <= 0:
        raise ValueError("checkpoint boundary and poll interval must be positive")
    stop_script = Path(stop_script).resolve()
    if not stop_script.is_file():
        raise FileNotFoundError(f"owned stop helper is missing: {stop_script}")
    job = Path(job).resolve()
    reason = f"checkpoint maintenance after {completed_updates} updates"
    print(json.dumps({"state": "waiting_for_checkpoint", "job": str(job),
                      "completed_updates": completed_updates}), flush=True)
    def node_status():
        return storage_observation(lambda: json.loads((job / "node_status.json").read_text()), poll_seconds)

    require_live_allocation(job, allocation)
    while storage_observation(lambda: checkpoint_event(job, completed_updates), poll_seconds) is None:
        require_live_allocation(job, allocation)
        node = node_status()
        if node["state"] == "stopped":
            raise RuntimeError("parent stopped before the approved checkpoint boundary")
        time.sleep(poll_seconds)
    event = storage_observation(lambda: verify_checkpoint(job, completed_updates), poll_seconds)
    require_live_allocation(job, allocation)
    stop_record = job / "operator_stop.json"
    # Validate the prepared successor before disrupting a healthy parent.
    # Callers pin its sources before waiting rather than requiring the live
    # checkout to remain unchanged after the parent has already stopped.
    if before_stop is not None:
        before_stop()
    if not stop_record.exists():
        node = node_status()
        # A compute-owned watcher may inherit a high-core mask. Bind this
        # one-CPU step within its own allocation, not the watcher's mask.
        command = ["srun", f"--jobid={allocation}", "--overlap", "--exact", "--propagate=NONE",
                   "-N1", "-n1", "--cpus-per-task=1", "--cpu-bind=threads", "--gres=none", "/usr/bin/prlimit",
                   "--nproc=65536:65536", "/usr/bin/python3", "-B",
                   str(stop_script), str(job),
                   "--wrapper-pid", str(node["pid"]), "--started-at", node["started_at"], "--reason", reason]
        subprocess.run(command, check=True)
    stopped = storage_observation(lambda: json.loads(stop_record.read_text()), poll_seconds)
    if stopped["reason"] != reason:
        raise RuntimeError("parent has an unrelated stop request; manual review required")
    print(json.dumps({"state": "waiting_for_owned_cleanup", "event": str(event)}), flush=True)
    while (ready := cleanup_confirmed(job, stopped)) is not True:
        require_live_allocation(job, allocation)
        node = node_status()
        if ready is False and node["state"] == "stopped":
            raise RuntimeError("parent stopped without complete cleanup evidence; refusing continuation")
        time.sleep(poll_seconds)
    require_live_allocation(job, allocation)
    storage_observation(lambda: verify_checkpoint(job, completed_updates), poll_seconds)
    print(json.dumps({"state": "boundary_ready", "completed_updates": completed_updates,
                      "cleanup_confirmed": True}), flush=True)


def hf_comparison_parent(previous: Path, step: int) -> Path:
    """Find the saved HF baseline for the final numerical report, not resume.

    Native saves can be newer than the last periodic HF export. Training still
    restores the exact native checkpoint; this path is only a weight-change
    comparison baseline.
    """
    exports = {int(path.name[3:]): path for path in (previous / "artifacts/plan").glob("hf_*")
               if path.is_dir() and path.name[3:].isdigit() and int(path.name[3:]) <= step}
    if exports:
        return exports[max(exports)]
    continuation = previous / "continuation.json"
    if continuation.is_file():
        from behave.core.runtime_locations import runtime_path
        return runtime_path(json.loads(continuation.read_text())["hf_parent"])
    return Path(json.loads((previous / "configuration.json").read_text())["model_path"])


def backup_task_pool(previous: Path, prefix, output: Path, destination, *, native_reader=None):
    """Keep the checkpoint's small pool state, including legacy task definitions."""
    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef
    from behave.core.storage import DirectoryStore
    from behave.train.pool import definition_refs, read_pool

    native = JournalDirectory(previous / "artifacts/plan/rounds/native")
    native_reader = native.objects if native_reader is None else native_reader
    pool = read_pool(native_reader, prefix)
    if pool is not None:
        # The immutable per-update history stays in the old NFS run. This copy
        # is independent of weight retention and is also the resume input.
        prior_inputs = DirectoryStore(previous / "artifacts/plan/inputs")
        for row in pool["tasks"].values():
            revision = BlobRef.from_dict(row["revision"])
            # New Syn tasks are in the native closure, not the original seed
            # plan. Legacy authored runs may still need that original store.
            reader = native_reader if (native_reader.directory / revision.sha256).is_file() else prior_inputs
            destination.publish(reader, definition_refs(reader, (revision,)))
        write_json(output / "task_pool_parent.json", pool)
    return pool


def native_feedback_owner(job: Path, events):
    """Resolve transparent pre-update resumes to the authenticated journal owner."""
    from behave.core.artifacts import BlobRef
    from behave.core.records import canonical_json, thaw_json
    from behave.core.runtime_locations import runtime_path
    from behave.train.slime_rollout import read_binding
    from behave.train.trainer import active_events

    active = active_events(events)
    expected = thaw_json(active[0].payload['binding'])
    head = BlobRef.of(events[-1].to_bytes()).as_dict()
    seen = set()
    while True:
        job = runtime_path(job)
        if job in seen:
            raise ValueError('inherited feedback job ancestry repeats')
        seen.add(job)
        path = job/'artifacts/plan/binding.json'
        binding = json.loads(path.read_text())
        if (BlobRef.of(canonical_json(binding)).as_dict() == expected
                or BlobRef.of(canonical_json(read_binding(path))).as_dict() == expected):
            return job
        continuation = json.loads((job/'continuation.json').read_text())
        if (binding.get('parent_checkpoint') != head or continuation.get('checkpoint') != head
                or continuation.get('first_update') != active[-1].payload['round'] + 1):
            raise ValueError('inherited feedback binding identity differs')
        job = Path(continuation['job'])


def native_resume_prefix(previous: Path):
    """Read the last committed native prefix, including a pre-update restart.

    Acquisition may run after restoring a checkpoint but before the first new
    optimizer update. Such a job still has an authenticated native parent; it
    must not fall back to HF or manufacture a local checkpoint event.
    """
    from behave.agent.journal import load_chain
    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef
    from behave.core.records import thaw_json
    from behave.core.storage import DirectoryStore
    from behave.train.trainer import NativeAnchor

    native = JournalDirectory(previous / "artifacts/plan/rounds/native")
    events = native.journal("rounds").load(verify_artifacts=False)
    saved = next((event for event in reversed(events) if event.kind == "checkpoint_saved"), None)
    if saved is not None:
        return {"events": events[:saved.sequence + 1], "reader": native.objects,
                "completed": saved.payload["round"] + 1,
                "load": thaw_json(saved.payload)["native_directory"], "feedback_job": previous}
    continuation_path = previous / "continuation.json"
    binding_path = previous / "artifacts/plan/binding.json"
    if not continuation_path.is_file() or not binding_path.is_file():
        raise ValueError("resume requires a committed full checkpoint")
    continuation = json.loads(continuation_path.read_text())
    binding = json.loads(binding_path.read_text())
    checkpoint = continuation.get("checkpoint")
    if checkpoint is None or binding.get("parent_checkpoint") != checkpoint:
        raise ValueError("inherited native checkpoint differs from the prepared binding")
    reader = DirectoryStore(continuation["objects"])
    head = BlobRef.from_dict(checkpoint)
    anchor = NativeAnchor.load(reader, head)
    if anchor.route != "task" or continuation.get("first_update") != anchor.step + 1:
        raise ValueError("inherited native checkpoint update or route differs")
    if not continuation.get("load"):
        raise ValueError("inherited native checkpoint has no restore directory")
    prefix = load_chain(reader, head, verify_artifacts=False)
    return {"events": prefix, "reader": reader,
            "completed": anchor.step + 1, "load": continuation["load"],
            "feedback_job": native_feedback_owner(Path(continuation["job"]), prefix)}


def node_completed(job: Path) -> bool:
    """Authenticate a clean node-side finish if its login supervisor was lost.

    Never infer completion from a stale heartbeat, saved weights, or idle GPUs.
    The node wrapper stops only after its child and owned cleanup have returned.
    Original control records stay untouched; submit still verifies the native
    checkpoint and carries its optimizer, RNG and pool state.
    """
    try:
        runtime = json.loads((job / "runtime.json").read_text())
        node = json.loads((job / "node_status.json").read_text())
        result = json.loads((job / "artifacts/plan/result.json").read_text())
        config = json.loads((job / "configuration.json").read_text())
        from behave.train.checkpoint_storage import job_checkpoint_directory

        saved = int((job_checkpoint_directory(job) / "latest_checkpointed_iteration.txt").read_text())
        target = config["initial_updates"] + config["resume_updates"]
        return (runtime["state"] == "completed" and node["state"] == "stopped"
                and node["host"] == runtime["host"]
                and datetime.fromisoformat(node["finished_at"]) >= datetime.fromisoformat(runtime["updated_at"])
                and result.get("native_cycle_completed") is True and saved == target - 1
                and [row["round"] for row in result["rounds"]] == list(range(runtime["first_update"], target)))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def wait_for_completion(job: Path, *, poll_seconds: float) -> None:
    """Wait for an owned job to release its resources before cold continuation."""
    print(f"Waiting for completed training job: {job}", flush=True)
    while True:
        try:
            status = json.loads((job / "control/status.json").read_text())
        except (OSError, json.JSONDecodeError) as error:
            # A shared-storage observation failure is not a terminal parent
            # result and must not authorize a new training job.
            print(f"Parent status unavailable; still waiting: {error}", flush=True)
        else:
            if "finished_at" in status:
                if status["state"] != "completed" or status.get("returncode") != 0:
                    raise RuntimeError(f"parent training did not complete: {status}")
                return
        if node_completed(job):
            print("Parent completed and stopped on-node; preserving stale login control records.", flush=True)
            return
        time.sleep(poll_seconds)


def job_configuration(configuration: Path, *, updates: int | None = None,
                      training_seed: int | None = None, workers: int | None = None,
                      rollout_gpus_per_engine: int | None = None,
                      rollout_excluded_gpus: tuple[int, ...] | None = None,
                      actor_gpus_per_node: int | None = None,
                      context_parallel: int | None = None,
                      machine_profile: Path | None = None) -> dict:
    """Resolve ordinary-run overrides without rewriting the shared profile."""
    from behave.train.plan import load_configuration, resolve_training_seed

    config = load_configuration(configuration, machine_profile=machine_profile)
    if updates is not None:
        if isinstance(updates, bool) or not isinstance(updates, int) or updates <= 0:
            raise ValueError("updates must be a positive absolute update target")
        # A production run does not implicitly insert the optional cold-resume
        # acceptance phase. Old two-phase configurations remain supported.
        config.update(initial_updates=updates, resume_updates=0)
    if training_seed is not None:
        config["training_seed"] = training_seed
    if workers is not None:
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive integer")
        config["workers"] = workers
    if rollout_gpus_per_engine is not None:
        config["layout"]["rollout_gpus_per_engine"] = rollout_gpus_per_engine
    if rollout_excluded_gpus is not None:
        config["layout"]["rollout_excluded_gpus"] = list(rollout_excluded_gpus)
    for name, value in (("actor_gpus_per_node", actor_gpus_per_node), ("context_parallel", context_parallel)):
        if value is not None:
            config["layout"][name] = value
    from behave.train.slime_launch import SlimeLayout

    SlimeLayout(**config["layout"])
    return resolve_training_seed(config)


def submit(output: Path, allocation: int, configuration: Path, release: Path,
           tasks: list[str], split: Path, *, repository: Path, cpus: int, resume_from: Path | None = None,
           updates: int | None = None,
           training_seed: int | None = None, resume_rollouts: bool = False,
           workers_stopped: bool = False, workers: int | None = None,
           rollout_gpus_per_engine: int | None = None,
           rollout_excluded_gpus: tuple[int, ...] | None = None,
           actor_gpus_per_node: int | None = None,
           context_parallel: int | None = None,
           checkpoint_directory: Path | None = None,
           machine_profile: Path | None = None) -> dict:
    from behave.runtime.jobs import status_of
    from behave.runtime.jobs import submit as detached
    from behave.runtime.snapshots import freeze

    output = output.resolve()
    if (output / "control/launch.json").exists():
        return status_of(output / "control")
    repository = Path(repository).resolve()
    check_training_selection(tasks, split, repository=repository)
    from behave.train.checkpoint_storage import install_storage, profile_pin_overrides, storage_destination

    destination = storage_destination(repository, output, checkpoint_directory)
    configuration = configuration.resolve()
    config = job_configuration(configuration, updates=updates, training_seed=training_seed,
        workers=workers, rollout_gpus_per_engine=rollout_gpus_per_engine,
        rollout_excluded_gpus=rollout_excluded_gpus, actor_gpus_per_node=actor_gpus_per_node,
        context_parallel=context_parallel, machine_profile=machine_profile)
    from behave.train.slime_launch import SlimeLayout

    layout = SlimeLayout(**config["layout"])
    handoff = None
    if resume_rollouts:
        from behave.train.slime_rollout import inspect_handoff

        if resume_from is None:
            raise ValueError("--resume-rollouts requires --resume-from")
        handoff = inspect_handoff(resume_from, config, release, tasks, workers_stopped=workers_stopped)
    elif workers_stopped:
        raise ValueError("--workers-stopped is only used with --resume-rollouts")
    source_overrides = {}
    previous, completed, resume_prefix = None, 0, None
    startup_handoff = None
    if resume_from is not None:
        from behave.core.artifacts import BlobRef
        from behave.core.storage import DirectoryStore
        from behave.train.trainer import NativeAnchor

        previous = resume_from.resolve()
        if handoff is None:
            resume_prefix = native_resume_prefix(previous)
            completed = resume_prefix["completed"]
            from behave.train.pool import inherited_handoff

            startup_handoff = inherited_handoff(previous, config, completed=completed)
        else:
            completed = handoff["step"] + 1
        if updates is not None and updates <= completed:
            raise ValueError("updates must exceed the parent's completed update count")

    from behave.train.jobs import retention_preflight, review_snapshot

    review = {"upgrade": None}
    if startup_handoff is not None:
        review["inherited_startup_handoff"] = startup_handoff
    from behave.core.runtime_locations import profile_identity
    runtime_locations = profile_identity()
    if runtime_locations is not None:
        review["runtime_locations"] = runtime_locations
    if handoff is not None:
        review["rollout_recovery"] = {"source": str(previous), "round": handoff["step"] + 1,
            "completed_candidates": len(handoff["recovery"].indices),
            "policy": handoff["policy_ref"].as_dict()}
    review["retention"] = retention_preflight(config)
    output.mkdir(parents=True, exist_ok=False)
    if destination is not None:
        install_storage(output, destination)
    write_json(output / "configuration.json", config)
    shutil.copyfile(split, output / "train.txt")
    write_json(output / "selection.json", {"tasks": tasks, "release": str(release.resolve()), "cpus": cpus})
    snapshot = output / "snapshot/checkout"
    if resume_from is None:
        freeze(repository, snapshot, extra_files=("tools/slime_training_job.py",))
    else:
        # A paused stage has not sealed its journal. Export only the committed
        # checkpoint prefix and its small receipts into the new job, leaving
        # the old journal and its unfinished next update untouched.
        parent_objects = DirectoryStore(output / "parent_objects")
        if handoff is None:
            prefix = resume_prefix["events"]
            parent_reader = resume_prefix["reader"]
        else:
            from behave.agent.journal import load_chain

            parent_reader = handoff["parent_reader"]
            prefix = handoff["parent_events"] or (() if handoff["parent"] is None else load_chain(
                parent_reader, handoff["parent"], verify_artifacts=False))
        parent_objects.publish_owned(parent_reader, tuple(ref for event in prefix for ref in event.artifacts))
        for event in prefix:
            parent_objects.put(event.to_bytes())
        if prefix and config.get('self_improvement') is not None:
            from behave.train.slime_acquisition import retain_legacy_feedback_bindings
            feedback_job = previous if handoff is not None else resume_prefix["feedback_job"]
            if handoff is not None and handoff["saved"] is None and handoff["parent"] is not None:
                inherited = json.loads((previous / "continuation.json").read_text())
                if inherited["checkpoint"] != handoff["parent"].as_dict():
                    raise ValueError("inherited feedback parent differs from native checkpoint")
                feedback_job = native_feedback_owner(Path(inherited["job"]), prefix)
            retain_legacy_feedback_bindings(feedback_job, prefix, parent_objects)
        if prefix:
            backup_task_pool(previous, prefix, output, parent_objects, native_reader=parent_reader)
        head = BlobRef.of(prefix[-1].to_bytes()) if prefix else None
        parent = None if head is None else NativeAnchor.load(parent_objects, head)
        if destination is not None:
            source_overrides.update(profile_pin_overrides(repository))
        freeze(previous / "snapshot/checkout", snapshot,
               extra_files=("tools/slime_training_job.py",), overrides=source_overrides)
        from behave.train.checkpoint_storage import resolve_checkpoint

        load = resume_prefix["load"] if handoff is None else handoff["load"]
        if load is not None:
            load = str(resolve_checkpoint(load, step=None if parent is None else parent.step,
                                          files=None if parent is None else parent.files,
                                          checkpoint=None if parent is None else parent.head))
        continuation = {"job": str(previous), "checkpoint": None if head is None else head.as_dict(),
            "objects": str(parent_objects.directory),
            "load": load,
            "first_update": 0 if parent is None else parent.step + 1,
            "hf_parent": str(hf_comparison_parent(previous, -1 if parent is None else parent.step))}
        if handoff is not None:
            from behave.train.slime_rollout import export_handoff

            continuation["rollout_recovery"] = export_handoff(handoff, output / "recovery_objects")
        elif startup_handoff is not None:
            continuation["rollout_recovery"] = startup_handoff
        write_json(output / "continuation.json", continuation)
    if (snapshot.parent / "snapshot.json").is_file():
        write_json(output / "runtime_review.json", review_snapshot(previous, snapshot, review))
    elif config.get("checkpoint_retention") is not None:
        raise RuntimeError("runtime review requires a frozen source manifest")
    bootstrap = ("import sys; sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; "
                 "from tools.slime_training_job import staged; raise SystemExit(staged(*sys.argv[1:]))")
    argv = ["srun", f"--jobid={allocation}", "--exact", "--cpu-bind=threads", "--nodes=1", "--ntasks=1",
            f"--cpus-per-task={cpus}", f"--gpus={layout.required_gpus}", "--kill-on-bad-exit=1",
            sys.executable, "-I", "-B", "-u", "-c", bootstrap,
            str(snapshot), str(snapshot / "src"), str(output)]
    if runtime_locations is not None:
        # Explicitly carry the hot-location profile into the node step, even
        # when its scheduler environment differs from the submitter's.
        interpreter = argv.index(sys.executable)
        argv[interpreter:interpreter] = ["env", "BEHAVE_RUNTIME_LOCATIONS=" + runtime_locations["path"]]
    # The Slurm allocation owns the job lifetime. No additional attempt/round
    # deadline is manufactured by this detached supervisor.
    return detached(output / "control", argv, cwd=repository, wall_seconds=None,
                    cleanup_seconds=config.get("cleanup_seconds"),
                    metadata={"purpose": "real training-task Slime job", "allocation": allocation,
                              "tasks": tasks, "samples_per_task": config["samples_per_task"]})
