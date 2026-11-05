"""Shared saved-submission scoring, independent of experiment launch scripts.

The caller supplies inputs, policy and resource budgets. A coordinator may share
one scoring_environment across many archives; no models or Slurm jobs are started.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp
from threading import Lock

from behave.core.artifacts import DirectoryReader
from behave.core.runtime_locations import runtime_path
from behave.core.storage import DirectoryStore, install_exclusive
from behave.runtime.records import command_scope, write_json
from behave.task_execution.run_store import judgment_directory

from .resources import scoring_cpu_plan


@contextmanager
def scoring_environment(*, environment=None):
    """One coordinator owns cancellation and the stable node-local runtime.

    Archives must not replace process-wide scratch paths or signal handlers
    while other archives are executing in the same coordinator. Workers must
    join before exit; restore command controls but retain the batch runtime.
    """
    from behave.isolation.runtime_environment import runtime_environment

    with command_scope(environment=environment) as cancelled:
        runtime_environment()
        yield cancelled


def _stage_score_objects(authority, destination, *, local=None):
    """Reuse immutable node-local CAS blobs; fetch only absent blobs from archive.

    The archive's names remain authoritative. Hard links are only used for the
    content-addressed, immutable object store, not mutable journals/workspaces.
    """
    destination.mkdir(parents=True)
    for path in authority.iterdir():
        preferred = None if local is None else local / path.name
        if preferred is not None and preferred.is_file():
            try:
                os.link(preferred, destination / path.name)
                continue
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
            path = preferred
        shutil.copyfile(path, destination / path.name)


def score_review(output, name, *, workers=None, previous=None, judge_policy=None, cpu_pool=None, stage_from=None,
                 all_submitted=False, retry_codes=None, cpu_capacity=None, selection=None, retry_testbench=True,
                 cpu_resources=None):
    """Score one saved review under a main-thread command scope.

    Multi-archive coordinators share scoring_environment and call score_saved
    directly, rather than installing per-archive signal handlers in threads.
    """
    from behave.core.process_limits import configure_worker_process_limit

    configure_worker_process_limit()
    options = {"workers": workers, "previous": previous, "judge_policy": judge_policy,
               "stage_from": stage_from, "all_submitted": all_submitted,
               "retry_codes": retry_codes, "cpu_capacity": cpu_capacity}
    if selection is not None or not retry_testbench:
        options.update(selection=selection, retry_testbench=retry_testbench)
    if cpu_resources is not None:
        options["cpu_resources"] = cpu_resources
    with scoring_environment() as cancelled:
        return score_saved(output, name, cpu_pool=cpu_pool, cancelled=cancelled, **options)


def score_saved(output, name, *, workers, previous, judge_policy, cpu_pool, stage_from, all_submitted=False,
                 retry_codes=None, cpu_capacity=None, selection=None, retry_testbench=True,
                 executor=None, on_case=None, cancelled=lambda: False, cpu_resources=None):
    """Stage once; persist each committed case, then archive final diagnostics."""
    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.core.lifecycle import CleanupState
    from behave.core.records import thaw_json
    from behave.core.storage import install_exclusive
    from behave.eval.score_stream import ScoreCheckpoint
    from behave.isolation.scratch import ScratchDirectory
    from behave.task_execution.cocotb_worker import CocotbBackend
    from behave.task_execution.recovery import require_task_quiescent
    from behave.task_execution.rejudge import judgment_run, rejudge_batch
    from behave.task_execution.run_store import RunStore
    from behave.task_execution.simulator_judge import SimulatorJudge

    root = RunStore(output / "run")
    staged_output = output if stage_from is None else Path(stage_from)
    staged_run = RunStore(staged_output / "run")
    preferred = staged_output
    runtime_path = output / "runtime.json"
    if runtime_path.exists():
        saved = json.loads(runtime_path.read_text())
        candidate = Path(saved.get("local_directory", staged_output))
        if saved.get("state") == "archived" and saved["host"] == socket.gethostname() and candidate.is_dir():
            preferred = candidate
    target = judgment_run(root, name)
    archived = False
    # Acquire the archive owner before allocating scratch. Both indexed and
    # full passes release only this workspace, after worker cleanup and archive
    # commit; failed workspaces and unrelated owners are never swept here.
    with root.owner(), ScratchDirectory(prefix="behave-eval-score-",
            cleanup=lambda: CleanupState.COMPLETE if archived else CleanupState.UNKNOWN) as local:
        print(f"Scoring workspace: {local}", flush=True)
        # New full-batch passes use the same demand-loaded path as indexed
        # scoring. Keep old attempts' replay identity: an already-started
        # unindexed pass must still resume with its original configuration.
        events = target.journal("task").load() if selection is None else ()
        legacy = bool(events) and "selection" not in events[0].payload
        objects = None
        if selection is None and not legacy:
            from behave.eval.scoring import StagedObjects, select_completed_run

            objects = StagedObjects(local / "run/objects", staged_run.objects.directory,
                                    preferred=preferred / "run/objects")
            selection = select_completed_run(staged_run.directory, objects, previous=previous)
        if selection is None:
            _stage_score_objects(staged_run.objects.directory, local / "run/objects",
                                 local=preferred / "run/objects")
            for directory in ("report", "judgments"):
                source = staged_run.directory / directory
                if directory == "report" and (preferred / "run/report").exists():
                    source = preferred / "run/report"
                if source.exists():
                    shutil.copytree(source, local / "run" / directory)
            candidates = preferred / "run/candidates"
            if candidates.exists():
                shutil.copytree(candidates, local / "run/candidates",
                                ignore=shutil.ignore_patterns("objects"))
            shutil.copytree(preferred / "inputs", local / "inputs")
            local_run = RunStore(local / "run")
            existing = {path.name for path in local_run.objects.directory.iterdir()}
            inputs = DirectoryReader(local / "inputs")
        else:
            from behave.eval.scoring import StagedObjects, stage_selected_journals

            if objects is None:
                objects = StagedObjects(local / "run/objects", staged_run.objects.directory,
                                        preferred=preferred / "run/objects")
            stage_selected_journals(staged_run.directory, local / "run", selection,
                                    name=name, previous=previous)
            local_run = RunStore(local / "run", objects=objects)
            existing = objects.fetched  # Updated as further old inputs are demanded.
            inputs = StagedObjects(local / "inputs", staged_output / "inputs",
                                   preferred=preferred / "inputs")
        local_target = judgment_run(local_run, name)
        checkpoint = ScoreCheckpoint(local_target, target, existing=existing)

        def committed(row):
            checkpoint.publish()
            if on_case is not None:
                on_case(row)

        runtime = {"host": socket.gethostname(), "local_directory": str(local), "state": "running"}
        write_json(target.directory / "runtime.json", runtime, durable=True)
        completed = False
        try:
            report = rejudge_batch(directory=local_run.directory, name=name,
                reader=inputs, objects=local_run.objects,
                judge=SimulatorJudge(cpu_pool=cpu_pool, cpu_capacity=cpu_capacity),
                testbench=CocotbBackend(cpu_pool=cpu_pool) if retry_testbench else None,
                workers=workers, previous=previous,
                all_submitted=all_submitted,
                retry_codes=retry_codes,
                selection=selection, retry_testbench=retry_testbench,
                judge_policy=None if judge_policy is None else Path(judge_policy).read_bytes(),
                executor=executor, on_case=committed, cancelled=cancelled, cpu_resources=cpu_resources)
            write_json(local_target.directory / "summary.json", thaw_json(report.summary))
            for candidate in (local_target.directory / "candidates").glob("*"):
                require_task_quiescent(RunStore(candidate, objects=local_run.objects), run_key=candidate.name)
            completed = True
            return report
        finally:
            # Publish blobs before journals. Do not recopy model histories or
            # rewrite the original report. Failed publication retains /tmp.
            published = {ref.sha256 for ref in checkpoint.published}
            refs = tuple(BlobRef(path.name, path.stat().st_size)
                         for path in local_run.objects.directory.iterdir()
                         if path.name not in existing and path.name not in published)
            root.objects.publish(local_run.objects, refs)
            if local_target.directory.exists():
                for path in local_target.directory.rglob("*"):
                    destination = target.directory / path.relative_to(local_target.directory)
                    if path.is_file() and path.name not in {"owner.lock", "runtime.json"} and not destination.exists():
                        install_exclusive(destination.parent, destination.name, path.read_bytes())
            write_json(target.directory / "runtime.json", {**runtime,
                "state": "archived" if completed else "failed-retained"}, durable=True)
            archived = completed  # A failed receipt must retain the recovery workspace.


def select_indices(indices, *, datasets=()):
    """Pin explicit result references, not a run's latest mutable score view."""
    selected = {}
    for index in indices:
        rows = json.loads(Path(index).read_bytes())
        for row in rows:
            if datasets and row["dataset"] not in datasets:
                continue
            if row["archive_state"] != "archived":
                raise ValueError("score selection requires archived input runs")
            source = str(Path(row["source_run"]).resolve())
            group = selected.setdefault(source, {})
            key = row["run_key"]
            if key in group:
                raise ValueError(f"duplicate indexed candidate: {source}/{key}")
            group[key] = {field: row[field] for field in (
                "task", "attempt_id", "setting", "release", "revision", "result", "submission",
                "judgment", "dataset", "model", "sample", "trace", "rollout_status")}
    if not selected:
        raise ValueError("no indexed candidates matched the requested datasets")
    return selected


