"""Execute saved Eval settings through the maintained evaluation runner.

Task preparation, candidate identity, trace export and environment restoration
are shared by local and hosted operator entries. This adapter preserves their
recorded settings/report format; it is not another evaluator or simulator.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from threading import Event, Thread

from behave.agent import trace_export
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import canonical_json, thaw_json
from behave.runtime.records import command_scope, write_json
from behave.serving.lifecycle import close_sessions, start_sessions
from behave.task_execution.accounting import load_accounting_identity
from behave.task_execution.batch import BatchRecovery
from behave.task_execution.result import TaskResult

reasoning_blocks = trace_export.reasoning_blocks
export_candidate = trace_export.export_candidate
_export_events = trace_export._export_events


def solver_cases(settings):
    """Interleave settings in one candidate queue, sharing its worker budget."""
    if "cases" in settings:
        return tuple((case["task"], case["setting"]) for case in settings["cases"])
    selected = settings.get("solver_settings", [settings.get("setting", "joint")])
    return tuple((task, setting) for task in settings["tasks"] for setting in selected)


def trace_name(task, setting, *, mixed):
    return f"traces/{setting}/{task}/trace.md" if mixed else f"traces/{task}/trace.md"


def attempt_name(output, settings, task, setting, *, mixed):
    namespace = settings.get("attempt_namespace", output.name)
    return f"{namespace}-{setting}-{task}" if mixed else f"{namespace}-{task}"


def load_tasks(output, settings):
    """Use the same published task reader as Eval/Train; retain the exact inputs.

    ``task_objects`` locates the source store, ``release`` is its release BlobRef,
    and ``tasks`` lists task IDs in that release. Reference entries, dependencies
    and evaluation policies belong to the release, not this experiment launcher.
    """
    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.core.records import RecordError
    from behave.core.storage import DirectoryStore
    from behave.data.published import PublishedReleaseReader
    from behave.data.published.files import task_files

    reader = DirectoryReader(settings["task_objects"])
    release_ref = BlobRef.from_dict(settings["release"])
    published = PublishedReleaseReader(reader, release_ref)
    release = published.manifest
    objects = DirectoryStore(output / "inputs")
    objects.put(release.to_bytes())
    tasks, preparation = [], []
    for task_id in settings["tasks"]:
        row = {"task_id": task_id}
        try:
            task = published.load(task_id)
            manifest = task.manifest
            files = task_files(manifest)
            objects.publish(reader, (task.public.revision, release.policy,
                manifest.run_policy, manifest.judge_policy, *(file.content for file in files)))
            tasks.append(task.public)
            row.update(status="prepared", revision=task.public.revision.as_dict(),
                       run_policy=manifest.run_policy.as_dict(), judge_policy=manifest.judge_policy.as_dict(),
                       reference_entry=manifest.reference_behavior.entry_symbol)
        except (OSError, RecordError) as error:
            # Record an input/preparation failure, never fabricate a model score.
            row.update(status="preparation_failed", error=f"{type(error).__name__}: {error}",
                       traceback=traceback.format_exc())
        preparation.append(row)
        write_json(output / "preparation.json", preparation)
        print(json.dumps({k: row[k] for k in ("task_id", "status")}), flush=True)
    write_json(output / "release.json", release.as_dict())
    return objects, tuple(tasks)


class LiveTraceExporter:
    """Reuse unchanged exports within one observer, never across worker restarts.

    Authenticate only the tail of a live immutable journal. A completed batch
    case with a terminal solver trace needs no further reads. Drop that case's
    cached events, retaining just its small progress row. This is a display
    cache, not a source of execution results or an archive-completion witness.
    """

    def __init__(self):
        self._candidates = {}

    def export(self, directory, destination, *, completed=False):
        from behave.task_execution.run_store import RunStore

        directory, destination = Path(directory), Path(destination)
        key = (directory, destination)
        events, summary, settled = self._candidates.get(key, ((), None, False))
        if settled:
            return dict(summary)
        if not (directory / "solver").is_dir():
            return {"status": "waiting", "turns": 0, "tool_calls": 0}
        store = RunStore(directory)
        tail = store.journal("solver").load_after(events[-1] if events else None, verify_artifacts=False)
        if tail or summary is None:
            events = (*events, *tail)
            summary = _export_events(store, events, destination, markdown="trace.md")
        settled = completed and summary["status"] not in {"running", "waiting"}
        # Commit cache state only after a successful export. A failed write is
        # retried by the observer; it cannot hide newly committed events.
        self._candidates[key] = (() if settled else events, summary, settled)
        return dict(summary)


class LiveProgressExporter:
    """Read journal metadata only; never load cumulative prompts or render traces.

    The original journal and its content-addressed objects remain the complete
    archive. Human-readable exports are an explicit, offline operation through
    ``export_candidate`` rather than a prerequisite for releasing GPU services.
    """

    def __init__(self):
        self._candidates = {}

    def export(self, directory, destination=None, *, completed=False):
        from behave.task_execution.run_store import RunStore

        directory = Path(directory)
        head, summary, settled = self._candidates.get(directory, (
            None, {"status": "waiting", "turns": 0, "tool_calls": 0, "events": 0}, False))
        if settled or not (directory / "solver").is_dir():
            return dict(summary)
        tail = RunStore(directory).journal("solver").load_after(head, verify_artifacts=False)
        summary = dict(summary)
        for event in tail:
            summary["events"] += 1
            if summary["status"] == "waiting":
                summary["status"] = "running"
            if event.kind == "model_settled" and event.payload.get("outcome") == "reply":
                summary["turns"] += 1
            elif event.kind == "tool_requested":
                summary["tool_calls"] += 1
            elif event.kind in {"terminal", "interrupted"}:
                summary["status"] = event.payload.get("reason", event.payload.get("code", event.kind))
        self._candidates[directory] = (tail[-1] if tail else head, summary, completed)
        return dict(summary)


def final_progress(inventory, observed, summary):
    """Complete display rows from the producer report, without reading traces."""
    cached = {row["run_key"]: row for row in observed}
    outcomes = {row["run_key"]: row["status"] for row in summary["candidates"]}
    rows = []
    for item in inventory:
        row = {**item, **cached.get(item["run_key"], {})}
        if item["run_key"] in outcomes:
            row["evaluation_status"] = outcomes[item["run_key"]]
            if row.get("status", "waiting") in {"waiting", "running"}:
                row["status"] = outcomes[item["run_key"]]
        rows.append(row)
    return rows


def stop_progress_observer(observer, stopped):
    """An optional display reader must not hold GPU cleanup behind slow I/O."""
    stopped.set()
    observer.join(timeout=2)
    return not observer.is_alive()


def endpoint_for(output, settings):
    """Use this experiment's explicit model and price snapshot."""
    if settings.get("provider") == "sglang":
        from behave.agent.providers.local_chat import LocalChatEndpoint
        return LocalChatEndpoint.from_dict(settings["endpoint"])
    from behave.agent.billing import TokenPrices
    from behave.agent.providers.credentials import CredentialRef
    from behave.agent.providers.hosted import HostedEndpoint
    from behave.agent.spending import SpendPolicy

    provider = settings.get("provider", "openai")
    if "endpoint" in settings:
        endpoint = HostedEndpoint.from_dict(settings["endpoint"])
        if endpoint.provider != provider:
            raise ValueError("provider must match the explicit hosted endpoint")
        ambiguous = {"credentials", "credential_key", "model", "generation_parameters", "prices",
                     "response_models", "billing_tier", "maximum_input_tokens"} & settings.keys()
        if ambiguous:
            raise ValueError(f"explicit endpoint cannot mix legacy model settings: {', '.join(sorted(ambiguous))}")
        # Follow-ups retain the campaign's common-ledger marker as well as the
        # typed endpoint. They must agree, never silently override each other.
        binding = {"budget_usd", "spending_directory", "spending_account"}
        if binding & settings.keys():
            spending = endpoint.spending
            expected = None if spending is None else {
                "budget_usd": spending.limit, "spending_directory": spending.directory,
                "spending_account": spending.account}
            if {key: settings.get(key) for key in binding} != expected:
                raise ValueError("campaign account must match the explicit hosted endpoint")
        return endpoint
    credential = CredentialRef(settings["credentials"], settings.get("credential_key", "OPENAI_API_KEY"), "user-supplied")
    return HostedEndpoint(provider, settings["model"], output.name, credential,
        parameters=settings["generation_parameters"],
        prices=TokenPrices.from_dict(settings["prices"]),
        response_models=tuple(settings.get("response_models", ())),
        billing_tier=settings.get("billing_tier", "default" if provider == "openai" else None),
        spending=SpendPolicy(settings.get("spending_directory", str(output / "spending")),
                             settings.get("spending_account", output.name), "USD",
                             settings["budget_usd"], settings["maximum_input_tokens"]))


