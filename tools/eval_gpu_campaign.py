"""Eight single-GPU engines and the shared, attempt-balanced Eval runner.

Weights are staged once per model to node-local storage. Runtime installations
remain shared read-only. Each model's joint and RTL-only arms use the same eight
engines; all are closed before the next model starts. Final judging is separate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp
from threading import Event

from behave.runtime.jobs import run_child as _child
from behave.runtime.records import archive_run, write_json


def prepare(root: Path, configuration: Path):
    """Combine specified frozen task releases into one interleaved queue per model."""
    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.core.records import canonical_json
    from behave.core.storage import DirectoryStore
    from behave.data.published import ReleaseManifest, TaskRevision, load_published_task
    from behave.data.published.files import task_files
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy

    config = json.loads(configuration.read_text())
    root.mkdir(parents=True, exist_ok=False)
    objects = DirectoryStore(root / "release/objects")
    tasks, revisions, inventory = [], [], []
    policies = set()
    for source_name in config["sources"]:
        source = Path(source_name)
        reader = DirectoryReader(source / "release/objects")
        release = BlobRef.from_dict(json.loads((source / "release/release.json").read_text()))
        for task_id in (source / "tasks.txt").read_text().split():
            if task_id in tasks:
                raise ValueError(f"source task lists overlap: {task_id}")
            published = load_published_task(reader, release, task_id)
            old = published.manifest
            policy = replace(JudgePolicy.from_bytes(published.judge.judge_policy),
                             command_version=api.COMMAND_VERSION, authority_version=api.RESULT_VERSION,
                             search_seconds=0, analysis=None)
            policies.add(policy.to_bytes())
            files = task_files(old)
            objects.publish(reader, (old.run_policy, *(file.content for file in files)))
            current = replace(old, judge_policy=objects.put(policy.to_bytes()))
            revisions.append(TaskRevision(task_id, objects.put(current.to_bytes())))
            tasks.append(task_id)
            inventory.append({"task_id": task_id, "source": str(source),
                              "original_revision": published.public.revision.as_dict(),
                              "revision": revisions[-1].manifest.as_dict()})
    if len(policies) != 1:
        raise ValueError("source baseline policies differ; select an explicit common policy")
    (root / "release/judge_policy.json").write_bytes(policies.pop())
    combined = ReleaseManifest(root.name, tuple(revisions), objects.put(canonical_json({
        "purpose": "additional frozen task evaluation", "sources": config["sources"]})))
    release = objects.put(combined.to_bytes()).as_dict()
    write_json(root / "release/release.json", release)
    write_json(root / "release/inventory.json", inventory)
    (root / "tasks.txt").write_text("\n".join(tasks) + "\n")
    runs = []
    for model in config["models"]:
        name = model["id"] + "_pair"
        write_json(root / "weights" / (model["id"] + ".json"), json.loads(Path(model["weights"]).read_text()))
        settings = {"model": model["model"], "provider": "sglang", "tasks": tasks,
                    "release": release, "task_objects": str(root / "release/objects"),
                    "solver_settings": ["joint", "rtl_only"], "score_on_submit": True,
                    "attempt": config["attempt"], "attempt_namespace": f"{root.name}-{model['id']}",
                    "workers": config["workers"], "model_workers": config["workers"],
                    "judge_workers": config["judge_workers"], "judge_cpu_capacity": config["judge_cpu_capacity"]}
        write_json(root / "runs" / name / "settings.json", settings)
        runs.append({"name": name, "model": model["id"], "provider": "sglang", "tasks": len(tasks) * 2})
    manifest = {"configuration": config, "runs": runs, "task_count": len(tasks),
                "rollouts": sum(row["tasks"] for row in runs), "release": release,
                "include_in_table1": False, "final_eval_split_changed": False}
    write_json(root / "campaign.json", manifest)
    return {"root": str(root), "tasks": len(tasks), "rollouts": manifest["rollouts"],
            "model_order": [model["id"] for model in config["models"]]}


def drain(root: Path, pid: int):
    """Let the live original model finish, then retire its delegated queue.

    This is an operational handoff for an already-running older dispatcher.
    It never interrupts an active rollout or rewrites its completion receipt.
    """
    from behave.runtime.jobs import wait_control

    path = root / "gpu_handoff.json"
    record = json.loads(path.read_text())
    if record["state"] == "drained":
        return record
    state = json.loads((root / "gpu_runtime.json").read_text())
    if state["host"] != socket.gethostname():
        raise ValueError("drain must run on the original dispatcher's node")
    cmdline = Path(f"/proc/{pid}/cmdline")

    def owned():
        raw = cmdline.read_bytes()
        return b"tools.eval_gpu_campaign" in raw and str(root).encode() in raw and b"run\x00" in raw

    if not owned():
        raise ValueError("PID does not identify this campaign's original GPU dispatcher")
    record.update(state="watching", dispatcher_pid=pid, host=socket.gethostname())
    write_json(path, record)
    wake = Event()
    while True:
        final = root / "runs" / record["after_run"] / "runtime.json"
        if final.exists() and json.loads(final.read_text())["state"] == "archived":
            break
        if not cmdline.exists():
            raise RuntimeError("dispatcher exited before the retained arm finished")
        wake.wait(1)
    # The original process is now in archive/engine cleanup or weight staging,
    # not in the retained model's rollout worker.
    for model in record["delegated_models"]:
        for setting in ("joint", "rtl_only"):
            other = root / "runs" / f"{model}_{setting}" / "runtime.json"
            if other.exists() and json.loads(other.read_text())["host"] == socket.gethostname():
                raise RuntimeError("delegated model has already started on the original node")
    if cmdline.exists() and owned():
        os.kill(pid, signal.SIGINT)
    original = wait_control(root / "gpu_control", allow_failure=True)
    record.update(state="drained", original_control=original["state"])
    write_json(path, record)
    return record


def repair_targets(root: Path, manifest: dict, failure_codes: tuple[str, ...]) -> list[dict]:
    """Select recorded transport interruptions, never failed candidate scores."""
    from behave.eval.followup import load_outcomes, prepare, select_retries

    previous = json.loads((root / "gpu_runtime.json").read_text())
    handoff = root / "gpu_handoff.json"
    drained = (handoff.exists() and json.loads(handoff.read_text()).get("state") == "drained"
               and json.loads((root / "gpu_extra_runtime.json").read_text())["state"] == "archived")
    if (previous["state"] != "archived" and not drained) or previous["host"] != socket.gethostname():
        raise ValueError("repair GPU runs only after rollout engines exit, on their owning node")
    targets = []
    for row in manifest["runs"]:
        if row["provider"] != "sglang":
            continue
        directory = root / "runs" / row["name"]
        selection = select_retries(load_outcomes([directory]), failure_codes=failure_codes)
        if not selection["rollout"]:
            continue
        target = directory / (row["name"] + "_infra_retry")
        prepare(target, [directory], failure_codes=failure_codes)
        targets.append({**row, "directory": target, "tasks": len(selection["rollout"])})
    return targets


def run(root: Path, *, failure_codes: tuple[str, ...] = (), models: tuple[str, ...] = (),
        parallel_models: int = 1):
    from behave.agent.cpu_resources import CpuResources, cpu_job
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.core.lifecycle import CleanupState
    from behave.core.resources import allocated_cpus
    from behave.serving.environment import environment, headers, inspect_installation
    from behave.serving.lifecycle import close_sessions, start_sessions
    from behave.serving.session import ServingSession
    from behave.serving.spec import bound_spec

    manifest = json.loads((root / "campaign.json").read_text())
    state_path = root / ("gpu_repair_runtime.json" if failure_codes else
                         "gpu_extra_runtime.json" if models else "gpu_runtime.json")
    if state_path.exists():
        previous = json.loads(state_path.read_text())
        if previous["state"] == "archived":
            return 0
        raise ValueError(f"existing local run requires recovery: {previous['local_directory']}")
    targets = (repair_targets(root, manifest, failure_codes) if failure_codes else
               [{**row, "directory": root / "runs" / row["name"]}
                for row in manifest["runs"] if row["provider"] == "sglang"
                and (not models or row["model"] in models)])
    if models:
        handoff = json.loads((root / "gpu_handoff.json").read_text())
        if set(models) != set(handoff["delegated_models"]):
            raise ValueError("extra GPU lane must match the explicitly delegated models")
        if any((row["directory"] / "runtime.json").exists() for row in targets):
            raise ValueError("delegated model already has an owner")
    local = Path(mkdtemp(prefix="behave-gpu-campaign-", dir="/tmp"))
    local.chmod(0o700)
    os.environ["BEHAVE_SCRATCH_DIR"] = str(local)
    if "sim_replay_workers" in manifest["configuration"]:
        os.environ["BEHAVE_REPLAY_WORKERS"] = str(manifest["configuration"]["sim_replay_workers"])
    state = {"state": "running", "host": socket.gethostname(), "local_directory": str(local),
             "failure_codes": list(failure_codes), "models": list(models), "parallel_models": parallel_models}
    write_json(state_path, state)
    shutil.copytree(root / "release", local / "release")
    source = Path(__file__).resolve().parents[1]
    binding = json.loads((source / "infra/model_runtimes/local-serving.json").read_text())
    devices = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")
    if len(devices) != 8 or not all(devices):
        raise ValueError(f"eight allocated GPUs required, got {devices}")
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    selected = [model for model in manifest["configuration"]["models"]
                if any(row["model"] == model["id"] for row in targets)]
    if parallel_models < 1 or parallel_models > len(devices) or len(devices) % parallel_models:
        raise ValueError("parallel model lanes must divide the allocated devices evenly")
    groups = [devices[index::parallel_models] for index in range(parallel_models)]
    lanes = CpuResources(parallel_models)

    def run_model(model, model_devices, port_offset):
        results = []
        model_targets = [row for row in targets if row["model"] == model["id"]]
        if not model_targets or stopped.is_set():
            return results
        weight = json.loads((root / "weights" / (model["id"] + ".json")).read_text())
        if weight["model"] != model["model"]:
            raise ValueError("weight record names a different model")
        scratch = local / model["id"]
        scratch.mkdir()
        evidence = root / ("repair_engines" if failure_codes else "engines") / model["id"]
        write_json(evidence / "runtime.json",
                   {"state": "staging_weights", "host": socket.gethostname(), "local_directory": str(scratch)})
        print(f"Staging {model['model']} once for {len(model_devices)} engines.", flush=True)
        shutil.copytree(weight["path"], scratch / "weights")
        base = replace(bound_spec(binding, "sglang"), model_path=str(scratch / "weights"),
            model_revision=weight["revision"], served_model=model["model"], tensor_parallel=1,
            context_length=manifest["configuration"]["attempt"]["context_tokens"],
            startup_seconds=900, lifetime_seconds=259200)
        shared = scratch / "shared"
        lane_cpus = str(len(allocated_cpus()))
        env = environment(base, shared, {"CUDA_VISIBLE_DEVICES": model_devices[0], "SLURM_CPUS_PER_TASK": lane_cpus})
        write_json(scratch / "installation.json", inspect_installation(base, shared, env,
                   deadline=time.monotonic() + 120))
        includes = headers(binding["headers"], shared, env, deadline=time.monotonic() + 120)
        sessions = []
        for index, device in enumerate(model_devices):
            spec = replace(base, port=32000 + port_offset + index)
            directory = scratch / f"engine-{index}"
            env = environment(spec, directory, {"CUDA_VISIBLE_DEVICES": device, "SLURM_CPUS_PER_TASK": lane_cpus},
                              includes=includes)
            sessions.append(ServingSession(spec, scratch=directory, environment=env,
                                           records=scratch / "engines" / str(index)))
        try:
            start_sessions(sessions, cancelled=stopped.is_set)
            write_json(evidence / "runtime.json",
                       {"state": "ready", "host": socket.gethostname(), "local_directory": str(scratch),
                        "engines": len(sessions), "devices": model_devices})
            urls = tuple(f"http://127.0.0.1:{session.spec.port}/v1/chat/completions" for session in sessions)
            parameters = {"chat_template_kwargs": {"enable_thinking": True}}
            if model["id"] == "qwen38_27b":
                parameters["reasoning_effort"] = "xhigh"
            endpoint = LocalChatEndpoint(urls[0], base.served_model,
                f"sglang-{base.package_version}/{base.model_revision}", parameters=parameters,
                routing="attempt", worker_urls=urls)
            write_json(scratch / "engines.json", [{"engine": i, "device": device, "url": urls[i],
                       "tensor_parallel": 1, "context_tokens": base.context_length} for i, device in enumerate(model_devices)])
            for row in model_targets:
                if stopped.is_set():
                    continue
                final = row["directory"]
                destination = local / final.name
                if failure_codes:
                    shutil.copytree(final, destination)
                else:
                    destination.mkdir()
                settings = json.loads((final / "settings.json").read_text())
                settings.update(endpoint=endpoint.as_dict(), task_objects=str(local / "release/objects"))
                if failure_codes:
                    settings["retry"] = {"selection": str(destination / "selection.json")}
                write_json(destination / "settings.json", settings)
                write_json(final / "runtime.json", {**state, "local_directory": str(destination)})
                bootstrap = ("import sys; sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; "
                    "from pathlib import Path; from tools.eval_review_trial import _run_local; "
                    "raise SystemExit(_run_local(Path(sys.argv[1]),status_output=Path(sys.argv[2])))")
                code = _child([sys.executable, "-I", "-B", "-u", "-c", bootstrap, str(source), str(source / "src"),
                               str(destination), str(final)], destination / "worker.log", stopped)
                results.append({"run": row["name"], "exit_code": code})
                archive_run(destination, final)
                write_json(final / "runtime.json", {**state, "state": "archived" if code == 0 else "failed",
                           "exit_code": code, "local_directory": str(destination)})
        finally:
            def record_cleanup(report):
                write_json(scratch / "cleanup.json", report["services"])
                evidence.mkdir(parents=True, exist_ok=True)
                for name in ("engines", "engines.json", "installation.json", "cleanup.json"):
                    path = scratch / name
                    if path.is_dir():
                        shutil.copytree(path, evidence / name, dirs_exist_ok=True)
                    elif path.exists():
                        shutil.copy2(path, evidence / name)

            close_sessions(sessions, record=record_cleanup, primary_error=sys.exception())
        return results

    def lane(index):
        with cpu_job(lanes, cancelled=stopped.is_set) as lease:
            rows = []
            for model in selected[index::parallel_models]:
                rows.extend(run_model(model, groups[index], index * len(groups[index])))
            if all(row["exit_code"] == 0 for row in rows) and not stopped.is_set():
                lease.cleanup = CleanupState.COMPLETE
            return rows

    try:
        with ThreadPoolExecutor(max_workers=parallel_models) as pool:
            results = [row for rows in pool.map(lane, range(parallel_models)) for row in rows]
    except BaseException as error:
        state.update(state="failed", failure=type(error).__name__)
        try:
            write_json(state_path, state)
        except BaseException as write_error:  # noqa: BLE001 -- retain and re-raise the active error
            error.add_note(f"Campaign failure receipt could not be recorded: {type(write_error).__name__}")
        raise
    state.update(state="archived" if len(results) == len(targets) and all(row["exit_code"] == 0 for row in results)
                 and not stopped.is_set() else "failed", runs=results)
    write_json(state_path, state)
    return 0 if state["state"] == "archived" else 1


def submit(root: Path, allocation: int, *, failure_codes: tuple[str, ...] = (), models: tuple[str, ...] = (),
           parallel_models: int = 1):
    from behave.runtime.jobs import submit as detached
    from behave.runtime.snapshots import verify
    from tools.eval_campaign import freeze_phase

    source = Path(__file__).resolve().parents[1]
    phase = "repair-gpu" if failure_codes else "gpu-extra" if models else "gpu"
    snapshot = root / "snapshots" / phase / "checkout"
    if not snapshot.exists():
        freeze_phase(source, root, snapshot, phase)
    else:
        verify(snapshot)
    bootstrap = ("import sys; sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; "
                 "from tools.eval_gpu_campaign import staged; raise SystemExit(staged(*sys.argv[1:]))")
    configuration = json.loads((root / "campaign.json").read_text())["configuration"]
    argv = ["srun", f"--jobid={allocation}", "--overlap", "--exact", "--nodes=1", "--ntasks=1",
            f"--cpus-per-task={configuration.get('cpus', 180)}", "--gpus=8", "--kill-on-bad-exit=1", sys.executable,
            "-I", "-B", "-u", "-c", bootstrap, str(snapshot), str(snapshot / "src"), str(snapshot), str(root),
            json.dumps(failure_codes), json.dumps(models), str(parallel_models)]
    return detached(root / (phase + "_control"), argv, cwd=Path(__file__).resolve().parents[1], wall_seconds=259200,
                    metadata={"campaign": root.name, "allocation": allocation, "phase": phase})


def staged(snapshot, root, failure_codes="[]", models="[]", parallel_models="1"):
    from behave.runtime.snapshots import stage
    local = Path(mkdtemp(prefix="behave-campaign-code-", dir="/tmp")) / "checkout"
    stage(Path(snapshot), local)
    os.execv(sys.executable, [sys.executable, "-I", "-B", "-u", "-c",
        "import sys; sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; from tools.eval_gpu_campaign import main; main()",
        str(local), str(local / "src"), "run", root,
        *[item for code in json.loads(failure_codes) for item in ("--failure-code", code)],
        *[item for model in json.loads(models) for item in ("--model", model)],
        "--parallel-models", parallel_models])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "submit", "run", "drain"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--allocation", type=int, help="existing Slurm allocation; required for submit")
    parser.add_argument("--failure-code", action="append", default=[],
                        help="after the original GPU phase exits, retry only this recorded infrastructure failure")
    parser.add_argument("--model", action="append", default=[], help="explicitly delegated models for the extra node")
    parser.add_argument("--parallel-models", type=int, default=1)
    parser.add_argument("--pid", type=int, help="original dispatcher PID for an explicit queue handoff")
    args = parser.parse_args()
    if args.action == "submit" and args.allocation is None:
        parser.error("submit requires --allocation")
    if args.action == "prepare":
        print(prepare(args.root.resolve(), args.configuration))
    elif args.action == "submit":
        print(json.dumps(submit(args.root.resolve(), args.allocation, failure_codes=tuple(args.failure_code),
                               models=tuple(args.model), parallel_models=args.parallel_models), indent=2))
    elif args.action == "drain":
        print(json.dumps(drain(args.root.resolve(), args.pid), indent=2))
    else:
        raise SystemExit(run(args.root.resolve(), failure_codes=tuple(args.failure_code),
                             models=tuple(args.model), parallel_models=args.parallel_models))


if __name__ == "__main__":
    main()