def source_shards(sources, selection, count):
    """Keep a source's journal owner on one node; balance by submitted artifacts."""
    if type(count) is not int or count < 1:
        raise ValueError("shards must be a positive integer")
    weights = {source: (sum((2 if row["setting"] == "joint" else 1)
                            for row in selection[source].values() if row["submission"] is not None)
                        if selection is not None else 1) for source in sources}
    shards, loads = [[] for _ in range(count)], [0] * count
    for source in sorted(sources, key=lambda source: (-weights[source], source)):
        index = min(range(count), key=lambda index: (loads[index], index))
        shards[index].append(source)
        loads[index] += max(weights[source], 1)
    return shards


def select_completed_run(directory, objects, *, previous=None):
    """Pin every candidate in a completed producer report, including failures.

    Reuse the producer's sealed results, not a scan of its model history. The
    ordinary indexed rejudge path validates task/result bindings again before
    executing. Full offline trace audits remain a separate operation.
    """
    from behave.agent.journal import Event
    from behave.core.artifacts import BlobRef, read_verified
    from behave.core.records import thaw_json
    from behave.task_execution.batch import case_of, read_batch_report
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.result import TaskResult

    source = Path(directory) if previous is None else judgment_directory(directory, previous)
    paths = sorted((source / "report").glob("*.json"), key=lambda path: (len(path.name), path.name))
    if not paths:
        raise ValueError("a completed evaluation report is required")
    terminal = Event.from_bytes(paths[-1].read_bytes())
    if terminal.kind != "evaluation_finished":
        raise ValueError("a completed evaluation report is required")
    report = read_batch_report(objects, objects.put(terminal.to_bytes()))
    summary = thaw_json(report.summary)
    plan = BatchPlan.from_bytes(read_verified(objects, BlobRef.from_dict(summary["plan"])), objects)
    selected = {}
    for index, (job, recorded) in enumerate(zip(plan.jobs, summary["candidates"], strict=True)):
        row = {key: value for key, value in recorded.items() if key not in {"billing", "final_score"}}
        case_of(plan, {"index": index, "row": row})
        key = row["run_key"]
        if key in selected:
            raise ValueError("duplicate candidate in completed report")
        result = (None if row["result"] is None else
                  TaskResult.from_bytes(read_verified(objects, BlobRef.from_dict(row["result"]))))
        selected[key] = {"task": job.task.task_id, "attempt_id": job.attempt.attempt_id,
                         "setting": job.setting, "release": job.task.release.as_dict(),
                         "revision": job.task.revision.as_dict(), "result": row["result"],
                         "submission": None if result is None or result.submission is None
                         else result.submission.as_dict(), "judgment": previous or "on_submit"}
    if not selected:
        raise ValueError("a completed evaluation report must contain candidates")
    return selected


