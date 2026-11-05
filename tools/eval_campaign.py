"""Fixed multi-model campaign driver over the existing Eval runner.

prepare writes explicit run settings from one immutable input release. hosted
runs all hosted models on one node with a shared local spending account. Each
model runs joint and RTL-only sequentially, with up to `workers` tasks in flight.
No evaluator is created by rollout workers. Explicitly selected archived arms
can be baseline-scored while the remaining rollouts continue.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import signal
import socket
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import mkdtemp
from threading import Event

from behave.runtime.jobs import run_child as _child
from behave.runtime.jobs import wait_control
from behave.runtime.records import archive_run, archive_spending, write_json

DATASETS = ("verilogeval_v2", "rtllm_v2", "cvdp", "authored600_v1")


def prepare(root: Path, configuration: Path, repository: Path) -> dict:
    from tools.dataset_splits import current_path

    config = json.loads(configuration.read_text())
    if "split" not in config:
        config["split"] = str(current_path(repository=repository).relative_to(repository))
    inventory = json.loads((root / "release/inventory.json").read_text())
    release = json.loads((root / "release/release.json").read_text())
    selected = {row["task_id"] for row in inventory}
    split = repository / config["split"]
    dataset_order = (*DATASETS[:3], split.name)
    heldout = split.joinpath("eval.txt").read_text().split()
    external = split.joinpath("external_eval.txt").read_text().split()
    if selected != set(heldout + external) or selected & set(split.joinpath("train.txt").read_text().split()):
        raise ValueError("release must contain the held-out and external sets, without training tasks")
    datasets = {row["task_id"]: (row["category"] if row["category"] in DATASETS[:3] else split.name)
                for row in inventory}
    tasks = [task for dataset in dataset_order for task in sorted(selected) if datasets[task] == dataset]
    runs = []
    for model in config["models"]:
        for setting in ("joint", "rtl_only"):
            name = model["id"] + "_" + setting
            task_ids = tasks if setting == "joint" else heldout
            settings = {"task_objects": str(root / "release/objects"), "release": release,
                "tasks": task_ids, "setting": setting, "score_on_submit": False,
                "model": model["model"], "provider": model["provider"], "attempt": config["attempt"],
                "workers": config["workers"], "model_workers": config["workers"],
                "judge_workers": config["judge_workers"], "budget_usd": config["hosted_budget_usd"],
                "maximum_input_tokens": model.get("maximum_input_tokens", config["attempt"]["context_tokens"]),
                "credentials": str(repository / config["credentials"]),
                **{key: model[key] for key in ("credential_key", "billing_tier", "generation_parameters") if key in model}}
            if model["provider"] != "sglang":
                settings["prices"] = {"revision": "official-pricing-2026-09-16", "model": model["model"],
                                      "currency": "USD", **model["rates"]}
            directory = root / "runs" / name
            directory.mkdir(parents=True, exist_ok=False)
            write_json(directory / "settings.json", settings)
            runs.append({"name": name, "model": model["id"], "setting": setting,
                         "provider": model["provider"], "tasks": len(task_ids)})
    manifest = {"configuration": config, "release": release, "datasets": datasets, "runs": runs,
                "task_count": len(selected), "rollouts": sum(row["tasks"] for row in runs),
                "dataset_order": dataset_order}
    write_json(root / "campaign.json", manifest)
    return manifest




def hosted(root: Path) -> int:
    """Mutable journals and the common ledger are node-local until all jobs exit."""
    from behave.agent.cpu_resources import CpuResources, cpu_job
    from behave.core.lifecycle import CleanupState
    from behave.core.process_limits import configure_worker_process_limit

    manifest = json.loads((root / "campaign.json").read_text())
    state_path = root / "hosted_runtime.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["state"] == "archived":
            return 0
        raise ValueError(f"existing local run requires recovery: {state['local_directory']}")
    limits = configure_worker_process_limit(manifest["configuration"].get("process_limit"))
    local = Path(mkdtemp(prefix="behave-hosted-campaign-", dir="/tmp"))
    local.chmod(0o700)
    state = {"state": "running", "host": socket.gethostname(), "local_directory": str(local),
             "process_limit": limits}
    write_json(state_path, state)
    shutil.copytree(root / "release", local / "release")
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    models = [model for model in manifest["configuration"]["models"] if model["provider"] != "sglang"]
    source = Path(__file__).resolve().parents[1]
    lanes = CpuResources(len(models))

    def execute_model(model):
        codes = []
        checked = False
        for row in manifest["runs"]:
            if row["model"] != model["id"] or stopped.is_set():
                continue
            final = root / "runs" / row["name"]
            destination = local / row["name"]
            destination.mkdir()
            settings = json.loads((final / "settings.json").read_text())
            settings.update(task_objects=str(local / "release/objects"),
                            spending_directory=str(local / "spending"), spending_account=root.name)
            write_json(destination / "settings.json", settings)
            write_json(final / "runtime.json", {**state, "local_directory": str(destination)})
            if not checked:
                # One real transport round trip before dispatching 50 tasks.
                # It uses the same native limits, tool declaration and ledger.
                # This is not a scored dataset attempt.
                from behave.agent.commands import shell_commands
                from behave.agent.model import ModelReply, ModelRequest
                from behave.agent.providers.hosted_worker import exchange
                from tools.eval_review_trial import endpoint_for

                endpoint = endpoint_for(destination, settings)
                request = ModelRequest(root.name + ":" + model["id"] + ":transport", 0, 0,
                    ({"role": "user", "content": "Call run_command with the command printf ready."},),
                    (shell_commands().tool().declaration(),), endpoint.model_policy(), None,
                    settings["attempt"]["context_tokens"])
                outcome = exchange(endpoint, request)
                write_json(destination / "transport_request.json", request.as_dict())
                write_json(destination / "transport_outcome.json", outcome.as_dict())
                (destination / "transport_provider_raw.json").write_bytes(outcome.raw)
                if not isinstance(outcome, ModelReply):
                    archive_run(destination, final)
                    write_json(final / "runtime.json", {**state, "state": "failed",
                        "local_directory": str(destination), "transport_error": outcome.code})
                    return [1]
                checked = True
            bootstrap = ("import sys; sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; "
                         "from pathlib import Path; from tools.eval_review_trial import _run_local; "
                         "raise SystemExit(_run_local(Path(sys.argv[1]),status_output=Path(sys.argv[2])))")
            code = _child([sys.executable, "-I", "-B", "-u", "-c", bootstrap,
                           str(source), str(source / "src"), str(destination), str(final)],
                          destination / "worker.log", stopped)
            codes.append(code)
            # Do not delete a failed run's local recovery state.
            archive_run(destination, final)
            runtime = {**state, "state": "archived" if code == 0 else "failed",
                       "local_directory": str(destination), "exit_code": code}
            write_json(final / "runtime.json", runtime)
        return codes

    def run_model(model):
        # Allocate a disjoint share to each independent coordinator. Its child
        # batch then admits actual CPU operations within that inherited share.
        with cpu_job(lanes, cancelled=stopped.is_set) as lease:
            result = execute_model(model)
            if all(code == 0 for code in result) and not stopped.is_set():
                lease.cleanup = CleanupState.COMPLETE
            return result

    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        codes = list(pool.map(run_model, models))
    archive_billing(root, local)
    state.update(state="archived" if all(code == 0 for group in codes for code in group) and not stopped.is_set()
                 else "failed", exit_codes=codes)
    write_json(state_path, state)
    # Keep the node-local tree available until its archive has been inspected.
    return 0 if state["state"] == "archived" else 1


def archive_billing(root: Path, local: Path):
    """Reconcile after the hosted workers sharing this ledger have exited."""
    from behave.agent.spending import SpendAccount, SpendPolicy
    config = json.loads((root / "campaign.json").read_text())["configuration"]
    account = SpendAccount(SpendPolicy(str(local / "spending"), root.name, "USD",
        config["hosted_budget_usd"], config["attempt"]["context_tokens"]))
    write_json(root / "billing.json", account.summary())
    archive_spending(local / "spending", root / "spending")


def repair_hosted(root: Path, failure_codes: tuple[str, ...]):
    """Retry specified pre-submission infrastructure failures with the same release."""
    from behave.eval.followup import load_outcomes, retry_wave, select_retries
    from tools.eval_review_trial import run as run_trial

    if not failure_codes:
        raise ValueError("repair-hosted requires explicit infrastructure failure codes")
    state = json.loads((root / "hosted_runtime.json").read_text())
    if state["state"] == "running" or state["host"] != socket.gethostname():
        raise ValueError("repair hosted runs only after they exit, on their owning node")
    manifest = json.loads((root / "campaign.json").read_text())
    repaired = []
    for row in manifest["runs"]:
        if row["provider"] == "sglang":
            continue
        directory = root / "runs" / row["name"]
        recover_archive(root, row["name"])
        # A different recovery class may already be running in this arm.
        # It is unrelated unless this original run needs the requested repair.
        if not select_retries(load_outcomes([directory]), failure_codes=failure_codes)["rollout"]:
            continue
        with retry_wave(directory, failure_codes=failure_codes) as pending:
            if pending is None:
                continue
            target, selected = pending
            code = run_trial(target, resume=True)
            repaired.append({"run": row["name"], "tasks": selected["rollout"], "exit_code": code})
            if code != 0:
                raise RuntimeError(f"infrastructure retry did not finish: {row['name']}")
    archive_billing(root, Path(state["local_directory"]))
    write_json(root / "infra_repair.json", {"failure_codes": failure_codes, "runs": repaired})
    return repaired


def supplement_hosted(root: Path, *, run_name: str | None = None):
    """Compatibility entry for the shared, replayable supplemental controller."""
    from behave.eval.followup import run_supplement as run

    return run(root, run_name=run_name, source=Path(__file__).resolve().parents[1])


def require_rollouts_finished(root: Path) -> list[Path]:
    """A missing/failed run is not a completed global rollout barrier."""
    manifest = json.loads((root / "campaign.json").read_text())
    directories = [root / "runs" / row["name"] for row in manifest["runs"]]
    pending = []
    from behave.eval.report import rollout_sources
    for directory in directories:
        for source in rollout_sources(directory):
            state = source / "runtime.json"
            if not state.exists() or json.loads(state.read_text())["state"] != "archived":
                pending.append(str(source.relative_to(root / "runs")))
    if pending:
        raise ValueError("rollouts are not finished: " + ", ".join(pending))
    return directories


def status(root: Path) -> dict:
    """Read only the small published status files, never the active NFS journal."""
    from behave.eval.report import rollout_sources
    manifest = json.loads((root / "campaign.json").read_text())
    runs = []
    for row in manifest["runs"]:
        directory = root / "runs" / row["name"]
        progress_file = directory / "progress.json"
        progress = json.loads(progress_file.read_text()) if progress_file.exists() else []
        runtime = directory / "runtime.json"
        counts = Counter(item["status"] for item in progress)
        counts["waiting"] += row["tasks"] - len(progress)
        runs.append({"name": row["name"], "tasks": row["tasks"],
                     "state": json.loads(runtime.read_text())["state"] if runtime.exists() else "waiting",
                     "settled_attempts": sum("evaluation_status" in item for item in progress),
                     "counts": dict(counts), "turns": sum(item["turns"] for item in progress)})
        retries = []
        for retry in rollout_sources(directory)[1:]:
            retry_state = retry / "runtime.json"
            selection = retry / "selection.json"
            selected = (json.loads(selection.read_text()) if selection.exists() else
                        json.loads((retry / "followup_preparation.json").read_text())["selection"])
            retries.append({"state": json.loads(retry_state.read_text())["state"]
                if retry_state.exists() else "waiting", "directory": str(retry),
                "tasks": len(selected["rollout"])})
        if retries:
            runs[-1]["infra_retry"] = retries[-1]
            runs[-1]["retry_waves"] = retries
    result = {"expected_rollouts": manifest["rollouts"], "runs": runs,
              "rollout_barrier_complete": all(row["state"] == "archived"
                  and all(retry["state"] == "archived" for retry in row.get("retry_waves", [])) for row in runs)}
    for path in sorted(root.glob("*_control/status.json")):
        phase = path.parent.name.removesuffix("_control")
        control = json.loads(path.read_text())
        result[phase] = {key: control[key] for key in ("state", "elapsed_s", "heartbeat_at", "exit_code")
                         if key in control}
    if (root / "billing.json").exists():
        result["billing"] = json.loads((root / "billing.json").read_text())
    if (root / "supplement.json").exists():
        result["budget_authorization"] = json.loads((root / "supplement.json").read_text())
        if (root / "supplement_billing.json").exists():
            result["supplement_billing"] = json.loads((root / "supplement_billing.json").read_text())
    return result


def cost(root: Path) -> dict:
    """Observe shared billing on its owning node, without taking its lock."""
    from behave.agent.spending import spending_snapshot

    runtime = json.loads((root / "hosted_runtime.json").read_text())
    original = spending_snapshot(Path(runtime["local_directory"]) / "spending")
    if not (root / "supplement_runtime.json").exists():
        return original
    from decimal import Decimal

    from behave.agent.billing import sum_amounts
    supplement = json.loads((root / "supplement_runtime.json").read_text())
    extra = spending_snapshot(Path(supplement["local_directory"]) / "spending")
    return {"accounts": {"original": original, "supplement": extra},
            **{key: str(sum_amounts(Decimal(row[key]) for row in (original, extra)))
               for key in ("limit", "spent", "reserved")}}


def recover_archive(root: Path, name: str) -> dict:
    """Accept a copied, sealed batch after a post-rollout worker failure."""
    from behave.core.records import thaw_json
    from behave.task_execution.batch import load_batch_report
    from behave.task_execution.run_store import RunStore

    manifest = json.loads((root / "campaign.json").read_text())
    run = next(row for row in manifest["runs"] if row["name"] == name)
    final = root / "runs" / name
    state = json.loads((final / "runtime.json").read_text())
    if state["state"] == "archived":
        return state
    if state["state"] != "failed" or state["host"] != socket.gethostname():
        raise ValueError("recover an exited failed run on its owning node")
    # The campaign writes this exit status only after copytree has returned.
    # A nonzero worker exit can be a final billing failure, not a failed copy.
    # Verify the existing archive without recopying the complete trace/CAS.
    original_exit_code = state["exit_code"]
    store = RunStore(final / "run")
    with store.owner():
        events = store.journal("report").load()
        if not events or events[-1].kind != "evaluation_finished":
            raise ValueError("rollouts are unfinished; archive recovery cannot rerun them")
        head, _ = store.seal("report")
        report = load_batch_report(store.objects, head)
        if report.summary["requested"] != run["tasks"]:
            raise ValueError("completed report differs from the campaign task count")
        write_json(final / "report.json", thaw_json(report.summary))
        state.update(state="archived", recovery="sealed-archive-accepted", original_exit_code=original_exit_code)
        write_json(final / "runtime.json", state)
    return state


def score_directories(root: Path, *, shard: int = 0, shards: int = 1, run_name: str | None = None,
                      wait_ready: bool = False) -> list[Path]:
    if not 0 <= shard < shards:
        raise ValueError("score shard must be in [0, shards)")
    if wait_ready and run_name is None:
        raise ValueError("waiting for scoring requires one explicit campaign run")
    manifest = json.loads((root / "campaign.json").read_text())
    if run_name is not None:
        from behave.core.runtime_defaults import JOB_HEARTBEAT_SECONDS
        from behave.eval.report import rollout_sources
        if run_name not in {row["name"] for row in manifest["runs"]}:
            raise ValueError("scoring run must belong to this campaign")
        directory = root / "runs" / run_name
        wake = Event()
        announced = None
        while True:
            for source in rollout_sources(directory):
                runtime = source / "runtime.json"
                if not runtime.exists() or json.loads(runtime.read_text())["state"] != "archived":
                    break
            else:
                return [directory]
            if not wait_ready:
                raise ValueError(f"selected rollout is not finished: {source.name}")
            if announced != source.name:
                print(f"Waiting for rollout archive: {source.name}", flush=True)
                announced = source.name
            wake.wait(JOB_HEARTBEAT_SECONDS)
    directories = require_rollouts_finished(root)
    models = [model["id"] for model in manifest["configuration"]["models"]]
    return [directory for directory, run in zip(directories, manifest["runs"], strict=True)
            if models.index(run["model"]) % shards == shard]


def score(root: Path, *, shard: int = 0, shards: int = 1, run_name: str | None = None):
    from dataclasses import replace

    from behave.agent.cpu_resources import CpuResources
    from behave.eval.report import rollout_sources
    from behave.eval.scoring import score_review as score_run
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy
    directories = score_directories(root, shard=shard, shards=shards, run_name=run_name)
    policy = replace(JudgePolicy.from_bytes((root / "release/judge_policy.json").read_bytes()),
                     command_version=api.COMMAND_VERSION, authority_version=api.RESULT_VERSION,
                     search_seconds=0, analysis=None)
    policy_path = root / "snapshots/score/baseline_policy.json"
    if policy_path.exists() and policy_path.read_bytes() != policy.to_bytes():
        raise ValueError("campaign baseline policy already fixed to a different evaluator")
    if not policy_path.exists():
        from behave.core.storage import install_exclusive
        install_exclusive(policy_path.parent, policy_path.name, policy.to_bytes())
    sources = [source for directory in directories for source in rollout_sources(directory)]
    worker_counts = {source: json.loads((source / "settings.json").read_text())["judge_workers"]
                     for source in sources}
    if not sources:
        return
    cpu = CpuResources(max(worker_counts.values()))
    try:
        for source in sources:
            saved = source / "run/judgments/baseline/runtime.json"
            if saved.exists() and json.loads(saved.read_text())["state"] == "archived":
                print(f"Already baseline-scored: {source.name}; no evaluator dispatched.", flush=True)
                continue
            runtime = json.loads((source / "runtime.json").read_text())
            local = Path(runtime["local_directory"])
            stage_from = (local if runtime["host"] == socket.gethostname() and local.is_dir()
                          and not (source / "run/judgments").exists() else source)
            score_run(source, "baseline", cpu_resources=cpu, workers=worker_counts[source],
                      judge_policy=policy_path, stage_from=stage_from)
    finally:
        label = run_name or f"{shard}_of_{shards}"
        write_json(root / f"scoring_cpu_{label}.json", cpu.snapshot())


def report(root: Path):
    from behave.eval.report import summarize_campaign as summarize
    from behave.eval.report import table1, task_rows
    manifest = json.loads((root / "campaign.json").read_text())
    rows = task_rows(root)
    dataset_order = tuple(manifest.get("dataset_order", DATASETS))
    summary = summarize(rows, dataset_order=dataset_order)
    destination = root / "results"
    write_json(destination / "tasks.json", rows)
    write_json(destination / "summary.json", summary)
    fields = ("model", "setting", "dataset", "task_id", "rollout_status", "infra_retry", "rtl_status", "rtl_score",
              "rtl_baseline", "rtl_reason", "behavior_status", "behavior_score", "behavior_baseline", "behavior_reason", "tb_status",
              "coverage", "coverage_counts", "trace")
    with (destination / "tasks.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flattened = {key: row[key] for key in fields if key in row}
            flattened["coverage_counts"] = json.dumps(row["coverage_counts"])
            for artifact in ("rtl", "behavior"):
                flattened.update({artifact + "_status": row[artifact]["status"],
                                  artifact + "_score": row[artifact]["score"],
                                  artifact + "_reason": json.dumps(row[artifact].get("reason")),
                                  artifact + "_baseline": json.dumps(row[artifact]["baseline"])})
            writer.writerow(flattened)
    (destination / "table1_rows.tex").write_text(table1(summary, manifest["configuration"]["models"],
                                                      dataset_order=dataset_order))
    return {"tasks": len(rows), "directory": str(destination), "read_only_evaluation": True}




def finish(root: Path, hosted_allocation: int, gpu_allocation: int, *,
           hosted_failure_codes: tuple[str, ...] = (), gpu_failure_codes: tuple[str, ...] = ()):
    """Join rollout jobs, recover selected interruptions, then score and report.

    Reopening this coordinator rejoins existing phase supervisors. It never
    interprets an unsuccessful phase as permission for another model sample.
    """
    from tools.eval_gpu_campaign import submit as submit_gpu

    def publish(phase, **details):
        row = {"phase": phase, **details}
        write_json(root / "finish_state.json", row)
        print(json.dumps(row), flush=True)

    def lane(name, allocation, codes):
        handoff = name == "gpu" and (root / "gpu_handoff.json").exists()
        wait_control(root / (name + "_control"), allow_failure=bool(codes) or handoff)
        if handoff:
            wait_control(root / "gpu-drain_control")
            wait_control(root / "gpu-extra_control")
        if codes:
            phase = "repair-" + name
            control = root / (phase + "_control")
            if not (control / "launch.json").exists():
                if name == "gpu":
                    submit_gpu(root, allocation, failure_codes=codes)
                else:
                    submit(root, allocation, phase=phase, failure_codes=codes)
            wait_control(control)
        if name == "hosted" and (root / "supplement.json").exists():
            recovery = root / "supplement-hosted-recovery_control"
            wait_control(recovery if (recovery / "launch.json").exists()
                         else root / "supplement-hosted_control")

    try:
        publish("rollouts", scoring_started=any(root.glob("score_ready_*_control/launch.json")))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(lane, "hosted", hosted_allocation, hosted_failure_codes),
                       pool.submit(lane, "gpu", gpu_allocation, gpu_failure_codes)]
            for future in futures:
                future.result()
        require_rollouts_finished(root)
        for control in sorted(root.glob("score_ready_*_control")):
            wait_control(control)
        # The current evaluator is frozen exactly once, after the global
        # rollout barrier. Both scoring shards receive this same snapshot.
        snapshot = root / "snapshots/score/checkout"
        if not snapshot.exists():
            freeze_phase(Path(__file__).resolve().parents[1], root, snapshot, "score")
        publish("baseline", scoring_started=True)
        controls = []
        for shard, allocation in enumerate((hosted_allocation, gpu_allocation)):
            control = root / f"score_{shard}_of_2_control"
            if not (control / "launch.json").exists():
                submit(root, allocation, phase="score", shard=shard, shards=2)
            controls.append(control)
        for control in controls:
            wait_control(control)
        result = report(root)
        publish("reported", **result)
        return result
    except Exception as error:
        publish("interrupted", error=f"{type(error).__name__}: {error}")
        raise


def audit(root: Path, name: str):
    from behave.eval.report import audit_traces
    result = audit_traces(root / "runs" / name)
    write_json(root / "results/trace_audits" / (name + ".json"), result)
    return {key: value for key, value in result.items() if key != "tasks"}


def probe_tool(root: Path, name: str, tool: str, arguments: dict):
    """One billed tool-format diagnostic; never execute its proposed call."""
    from behave.agent.model import ModelReply, ModelRequest
    from behave.agent.providers.hosted_worker import exchange
    from tools.eval_review_trial import endpoint_for

    directory = root / "runs" / name
    runtime = json.loads((directory / "runtime.json").read_text())
    if runtime["host"] != socket.gethostname():
        raise ValueError("run the diagnostic on the owning node")
    local = Path(runtime["local_directory"])
    settings = json.loads((local / "settings.json").read_text())
    declarations = json.loads(next(iter(sorted(local.glob("traces/*/tools.json")))).read_text())
    declaration = next(item for item in declarations if item["name"] == tool)
    target = local.parent / (name + "_" + tool + "_transport")
    target.mkdir(exist_ok=True)
    endpoint = endpoint_for(target, settings)
    request = ModelRequest(root.name + ":" + name + ":tool-transport:" + tool, 0, 0,
        ({"role": "user", "content": "Call " + tool + " with exactly these arguments: " + json.dumps(arguments)},),
        (declaration,), endpoint.model_policy(), None, settings["attempt"]["context_tokens"])
    outcome = exchange(endpoint, request)
    write_json(target / "request.json", request.as_dict())
    write_json(target / "outcome.json", outcome.as_dict())
    (target / "provider_raw.json").write_bytes(outcome.raw)
    shutil.copytree(target, root / "transport_checks" / target.name, dirs_exist_ok=True)
    return {"model": name, "tool": tool,
            "calls": [call.as_dict() for call in outcome.calls] if isinstance(outcome, ModelReply) else [],
            "error": None if isinstance(outcome, ModelReply) else outcome.code,
            "charged": outcome.charge.as_dict() if outcome.charge is not None else None}


def freeze_phase(source: Path, root: Path, snapshot: Path, phase: str):
    """Freeze the code for one phase; repair and supplement phases reuse the original snapshot unchanged."""
    from behave.runtime.snapshots import freeze

    tools = ("tools/eval_campaign.py", "tools/eval_review_retry.py")
    if phase in ("gpu", "repair-gpu", "gpu-extra"):
        tools += ("tools/eval_gpu_campaign.py",)
    if phase not in ("repair-hosted", "repair-gpu", "supplement-hosted", "gpu-extra"):
        return freeze(source, snapshot, extra_files=(*tools, "tools/eval_review_trial.py"))
    # A retry must not pick up newly edited prompts, guides, public libraries
    # or a different solver implementation.
    original = root / ("gpu_snapshot" if phase in ("repair-gpu", "gpu-extra") else "runtime_snapshot")
    previous = json.loads((original.parent / "snapshot.json").read_text())
    return freeze(original, snapshot, extra_files=tuple(previous["extra_files"]))


def submit(root: Path, allocation: int, *, phase: str, shard: int = 0, shards: int = 1,
           failure_codes: tuple[str, ...] = (), run_name: str | None = None, cpus: int = 180,
           after: Path | None = None, wait_ready: bool = False):
    from behave.runtime.jobs import submit as detached
    from behave.runtime.snapshots import verify

    source = Path(__file__).resolve().parents[1]
    if wait_ready:
        if phase != "score" or run_name is None:
            raise ValueError("--wait-ready requires --phase score and --run")
        manifest = json.loads((root / "campaign.json").read_text())
        if run_name not in {row["name"] for row in manifest["runs"]}:
            raise ValueError("scoring run must belong to this campaign")
    elif phase == "score":
        score_directories(root, shard=shard, shards=shards, run_name=run_name)
    if phase == "repair-hosted" and not failure_codes:
        raise ValueError("repair-hosted requires explicit infrastructure failure codes")
    # freeze() stores its manifest next to the checkout. Each phase needs its
    # own parent, and judging must not silently reuse the rollout's Sim build.
    snapshot = root / "snapshots" / phase / "checkout"
    if not snapshot.exists():
        freeze_phase(source, root, snapshot, phase)
    else:
        verify(snapshot)
    bootstrap = ("import sys; sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; "
                 "from tools.eval_campaign import staged; raise SystemExit(staged(*sys.argv[1:]))")
    # Scheduler allocation lifetime, not an agent attempt timeout.
    argv = ["srun", f"--jobid={allocation}", "--overlap", "--exact", "--nodes=1", "--ntasks=1",
            f"--cpus-per-task={cpus}", "--gres=none", "--kill-on-bad-exit=1", sys.executable,
            "-I", "-B", "-u", "-c", bootstrap, str(snapshot), str(snapshot / "src"), str(snapshot), str(root), phase,
            str(shard), str(shards), json.dumps(failure_codes), run_name or ""]
    if wait_ready:
        waiter = ("import os,sys; from pathlib import Path; "
                  "from tools.eval_campaign import score_directories; "
                  "score_directories(Path(sys.argv[1]),run_name=sys.argv[2],wait_ready=True); "
                  "os.execvp(sys.argv[3],sys.argv[3:])")
        argv = [sys.executable, "-B", "-u", "-c", waiter, str(root.resolve()), run_name, *argv]
    if after is not None:
        after = after.resolve()
        waiter = ("import os,sys; from pathlib import Path; "
                  "from behave.runtime.jobs import wait_control; "
                  "wait_control(Path(sys.argv[1])); os.execvp(sys.argv[2],sys.argv[2:])")
        argv = [sys.executable, "-B", "-u", "-c", waiter, str(after), *argv]
    label = ("score_ready_" + run_name if run_name else f"score_{shard}_of_{shards}") if phase == "score" else phase
    return detached(root / (label + "_control"), argv, cwd=Path(__file__).resolve().parents[1], wall_seconds=259200,
                    metadata={"phase": phase, "campaign": root.name, "allocation": allocation,
                              "shard": shard, "shards": shards, "run": run_name, "cpus": cpus,
                              "after": None if after is None else str(after),
                              **({"wait_ready": True} if wait_ready else {})})


def staged(snapshot, root, phase, shard="0", shards="1", failure_codes="[]", run_name=""):
    from behave.runtime.snapshots import stage
    local = Path(mkdtemp(prefix="behave-campaign-code-", dir="/tmp")) / "checkout"
    stage(Path(snapshot), local)
    # Execute rather than import the already-loaded dispatcher, so every worker
    # uses the verified local source tree, not the NFS snapshot.
    os.execv(sys.executable, [sys.executable, "-I", "-B", "-u", "-c",
        "import sys; sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)]; from tools.eval_campaign import main; main()",
        str(local), str(local / "src"), phase, root, "--shard", shard, "--shards", shards,
        *[item for code in json.loads(failure_codes) for item in ("--failure-code", code)],
        *(["--run", run_name] if run_name else [])])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "submit", "hosted", "repair-hosted", "supplement-hosted", "score", "report", "status", "cost", "recover-archive", "audit", "probe-tool", "finish"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--configuration", type=Path, help="model and resource configuration required for prepare")
    parser.add_argument("--allocation", type=int)
    parser.add_argument("--cpus", type=int, default=180, help="CPU allocation for the submitted node job")
    parser.add_argument("--after", type=Path, help="wait for this existing supervisor before starting the node job")
    parser.add_argument("--wait-ready", action="store_true",
                        help="queue one scoring arm until its rollout archives are ready, without reserving a node")
    parser.add_argument("--phase", choices=("hosted", "repair-hosted", "supplement-hosted", "score"), default="hosted")
    parser.add_argument("--failure-code", action="append", default=[])
    parser.add_argument("--hosted-allocation", type=int)
    parser.add_argument("--gpu-allocation", type=int)
    parser.add_argument("--hosted-failure-code", action="append", default=[])
    parser.add_argument("--gpu-failure-code", action="append", default=[])
    parser.add_argument("--run", help="one campaign arm for scoring, recovery, or an authorized supplement")
    parser.add_argument("--tool", help="declared tool name for a transport diagnostic")
    parser.add_argument("--arguments", type=json.loads, help="expected JSON arguments for a tool diagnostic")
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        if args.configuration is None:
            parser.error("prepare requires --configuration")
        result = prepare(root, args.configuration, Path(__file__).resolve().parents[1])
        print(json.dumps({"tasks": result["task_count"], "rollouts": result["rollouts"]}))
    elif args.action == "submit":
        if args.allocation is None:
            parser.error("submit requires --allocation")
        print(json.dumps(submit(root, args.allocation, phase=args.phase, shard=args.shard, shards=args.shards,
                               failure_codes=tuple(args.failure_code), run_name=args.run,
                               cpus=args.cpus, after=args.after, wait_ready=args.wait_ready), indent=2))
    elif args.action == "hosted":
        raise SystemExit(hosted(root))
    elif args.action == "repair-hosted":
        print(json.dumps(repair_hosted(root, tuple(args.failure_code)), indent=2))
    elif args.action == "supplement-hosted":
        print(json.dumps(supplement_hosted(root, run_name=args.run), indent=2))
    elif args.action == "status":
        print(json.dumps(status(root), indent=2))
    elif args.action == "cost":
        print(json.dumps(cost(root), indent=2))
    elif args.action == "recover-archive":
        if args.run is None:
            parser.error("recover-archive requires --run")
        print(json.dumps(recover_archive(root, args.run), indent=2))
    elif args.action == "audit":
        if args.run is None:
            parser.error("audit requires --run")
        print(json.dumps(audit(root, args.run), indent=2))
    elif args.action == "probe-tool":
        if args.run is None or args.tool is None or not isinstance(args.arguments, dict):
            parser.error("probe-tool requires --run, --tool and JSON object --arguments")
        print(json.dumps(probe_tool(root, args.run, args.tool, args.arguments), indent=2))
    elif args.action == "report":
        print(json.dumps(report(root), indent=2))
    elif args.action == "finish":
        if args.hosted_allocation is None or args.gpu_allocation is None:
            parser.error("finish requires --hosted-allocation and --gpu-allocation")
        finish(root, args.hosted_allocation, args.gpu_allocation,
               hosted_failure_codes=tuple(args.hosted_failure_code), gpu_failure_codes=tuple(args.gpu_failure_code))
    else:
        score(root, shard=args.shard, shards=args.shards, run_name=args.run)


if __name__ == "__main__":
    main()
