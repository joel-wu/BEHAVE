"""Bounded local orchestration; task execution owns all model and score semantics.

Threads wait on supervised processes, not arbitrary candidate code. The worker
count bounds active candidates; a separate pool bounds final judge/TB services.
An optional coordinator model pool owns provider calls from all solver workers.
Shell, comparison, judge and TB invocations share a CPU allocation independently
of model requests and idle candidate workers.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from behave.agent.admission import shared_pools
from behave.agent.attempt import AttemptInterrupted
from behave.agent.cpu_resources import CpuResources
from behave.agent.journal import load_chain
from behave.agent.model import Charge
from behave.agent.replay import Tape
from behave.agent.trajectory import CostLedger
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, canonical_json, fields, metadata, text, thaw_json
from behave.core.storage import install_exclusive

from .accounting import load_accounting
from .batch_plan import BatchPlan
from .judge import JudgeBackend
from .judgment import final_score
from .recovery import require_task_quiescent
from .resources import ScheduledJudge, ScheduledTestbench
from .result import TaskReport, TaskResult, final_score_from_record
from .run_store import RunStore
from .testbench import TBBackend, record_final_testbench
from .workflow import execute_task


@dataclass(frozen=True, slots=True)
class BatchReport:
    content: BlobRef
    head: BlobRef
    summary: MappingProxyType

    def as_dict(self) -> dict:
        return {"schema": "behave.evaluation-report/2", "content": self.content.as_dict(),
                "head": self.head.as_dict(), "summary": thaw_json(self.summary)}


def execution_status(report: BatchReport, reader: ContentReader) -> dict:
    """Human-facing stage counts, without changing historical report schemas."""
    counts = {"submitted": 0, "pending_judgment": 0, "judgment_unavailable": 0,
              "no_submission": 0, "rollout_unavailable": 0}
    for row in report.summary["candidates"]:
        if row["result"] is None:
            counts["rollout_unavailable"] += 1
            continue
        result = TaskResult.from_bytes(read_verified(reader, BlobRef.from_dict(thaw_json(row["result"]))))
        key = "rollout_unavailable" if result.rollout_status == "interrupted" else result.rollout_status
        counts[key] += 1
        counts["pending_judgment"] += result.status == "pending_judgment"
        counts["judgment_unavailable"] += result.submission is not None and result.status == "no_result"
    return counts


def case_of(plan: BatchPlan, payload: dict) -> tuple[int, dict]:
    item = fields(payload, {"index", "row"})
    index = item["index"]
    if type(index) is not int or not 0 <= index < len(plan.jobs):
        raise RecordError("evaluation-case-invalid", "invalid candidate index")
    row = fields(item["row"], {"task_id", "attempt_id", "run_key", "result", "score", "status", "error",
                               "testbench", "testbench_error"})
    job = plan.jobs[index]
    if (row["task_id"], row["attempt_id"], row["run_key"]) != (
            job.task.task_id, job.attempt.attempt_id, BlobRef.of(job.to_bytes()).sha256):
        raise RecordError("evaluation-case-mismatch", "result belongs to another candidate")
    for key in ("error", "testbench_error"):
        if row[key] is not None:
            error = fields(row[key], {"type", "code"})
            text(error["type"], "error type")
            text(error["code"], "error code")
    if row["testbench"] is not None:
        BlobRef.from_dict(row["testbench"])
    return index, row


def _costs(ledger: CostLedger) -> dict:
    # Unresolved overlaps priced/unpriced: observing a fee does not prove the
    # remote invocation finished or its invoice is final.
    return {"known_estimates_by_currency": thaw_json(ledger.cost_by_currency()),
            "priced_dispatches": len(ledger.charges), "unpriced_dispatches": ledger.unpriced_dispatches,
            "unresolved_dispatches": ledger.unresolved_dispatches}


def summarize(plan: BatchPlan, rows: list[dict], reader: ContentReader, *, accounting=None) -> dict:
    if len(rows) != len(plan.jobs):
        raise RecordError("evaluation-case-invalid", "one row is required per candidate")
    candidates, ledgers = [], []
    for index, item in enumerate(rows):
        _, row = case_of(plan, {"index": index, "row": item})
        billed = None
        settled = 0.0
        if row["result"] is None:
            if row["score"] is not None or row["status"] != "no_result" or row["error"] is None:
                raise RecordError("evaluation-case-mismatch", "missing result requires an explicit unavailable outcome")
        else:
            result = TaskResult.from_bytes(read_verified(reader, BlobRef.from_dict(row["result"])))
            job = plan.jobs[index]
            if (result.task_id, result.release, result.revision, result.attempt_id) != (
                    job.task.task_id, job.task.release, job.task.revision, job.attempt.attempt_id):
                raise RecordError("evaluation-case-mismatch", "sealed result belongs to another candidate")
            if canonical_json([row["score"], row["status"], row["error"]]) != canonical_json([
                    result.score, result.status, None]):
                raise RecordError("evaluation-case-mismatch", "score or disposition differs from the sealed result")
            account = load_accounting(reader, result)
            settled = result.final_score
            if accounting is not None:
                accounting[BlobRef.from_dict(row["result"])] = account
            admission = plan.admission_identity(job)
            if account.job != BlobRef.of(job.to_bytes()) or account.model_admission != admission:
                raise RecordError("evaluation-case-mismatch", "accounting execution differs from the evaluation plan")
            ledgers.append(account.billing)
            billed = {"accounting": result.accounting.as_dict(), "source": account.billing_source,
                      **_costs(account.billing)}
        candidates.append({**row, "billing": billed, "final_score": settled})
    return _summary(plan, candidates, ledgers)


def summarize_recorded(plan, rows, reader, *, accounting=None):
    """Use the execution producer's cost records without reconstructing traces."""
    candidates, ledgers = [], []
    for row in rows:
        billed = None
        settled = 0.0
        if row["result"] is not None:
            result = json.loads(reader.read(BlobRef.from_dict(row["result"])))
            settled = final_score_from_record(result)
            account = json.loads(reader.read(BlobRef.from_dict(result["accounting"])))
            ledger = CostLedger(tuple(Charge(**charge) for charge in account["charges"]),
                                account["unpriced_dispatches"], account["unresolved_dispatches"])
            ledgers.append(ledger)
            billed = {"accounting": result["accounting"], "source": account["billing_source"], **_costs(ledger)}
        candidates.append({**row, "billing": billed, "final_score": settled})
    return _summary(plan, candidates, ledgers)