def validate_settings(output, settings, endpoint):
    """Preflight task-independent policy without input IO or worker startup.

    Reuse the execution types' validators rather than maintain a second schema
    with different defaults. Input completeness and job identities are still
    checked when the real plan is bound to its selected tasks.
    """
    from behave.agent.admission import AdmissionPolicy
    from behave.agent.attempt import AttemptSpec
    from behave.agent.commands import validate_output_preview_bytes
    from behave.task_execution.batch_plan import validate_execution_options
    from behave.task_execution.solver_settings import guide_names

    validate_execution_options(settings["workers"], settings.get("score_on_submit", True))
    for name, resource in (("model_workers", "eval-review-model"), ("judge_workers", "eval-review-judging")):
        try:
            AdmissionPolicy(resource, settings[name])
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name}: {error}") from error
    validate_output_preview_bytes(settings.get("output_preview_bytes"))
    AttemptSpec(output.name, "solver", endpoint.model_policy(), **settings["attempt"])
    for _, setting in solver_cases(settings):
        guide_names(setting)


def build_plan(output, settings, objects, tasks, endpoint):
    """Build a complete frozen selection for preparation and live campaigns.

    Input failures are retained by load_tasks, but must not silently remove
    candidates from a plan. They are distinct from failures of a running solver.
    """
    from behave.agent.admission import AdmissionPolicy
    from behave.agent.attempt import AttemptSpec
    from behave.core.records import RecordError
    from behave.data.published import PublishedReleaseReader
    from behave.eval.plan import EvaluationPlan
    from behave.task_execution.cocotb_worker import BACKEND, COLLECTOR, METRIC
    from behave.task_execution.comparison import COMPARISON_BACKEND
    from behave.task_execution.sim_tool import ComparisonToolInputs
    from behave.task_execution.solver import SolverJob
    from behave.task_execution.solver_settings import comparison_source
    from behave.task_execution.testbench import TBPolicy

    validate_settings(output, settings, endpoint)
    score_on_submit = settings.get("score_on_submit", True)
    cases = solver_cases(settings)
    selected_settings = {setting for _, setting in cases}
    mixed = len(selected_settings) > 1
    tasks = tuple(tasks)
    by_task = {task.task_id: task for task in tasks}
    if len(by_task) != len(tasks):
        raise RecordError("evaluation-tasks-invalid", "prepared inputs contain duplicate task IDs")
    missing = tuple(dict.fromkeys(task for task, _ in cases if task not in by_task))
    if missing:
        raise RecordError("evaluation-inputs-incomplete",
                          f"requested tasks could not be prepared: {', '.join(missing)}; "
                          "see preparation.json; no plan was published")
    jobs, releases = [], {}
    for task_id, setting in cases:
        task = by_task[task_id]
        comparison = None
        source = comparison_source(setting)
        if source is not None:
            # Share the pinned inventory, never a cache of private task payloads.
            # Non-comparison solvers need only their already-prepared public view.
            if task.release not in releases:
                releases[task.release] = PublishedReleaseReader(objects, task.release)
            published = releases[task.release].load(task.task_id)
            comparison = ComparisonToolInputs.baseline(published.judge, source=source,
                reference_visible=settings.get("reference_visible", False))
        name = attempt_name(output, settings, task.task_id, setting, mixed=mixed)
        jobs.append(SolverJob(task, AttemptSpec(name, "solver",
                    endpoint.model_policy(), **settings["attempt"]), endpoint.command(),
                    setting=setting, comparison=comparison,
                    output_preview_bytes=settings.get("output_preview_bytes")))
    jobs = tuple(jobs)
    recovery = None
    model_admission = AdmissionPolicy("eval-review-model", settings["model_workers"])
    if settings.get("generation_recovery") is not None:
        from behave.eval.generation import recover_jobs, recovered_model_admission
        jobs, recovery = recover_jobs(jobs, settings["generation_recovery"],
                                      score_on_submit=score_on_submit)
        model_admission = recovered_model_admission(model_admission, recovery)
    tb = TBPolicy(BACKEND, COLLECTOR, METRIC) if "joint" in selected_settings else None
    plan = EvaluationPlan(output.name, jobs, COMPARISON_BACKEND, settings["workers"],
        AdmissionPolicy("eval-review-judging", settings["judge_workers"]), tb,
        model_admission, score_on_submit=score_on_submit,
        recorded_admissions=() if recovery is None else recovery.plan.recorded_admissions)
    return plan, recovery


