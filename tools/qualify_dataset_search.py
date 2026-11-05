"""Run frozen automatic-goal/search qualification through the public Sim command.

This schedules the existing measure_search_score worker; it is not an evaluator.
B/B uses the task's reference on both sides to exercise the Behavior search path,
not to claim independent functional validation. B/R uses the unchanged reference
pair. A score and a search capability observation are reported separately.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tools.measure_search_score import write


def prepare(repository, output):
    from tools.dataset_runtime_snapshot import freeze
    from tools.qualify_dataset_baseline import task_order

    output.mkdir(parents=True, exist_ok=False)
    tasks = [path.relative_to(repository).as_posix()
             for path in task_order(repository / "data/problems")]
    manifest = freeze(repository, output / "snapshot", (
        "src", "tools/measure_search_score.py", "tools/qualify_dataset_search.py",
        "tools/dataset_runtime_snapshot.py", *tasks))
    write(output / "plan.json", {"tasks": tasks, "artifacts": ["rtl", "behavior"],
                                "snapshot": manifest["digest"],
                                "meaning": "search/observation qualification, not independent B/B correctness"})
    return {"tasks": len(tasks), "artifact_runs": 2 * len(tasks), "snapshot": manifest["digest"]}


def select_tasks(tasks, *, include=None, exclude=(), shard=0, shards=1):
    if shards < 1 or not 0 <= shard < shards:
        raise ValueError("shard must be in [0, shards)")
    requested = set(tasks if include is None else include)
    excluded = set(exclude)
    if (requested | excluded) - set(tasks):
        raise ValueError("selection names tasks outside the frozen plan")
    return [task for task in tasks if task in requested and task not in excluded][shard::shards]


def summarize(directory):
    """Read existing receipts only. Never turn a missing decision into a pass."""
    measurement = directory / "measurement.json"
    worker = directory / "worker.json"
    record = json.loads(measurement.read_text()) if measurement.is_file() else {}
    supervision = json.loads(worker.read_text()) if worker.is_file() else {}
    return {"status": record.get("status", "no-measurement"),
            "worker_status": supervision.get("status"),
            "phase": record.get("phase"), "elapsed_s": supervision.get("elapsed_s"),
            "decision": record.get("decision"), "evaluation": record.get("evaluation"),
            "search": record.get("search_observation"),
            "search_summary": record.get("search_summary"),
            "diagnostics": record.get("diagnostics"),
            "refusal": record.get("refusal"), "failure": record.get("failure"),
            "error": record.get("error")}


def inventory(campaign, output):
    """Inventory declared monitor bindings, not source or solver qualification.

    Use unchanged task bytes from the campaign and the running Sim's contract
    reader/providers. Missing bindings stay explicit per goal; a present binding
    is not evidence that an RTL relation can actually be built or queried.
    """
    from behave.sim.core.goal_provider import EXACT_BOUNDED_HIT, LOCAL_GUIDANCE
    from behave.sim.core.monitor import monitor_for
    from tools.measure_search_score import task_configuration

    plan = json.loads((campaign / "plan.json").read_text())
    output.mkdir(parents=True, exist_ok=False)
    counts, profiles, tasks, goals = Counter(), {}, [], []
    for task in plan["tasks"]:
        try:
            contract, _ = task_configuration(campaign / "snapshot" / task)
            providers = monitor_for(contract).goal_providers()
        except Exception as error:  # retain unreadable tasks, not empty catalogues
            tasks.append({"task": task, "status": "contract-error",
                          "error": f"{type(error).__name__}: {error}"})
            counts["contract_errors"] += 1
            continue
        profile = ",".join(sorted({e.adapter.name for e in contract.endpoints}))
        rows = []
        for provider in providers:
            local = provider.binding(LOCAL_GUIDANCE)
            exact = provider.binding(EXACT_BOUNDED_HIT)
            for name, goal in provider.catalogue.items():
                rows.append({
                    "task": task, "profile": profile, "goal": name,
                    "class": goal.cls, "schema": provider.schema.name,
                    "provider": provider.identity.key,
                    "local_binding": None if local is None else local.implementation,
                    "exact_binding": None if exact is None else exact.implementation,
                })
        missing = sum(row["local_binding"] is None for row in rows)
        missing_exact = sum(row["exact_binding"] is None for row in rows)
        tasks.append({"task": task, "profile": profile, "status": "inspected",
                      "monitor_goals": len(rows), "missing_local_bindings": missing,
                      "missing_exact_bindings": missing_exact})
        goals.extend(rows)
        group = profiles.setdefault(profile, Counter())
        for counter in (counts, group):
            counter["tasks_inspected"] += 1
            counter["monitor_goal_instances"] += len(rows)
            counter["missing_local_bindings"] += missing
            counter["missing_exact_bindings"] += missing_exact
            counter["tasks_missing_local_bindings"] += bool(missing)
            counter["tasks_missing_exact_bindings"] += bool(missing_exact)
            counter["tasks_all_local_bindings_present"] += not missing
            counter["tasks_all_monitor_bindings_present"] += not (missing or missing_exact)
    summary = {
        "scope": "contract monitor declarations only; no source catalogue, query, "
                 "solver, replay or scoring acceptance",
        "task_snapshot": plan["snapshot"], "sim_code": "current Python import environment",
        "tasks_planned": len(plan["tasks"]), "counts": dict(counts),
        "profiles": profiles,
    }
    write(output / "tasks.json", tasks)
    write(output / "goals.json", goals)
    write(output / "summary.json", summary)
    return summary


def run(args):
    from behave.agent.cpu_resources import CpuResources, cpu_job
    from behave.core import runtime_defaults as defaults
    from behave.core.paths import eda_root
    from behave.core.process_limits import configure_worker_process_limit
    from behave.isolation.service import run_service

    limits = configure_worker_process_limit()
    plan = json.loads((args.campaign / "plan.json").read_text())
    include = json.loads(args.include.read_text()) if args.include else None
    exclude = json.loads(args.exclude.read_text()) if args.exclude else ()
    tasks = select_tasks(plan["tasks"], include=include, exclude=exclude,
                         shard=args.shard, shards=args.shards)
    args.output.mkdir(parents=True, exist_ok=False)
    local = Path(tempfile.mkdtemp(prefix="behave-search-qualification-", dir="/tmp"))
    checkout = local / "checkout"
    # One node-local copy per batch, never copies an interpreter/site-packages.
    shutil.copytree(args.campaign / "snapshot", checkout)
    resources = CpuResources(args.workers, cpus=args.cpus)
    environment = {
        "PATH": str(eda_root() / "bin") + ":/usr/bin:/bin",
        "PYTHONPATH": str(checkout / "src") + ":" + str(checkout),
        "PYTHONDONTWRITEBYTECODE": "1", "BEHAVE_SCRATCH_DIR": "/tmp",
        "BEHAVE_RUNTIME_DIR": str(local / "runtime"), "BEHAVE_EDA_ROOT": str(eda_root()),
        "LC_ALL": "C.UTF-8",
    }
    if args.replay_workers is not None:
        environment["BEHAVE_REPLAY_WORKERS"] = str(args.replay_workers)
    jobs = [(task, artifact) for task in tasks for artifact in args.artifacts]
    write(args.output / "stage.json", {"local": str(local), "host": socket.gethostname(),
        "snapshot": plan["snapshot"], "tasks": tasks, "artifacts": args.artifacts,
        "replay_workers": args.replay_workers,
        "process_limits": limits, "policy": "public runtime defaults: baseline, search and total scoring",
        "evidence": "complete execution evidence retained node-local; compact receipts exported"})
    write(args.output / "resources.json", resources.snapshot())

    def execute(task, artifact):
        name = Path(task).name + "/" + artifact
        result = local / "runs" / name
        target = args.output / "cases" / name
        target.mkdir(parents=True)
        with cpu_job(resources) as lease:
            env = dict(environment, SLURM_CPUS_PER_TASK=str(len(lease.cpus)))
            started = time.monotonic()
            write(target / "launch.json", {"task": task, "artifact": artifact,
                                          "local": str(result), "cpus": list(lease.cpus)})
            # The measurement worker owns the public 1200s score budget and its
            # finalize reserve. The existing PID-namespace supervisor fences all
            # descendants before this lease is reused, including failed workers.
            got = run_service((sys.executable, "-B", str(checkout / "tools/measure_search_score.py"),
                               "run", str(checkout), str(result), task, "--artifact", artifact,
                               "--default-baseline"),
                              cwd=checkout, environment=env, deadline=None, output_bytes=None,
                              cleanup_seconds=defaults.SERVICE_CLEANUP_SECONDS)
            lease.cleanup = got.cleanup_state
            (target / "stdout.log").write_bytes(got.stdout)
            (target / "stderr.log").write_bytes(got.stderr)
            for filename in ("measurement.json", "progress.json", "worker.json",
                             "behavior_source.json", "rtl_source.json"):
                if (result / filename).is_file():
                    shutil.copyfile(result / filename, target / filename)
            row = {"task": task, "artifact": artifact, **summarize(target),
                   "service_status": got.status, "returncode": got.returncode,
                   "cleanup": got.cleanup_state.value, "service_elapsed_s": time.monotonic() - started,
                   "local": str(result), "receipt": str(target)}
            write(target / "result.json", row)
            return row

    completed = []
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        pending = {workers.submit(execute, task, artifact): (task, artifact) for task, artifact in jobs}
        for future in as_completed(pending):
            task, artifact = pending[future]
            try:
                row = future.result()
            except Exception as error:  # noqa: BLE001 -- retain each failed orchestration, never infer a score
                row = {"task": task, "artifact": artifact, "status": "orchestration-error",
                       "error": f"{type(error).__name__}: {error}", "decision": None}
            completed.append(row)
            write(args.output / "results.json", sorted(completed, key=lambda row: (row["task"], row["artifact"])))
            write(args.output / "resources.json", resources.snapshot())
            write(args.output / "status.json", {"completed": len(completed), "total": len(jobs)})
            print(json.dumps({"completed": len(completed), "total": len(jobs),
                              "task": task, "artifact": artifact, "status": row["status"]}), flush=True)
    return {"completed": len(completed), "total": len(jobs), "local": str(local)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    freeze = modes.add_parser("prepare")
    freeze.add_argument("output", type=Path)
    freeze.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    audit = modes.add_parser("inventory", help="inspect monitor bindings without executing tasks")
    audit.add_argument("campaign", type=Path)
    audit.add_argument("output", type=Path)
    batch = modes.add_parser("run")
    batch.add_argument("campaign", type=Path)
    batch.add_argument("output", type=Path)
    batch.add_argument("--workers", type=int, required=True)
    batch.add_argument("--cpus", type=int, required=True,
                       help="Total allocated CPU budget, shared across all workers (not per worker).")
    batch.add_argument("--replay-workers", type=int,
                       help="Explicit Sim replay width per invocation; absent uses the public runtime default.")
    batch.add_argument("--shard", type=int, default=0)
    batch.add_argument("--shards", type=int, default=1)
    batch.add_argument("--include", type=Path, help="JSON list of frozen task paths")
    batch.add_argument("--exclude", type=Path, help="JSON list of frozen task paths")
    batch.add_argument("--artifacts", nargs="+", choices=("rtl", "behavior"), default=["rtl", "behavior"])
    args = parser.parse_args()
    if args.mode == "prepare":
        result = prepare(args.repository.resolve(), args.output.resolve())
    elif args.mode == "inventory":
        result = inventory(args.campaign.resolve(), args.output.resolve())
    else:
        result = run(args)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
