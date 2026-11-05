"""Population check: score every declared task's reference artifact through public Sim.

Each case runs ``tools/measure_search_score.py`` on one task's reference RTL, or on
its second Behavior model scored against the golden one, with the public
defaults: the complete baseline, then shared-budget Search, then the optional
bounded Analysis. The code under test is ``git archive <commit>``
extracted to node-local /tmp, so a run is pinned to one commit and is not
affected by edits to the working tree. Every case runs as a supervised service
on a disjoint CPU lease. Nodes claim cases from the shared plan directory, so a
node can join or leave at any time; a case is claimed by creating
``<plan dir>/claims/<case>`` atomically. Detailed records stay in the node-local
directory; one compact JSON row per case is appended to
``<plan dir>/rows/<node>.jsonl`` on the shared filesystem.

    python tools/sim_census.py plan CENSUS/plan.json --tag sim-baseline-20261018 \\
        --search-seconds 600 --score-seconds 1200 --analysis-seconds 3000
    srun --jobid=JOB --overlap --propagate=NONE --ntasks=1 \\
        .venv/bin/python tools/sim_census.py run CENSUS/plan.json --node n02 --cpus 0-39 --workers 40
    python tools/sim_census.py summarize CENSUS/plan.json

A case keeps about one CPU busy (one Python worker at a time), so one worker
per CPU uses the lease; two CPUs per case left half of them idle in practice.

This is a measurement, not an evaluator: it changes no task, bound or score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "behave.sim-census/1"
# The bounded Analysis domain: N transactions per input endpoint and H execution
# slots per clock domain for clocked tasks whose inputs are not sampled.
INPUT_CAPACITY = 100
HORIZON_SLOTS = 337
# By default, do not start another case while the node has less memory available.
MIN_AVAILABLE_GIB = 300
LOG_BYTES = 4 << 20
SUPERVISION_MARGIN_SECONDS = 120


def _git(*arguments: str) -> str:
    return subprocess.run(("git", "-C", str(REPO), *arguments), check=True,
                          capture_output=True, text=True).stdout


def population(commit: str) -> list[dict]:
    """The authored split (train and eval) and the external evaluation set, at ``commit``."""
    index = json.loads(_git("show", f"{commit}:data/splits/current.json"))
    split = f"data/splits/{index['authored']}"
    names = [line.strip() for part in ("train.txt", "eval.txt", "external_eval.txt")
             for line in _git("show", f"{commit}:{split}/{part}").splitlines() if line.strip()]
    return cases_for(commit, names)


def cases_for(commit: str, names: list[str]) -> list[dict]:
    folders: dict[str, list[str]] = {}
    for dataset in _git("ls-tree", "-d", "--name-only", f"{commit}:data/problems").split():
        for name in _git("ls-tree", "-d", "--name-only", f"{commit}:data/problems/{dataset}").split():
            folders.setdefault(name, []).append(dataset)
    cases = []
    for name in names:
        found = folders.get(name, [])
        if len(found) != 1:
            raise ValueError(f"task {name!r} resolves to {len(found)} problem directories")
        cases.append({"name": f"{found[0]}__{name}", "dataset": found[0],
                      "task": f"data/problems/{found[0]}/{name}"})
    if len({case["name"] for case in cases}) != len(cases):
        raise ValueError("a task is listed twice")
    return cases


def plan(args) -> None:
    if (args.artifact == "behavior") != bool(args.candidate_behavior):
        raise SystemExit("a Behavior census names its --candidate-behavior; an RTL census does not")
    commit = _git("rev-parse", "--verify", f"{args.tag}^{{commit}}").strip()
    if args.tasks is None:
        cases = population(commit)
    else:
        names = [line.strip() for line in args.tasks.read_text().splitlines() if line.strip()]
        cases = cases_for(commit, names)
    record = {
        "schema": SCHEMA, "tag": args.tag, "commit": commit,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "policy": {"artifact": args.artifact, "candidate_behavior": args.candidate_behavior,
                   "search_seconds": args.search_seconds, "score_seconds": args.score_seconds,
                   "analysis_seconds": args.analysis_seconds, "prepare_seconds": args.prepare_seconds,
                   "save_analysis_inputs": args.analysis_seconds > 0},
        "cases": cases,
    }
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    with args.plan.open("x") as stream:
        stream.write(json.dumps(record, indent=1) + "\n")
    print(json.dumps({"plan": str(args.plan), "commit": commit, "cases": len(cases),
                      "datasets": dict(Counter(case["dataset"] for case in cases))}))


def analysis_domain(checkout: Path, case: dict, contract, *, artifact: str = "rtl"):
    """Return (H, drain slots, declared clock schedule or None) for one task.

    The domain belongs to the task's contract; only RTL Analysis takes a
    declared clock schedule.
    """
    from behave.sim import api
    from behave.sim.rtl.exact_product import global_schedule_for_stimuli, needs_global_schedule_product

    replay = api.ReplayPolicy.for_contract(contract)
    if not contract.clocks:
        horizon, drain = INPUT_CAPACITY, 0
    elif all(endpoint.adapter.name == "sampled-edge" for endpoint in contract.inputs()):
        # The fixed sampled calendar ticks every participating domain in each
        # slot, so H is not multiplied by the clock count here.
        horizon, drain = INPUT_CAPACITY + replay.reset_hold_cycles, 0
    else:
        horizon, drain = HORIZON_SLOTS * len(contract.clocks), replay.drain_slots
    if artifact != "rtl" or not (contract.clocks and needs_global_schedule_product(contract)):
        return horizon, drain, None
    # One seed fixes the clock/action calendar; payload cells stay free in the query.
    from behave.pipelines.public_inputs import rtl_interface

    folder = checkout / case["task"]
    files = {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*")
             if path.is_file() and path.suffix in {".sv", ".v", ".svh", ".vh"}}
    bundle = api.prepare_rtl_bundle(api.bundle_for_entry(files, "rtl.sv"))
    top = rtl_interface((folder / "spec.md").read_text())[0]
    realization = api.realization_of_rtl_source(bundle.source, folder.name, top=top, contract=contract)
    generation = api.GenerationSpec(horizon, INPUT_CAPACITY, replay.reset_hold_cycles, drain,
                                    stall_every=replay.stall_every)
    shape = api.build_baseline(contract, generation, realization=realization,
                               policy=api.BaselinePolicy(case_limit=1, max_queue_depth=INPUT_CAPACITY))
    return horizon, drain, global_schedule_for_stimuli(contract, shape.stimuli[:1], horizon=horizon)


def case(args) -> None:
    """Runs inside one case's service: declare the domain, then become the measurement worker."""
    plan_record = json.loads(args.plan.read_text())
    entry, policy = plan_record["cases"][args.index], plan_record["policy"]
    args.output.mkdir(parents=True)
    from tools.measure_search_score import task_configuration, write

    try:
        contract, _ = task_configuration(args.checkout / entry["task"])
        artifact = policy.get("artifact", "rtl")
        command = [sys.executable, "-B", str(args.checkout / "tools/measure_search_score.py"), "_worker",
                   str(args.checkout), str(args.output / "measurement"), entry["task"],
                   "--artifact", artifact, "--default-baseline",
                   *(["--candidate-behavior", policy["candidate_behavior"]]
                     if policy.get("candidate_behavior") else []),
                   "--search-seconds", str(policy["search_seconds"]),
                   "--seconds", str(policy["score_seconds"]),
                   "--prepare-seconds", str(policy["prepare_seconds"])]
        domain = None
        if policy["analysis_seconds"]:
            horizon, drain, schedule = analysis_domain(args.checkout, entry, contract, artifact=artifact)
            command += ["--analysis", "--analysis-seconds", str(policy["analysis_seconds"]),
                        "--analysis-horizon", str(horizon), "--analysis-drain-slots", str(drain)]
            if schedule is not None:
                write(args.output / "schedule.json", schedule.as_dict())
                command += ["--analysis-clock-schedule", str(args.output / "schedule.json")]
            domain = {"horizon": horizon, "inputs": INPUT_CAPACITY, "drain_slots": drain,
                      "clock_schedule": schedule is not None}
        if policy.get("save_analysis_inputs"):
            command.append("--save-analysis-inputs")
        write(args.output / "domain.json", {"case": entry, "policy": policy, "domain": domain,
                                            "command": command})
    except Exception as error:
        write(args.output / "setup_error.json", {"type": type(error).__name__, "why": str(error)})
        raise
    (args.output / "measurement").mkdir()
    os.execv(sys.executable, command)