def run(output, *, status_output):
    """Restore scratch selection before a following independent scoring phase."""
    previous_scratch = os.environ.get("BEHAVE_SCRATCH_DIR")
    previous_runtime = os.environ.get("BEHAVE_RUNTIME_DIR")
    try:
        return _run_with_scratch(output, status_output=status_output)
    finally:
        if previous_scratch is None:
            os.environ.pop("BEHAVE_SCRATCH_DIR", None)
        else:
            os.environ["BEHAVE_SCRATCH_DIR"] = previous_scratch
        if previous_runtime is None:
            os.environ.pop("BEHAVE_RUNTIME_DIR", None)
        else:
            os.environ["BEHAVE_RUNTIME_DIR"] = previous_runtime


def check_development_shell(output):
    """Fail the batch before model calls when its actual tool sandbox cannot start."""
    from dataclasses import replace

    from behave.agent.local_rpc import LocalRPCServer
    from behave.isolation import IsolationStatus, run
    from behave.task_execution.shell import development_shell

    # Exercise the same IPC constructor before admitting any candidate. A
    # filesystem/sandbox check alone does not catch Unix socket path failures.
    for name in ("model-relay", "development-relay", "cpu-relay"):
        with LocalRPCServer(name=name, deadline=None):
            pass

    shell = development_shell(None).template
    command = ('set -e; test -r /docs/development.md; '
               'python3 -c "print(42)"; verilator --version')
    result = run(replace(shell, argv=(*shell.argv, "-c", command),
                         limits=replace(shell.limits, wall_seconds=30)))
    passed = (result.status is IsolationStatus.COMPLETED and result.exit_code == 0
              and result.cleanup_confirmed)
    write_json(output / "tool_preflight.json", {
        "passed": passed, "status": result.status.value, "exit_code": result.exit_code,
        "cleanup_confirmed": result.cleanup_confirmed,
        "stdout": result.stdout.decode(errors="replace"),
        "stderr": result.stderr.decode(errors="replace"),
        "refusal": None if result.refusal is None else result.refusal.as_dict(),
    })
    if not passed:
        raise RuntimeError(f"Eval tool preflight failed; see {output / 'tool_preflight.json'}")


