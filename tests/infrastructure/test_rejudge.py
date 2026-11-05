"""Rollout, immediate scoring and later scoring share the same saved submission."""

from dataclasses import replace
from pathlib import Path
from shlex import quote

import pytest
from test_attempts import Scripted, local_shell
from test_evaluation import plan_for
from test_task_workflow import setup

from behave.agent.model import Charge, ModelReply, ToolCall
from behave.agent.process_worker import WorkerChannels, settled_attempt
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, parse_json, thaw_json
from behave.eval.runner import run_evaluation
from behave.task_execution import workflow
from behave.task_execution.accounting import load_accounting
from behave.task_execution.batch import execution_status, load_batch_report
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.rejudge import judgment_run, rejudge_batch
from behave.task_execution.result import TaskResult
from behave.task_execution.run_store import RunStore
from behave.task_execution.solver import run_solver, solver_outcome


@pytest.fixture
def local_rollout(monkeypatch):
    """Use the real solver/journal loop with trusted local fixture commands."""
    launches = []

    def execute(job, *, journal, supervision, **kwargs):
        reference = journal.objects.put(job.to_bytes())
        refs = tuple(journal.objects.put(file.data) for file in job.task.files)
        channels = WorkerChannels(journal, supervision)

        def launch():
            name = job.attempt.attempt_id
            launches.append(name)
            charge = Charge("0.01", "USD", "fixture")
            if name == "no_submission":
                replies = [ModelReply("Done.", charge=charge)]
            else:
                command = "printf %s " + quote(f"# {name}\nclass M: pass\n") + " > behavior.py\n"
                command += "printf %s 'module top; endmodule' > rtl.sv"
                selections = {"rtl": {"files": ["rtl.sv"], "entry_file": "rtl.sv", "entry_symbol": "top"}}
                if job.setting == "joint":
                    selections.update({
                        "behavior": {"files": ["behavior.py"], "entry_file": "behavior.py", "entry_symbol": "M"},
                        "testbench": None})
                    if name == "with_tb":
                        command += "\nprintf %s '# fixture test' > tb.py"
                        selections["testbench"] = {"files": ["tb.py"], "entry_file": "tb.py", "entry_symbol": "tb"}
                replies = [ModelReply("Create the files.", (ToolCall("edit", "run_command", {"command": command}),),
                                      charge=charge),
                           ModelReply("Submit.", (ToolCall("submit", "submit", selections),), charge=charge)]
            run_solver(task=job.task, spec=job.attempt, backend=Scripted(*replies), journal=journal,
                       command_tool=local_shell(), setting=job.setting)
            return {"schema": "behave.solver-settlement/1", "cleanup": "complete", "status": "exited",
                    "returncode": 0, "elapsed_seconds": 0., "interruption": None, "supervision_errors": []}, ()

        result = settled_attempt(namespace="solver", spec=job.attempt, journal=journal, supervision=supervision,
                                 payload=channels.payload(reference), dependencies=(reference, *refs), launch=launch)
        return solver_outcome(job.task, journal, result, setting=job.setting)

    monkeypatch.setattr(workflow, "run_solver_process", execute)
    return launches


class Judge:
    identity = "thread-safe-fixture-judge/1"

    def __init__(self, *, unavailable=True):
        self.unavailable = unavailable
        self.requests = []

    def evaluate(self, request, objects):
        self.requests.append(request)
        if self.unavailable and request.component == "behavior" and b"# pending" in request.source.files[0].data:
            return ComponentJudgment(None, "sim-unavailable", "verifier")
        return ComponentJudgment(1, "matched")


def initial_run(tmp_path, publication):
    base, _ = setup(tmp_path / "fixture", publication)
    jobs = tuple(replace(base, attempt=replace(base.attempt, attempt_id=name))
                 for name in ("scored", "pending", "no_submission"))
    root = RunStore(tmp_path / "batch")
    report = run_evaluation(plan=plan_for(*jobs), reader=publication.reader, directory=root.directory, judge=Judge())
    return root, report


def results(root, report):
    return [TaskResult.from_bytes(read_verified(root.objects, BlobRef.from_dict(thaw_json(row["result"]))))
            for row in report.summary["candidates"]]


def test_rejudge_accepts_readonly_restored_resource_snapshot(tmp_path, publication, local_rollout):
    root, _ = initial_run(tmp_path, publication)
    before = list(local_rollout)
    target = judgment_run(root, "restored")
    target.directory.mkdir(parents=True, exist_ok=True)
    resources = target.directory / "resources.json"
    resources.write_bytes(b'{}')
    resources.chmod(0o444)
    report = rejudge_batch(directory=root.directory, name="restored",
        reader=publication.reader, judge=Judge(unavailable=False), workers=2, all_submitted=True)
    assert report.summary["scored"] == 3
    assert parse_json(resources.read_bytes())["active"] == 0
    assert local_rollout == before


