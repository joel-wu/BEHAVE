"""One selection policy, immutable old results, no resampling on resume."""

from types import SimpleNamespace

import pytest
from test_rejudge import local_rollout  # noqa: F401 -- shared fixture

from behave.eval import followup as retry
from tools import eval_review_trial as trial


def outcome(score=None, *, submitted=True):
    return {"result": {"submission": {} if submitted else None,
                       "judgment": {"rtl": {"score": score}} if submitted else None}}


def test_default_only_rejudges_unscored_submissions():
    selected = retry.select_retries({"pass": outcome(1), "fail": outcome(0),
                                    "sim": outcome(), "absent": outcome(submitted=False)})
    assert selected == {"keep": ["pass", "fail", "absent"], "rollout": [], "rejudge": ["sim"]}


def test_explicit_reroll_still_skips_passes_and_reuses_unscored_submissions():
    selected = retry.select_retries({"pass": outcome(1), "fail": outcome(0),
                                    "sim": outcome(), "absent": outcome(submitted=False),
                                    "interrupted": {"result": None}}, reroll=True)
    assert selected == {"keep": ["pass"], "rollout": ["fail", "absent", "interrupted"], "rejudge": ["sim"]}


def test_failure_code_filter_does_not_resample_candidate_failures_or_submissions():
    blocked = outcome(submitted=False)
    blocked["result"]["failure_code"] = "model-permanent:model-spending-stopped"
    selected = retry.select_retries({"pass": outcome(1), "fail": outcome(0),
        "sim": outcome(), "absent": outcome(submitted=False), "blocked": blocked},
        failure_codes=("model-permanent:model-spending-stopped",))
    assert selected == {"keep": ["pass", "fail", "sim", "absent"],
                        "rollout": ["blocked"], "rejudge": []}


def test_transport_retry_preserves_the_exact_input_release(tmp_path, publication, monkeypatch):
    import json

    source = tmp_path / "original"
    task = publication.task.task_id
    blocked = outcome(submitted=False)
    blocked["result"]["failure_code"] = "model-unknown:anthropic-response-invalid"
    monkeypatch.setattr(retry, "load_outcomes", lambda _: {task: blocked})
    trial.write_json(source / "settings.json", {"tasks": [task], "setting": "joint",
        "release": publication.ref.as_dict(), "task_objects": str(publication.root),
        "score_on_submit": False, "spending_account": "shared"})
    target = tmp_path / "retry"
    selected = retry.prepare(target, [source], failure_codes=(blocked["result"]["failure_code"],))
    settings = json.loads((target / "settings.json").read_text())
    assert settings["release"] == publication.ref.as_dict()
    assert selected["policies"] == {} and selected["rejudge"] == []
    assert settings["tasks"] == [task] and settings["score_on_submit"] is False
    assert settings["spending_account"] == "shared"


def test_read_sources_uses_latest_attempt_not_best_score(tmp_path):
    from behave.core.records import canonical_json
    from behave.task_execution.run_store import RunStore

    sources = [tmp_path / name for name in ("old", "new")]
    for source, score in zip(sources, (1, 0), strict=True):
        store = RunStore(source / "run/candidates/candidate")
        ref = store.objects.put(canonical_json(outcome(score)["result"]))
        trial.write_json(source / "run/report/1.json", {"kind": "evaluation_case", "payload": {"row": {
            "task_id": "task", "run_key": "candidate", "result": ref.as_dict()}}})
    merged = retry.load_outcomes(sources)
    assert merged["task"]["result"]["judgment"]["rtl"]["score"] == 0
    assert merged["task"]["source"] == str(sources[-1])


def test_preparing_same_followup_does_not_select_or_dispatch_again(tmp_path, monkeypatch):
    selection = {"sources": [str(tmp_path / "original")], "reroll": True, "keep": [], "rollout": ["task"], "rejudge": []}
    trial.write_json(tmp_path / "followup/selection.json", selection)
    trial.write_json(tmp_path / "followup/settings.json", {"tasks": ["task"]})
    monkeypatch.setattr(retry, "load_outcomes", lambda *_: pytest.fail("must retain prepared selection"))
    assert retry.prepare(tmp_path / "followup", [tmp_path / "original"], reroll=True) == selection
    with pytest.raises(ValueError, match="different follow-up"):
        retry.prepare(tmp_path / "followup", [tmp_path / "original"], reroll=False)


def test_archived_run_is_a_noop(tmp_path, monkeypatch):
    trial.write_json(tmp_path / "runtime.json", {"state": "archived"})
    monkeypatch.setattr("behave.eval.generation.run", lambda *a, **kw: pytest.fail("must not dispatch"))
    assert trial.run(tmp_path) == 0