def _run_with_scratch(output, *, status_output):
    from behave.agent.providers.credentials import load_credential
    from behave.agent.spending import SpendAccount
    from behave.core.artifacts import BlobRef
    from behave.core.paths import scratch_root
    from behave.core.process_limits import configure_worker_process_limit
    from behave.core.records import thaw_json
    from behave.eval.runner import run_evaluation
    from behave.isolation.startup import startup_environment
    from behave.sim import api
    from behave.task_execution.batch import read_batch_report, summarize_recorded
    from behave.task_execution.cocotb_worker import CocotbBackend
    from behave.task_execution.simulator_judge import SimulatorJudge
    from behave.task_execution.solver_settings import comparison_source

    settings = json.loads((output / "settings.json").read_text())
    endpoint = endpoint_for(output, settings)
    validate_settings(output, settings, endpoint)
    # Campaign workers also enter here directly, without run()'s staging layer.
    limits = configure_worker_process_limit(settings.get("process_limit"))
    startup = startup_environment(settings.get("isolation_startup_slots"))
    write_json(output / "runtime_options.json", {"isolation_startup_slots": settings.get("isolation_startup_slots")})
    write_json(output / "process_limit.json", limits)
    if hasattr(endpoint, "credential"):
        load_credential(endpoint.credential)  # Validate presence only. Never print the value.
    write_json(output / "endpoint.json", endpoint.as_dict())
    stopped, exported = Event(), Event()
    # Journals stay at output, which may be a long NFS path. Worker IPC and
    # disposable build files use the existing short, node-local scratch root.
    with command_scope(environment=startup, stopped=stopped), \
            TemporaryDirectory(prefix="execution-", dir=scratch_root()) as scratch:
        os.environ["BEHAVE_SCRATCH_DIR"] = scratch
        score_on_submit = settings.get("score_on_submit", True)
        cases = solver_cases(settings)
        selected_settings = {setting for _, setting in cases}
        profiles = ("development", "simulation", "testbench-build", "testbench") if score_on_submit else ("development",)
        if not score_on_submit and any(comparison_source(setting) is not None for setting in selected_settings):
            profiles = ("development", "simulation")
        # Prepared libraries and mount declarations belong to this worker batch,
        # not a durable campaign that may later resume on a different node image.
        api.prepare_runtime(profiles=profiles, directory=Path(scratch) / "runtime")
        check_development_shell(status_output)
        print("Shared runtime prepared; loading the selected tasks.", flush=True)
        objects, tasks = load_tasks(output, settings)
        mixed = len(selected_settings) > 1
        plan, recovery = build_plan(output, settings, objects, tasks, endpoint)
        jobs, tb = plan.jobs, plan.testbench
        write_json(output / "plan.json", json.loads(plan.to_bytes()))
        inventory = [{"task": job.task.task_id, "setting": job.setting,
                      "run_key": BlobRef.of(job.to_bytes()).sha256,
                      "trace": trace_name(job.task.task_id, job.setting, mixed=mixed)} for job in jobs]
        write_json(output / "trace_index.json", inventory)
        traces = LiveProgressExporter()
        observed = []
        summary = None
        write_json(output / "trace_export.json", {
            "state": "on_demand", "source": "run/candidates",
            "exporter": "behave.eval.generation.export_candidate"})

        def export():
            nonlocal observed
            outcomes = {}
            for path in sorted((output / "run/report").glob("*.json")):
                event = json.loads(path.read_text())
                if event["kind"] == "evaluation_case":
                    row = event["payload"]["row"]
                    outcomes[row["run_key"]] = row["status"]
            rows = []
            for item in inventory:
                directory = output / "run/candidates" / item["run_key"]
                progress = {**item, **traces.export(directory, output / Path(item["trace"]).parent,
                                                  completed=item["run_key"] in outcomes)}
                if item["run_key"] in outcomes:
                    progress["evaluation_status"] = outcomes[item["run_key"]]
                    if progress["status"] in {"running", "waiting"}:
                        progress["status"] = outcomes[item["run_key"]]
                rows.append(progress)
            observed = rows
            if exported.is_set():
                return rows
            write_json(output / "progress.json", rows)
            # Mirror only when the status destination differs. Some launchers
            # use node-local output; others keep durable journals on NFS.
            if status_output != output:
                write_json(status_output / "progress.json", rows)
            return rows

        def watch():
            while not exported.wait(5):
                try:
                    export()
                except Exception:  # noqa: BLE001 -- export errors must not interrupt model/tool work
                    # Export is observational. Never interrupt or retry model/tool work.
                    (output / "trace_export_error.txt").write_text(traceback.format_exc())

        observer = Thread(target=watch, name="trace-export", daemon=True)
        observer.start()
        try:
            report = run_evaluation(plan=plan, reader=objects, directory=output / "run",
                judge=SimulatorJudge(cpu_capacity=settings.get("judge_cpu_capacity")) if score_on_submit else None,
                testbench=CocotbBackend() if score_on_submit and tb is not None else None,
                cancelled=stopped.is_set,
                report_summary=summarize_recorded, report_reader=read_batch_report,
                recovery=recovery)
            summary = thaw_json(report.summary)
            write_json(output / "report.json", summary)
        finally:
            observer_finished = stop_progress_observer(observer, exported)
            rows = observed if summary is None else final_progress(inventory, observed, summary)
            # A delayed observer may still finish its old progress write. Use a
            # separate final view so it cannot overwrite authoritative completion.
            write_json(output / "progress_final.json", rows)
            if observer_finished:
                write_json(output / "progress.json", rows)
                if status_output != output:
                    write_json(status_output / "progress.json", rows)
            if settings.get("spending_directory"):
                # The campaign coordinator reconciles the shared account after
                # all models exit. Another model can still hold its ledger lock.
                write_json(output / "billing.json", {"account": settings["spending_account"],
                    "scope": "campaign", "directory": settings["spending_directory"]})
            elif getattr(endpoint, "spending", None) is not None:
                write_json(output / "billing.json", SpendAccount(endpoint.spending).summary())
            elif hasattr(endpoint, "credential"):
                # No account is not a free local call. Per-candidate accounting
                # still retains recorded usage/charges and unresolved amounts.
                write_json(output / "billing.json", {"provider": endpoint.provider,
                    "scope": "candidate-accounting", "hosted_api_charge_usd": None})
            else:
                write_json(output / "billing.json", {"hosted_api_charge_usd": "0", "provider": "local"})
            print(json.dumps(rows), flush=True)
    return 1 if stopped.is_set() else 0


