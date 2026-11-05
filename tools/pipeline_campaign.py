"""Run multiple hosted backbones through the existing pipeline operators.

One configuration owns the task release, shared monetary account and CPU steps.
Rollout and baseline scoring remain separate operations. No new solver or judge
is implemented here. Delayed arms wait before requesting a Slurm CPU step.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

from behave.runtime.records import write_json

SOLVER_METHODS = ("joint", "rtl_only", "joint_sim", "rtl_sim")


def read(path):
    return json.loads(Path(path).read_text())


def arms(config):
    return [(model, method) for model in config["models"] for method in config["workers"]]


def arm_name(model, method):
    return f"{model}_{method}"


def arm_directory(root, config, model, method):
    name = arm_name(model, method)
    return root / "runs" / config.get("run_directories", {}).get(name, name)


def settings_for(config, model, template):
    """Keep provider-specific parameters, but replace the old experiment account."""
    settings = dict(template)
    settings.pop("retry", None)
    settings.pop("cases", None)
    settings.pop("solver_settings", None)
    settings.update(budget_usd=config["budget_usd"], spending_account=config["account"],
                    spending_directory=config["spending_directory"],
                    cpus=config["cpus_per_arm"], judge_workers=config["judge_workers"],
                    judge_cpu_capacity=config["judge_cpu_capacity"], process_limit=None,
                    score_on_submit=False, purpose="formal-pipeline-comparison",
                    task_objects=config["task_objects"], release=config["release"], tasks=config["tasks"])
    if settings["model"] != config["models"][model]["model"]:
        raise ValueError(f"provider template has the wrong model for {model}")
    return settings


def prepare(root):
    from behave.core.artifacts import BlobRef, read_verified
    from behave.core.storage import DirectoryReader
    from behave.data.published import ReleaseManifest
    from behave.eval.followup import prepare_review as prepare_solver
    from behave.pipelines.configuration import baseline_policy
    from behave.pipelines.configuration import prepare as prepare_external
    from behave.runtime.snapshots import freeze

    config = read(root / "configuration.json")
    base = Path(config["task_release_directory"])
    config.update(release=read(base / "release" / "release.json"),
                  task_objects=str(base / "release" / "inputs"),
                  tasks=config.get("tasks", (base / "tasks.txt").read_text().split()))
    release = ReleaseManifest.from_bytes(read_verified(DirectoryReader(config["task_objects"]),
                                                       BlobRef.from_dict(config["release"])))
    if not config["tasks"] or len(config["tasks"]) != len(set(config["tasks"])):
        raise ValueError("expected nonempty distinct task ids in campaign")
    if not set(config["tasks"]) <= {task.task_id for task in release.tasks}:
        raise ValueError("task list contains tasks outside the selected release")
    write_json(root / "configuration.json", config)
    (root / "tasks.txt").write_text("\n".join(config["tasks"]) + "\n")
    write_json(root / "task_lineage.json", [row for row in read(base / "task_lineage.json")
                                           if row["task"] in config["tasks"]])
    write_json(root / "release.json", config["release"])
    policy = replace(baseline_policy((base / "judge_policy.json").read_bytes()), max_retries=0)
    (root / "judge_policy.json").write_bytes(policy.to_bytes())
    for model, data in config["models"].items():
        template = root / "templates" / f"{model}.json"
        write_json(template, settings_for(config, model, read(data["template"])))
        for method, workers in config["workers"].items():
            output = arm_directory(root, config, model, method)
            if method in SOLVER_METHODS:
                prepare_solver(output, template, setting=method, workers=workers, rollout_only=True)
            else:
                prepare_external(output, template, method, workers=workers,
                                 provider_sampling="provider_default")
    snapshot = freeze(Path(config["repository"]), root / "snapshot",
                      extra_files=("tools/pipeline_trial.py", "tools/pipeline_campaign.py", "tools/eval_score_inputs.py"))
    write_json(root / "manifest.json", {
        "purpose": "formal-pipeline-comparison", "tasks": len(config["tasks"]),
        "rollouts": len(config["tasks"]) * len(arms(config)), "release": config["release"],
        "models": {key: value["model"] for key, value in config["models"].items()},
        "methods": list(config["workers"]), "scoring": "baseline-only",
        "source_snapshot": snapshot["sha256"], "split_changed": False,
        "account": config["account"], "budget_usd": config["budget_usd"],
        "task_release_directory": str(base), "prior_rollouts_reused": False})


def slurm_command(root, config, model, method):
    from behave.runtime.snapshots import staged_command

    snapshot = root / config.get("source_snapshot", "snapshot")
    worker = "from tools.pipeline_campaign import run; run(Path(sys.argv[1]),sys.argv[2],sys.argv[3],source)"
    return ["srun", f"--jobid={config['allocation']}", "--exact", "--nodes=1", "--ntasks=1",
            f"--cpus-per-task={config['cpus_per_arm']}", "--gres=none", "--kill-on-bad-exit=1",
            "--propagate=NONE", *staged_command(snapshot, worker, root, model, method,
                                                prefix="behave-pipeline-code-")]


def wait_until(timestamp, *, clock=time.time, sleep=time.sleep):
    """Recheck UTC after every wakeup, including an interrupted/early sleep."""
    target = datetime.fromisoformat(timestamp).timestamp()
    while (remaining := target - clock()) > 0:
        sleep(min(30, remaining))


def dispatch(root, model, method):
    from behave.core.process_limits import configure_worker_process_limit

    configure_worker_process_limit()
    config = read(root / "configuration.json")
    delayed = config["models"][model].get("not_before")
    control = arm_directory(root, config, model, method) / "control"
    if delayed:
        write_json(control / "dispatch.json", {"state": "waiting", "not_before": delayed})
        wait_until(delayed)
    write_json(control / "dispatch.json", {"state": "requesting-cpus", "dispatched_at": time.time()})
    command = slurm_command(root, config, model, method)
    os.execvp(command[0], command)


def launch(root):
    from behave.core.process_limits import configure_worker_process_limit
    from behave.runtime.jobs import status_of, submit

    configure_worker_process_limit()
    config = read(root / "configuration.json")
    snapshot = root / config.get("source_snapshot", "snapshot")
    bootstrap = ("import sys; from pathlib import Path; "
                 "sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; "
                 "from tools.pipeline_campaign import dispatch; "
                 "dispatch(Path(sys.argv[1]),sys.argv[2],sys.argv[3])")
    for model, method in arms(config):
        control = arm_directory(root, config, model, method) / "control"
        if (control / "launch.json").exists():
            print(arm_name(model, method), status_of(control), flush=True)
            continue
        command = [sys.executable, "-I", "-B", "-u", "-c", bootstrap,
                   str(snapshot), str(snapshot / "src"), str(root), model, method]
        metadata = {"purpose": "formal-pipeline-comparison", "model": config["models"][model]["model"],
                    "method": method, "tasks": len(config["tasks"]), "allocation": config["allocation"],
                    "cpus": config["cpus_per_arm"], "scoring": "baseline-only",
                    "not_before": config["models"][model].get("not_before"), "account": config["account"]}
        print(arm_name(model, method), submit(control, command, cwd=Path(__file__).resolve().parents[1],
              wall_seconds=None, metadata=metadata), flush=True)


def run(root, model, method, source):
    from behave.agent.spending import spending_snapshot
    from behave.core.process_limits import configure_worker_process_limit
    from behave.runtime.records import archive_run

    config = read(root / "configuration.json")
    final = arm_directory(root, config, model, method)
    # Nested external-method scratch also contains AF_UNIX sockets. Human-readable
    # model/method names belong in runtime.json, not in their constrained paths.
    local = Path(mkdtemp(prefix="bp-", dir="/tmp"))
    shutil.copy2(final / "settings.json", local / "settings.json")
    if (final / "pipeline.json").exists():
        shutil.copy2(final / "pipeline.json", local / "pipeline.json")
    runtime = {"host": socket.gethostname(), "local_directory": str(local), "source": str(source),
               "process_limit": configure_worker_process_limit(), "cpus": sorted(os.sched_getaffinity(0))}
    os.environ.update(BEHAVE_EDA_ROOT=str(source / "external" / "oss-cad-suite"),
                      BEHAVE_SCRATCH_DIR=str(local), BEHAVE_WORK_DIR=str(local / "work"),
                      BEHAVE_RUNTIME_DIR=str(local / "runtime"),
                      BEHAVE_REPLAY_WORKERS=str(config["sim_replay_workers"]))
    write_json(final / "runtime.json", {**runtime, "state": "rollout"})
    completed = False
    try:
        if method in SOLVER_METHODS:
            from behave.eval.generation import run as _run_local
            from behave.eval.report import export_review
            from behave.eval.scoring import score_review as score
            if _run_local(local, status_output=final):
                raise RuntimeError(f"{method} rollout operator failed")
            write_json(final / "runtime.json", {**runtime, "state": "baseline-scoring"})
            score(local, config["judgment"], workers=config["judge_workers"],
                  judge_policy=root / "judge_policy.json", all_submitted=True,
                  cpu_capacity=config["judge_cpu_capacity"])
            export_review(local)
        else:
            from behave.pipelines.execution import rollout, score
            rollout(local)
            write_json(final / "runtime.json", {**runtime, "state": "baseline-scoring"})
            score(local, name=config["judgment"], workers=config["judge_workers"],
                  policy_file=root / "judge_policy.json")
        completed = True
    finally:
        write_json(final / "runtime.json", {**runtime, "state": "archiving"})
        archive_run(local, final)
        write_json(final / "runtime.json", {**runtime, "state": "archived" if completed else "failed-retained"})
        if (Path(config["spending_directory"]) / "spending_cache.json").exists():
            write_json(root / "billing_live.json", spending_snapshot(Path(config["spending_directory"])))


def retry_tasks(source, method, failure_codes):
    """Choose named infrastructure failures without a submission, never scores."""
    if read(source / "runtime.json")["state"] not in {"archived", "failed-retained"}:
        raise ValueError("wait for the source arm to finish archiving before preparing a recovery")
    if method in SOLVER_METHODS:
        from behave.eval.followup import load_outcomes, select_retries

        return sorted(select_retries(load_outcomes([source]), failure_codes=failure_codes)["rollout"])
    return sorted(row["task"] for row in read(source / "rollout.json")
                  if row.get("submission") is None and row.get("error") in failure_codes)


def prepare_recovery(root, model, method, source_name, name, *, failure_codes, cpus, workers):
    """Prepare one declared whole-trace replacement through the existing operators.

    Launch is separate. Source attempts, uncertain charges, task bytes, prompts,
    method settings and judge policy are retained. A second recovery must name
    its actual ancestor instead of sampling the original failure again.
    """
    from behave.eval.followup import prepare_review as prepare_solver
    from behave.pipelines.configuration import prepare as prepare_external

    if not failure_codes:
        raise ValueError("recovery requires explicit failure codes")
    config = read(root / "configuration.json")
    source = root / "runs" / source_name
    primary = arm_directory(root, config, model, method)
    ancestors = {primary.name} | {r["run_directory"] for r in config.get("recoveries", [])
                                  if (r["model"], r["method"]) == (model, method)}
    if source_name not in ancestors:
        raise ValueError("source is not a declared arm or recovery for this model/method")
    already_replaced = {task for r in config.get("recoveries", [])
                        if (r["model"], r["method"], r["replaces"]) == (model, method, source_name)
                        for task in r["tasks"]}
    tasks = [task for task in retry_tasks(source, method, failure_codes) if task not in already_replaced]
    if not tasks:
        return {"tasks": [], "prepared": False}
    if cpus < config["judge_cpu_capacity"]:
        raise ValueError("recovery CPUs must fit one configured judge worker")
    child = root / "retries" / name
    output = root / "runs" / name
    if output.exists():
        raise FileExistsError(output)
    child.mkdir(parents=True, exist_ok=False)
    recovery_config = {**config, "parent_campaign": str(root), "models": {model: config["models"][model]},
        "workers": {method: workers}, "cpus_per_arm": cpus,
        "judge_workers": cpus // config["judge_cpu_capacity"], "tasks": tasks,
        "run_directories": {arm_name(model, method): str(output)},
        "source_snapshot": str(root / config.get("source_snapshot", "snapshot"))}
    recovery_config.pop("recoveries", None)
    write_json(child / "configuration.json", recovery_config)
    shutil.copy2(root / "judge_policy.json", child / "judge_policy.json")
    template = child / "template.json"
    write_json(template, settings_for(recovery_config, model, read(source / "settings.json")))
    if method in SOLVER_METHODS:
        prepare_solver(output, template, setting=method, workers=workers, rollout_only=True)
    else:
        original = read(source / "pipeline.json")
        prepare_external(output, template, method, workers=workers, options=original["options"],
                         provider_sampling=original["provider_sampling"])
        write_json(output / "pipeline.json", original)
    declaration = {"model": model, "method": method, "run_directory": name,
                   "replaces": source_name, "tasks": tasks, "reason": ",".join(failure_codes)}
    write_json(child / "selection.json", {**declaration, "source": str(source),
                                         "failure_codes": list(failure_codes)})
    config.setdefault("recoveries", []).append(declaration)
    write_json(root / "configuration.json", config)
    return {"tasks": tasks, "prepared": True, "campaign": str(child), "output": str(output)}


def settle(root):
    """Called once on the ledger's compute node after all arms have exited."""
    from behave.agent.spending import SpendAccount, SpendPolicy
    from behave.runtime.records import archive_spending

    config = read(root / "configuration.json")
    if config.get("parent_campaign"):
        raise ValueError("settle the parent campaign after all sibling arms and recoveries finish")
    directories = [arm_directory(root, config, model, method) for model, method in arms(config)]
    directories.extend(root / "runs" / recovery["run_directory"] for recovery in config.get("recoveries", []))
    for directory in directories:
        state = read(directory / "runtime.json")["state"]
        if state not in {"archived", "failed-retained"}:
            raise ValueError("cannot settle a campaign with active arms")
    account = SpendAccount(SpendPolicy(config["spending_directory"], config["account"], "USD",
                                      config["budget_usd"], 131072))
    write_json(root / "billing.json", account.summary())
    archive_spending(Path(config["spending_directory"]), root / "spending")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "launch", "settle", "prepare-recovery"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--method", choices=(*SOLVER_METHODS, "mage", "chipmate"))
    parser.add_argument("--source", help="Declared source run-directory name")
    parser.add_argument("--name", help="New recovery run-directory name")
    parser.add_argument("--failure-code", action="append", default=[])
    parser.add_argument("--cpus", type=int)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args()
    if args.action == "prepare-recovery":
        if any(getattr(args, key) is None for key in ("model", "method", "source", "name", "cpus", "workers")):
            parser.error("prepare-recovery requires --model, --method, --source, --name, --cpus and --workers")
        print(json.dumps(prepare_recovery(args.directory.resolve(), args.model, args.method,
            args.source, args.name, failure_codes=tuple(args.failure_code), cpus=args.cpus, workers=args.workers)))
    else:
        globals()[args.action](args.directory.resolve())


if __name__ == "__main__":
    main()