def test_rejudge_archives_share_executor_without_waiting_for_slow_archive(tmp_path, publication, local_rollout):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from tools.eval_score_job import CandidateQueue

    first, _ = initial_run(tmp_path / "first", publication)
    second, _ = initial_run(tmp_path / "second", publication)
    started, release, second_done = Event(), Event(), Event()

    class SlowJudge(Judge):
        def evaluate(self, request, objects):
            if request.component == "behavior" and b"# scored" in request.source.files[0].data:
                started.set()
                assert release.wait(10)
            return super().evaluate(request, objects)

    with CandidateQueue(2) as candidates, ThreadPoolExecutor(max_workers=2) as archives:
        a = archives.submit(rejudge_batch, directory=first.directory, name="shared",
            reader=publication.reader, judge=SlowJudge(unavailable=False), workers=2,
            all_submitted=True, executor=candidates)
        try:
            assert started.wait(10)
            b = archives.submit(rejudge_batch, directory=second.directory, name="shared",
                reader=publication.reader, judge=Judge(unavailable=False), workers=2,
                all_submitted=True, executor=candidates, on_case=lambda row: second_done.set())
            assert second_done.wait(10)
            assert not a.done()
            assert b.result(timeout=10).summary["scored"] == 3
        finally:
            release.set()
        assert a.result(timeout=10).summary["scored"] == 3
        assert candidates.submit(lambda: "still owned by caller").result(timeout=10) == "still owned by caller"
        assert candidates.activity()["peak_active"] == 2


def test_rejudge_cancellation_drains_owned_work_before_returning(tmp_path, publication, local_rollout):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from behave.agent.attempt import AttemptInterrupted
    from tools.eval_score_job import CandidateQueue

    root, _ = initial_run(tmp_path, publication)
    started, release, stopped = Event(), Event(), Event()
    committed = []

    class SlowJudge(Judge):
        def evaluate(self, request, objects):
            started.set()
            assert release.wait(10)
            return super().evaluate(request, objects)

    with CandidateQueue(1) as candidates, ThreadPoolExecutor(max_workers=1) as coordinator:
        running = coordinator.submit(rejudge_batch, directory=root.directory, name="cancelled",
            reader=publication.reader, judge=SlowJudge(unavailable=False), workers=1,
            all_submitted=True, executor=candidates, cancelled=stopped.is_set,
            on_case=committed.append)
        try:
            assert started.wait(10)
            stopped.set()
            assert not running.done(), "archive owner was released before active work settled"
        finally:
            release.set()
        with pytest.raises(AttemptInterrupted, match="rejudgment-cancelled"):
            running.result(timeout=10)
        assert candidates.activity()["active"] == candidates.activity()["queued"] == 0
        assert any(row["status"] == "no_submission" for row in committed)
        assert candidates.submit(lambda: True).result(timeout=10)
    # Cancellation must not emit a completed full-batch report or zero for
    # submissions that never reached the shared judge.
    events = judgment_run(root, "cancelled").journal("report").load()
    assert events[-1].kind != "evaluation_finished"
    assert len([event for event in events if event.kind == "evaluation_case"]) == len(committed)


def test_partial_rejudge_resumes_readonly_resources_without_changing_prior_rows(
        tmp_path, publication, local_rollout):
    import os

    root, _ = initial_run(tmp_path, publication)
    launches = list(local_rollout)

    def interrupt_after_commit(row):
        raise RuntimeError("stop after a durable case")

    with pytest.raises(RuntimeError, match="stop after a durable case"):
        rejudge_batch(directory=root.directory, name="readonly_resume",
            reader=publication.reader, judge=Judge(unavailable=False), all_submitted=True,
            on_case=interrupt_after_commit)
    target = judgment_run(root, "readonly_resume")
    before = [e for e in target.journal("report").load() if e.kind == "evaluation_case"]
    assert len(before) == 1
    resources = target.directory / "resources.json"
    resources.chmod(0o444)
    archived = tmp_path / "archived-resources.json"
    os.link(resources, archived)
    original_bytes = archived.read_bytes()
    result = rejudge_batch(directory=root.directory, name="readonly_resume",
        reader=publication.reader, judge=Judge(unavailable=False), all_submitted=True)
    after = [e for e in target.journal("report").load() if e.kind == "evaluation_case"]
    assert after[0].to_bytes() == before[0].to_bytes()
    assert len(after) == result.summary["requested"] == 3
    assert local_rollout == launches
    assert archived.read_bytes() == original_bytes
    assert archived.stat().st_mode & 0o777 == 0o444
    assert resources.stat().st_ino != archived.stat().st_ino