def _summary(plan, candidates, ledgers):
    total = CostLedger.combine(tuple(ledgers))
    missing = len(candidates) - len(ledgers)
    scores = [row["score"] for row in candidates if row["score"] is not None]
    pending = sum(row["status"] == "pending_judgment" for row in candidates)
    return {"schema": "behave.evaluation-summary/2", "plan": plan.identity.as_dict(), "candidates": candidates,
            "requested": len(candidates), "scored": len(scores), "no_result": len(candidates) - len(scores) - pending,
            **({"pending_judgment": pending} if pending else {}),
            "no_submission": sum(row["status"] == "no_submission" for row in candidates),
            "mean_on_scored": None if not scores else sum(scores) / len(scores),
            "mean": final_score(*(row["final_score"] for row in candidates)) if candidates else None,
            "billing": {"basis": "recorded-api-estimates", **_costs(total),
                        "accounted_candidates": len(ledgers), "unavailable_candidates": missing,
                        "fully_accounted": not (missing or total.unpriced_dispatches or total.unresolved_dispatches)}}


def read_batch_report(reader, head, **_):
    """Read an existing producer report; offline audits use load_batch_report."""
    terminal = json.loads(reader.read(head))
    ref = BlobRef.from_dict(terminal["payload"]["summary"])
    return BatchReport(ref, head, metadata(json.loads(reader.read(ref))))


def load_batch_report(reader: ContentReader, head: BlobRef, *, accounting=None) -> BatchReport:
    """Verify a sealed report without candidate directories or an execution service."""
    events = load_chain(reader, head)
    if len(events) < 3 or events[0].kind != "evaluation_started" or events[-1].kind != "evaluation_finished":
        raise RecordError("evaluation-report-incomplete", "a completed report is required")
    plan_ref = BlobRef.from_dict(fields(thaw_json(events[0].payload), {"plan"})["plan"])
    plan = BatchPlan.from_bytes(read_verified(reader, plan_ref), reader)
    rows = {}
    for event in events[1:-1]:
        if event.kind != "evaluation_case":
            raise RecordError("evaluation-case-invalid", "unexpected evaluation event")
        index, row = case_of(plan, thaw_json(event.payload))
        if index in rows:
            raise RecordError("evaluation-case-invalid", "repeated candidate index")
        rows[index] = row
    if len(rows) != len(plan.jobs):
        raise RecordError("evaluation-case-invalid", "missing candidate rows")
    ref = BlobRef.from_dict(fields(thaw_json(events[-1].payload), {"summary"})["summary"])
    expected = summarize(plan, [rows[i] for i in range(len(rows))], reader, accounting=accounting)
    recorded = json.loads(read_verified(reader, ref))
    # Earlier sealed summaries predate the derived final-score view. Their
    # raw outcomes stay readable and remain available for rejudging.
    if "mean" not in recorded:
        expected.pop("mean")
        for row in expected["candidates"]:
            row.pop("final_score")
    if canonical_json(recorded) != canonical_json(expected):
        raise RecordError("evaluation-summary-mismatch", "summary differs from sealed candidates and accounting")
    return BatchReport(ref, head, metadata(expected))


