"""Train a retained scored batch through the normal native learning stage.

The first measured update reuses immutable rewards and old-policy tokens.
No inference engines, new candidate selection or simulator calls are involved.
All run counts and resource choices are explicit launch inputs.
"""

import argparse
import json
import os
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

from behave.runtime.records import write_json


def submit(output, job, allocation, cpus, updates, preparation_workers, preparation_start_method, cpu_threads,
           *, source_plan=None, model_path=None, timing_microbatches=0, kernel_cache=None,
           configuration=None, resume_updates=0, group=None):
    from behave.runtime.jobs import submit as detached
    from behave.runtime.snapshots import freeze
    from behave.train.slime_launch import SlimeLayout

    output, job = output.resolve(), job.resolve()
    repository = Path(__file__).resolve().parents[1]
    if updates < 1 or resume_updates < 0:
        raise ValueError("choose positive initial updates and nonnegative cold-resume updates")
    profile = job / "configuration.json" if configuration is None else configuration.resolve()
    config = json.loads(profile.read_text())
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(profile, output / "source_configuration.json")
    gpus = SlimeLayout(**config["layout"]).actor_gpus
    selection = {"original_job": str(job),
        "source_plan": str((job / "artifacts/plan" if source_plan is None else source_plan).resolve()),
        "model_path": str(Path(config["model_path"] if model_path is None else model_path).resolve()),
        "allocation": allocation, "cpus": cpus, "gpus": gpus,
        "updates": updates, "preparation_workers": preparation_workers,
        "resume_updates": resume_updates, "group": group,
        "preparation_start_method": preparation_start_method, "cpu_threads": cpu_threads,
        "timing_microbatches": timing_microbatches,
        "kernel_cache": None if kernel_cache is None else str(kernel_cache.resolve())}
    write_json(output / "selection.json", selection)
    snapshot = output / "snapshot/checkout"
    freeze(repository, snapshot, extra_files=("tools/slime_recorded_job.py",))
    bootstrap = ("import sys; sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; "
                 "from tools.slime_recorded_job import staged; raise SystemExit(staged(sys.argv[1]))")
    argv = ["srun", f"--jobid={allocation}", "--overlap", "--exact", "--cpu-bind=threads", "-N1", "-n1",
        f"--cpus-per-task={cpus}", f"--gpus={gpus}", "--kill-on-bad-exit=1",
        sys.executable, "-I", "-B", "-u", "-c", bootstrap,
        str(snapshot), str(snapshot / "src"), str(output)]
    return detached(output / "control", argv, cwd=Path(__file__).resolve().parents[1], wall_seconds=None, metadata=selection,
                    cleanup_seconds=config.get("cleanup_seconds"))


def staged(output):
    from behave.core import runtime_defaults as defaults
    from behave.core.resources import allocated_cpus
    from behave.runtime.jobs import node_heartbeat
    from behave.core.process_limits import configure_worker_process_limit

    output = Path(output)
    selection = json.loads((output / "selection.json").read_text())
    original_job_config = json.loads((output / "source_configuration.json").read_text())
    limits = configure_worker_process_limit(original_job_config.get("process_limit"))
    # Some Slurm installations expose more CPUs than the requested step. Keep
    # this job and its children inside the operator's explicit CPU allocation.
    os.sched_setaffinity(0, allocated_cpus(selection["cpus"]))
    with node_heartbeat(output / "node_status.json",
                        interval=original_job_config.get("heartbeat_seconds", defaults.JOB_HEARTBEAT_SECONDS)):
        return _staged(output, selection, original_job_config, limits)


def _staged(output, selection, original_job_config, limits):
    from behave.runtime.snapshots import install_runtime, stage

    local = Path(mkdtemp(prefix="behave-recorded-", dir="/tmp"))
    local.chmod(0o700)
    began = time.monotonic()
    write_json(output / "runtime.json", {"state": "preparing", "host": socket.gethostname(),
        "local_directory": str(local), "started_at": datetime.now(UTC).isoformat(), "process_limit": limits})
    stage(output / "snapshot/checkout", local / "checkout")
    installation = output / "artifacts/installation"
    installation.mkdir(parents=True)
    # Use the original runtime ABI, installing only this frozen BEHAVE revision.
    installed = install_runtime(local / "checkout", installation, original_job_config["native_python"])
    installed["evidence"]["preparation_seconds"] = time.monotonic() - began
    installed["evidence"]["base_native_python"] = original_job_config["native_python"]
    installed["evidence"]["process_limit"] = limits
    write_json(installation / "installed.json", installed["evidence"])
    bootstrap = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
                 "from tools.slime_recorded_job import execute; raise SystemExit(execute(*sys.argv[1:]))")
    return subprocess.run((installed["evidence"]["control_python"], "-I", "-B", "-u", "-c", bootstrap,
        str(local / "checkout"), str(output), str(local)), cwd=local,
        env=installed["environment"], check=False).returncode


