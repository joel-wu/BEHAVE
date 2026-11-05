"""Small, detached workflow reviews using the existing rollout and judge operators.

The configuration supplies tasks, release, model, node and an existing budget.
This operator neither selects a benchmark nor creates a new spending allowance.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import mkdtemp
from threading import Event

from behave.runtime.records import archive_run, write_json


def read(path):
    return json.loads(Path(path).read_text())


def prepare(root, configuration):
    from behave.core.artifacts import BlobRef
    from behave.data.published import load_published_task
    from behave.eval.followup import prepare_review as prepare_solver
    from behave.eval.generation import load_tasks
    from behave.pipelines.configuration import baseline_policy
    from behave.pipelines.configuration import prepare as prepare_external
    from tools.infra_validation_job import freeze

    config = read(configuration)
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "configuration.json", config)
    (root / "tasks.txt").write_text("\n".join(config["tasks"]) + "\n")
    base = read(config["template"])
    base.update(tasks=config["tasks"], task_objects=config["task_objects"], release=config["release"],
                spending_directory=config["spending"]["directory"],
                spending_account=config["spending"]["account"], budget_usd=config["spending"]["limit"],
                judge_workers=config["judge_workers"], judge_cpu_capacity=config["judge_cpu_capacity"])
    base.pop("experiment", None)
    # Retain only this review's closed task inputs, not the whole source campaign.
    objects, tasks = load_tasks(root / "release", base)
    if len(tasks) != len(config["tasks"]):
        raise ValueError("review task preparation failed; see release/preparation.json")
    base["task_objects"] = str(root / "release/inputs")
    write_json(root / "template.json", base)
    policies = {baseline_policy(load_published_task(objects, BlobRef.from_dict(base["release"]),
                                                  task.task_id).judge.judge_policy).to_bytes() for task in tasks}
    if len(policies) != 1:
        raise ValueError("select a common explicit baseline policy for this comparison")
    (root / "judge_policy.json").write_bytes(policies.pop())
    for method in config["methods"]:
        output = root / "runs" / method
        if method in config.get("reuse_rollouts", {}):
            shutil.copytree(config["reuse_rollouts"][method], output)
            continue
        if method == "joint_sim":
            prepare_solver(output, root / "template.json", setting=method,
                           workers=len(tasks), rollout_only=True)
        else:
            prepare_external(output, root / "template.json", method, workers=len(tasks),
                             provider_sampling=config.get("provider_sampling", "upstream"))
        settings = read(output / "settings.json")
        settings["model_workers"] = config["model_workers"]
        write_json(output / "settings.json", settings)
    freeze(Path(__file__).resolve().parents[1], root / "snapshot",
           extra_files=("tools/pipeline_trial.py", "tools/pipeline_review_trial.py"))
    return {"root": str(root), "model": base["model"], "tasks": config["tasks"], "methods": config["methods"]}


def launch(root):
    from behave.runtime.jobs import status_of, submit
    from behave.runtime.snapshots import staged_command

    if (root / "control/launch.json").exists():
        return status_of(root / "control")
    config = read(root / "configuration.json")
    worker = ("from tools.pipeline_review_trial import node_run; "
              "raise SystemExit(node_run(Path(sys.argv[1]),source))")
    argv = ["srun", f"--jobid={config['allocation']}", "--overlap", "--exact", "--nodes=1", "--ntasks=1",
            f"--cpus-per-task={config['cpus']}", "--gres=none", "--kill-on-bad-exit=1",
            *staged_command(root / "snapshot", worker, root, prefix="behave-method-code-")]
    return submit(root / "control", argv, cwd=Path(__file__).resolve().parents[1], wall_seconds=None,
                  metadata={"purpose": "workflow-review-not-benchmark", "methods": config["methods"]})


def node_run(root, source):
    from behave.agent.cpu_resources import CpuResources, cpu_job
    from behave.agent.spending import spending_snapshot
    from behave.core.lifecycle import CleanupState
    from behave.core.process_limits import configure_worker_process_limit
    from behave.core.resources import allocated_cpus
    from behave.runtime.jobs import run_child as _child

    config = read(root / "configuration.json")
    limits = configure_worker_process_limit(config.get("process_limit"))
    local = Path(mkdtemp(prefix="behave-method-review-", dir="/tmp"))
    shutil.copytree(root / "release", local / "release")
    shutil.copy2(root / "judge_policy.json", local / "judge_policy.json")
    runtime = {"host": socket.gethostname(), "local_directory": str(local), "source": str(source),
               "process_limit": limits}
    write_json(root / "runtime.json", {**runtime, "state": "validating"})
    os.environ.update(BEHAVE_EDA_ROOT=str(source / "external/oss-cad-suite"),
                      BEHAVE_SCRATCH_DIR=str(local), BEHAVE_WORK_DIR=str(local / "work"),
                      BEHAVE_RUNTIME_DIR=str(local / "runtime"),
                      BEHAVE_REPLAY_WORKERS=str(config["sim_replay_workers"]))
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    boot = [sys.executable, "-I", "-B", "-u", "-c",
            ("import sys,os; source=sys.argv.pop(1); sys.path[:0]=[source,source+'/src']; "
            "os.chdir(source); from tools.pipeline_review_trial import worker; "
            "raise SystemExit(worker(*sys.argv[1:]))"), str(source)]
    validation = _child([*boot, "validate", str(local)], local / "validation.log", stopped)
    shutil.copy2(local / "validation.log", root / "validation.log")
    if (local / "validation.xml").exists():
        shutil.copy2(local / "validation.xml", root / "validation.xml")
    if validation:
        write_json(root / "runtime.json", {**runtime, "state": "validation_failed", "exit_code": validation})
        return validation
    write_json(root / "runtime.json", {**runtime, "state": "running"})
    write_json(root / "budget_before.json", spending_snapshot(Path(config["spending"]["directory"])))

    lanes = CpuResources(len(config["methods"]))

    def execute_method(method):
        output, final = local / "runs" / method, root / "runs" / method
        shutil.copytree(final, output)
        settings = read(output / "settings.json")
        settings["task_objects"] = str(local / "release/inputs")
        write_json(output / "settings.json", settings)
        write_json(final / "runtime.json", {**runtime, "local_directory": str(output), "state": "running"})
        code = _child([*boot, method, str(output), str(final), str(local / "judge_policy.json"),
                       str(len(allocated_cpus())),
                       str(method in config.get("reuse_rollouts", {}))], output / "worker.log", stopped)
        write_json(final / "runtime.json", {**runtime, "local_directory": str(output),
                                           "state": "archiving", "exit_code": code})
        try:
            archive_run(output, final)
        except Exception as exc:  # noqa: BLE001 -- archive boundary retains evidence and reports failure
            write_json(final / "runtime.json", {**runtime, "local_directory": str(output),
                       "state": "failed-retained", "exit_code": code, "archive_error": str(exc)})
            return method, 1
        write_json(final / "runtime.json", {**runtime, "local_directory": str(output),
                                           "state": "archived" if code == 0 else "failed-retained", "exit_code": code})
        return method, code

    def one(method):
        with cpu_job(lanes, cancelled=stopped.is_set) as lease:
            result = execute_method(method)
            if result[1] == 0 and not stopped.is_set():
                lease.cleanup = CleanupState.COMPLETE
            return result

    with ThreadPoolExecutor(max_workers=len(config["methods"])) as pool:
        codes = dict(pool.map(one, config["methods"]))
    write_json(root / "budget_after.json", spending_snapshot(Path(config["spending"]["directory"])))
    write_json(root / "runtime.json", {**runtime, "state": "failed-retained" if any(codes.values()) else "archived",
                                      "exit_codes": codes})
    return int(any(codes.values()))


def recover_archive(root):
    """Copy retained results after a stopped supervisor, without executing models or judges."""
    control = read(root / "control/status.json")
    runtime = read(root / "runtime.json")
    if control["state"] not in {"failed", "completed"} or runtime["host"] != socket.gethostname():
        raise ValueError("recover an exited review on the node retaining its results")
    config = read(root / "configuration.json")
    local = Path(runtime["local_directory"])
    for method in config["methods"]:
        output, final = local / "runs" / method, root / "runs" / method
        archive_run(output, final)
        write_json(final / "runtime.json", {**runtime, "local_directory": str(output),
                   "state": "archive-recovered", "recovery": "retained-results-only"})
    from behave.agent.spending import spending_snapshot
    write_json(root / "budget_after.json", spending_snapshot(Path(config["spending"]["directory"])))
    write_json(root / "runtime.json", {**runtime, "state": "archive-recovered",
               "recovery": "retained-results-only", "original_exit_code": control.get("returncode")})
    return {"root": str(root), "state": "archive-recovered"}


def worker(method, output, final=None, policy=None, cpus=None, score_only="False"):
    output = Path(output)
    if method == "validate":
        import pytest
        return pytest.main(["-q", "-p", "no:cacheprovider", "--junitxml=" + str(output / "validation.xml"),
                            "tests/infrastructure/test_external_pipelines.py",
                            "tests/infrastructure/test_solver_sim.py",
                            "tests/infrastructure/test_sim_tool.py",
                            "tests/infrastructure/test_eval_review_trial.py"])
    os.environ["SLURM_CPUS_PER_TASK"] = cpus
    if method == "joint_sim":
        from behave.eval.generation import run as _run_local
        from behave.eval.report import export_review
        from behave.eval.scoring import score_review as score
        if score_only != "True":
            code = _run_local(output, status_output=Path(final))
            if code:
                return code
        settings = read(output / "settings.json")
        score(output, "baseline", workers=settings["judge_workers"], judge_policy=Path(policy),
              all_submitted=True, cpu_capacity=settings["judge_cpu_capacity"])
        export_review(output)
    else:
        from behave.pipelines.execution import rollout, score
        rollout(output)
        score(output, policy_file=Path(policy))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "launch", "recover-archive"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--configuration", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        print(prepare(root, args.configuration))
    elif args.action == "launch":
        print(launch(root))
    else:
        print(recover_archive(root))