def select_score_recovery(directory, objects, *, judgment):
    """Carry committed scores into a new pass after its old owner is stopped.

    The completed generation remains authoritative for membership/submissions.
    An interrupted scoring journal may supply newer result references, but does
    not remove unfinished cases. Execution checks old workers are quiescent.
    """
    from behave.core.artifacts import BlobRef, read_verified
    from behave.core.records import thaw_json
    from behave.task_execution.batch import case_of
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.result import TaskResult
    from behave.task_execution.run_store import RunStore

    directory = Path(directory)
    target = judgment_directory(directory, judgment)
    selected = select_completed_run(directory, objects)
    events = RunStore(target, objects=objects).journal("report").load(
        verify_artifacts=False)
    if not events:
        return selected
    if events[0].kind != "evaluation_started":
        raise ValueError("scoring journal has no evaluation plan")
    plan = BatchPlan.from_bytes(read_verified(objects,
        BlobRef.from_dict(thaw_json(events[0].payload)["plan"])), objects)
    seen = set()
    for event in events[1:]:
        if event.kind == "evaluation_finished":
            continue
        if event.kind != "evaluation_case":
            raise ValueError("unexpected scoring journal event")
        _, row = case_of(plan, thaw_json(event.payload))
        key = row["run_key"]
        if key not in selected or key in seen:
            raise ValueError("scoring recovery contains an unrequested or repeated candidate")
        seen.add(key)
        original = selected[key]
        if row["result"] is None:
            if original["result"] is not None:
                raise ValueError("scoring recovery dropped a recorded result")
            continue
        result = TaskResult.from_bytes(read_verified(objects, BlobRef.from_dict(row["result"])))
        if (result.task_id, result.attempt_id, result.release.as_dict(), result.revision.as_dict(),
                None if result.submission is None else result.submission.as_dict()) != (
                original["task"], original["attempt_id"], original["release"], original["revision"],
                original["submission"]):
            raise ValueError("scoring recovery changed the saved submission")
        selected[key] = {**original, "result": row["result"], "judgment": judgment}
    return selected