def worker(root):
    """Run the existing saved-settings evaluator with managed local serving."""
    from behave.eval.generation import run as evaluate

    return run_local(Path(root), evaluate=lambda output: evaluate(output, status_output=output))


def engine_spec(spec, runtime, index):
    arguments = spec.native_arguments
    if "nccl_port_first" in runtime:
        arguments += ("--nccl-port", str(runtime["nccl_port_first"] + index))
    return replace(spec, port=runtime["port_first"] + index, native_arguments=arguments)


def run_local(root, *, evaluate):
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.core.process_limits import configure_worker_process_limit
    from behave.isolation.startup import startup_environment
    from behave.serving import ServingSession
    from behave.serving.environment import environment, headers
    from behave.serving.spec import ServingSpec

    runtime = json.loads((root / "runtime_settings.json").read_text())
    os.environ.update(startup_environment(runtime.get("isolation_startup_slots")))
    write_json(root / "runtime_options.json", {"isolation_startup_slots": runtime.get("isolation_startup_slots")})
    configure_worker_process_limit(runtime["process_limit"])
    os.sched_setaffinity(0, runtime["cpu_ids"])
    scratch = Path(mkdtemp(prefix="behave-eval-generation-", dir=runtime["scratch_root"]))
    os.environ["BEHAVE_SCRATCH_DIR"] = str(scratch)
    spec = ServingSpec.from_dict(json.loads(Path(runtime["original_launch"]).read_text())["spec"])
    checkpoint = json.loads((Path(runtime["source"]) / "trained/configuration.json").read_text())
    spec = replace(
        spec,
        model_path=runtime["staged_weights"],
        model_revision=checkpoint["model_revision"],
        **runtime["serving_limits"],
    )
    shared = scratch / "shared"
    env = environment(
        spec,
        shared,
        {"CUDA_VISIBLE_DEVICES": runtime["devices"][0], "SLURM_CPUS_PER_TASK": str(runtime["cpu_threads"])},
    )
    binding = json.loads(Path(runtime["serving_binding"]).read_text())
    includes = headers(binding["headers"], shared, env, deadline=time.monotonic() + runtime["headers_seconds"])
    sessions = []
    for index, device in enumerate(runtime["devices"]):
        selected = engine_spec(spec, runtime, index)
        directory = scratch / f"engine-{index}"
        sessions.append(
            ServingSession(
                selected,
                scratch=directory,
                environment=environment(
                    selected,
                    directory,
                    {"CUDA_VISIBLE_DEVICES": device, "SLURM_CPUS_PER_TASK": str(runtime["cpu_threads"])},
                    includes=includes,
                ),
                records=root / "serving" / str(index),
            )
        )
    write_json(
        root / "runtime.json",
        {
            "state": "starting",
            "scratch": str(scratch),
            "cpus": sorted(os.sched_getaffinity(0)),
            "devices": runtime["devices"],
            "isolation_startup_slots": runtime.get("isolation_startup_slots"),
            "started_at": time.time(),
        },
    )
    try:
        start_sessions(sessions)
        if runtime.get("serving_probe_request"):
            from behave.eval.generation import check_running_session

            with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
                accepted = list(
                    pool.map(
                        lambda pair: check_running_session(
                            pair[1].spec,
                            Path(runtime["serving_probe_request"]),
                            json.loads((root / "sampling.json").read_text()),
                            root / "serving_preflight" / str(pair[0]),
                        ),
                        enumerate(sessions),
                    )
                )
            if not all(accepted):
                raise RuntimeError("serving preflight failed; no evaluation candidates admitted")
        urls = tuple(f"http://127.0.0.1:{service.spec.port}/v1/chat/completions" for service in sessions)
        endpoint = LocalChatEndpoint(
            urls[0],
            spec.served_model,
            f"sglang-{spec.package_version}/{spec.model_revision}",
            parameters=json.loads((root / "sampling.json").read_text()),
            worker_urls=urls,
            routing="attempt",
        )
        settings = json.loads((root / "settings.json").read_text())
        settings["endpoint"] = endpoint.as_dict()
        settings["isolation_startup_slots"] = runtime.get("isolation_startup_slots")
        write_json(root / "settings.json", settings)
        write_json(root / "runtime.json", {"state": "evaluating", "scratch": str(scratch), "ready_at": time.time()})
        code = evaluate(root)
    finally:
        primary = sys.exception()

        def record_cleanup(report):
            write_json(root / "cleanup.json", report)
            if primary is not None or not report["cleanup_complete"]:
                write_json(root / "runtime.json", {"state": "failed", "finished_at": time.time(),
                    "cleanup_complete": report["cleanup_complete"],
                    "failure": type(primary).__name__ if primary is not None else "cleanup-unconfirmed"})

        close_sessions(sessions, record=record_cleanup, primary_error=primary)
    write_json(root / "runtime.json",
        {"state": "completed" if code == 0 else "failed", "returncode": code, "finished_at": time.time()})
    return code


