"""Shared PPA campaign execution, independent of cluster submission.

Prepared plans run the existing search/compare CLI with a common final suite.
Deployment owns source staging and allocation; this module retains service
receipts, recovery fences, shared CPU admission and per-task evidence.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from pathlib import Path

from behave.core.paths import repo_root
from behave.runtime.records import write_json

from .campaign_inputs import freeze_plan, publish
from .campaign_recovery import recover_final, recovery_binding, selected_search
from .campaign_report import collect_final, plot
from .planning import campaign_arguments as common_arguments
from .planning import campaign_slots as slots
from .planning import validate_campaign as validate_plan
from .planning import validate_campaign_group as validate_group


def service(snapshot, root, arguments, seconds, *, cpu_resources=None):
    """One bounded trusted CLI; child tools retain their own candidate isolation."""
    from behave.core import runtime_defaults as defaults
    from behave.core.paths import eda_root
    from behave.isolation.runtime_environment import runtime_environment
    from behave.isolation.service import run_service

    root.mkdir(parents=True, exist_ok=True)
    receipt = root / "service.json"
    if receipt.exists():
        return json.loads(receipt.read_text())
    # A requested-without-receipt process is ambiguous. Never pay to redispatch it.
    if (root / "requested.json").exists():
        raise RuntimeError(f"unsettled campaign service; inspect before resuming: {root}")
    publish(root / "requested.json", {"arguments": arguments, "seconds": seconds})
    bootstrap = service_bootstrap()
    with ExitStack() as resources:
        count = 0 if cpu_resources is None else 1
        if count and arguments and arguments[0] == "compare":
            from behave.ppa.records import finalized_report

            candidates = set()
            for index, arg in enumerate(arguments):
                if arg == "--search":
                    _, _, report = finalized_report(Path(arguments[index + 2]))
                    candidates.update(row["candidate"] for row in report["candidates"])
            count = max(1, min(len(candidates), cpu_resources.snapshot()["slots"]))
        relays = [resources.enter_context(cpu_resources.relay()) for _ in range(count)]
        cpu_args = tuple(arg for relay in relays for arg in ("--cpu-endpoint", relay.endpoint))
        command = (sys.executable, "-I", "-B", "-c", bootstrap, str(snapshot / "src"),
                   *arguments, *cpu_args)
        result = run_service(command, cwd=root, network="host",
            deadline=None if seconds is None else time.monotonic() + seconds,
            output_bytes=defaults.JOB_OUTPUT_BYTES,
            environment={"PATH": f"{Path(sys.executable).parent}:{eda_root() / 'bin'}:/usr/bin:/bin",
                         "PYTHONUNBUFFERED": "1", "TMPDIR": "/tmp",
                         "BEHAVE_RUNTIME_DIR": str(runtime_environment().directory),
                         "BEHAVE_EDA_ROOT": str(eda_root())})
        for relay in relays:
            relay.worker_cleanup = result.cleanup_state
    (root / "stdout.log").write_bytes(result.stdout)
    (root / "stderr.log").write_bytes(result.stderr)
    record = {"status": result.status, "returncode": result.returncode,
              "cleanup": result.cleanup_state.value, "elapsed_seconds": result.elapsed_seconds,
              "ok": result.status == "exited" and result.returncode == 0 and result.cleanup_state.quiescent}
    publish(receipt, record)
    if not result.cleanup_state.quiescent:
        raise RuntimeError(f"campaign child cleanup unconfirmed: {root}")
    return record


def service_bootstrap():
    return """import runpy, sys