def required_baseline_passed(record: dict) -> bool:
    """The public decision passed on the complete required pool, not on a prefix."""
    evaluation = record.get("evaluation") or {}
    authority = (record.get("decision") or {}).get("authority") or {}
    return (evaluation.get("passed") is True
            and authority.get("kind") == "concrete_evaluation"
            and authority.get("authoritative") is True
            and authority.get("passed") is True
            and authority.get("score_scope") in ("requested-pool", "required-baseline"))


def case_row(entry: dict, output: Path, service, cpus, elapsed: float) -> dict:
    measured = output / "measurement"
    path = next((path for path in (measured / "measurement.json", measured / "progress.json")
                 if path.is_file()), None)
    record = json.loads(path.read_text()) if path is not None else {}
    setup = output / "setup_error.json"
    goals = Counter()
    classes = {}
    for row in (record.get("replay") or {}).get("classes", ()):
        counts = {key: row[key] for key in ("declared", "baseline_hit", "search_only_hit",
                                            "continuous_only_hit", "unhit")}
        classes[row["class"]] = counts
        goals.update(counts)
    analysis = record.get("analysis") or {}
    per_goal = analysis.get("per_goal") or {}
    status_of = {goal: row["status"] for goal, row in per_goal.items()}
    evidence = analysis.get("evidence") or {}
    catalogue = evidence.get("catalogue")
    closure = evidence.get("closure") or {}
    accounted = catalogue is not None and {goal["id"] for goal in catalogue} == set(per_goal)
    refusal = evidence.get("refusal") or record.get("refusal")
    detail = (refusal or {}).get("detail") or {}
    if not per_goal and "pending" in detail:
        # An exhausted Analysis budget reports its completed part in the refusal.
        for key, status in (("completed_reached", "reached"),
                            ("completed_unreachable", "bounded-unreachable"),
                            ("completed_unresolved", "unresolved"), ("pending", "pending")):
            status_of.update(dict.fromkeys(detail.get(key, ()), status))
    statuses = Counter(status_of.values())
    families = {goal["id"]: f"{goal.get('cls')}/{goal.get('family')}" for goal in catalogue or ()}
    open_goals = Counter(families.get(goal, "unknown") for goal, status in status_of.items()
                         if status not in ("reached", "bounded-unreachable"))
    stages = Counter()
    for stage in record.get("command_stages", ()):
        if stage["name"] in ("replay_assessment", "generation", "concrete_score",
                             "analysis_preparation", "bounded_analysis"):
            stages[stage["name"]] += stage.get("elapsed_s", 0.0)
    search = record.get("search_observation") or {}
    return {
        "case": entry["name"], "dataset": entry["dataset"], "task": entry["task"],
        "host": socket.gethostname(), "cpus": list(cpus), "elapsed_s": round(elapsed, 2),
        "service": {"status": service.status, "returncode": service.returncode,
                    "cleanup": service.cleanup_state.value},
        "setup_error": json.loads(setup.read_text()) if setup.is_file() else None,
        "status": record.get("status"), "phase": record.get("phase"),
        "error": record.get("error"),
        "failure": (record.get("failure") or {}).get("kind") if isinstance(record.get("failure"), dict)
        else record.get("failure"),
        "required_baseline_passed": required_baseline_passed(record),
        "evaluation": None if not record.get("evaluation") else {
            key: record["evaluation"].get(key)
            for key in ("passed", "complete", "accepted", "mismatch_count", "safety_hits", "runs")},
        "goals": dict(goals), "goal_classes": classes,
        "search": {"exercised": search.get("exercised"),
                   "directions": [{key: row.get(key) for key in ("provider", "state", "stop_reason",
                                                                  "probes_completed", "candidates_retained")}
                                  for row in search.get("directions", ())]},
        "analysis": None if not analysis else {
            "verdict": analysis.get("verdict"), "why": analysis.get("why"),
            "catalogue": None if catalogue is None else len(catalogue),
            "statuses": dict(statuses),
            "open_by_family": dict(open_goals),
            "fully_classified": (analysis.get("verdict") == "classified" and accounted
                                 and closure.get("accounted_for") is True
                                 and closure.get("n_unresolved") == 0
                                 and not set(statuses) - {"reached", "bounded-unreachable"}),
            "refusal": None if not refusal else refusal.get("kind"),
        },
        "stages_s": {name: round(seconds, 2) for name, seconds in stages.items()},
        "measurement_s": record.get("elapsed_s"),
        "record": None if path is None else str(path),
    }