class StagedObjects(DirectoryStore):
    """Fetch a referenced immutable blob once; do not enumerate the archive CAS.

    Preferred node-local blobs are verified by the same reader as archive blobs.
    Corrupt content is an error, not a reason to silently select another copy.
    ``fetched`` separates old inputs from newly produced objects for publication.
    """

    def __init__(self, directory, authority, *, preferred=None):
        super().__init__(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Streaming subsets share their trusted inputs root by symlink. Pin
        # its mapped target once; DirectoryReader still rejects symlinked
        # content files and verifies every requested blob's size and digest.
        self.authority = DirectoryReader(runtime_path(authority).resolve())
        self.preferred = (None if preferred is None else
                          DirectoryReader(runtime_path(preferred).resolve()))
        self.fetched = set()
        self._lock = Lock()

    def read(self, ref):
        if not (self.directory / ref.sha256).exists():
            with self._lock:
                if not (self.directory / ref.sha256).exists():
                    reader = self.authority
                    if self.preferred is not None and (self.preferred.directory / ref.sha256).is_file():
                        reader = self.preferred
                    data = reader.read(ref)
                    install_exclusive(self.directory, ref.sha256, data)
                    self.fetched.add(ref.sha256)
        return super().read(ref)


def stage_selected_journals(source, destination, selection, *, name, previous=None):
    """Copy selected service receipts plus report/config journals, not trajectories.

    Supervision settles solver/model/tool processes. Legacy orphan phase journals
    remain visible to the existing cleanup admission check; they are not discarded.
    Existing target journals are retained for a same-name resume.
    """
    source, destination = Path(source), Path(destination)
    passes = [name]
    if previous is not None:
        passes.append(previous)
    passes.extend(row["judgment"] for row in selection.values()
                  if row["judgment"] is not None and row["judgment"] != "on_submit")
    for selected in passes:
        judgment_directory(source, selected)

    def copy(relative):
        path = source / relative
        if path.exists():
            shutil.copytree(path, destination / relative, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("objects"))

    copy(Path("report"))
    if previous is not None:
        copy(Path("judgments") / previous / "report")
    copy(Path("judgments") / name)
    for key, row in selection.items():
        parents = {Path("candidates") / key}
        for judgment in (previous, row["judgment"]):
            if judgment is not None and judgment != "on_submit":
                parents.add(Path("judgments") / judgment / "candidates" / key)
        for candidate in parents:
            for phase in ("supervision", "judge", "testbench"):
                copy(candidate / phase)
            if not any((source / candidate / "supervision").glob("*.json")):
                for phase in ("solver", "provider", "development"):
                    copy(candidate / phase)


class CandidateQueue(ThreadPoolExecutor):
    """One coordinator's candidate queue, shared across its archives.

    Finished counts executed calls (including failures). Cancelled counts only
    calls cancelled before they start; neither counter is a scored-case count.
    """

    def __init__(self, workers):
        super().__init__(max_workers=workers, thread_name_prefix="score-candidate")
        self._activity_lock = Lock()
        self._queued = self._active = self._finished = self._peak = 0
        self._cancelled = 0

    def submit(self, fn, /, *args, **kwargs):
        with self._activity_lock:
            self._queued += 1

        def invoke():
            with self._activity_lock:
                self._queued -= 1
                self._active += 1
                self._peak = max(self._peak, self._active)
            try:
                return fn(*args, **kwargs)
            finally:
                with self._activity_lock:
                    self._active -= 1
                    self._finished += 1

        try:
            future = super().submit(invoke)
        except Exception:
            with self._activity_lock:
                self._queued -= 1
            raise

        def settled(future):
            # A cancelled queued future never enters invoke(). The executor
            # makes cancellation and starting mutually exclusive.
            if future.cancelled():
                with self._activity_lock:
                    self._queued -= 1
                    self._cancelled += 1

        future.add_done_callback(settled)
        return future

    def activity(self):
        with self._activity_lock:
            return {
                "queued": self._queued,
                "active": self._active,
                "finished": self._finished,
                "peak_active": self._peak,
                "cancelled": self._cancelled,
            }


def score_shard(output, shard=0, *, process_limit=None):
    """Score a prepared shard, sharing one candidate budget across archives."""
    from behave.agent.attempt import AttemptInterrupted
    from behave.agent.cpu_resources import CpuResources
    from behave.core import runtime_defaults as defaults
    from behave.core.process_limits import configure_worker_process_limit
    from behave.eval.scoring import score_saved, scoring_environment
    from behave.isolation.startup import startup_environment

    output = Path(output)
    limits = configure_worker_process_limit(process_limit)
    plan = json.loads((output / "plan.json").read_text())
    runtime = output / "runtime_options.json"
    options = json.loads(runtime.read_text()) if runtime.exists() else {}
    slots = options.get("isolation_startup_slots")
    startup = startup_environment(slots)
    resources = CpuResources(plan["workers"])
    capacity = len(resources.cpus)
    workers = resources.snapshot()["slots"]
    write_json(
        output / f"cpu-{shard}.json",
        {
            "capacity": capacity,
            "judge_workers": workers,
            "resources": resources.snapshot(),
            "scope": "supervised-comparison",
            "process_limit": limits,
            "isolation_startup_slots": slots,
        },
    )
    sources = plan["shards"][shard]
    progress, state_lock = [], Lock()
    states = {source: {"state": "preparing", "returned": 0, "statuses": {}} for source in sources}

    def committed(source, row):
        with state_lock:
            state = states[source]
            state["state"] = "scoring"
            state["returned"] += 1
            statuses = state["statuses"]
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1

    with scoring_environment(environment=startup) as cancelled, CandidateQueue(workers) as candidates:

        def archive(item):
            started = time.monotonic()
            print(f"Saved-submission scoring: {item}", flush=True)
            try:
                if cancelled():
                    raise AttemptInterrupted("rejudgment-cancelled")
                report = score_saved(
                    Path(item),
                    plan["name"],
                    workers=workers,
                    previous=plan["previous"],
                    all_submitted=plan["all_submitted"],
                    judge_policy=output / "policy.json",
                    retry_codes=None if "retry_codes" not in plan else tuple(plan["retry_codes"]),
                    selection=None if plan["selection"] is None else plan["selection"][item],
                    retry_testbench=plan["retry_testbench"],
                    cpu_resources=resources,
                    cpu_pool=None,
                    stage_from=None,
                    executor=candidates,
                    cancelled=cancelled,
                    on_case=lambda row: committed(item, row),
                )
                result = {"source": item, "state": "completed", "report": report.as_dict()}
            except Exception as error:  # noqa: BLE001 -- isolate per-archive failure
                # A source failure neither fabricates zeros nor blocks other
                # archives. score_saved publishes completed evidence first.
                traceback.print_exc()
                result = {
                    "source": item,
                    "state": "failed",
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "error": str(error),
                }
            result["elapsed_seconds"] = time.monotonic() - started
            return result

        # These threads only stage/publish independent archives and wait for
        # their futures. ALL candidate scoring uses the single queue above.
        with ThreadPoolExecutor(max_workers=max(1, len(sources)), thread_name_prefix="score-archive") as archives:
            pending = {archives.submit(archive, source) for source in sources}
            while pending:
                done, pending = wait(pending, timeout=defaults.JOB_HEARTBEAT_SECONDS, return_when=FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    progress.append(result)
                    with state_lock:
                        states[result["source"]]["state"] = result["state"]
                    print(json.dumps({k: v for k, v in result.items() if k != "report"}), flush=True)
                with state_lock:
                    live = {
                        "candidate_capacity": workers,
                        **candidates.activity(),
                        "cpu": resources.snapshot(),
                        "sources": {
                            key: {**value, "statuses": dict(value["statuses"])} for key, value in states.items()
                        },
                        "updated_at": time.time(),
                    }
                write_json(output / f"live-{shard}.json", live)
                write_json(output / f"progress-{shard}.json", progress)
    if any(row["state"] != "completed" for row in progress):
        raise RuntimeError("some archived sources could not be rescored; see progress records")


def prepare(output, sources=(), *, repository, policy=None, workers, previous=None, all_submitted=False,
            retry_codes=None, selection=None, shards=1, retry_testbench=True, search_seconds=0,
            isolation_startup_slots=None):
    from behave.eval.scoring import source_shards
    from behave.isolation.startup import startup_environment
    from behave.runtime.snapshots import freeze
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy

    output = Path(output).resolve()
    startup_environment(isolation_startup_slots)  # Validate before writing or freezing code.
    runtime = {"isolation_startup_slots": isolation_startup_slots}
    defaults = JudgePolicy(command_version=api.COMMAND_VERSION, authority_version=api.RESULT_VERSION)
    selected = replace(defaults if policy is None else JudgePolicy.from_bytes(Path(policy).read_bytes()),
                       command_version=api.COMMAND_VERSION, authority_version=api.RESULT_VERSION,
                       search_seconds=search_seconds, analysis=None)
    if selection is not None:
        if sources or previous is not None or not selection:
            raise ValueError("indexed scoring selects its own sources and saved result references")
        sources = sorted(selection)
    sources = [str(Path(p).resolve()) for p in sources]
    if not sources or type(workers) is not int or workers < 1:
        raise ValueError("scoring requires sources and a positive worker count")
    plan = {"name": output.name, "sources": sources,
            "workers": workers, "previous": previous, "all_submitted": all_submitted,
            "policy": json.loads(selected.to_bytes()),
            "selection": selection, "retry_testbench": retry_testbench,
            "shards": source_shards(sources, selection, shards),
            **({"retry_codes": list(retry_codes)} if retry_codes is not None else {})}
    if (output / "plan.json").exists():
        if json.loads((output / "plan.json").read_text()) != plan:
            raise ValueError("this scoring name already belongs to a different pass")
        runtime_path = output / "runtime_options.json"
        saved = json.loads(runtime_path.read_text()) if runtime_path.exists() else {"isolation_startup_slots": None}
        if saved != runtime:
            raise ValueError("scoring startup policy changed; use a new scoring pass")
        return plan
    output.mkdir(parents=True, exist_ok=False)
    freeze(Path(repository).resolve(), output / "snapshot",
           extra_files=("tools/eval_score_job.py", "tools/eval_score_inputs.py"))
    write_json(output / "policy.json", plan["policy"])
    write_json(output / "runtime_options.json", runtime)
    write_json(output / "plan.json", plan)
    return plan


def launch(output, allocation, cpus, *, shard=0, process_limit=None, cpu_ids=None,
           allocated_cpus=None, allocated_cpu_ids=None, cwd=None):
    from behave.runtime.jobs import status_of, submit

    output = Path(output).resolve()
    plan = json.loads((output / "plan.json").read_text())
    if not 0 <= shard < len(plan["shards"]):
        raise ValueError("shard is outside the frozen plan")
    control = output / "control" / f"shard-{shard}"
    if (control / "launch.json").exists():
        return status_of(control)
    snapshot = output / "snapshot"
    if process_limit is not None and (type(process_limit) is not int or process_limit < -1 or process_limit == 0):
        raise ValueError("process_limit must be positive, or -1 for the inherited hard limit")
    requested_cpus, cpu_ids = scoring_cpu_plan(cpus, cpu_ids=cpu_ids,
        allocated_cpus=allocated_cpus, allocated_cpu_ids=allocated_cpu_ids)
    # Resolve on the compute node, not on the submitting host. Keep -1 as the
    # existing CLI spelling for the default inherited hard limit.
    selected_limit = None if process_limit == -1 else process_limit
    bootstrap = ("import sys;sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
                 f"from tools.eval_score_job import staged;staged(sys.argv[1],int(sys.argv[2]),process_limit={selected_limit!r})")
    # Physical CPU IDs can have gaps or start above the allocation's CPU count.
    # Reserve the declared allocation for explicit binding, not max(ID) + 1.
    # CpuResources derives the actual scoring budget from the smaller affinity.
    # Nested srun inherits SLURM_CPU_BIND from the controller. Requesting more
    # CPUs does not override that mask, so a one-CPU controller can silently
    # serialize scoring. Always choose a step-local binding explicitly.
    bindings = (["--cpu-bind=threads"] if cpu_ids is None else
                [f"--cpu-bind=mask_cpu:{hex(sum(1 << cpu for cpu in cpu_ids))}"])
    argv = ["srun", f"--jobid={allocation}", "--overlap", "--exact", "--nodes=1", "--ntasks=1",
            f"--cpus-per-task={requested_cpus}", *bindings,
            "--gres=none", "--kill-on-bad-exit=1", "--propagate=NONE", sys.executable,
            "-I", "-B", "-u", "-c", bootstrap, str(snapshot), str(snapshot / "src"), str(output), str(shard)]
    return submit(control, argv, cwd=Path.cwd() if cwd is None else Path(cwd), wall_seconds=None,
                  metadata={"purpose": "saved-submission scoring", "allocation": allocation,
                            "cpus": cpus, "shard": shard, "process_limit": process_limit,
                            **({"allocated_cpus": allocated_cpus} if allocated_cpus is not None else {}),
                            **({"allocated_cpu_ids": list(allocated_cpu_ids)}
                               if allocated_cpu_ids is not None else {}),
                            **({"cpu_ids": list(cpu_ids)} if cpu_ids is not None else {}),
                            "plan": str(output / "plan.json")})


def staged(output, shard=0, *, process_limit=None):
    from behave.core.process_limits import configure_worker_process_limit
    from behave.runtime.snapshots import stage

    output = Path(output)
    limits = configure_worker_process_limit(process_limit)
    write_json(output / f"process-{shard}.json", limits)
    local = Path(mkdtemp(prefix="behave-score-code-", dir="/tmp")) / "checkout"
    stage(output / "snapshot", local)
    os.execv(sys.executable, [sys.executable, "-I", "-B", "-u", "-c",
        ("import sys;sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1)];"
         f"from tools.eval_score_job import run;run(sys.argv[1],int(sys.argv[2]),process_limit={process_limit!r})"),
        str(local), str(local / "src"), str(output), str(shard)])