@pytest.mark.parametrize("state", ["running", "failed-retained"])
def test_unarchived_run_is_not_silently_resampled(tmp_path, state):
    trial.write_json(tmp_path / "runtime.json", {"state": state})
    with pytest.raises(RuntimeError, match="existing node-local run"):
        trial.run(tmp_path)


def test_rejudge_only_run_never_constructs_model_endpoint(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    trial.write_json(archive / "settings.json", {"tasks": [], "retry": {"selection": "fixture"}})
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr("behave.eval.generation.run", lambda *a, **kw: pytest.fail("must not call model path"))
    calls = []
    monkeypatch.setattr(retry, "run_rejudgments", lambda local, **kw: calls.append(local))
    assert trial.run(archive) == 0 and len(calls) == 1
    assert not calls[0].exists()


@pytest.mark.parametrize(("score", "origin", "expected"), [
    (1, None, "passed"), (0, None, "candidate_failed"),
    (None, "verifier", "sim_no_conclusion"), (None, "reference", "sim_no_conclusion"),
    (None, "infrastructure", "execution_no_conclusion")])
def test_baseline_progress_is_independent_of_score(score, origin, expected):
    part = {"score": score, "code": "fixture", "origin": origin,
            "detail": {"baseline": {"status": "completed", "generated": 3, "completed": 3}}}
    report = trial.component_progress(part)
    assert report["status"] == expected and report["baseline"]["status"] == "completed"


def test_baseline_summary_does_not_turn_faults_into_completed_replays():
    from behave.sim import api
    from behave.task_execution.simulator_judge import _baseline_progress

    seeds = [SimpleNamespace(fingerprint=str(i)) for i in range(3)]
    trace = SimpleNamespace(completion=api.Completion.NORMAL)
    runs = [SimpleNamespace(stimulus=seeds[0], trace=trace, comparison=SimpleNamespace(complete=True)),
            SimpleNamespace(stimulus=seeds[1], comparison=None),
            SimpleNamespace(stimulus=seeds[2], trace=trace, comparison=SimpleNamespace(complete=False))]
    result = SimpleNamespace(generated=SimpleNamespace(portfolio=SimpleNamespace(baseline=seeds), stimuli=seeds),
                             evaluation=SimpleNamespace(results=runs, required_stimuli=seeds,
                                                        required_complete=False))
    assert _baseline_progress(result) == {
        "status": "incomplete", "generated": 3, "completed": 1, "search_generated": 0,
        "required_runs": 3, "required_complete": False}
    runs[1].comparison = runs[2].comparison = SimpleNamespace(complete=True)
    runs[1].trace = trace
    result.evaluation.required_complete = True
    assert _baseline_progress(result)["status"] == "completed"


def test_baseline_summary_does_not_count_a_checked_prefix_as_a_complete_run():
    from behave.sim import api
    from behave.task_execution.simulator_judge import _baseline_progress

    seed = SimpleNamespace(fingerprint="same-request")
    run = SimpleNamespace(stimulus=seed, comparison=SimpleNamespace(complete=True),
                          trace=SimpleNamespace(completion=api.Completion.CHECKPOINT))
    result = SimpleNamespace(
        generated=SimpleNamespace(portfolio=SimpleNamespace(baseline=[seed]), stimuli=[seed]),
        evaluation=SimpleNamespace(results=[run], required_stimuli=[seed], required_complete=False))
    assert _baseline_progress(result) == {
        "status": "incomplete", "generated": 1, "completed": 0, "search_generated": 0,
        "required_runs": 1, "required_complete": False}
    run.trace.completion = api.Completion.NORMAL
    result.evaluation.required_complete = True
    assert _baseline_progress(result)["status"] == "completed"


def test_baseline_summary_requires_the_continuous_workload_too():
    from behave.sim import api
    from behave.task_execution.simulator_judge import _baseline_progress

    seed = SimpleNamespace(fingerprint="isolated")
    continuous = SimpleNamespace(fingerprint="continuous")
    run = SimpleNamespace(stimulus=seed, comparison=SimpleNamespace(complete=True),
                          trace=SimpleNamespace(completion=api.Completion.NORMAL))
    result = SimpleNamespace(
        generated=SimpleNamespace(portfolio=SimpleNamespace(baseline=[seed]), stimuli=[seed]),
        evaluation=SimpleNamespace(results=[run], required_stimuli=[seed, continuous],
                                   required_complete=False))
    progress = _baseline_progress(result)
    assert progress["completed"] == progress["generated"] == 1
    assert progress["status"] == "incomplete" and progress["required_runs"] == 2


def test_followup_report_distinguishes_pending_from_a_failed_retry(tmp_path, monkeypatch):
    def item(score, source):
        return {"source": str(source), "result": {"submission": {"fixture": True},
            "judgment": {"rtl": {"score": score, "code": "fixture", "origin": None}}}}

    old = {"keep": item(1, tmp_path / "old"), "failed": item(0, tmp_path / "old"),
           "pending": item(0, tmp_path / "old")}
    trial.write_json(tmp_path / "selection.json", {"original_outcomes": old,
        "keep": ["keep"], "rollout": ["failed", "pending"], "rejudge": []})
    current = {**old, "failed": item(0, tmp_path)}
    monkeypatch.setattr(retry, "load_outcomes", lambda _: current)
    report = retry.summarize(tmp_path)
    assert report["pending_retries"] == 1
    assert [row["after"]["status"] for row in report["rows"]] == ["candidate_failed", "pending"]
    assert report["latest_known_counts"] == {"passed": 1, "candidate_failed": 2}
    assert (tmp_path / "FOLLOWUP.md").is_file()


def test_prepared_rejudge_preserves_task_submission_and_model_accounting(tmp_path, publication, local_rollout, monkeypatch):  # noqa: F811
    import json
    import shutil
    from dataclasses import replace

    from test_evaluation import plan_for
    from test_rejudge import Judge, results
    from test_task_workflow import setup

    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.core.records import thaw_json
    from behave.data.published import load_published_task
    from behave.eval.runner import run_evaluation
    from behave.sim import api
    from behave.task_execution import simulator_judge, workflow
    from behave.task_execution.accounting import load_accounting
    from behave.task_execution.judgment import ComponentJudgment
    from behave.task_execution.result import TaskResult
    from behave.task_execution.run_store import RunStore

    job, root = setup(tmp_path / "fixture", publication)
    job = replace(job, attempt=replace(job.attempt, attempt_id="pending"))
    unavailable = SimpleNamespace(identity=Judge.identity,
        evaluate=lambda *_: ComponentJudgment(None, "cancelled", "infrastructure"))
    report = run_evaluation(plan=plan_for(job), reader=publication.reader, directory=root.directory, judge=unavailable)
    old = results(root, report)[0]
    row = thaw_json(report.summary["candidates"][0])
    source = tmp_path / "source"
    shutil.copytree(root.directory, source / "run")
    monkeypatch.setattr(retry, "load_outcomes", lambda _: {
        old.task_id: {"source": str(source), "row": row, "result": old.as_dict()}})
    trial.write_json(source / "settings.json", {"tasks": [old.task_id], "setting": "joint",
        "release": old.release.as_dict(), "task_objects": str(publication.root), "judge_workers": 2})
    archive = tmp_path / "archive"
    selected = retry.prepare(archive, [source])
    assert selected["rejudge"] == [old.task_id] and not selected["rollout"]
    settings = json.loads((archive / "settings.json").read_text())
    reader = DirectoryReader(settings["task_objects"])
    updated = load_published_task(reader, BlobRef.from_dict(settings["release"]), old.task_id)
    original = load_published_task(publication.reader, old.release, old.task_id)
    assert updated.judge.reference_behavior == original.judge.reference_behavior
    assert updated.judge.contract == original.judge.contract and updated.judge.run_policy == original.judge.run_policy
    assert selected["policies"][old.task_id]["search_seconds"] == 0
    assert selected["policies"][old.task_id]["analysis"] is None

    local = tmp_path / "local"
    local.mkdir()
    shutil.copy2(archive / "settings.json", local / "settings.json")
    monkeypatch.setattr(api, "prepare_runtime", lambda **_: None)
    monkeypatch.setattr(simulator_judge, "SimulatorJudge", lambda: Judge(unavailable=False))
    monkeypatch.setattr(workflow, "run_solver_process", lambda **_: pytest.fail("must not resample"))
    rejudged = retry.run_rejudgments(local, status_output=archive, cancelled=lambda: False)[old.task_id]
    run = RunStore(local / "rejudgments" / old.task_id)
    new = TaskResult.from_bytes(run.objects.read(BlobRef.from_dict(rejudged["row"]["result"])))
    assert new.score == 1 and new.submission == old.submission and new.solver_head == old.solver_head
    assert load_accounting(run.objects, new).billing == load_accounting(root.objects, old).billing
    assert rejudged["new_model_calls"] == 0 and len(local_rollout) == 1
