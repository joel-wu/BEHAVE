"""External-method rollout and saved scoring over shared execution services.

The caller supplies settings, upstream sources and an existing model account.
Run-local journals stay in owned scratch; publication uses runtime.records.
"""

import json
import shutil
import socket
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event

from behave.eval.generation import endpoint_for, load_tasks
from behave.runtime.records import archive_run, command_scope, write_json
from behave.task_execution.run_store import judgment_directory

from .configuration import baseline_policy, method_settings


@contextmanager
def local_run(output, phase, *, judgment=None):
    """Stage once; release scratch only after evidence and runtime commit."""
    from behave.agent.run_store import JournalDirectory
    from behave.core.lifecycle import CleanupState
    from behave.core.process_limits import configure_worker_process_limit
    from behave.isolation.scratch import ScratchDirectory

    output = Path(output).resolve()
    if judgment is not None:
        previous = judgment_directory(output, judgment)
    settings = json.loads((output / "settings.json").read_text())
    limits = configure_worker_process_limit(settings.get("process_limit"))
    archived = False
    stopped = Event()
    with JournalDirectory(output).owner(), ScratchDirectory(prefix="behave-external-",
            cleanup=lambda: CleanupState.COMPLETE if archived else CleanupState.UNKNOWN) as local, \
            command_scope(environment={"BEHAVE_SCRATCH_DIR": str(local / "scratch")}, stopped=stopped):
        # Supervisor and phase status belong to the durable owner, not the
        # staged evidence. Old status must not overwrite a recovery pointer.
        def ignore(directory, names):
            return {name for name in names if name in {"owner.lock", "scratch"}
                    or Path(directory) / name == local
                    or (Path(directory) == output and name in {"control", f"{phase}_runtime.json"})}

        if judgment is None:
            shutil.copytree(output, local, dirs_exist_ok=True, ignore=ignore)
        else:
            # Score only frozen submissions and the selected judgment journal,
            # not the previous model histories, traces or object population.
            shutil.copy2(output / "settings.json", local / "settings.json")
            if previous.exists():
                shutil.copytree(previous, judgment_directory(local, judgment),
                                ignore=shutil.ignore_patterns("owner.lock"))
        (local / "scratch").mkdir()
        write_json(output / f"{phase}_runtime.json", {"state": "running", "host": socket.gethostname(),
                                                    "local_directory": str(local), "process_limit": limits},
                   durable=True)
        try:
            yield local, stopped
        finally:
            archive_run(local, output)
            write_json(output / f"{phase}_runtime.json", {"state": "archived", "host": socket.gethostname(),
                                                        "process_limit": limits}, durable=True)
            archived = True


