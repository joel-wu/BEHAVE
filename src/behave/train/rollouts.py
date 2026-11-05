"""Generate and score candidates, then seal training eligibility; no optimizer."""

import json
from collections.abc import Callable
from functools import partial

from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, ContentReader
from behave.core.records import thaw_json
from behave.task_execution.batch import read_batch_report, run_batch, summarize_recorded
from behave.task_execution.judge import JudgeBackend
from behave.task_execution.run_store import RunStore
from behave.task_execution.testbench import TBBackend

from .learning_report import PreparedTrainingReport
from .plan import TrainingPlan, chunks
from .pool import CompletedGroups, ReplenishingGroups, has_rollout, retry_empty_rollout
from .recorded import capture_token_statuses
from .recorded import record_rollouts as prepare_rollouts
from .training_data import TokenVerifier


def run_rollouts(
    *,
    plan: TrainingPlan,
    approved_manifest: BlobRef,
    reader: ContentReader,
    run: JournalDirectory,
    judge: JudgeBackend,
    testbench: TBBackend | None = None,
    verifier: TokenVerifier | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    on_prepared: Callable | None = None,
    update_groups: int | None = None,
    active_groups: int | None = None,
    empty_rollout_retries: int = 0,
    recovery=None,
) -> dict:
    """Resume committed candidates without changing groups, scores or accounting.

    A generated no-result remains in the batch. Runtime may replace empty
    transport failures in their own slots before whole-group racing completes.
    """
    if type(plan) is not TrainingPlan or type(run) is not JournalDirectory:
        raise TypeError("rollouts require TrainingPlan and JournalDirectory")
    if verifier is not None and verifier.identity != plan.requirements.adapter:
        raise ValueError("training verifier differs from the planned serving adapter")
    token_statuses = {}
    selection = (ReplenishingGroups(plan.group_ids, update_groups, active_groups,
        requirements=plan.requirements.as_dict(), verifier=verifier, token_statuses=token_statuses)
        if active_groups is not None else
        None if update_groups is None else CompletedGroups(plan.group_ids, update_groups))
    with run.owner():
        tape = Tape(run.journal("training"))
        objects = run.objects
        inputs = objects if tape.events else reader
        membership = plan.preflight(inputs, approved_manifest=approved_manifest)
        plan_ref = objects.put(plan.to_bytes())
        if not tape.events:
            objects.publish(reader, (plan.manifest, *membership))
        job_inputs = tuple(dict.fromkeys(objects.put(data) for job in plan.execution.jobs for data in job.input_blobs()))
        selection_requires_tokens = not tape.events or tape.events[0].payload.get("selection_requires_tokens", False)
        tape.record(
            "training_started",
            {"plan": plan_ref.as_dict(), "verifier": None if verifier is None else verifier.identity,
             **({} if not empty_rollout_retries else {"empty_rollout_retries": empty_rollout_retries}),
             **({} if active_groups is None else {"active_groups": active_groups}),
             **({"selection_requires_tokens": True} if selection is not None and selection_requires_tokens else {}),
             **({} if selection is None else {"update_groups": update_groups})},
            (plan_ref, plan.manifest, *job_inputs),
        )
        for payload, refs in chunks(membership):
            tape.record("training_membership", payload, refs)
        if tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "training_finished":
            terminal = tape.take("training_finished")
            tape.finish()
            prepared = PreparedTrainingReport(
                json.loads(objects.read(BlobRef.from_dict(thaw_json(terminal.payload["result"])))), plan, (), objects)
            if on_prepared is not None and prepared.summary.get("group_selection", {}).get("status") != "insufficient_groups":
                on_prepared(prepared)
            return prepared.summary
        child = RunStore(run.directory / "execution")
        accounting = {}
        def observe(reference, trajectory):
            capture_token_statuses(reference, trajectory, token_statuses,
                                   plan.requirements.as_dict(), verifier)
        report = run_batch(
            plan=plan.execution,
            reader=reader,
            directory=child.directory,
            judge=judge,
            testbench=testbench,
            cancelled=(cancelled if selection is None else lambda: cancelled() or selection.stop.is_set()),
            on_case=None if selection is None or active_groups is not None else selection.complete,
            case_scheduler=selection if active_groups is not None else None,
            case_retries=empty_rollout_retries,
            retry_case=retry_empty_rollout if empty_rollout_retries else None,
            accept_case=(partial(has_rollout, token_statuses=token_statuses,
                                 requirements=plan.requirements.as_dict(), verifier=verifier)
                         if selection is not None else None),
            accounting=accounting,
            observe_trajectory=observe,
            report_summary=summarize_recorded, report_reader=read_batch_report,
            **({"recovery": recovery} if recovery is not None else {}),
        )
        _, closure = child.seal("report")
        objects.publish_owned(child.objects, closure)
        prepared = prepare_rollouts(plan, objects, report, verifier, accounting=accounting,
                                    token_statuses=token_statuses)
        result = prepared.summary
        if selection is not None:
            result["group_selection"] = selection.summary()
        ref = objects.put(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                      allow_nan=False, separators=(",", ":")).encode())
        tape.record("training_finished", {"result": ref.as_dict()}, (*closure, ref))
        tape.finish()
        if on_prepared is not None and result.get("group_selection", {}).get("status") != "insufficient_groups":
            on_prepared(prepared)
        return result