def test_full_baseline_rejudges_settled_submissions_without_reroll(tmp_path, publication, local_rollout):
    root, original = initial_run(tmp_path, publication)
    launches = list(local_rollout)
    before = results(root, original)
    judge = Judge(unavailable=False)
    fresh = rejudge_batch(directory=root.directory, name="uniform_baseline", reader=publication.reader,
                          judge=judge, all_submitted=True)
    assert len(judge.requests) == 4  # Both artifacts of both submissions, including the settled one.
    assert local_rollout == launches
    after = results(root, fresh)
    assert [r.submission for r in after] == [r.submission for r in before]
    assert results(root, original) == before
    assert all(r.score == 1 for r in after if r.submission is not None)
    replay = rejudge_batch(directory=root.directory, name="uniform_baseline", reader=publication.reader,
                           judge=judge, all_submitted=True)
    assert replay == fresh and len(judge.requests) == 4
    with pytest.raises(RecordError):
        rejudge_batch(directory=root.directory, name="uniform_baseline", reader=publication.reader, judge=judge)


def index_selection(root, report, indices):
    selected = {}
    for index in indices:
        row = thaw_json(report.summary["candidates"][index])
        result = TaskResult.from_bytes(root.objects.read(BlobRef.from_dict(row["result"])))
        selected[row["run_key"]] = {"task": result.task_id, "attempt_id": result.attempt_id,
            "setting": "joint", "release": result.release.as_dict(), "revision": result.revision.as_dict(),
            "result": row["result"], "submission": None if result.submission is None else result.submission.as_dict(),
            "judgment": "on_submit"}
    return selected


def test_indexed_rejudge_selects_exact_results_and_never_retries_tb(tmp_path, publication, local_rollout):
    from test_testbench_reports import POLICY, ReportBackend

    base, _ = setup(tmp_path / "fixture", publication)
    jobs = tuple(replace(base, attempt=replace(base.attempt, attempt_id=name))
                 for name in ("with_tb", "pending", "no_submission"))
    root = RunStore(tmp_path / "batch")
    original = run_evaluation(plan=replace(plan_for(*jobs, testbench=POLICY), score_on_submit=False),
                             reader=publication.reader, directory=root.directory)
    before, launches = results(root, original), list(local_rollout)
    selected = index_selection(root, original, [0, 2])
    judge, tb = Judge(unavailable=False), ReportBackend()
    fresh = rejudge_batch(directory=root.directory, name="indexed", reader=publication.reader,
                         judge=judge, testbench=tb, all_submitted=True, selection=selected, retry_testbench=False)
    assert fresh.summary["requested"] == 2 and fresh.summary["no_submission"] == 1
    assert len(judge.requests) == 2 and tb.requests == []
    assert local_rollout == launches and results(root, original) == before
    assert [r.submission for r in results(root, fresh)] == [before[0].submission, before[2].submission]
    assert fresh.summary["candidates"][0]["testbench"] is None
    assert rejudge_batch(directory=root.directory, name="indexed", reader=publication.reader,
                         judge=judge, all_submitted=True, selection=selected, retry_testbench=False) == fresh
    assert len(judge.requests) == 2
    assert load_batch_report(root.objects, fresh.head) == fresh


def test_indexed_rejudge_uses_named_saved_result_not_root_report(tmp_path, publication, local_rollout):
    root, original = initial_run(tmp_path, publication)
    previous = rejudge_batch(directory=root.directory, name="earlier", reader=publication.reader,
                            judge=Judge(unavailable=False), all_submitted=True)
    selected = index_selection(root, previous, [1])
    next(iter(selected.values()))["judgment"] = "earlier"
    judge = Judge(unavailable=False)
    fresh = rejudge_batch(directory=root.directory, name="indexed", reader=publication.reader,
                         judge=judge, selection=selected, retry_testbench=False)
    assert judge.requests == []  # The indexed settled result is retained without all_submitted.
    assert results(root, fresh)[0] == results(root, previous)[1]
    assert results(root, original)[1].score is None


def test_indexed_node_staging_uses_shared_rejudge_without_loading_other_candidates(
        tmp_path, publication, local_rollout, monkeypatch):
    import shutil

    from tools.eval_review_trial import score

    root, original = initial_run(tmp_path, publication)
    selected = index_selection(root, original, [0, 2])
    output = tmp_path / "archive"
    output.mkdir()
    root.directory.rename(output / "run")
    root = RunStore(output / "run")
    shutil.copytree(publication.root, output / "inputs")
    judge = Judge(unavailable=False)
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda **kw: judge)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(scratch))
    from behave.eval import scoring as scoring_module
    write, staged = scoring_module.write_json, []

    def inspect_staging(path, row, **kwargs):
        if path.name == "runtime.json" and row["state"] == "running":
            local = Path(row["local_directory"])
            assert local.is_dir()
            assert not (local / "run/candidates" / original.summary["candidates"][1]["run_key"]).exists()
            staged.append(local)
        return write(path, row, **kwargs)

    monkeypatch.setattr(scoring_module, "write_json", inspect_staging)
    launches = list(local_rollout)
    report = score(output, "uniform", workers=2, all_submitted=True, selection=selected,
                   retry_testbench=False, cpu_capacity=2)
    assert len(judge.requests) == 2 and local_rollout == launches
    assert report.summary["requested"] == 2
    runtime = parse_json((output / "run/judgments/uniform/runtime.json").read_bytes())
    assert runtime["state"] == "archived"
    local = Path(runtime["local_directory"])
    assert staged == [local] and not local.exists()
    assert load_batch_report(root.objects, report.head) == report