def run_batch(*, plan: BatchPlan, reader: ContentReader, directory: str | Path,
                   judge: JudgeBackend | None = None, testbench: TBBackend | None = None,
                   cancelled: Callable[[], bool] = lambda: False, accounting=None,
                   report_summary=summarize, report_reader=load_batch_report,
                   cpu_resources=None, on_case: Callable[[int], None] | None = None,
                   case_retries: int = 0, retry_case=None, accept_case=None,
                   case_scheduler=None,
                   observe_trajectory: Callable | None = None,
                   recovery=None) -> BatchReport:
    """Return every requested candidate, including no-result and application errors.

    Completed histories are read without launching workers. Crashed, uncommitted
    candidates resume through their phase journals; unknown calls are never
    redispatched. Before any new admission, all old candidate lifetime owners
    (including skipped completed rows) must be quiescent. A completed batch is
    immutable, even if some results are absent.
    """
    if type(plan) is not BatchPlan or not callable(cancelled):
        raise ValueError("evaluation backend/configuration mismatch")
    if type(case_retries) is not int or case_retries < 0:
        raise ValueError("case_retries must be a nonnegative integer")
    if plan.score_on_submit:
        if judge is None or judge.identity != plan.judge_backend:
            raise ValueError("evaluation backend/configuration mismatch")
        if (plan.testbench is None) != (testbench is None) or (
                testbench is not None and testbench.identity != plan.testbench.backend):
            raise ValueError("evaluation testbench configuration mismatch")
    root = RunStore(directory)
    with root.owner():
        tape = Tape(root.journal("report"))
        if recovery is not None:
            recovery.validate_plan(plan)
            recovery.publish(root.objects)
        plan_ref = root.objects.put(plan.to_bytes())
        # Candidates in one task group share the same immutable input bytes.
        # Deduplicate before publication, avoiding repeated NFS fsync/readback.
        inputs = tuple(root.objects.put(data) for data in plan.input_blobs())
        tape.record("evaluation_started", {"plan": plan_ref.as_dict()},
                    (plan_ref, *inputs, *((recovery.head,) if recovery is not None else ())))
        completed = {}
        while tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "evaluation_case":
            index, row = case_of(plan, thaw_json(tape.take("evaluation_case").payload))
            if index in completed:
                raise RecordError("evaluation-case-invalid", "repeated candidate index")
            completed[index] = row
            if case_scheduler is not None:
                case_scheduler.observe(index, row, root.objects)
            if on_case is not None and (accept_case is None or accept_case(row, root.objects)):
                on_case(index)
        if tape.cursor < len(tape.events):
            terminal = tape.take("evaluation_finished")
            tape.finish()
            tape.journal.seal()
            return report_reader(root.objects, BlobRef.of(terminal.to_bytes()), accounting=accounting)
        if recovery is not None:
            for event in recovery.cases:
                index, row = case_of(plan, thaw_json(event.payload))
                if index in completed:
                    if canonical_json(completed[index]) != canonical_json(row):
                        raise RecordError("batch-recovery-invalid", "imported candidate changed on replay")
                    continue
                tape.record("evaluation_case", {"index": index, "row": row}, event.artifacts)
                completed[index] = row
                if case_scheduler is not None:
                    case_scheduler.observe(index, row, root.objects)
                if on_case is not None and (accept_case is None or accept_case(row, root.objects)):
                    on_case(index)
        pending_jobs = tuple((index, job) for index, job in enumerate(plan.jobs) if index not in completed)
        recorded = {ref for ref, _ in plan.recorded_admissions}
        if any(BlobRef.of(job.to_bytes()) in recorded for _, job in pending_jobs):
            raise RecordError("batch-recovery-invalid", "recorded admission requires an imported or replayed case")
        if pending_jobs:
            # This barrier precedes EVERY new worker, not merely replay of the
            # old candidate. Fresh in-memory pools have forgotten old quarantine.
            # Pure report finalization needs no admission and remains available.
            for index, job in enumerate(plan.jobs):
                key = BlobRef.of(job.to_bytes()).sha256
                run = RunStore(root.directory / "candidates" / key)
                if not run.directory.exists():
                    if recovery is not None and index in recovery.indices:
                        # Imported results start no local worker. The operator
                        # fenced source workers before exporting this snapshot.
                        continue
                    if index in completed and (completed[index].get("error") or {}).get("code") != "candidate-not-admitted":
                        raise RecordError("batch-recovery-history-missing", "completed candidate history is missing")
                    continue
                with run.owner():
                    require_task_quiescent(run, run_key=key)

        cpus = cpu_resources or CpuResources(min(plan.verification.concurrency, plan.workers,
                                                 max(1, len(pending_jobs))))
        if judge is not None:
            judge = ScheduledJudge(judge, cpus)
        if testbench is not None:
            testbench = ScheduledTestbench(testbench, cpus)
        cpus.write_snapshot(root.directory / "resources.json")

        def one(job):
            # Paths derive from content, never user task/attempt identifiers.
            key = BlobRef.of(job.to_bytes()).sha256
            candidate_directory = root.directory / "candidates" / key
            run = RunStore(candidate_directory)
            row = {"task_id": job.task.task_id, "attempt_id": job.attempt.attempt_id,
                   "run_key": key, "result": None, "score": None, "status": "no_result",
                   "error": None, "testbench": None, "testbench_error": None}
            closure = []
            try:
                for attempt in range(case_retries + 1):
                    # Keep the candidate slot and its peers fixed. A fresh run
                    # directory owns each infrastructure retry's RPC journals.
                    run = RunStore(candidate_directory if attempt == 0 else
                                   candidate_directory / f"retry_{attempt:06d}")
                    began = time.monotonic()
                    report: TaskReport = execute_task(reader=reader, job=job, run=run,
                        judge=judge if plan.score_on_submit else None, cancelled=cancelled,
                        judge_admission=pool, model_pool=model_pool, cpu_resources=cpus,
                        **({"observe_trajectory": observe_trajectory} if observe_trajectory is not None else {}))
                    _, refs = run.seal("task")
                    root.objects.publish_owned(run.objects, refs)
                    closure.extend(refs)
                    if case_retries:
                        install_exclusive(candidate_directory, f"attempt_{attempt:06d}.json", canonical_json({
                            "attempt": attempt, "result": report.content.as_dict(),
                            "elapsed_seconds": time.monotonic() - began,
                            "failure_code": report.result.failure_code,
                            "directory": str(run.directory)}))
                    if retry_case is None or not retry_case(report.result, run.objects):
                        break
                    # Never launch a replacement while the previous owner has
                    # unaccounted local processes. No other candidate is redone.
                    require_task_quiescent(run, run_key=key)
                    if cancelled() or attempt == case_retries:
                        break
                row.update(result=report.content.as_dict(), score=report.result.score, status=report.result.status)
            except Exception as error:
                # Do not expose paths or private traceback text in the public summary.
                # Keep the candidate journals intact, including any calls, charges,
                # submission and cleanup receipts recorded before this failure.
                if not isinstance(error, (RecordError, AttemptInterrupted, OSError)):
                    logging.getLogger(__name__).exception("Candidate execution failed: %s", key)
                code = "storage-unavailable" if isinstance(error, OSError) else f"candidate-{type(error).__name__}"
                row["error"] = {"type": type(error).__name__, "code": getattr(error, "code", code)}
                return row, ()
            if (plan.score_on_submit and job.setting == "joint" and plan.testbench is not None
                    and report.result.submission is not None):
                tb_ref, row["testbench_error"], refs = record_final_testbench(
                    task=job.task, result=report.result, run=run, policy=plan.testbench,
                    backend=testbench, admission=pool, cancelled=cancelled)
                row["testbench"] = None if tb_ref is None else tb_ref.as_dict()
                closure.extend(refs)
                root.objects.publish_owned(run.objects, refs)
            # Publish the explicit immutable closure, not a directory walk. Readers
            # can audit this batch without mutable candidate work directories.
            # Every owned closure was published once at its producer boundary.
            return row, tuple(dict.fromkeys(closure))

        if pending_jobs:
            pools = shared_pools(verification=plan.verification, model=plan.model_admission)
            pool, model_pool = (pools[k] for k in ("verification", "model"))
            with ExitStack() as resources:
                workers = resources.enter_context(ThreadPoolExecutor(
                    max_workers=plan.workers, thread_name_prefix="behave-batch"))
                pending = dict(pending_jobs)
                futures = {} if case_scheduler is not None else {
                    workers.submit(one, job): index for index, job in pending_jobs}
                if case_scheduler is None:
                    pending.clear()
                def finished():
                    if case_scheduler is None:
                        yield from as_completed(futures)
                        return
                    while pending or futures:
                        if not cancelled():
                            for index in case_scheduler.admit(pending, plan.workers - len(futures)):
                                futures[workers.submit(one, pending.pop(index))] = index
                        if not futures:
                            return
                        yield next(as_completed(futures))

                for future in finished():
                    index = futures.pop(future)
                    row, refs = future.result()
                    tape.record("evaluation_case", {"index": index, "row": row}, refs)
                    completed[index] = row
                    if case_scheduler is not None:
                        case_scheduler.observe(index, row, root.objects)
                    if on_case is not None and (accept_case is None or accept_case(row, root.objects)):
                        on_case(index)
                    cpus.write_snapshot(root.directory / "resources.json")
                # Keep the shared report's index mapping without starting a
                # solver for the unused reserve. These are not zero-score
                # training candidates or observations of task difficulty.
                for index, job in pending.items():
                    row = {"task_id": job.task.task_id, "attempt_id": job.attempt.attempt_id,
                           "run_key": BlobRef.of(job.to_bytes()).sha256,
                           "result": None, "score": None, "status": "no_result",
                           "error": {"type": "NotAdmitted", "code": "candidate-not-admitted"},
                           "testbench": None, "testbench_error": None}
                    tape.record("evaluation_case", {"index": index, "row": row})
                    completed[index] = row
        rows = [completed[index] for index in range(len(plan.jobs))]
        summary = report_summary(plan, rows, root.objects, accounting=accounting)
        ref = root.objects.put(canonical_json(summary))
        terminal = tape.record("evaluation_finished", {"summary": ref.as_dict()}, (ref,))
        tape.finish()
        tape.journal.seal()
        return BatchReport(ref, BlobRef.of(terminal.to_bytes()), metadata(summary))