def select_group(batch, group):
    """Diagnostic subset: retain every candidate and segment of one group."""
    from dataclasses import replace

    candidates = tuple(row for row in batch.candidates if row["group_id"] == group)
    if not candidates:
        raise ValueError(f"recorded batch has no group {group}")
    ids = {row["rollout_id"] for row in candidates}
    payloads = []
    for part in batch.payloads:
        if part["rollout_id"] not in ids:
            continue
        metadata = dict(part["metadata"])
        if "behave_batch_admission" in metadata:
            metadata["behave_batch_admission"] = {
                "planned_candidates": len(candidates), "selected_candidates": len(candidates)}
        payloads.append(dict(part, index=len(payloads), metadata=metadata))
    return replace(batch, candidates=candidates, payloads=tuple(payloads),
                   skipped_groups=(), group_selection=None)


def prepare(output, local, selection, installed):
    from behave.core.artifacts import BlobRef
    from behave.core.records import canonical_json
    from behave.core.storage import DirectoryStore
    from behave.train.slime_launch import SlimeRecipe
    from behave.train.slime_rollout import load_batch, save_batch

    old_plan = Path(selection["source_plan"])
    plan = output / "artifacts/plan"
    plan.mkdir(parents=True)
    shutil.copytree(old_plan / "inputs", plan / "inputs")
    for name in ("training_selection.json", "source_split.json"):
        shutil.copyfile(old_plan / name, plan / name)
    profile = output / "source_configuration.json"
    config = json.loads((profile if profile.exists() else old_plan / "configuration.json").read_text())
    config.setdefault("process_limit", None)
    config.update(model_path=str(local / "weights"), native_python=installed["native_python"],
                  preparation_workers=selection["preparation_workers"],
                  preparation_start_method=selection["preparation_start_method"], cpu_threads=selection["cpu_threads"])
    if selection.get("kernel_cache") is not None:
        config["kernel_cache"] = selection["kernel_cache"]
    config["layout"] = {**config["layout"], "rollout_gpus": 0, "colocate": False}
    # Inference flags do not belong to a train-only CLI. Keep every actor flag.
    actor_arguments, skip = [], False
    for value in config["native_arguments"]:
        if value.startswith("--"):
            skip = value.startswith("--sglang-")
        if not skip:
            actor_arguments.append(value)
    from behave.train.slime_hooks import HOOK_ARGUMENTS

    # Old live-plan snapshots may already contain these exact timing hooks.
    hooks = list(HOOK_ARGUMENTS)
    for flag, value in zip(hooks[::2], hooks[1::2], strict=True):
        if flag not in actor_arguments:
            actor_arguments.extend((flag, value))
    config["native_arguments"] = actor_arguments
    binding = json.loads((old_plan / "binding.json").read_text())
    receipt = json.loads((old_plan / "rounds/native/request_00000000/response.json").read_text())
    # Historical archives can contain absolute paths on a lost worker. Rebase
    # storage locations, not tokens, scores, policies or the source receipt.
    for name in ("objects", "prepared_batch"):
        if name in receipt:
            relative = Path(receipt[name]).relative_to(binding["run_directory"])
            receipt[name] = str(old_plan / "rounds" / relative)
    reference = DirectoryStore(plan / "inputs").put(canonical_json(receipt))
    binding.update(objects=str(plan / "inputs"), run_directory=str(plan / "rounds"),
                   recorded_rounds=[reference.as_dict()] * (selection["updates"] + selection.get("resume_updates", 0)),
                   preparation_workers=selection["preparation_workers"],
                   preparation_start_method=selection["preparation_start_method"])
    # Reuse the producer's already prepared payload. Replaying its full trace
    # and repacking it again is unnecessary for an offline update.
    if "prepared_batch" in receipt:
        binding["prepared_batches"] = {reference.sha256: receipt["prepared_batch"]}
    if selection.get("group") is not None:
        from behave.agent.providers.local_chat import LocalChatEndpoint
        from behave.train.slime_rollout import round_plan

        expected = round_plan(binding, DirectoryStore(binding["objects"]), round_id=receipt["round"],
            endpoint=LocalChatEndpoint.from_dict(receipt["endpoint"]),
            policy=BlobRef.from_dict(receipt["policy"]), task_selection=receipt.get("task_selection"))
        identities = {"head": BlobRef.from_dict(receipt["head"]),
            "manifest": BlobRef.from_dict(binding["manifest"]), "plan": BlobRef.of(expected.to_bytes())}
        batch = load_batch(receipt["prepared_batch"], **identities,
                           recipe=SlimeRecipe.from_dict(binding["recipe"]))
        subset = select_group(batch, selection["group"])
        target = plan / "prepared/group.json"
        save_batch(target, subset, **identities)
        binding["prepared_batches"] = {reference.sha256: str(target)}
        selection_path = plan / "training_selection.json"
        training_selection = json.loads(selection_path.read_text())
        training_selection.update(tasks_per_update=1, candidates_per_update=len(subset.candidates))
        write_json(selection_path, training_selection)
        write_json(output / "recorded_subset.json", {"source_prepared_batch": receipt["prepared_batch"],
            "group": selection["group"], "candidates": subset.candidates,
            "training_segments": len(subset.payloads),
            "sequence_tokens": sum(len(p["tokens"]) for p in subset.payloads),
            "loss_tokens": sum(sum(p["loss_mask"]) for p in subset.payloads),
            "tokens_truncated": False, "fresh_accuracy_measured": False})
    write_json(plan / "configuration.json", config)
    (plan / "binding.json").write_bytes(canonical_json(binding))
    write_json(output / "configuration.json", config)
    write_json(output / "recorded_input.json", receipt)
    return plan, config


