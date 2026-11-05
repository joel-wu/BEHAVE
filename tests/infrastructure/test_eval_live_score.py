import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

from test_evaluation import plan_for
from test_rejudge import Judge, local_rollout  # noqa: F401
from test_task_workflow import setup

from behave.eval.runner import run_evaluation
from behave.task_execution import workflow
from behave.task_execution.batch import load_batch_report
from behave.task_execution.rejudge import judgment_run
from behave.task_execution.run_store import RunStore
from tools.eval_live_score import committed_cases, score_live


def test_live_score_runs_before_long_tail_finishes_and_reuses_completed_results(
        tmp_path, publication, local_rollout, monkeypatch):
    base, _ = setup(tmp_path / "fixture", publication)
    jobs = tuple(replace(base, attempt=replace(base.attempt, attempt_id=name))
                 for name in ("early", "late"))
    root = tmp_path / "eval"
    root.mkdir()
    shutil.copytree(publication.root, root / "inputs")
    store = RunStore(root / "run")
    release, late_started = Event(), Event()
    original = workflow.run_solver_process

    def rollout(job, **kwargs):
        if job.attempt.attempt_id == "late":
            late_started.set()
            assert release.wait(15)
        return original(job, **kwargs)

    monkeypatch.setattr(workflow, "run_solver_process", rollout)
    judge, scored = Judge(unavailable=False), Event()
    evaluate = judge.evaluate

    def observed(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        scored.set()
        return result

    judge.evaluate = observed
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda: judge)
    policy = publication.reader.read(publication.task.judge_policy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(run_evaluation, plan=replace(plan_for(*jobs), score_on_submit=False),
                                reader=publication.reader, directory=store.directory, judge=Judge())
        try:
            assert late_started.wait(10)
            deadline = time.monotonic() + 10
            while not committed_cases(root)[0] and time.monotonic() < deadline:
                Event().wait(.01)
            assert len(committed_cases(root)[0]) == 1
            scoring = pool.submit(score_live, root, "live", workers=2, policy=policy, poll_seconds=.01)
            assert scored.wait(10)
            assert not execution.done()  # No full-batch/root lock or archive barrier.
        finally:
            release.set()
        assert execution.result(timeout=10).summary["requested"] == 2
        summary = scoring.result(timeout=10)
    assert summary["requested"] == 2 and summary["mean"] == 1
    target = judgment_run(store, "live")
    head, _ = target.seal("report")
    assert load_batch_report(store.objects, head).summary["requested"] == 2
    count = len(judge.requests)
    assert score_live(root, "live", workers=2, policy=policy, poll_seconds=.01) == summary
    assert len(judge.requests) == count and local_rollout == ["early", "late"]