def _cpu_list(text: str) -> tuple[int, ...]:
    cpus = []
    for part in text.split(","):
        low, _, high = part.partition("-")
        cpus.extend(range(int(low), int(high or low) + 1))
    if len(set(cpus)) != len(cpus):
        raise ValueError("CPU list repeats a CPU")
    return tuple(cpus)


def _available_gib() -> float:
    with open("/proc/meminfo") as stream:
        for line in stream:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1 << 20)
    raise RuntimeError("MemAvailable is missing from /proc/meminfo")


def _extract(commit: str, checkout: Path) -> None:
    checkout.mkdir()
    archive = subprocess.Popen(("git", "-C", str(REPO), "archive", commit), stdout=subprocess.PIPE)
    subprocess.run(("tar", "-x", "-C", str(checkout)), stdin=archive.stdout, check=True)
    archive.stdout.close()
    if archive.wait() != 0:
        raise RuntimeError(f"git archive {commit} failed")
    (checkout / "external").mkdir(exist_ok=True)
    (checkout / "external/oss-cad-suite").symlink_to(REPO / "external/oss-cad-suite",
                                                     target_is_directory=True)


def run(args) -> None:
    plan_path = args.plan.resolve()
    record = json.loads(plan_path.read_text())
    if record.get("schema") != SCHEMA:
        raise ValueError("not a census plan")
    if not args.node.replace("-", "").replace("_", "").isalnum():
        raise ValueError("a node label is alphanumeric")
    cpus = _cpu_list(args.cpus)
    if not set(cpus) <= os.sched_getaffinity(0):
        raise ValueError("requested CPUs are outside this step's allocation")
    os.sched_setaffinity(0, cpus)
    os.environ["SLURM_CPUS_PER_TASK"] = str(len(cpus))

    rows_path = plan_path.parent / "rows" / f"{args.node}.jsonl"
    rows_path.parent.mkdir(exist_ok=True)
    claims = plan_path.parent / "claims"
    claims.mkdir(exist_ok=True)
    finished = {json.loads(line)["case"] for path in rows_path.parent.glob("*.jsonl")
                for line in path.read_text().splitlines() if line}
    queue = [index for index, entry in enumerate(record["cases"]) if entry["name"] not in finished]

    scratch = Path(tempfile.mkdtemp(prefix="behave-census-", dir="/tmp"))
    checkout = scratch / "checkout"
    began = time.monotonic()
    _extract(record["commit"], checkout)
    tool = scratch / "sim_census.py"
    shutil.copyfile(Path(__file__).resolve(), tool)
    local_plan = scratch / "plan.json"
    shutil.copyfile(plan_path, local_plan)
    receipt = {"node": args.node, "host": socket.gethostname(), "scratch": str(scratch),
               "commit": record["commit"], "cpus": list(cpus), "workers": args.workers,
               "tool_sha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
               "queued": len(queue), "already_finished": len(finished),
               "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
               "extract_s": round(time.monotonic() - began, 1)}
    receipts = plan_path.parent / "nodes"
    receipts.mkdir(exist_ok=True)
    (receipts / f"{args.node}.started.json").write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps(receipt), flush=True)

    # From here on, import the pinned code, never the working tree.
    sys.path[:0] = [str(checkout / "src"), str(checkout)]
    from behave.agent.cpu_resources import CpuResources, cpu_job
    from behave.core.lifecycle import CleanupState
    from behave.core.process_limits import configure_worker_process_limit
    from behave.core.runtime_defaults import SERVICE_CLEANUP_SECONDS
    from behave.isolation.service import run_service
    from tools.local_batch import environment as batch_environment

    configure_worker_process_limit()
    environment = batch_environment(checkout, scratch, REPO / "external/oss-cad-suite")
    resources = CpuResources(args.workers, cpus=len(cpus))
    policy = record["policy"]
    supervision = (policy["score_seconds"] + policy["analysis_seconds"] + policy["prepare_seconds"]
                   + SUPERVISION_MARGIN_SECONDS)

    # Prepare the shared immutable tool runtime once, as Eval does, before any case.
    warm = "\n".join((
        "import os",
        "from behave.sim import api",
        "from behave.sim.job_isolation import _package_mount",
        "runtime = api.prepare_runtime(profiles=('source-catalogue', 'source-observation', "
        "'source-events', 'simulation'), directory=os.environ['BEHAVE_RUNTIME_DIR'])",
        "with runtime.activate():",
        "    for package in ('behave', 'z3'):",
        "        _package_mount(package)",
    ))
    with cpu_job(resources) as lease:
        prepared = run_service((sys.executable, "-B", "-c", warm), cwd=checkout, environment=environment,
                               deadline=time.monotonic() + 1800, output_bytes=LOG_BYTES,
                               cleanup_seconds=SERVICE_CLEANUP_SECONDS)
        lease.cleanup = prepared.cleanup_state
    if prepared.status != "exited" or prepared.returncode != 0:
        sys.stderr.write(prepared.stderr.decode(errors="replace"))
        raise RuntimeError("shared runtime preparation failed; no case was started")

    lock = threading.Lock()

    def claim(entry) -> bool:
        try:
            (claims / entry["name"]).mkdir()
        except FileExistsError:
            return False
        (claims / entry["name"] / "owner.json").write_text(json.dumps(
            {"node": args.node, "host": socket.gethostname(), "scratch": str(scratch),
             "claimed_at": datetime.now(UTC).isoformat(timespec="seconds")}) + "\n")
        return True

    def lane() -> int:
        done = 0
        while True:
            while _available_gib() < args.min_free_gib:
                time.sleep(30)
            with lock:
                while queue and not claim(record["cases"][queue[0]]):
                    queue.pop(0)
                if not queue:
                    return done
                index = queue.pop(0)
            entry = record["cases"][index]
            output = scratch / "cases" / entry["name"]
            started = time.monotonic()
            with cpu_job(resources) as lease:
                result = run_service(
                    (sys.executable, "-B", str(tool), "case", str(checkout), str(local_plan),
                     str(index), str(output)),
                    cwd=checkout, environment={**environment, "SLURM_CPUS_PER_TASK": str(len(lease.cpus))},
                    deadline=time.monotonic() + supervision, output_bytes=LOG_BYTES,
                    cleanup_seconds=SERVICE_CLEANUP_SECONDS)
                lease.cleanup = result.cleanup_state
                cpus_used = lease.cpus
            output.mkdir(parents=True, exist_ok=True)
            (output / "stdout.log").write_bytes(result.stdout)
            (output / "stderr.log").write_bytes(result.stderr)
            row = case_row(entry, output, result, cpus_used, time.monotonic() - started)
            with lock:
                with rows_path.open("a") as stream:
                    stream.write(json.dumps(row) + "\n")
            print(json.dumps({key: row[key] for key in ("case", "status", "elapsed_s")}), flush=True)
            done += 1
            if result.cleanup_state is not CleanupState.COMPLETE:
                # These CPUs are retired; continuing would oversubscribe the rest.
                return done

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        completed = sum(pool.map(lambda _: lane(), range(args.workers)))
    receipt.update(completed=completed, unclaimed_here=len(queue),
                   finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
                   elapsed_s=round(time.monotonic() - began, 1))
    (receipts / f"{args.node}.finished.json").write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps(receipt), flush=True)