@dataclass(frozen=True)
class BatchRecovery:
    reader: object
    head: BlobRef
    plan: BatchPlan
    events: tuple
    cases: tuple
    allow_append: bool = False

    @classmethod
    def load(cls, reader, head):
        events = load_chain(reader, head, verify_artifacts=False)
        return cls.from_events(reader, events)

    @classmethod
    def from_events(cls, reader, events):
        if not events or events[0].kind != "evaluation_started":
            raise RecordError("batch-recovery-invalid", "source needs a recorded batch plan")
        plan = BatchPlan.from_bytes(read_verified(reader,
            BlobRef.from_dict(thaw_json(events[0].payload["plan"]))), reader)
        cases, seen = [], set()
        for event in events[1:]:
            if event.kind == "evaluation_finished":
                if event is not events[-1]:
                    raise RecordError("batch-recovery-invalid", "events follow a finished batch")
                continue
            if event.kind != "evaluation_case":
                raise RecordError("batch-recovery-invalid", "unexpected source event")
            index, row = case_of(plan, thaw_json(event.payload))
            if index in seen:
                raise RecordError("batch-recovery-invalid", "duplicate source candidate")
            seen.add(index)
            if row["result"] is None:
                continue
            result = TaskResult.from_bytes(read_verified(reader, BlobRef.from_dict(row["result"])))
            job = plan.jobs[index]
            if (result.task_id != job.task.task_id or result.release != job.task.release or
                    result.revision != job.task.revision or result.attempt_id != job.attempt.attempt_id):
                raise RecordError("batch-recovery-invalid", "result belongs to another candidate")
            if result.failure_stage == "solver" or result.status == "pending_judgment":
                continue
            if canonical_json([row["score"], row["status"], row["error"]]) != canonical_json(
                    [result.score, result.status, None]):
                raise RecordError("batch-recovery-invalid", "candidate differs from its original result")
            cases.append(event)
        return cls(reader, BlobRef.of(events[-1].to_bytes()), plan, events, tuple(cases))

    @property
    def indices(self):
        return frozenset(event.payload["index"] for event in self.cases)

    def validate_plan(self, plan):
        if (len(plan.jobs) < len(self.plan.jobs) or
                (len(plan.jobs) != len(self.plan.jobs) and not self.allow_append)):
            raise RecordError("batch-recovery-invalid", "candidate slots changed")
        for index in self.indices:
            if plan.jobs[index].to_bytes() != self.plan.jobs[index].to_bytes():
                raise RecordError("batch-recovery-invalid", "completed candidate identity changed")

    def publish(self, destination):
        destination.publish_owned(self.reader, tuple(dict.fromkeys(
            ref for event in self.events for ref in event.artifacts)))
        for event in self.events:
            destination.put(event.to_bytes())