@pytest.mark.parametrize("field", ["release", "submission", "attempt_id"])
def test_indexed_rejudge_rejects_wrong_bindings_before_dispatch(tmp_path, publication, local_rollout, field):
    root, original = initial_run(tmp_path, publication)
    selected = index_selection(root, original, [0])
    next(iter(selected.values()))[field] = None
    judge = Judge()
    with pytest.raises(ValueError, match="different|differs"):
        rejudge_batch(directory=root.directory, name="wrong", reader=publication.reader,
                      judge=judge, all_submitted=True, selection=selected, retry_testbench=False)
    assert judge.requests == []


def test_targeted_rejudge_preserves_other_components_and_skips_other_failures(tmp_path, publication, local_rollout):
    root, original = initial_run(tmp_path, publication)
    before = results(root, original)
    launches = list(local_rollout)
    judge = Judge(unavailable=False)
    skipped = rejudge_batch(directory=root.directory, name="skip_unrelated", reader=publication.reader,
                            judge=judge, retry_codes=("cpu-pool-quarantined",))
    assert judge.requests == []
    assert results(root, skipped) == before
    repaired = rejudge_batch(directory=root.directory, name="repair_selected", reader=publication.reader,
                             judge=judge, retry_codes=("sim-unavailable",))
    assert len(judge.requests) == 1 and judge.requests[0].component == "behavior"
    from behave.task_execution.recovery import require_task_quiescent

    for candidate in (judgment_run(root, "repair_selected").directory / "candidates").iterdir():
        require_task_quiescent(RunStore(candidate, objects=root.objects), run_key=candidate.name)
    after = results(root, repaired)
    assert after[1].judgment.rtl == before[1].judgment.rtl
    assert after[1].judgment.behavior.score == 1
    assert local_rollout == launches
    assert [r.submission for r in after] == [r.submission for r in before]
    assert rejudge_batch(directory=root.directory, name="repair_selected", reader=publication.reader,
                         judge=judge, retry_codes=("sim-unavailable",)) == repaired
    assert len(judge.requests) == 1
    with pytest.raises(RecordError):
        rejudge_batch(directory=root.directory, name="repair_selected", reader=publication.reader,
                       judge=judge, retry_codes=("judge-worker-timeout",))


@pytest.mark.parametrize("setting", ["joint", "rtl_only"])
def test_rollout_only_then_score_preserves_artifacts_trace_and_cost(tmp_path, publication, local_rollout, setting):
    from test_testbench_reports import POLICY, ReportBackend, measured

    from behave.task_execution.batch_plan import BatchPlan
    base, _ = setup(tmp_path / "fixture", publication)
    jobs = tuple(replace(base, setting=setting, attempt=replace(base.attempt, attempt_id=name))
                 for name in ("with_tb", "no_submission"))
    plan = replace(plan_for(*jobs, testbench=POLICY), score_on_submit=False)
    # Both kinds of plans survive serialization, including historical defaults.
    assert BatchPlan.from_bytes(plan.to_bytes(), publication.reader) == plan
    immediate = replace(plan, score_on_submit=True)
    assert b"score_on_submit" not in immediate.to_bytes()
    assert BatchPlan.from_bytes(immediate.to_bytes(), publication.reader) == immediate
    root = RunStore(tmp_path / "batch")
    unused_judge, unused_tb = Judge(), ReportBackend()
    original = run_evaluation(plan=plan, reader=publication.reader, directory=root.directory,
                              judge=unused_judge, testbench=unused_tb)
    assert unused_judge.requests == unused_tb.requests == []
    assert original.summary["pending_judgment"] == 1
    assert original.summary["mean"] == 0
    assert original.summary["no_result"] == 0 and original.summary["no_submission"] == 1
    before = results(root, original)
    assert before[0].status == "pending_judgment" and before[0].failure_stage is None
    assert before[0].judgment is None and before[0].score is None
    assert not list((root.directory / "candidates").glob("*/judge/*.json"))
    assert not list((root.directory / "candidates").glob("*/testbench/*.json"))
    assert load_batch_report(root.objects, original.head) == original
    assert run_evaluation(plan=plan, reader=publication.reader, directory=root.directory) == original
    assert len(local_rollout) == 2

    judge, tb = Judge(unavailable=False), ReportBackend(measured)
    scored = rejudge_batch(directory=root.directory, name="baseline", reader=publication.reader,
                           judge=judge, testbench=tb, workers=4)
    after = results(root, scored)
    assert after[0].score == 1 and after[1].status == "no_submission"
    assert scored.summary["mean"] == .5
    assert [row["final_score"] for row in scored.summary["candidates"]] == [1, 0]
    assert original.summary["mean"] == 0  # Rejudging leaves the old report unchanged.
    assert scored.summary["billing"] == original.summary["billing"]
    assert after[0].submission == before[0].submission
    assert after[0].solver_head == before[0].solver_head
    assert after[1] == before[1]
    assert len(judge.requests) == (2 if setting == "joint" else 1)
    assert len(tb.requests) == (1 if setting == "joint" else 0)
    if setting == "joint":
        reference = BlobRef.from_dict(thaw_json(scored.summary["candidates"][0]["testbench"]))
        measured_tb = parse_json(read_verified(root.objects, reference))
        assert measured_tb["observation"]["coverage"]["covered"] == 3
        assert measured_tb["observation"]["coverage"]["total"] == 10
    assert load_batch_report(root.objects, scored.head) == scored
    from behave.core.storage import DirectoryStore

    head, closure = judgment_run(root, "baseline").seal("report")
    exported = DirectoryStore(tmp_path / "scoring-export")
    exported.publish(root.objects, closure)
    assert load_batch_report(exported, head) == scored
    assert [load_accounting(exported, result) for result in after] == [
        load_accounting(root.objects, result) for result in after]
    if setting == "joint":
        assert parse_json(read_verified(exported, reference)) == measured_tb
    assert not list((root.directory / "judgments").rglob("solver"))
    assert rejudge_batch(directory=root.directory, name="baseline", reader=publication.reader,
                         judge=judge, testbench=tb, workers=4) == scored
    no_calls, no_tb = Judge(), ReportBackend()
    rejudge_batch(directory=root.directory, name="again", previous="baseline", reader=publication.reader,
                  judge=no_calls, testbench=no_tb)
    assert no_calls.requests == no_tb.requests == [] and len(local_rollout) == 2