sys.path.insert(0, sys.argv.pop(1))
runpy.run_module('behave.ppa', run_name='__main__')
"""


def run_phase(items, parallelism, action):
    results = []
    with ThreadPoolExecutor(max_workers=parallelism) as workers:
        futures = {workers.submit(action, item): item for item in items}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result), flush=True)
    return sorted(results, key=lambda row: row["label"])


def await_preflight(root):
    """Optional private no-model gate. Its RTL never enters a search archive."""
    gate_file = root / "preflight-gate.json"
    if not gate_file.exists():
        return
    gate = json.loads(gate_file.read_text())
    directory = Path(gate["directory"])
    deadline = time.monotonic() + gate.get("wait_seconds", 5400)
    while True:
        receipt = directory / "results/preflight.json"
        if receipt.exists():
            report = json.loads(receipt.read_text())
            row = report["candidate"]
            history = row["history"]
            checks = [h["result"].get("score") for h in history if h["mode"] in {"check", "final"}]
            checked_modes = {h["mode"] for h in history} & {"check", "final"}
            if (report.get("excluded_from_comparison") is not True
                    or checked_modes != {"check", "final"}
                    or any(score != 1 for score in checks)
                    or row.get("performance", {}).get("source") != "rtl"
                    or row.get("performance", {}).get("score") != 1
                    or row.get("performance", {}).get("time_ns") is None
                    or row.get("eda", {}).get("status") != "measured"
                    or row.get("eda", {}).get("area_um2") is None):
                raise RuntimeError("private PPA preflight failed; no model dispatch authorized by this gate")
            from behave.core.artifacts import BlobRef
            publish(root / "preflight-passed.json", {"receipt": BlobRef.of(receipt.read_bytes()).as_dict(),
                "native": report["native"], "excluded_from_comparison": True})
            print('{"phase":"preflight-passed","model_dispatch_enabled":true}', flush=True)
            return
        status_file = directory / "supervision/status.json"
        status = json.loads(status_file.read_text()) if status_file.exists() else {}
        if status.get("state") in {"failed", "completed", "timeout", "timed-out", "launch-error", "cancelled"} or time.monotonic() >= deadline:
            raise RuntimeError("private PPA preflight did not produce a successful receipt; no model dispatched")
        time.sleep(5)


def run(root, *, snapshot=None, cpu_resources=None, final_only=False, remeasure_final=False,
        refresh_native=False):
    if (remeasure_final or refresh_native) and not final_only:
        raise ValueError("explicit final remeasurement requires a final-only campaign")
    from behave.core.artifacts import BlobRef
    from behave.ppa.records import finalized_report, read_document, union_pool

    snapshot = repo_root() if snapshot is None else snapshot
    plan = validate_plan(json.loads((root / "plan.json").read_text()))
    from behave.agent.cpu_resources import CpuResources
    if cpu_resources is None:
        cpu_resources = CpuResources(plan.get("cpu_workers", plan["parallelism"]), cpus=plan.get("cpus"))
    elif plan.get("cpus") is not None or plan.get("cpu_workers") is not None:
        raise ValueError("shared CPU scheduling belongs to the campaign group, not individual plans")
    expected = json.loads((root / "inputs.json").read_text())
    for path, digest in expected.items():
        if BlobRef.of(Path(path).read_bytes()).sha256 != digest:
            raise ValueError(f"campaign input changed before dispatch: {path}")
    await_preflight(root)
    common = common_arguments(plan)
    binding = json.loads((root / "recovery.json").read_text()) if (root / "recovery.json").exists() else None
    if binding is not None and recovery_binding(plan, Path(binding["directory"])) != binding:
        raise ValueError("campaign recovery binding changed")

    def search(item):
        method, replica, directory = item
        label = f"{method['name']}/r{replica:02d}"
        prior = selected_search(binding, label, directory, final_only=final_only)
        if prior is not None:
            return prior
        experiment = json.loads(Path(plan["experiment"]).read_text())
        from behave.ppa.prompting import bind_feedback, bind_objective

        prompt = bind_objective(Path(method["prompt"]).read_text(), experiment, replica)
        prompt = bind_feedback(prompt, sim_feedback=method.get("sim_feedback", True))
        from behave.core.storage import install_exclusive
        prompt_file = directory / "prompt.md"
        data = prompt.encode()
        if not install_exclusive(directory, prompt_file.name, data) and prompt_file.read_bytes() != data:
            raise ValueError("replicate prompt changed")
        outcome = service(snapshot, directory / "search-service", ["search", *common,
            "--provider", method["provider"], "--prompt", str(prompt_file),
            "--turns", str(plan["turns"]),
            "--behavior-source", method["behavior_source"],
            "--sim-feedback", "enabled" if method.get("sim_feedback", True) else "disabled",
            *([] if plan["request_seconds"] is None else ["--request-seconds", str(plan["request_seconds"])]),
            "--output", str(directory / "search")], plan["replicate_seconds"], cpu_resources=cpu_resources)
        return {"label": label, "phase": "search", **outcome, "search_directory": str(directory / "search")}

    searches = run_phase(slots(plan, root), plan["parallelism"], search)
    publish(root / "searches.json", searches)
    pool = union_pool(json.loads(Path(plan["retained_pool"]).read_text())["stimuli"])
    for item in searches:
        if item["ok"]:
            run, _, report = finalized_report(Path(item["search_directory"]))
            pool = union_pool(pool, read_document(run.objects, BlobRef.from_dict(report["pool_ref"])))
    # No-Sim methods must receive an independent correctness baseline even when
    # no development comparison succeeded. Use Sim's public generator, not RTL.
    from dataclasses import replace

    from behave.core.artifacts import DirectoryReader
    from behave.data.published import load_published_task
    from behave.ppa.finalize import final_baseline
    from behave.ppa.records import release_reference
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.task_execution.sim_tool import ComparisonToolInputs

    task = load_published_task(DirectoryReader(plan["objects"]), release_reference(plan["release"]), plan["task"]).judge
    policy_path = plan.get("final_judge_policy") or plan.get("judge_policy")
    policy = (JudgePolicy.from_bytes(Path(policy_path).read_bytes()).to_bytes()
              if policy_path else ComparisonToolInputs.baseline(task).judge_policy)
    baseline = final_baseline(replace(task, judge_policy=policy))
    pool = union_pool(pool, (stimulus.as_dict() for stimulus in baseline.stimuli))
    publish(root / "common-pool.json", {"stimuli": list(pool)})

    native_identity = None
    if refresh_native and not remeasure_final:
        from behave.ppa.worker import NativePPA

        native_identity = NativePPA(liberty=Path(plan["liberty"]), sta=Path(plan["sta"]),
                                    cpu_resources=cpu_resources).identity

    def final(item):
        if not item["ok"]:
            return {**item, "phase": "final", "status": "search-not-completed"}
        directory = root / "runs" / item["label"]
        reuse = []
        prior = None if remeasure_final else recover_final(binding, item["label"], pool,
                                                          native_identity=native_identity)
        if prior is not None:
            reuse = ["--reuse-final", str(prior)]
        outcome = service(snapshot, directory / "final-service", ["compare", *common_arguments(plan, final=True),
            "--search", item["label"], item["search_directory"],
            "--retained-pool", str(root / "common-pool.json"),
            *reuse,
            "--output", str(directory / "final")], plan["final_seconds"], cpu_resources=cpu_resources)
        return {"label": item["label"], "phase": "final", **outcome}

    outcomes = run_phase(searches, plan["parallelism"], final)
    publish(root / "finals.json", outcomes)
    report = collect_final(plan, root, outcomes, pool)
    # Search-summary costs alone omit interrupted attempts. Account totals keep
    # all known charges and uncertain reservations, including the original run.
    from behave.agent.spending import SpendAccount, SpendPolicy
    report["spending_accounts"] = {}
    for method in plan["methods"]:
        spending = json.loads(Path(method["provider"]).read_text()).get("spending")
        if spending is not None:
            report["spending_accounts"][method["name"]] = SpendAccount(SpendPolicy.from_dict(spending)).summary()
    if binding is not None:
        report["recovery"] = {"binding": binding, "searches": searches}
    publish(root / "report.json", report)
    plot(report, root / "frontier")
    return 0 if all(o["ok"] for o in outcomes) else 1


def export_traces(root):
    """Use the same lossless transcript exporter as Eval, on PPA's agent journal."""
    from behave.agent.trace_export import export_candidate

    rows = {}
    for search in sorted(root.glob("runs/*/r*/search")):
        label = search.parent.relative_to(root / "runs").as_posix()
        try:
            rows[label] = export_candidate(search, root / "traces" / label, journal="agent")
        except Exception as error:  # noqa: BLE001 -- retain failed optional exports alongside raw journals
            # Retain raw journals even when a readable export fails.
            rows[label] = {"status": "export-failed", "error": f"{type(error).__name__}: {error}"}
    write_json(root / "trace_index.json", rows)
    return rows


