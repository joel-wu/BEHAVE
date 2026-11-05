"""Public task -> supervised solver -> frozen submission -> shared judge.

Eval and train call this boundary. Solver, supervision, judging and workflow
retain independent journals; the workflow never retries an agent because its
judge failed. Final TB observation is separate and cannot alter this result.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.replay import Tape
from behave.agent.trajectory import trajectory
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, canonical_json, fields, thaw_json
from behave.data.published import load_published_task

from .accounting import PHASES, encode_accounting
from .judge import JudgeBackend, judge_submission
from .result import TaskReport, TaskResult
from .run_store import RunStore
from .solver import SolverJob, run_solver_process


def execute_task(*, reader: ContentReader, job: SolverJob, run: RunStore, judge: JudgeBackend | None = None,
                 cancelled: Callable[[], bool] = lambda: False,
                 judge_admission: ResourcePool | None = None, model_pool: ResourcePool | None = None,
                 comparison_cpu_pool: dict | None = None, cpu_resources=None,
                 observe_trajectory: Callable | None = None) -> TaskReport:
    """Caller holds no model/EDA lease during another stage or its queue wait.

    Cooperating coordinators use a nonblocking directory lock. It does not fence
    previous remote calls: old pending phase requests still fail closed. Only the
    fixed solver worker is launched; plugins here are trusted judge adapters.
    """
    if type(job) is not SolverJob or type(run) is not RunStore or not callable(cancelled):
        raise TypeError("execute_task requires a SolverJob, RunStore and cancellation predicate")
    if judge_admission is not None and type(judge_admission) is not ResourcePool:
        raise TypeError("judge admission requires ResourcePool")
    if model_pool is not None and type(model_pool) is not ResourcePool:
        raise TypeError("model admission requires ResourcePool")
    with run.owner():
        task = load_published_task(reader, job.task.release, job.task.task_id)
        if task.public != job.task:
            raise RecordError("workflow-public-task-mismatch", "solver inputs differ from the pinned publication")
        objects = run.objects
        plan = objects.put(canonical_json({
            "schema": "behave.task-execution/4", "solver": job.to_bytes().decode("utf-8"),
            "judge_backend": None if judge is None else judge.identity,
            "judge_admission": None if judge_admission is None else judge_admission.policy.identity,
            "model_admission": None if model_pool is None else model_pool.policy.identity,
        }))
        # The manifest is authoritative over the private side, not the public job.
        release = objects.put(task.release.to_bytes())
        manifest = objects.put(task.manifest.to_bytes())
        tape = Tape(run.journal("task"))
        tape.record("task_started", {"plan": plan.as_dict()}, (plan, release, manifest))
        if tape.cursor < len(tape.events):
            terminal = tape.take("task_finished")
            reference = BlobRef.from_dict(fields(thaw_json(terminal.payload), {"result"})["result"])
            result = TaskResult.from_bytes(read_verified(objects, reference))
            tape.finish()
            return TaskReport(result, reference, BlobRef.of(terminal.to_bytes()))

        solver = judgment = None
        failure_stage = failure_code = None
        stage = "solver"
        try:
            solver = run_solver_process(job, journal=run.journal("solver"), supervision=run.journal("supervision"),
                                        cancelled=cancelled, model_pool=model_pool,
                                        provider=None if model_pool is None else run.journal("provider"),
                                        **({"cpu_resources": cpu_resources} if cpu_resources is not None else {}),
                                        **({"comparison_cpu_pool": comparison_cpu_pool}
                                           if comparison_cpu_pool is not None else {}))
            if solver.submission is not None and judge is not None:
                stage = "judge"
                judgment = judge_submission(task=task.judge, submission=solver.submission,
                                            journal=run.journal("judge"), backend=judge,
                                            cancelled=cancelled, admission=judge_admission)
        except AttemptInterrupted as error:
            failure_stage, failure_code = stage, error.code
        except Exception as error:
            if stage != "judge":
                raise
            logging.getLogger(__name__).exception("Judging failed; retaining the submitted rollout")
            failure_stage = "judge"
            failure_code = getattr(error, "code", f"judge-{type(error).__name__}")

        # Nothing here looks at source files or guesses submission from a workspace.
        # Read the records needed for accounting, without auditing every other
        # artifact in these producer-owned journals again at task completion.
        view = trajectory(run.journal("solver"), verify_artifacts=False)
        billing = view if model_pool is None else trajectory(run.journal("provider"), verify_artifacts=False)
        heads = {}
        closure = []
        for phase in PHASES:
            head, references = run.seal(phase)
            heads[phase] = None if head is None else head.as_dict()
            closure.extend(references)
        accounting = objects.put(encode_accounting(
            attempt_id=job.attempt.attempt_id, heads=heads, trajectory=view, billing=billing.billing,
            billing_source="solver" if model_pool is None else "provider"))
        result = TaskResult(task.public.task_id, task.public.release, task.public.revision, job.attempt.attempt_id,
                            None if heads["solver"] is None else BlobRef.from_dict(heads["solver"]),
                            None if solver is None else solver.attempt.reason,
                            None if solver is None else solver.submission, judgment,
                            failure_stage, failure_code, accounting)
        reference = objects.put(result.to_bytes())
        terminal = tape.record("task_finished", {"result": reference.as_dict()}, (reference, accounting, *closure))
        tape.finish()
        if observe_trajectory is not None:
            observe_trajectory(reference, view)
        return TaskReport(result, reference, BlobRef.of(terminal.to_bytes()))