def test_scoring_cli_exposes_independent_worker_count(monkeypatch, tmp_path):
    from behave.eval import __main__ as cli

    calls = []
    monkeypatch.setattr("behave.task_execution.rejudge.rejudge_batch",
                        lambda **kw: calls.append(kw) or pytest.fail("captured"))
    with pytest.raises(pytest.fail.Exception, match="captured"):
        cli.main(["--score", "baseline", "--judge-workers", "50", "--run-directory", str(tmp_path),
                  "--task-objects", str(tmp_path / "objects")])
    assert calls[0]["workers"] == 50
    assert calls[0]["name"] == "baseline"


def test_new_search_policy_scores_original_rollouts_not_cached_baseline(tmp_path, publication, local_rollout):
    from behave.task_execution.judge_policy import JudgePolicy

    base, _ = setup(tmp_path / "fixture", publication)
    jobs = tuple(replace(base, attempt=replace(base.attempt, attempt_id=name))
                 for name in ("first", "second", "no_submission"))
    root = RunStore(tmp_path / "batch")
    original = run_evaluation(plan=replace(plan_for(*jobs), score_on_submit=False),
                              reader=publication.reader, directory=root.directory)
    baseline = rejudge_batch(directory=root.directory, name="baseline", reader=publication.reader,
                             judge=Judge(unavailable=False))
    baseline_head = judgment_run(root, "baseline").seal("report")[0]
    policy = JudgePolicy("search-command", "search-authority", search_seconds=30).to_bytes()
    judge = Judge(unavailable=False)
    # No previous=baseline: the source is the unscored original submissions.
    searched = rejudge_batch(directory=root.directory, name="search_v1", reader=publication.reader,
                             judge=judge, judge_policy=policy)
    assert len(judge.requests) == 4
    assert all(request.task.judge_policy == policy for request in judge.requests)
    assert len(local_rollout) == 3
    assert searched.summary["billing"] == baseline.summary["billing"] == original.summary["billing"]
    assert [result.submission for result in results(root, searched)] == [
        result.submission for result in results(root, original)]
    assert judgment_run(root, "baseline").seal("report")[0] == baseline_head
    assert load_batch_report(root.objects, original.head) == original
    rejudge_batch(directory=root.directory, name="search_v1", reader=publication.reader,
                  judge=judge, judge_policy=policy)
    assert len(judge.requests) == 4  # Same name resumes the saved attempt.


