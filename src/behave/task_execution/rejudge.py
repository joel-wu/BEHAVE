"""Score saved submissions in a new report, without launching a solver.

Judgment runs share the original object store. They neither copy trajectories
nor change the original reports, submissions or model billing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor, as_completed, wait
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.replay import Tape
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import canonical_json, metadata, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task

from .batch import BatchReport, case_of, load_batch_report, read_batch_report, summarize, summarize_recorded
from .batch_plan import BatchPlan
from .judge import JudgeBackend, judge_submission
from .judge_policy import JudgePolicy
from .recovery import require_task_quiescent
from .result import TaskResult
from .run_store import RunStore, judgment_directory
from .testbench import TBBackend, record_final_testbench


def judgment_run(root: RunStore, name: str) -> RunStore:
    return RunStore(judgment_directory(root.directory, name), objects=root.objects)


def _selected_source(source, root, selection):
    """Read a published report, selecting exact saved results before loading jobs.

    This is producer-record reuse, not a new audit of every unrelated rollout's
    model history. Offline verification remains available via load_batch_report.
    The resulting subset keeps the original jobs, submissions and task releases.
    """
    from behave.agent.journal import Event

    paths = sorted((source.directory / "report").glob("*.json"), key=lambda p: (len(p.name), p.name))
    if not paths:
        raise ValueError("a completed evaluation report is required")
    terminal = Event.from_bytes(paths[-1].read_bytes())
    if terminal.kind != "evaluation_finished":
        raise ValueError("a completed evaluation report is required")
    head = root.objects.put(terminal.to_bytes())
    prior = read_batch_report(root.objects, head)
    raw_plan = parse_json(read_verified(root.objects, BlobRef.from_dict(thaw_json(prior.summary)["plan"])))
    rows = thaw_json(prior.summary)["candidates"]
    if not selection or set(selection) - {row["run_key"] for row in rows}:
        raise ValueError("selection must name existing candidates in this source report")
    chosen = [(job, row) for job, row in zip(raw_plan["jobs"], rows, strict=True)
              if row["run_key"] in selection]
    raw_plan["jobs"] = [job for job, _ in chosen]
    if "recorded_admissions" in raw_plan:
        keys = {row["run_key"] for _, row in chosen}
        raw_plan["recorded_admissions"] = [
            item for item in raw_plan["recorded_admissions"] if item["job"]["sha256"] in keys]
    plan = BatchPlan.from_bytes(canonical_json(raw_plan), root.objects)
    selected_rows = []
    for index, (job, (_, row)) in enumerate(zip(plan.jobs, chosen, strict=True)):
        row = {k: v for k, v in row.items() if k not in {"billing", "final_score"}}
        case_of(plan, {"index": index, "row": row})
        item = selection[row["run_key"]]
        if (item["task"], item["attempt_id"], item["setting"], item["release"], item["revision"]) != (
                job.task.task_id, job.attempt.attempt_id, job.setting,
                job.task.release.as_dict(), job.task.revision.as_dict()):
            raise ValueError("selected index entry belongs to a different task or attempt")
        if item["result"] is None:
            if row["result"] is not None or item["submission"] is not None:
                raise ValueError("selected missing result disagrees with source report")
        else:
            original = TaskResult.from_bytes(read_verified(root.objects, BlobRef.from_dict(item["result"])))
            if (original.task_id, original.attempt_id, original.release.as_dict(), original.revision.as_dict(),
                    None if original.submission is None else original.submission.as_dict()) != (
                    item["task"], item["attempt_id"], item["release"], item["revision"], item["submission"]):
                raise ValueError("selected saved result differs from the indexed submission")
            row.update(result=item["result"], score=original.score, status=original.status, error=None)
        selected_rows.append(row)
    return head, (head, prior.content), plan, selected_rows


def rejudge_task(*, previous: BlobRef, reader: ContentReader, run: RunStore, judge: JudgeBackend,
                  judge_policy: bytes | None, admission: ResourcePool,
                  cancelled: Callable[[], bool], retry_codes: tuple[str, ...] | None = None) -> BlobRef:
    """Rejudge a saved submission in a fresh run, without executing its solver.

    The caller stages the original result and its content closure into
    ``run.objects`` and establishes that the previous task is quiescent.
    Batch recovery and targeted offline rejudging share this implementation.
    """
    with run.owner():
        tape = Tape(run.journal("task"))
        policy_ref = None if judge_policy is None else run.objects.put(judge_policy)
        tape.record("rejudgment_started", {
            "previous": previous.as_dict(), "backend": judge.identity,
            "policy": None if policy_ref is None else policy_ref.as_dict(),
            "admission": admission.policy.identity,
            **({"retry_codes": list(retry_codes)} if retry_codes is not None else {}),
        }, (previous,) + (() if policy_ref is None else (policy_ref,)))
        if tape.cursor < len(tape.events):
            reference = BlobRef.from_dict(thaw_json(tape.take("task_finished").payload)["result"])
            tape.finish()
            return reference
        original = TaskResult.from_bytes(read_verified(run.objects, previous))
        judgment, failure_stage, failure_code = None, None, None
        try:
            task = load_published_task(reader, original.release, original.task_id).judge
            if judge_policy is not None:
                task = replace(task, judge_policy=judge_policy)
            retained = None
            if retry_codes is not None and original.judgment is not None:
                retained = {name: component for name in ("rtl", "behavior")
                            if (component := getattr(original.judgment, name)) is not None
                            and (component.score is not None or component.code not in retry_codes)}
            judgment = judge_submission(task=task, submission=original.submission,
                journal=run.journal("judge"), backend=judge, admission=admission, cancelled=cancelled,
                retained=retained)
        except AttemptInterrupted as error:
            failure_stage, failure_code = "judge", error.code
        except Exception as error:
            logging.getLogger(__name__).exception("Replacement judgment failed; submission remains available")
            failure_stage = "judge"
            failure_code = getattr(error, "code", f"judge-{type(error).__name__}")
        head, closure = run.seal("judge")
        # Only the judge head changes. Model history and billing remain exact.
        accounting = parse_json(read_verified(run.objects, original.accounting))
        accounting["heads"]["judge"] = None if head is None else head.as_dict()
        accounting_ref = run.objects.put(canonical_json(accounting))
        result = replace(original, judgment=judgment, failure_stage=failure_stage,
                         failure_code=failure_code, accounting=accounting_ref)
        reference = run.objects.put(result.to_bytes())
        tape.record("task_finished", {"result": reference.as_dict()}, (reference, accounting_ref, *closure))
        tape.finish()
        run.seal("task")
        return reference


def rejudge_batch(*, directory: str | Path, name: str, reader: ContentReader, judge: JudgeBackend,
                  previous: str | None = None, judge_policy: bytes | None = None,
                  testbench: TBBackend | None = None, workers: int | None = None,
                  all_submitted: bool = False,
                  retry_codes: tuple[str, ...] | None = None,
                  selection: dict[str, dict] | None = None, retry_testbench: bool = True,
                  objects: DirectoryStore | None = None,
                  executor: Executor | None = None,
                  on_case: Callable[[dict], None] | None = None,
                  cancelled: Callable[[], bool] = lambda: False, cpu_resources=None) -> BatchReport:
    """Score saved submissions and final TBs without generating new candidates.

    A name identifies one scoring attempt. Reusing it resumes/reads that attempt.
    Use a new name (and previous=<old name>) after repairing the evaluator again.
    Settled scores are retained unless ``all_submitted`` requests a fresh judge
    pass over every submission. Neither mode runs the model again. Existing
    available TB measurements are retained in both modes.
    ``workers`` controls candidate scoring concurrency, independently of rollouts.
    ``selection`` pins indexed result/submission bindings and emits only those
    candidates. ``retry_testbench=False`` preserves TB records without dispatch.
    An external ``executor`` shares candidate concurrency across archives; this
    call drains only its own futures and never shuts down the shared executor.
    ``on_case`` observes committed rows, not another scoring decision.
    """
    if judge_policy is not None:
        JudgePolicy.from_bytes(judge_policy)
    if all_submitted and retry_codes is not None:
        raise ValueError("choose a full pass or selected failure codes, not both")
    if previous == name:
        raise ValueError("a judgment run cannot be its own source")
    root = RunStore(directory, objects=objects)
    target = judgment_run(root, name)
    source = root if previous is None else judgment_run(root, previous)
    with root.owner(), target.owner():
        if selection is None:
            events = source.journal("report").load()
            if not events or events[-1].kind != "evaluation_finished":
                raise ValueError("a completed evaluation report is required")
            head, prior_closure = source.seal("report")
            prior = load_batch_report(root.objects, head)
            plan_ref = BlobRef.from_dict(thaw_json(events[0].payload)["plan"])
            plan = BatchPlan.from_bytes(read_verified(root.objects, plan_ref), root.objects)
            rows = thaw_json(prior.summary)["candidates"]
        else:
            head, prior_closure, plan, rows = _selected_source(source, root, selection)
        concurrency = plan.verification.concurrency if workers is None else workers
        plan = replace(plan, judge_backend=judge.identity, score_on_submit=True, workers=concurrency,
                       verification=replace(plan.verification, concurrency=concurrency))
        if retry_testbench and plan.testbench is not None and any(job.setting == "joint" for job in plan.jobs) and (
                testbench is None or testbench.identity != plan.testbench.backend):
            raise ValueError("scoring requires the plan's final testbench backend")
        # Bind this attempt to its source and any explicit scoring-policy override.
        config = Tape(target.journal("task"))
        policy_ref = None if judge_policy is None else root.objects.put(judge_policy)
        config.record("rejudgment_started", {"source": head.as_dict(), "backend": judge.identity,
            "policy": None if policy_ref is None else policy_ref.as_dict(),
            **({"all_submitted": True} if all_submitted else {}),
            **({"selection": selection} if selection is not None else {}),
            **({"retry_testbench": False} if not retry_testbench else {}),
            **({"retry_codes": list(retry_codes)} if retry_codes is not None else {})},
            (head,) + (() if policy_ref is None else (policy_ref,)))
        config.finish()
        _, config_closure = target.seal("task")
        tape = Tape(target.journal("report"))
        plan_ref = root.objects.put(plan.to_bytes())
        tape.record("evaluation_started", {"plan": plan_ref.as_dict()},
                    (plan_ref, *prior_closure, *config_closure))
        completed = {}
        while tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "evaluation_case":
            index, row = case_of(plan, thaw_json(tape.take("evaluation_case").payload))
            completed[index] = row
        if tape.cursor < len(tape.events):
            terminal = tape.take("evaluation_finished")
            tape.finish()
            read_report = load_batch_report if selection is None else read_batch_report
            return read_report(root.objects, BlobRef.of(terminal.to_bytes()))

        pending = [(i, row) for i, row in enumerate(rows) if i not in completed]
        from behave.agent.cpu_resources import CpuResources

        from .resources import ScheduledJudge, ScheduledTestbench

        cpus = cpu_resources or CpuResources(concurrency)
        judge = ScheduledJudge(judge, cpus)
        if testbench is not None:
            testbench = ScheduledTestbench(testbench, cpus)
        cpus.write_snapshot(target.directory / "resources.json")

        def selected(row):
            if retry_codes is None:
                return True
            if row["result"] is None:
                return False
            result = TaskResult.from_bytes(read_verified(root.objects, BlobRef.from_dict(row["result"])))
            if result.submission is None:
                return False
            if result.judgment is None:
                return (result.failure_code or "pending_judgment") in retry_codes
            return any(component is not None and component.score is None and component.code in retry_codes
                       for component in (result.judgment.rtl, result.judgment.behavior))

        selected_indices = {i for i, row in pending if selected(row)}

        def needs_testbench(index, row):
            if not retry_testbench or retry_codes is not None:
                return False  # A targeted score repair does not rerun the model's TB.
            if plan.jobs[index].setting != "joint" or plan.testbench is None:
                return False
            if row["testbench"] is None:
                return True
            report = parse_json(read_verified(root.objects, BlobRef.from_dict(row["testbench"])))
            return report["observation"]["status"] == "unavailable"

        # Retain the existing process-cleanup boundary before starting another
        # evaluator. A score failure alone is not proof its worker has exited.
        for index, row in pending:
            if index not in selected_indices:
                continue
            if row["result"] is None or (not all_submitted and row["score"] is not None
                                         and not needs_testbench(index, row)):
                continue
            original = TaskResult.from_bytes(read_verified(root.objects, BlobRef.from_dict(row["result"])))
            if original.submission is None:
                continue
            indexed_source = (() if selection is None or selection[row["run_key"]]["judgment"] == "on_submit"
                              else (judgment_run(root, selection[row["run_key"]]["judgment"]).directory,))
            for old in dict.fromkeys((root.directory, source.directory, target.directory, *indexed_source)):
                candidate = RunStore(old / "candidates" / row["run_key"], objects=root.objects)
                if candidate.directory.exists():
                    with candidate.owner():
                        require_task_quiescent(candidate, run_key=row["run_key"])

        def one(index, row):
            row = {key: value for key, value in row.items() if key not in {"billing", "final_score"}}
            if index not in selected_indices:
                return row
            if row["result"] is None or (not all_submitted and row["score"] is not None
                                         and not needs_testbench(index, row)):
                return row
            previous_ref = BlobRef.from_dict(row["result"])
            result = TaskResult.from_bytes(read_verified(root.objects, previous_ref))
            if result.submission is None:
                return row
            if cancelled():
                raise AttemptInterrupted("rejudgment-cancelled")
            candidate = RunStore(target.directory / "candidates" / row["run_key"], objects=root.objects)
            if all_submitted or result.score is None:
                reference = rejudge_task(previous=previous_ref, reader=reader, run=candidate, judge=judge,
                                         judge_policy=judge_policy, admission=pool, cancelled=cancelled,
                                         retry_codes=retry_codes)
                result = TaskResult.from_bytes(read_verified(root.objects, reference))
                row.update(result=reference.as_dict(), score=result.score, status=result.status, error=None)
            job = plan.jobs[index]
            if needs_testbench(index, row):
                tb_ref, row["testbench_error"], _ = record_final_testbench(
                    task=job.task, result=result, run=candidate, policy=plan.testbench,
                    backend=testbench, admission=pool, cancelled=cancelled)
                row["testbench"] = None if tb_ref is None else tb_ref.as_dict()
            return row

        pool = ResourcePool(plan.verification)
        execution = (ThreadPoolExecutor(max_workers=plan.workers) if executor is None
                     else nullcontext(executor))
        with execution as candidates:
            futures, first_error = {}, None
            try:
                for index, row in pending:
                    futures[candidates.submit(one, index, row)] = index
                for future in as_completed(futures):
                    try:
                        row = future.result()
                    except Exception as error:
                        # Preserve other completed candidates, including during
                        # cancellation, before reporting this archive's error.
                        if first_error is None:
                            first_error = error
                        continue
                    index = futures[future]
                    refs = tuple(BlobRef.from_dict(row[key]) for key in ("result", "testbench") if row[key] is not None)
                    # Include the existing accounting/diagnostics, not another
                    # score computed by the orchestration layer.
                    candidate = RunStore(target.directory / "candidates" / row["run_key"], objects=root.objects)
                    for phase in ("task", "testbench"):
                        _, closure = candidate.seal(phase)
                        refs += closure
                    tape.record("evaluation_case", {"index": index, "row": row}, refs)
                    completed[index] = row
                    cpus.write_snapshot(target.directory / "resources.json")
                    if on_case is not None:
                        on_case(row)
            finally:
                # A shared executor outlives this archive. Its files and owner
                # must remain in place until this archive's work has stopped.
                wait(futures)
            if first_error is not None:
                raise first_error
        summarize_results = summarize if selection is None else summarize_recorded
        summary = summarize_results(plan, [completed[i] for i in range(len(plan.jobs))], root.objects)
        reference = root.objects.put(canonical_json(summary))
        terminal = tape.record("evaluation_finished", {"summary": reference.as_dict()}, (reference,))
        tape.finish()
        target.seal("report")
        return BatchReport(reference, BlobRef.of(terminal.to_bytes()), metadata(summary))
