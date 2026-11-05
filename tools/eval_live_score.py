"""Score committed candidates while the remaining Eval rollouts continue.

The source run must already write durable candidate results on shared storage.
Only the new named judgment is owned here; the active rollout report is read
without taking its batch lock. Scoring uses the ordinary rejudge_task backend.
"""

import argparse
import json
import os
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from pathlib import Path
from threading import Event


def committed_cases(directory):
    """Read atomic producer commits, not approximate progress/trace status."""
    cases, finished = {}, False
    for path in sorted((directory / "run/report").glob("*.json")):
        event = json.loads(path.read_bytes())
        if event["kind"] == "evaluation_case":
            cases[event["payload"]["index"]] = event["payload"]["row"]
        elif event["kind"] == "evaluation_finished":
            finished = True
    return cases, finished


def score_live(root, name, *, workers, policy, poll_seconds, cancelled=lambda: False):
    from behave.agent.admission import ResourcePool
    from behave.agent.cpu_resources import CpuResources
    from behave.agent.replay import Tape
    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.core.records import canonical_json, thaw_json
    from behave.runtime.records import write_json
    from behave.task_execution.batch import case_of, summarize_recorded
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.task_execution.recovery import require_task_quiescent
    from behave.task_execution.rejudge import judgment_run, rejudge_task
    from behave.task_execution.resources import ScheduledJudge
    from behave.task_execution.result import TaskResult
    from behave.task_execution.run_store import RunStore
    from behave.task_execution.simulator_judge import SimulatorJudge

    root = Path(root)
    source = RunStore(root / "run")
    target = judgment_run(source, name)
    inputs = DirectoryReader(root / "inputs")
    first = json.loads((root / "run/report/000000.json").read_bytes())
    original_plan = BlobRef.from_dict(first["payload"]["plan"])
    plan = BatchPlan.from_bytes(source.objects.read(original_plan), source.objects)
    cpus = CpuResources(workers)
    judge = ScheduledJudge(SimulatorJudge(), cpus)
    plan = replace(plan, judge_backend=judge.identity, score_on_submit=True, workers=workers,
                   verification=replace(plan.verification, concurrency=workers))
    pool = ResourcePool(plan.verification)
    JudgePolicy.from_bytes(policy)

    with target.owner():
        config = Tape(target.journal("task"))
        policy_ref = target.objects.put(policy)
        config.record("live_rejudgment_started", {"source_plan": original_plan.as_dict(),
            "policy": policy_ref.as_dict(), "workers": workers, "retry_testbench": False},
            (original_plan, policy_ref))
        config.finish()
        tape = Tape(target.journal("report"))
        plan_ref = target.objects.put(plan.to_bytes())
        tape.record("evaluation_started", {"plan": plan_ref.as_dict()}, (plan_ref,))
        completed = {}
        while tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "evaluation_case":
            index, row = case_of(plan, thaw_json(tape.take("evaluation_case").payload))
            completed[index] = row
        if tape.cursor < len(tape.events):
            ref = BlobRef.from_dict(thaw_json(tape.take("evaluation_finished").payload)["summary"])
            tape.finish()
            return json.loads(target.objects.read(ref))

        def one(index, row):
            case_of(plan, {"index": index, "row": row})
            if row["result"] is None:
                return row
            previous = BlobRef.from_dict(row["result"])
            result = TaskResult.from_bytes(source.objects.read(previous))
            if result.submission is None:
                return row
            old = RunStore(source.directory / "candidates" / row["run_key"], objects=source.objects)
            with old.owner():
                require_task_quiescent(old, run_key=row["run_key"])
            candidate = RunStore(target.directory / "candidates" / row["run_key"], objects=target.objects)
            # Reuses ordinary scoring, CPU scheduling and per-task recovery.
            ref = rejudge_task(previous=previous, reader=inputs, run=candidate, judge=judge,
                judge_policy=policy, admission=pool, cancelled=cancelled)
            result = TaskResult.from_bytes(target.objects.read(ref))
            return {**row, "result": ref.as_dict(), "score": result.score,
                    "status": result.status, "error": None}

        def progress(pending):
            rows = []
            for index, row in sorted(completed.items()):
                result = (None if row["result"] is None else
                          TaskResult.from_bytes(target.objects.read(BlobRef.from_dict(row["result"]))))
                judgment = None if result is None else result.judgment
                rows.append({"index": index, "task": row["task_id"], "status": row["status"],
                    "result": row["result"], "submitted": result is not None and result.submission is not None,
                    "rtl_score": None if judgment is None else judgment.rtl.score,
                    "behavior_score": None if judgment is None or judgment.behavior is None else judgment.behavior.score})
            write_json(target.directory / "progress.json", {"requested": len(plan.jobs),
                "completed": len(rows), "scoring": len(pending), "candidates": rows,
                "resources": cpus.snapshot()})

        write_json(target.directory / "runtime.json", {"state": "running", "mode": "committed-candidates"})
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="live-score") as executor:
            pending, dispatched = {}, set(completed)
            while not cancelled():
                rows, finished = committed_cases(root)
                for index, row in rows.items():
                    if index not in dispatched:
                        pending[executor.submit(one, index, row)] = index
                        dispatched.add(index)
                if finished and len(rows) != len(plan.jobs):
                    raise ValueError("finished source report has missing candidates")
                if not pending and finished:
                    break
                if not pending:
                    status_path = root / "control/status.json"
                    if status_path.exists() and json.loads(status_path.read_bytes()).get("finished_at") and not finished:
                        raise RuntimeError("source stopped without a complete report; partial scores retained")
                    Event().wait(poll_seconds)
                    continue
                done, _ = wait(pending, timeout=poll_seconds, return_when=FIRST_COMPLETED)
                for future in done:
                    index, row = pending.pop(future), future.result()
                    candidate = RunStore(target.directory / "candidates" / row["run_key"], objects=target.objects)
                    _, closure = candidate.seal("task")
                    refs = tuple(BlobRef.from_dict(row[key]) for key in ("result", "testbench") if row[key] is not None)
                    tape.record("evaluation_case", {"index": index, "row": row}, (*refs, *closure))
                    completed[index] = row
                progress(pending)
        if cancelled():
            raise RuntimeError("live score interrupted; committed candidate scores retained")
        summary = summarize_recorded(plan, [completed[i] for i in range(len(plan.jobs))], target.objects)
        reference = target.objects.put(canonical_json(summary))
        tape.record("evaluation_finished", {"summary": reference.as_dict()}, (reference,))
        tape.finish()
        target.seal("report")
        write_json(target.directory / "summary.json", summary)
        write_json(target.directory / "runtime.json", {"state": "archived", "mode": "committed-candidates"})
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    args = parser.parse_args()
    config = json.loads(args.runtime.read_bytes())
    source = Path(config["source"])
    # Pin Sim and all scoring machinery to the already reviewed source snapshot.
    sys.path[:0] = [str(source), str(source / "src")]
    from behave.core.process_limits import configure_worker_process_limit
    from behave.eval.scoring import scoring_environment
    from behave.runtime.jobs import wait_control
    from behave.runtime.records import write_json
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy

    os.sched_setaffinity(0, config["cpu_ids"])
    configure_worker_process_limit(config.get("process_limit"))
    root = Path(config["run"])
    policy = JudgePolicy.from_bytes(Path(config["policy"]).read_bytes())
    if (policy.command_version, policy.authority_version) != (api.COMMAND_VERSION, api.RESULT_VERSION):
        raise ValueError("live scorer differs from the frozen comparison policy")
    with scoring_environment() as cancelled:
        score_live(root, config["name"], workers=config["workers"], policy=policy.to_bytes(),
                   poll_seconds=config["poll_seconds"], cancelled=cancelled)
    # Scores can finish before the serving owner publishes its cleanup receipt.
    # Archive metadata only after that owner exits; never rewrite its live state.
    wait_control(root / "control")
    if not json.loads((root / "cleanup.json").read_bytes())["cleanup_complete"]:
        raise RuntimeError("source serving cleanup remains incomplete")
    runtime = json.loads((root / "runtime.json").read_bytes())
    if runtime["state"] != "archived":
        write_json(root / "serving_runtime.json", runtime)
        write_json(root / "runtime.json", {"state": "archived", "host": None,
            "local_directory": str(root), "storage": "direct-nfs", "finished_at": runtime["finished_at"]})


if __name__ == "__main__":
    main()