def test_retry_final_tb_without_repeating_a_settled_score(tmp_path, publication, local_rollout):
    from test_testbench_reports import POLICY, ReportBackend, measured

    from behave.task_execution.tb_report import TBObservation

    base, _ = setup(tmp_path / "fixture", publication)
    job = replace(base, attempt=replace(base.attempt, attempt_id="with_tb"))
    root = RunStore(tmp_path / "run")
    original = run_evaluation(plan=replace(plan_for(job, testbench=POLICY), score_on_submit=False),
                              reader=publication.reader, directory=root.directory)
    judge = Judge(unavailable=False)
    failed_tb = ReportBackend(TBObservation("unavailable", "fixture-unavailable"))
    rejudge_batch(directory=root.directory, name="baseline", reader=publication.reader,
                  judge=judge, testbench=failed_tb)
    assert len(judge.requests) == 2
    unused_judge, tb = Judge(), ReportBackend(measured)
    report = rejudge_batch(directory=root.directory, name="tb_retry", previous="baseline",
                           reader=publication.reader, judge=unused_judge, testbench=tb)
    assert unused_judge.requests == [] and len(tb.requests) == 1 and len(local_rollout) == 1
    assert report.summary["billing"] == original.summary["billing"]
    reference = BlobRef.from_dict(thaw_json(report.summary["candidates"][0]["testbench"]))
    assert parse_json(read_verified(root.objects, reference))["observation"]["status"] == "completed"


def test_score_launcher_stages_locally_and_archives_without_recopying_rollouts(tmp_path, publication, local_rollout, monkeypatch):
    import json
    import shutil

    from behave.task_execution import simulator_judge
    from tools import eval_review_trial as trial

    root, original = initial_run(tmp_path, publication)
    # The launcher archive has the shared run/ and published inputs/ layout.
    output = tmp_path / "archive"
    output.mkdir()
    shutil.move(str(root.directory), output / "run")
    shutil.copytree(publication.root, output / "inputs")
    root = RunStore(output / "run")
    old_report = root.journal("report").load()
    old_objects = {path.name for path in root.objects.directory.iterdir()}
    scratch = tmp_path / "node-local"
    scratch.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(scratch))
    # Keep the real Unix socket below the OS path-length limit while exercising
    # archive staging in pytest's longer per-test directory.
    monkeypatch.setattr("behave.sim.cpu_admission.scratch_root", lambda: tmp_path.parent)
    judge = Judge(unavailable=False)
    monkeypatch.setattr(simulator_judge, "SimulatorJudge", lambda **kwargs: judge)
    score = trial.score(output, "baseline", workers=3)
    assert score.summary["scored"] == 3 and len(local_rollout) == 3
    assert score.summary["billing"] == original.summary["billing"]
    assert load_batch_report(root.objects, score.head) == score
    assert root.journal("report").load() == old_report
    assert old_objects < {path.name for path in root.objects.directory.iterdir()}
    runtime = json.loads((root.directory / "judgments/baseline/runtime.json").read_text())
    local = Path(runtime["local_directory"])
    assert runtime["state"] == "archived" and local.is_relative_to(scratch) and not local.exists()
    assert not list((root.directory / "judgments").glob("*/candidates/*/solver/*.json"))
    assert not list((root.directory / "judgments").rglob("objects"))
    trial.score(output, "baseline", workers=3)
    assert len(judge.requests) == 2 and len(local_rollout) == 3
    # Put a no-submission row first to exercise report selection throughout export.
    rows = list(reversed(original.summary["candidates"]))
    trial.write_json(output / "trace_index.json", [
        {"task": row["attempt_id"], "run_key": row["run_key"]} for row in rows])
    trial.export_review(output, judgment="baseline")
    assessment = json.loads((output / "traces/pending/assessment.json").read_text())
    assert assessment["score"] == 1
    assert (output / "traces/pending/submitted/rtl/rtl.sv").is_file()


def test_rtl_only_batch_rejudge_and_training_keep_setting(tmp_path, publication, local_rollout, monkeypatch):
    from test_testbench_reports import POLICY, ReportBackend

    from behave.task_execution.solver import SolverJob
    from behave.train.training_data import load_candidate

    class RTLJudge(Judge):
        def evaluate(self, request, objects):
            assert request.component == "rtl"
            self.requests.append(request)
            if self.unavailable:
                return ComponentJudgment(None, "sim-unavailable", "verifier")
            return ComponentJudgment(1, "matched")

    base, _ = setup(tmp_path / "fixture", publication)
    job = replace(base, setting="rtl_only")
    root = RunStore(tmp_path / "batch")
    judge = RTLJudge()
    tb = ReportBackend()
    original = run_evaluation(plan=plan_for(job, testbench=POLICY), reader=publication.reader,
                              directory=root.directory, judge=judge, testbench=tb)
    old = results(root, original)[0]
    assert old.score is None and old.judgment.behavior is None
    assert len(judge.requests) == 1 and tb.requests == []
    assert original.summary["candidates"][0]["testbench"] is None
    monkeypatch.setattr(workflow, "run_solver_process", lambda **kw: pytest.fail("must not call the model again"))
    judge = RTLJudge(unavailable=False)
    repaired = rejudge_batch(directory=root.directory, name="fixed", reader=publication.reader, judge=judge)
    new = results(root, repaired)[0]
    assert new.score == 1 and new.judgment.behavior is None
    assert new.submission == old.submission and new.solver_head == old.solver_head
    assert len(judge.requests) == 1 and len(local_rollout) == 1
    assert repaired.summary["mean_on_scored"] == 1
    assert repaired.summary["billing"] == original.summary["billing"]
    assert load_batch_report(root.objects, repaired.head) == repaired
    ref = BlobRef.from_dict(thaw_json(repaired.summary["candidates"][0]["result"]))
    candidate = load_candidate(root.objects, ref)
    assert candidate.reward == 1
    job_ref = load_accounting(root.objects, candidate.result).job
    assert SolverJob.from_bytes(read_verified(root.objects, job_ref), root.objects).setting == "rtl_only"