def reconcile_completed_generation(measurement, fence_path):
    """Recover a sealed batch whose display export prevented worker shutdown.

    The caller must first stop the entire owning process tree/Slurm step and
    record that external fence. A final report alone never proves cleanup. No
    model call, score change or trajectory reconstruction is performed here.
    """
    from behave.runtime.records import write_json

    measurement, fence_path = Path(measurement), Path(fence_path)
    output = measurement / "rollout"
    fence = json.loads(fence_path.read_text())
    if (fence.get("old_workers_stopped") is not True
            or str(output.resolve()) not in fence.get("sources", ())
            or not fence.get("owner") or not fence.get("verified_at")):
        raise ValueError("completed generation requires an explicit external-worker fence")
    settings = json.loads((output / "settings.json").read_text())
    if settings.get("score_on_submit") is not False:
        raise ValueError("only separated, completed generation can be reconciled")
    store = JournalDirectory(output / "run")
    with store.owner():
        events = store.journal("report").load(verify_artifacts=False)
        if (len(events) < 3 or events[0].kind != "evaluation_started"
                or events[-1].kind != "evaluation_finished"):
            raise ValueError("generation report is not sealed")
        head = BlobRef.of(events[-1].to_bytes())
        if fence.get("report_head") != head.as_dict():
            raise ValueError("report changed after fencing")
        summary_ref = BlobRef.from_dict(thaw_json(events[-1].payload["summary"]))
        summary = json.loads(read_verified(store.objects, summary_ref))
        rows = summary["candidates"]
        cases = events[1:-1]
        expected = len(settings["tasks"]) * len(settings.get("solver_settings", [settings.get("setting", "joint")]))
        if "cases" in settings:
            expected = len(settings["cases"])
        if (summary["requested"] != expected or len(rows) != expected or len(cases) != expected
                or summary["plan"] != thaw_json(events[0].payload["plan"])
                or len({row["run_key"] for row in rows}) != expected):
            raise ValueError("completed report does not cover the requested batch")
        by_key = {row["run_key"]: row for row in rows}
        indexes = set()
        for event in cases:
            if event.kind != "evaluation_case":
                raise ValueError("unexpected event in completed report")
            row = thaw_json(event.payload["row"])
            index = event.payload["index"]
            if type(index) is not int or not 0 <= index < expected or index in indexes:
                raise ValueError("duplicate or invalid completed candidate index")
            indexes.add(index)
            sealed = by_key.get(row["run_key"], {})
            if any(sealed.get(key) != value for key, value in row.items()):
                raise ValueError("completed candidate differs from the sealed report")
            if row["result"] is not None:
                # Verify small result records, not the cumulative prompt blobs.
                read_verified(store.objects, BlobRef.from_dict(row["result"]))
        if json.loads((output / "report.json").read_text()) != summary:
            raise ValueError("published summary differs from the producer's sealed report")
        prior = json.loads((measurement / "generation.json").read_text())
        if prior["state"] not in {"running", "interrupted"}:
            raise ValueError("generation is not awaiting reconciliation")
        write_json(measurement / "reconciliation.json", {
            "prior_generation": prior, "report_head": head.as_dict(), "fence": str(fence_path),
            "prior_runtime": json.loads((output / "runtime.json").read_text())
            if (output / "runtime.json").exists() else None,
            "candidates_retained": expected, "regenerated": 0})
        write_json(output / "cleanup.json", {"cleanup_complete": True,
            "method": "externally_fenced_owner", "fence": str(fence_path)})
        write_json(output / "runtime.json", {"state": "archived",
            "completion_basis": "sealed_report_after_owner_fence", "fence": str(fence_path)})
        write_json(measurement / "generation.json", {
            "state": "completed", "seed": prior["seed"], "cleanup_complete": True,
            "completion_basis": "sealed_report_after_owner_fence", "fence": str(fence_path)})
        return {"candidates_retained": expected, "regenerated": 0, "report_head": head.as_dict()}