def summarize(args) -> None:
    plan_path = args.plan.resolve()
    record = json.loads(plan_path.read_text())
    rows = {}
    for path in sorted((plan_path.parent / "rows").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line:
                row = json.loads(line)
                rows[row["case"]] = row

    def tally(selected):
        total = Counter()
        for row in selected:
            analysis = row["analysis"] or {}
            statuses = analysis.get("statuses", {})
            total.update({
                "finished": 1,
                "service_failed": int(row["service"]["status"] != "exited"
                                      or row["service"]["cleanup"] != "complete"),
                "evaluated": int(row["status"] == "evaluated"),
                "baseline_passed": int(row["required_baseline_passed"]),
                "search_exercised": int(row["search"]["exercised"] is True),
                "goals": row["goals"].get("declared", 0),
                "baseline_hit": row["goals"].get("baseline_hit", 0),
                "search_only_hit": row["goals"].get("search_only_hit", 0),
                "fully_classified": int(analysis.get("fully_classified") is True),
                "reached": statuses.get("reached", 0),
                "unreachable": statuses.get("bounded-unreachable", 0),
                "pending": sum(n for status, n in statuses.items()
                               if status not in ("reached", "bounded-unreachable")),
            })
        return total

    datasets = sorted({entry["dataset"] for entry in record["cases"]})
    planned = Counter(entry["dataset"] for entry in record["cases"])
    table = {name: {"planned": planned[name],
                    **tally(row for row in rows.values() if row["dataset"] == name)}
             for name in datasets}
    table["all"] = {"planned": len(record["cases"]), **tally(rows.values())}
    outcome = {
        "status": dict(Counter(str(row["status"]) for row in rows.values())),
        "analysis_verdict": dict(Counter(str((row["analysis"] or {}).get("verdict"))
                                         for row in rows.values())),
        "refusal": dict(Counter((row["analysis"] or {}).get("refusal") for row in rows.values()
                                if (row["analysis"] or {}).get("refusal"))),
        "failure": dict(Counter(str(row["failure"]) for row in rows.values() if row["failure"])),
    }
    summary = {"schema": SCHEMA, "commit": record["commit"], "policy": record["policy"],
               "by_dataset": table, "outcomes": outcome}
    (plan_path.parent / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    columns = ("planned", "finished", "service_failed", "evaluated", "baseline_passed",
               "search_exercised", "goals", "baseline_hit", "search_only_hit",
               "fully_classified", "reached", "unreachable", "pending")
    print("dataset".ljust(18) + "".join(column[:12].rjust(13) for column in columns))
    for name, counts in table.items():
        print(name.ljust(18) + "".join(str(counts.get(column, 0)).rjust(13) for column in columns))
    print(json.dumps(outcome, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    making = commands.add_parser("plan", help="declare the cases and budgets of one census")
    making.add_argument("plan", type=Path)
    making.add_argument("--tag", required=True, help="git revision of the code and tasks under test")
    making.add_argument("--tasks", type=Path, help="explicit task ids, one per line; default: all 921")
    making.add_argument("--artifact", choices=("rtl", "behavior"), default="rtl")
    making.add_argument("--candidate-behavior",
                        help="with --artifact behavior: the task file scored against behavior.py, "
                             "e.g. behavior2.py")
    making.add_argument("--search-seconds", type=int, required=True)
    making.add_argument("--score-seconds", type=int, required=True)
    making.add_argument("--analysis-seconds", type=int, default=0, help="0 skips bounded Analysis")
    making.add_argument("--prepare-seconds", type=int, default=300)
    running = commands.add_parser("run", help="claim and run cases of a census on this node")
    running.add_argument("plan", type=Path)
    running.add_argument("--node", required=True, help="label of this node's rows file, e.g. n02")
    running.add_argument("--cpus", required=True, help="CPU list for this node, e.g. 0-39")
    running.add_argument("--workers", type=int, required=True, help="concurrent cases")
    running.add_argument("--min-free-gib", type=float, default=MIN_AVAILABLE_GIB,
                         help="start a case only while the node has this much memory available")
    inner = commands.add_parser("case", help=argparse.SUPPRESS)
    inner.add_argument("checkout", type=Path)
    inner.add_argument("plan", type=Path)
    inner.add_argument("index", type=int)
    inner.add_argument("output", type=Path)
    report = commands.add_parser("summarize", help="tabulate the rows of a census")
    report.add_argument("plan", type=Path)
    args = parser.parse_args()
    {"plan": plan, "run": run, "case": case, "summarize": summarize}[args.command](args)


if __name__ == "__main__":
    main()