def test_retry_keeps_rollout_cost_and_skips_scored_candidates(tmp_path, publication, local_rollout, monkeypatch):
    root, original = initial_run(tmp_path, publication)
    before = results(root, original)
    assert execution_status(original, root.objects) == {
        "submitted": 2, "pending_judgment": 0, "judgment_unavailable": 1,
        "no_submission": 1, "rollout_unavailable": 0}
    assert (before[1].rollout_status, before[1].judgment_status, before[1].score) == ("submitted", "pending", None)
    old_events = root.journal("report").load()
    monkeypatch.setattr(workflow, "run_solver_process", lambda *a, **kw: pytest.fail("must not relaunch a model"))
    judge = Judge(unavailable=False)
    repaired = rejudge_batch(directory=root.directory, name="fixed", reader=publication.reader, judge=judge)
    after = results(root, repaired)
    assert len(judge.requests) == 2  # Only the one previously unscored submission.
    assert len(local_rollout) == 3
    assert [result.score for result in after] == [1, 1, 0]
    assert [result.submission for result in after] == [result.submission for result in before]
    for old, new in zip(before, after, strict=True):
        assert old.solver_head == new.solver_head
        assert load_accounting(root.objects, old).billing == load_accounting(root.objects, new).billing
    from behave.train.training_data import load_candidate

    candidate_ref = BlobRef.from_dict(thaw_json(repaired.summary["candidates"][1]["result"]))
    assert load_candidate(root.objects, candidate_ref).reward == 1
    assert repaired.summary["billing"] == original.summary["billing"]
    assert root.journal("report").load() == old_events
    assert load_batch_report(root.objects, original.head) == original
    assert load_batch_report(root.objects, repaired.head) == repaired
    assert not list((root.directory / "judgments").rglob("solver"))
    assert not list((root.directory / "judgments").rglob("objects"))
    assert rejudge_batch(directory=root.directory, name="fixed", reader=publication.reader, judge=judge) == repaired
    assert len(judge.requests) == 2


def test_repeated_failure_can_be_scored_again_without_resampling(tmp_path, publication, local_rollout):
    root, _ = initial_run(tmp_path, publication)
    again = rejudge_batch(directory=root.directory, name="still_broken", reader=publication.reader, judge=Judge())
    assert execution_status(again, root.objects)["judgment_unavailable"] == 1
    fixed = rejudge_batch(directory=root.directory, name="fixed", previous="still_broken",
                         reader=publication.reader, judge=Judge(unavailable=False))
    assert execution_status(fixed, root.objects)["pending_judgment"] == 0
    no_calls = Judge()
    rejudge_batch(directory=root.directory, name="already_fixed", previous="fixed",
                  reader=publication.reader, judge=no_calls)
    assert no_calls.requests == [] and len(local_rollout) == 3


def test_judge_setup_error_does_not_discard_submission(tmp_path, publication, local_rollout, monkeypatch):
    actual = workflow.judge_submission
    monkeypatch.setattr(workflow, "judge_submission", lambda **kw: (_ for _ in ()).throw(ValueError("bad sim setup")))
    root, report = initial_run(tmp_path, publication)
    assert report.summary["billing"]["known_estimates_by_currency"] == {"USD": "0.05"}
    assert execution_status(report, root.objects)["judgment_unavailable"] == 2
    assert results(root, report)[0].failure_code == "judge-ValueError"
    monkeypatch.setattr(workflow, "judge_submission", actual)
    repaired = rejudge_batch(directory=root.directory, name="fixed", reader=publication.reader,
                             judge=Judge(unavailable=False))
    assert repaired.summary["scored"] == 3 and len(local_rollout) == 3


def test_replacement_policy_is_explicit_and_recorded(tmp_path, publication, local_rollout):
    root, report = initial_run(tmp_path, publication)
    job = load_accounting(root.objects, results(root, report)[0]).job
    from behave.task_execution.judge_policy import JudgePolicy

    policy = JudgePolicy("replacement-command", "replacement-authority", search_seconds=0, max_retries=0).to_bytes()
    judge = Judge(unavailable=False)
    replacement = rejudge_batch(directory=root.directory, name="new_policy", reader=publication.reader,
                               judge=judge, judge_policy=policy)
    assert all(request.task.judge_policy == policy for request in judge.requests)
    assert all(load_accounting(root.objects, value).job == job for value in results(root, replacement)[:1])
    with pytest.raises(RecordError, match="replay differs"):
        rejudge_batch(directory=root.directory, name="new_policy", reader=publication.reader, judge=judge)