def validate_recovery_runtime():
    """Reject an incomplete frozen handoff before staging weights or serving."""
    from behave.eval.plan import EvaluationPlan
    from behave.task_execution.batch_plan import BatchPlan

    if (
        EvaluationPlan is not BatchPlan
        or "recorded_admissions" not in BatchPlan.__dataclass_fields__
        or not callable(getattr(BatchPlan, "admission_identity", None))
    ):
        raise RuntimeError("generation recovery snapshot lacks recorded-admission dependencies")


def recovered_model_admission(configured, recovery):
    """Keep the policy identity sealed into retained candidate accounting.

    Candidate workers may be redistributed on another node. That does not
    rewrite the admission policy under which imported results were produced.
    New candidates use that same policy so the batch remains strictly readable.
    """
    return configured if recovery is None else recovery.plan.model_admission


def semantic_job(job):
    row = json.loads(job.to_bytes())
    command = row["model"]
    if command["revision"] != "behave.local-chat/8":
        raise ValueError("generation recovery requires the reviewed local-chat transport")
    argv = command["argv"]
    endpoint = json.loads(argv[-1])
    # Only ephemeral local URLs and the staged code path change after migration.
    endpoint.pop("url", None)
    endpoint.pop("worker_urls", None)
    argv[-1] = endpoint
    if not argv[-2].endswith("/code/src"):
        raise ValueError("unrecognized staged solver code path")
    argv[-2] = "<staged-code>/src"
    policy = row["attempt"]["model_policy"]
    policy.pop("provider", None)
    policy.pop("transport", None)
    return canonical_json(row)