def execute(output, local):
    from behave.agent.run_store import JournalDirectory
    from behave.isolation.service import run_service
    from behave.train.slime_checkpoint import training_steps
    from behave.train.slime_launch import environment, megatron_source
    from behave.train.slime_rollout import prepare_recorded_batches
    from behave.train.slime_stage import run_learning

    output, local = Path(output), Path(local)
    selection = json.loads((output / "selection.json").read_text())
    installed = json.loads((output / "artifacts/installation/installed.json").read_text())
    stopped = Event()
    previous = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    began = time.monotonic()
    state = {"state": "preparing", "host": socket.gethostname(), "local_directory": str(local),
             "artifacts": str(output / "artifacts"), "plan_directory": str(output / "artifacts/plan"),
             "checkpoint_directory": str(output / "artifacts/checkpoints"),
             "source_plan": selection["source_plan"], "source_model": selection["model_path"],
             "cpu_affinity": sorted(os.sched_getaffinity(0)), "preparation_workers": selection["preparation_workers"],
             "cpu_threads": selection["cpu_threads"],
             "process_limit": installed.get("process_limit"),
             "started_at": datetime.now(UTC).isoformat(), "installation_seconds": installed["preparation_seconds"]}

    def publish(**values):
        state.update(values, updated_at=datetime.now(UTC).isoformat(), elapsed_seconds=time.monotonic() - began)
        write_json(output / "runtime.json", state)

    try:
        plan, config = prepare(output, local, selection, installed)
        publish(state="preparing_batch", phase="recorded_batch_cpu")
        preparation = prepare_recorded_batches(plan)
        publish(batch_preparation=preparation)
        if stopped.is_set():
            raise InterruptedError("recorded preparation cancelled; no optimizer was started")
        publish(state="preparing", phase="stage_weights")
        shutil.copytree(selection["model_path"], local / "weights")
        cache = Path(mkdtemp(prefix="br-", dir="/tmp"))
        env = environment(Path(installed["base_native_python"]).resolve().parents[1], cache,
            cuda_visible_devices=os.environ["CUDA_VISIBLE_DEVICES"],
            cpu_threads=config["cpu_threads"],
            kernel_cache=config.get("kernel_cache"),
            megatron_source=megatron_source(config))
        env.update(TORCHDYNAMO_DISABLE="1", NCCL_SOCKET_IFNAME="lo", GLOO_SOCKET_IFNAME="lo",
                   BEHAVE_TRAIN_TIMING_DIRECTORY=str(output / "artifacts/timing"))
        env["BEHAVE_TRAIN_TIMING_MICROBATCHES"] = str(selection.get("timing_microbatches", 0))
        checkpoints = output / "artifacts/checkpoints"
        first, resume = selection["updates"], selection.get("resume_updates", 0)
        final = first + resume
        phases = [("recorded", Path(config["model_path"]), first)]
        if resume:
            phases.append(("resume", checkpoints, final))
        reports, metrics, weight_paths = [], {}, [Path(config["model_path"])]
        for name, load, count in phases:
            stage = JournalDirectory(plan / "learning" / name)
            publish(state="training", phase=name, native_cache=str(cache),
                    stdout=str(stage.directory / "stdout"), stderr=str(stage.directory / "stderr"),
                    timing_directory=str(output / "artifacts/timing"))
            phase_started = time.monotonic()
            report = run_learning(plan=plan, run=stage, updates=count,
                load=load, save=checkpoints, cpus=selection["cpus"],
                environment=env, seconds=config.get("learning_seconds"),
                cleanup_seconds=config.get("cleanup_seconds"), cancelled=stopped.is_set)
            reports.append({"phase": name, "seconds": time.monotonic() - phase_started, "report": report})
            publish(report=report, phases=list(reports))
            if report["status"] != "completed":
                raise RuntimeError("recorded native update did not complete; all files retained")
            text = (stage.directory / "stdout").read_text() + (stage.directory / "stderr").read_text()
            metrics[name] = training_steps(text, require_nonzero=False)
            weight_paths.append(plan / f"hf_{count-1}")
        publish(state="checking_weights", phase_training_metrics=metrics,
                training_metrics=[row for rows in metrics.values() for row in rows])
        script = ("import json,sys; from pathlib import Path; "
            "from behave.train.slime_checkpoint import changed_decoder_weight; "
            "paths=list(map(Path,sys.argv[1:])); "
            "print(json.dumps([changed_decoder_weight(a,b,require_change=False) for a,b in zip(paths,paths[1:])]))")
        final_hf = plan / f"hf_{final-1}"
        check = run_service((installed["native_python"], "-B", "-c", script, *map(str, weight_paths)),
            cwd=local, environment=env, network="none", deadline=None, output_bytes=None,
            cleanup_seconds=config.get("cleanup_seconds"), cancelled=stopped.is_set)
        if check.status != "exited" or check.returncode != 0 or not check.cleanup_state.quiescent:
            raise RuntimeError("weight-change inspection failed: " + check.stderr.decode(errors="replace"))
        artifacts = output / "artifacts"
        (artifacts / "final_hf").symlink_to(final_hf.relative_to(artifacts), target_is_directory=True)
        changes = json.loads(check.stdout)
        gradients = {name: any(row["train/grad_norm"] > 0 for row in rows) for name, rows in metrics.items()}
        publish(state="completed", phase=None, weight_change=changes[-1], weight_changes=changes,
                nonzero_gradients=gradients,
                native_update_and_cold_resume=bool(resume) and all(changes) and all(gradients.values()),
                final_hf=str(final_hf), learned_accuracy_measured=False)
        return 0
    except Exception as error:
        publish(state="interrupted", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, help="maintained runtime profile; source job is unchanged")
    parser.add_argument("--group", help="retain one complete recorded candidate group, without truncating tokens")
    parser.add_argument("--resume-updates", type=int, default=0,
                        help="additional updates in a new native process from the first phase's checkpoint")
    parser.add_argument("--source-plan", type=Path, help="shared plan extracted from a legacy archive, if needed")
    parser.add_argument("--model-path", type=Path, help="shared initial model; defaults to the source job configuration")
    parser.add_argument("--timing-microbatches", type=int, default=0,
                        help="number of forward/backward calls to time per step; 0 disables detailed timing")
    parser.add_argument("--allocation", type=int, required=True)
    parser.add_argument("--cpus", type=int, required=True)
    parser.add_argument("--updates", type=int, required=True)
    parser.add_argument("--preparation-workers", type=int, required=True)
    parser.add_argument("--preparation-start-method", choices=("spawn", "forkserver", "fork"), required=True)
    parser.add_argument("--cpu-threads", type=int, required=True)
    parser.add_argument("--kernel-cache", type=Path,
                        help="reuse compatible native kernel caches; process scratch remains private")
    args = parser.parse_args()
    print(json.dumps(submit(args.output, args.job, args.allocation, args.cpus, args.updates,
                           args.preparation_workers, args.preparation_start_method, args.cpu_threads,
                           source_plan=args.source_plan, model_path=args.model_path,
                           timing_microbatches=args.timing_microbatches,
                           kernel_cache=args.kernel_cache, configuration=args.configuration,
                           group=args.group, resume_updates=args.resume_updates), indent=2))


if __name__ == "__main__":
    main()