def test_retry_cli_does_not_load_a_model_plan(tmp_path, publication, local_rollout, monkeypatch, capsys):
    from behave.eval import __main__ as cli

    root, _ = initial_run(tmp_path, publication)
    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", lambda: Judge(unavailable=False))
    monkeypatch.setattr("behave.eval.runner.run_evaluation", lambda **kw: pytest.fail("retry is not another rollout"))
    assert cli.main(["--retry-judgment", "fixed", "--run-directory", str(root.directory),
                     "--task-objects", str(publication.root)]) == 0
    response = parse_json(capsys.readouterr().out.encode())
    assert response["submitted"] == 2 and response["pending_judgment"] == 0
    assert len(local_rollout) == 3


def test_report_resume_does_not_repeat_a_finished_judgment(tmp_path, publication, local_rollout, monkeypatch):
    from behave.agent.replay import Tape

    root, _ = initial_run(tmp_path, publication)
    record = Tape.record
    class Crash(BaseException):
        pass

    def crash(self, kind, *args, **kwargs):
        if kind == "evaluation_finished":
            raise Crash()
        return record(self, kind, *args, **kwargs)

    judge = Judge(unavailable=False)
    monkeypatch.setattr(Tape, "record", crash)
    with pytest.raises(Crash):
        rejudge_batch(directory=root.directory, name="fixed", reader=publication.reader, judge=judge)
    assert len(judge.requests) == 2
    monkeypatch.setattr(Tape, "record", record)
    report = rejudge_batch(directory=root.directory, name="fixed", reader=publication.reader, judge=judge)
    assert report.summary["scored"] == 3 and len(judge.requests) == 2


def test_unavailable_task_inputs_remain_pending(tmp_path, publication, local_rollout, monkeypatch):
    from behave.task_execution import rejudge

    root, _ = initial_run(tmp_path, publication)
    monkeypatch.setattr(rejudge, "load_published_task", lambda *a, **kw: (_ for _ in ()).throw(OSError("unavailable")))
    judge = Judge()
    report = rejudge_batch(directory=root.directory, name="failed_setup", reader=publication.reader, judge=judge)
    assert execution_status(report, root.objects)["judgment_unavailable"] == 1
    assert results(root, report)[1].failure_code == "judge-OSError"
    assert judge.requests == []


def test_retry_does_not_bypass_worker_cleanup(tmp_path, publication, local_rollout):
    class CrashedJudge(Judge):
        def evaluate(self, request, objects):
            raise RuntimeError("worker state unknown")

    job, _ = setup(tmp_path / "fixture", publication)
    root = RunStore(tmp_path / "batch")
    run_evaluation(plan=plan_for(job), reader=publication.reader, directory=root.directory, judge=CrashedJudge())
    judge = Judge()
    with pytest.raises(RecordError, match="old execution requires"):
        rejudge_batch(directory=root.directory, name="unsafe_retry", reader=publication.reader, judge=judge)
    assert judge.requests == []


@pytest.mark.isolation
@pytest.mark.parametrize("setting", ["joint", "rtl_only"])
def test_supervised_rollout_only_then_score(tmp_path, publication, monkeypatch, setting):
    from test_evaluation import Judge as OriginalJudge
    from test_task_workflow import SUBMIT

    program = SUBMIT
    if setting == "rtl_only":
        program = program.replace("'behavior':{'files':['agent.py'],'entry_file':'agent.py','entry_symbol':'M'},", "")
        program = program.replace(",'testbench':None", "")
    job, _ = setup(tmp_path / "fixture", publication, program)
    job = replace(job, setting=setting)
    root = RunStore(tmp_path / "batch")
    generated = run_evaluation(plan=replace(plan_for(job), score_on_submit=False),
                               reader=publication.reader, directory=root.directory)
    original = results(root, generated)[0]
    assert original.submission is not None and original.status == "pending_judgment"
    assert execution_status(generated, root.objects)["pending_judgment"] == 1
    monkeypatch.setattr(workflow, "run_solver_process", lambda *a, **kw: pytest.fail("must not rerun the solver"))
    scored = rejudge_batch(directory=root.directory, name="baseline", reader=publication.reader,
                           judge=OriginalJudge(), workers=2)
    final = results(root, scored)[0]
    assert final.score == (.5 if setting == "joint" else 0)
    assert final.submission == original.submission and final.solver_head == original.solver_head
    assert scored.summary["billing"] == generated.summary["billing"]


@pytest.mark.parametrize("name", ["", ".", "..", "../elsewhere", "/tmp/scoring"])
def test_judgment_run_names_stay_under_the_original_run(tmp_path, name):
    with pytest.raises(ValueError):
        judgment_run(RunStore(tmp_path), name)