def rollout(output):
    from behave.agent.admission import AdmissionPolicy, ResourcePool
    from behave.agent.attempt import AttemptSpec
    from behave.agent.cpu_resources import CpuResources
    from behave.agent.lifetime import AttemptControl
    from behave.core.storage import DirectoryStore
    from behave.pipelines.provider import model_factory, sampling_endpoint, sampling_policy
    from behave.pipelines.workflow import export_trace, run
    from behave.sim import api

    settings = json.loads((Path(output) / "settings.json").read_text())
    method = json.loads((Path(output) / "pipeline.json").read_text())
    provider_sampling = sampling_policy(method.get("provider_sampling", "upstream"))
    endpoint = endpoint_for(Path(output), settings)
    configured = method_settings(method["method"], method["options"])
    # Fix the selected method's sampling in the saved root policy as well as in
    # every provider call. The unchanged upstream code owns per-call values.
    endpoint = sampling_endpoint(endpoint, {"temperature": configured.temperature, "top_p": configured.top_p},
                                 provider_sampling=provider_sampling)
    with local_run(output, "rollout") as (local, stopped):
        api.prepare_tool_environment("iverilog")
        _, tasks = load_tasks(local, settings)
        objects = DirectoryStore(local / "objects")
        model_pool = ResourcePool(AdmissionPolicy("external-model", settings["model_workers"]))
        cpus = CpuResources(settings["workers"])
        write_json(local / "resources.json", cpus.snapshot())
        def one(task):
            directory = local / "candidates" / task.task_id
            directory.mkdir(parents=True, exist_ok=True)
            try:
                result = run(method=method["method"], settings=configured, task=task,
                    spec=AttemptSpec(f"{settings.get('attempt_namespace', Path(output).name)}-{task.task_id}", "solver", endpoint.model_policy(),
                                     **settings["attempt"]), backend_factory=model_factory(endpoint, provider_sampling=provider_sampling),
                    directory=directory, objects=objects, model_pool=model_pool,
                    control=AttemptControl(cancelled=stopped.is_set), cpu_resources=cpus)
                write_json(directory / "result.json", result)
                (directory / "trace.md").write_text(export_trace(result, directory, objects))
            except Exception as exc:  # noqa: BLE001 -- per-task boundary preserves evidence, never assigns a score
                result = {"task": task.task_id, "status": "execution_error", "error": str(exc),
                          "traceback": traceback.format_exc(), "submission": None}
                write_json(directory / "error.json", result)
            print(json.dumps({"task": task.task_id, "status": result["status"]}), flush=True)
            return result
        with ThreadPoolExecutor(max_workers=settings["workers"]) as pool:
            results = list(pool.map(one, tasks))
        write_json(local / "resources.json", cpus.snapshot())
        write_json(local / "rollout.json", results)
        return results


def score(output, *, name="baseline", workers=None, policy_file=None):
    judgment_directory(output, name)

    from behave.agent.cpu_resources import CpuResources
    from behave.core.artifacts import BlobRef
    from behave.data.published import load_published_task
    from behave.eval.scoring import StagedObjects
    from behave.pipelines.workflow import score as judge_one
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.task_execution.resources import ScheduledJudge
    from behave.task_execution.simulator_judge import SimulatorJudge

    output = Path(output)
    settings = json.loads((output / "settings.json").read_text())
    explicit_policy = JudgePolicy.from_bytes(Path(policy_file).read_bytes()) if policy_file else None
    judge_workers = settings["judge_workers"] if workers is None else workers
    cpus = CpuResources(judge_workers)
    judge = ScheduledJudge(SimulatorJudge(cpu_capacity=settings.get("judge_cpu_capacity")), cpus)
    with local_run(output, "score", judgment=name) as (local, stopped):
        write_json(local / "judgments" / name / "resources.json", cpus.snapshot())
        inputs = StagedObjects(local / "inputs", output / "inputs")
        objects = StagedObjects(local / "objects", output / "objects")
        release = BlobRef.from_dict(settings["release"])

        def one(task_id):
            path = output / "candidates" / task_id / "result.json"
            if not path.exists():
                return {"task": task_id, "status": "rollout_unavailable"}
            result = json.loads(path.read_text())
            if result["submission"] is None:
                return {"task": task_id, "status": result["status"]}
            task = load_published_task(inputs, release, task_id).judge
            policy = explicit_policy or baseline_policy(task.judge_policy)
            task = replace(task, judge_policy=policy.to_bytes())
            directory = local / "judgments" / name / task_id
            write_json(directory / "policy.json", json.loads(policy.to_bytes()))
            try:
                judged = judge_one(task=task, result=result, directory=directory / "journal", objects=objects,
                                   backend=judge, cancelled=stopped.is_set)
                row = {"task": task_id, **judged.as_dict()}
            except Exception as exc:  # noqa: BLE001 -- report per-task evaluation failure, never infer zero
                row = {"task": task_id, "status": "evaluation_error", "error": str(exc)}
            write_json(directory / "result.json", row)
            return row

        with ThreadPoolExecutor(max_workers=judge_workers) as pool:
            results = list(pool.map(one, settings["tasks"]))
        write_json(local / "judgments" / name / "resources.json", cpus.snapshot())
        write_json(local / "judgments" / name / "summary.json", results)
        if explicit_policy is not None:
            write_json(local / "judgments" / name / "policy.json", json.loads(explicit_policy.to_bytes()))
        return results