def run_group(root, *, snapshot, group):
    """Keep each task's common final suite; share CPU admission across tasks."""
    from behave.agent.cpu_resources import CpuResources

    group = validate_group(group)
    resources = CpuResources(group["cpu_workers"])
    for name, source in group["plans"].items():
        plan = json.loads(Path(source).read_text())
        if plan.get("cpus") is not None or plan.get("cpu_workers") is not None:
            raise ValueError("group plans cannot override shared CPU scheduling")
        freeze_plan(plan, root / "campaigns" / name)
        binding = Path(source).parent / 'recovery.json'
        if binding.exists():
            publish(root / 'campaigns' / name / 'recovery.json', json.loads(binding.read_text()))
    publish(root / "group.json", group)
    write_json(root / "cpu-resources.json", resources.snapshot())

    def execute(name):
        directory = root / "campaigns" / name
        try:
            options = {key: group[key] for key in ("final_only", "remeasure_final", "refresh_native") if key in group}
            if name in group.get("remeasure_tasks", ()):
                options["remeasure_final"] = True
            code = run(directory, snapshot=snapshot, cpu_resources=resources,
                       **options)
            return {"name": name, "returncode": code}
        except Exception as error:  # noqa: BLE001 -- settle each task independently and retain its failure
            # A failed task retains its evidence while other tasks keep running.
            return {"name": name, "returncode": 1,
                    "error": f"{type(error).__name__}: {error}"}
        finally:
            export_traces(directory)
            write_json(root / "cpu-resources.json", resources.snapshot())

    with ThreadPoolExecutor(max_workers=len(group["plans"])) as executor:
        futures = [executor.submit(execute, name) for name in group["plans"]]
        results = [future.result() for future in as_completed(futures)]
    # Per-task writers can finish out of order; publish the final released pool
    # once all campaign owners have stopped using it.
    write_json(root / "cpu-resources.json", resources.snapshot())
    publish(root / "group-results.json", results)
    return int(any(row["returncode"] for row in results))