def recover_jobs(jobs, descriptor, *, score_on_submit):
    validate_recovery_runtime()
    if score_on_submit or descriptor.get("schema") != "behave.eval-generation-recovery/1":
        raise ValueError("this handoff only resumes separated generation")
    retry_cancelled = descriptor.get("retry_solver_cancellations", False)
    if type(retry_cancelled) is not bool:
        raise ValueError("retry_solver_cancellations must be a boolean")
    if retry_cancelled and descriptor.get("head") is None:
        raise ValueError("cancellation retry requires a pinned recovery head")
    source = Path(descriptor["source"])
    fence = json.loads(Path(descriptor["fence"]).read_text())
    if fence.get("old_workers_stopped") is not True or str(source.resolve()) not in fence.get("sources", ()):
        raise ValueError("source has no explicit external-worker fencing receipt")
    origin = JournalDirectory(source / "run")
    with origin.owner():
        events = origin.journal("report").load(verify_artifacts=False)
        if descriptor.get("head") is not None:
            head = BlobRef.from_dict(descriptor["head"])
            positions = [i for i, event in enumerate(events) if BlobRef.of(event.to_bytes()) == head]
            if len(positions) != 1:
                raise ValueError("requested committed recovery head is absent")
            events = events[: positions[0] + 1]
        recovery = BatchRecovery.from_events(origin.objects, events)
        if recovery.plan.score_on_submit or len(jobs) != len(recovery.plan.jobs):
            raise ValueError("generation-only candidate slots changed")
        keep = {event.payload["index"]: event for event in recovery.cases}
        admissions = {}
        for event in events:
            if event.kind != "evaluation_case":
                continue
            row = thaw_json(event.payload["row"])
            if row["result"] is None:
                continue
            result = TaskResult.from_bytes(read_verified(origin.objects, BlobRef.from_dict(row["result"])))
            # Normal failures remain evidence. Only an explicitly requested,
            # fenced infrastructure cancellation may be regenerated below.
            if canonical_json([row["score"], row["status"], row["error"]]) != canonical_json(
                [result.score, result.status, None]
            ):
                raise ValueError("generation result differs from its committed case")
            account = load_accounting_identity(origin.objects, result)
            job_ref = BlobRef.of(recovery.plan.jobs[event.payload["index"]].to_bytes())
            if account.job != job_ref:
                raise ValueError("retained accounting belongs to a different job")
            if (
                retry_cancelled
                and result.failure_stage == "solver"
                and result.failure_code == "solver-worker-cancelled"
            ):
                keep.pop(event.payload["index"], None)
                continue
            default = None if recovery.plan.model_admission is None else recovery.plan.model_admission.identity
            if account.model_admission != default:
                admissions[job_ref] = account.model_admission
            keep[event.payload["index"]] = event
        recovery = replace(
            recovery,
            cases=tuple(keep[index] for index in sorted(keep)),
            plan=replace(recovery.plan, recorded_admissions=tuple(admissions.items())),
        )
        restored = list(jobs)
        for index, (old, new) in enumerate(zip(recovery.plan.jobs, jobs, strict=True)):
            if semantic_job(old) != semantic_job(new):
                raise ValueError(f"evaluation settings changed at candidate {index}")
            if index in recovery.indices:
                restored[index] = old  # Preserve original run keys and results.
        return tuple(restored), recovery


def reply_health(raw):
    try:
        body = json.loads(raw)
        choice = body["choices"][0]
        message = choice["message"]
        text = (message.get("reasoning_content") or message.get("reasoning") or "") + (message.get("content") or "")
        compact = "".join(text.split())
        repeated = bool(len(compact) >= 128 and Counter(compact).most_common(1)[0][1] / len(compact) >= 0.98)
        return {
            "repetitive": repeated,
            "finish_reason": choice.get("finish_reason"),
            "characters": len(text),
            "preview": text[:240],
            "usage": body.get("usage"),
            "tool_calls": len(message.get("tool_calls") or []),
            "healthy": not repeated and bool(compact or message.get("tool_calls")),
        }
    except (ValueError, KeyError, IndexError, TypeError):
        return {"healthy": False, "error": "unreadable-chat-response"}


def check_running_session(spec, request_path, sampling, output):
    from behave.agent.model import ModelRequest
    from behave.agent.providers.chat_worker import exchange
    from behave.agent.providers.local_chat import LocalChatEndpoint

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = ModelRequest.from_dict(json.loads(Path(request_path).read_text()))
    # First inference may compile cold CUDA kernels after HTTP readiness. Keep
    # this under the existing startup budget and exercise the formal sampler:
    # adding a request-level seed would select a different SGLang kernel path.
    request = replace(
        source, call_id="serving-health:0:0", turn=0, dispatch=0, timeout_seconds=spec.startup_seconds or 1800
    )
    endpoint = LocalChatEndpoint(
        f"http://127.0.0.1:{spec.port}/v1/chat/completions",
        spec.served_model,
        spec.identity,
        parameters={**sampling, "max_tokens": 256},
    )
    request = replace(request, model_policy={"provider": endpoint.identity, "model": endpoint.model})
    write_json(output / "request.json", request.as_dict())
    write_json(output / "endpoint.json", endpoint.as_dict())
    outcome = exchange(endpoint, request)
    (output / "response.json").write_bytes(outcome.raw)
    row = {**reply_health(outcome.raw), "provider_error": getattr(outcome, "code", None)}
    row["healthy"] = row["healthy"] and row["provider_error"] is None
    write_json(output / "result.json", row)
    return row["healthy"]
