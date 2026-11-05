"""Campaign assembly/reporting does not introduce another solver or judge."""

import json
from pathlib import Path

import pytest
from test_rejudge import local_rollout  # noqa: F401 -- real sealed report, trusted fixture commands

from behave.data.spec_format import behavior_entry, without_behavior_interface
from behave.eval.report import DATASETS, table1
from behave.eval.report import summarize_campaign as summarize


@pytest.mark.parametrize('entry', ['campaign', 'gpu', 'review'])
@pytest.mark.parametrize('condition', [
    'ready', 'missing-receipt', 'changed-file', 'missing-file', 'extra-file', 'unknown-schema'])
def test_launch_reuses_only_complete_verified_snapshots(tmp_path, monkeypatch, entry, condition):
    from test_runtime_support import repository

    from behave.runtime import jobs
    from behave.runtime import snapshots as snapshots_module
    from behave.runtime.records import write_json
    from tools import eval_campaign, eval_gpu_campaign, eval_review_trial

    repo, _ = repository(tmp_path, monkeypatch)
    root = tmp_path / 'campaign'
    snapshot = root / 'snapshots/gpu/checkout'
    receipt = snapshots_module.freeze(repo, snapshot)
    write_json(root / 'campaign.json', {'configuration': {'cpus': 37}})
    write_json(root / 'settings.json', {'cpus': 37, 'tasks': ['task'], 'model': 'fixture'})
    target = snapshot / 'src/behave/code.py'
    if condition == 'missing-receipt':
        (snapshot.parent / 'snapshot.json').rename(tmp_path / 'retained-receipt.json')
    elif condition == 'changed-file':
        target.chmod(0o644)
        target.write_text('# changed\n')
    elif condition == 'missing-file':
        target.rename(tmp_path / 'retained-file.py')
    elif condition == 'extra-file':
        (target.parent / 'unrecorded.py').write_text('# absent from receipt\n')
    elif condition == 'unknown-schema':
        write_json(snapshot.parent / 'snapshot.json', {**receipt, 'schema': 'unsupported/1'})
    calls = []
    monkeypatch.setattr(jobs, 'submit', lambda path, argv, **kw: calls.append((path, argv, kw)) or 'started')
    monkeypatch.setattr(eval_campaign, 'freeze_phase', lambda *a: pytest.fail('do not rebuild existing snapshot'))
    monkeypatch.setattr(snapshots_module, 'freeze', lambda *a, **k: pytest.fail('do not replace existing snapshot'))

    def launch():
        if entry == 'review':
            return eval_review_trial.submit(root, 123, snapshot=snapshot)
        if entry == 'gpu':
            return eval_gpu_campaign.submit(root, 123)
        return eval_campaign.submit(root, 123, phase='gpu', cpus=37)

    if condition != 'ready':
        with pytest.raises((RuntimeError, FileNotFoundError)):
            launch()
        assert calls == [] and not (root / 'gpu_control').exists() and not (root / 'control').exists()
        return
    assert launch() == 'started'
    assert snapshots_module.verify(snapshot) == receipt
    assert len(calls) == 1
    path, argv, options = calls[0]
    assert path == root / ('control' if entry == 'review' else 'gpu_control')
    assert '--jobid=123' in argv and '--cpus-per-task=37' in argv
    assert str(snapshot) in argv
    assert ('--gpus=8' if entry == 'gpu' else '--gres=none') in argv
    assert options['cwd'] == Path(eval_campaign.__file__).resolve().parents[1]
    assert options['wall_seconds'] == (None if entry == 'review' else 259200)


@pytest.mark.parametrize('phase', ['repair-hosted', 'repair-gpu', 'supplement-hosted', 'gpu-extra'])
def test_retry_requires_parent_receipt(tmp_path, monkeypatch, phase):
    from tools.eval_campaign import freeze_phase

    monkeypatch.setattr('behave.runtime.snapshots.freeze', lambda *a, **k: pytest.fail('cannot freeze unverified parent'))
    root = tmp_path / 'campaign'
    snapshot = root / 'snapshots' / phase / 'checkout'
    with pytest.raises(FileNotFoundError, match='snapshot.json'):
        freeze_phase(tmp_path / 'missing-current-source', root, snapshot, phase)
    assert not snapshot.exists()


@pytest.mark.parametrize('failed_dependency', [False, True])
def test_deferred_launch_waits_on_shared_owner_before_exec(tmp_path, monkeypatch, failed_dependency):
    import os
    import sys

    from behave.runtime import jobs
    from tools import eval_campaign as campaign

    calls = []
    monkeypatch.setattr(campaign, 'freeze_phase', lambda *a: None)
    monkeypatch.setattr(jobs, 'submit', lambda path, argv, **kw: calls.append((path, argv, kw)))
    dependency = tmp_path / 'prior/control'
    campaign.submit(tmp_path, 123, phase='gpu', cpus=37, after=dependency)
    path, argv, options = calls[0]
    assert path == tmp_path / 'gpu_control'
    assert options['wall_seconds'] == 259200
    assert options['cwd'] == Path(campaign.__file__).resolve().parents[1]
    assert '--cpus-per-task=37' in argv and '--jobid=123' in argv
    events = []

    def wait(path):
        events.append(('wait', path))
        if failed_dependency:
            raise RuntimeError('dependency failed')

    monkeypatch.setattr(jobs, 'wait_control', wait)
    monkeypatch.setattr(os, 'execvp', lambda name, args: events.append(('exec', name, args)))
    monkeypatch.setattr(sys, 'argv', ['-c', *argv[5:]])
    code = compile(argv[4], '<deferred-eval-launch>', 'exec')
    if failed_dependency:
        with pytest.raises(RuntimeError, match='dependency failed'):
            exec(code, {})  # noqa: S102 -- fixed launcher bootstrap, wait and execvp mocked.
        assert events == [('wait', dependency)]
    else:
        exec(code, {})  # noqa: S102 -- fixed launcher bootstrap, wait and execvp mocked.
        assert events == [('wait', dependency), ('exec', 'srun', argv[6:])]


def test_campaign_cost_reads_the_owning_nodes_existing_cache(tmp_path, monkeypatch):
    from tools.eval_campaign import cost
    from tools.eval_review_trial import write_json

    local = tmp_path / "node-local"
    write_json(tmp_path / "hosted_runtime.json", {"local_directory": str(local)})
    seen = []

    def snapshot(directory):
        seen.append(directory)
        return {"spent": "3.14", "reserved": "0.6", "stopped": False, "provisional": True}

    monkeypatch.setattr("behave.agent.spending.spending_snapshot", snapshot)
    assert cost(tmp_path)["spent"] == "3.14"
    assert seen == [local / "spending"]


def test_model_entry_is_explicit_spec_metadata():
    text = "# task\n## RTL interface\n```sv\nmodule top();\n```\n## Behavior interface\n```python\nclass Model:\n    pass\n```\n## Reset Behavior\nreset clears state\n"
    assert behavior_entry(text) == "Model"
    assert "class Model" not in without_behavior_interface(text)
    assert "reset clears state" in without_behavior_interface(text)
    with pytest.raises(ValueError):
        behavior_entry("# task\n## Behavior interface\nnot a declared entry")


def samples():
    return [{"model": "model", "setting": "joint", "dataset": dataset,
             "rtl": {"status": "passed" if score else "candidate_failed", "score": score},
             "behavior": {"status": "passed", "score": 1}, "coverage": 1}
            for dataset in DATASETS for score in ([0, 0, 0] if dataset == "authored600_v1" else [1])]


def test_table1_uses_macro_not_micro_average():
    summary = summarize(samples())
    assert summary["model/joint"]["macro_average"]["rtl"] == .75
    rendered = table1(summary, [{"id": "model", "label": "Model"}])
    assert "75.0 & 100.0 & 100.0" in rendered
    assert rendered.splitlines()[1].count(" & ") == 15


def test_current_split_label_is_preserved_in_reports():
    order = (*DATASETS[:3], "authored600_v2")
    rows = [dict(row, dataset="authored600_v2") if row["dataset"] == DATASETS[-1] else row
            for row in samples()]
    summary = summarize(rows, dataset_order=order)
    assert set(summary["model/joint"]["datasets"]) == set(order)
    assert summary["model/joint"]["macro_average"]["rtl"] == .75
    assert "75.0 & 100.0 & 100.0" in table1(summary, [{"id": "model", "label": "Model"}],
                                          dataset_order=order)


def test_new_campaign_resolves_and_pins_current_split(tmp_path):
    from tools.eval_campaign import prepare
    from tools.eval_review_trial import write_json

    write_json(tmp_path / "data/splits/current.json", {"authored": "authored600_v2"})
    split = tmp_path / "data/splits/authored600_v2"
    split.mkdir()
    for name, content in (("train.txt", "training"), ("eval.txt", "evaluation"),
                          ("external_eval.txt", "external")):
        (split / name).write_text(content)
    run = tmp_path / "run"
    write_json(run / "release/inventory.json", [{"task_id": "evaluation", "category": "numeric"},
                                               {"task_id": "external", "category": "cvdp"}])
    write_json(run / "release/release.json", {"sha256": "a" * 64, "size_bytes": 1})
    write_json(tmp_path / "config.json", {"models": []})
    manifest = prepare(run, tmp_path / "config.json", tmp_path)
    assert manifest["configuration"]["split"] == "data/splits/authored600_v2"
    assert manifest["datasets"]["evaluation"] == "authored600_v2"


def test_unresolved_counts_as_zero_in_final_rates_without_losing_its_cause():
    rows = samples()
    rows[0]["rtl"] = {"status": "sim_no_conclusion", "score": None}
    rows[0]["coverage"] = None
    summary = summarize(rows)["model/joint"]
    assert summary["datasets"][DATASETS[0]]["rtl"]["counts"] == {"sim_no_conclusion": 1}
    assert summary["datasets"][DATASETS[0]]["rtl"]["pass_rate"] == 0
    assert summary["macro_average"]["rtl"] == .5
    assert summary["macro_average"]["coverage"] == .75
    assert rows[0]["coverage"] is None


def test_undefined_coverage_settles_to_zero_without_changing_the_measurement():
    rows = samples()
    rows[0].update(coverage=None, coverage_counts={"covered": 0, "total": 0})
    coverage = summarize(rows)["model/joint"]["datasets"][DATASETS[0]]["coverage"]
    assert coverage == {"known": 0, "undefined": 1, "unknown": 0, "mean": 0}
    assert rows[0]["coverage"] is None
    assert rows[0]["coverage_counts"] == {"covered": 0, "total": 0}


@pytest.mark.parametrize("status", ["not_submitted", "execution_no_conclusion", "failed", "pending"])
def test_missing_coverage_uses_all_requested_tasks_as_the_denominator(status):
    rows = [dict(samples()[0], coverage=value) for value in (.8, None, .4)]
    rows[1]["tb_status"] = status
    summary = summarize(rows)["model/joint"]
    coverage = summary["datasets"][DATASETS[0]]["coverage"]
    assert coverage["mean"] == pytest.approx(.4)
    assert coverage["known"] == 2 and coverage["unknown"] == 1
    assert rows[1]["coverage"] is None
    # A missing dataset is not a zero-coverage task in an existing dataset.
    assert summary["macro_average"]["coverage"] is None


@pytest.mark.parametrize("setting", ["rtl_only", "joint_sim"])
def test_coverage_not_requested_remains_not_applicable(setting):
    rows = [dict(row, setting=setting, coverage=None, tb_status="not_requested") for row in samples()]
    summary = summarize(rows)[f"model/{setting}"]
    assert all(dataset["coverage"] == {"known": 0, "undefined": 0, "unknown": 0, "mean": None}
               for dataset in summary["datasets"].values())
    assert summary["macro_average"]["coverage"] is None


def test_table1_renders_missing_coverage_as_zero():
    rows = [dict(row, coverage=None) for row in samples()]
    summary = summarize(rows)
    rendered = table1(summary, [{"id": "model", "label": "Model"}])
    cells = rendered.splitlines()[1].removesuffix(r" \\").split(" & ")[1:]
    assert [cells[index] for index in (2, 5, 8, 11, 14)] == ["0.0"] * 5


def test_campaign_prepare_requires_explicit_configuration(tmp_path, monkeypatch, capsys):
    from tools import eval_campaign

    root = tmp_path / "run"
    monkeypatch.setattr("sys.argv", ["eval_campaign", "prepare", str(root)])
    with pytest.raises(SystemExit) as caught:
        eval_campaign.main()
    assert caught.value.code == 2
    assert "prepare requires --configuration" in capsys.readouterr().err
    assert not root.exists()


def test_campaign_selects_only_heldout_for_rtl_only_and_enforces_global_barrier(tmp_path):
    from tools.eval_campaign import prepare, require_rollouts_finished, score_directories, status, submit
    from tools.eval_review_trial import write_json
    split = tmp_path / "split"
    split.mkdir()
    for name, text in (("train.txt", "train_task"), ("eval.txt", "holdout"), ("external_eval.txt", "external")):
        (split / name).write_text(text)
    root = tmp_path / "run"
    write_json(root / "release/inventory.json", [{"task_id": "holdout", "category": "control"},
                                                {"task_id": "external", "category": "cvdp"}])
    write_json(root / "release/release.json", {"sha256": "a" * 64, "size_bytes": 1})
    configuration = {
        "split": "split", "hosted_budget_usd": "1", "workers": 1, "judge_workers": 1,
        "attempt": {"max_turns": 60, "context_tokens": 131072, "request_timeout_seconds": 600},
        "credentials": "fixture.env",
        "models": [{"id": "fixture", "label": "Test model", "provider": "openai",
                    "model": "test-model", "credential_key": "OPENAI_API_KEY",
                    "rates": {"input_per_million": "1", "cached_input_per_million": "0.1",
                              "output_per_million": "1"}}],
    }
    write_json(tmp_path / "configuration.json", configuration)
    manifest = prepare(root, tmp_path / "configuration.json", tmp_path)
    assert manifest["rollouts"] == 3
    assert manifest["dataset_order"][-1] == "split"
    assert manifest["datasets"]["holdout"] == "split"
    settings = json.loads((root / "runs/fixture_rtl_only/settings.json").read_text())
    assert settings["tasks"] == ["holdout"] and settings["score_on_submit"] is False
    assert settings["attempt"]["context_tokens"] == 131072
    assert "wall_timeout_seconds" not in settings["attempt"]
    with pytest.raises(ValueError, match="rollouts are not finished"):
        require_rollouts_finished(root)
    with pytest.raises(ValueError, match="rollouts are not finished"):
        submit(root, 123, phase="score")
    assert not (root / "snapshots/score").exists()
    assert status(root)["runs"][0]["counts"] == {"waiting": 2}
    assert not status(root)["rollout_barrier_complete"]
    for row in manifest["runs"]:
        write_json(root / "runs" / row["name"] / "runtime.json", {"state": "archived"})
    assert len(require_rollouts_finished(root)) == 2
    assert status(root)["rollout_barrier_complete"]
    assert len(score_directories(root, shard=0, shards=2)) == 2
    assert score_directories(root, shard=1, shards=2) == []
    with pytest.raises(ValueError, match="score shard"):
        score_directories(root, shard=2, shards=2)
    retry = root / "runs/fixture_joint/fixture_joint_infra_retry"
    write_json(retry / "selection.json", {"rollout": ["external"]})
    assert not status(root)["rollout_barrier_complete"]
    with pytest.raises(ValueError, match="infra_retry"):
        require_rollouts_finished(root)
    write_json(retry / "runtime.json", {"state": "archived"})
    assert status(root)["rollout_barrier_complete"]


def test_archive_recovery_never_restarts_an_active_run(tmp_path):
    import socket

    from tools.eval_campaign import recover_archive
    from tools.eval_review_trial import write_json

    write_json(tmp_path / "campaign.json", {"runs": [{"name": "model_joint", "tasks": 1}]})
    write_json(tmp_path / "runs/model_joint/runtime.json", {"state": "running", "host": socket.gethostname()})
    with pytest.raises(ValueError, match="exited failed run"):
        recover_archive(tmp_path, "model_joint")


@pytest.mark.parametrize("requested", [1, 2])
def test_archive_recovery_reuses_verified_copied_batch(tmp_path, publication, local_rollout, monkeypatch, requested):  # noqa: F811 -- pytest injects the imported fixture
    import socket
    from dataclasses import replace

    from test_evaluation import plan_for
    from test_task_workflow import setup

    from behave.core.records import thaw_json
    from behave.eval.runner import run_evaluation
    from tools import eval_campaign as campaign
    from tools.eval_review_trial import write_json

    root = tmp_path / "campaign"
    final = root / "runs/model_joint"
    job, _ = setup(tmp_path / "fixture", publication)
    report = run_evaluation(plan=replace(plan_for(job), score_on_submit=False),
                           reader=publication.reader, directory=final / "run")
    write_json(root / "campaign.json", {"runs": [{"name": "model_joint", "tasks": requested}]})
    write_json(final / "runtime.json", {"state": "failed", "host": socket.gethostname(),
        "local_directory": str(tmp_path / "expired-node-local"), "exit_code": 1})
    (final / "worker.log").write_text("original post-rollout billing failure\n")

    monkeypatch.setattr(campaign.shutil, "copytree", lambda *a, **kw: pytest.fail("archive already copied"))
    monkeypatch.setattr("behave.eval.report.export_review",
                        lambda *a, **kw: pytest.fail("complete trace does not need another export"))
    launches = list(local_rollout)
    if requested == 2:
        with pytest.raises(ValueError, match="campaign task count"):
            campaign.recover_archive(root, "model_joint")
        assert json.loads((final / "runtime.json").read_text())["state"] == "failed"
    else:
        state = campaign.recover_archive(root, "model_joint")
        assert state["state"] == "archived"
        assert state["recovery"] == "sealed-archive-accepted"
        assert state["original_exit_code"] == 1
        assert json.loads((final / "report.json").read_text()) == thaw_json(report.summary)
        assert campaign.recover_archive(root, "model_joint") == state
    assert local_rollout == launches
    assert (final / "worker.log").read_text() == "original post-rollout billing failure\n"


def test_status_distinguishes_settled_attempts_from_finished_archival(tmp_path):
    from tools.eval_campaign import status
    from tools.eval_review_trial import write_json

    write_json(tmp_path / "campaign.json", {"rollouts": 2,
        "runs": [{"name": "model_joint", "tasks": 2}]})
    directory = tmp_path / "runs/model_joint"
    write_json(directory / "runtime.json", {"state": "running"})
    progress = [
        {"status": "final_action", "turns": 2, "evaluation_status": "pending_judgment"},
        {"status": "no_result", "turns": 0, "evaluation_status": "no_result"},
    ]
    write_json(directory / "progress.json", progress)
    current = status(tmp_path)
    assert current["runs"][0]["settled_attempts"] == 2
    assert current["runs"][0]["state"] == "running"
    assert not current["rollout_barrier_complete"]
    # An exported submit action alone is not the task-result commit.
    del progress[0]["evaluation_status"]
    write_json(directory / "progress.json", progress)
    assert status(tmp_path)["runs"][0]["settled_attempts"] == 1


def test_finish_joins_all_rollouts_before_single_snapshot_and_scoring(tmp_path, monkeypatch):
    from tools import eval_campaign as campaign
    from tools.eval_review_trial import write_json

    waited, launched, frozen = [], [], []
    write_json(tmp_path / "score_ready_prior_control/launch.json", {})

    def wait(control, **_):
        waited.append(control.name)
        return {"state": "completed"}

    def submit(root, allocation, *, phase, **options):
        label = f"score_{options['shard']}_of_2" if phase == "score" else phase
        if phase == "score":
            assert set(waited) >= {"hosted_control", "gpu_control", "repair-hosted_control", "repair-gpu_control"}
            assert "score_ready_prior_control" in waited
            assert (root / "snapshots/score/checkout").exists()
        launched.append((label, allocation))
        write_json(root / (label + "_control/launch.json"), {})

    def freeze(source, root, snapshot, phase):
        frozen.append(phase)
        snapshot.mkdir(parents=True)

    def report(root):
        assert set(waited) >= {"score_0_of_2_control", "score_1_of_2_control"}
        return {"tasks": 3528}

    monkeypatch.setattr(campaign, "wait_control", wait)
    monkeypatch.setattr(campaign, "submit", submit)
    monkeypatch.setattr("tools.eval_gpu_campaign.submit", lambda root, allocation, **kw:
                        submit(root, allocation, phase="repair-gpu", **kw))
    monkeypatch.setattr(campaign, "freeze_phase", freeze)
    monkeypatch.setattr(campaign, "require_rollouts_finished", lambda root: None)
    monkeypatch.setattr(campaign, "report", report)
    for _ in range(2):
        assert campaign.finish(tmp_path, 101, 102, hosted_failure_codes=("anthropic",),
                               gpu_failure_codes=("chat",)) == {"tasks": 3528}
    assert sorted(launched) == [("repair-gpu", 102), ("repair-hosted", 101),
                                ("score_0_of_2", 101), ("score_1_of_2", 102)]
    assert frozen == ["score"]
    assert json.loads((tmp_path / "finish_state.json").read_text())["phase"] == "reported"


def test_finish_does_not_score_after_failed_repair(tmp_path, monkeypatch):
    from tools import eval_campaign as campaign
    from tools.eval_review_trial import write_json

    write_json(tmp_path / "repair-gpu_control/launch.json", {})

    def wait(control, **_):
        if control.name == "repair-gpu_control":
            raise RuntimeError("repair did not finish")
        return {"state": "completed"}

    monkeypatch.setattr(campaign, "wait_control", wait)
    monkeypatch.setattr(campaign, "submit", lambda *_a, **_k: pytest.fail("must not dispatch scoring"))
    with pytest.raises(RuntimeError, match="repair did not finish"):
        campaign.finish(tmp_path, 101, 102, gpu_failure_codes=("chat",))
    assert json.loads((tmp_path / "finish_state.json").read_text())["phase"] == "interrupted"


def test_wait_control_observes_existing_supervisor_and_propagates_failure(tmp_path, monkeypatch):
    from tools import eval_campaign as campaign

    states = iter(({"state": "supervising"}, {"state": "completed"}, {"state": "failed"}))
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: next(states))
    pauses = []
    monkeypatch.setattr("behave.runtime.jobs.Event", lambda: type("Wait", (), {"wait": lambda _, n: pauses.append(n)})())
    assert campaign.wait_control(tmp_path)["state"] == "completed"
    assert pauses == [30]
    with pytest.raises(RuntimeError, match="did not complete"):
        campaign.wait_control(tmp_path)


def test_campaign_report_uses_recorded_retry_even_when_it_does_not_succeed(tmp_path):
    from behave.eval.report import task_rows
    from behave.task_execution.run_store import RunStore
    from tools.eval_review_trial import write_json

    write_json(tmp_path / "campaign.json", {"runs": [{"name": "model_joint", "model": "model", "setting": "joint"}],
                                          "datasets": {"task": "cvdp"}})
    original = tmp_path / "runs/model_joint"
    retry = original / "model_joint_infra_retry"
    write_json(original / "settings.json", {"tasks": ["task"]})
    write_json(retry / "selection.json", {"rollout": ["task"]})
    for source in (original, retry):
        store = RunStore(source / "run")
        store.journal("report").append([], "evaluation_case", {"row": {
            "task_id": "task", "status": "no_result", "result": None, "testbench": None,
            "error": {"type": source.name}}})
    write_json(retry / "runtime.json", {"state": "archiving"})
    partial = task_rows(tmp_path)
    assert not partial[0]["infra_retry"]
    assert partial[0]["rollout_error"] == {"type": "model_joint"}
    write_json(retry / "runtime.json", {"state": "archived"})
    rows = task_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["infra_retry"]
    assert rows[0]["rollout_error"] == {"type": "model_joint_infra_retry"}
    assert rows[0]["rtl"]["score"] is None
    assert rows[0]["trace"] == str(retry / "traces/task/trace.md")


def test_campaign_report_waits_for_baseline_publication_before_reading_its_journal(tmp_path):
    from behave.eval.report import _cases
    from behave.task_execution.rejudge import judgment_run
    from behave.task_execution.run_store import RunStore
    from tools.eval_review_trial import write_json

    store = RunStore(tmp_path / "run")
    store.journal("report").append([], "evaluation_case", {"row": {"task_id": "task", "score": None}})
    baseline = judgment_run(store, "baseline")
    baseline.journal("report").append([], "evaluation_case", {"row": {"task_id": "task", "score": 1}})
    write_json(baseline.directory / "runtime.json", {"state": "running"})
    assert _cases(tmp_path)[1]["task"]["score"] is None
    write_json(baseline.directory / "runtime.json", {"state": "archived"})
    assert _cases(tmp_path)[1]["task"]["score"] == 1


def test_campaign_report_reuses_archived_summaries_with_identical_candidate_rows(tmp_path, monkeypatch):
    from behave.agent.journal import AttemptJournal
    from behave.eval.report import _cases
    from behave.task_execution.rejudge import judgment_run
    from behave.task_execution.run_store import RunStore
    from tools.eval_review_trial import write_json

    store = RunStore(tmp_path / "run")
    baseline = judgment_run(store, "baseline")
    originals = [{"task_id": name, "score": None} for name in ("task", "other")]
    evaluated = [{"task_id": "task", "score": 1}]
    for run, rows in ((store, originals), (baseline, evaluated)):
        tape = []
        for row in rows:
            tape.append(run.journal("report").append(tape, "evaluation_case", {"row": row}))
    write_json(baseline.directory / "runtime.json", {"state": "archived"})
    expected = _cases(tmp_path)[1]
    write_json(tmp_path / "report.json", {"schema": "behave.evaluation-summary/2", "candidates": originals})
    write_json(baseline.directory / "summary.json", {
        "schema": "behave.evaluation-summary/2", "candidates": evaluated})
    monkeypatch.setattr(AttemptJournal, "load", lambda *_: pytest.fail("archived summary already available"))
    assert _cases(tmp_path)[1] == expected


def test_campaign_report_does_not_read_a_summary_before_publication(tmp_path):
    from behave.eval.report import _cases
    from behave.task_execution.rejudge import judgment_run
    from behave.task_execution.run_store import RunStore
    from tools.eval_review_trial import write_json

    baseline = judgment_run(RunStore(tmp_path / "run"), "baseline")
    write_json(tmp_path / "report.json", {"schema": "behave.evaluation-summary/2",
        "candidates": [{"task_id": "task", "score": None}]})
    write_json(baseline.directory / "runtime.json", {"state": "running"})
    (baseline.directory / "summary.json").write_text("not published yet")
    assert _cases(tmp_path)[1]["task"]["score"] is None


def test_campaign_report_rejects_an_invalid_published_summary(tmp_path):
    from behave.core.records import RecordError
    from behave.eval.report import _cases
    from tools.eval_review_trial import write_json

    write_json(tmp_path / "report.json", {"schema": "unrelated-summary/1", "candidates": []})
    with pytest.raises(RecordError):
        _cases(tmp_path)


def test_component_report_preserves_the_simulators_refusal_without_reclassification():
    from behave.eval.report import component_progress

    reason = {"kind": "rtl-structural-frontend-unsupported", "why": "unsupported source form",
              "detail": {"diagnostic": "source.sv:15: expected expression"}}
    result = component_progress({"score": None, "code": "judge-simulator-refused",
        "origin": "verifier", "detail": {"reason": reason}})
    assert result["reason"] == reason
    assert result["score"] is None
    assert result["status"] == "sim_no_conclusion"


@pytest.mark.parametrize("setting", ("joint", "rtl_only"))
@pytest.mark.parametrize("failure_code", (
    "model-permanent:model-budget-exhausted",
    "model-unknown:fireworks-exchange-interrupted",
    None,
))
def test_campaign_report_keeps_dispatch_interruptions_distinct_from_no_submission(
        tmp_path, setting, failure_code):
    from behave.eval.report import task_rows
    from behave.task_execution.result import TaskResult
    from behave.task_execution.run_store import RunStore
    from tools.eval_review_trial import write_json

    run_name = "model_" + setting
    write_json(tmp_path / "campaign.json", {
        "runs": [{"name": run_name, "model": "model", "setting": setting}],
        "datasets": {"task": "cvdp"},
    })
    directory = tmp_path / "runs" / run_name
    write_json(directory / "settings.json", {"tasks": ["task"]})
    store = RunStore(directory / "run")
    identity = store.objects.put(b"test task and accounting identity")
    result = TaskResult("task", identity, identity, "attempt", identity,
        "turn_limit" if failure_code is None else None, None, None,
        None if failure_code is None else "solver", failure_code, identity)
    reference = store.objects.put(result.to_bytes())
    store.journal("report").append([], "evaluation_case", {"row": {
        "task_id": "task", "status": result.status,
        "result": reference.as_dict(), "testbench": None,
    }}, (reference,))

    rows = task_rows(tmp_path)
    expected_score = 0 if failure_code is None else None
    expected_status = "not_submitted" if failure_code is None else "rollout_unavailable"
    for artifact in ("rtl", "behavior") if setting == "joint" else ("rtl",):
        assert rows[0][artifact]["score"] == expected_score
        assert rows[0][artifact]["status"] == expected_status
    if failure_code is not None:
        assert rows[0]["failure_code"] == failure_code
    summary = summarize(rows)["model/" + setting]["datasets"]["cvdp"]["rtl"]
    assert summary["pass_rate"] == 0
    assert summary["unknown"] == (0 if failure_code is None else 1)


def test_trace_audit_counts_reasoning_context_and_actual_worker_affinity(tmp_path):
    from behave.eval.report import audit_traces
    from tools.eval_review_trial import write_json

    reply = {"content": "test", "calls": [{"arguments": {}}], "finish_reason": "tool_calls",
             "continuation": {"reasoning_content": "returned reasoning"},
             "usage": {"context_budget": {"input_tokens": 30, "max_output_tokens": 70, "limit": 100},
                       "normalized": {"input_tokens": 30, "output_tokens": 20}, "routing": {"url": "engine-1"}}}
    write_json(tmp_path / "traces/task/0001_reply.json", reply)
    write_json(tmp_path / "traces/task/0003_reply.json", reply)
    result = audit_traces(tmp_path)
    assert result["counts"]["empty_tool_arguments"] == 2
    assert result["counts"]["readable_reasoning"] == 2
    assert result["destinations"] == {"engine-1": 2}
    assert not result["affinity_violations"] and not result["context_violations"]
    reply["usage"]["routing"]["url"] = "engine-2"
    reply["usage"]["normalized"]["output_tokens"] = 80
    write_json(tmp_path / "traces/task/0005_reply.json", reply)
    result = audit_traces(tmp_path)
    assert result["affinity_violations"] == ["task"]
    assert result["context_violations"][0]["kind"] == "returned_context_exceeded"


@pytest.mark.parametrize("content,has_text", [("", False), (" \n\t", False), ("\nExplain.\n", True)])
def test_trace_audit_does_not_count_whitespace_as_assistant_text(tmp_path, content, has_text):
    from behave.eval.report import audit_traces
    from tools.eval_review_trial import write_json

    reply = {"content": content, "calls": [], "finish_reason": "stop", "usage": {},
             "continuation": {"reasoning_content": "Returned reasoning is separate."}}
    write_json(tmp_path / "traces/task/0001_reply.json", reply)

    counts = audit_traces(tmp_path)["counts"]
    assert counts["assistant_text"] == int(has_text)
    assert counts["readable_reasoning"] == 1
    assert counts["responses"] == 1


def test_trace_audit_uses_paired_inventory_without_merging_settings(tmp_path):
    from behave.eval.report import audit_traces
    from tools.eval_review_trial import write_json

    reply = {"content": "", "calls": [], "finish_reason": "stop", "usage": {}}
    rows = [{"task": "same", "setting": setting, "trace": f"traces/{setting}/same/trace.md"}
            for setting in ("joint", "rtl_only")]
    rows.append({"task": "waiting", "setting": "joint", "trace": "traces/joint/waiting/trace.md"})
    write_json(tmp_path / "trace_index.json", rows)
    for setting, engine in (("joint", "one"), ("rtl_only", "two")):
        write_json(tmp_path / f"traces/{setting}/same/0001_reply.json",
                   {**reply, "usage": {"routing": {"url": engine}}})
    result = audit_traces(tmp_path)
    assert [row["task"] for row in result["tasks"]] == ["joint/same", "rtl_only/same"]
    assert result["counts"]["responses"] == 2 and result["destinations"] == {"one": 1, "two": 1}
    assert result["affinity_violations"] == []
    assert {row["task"] for row in result["missing_exports"]} == {"joint/same", "rtl_only/same"}


def test_trace_audit_distinguishes_rejected_input_from_generated_overflow(tmp_path):
    from behave.eval.report import audit_traces
    from tools.eval_review_trial import write_json

    reply = {"kind": "failure", "code": "context-input-full", "charge": None,
             "usage": {"context_budget": {"input_tokens": 131113, "limit": 131072,
                                           "max_output_tokens": 0}}}
    write_json(tmp_path / "traces/task/0001_reply.json", reply)
    audit = audit_traces(tmp_path)
    assert audit["counts"]["input_preflight_rejections"] == 1
    assert audit["counts"].get("counted_requests", 0) == 0
    assert audit["failures"] == {"context-input-full": 1}
    assert not audit["context_violations"]
    # Other failures still do not excuse an oversized generation request.
    reply["code"] = "http-error"
    write_json(tmp_path / "traces/task/0002_reply.json", reply)
    audit = audit_traces(tmp_path)
    assert audit["counts"]["counted_requests"] == 1
    assert audit["context_violations"] == [{"task": "task", "file": "0002_reply.json",
                                             "kind": "requested_context_exceeded"}]


def test_trace_audit_reports_missing_exports_from_committed_events(tmp_path):
    from behave.eval.report import audit_traces
    from tools.eval_review_trial import write_json

    trace = tmp_path / "traces/task"
    for name in ("0001_model_requested.json", "0002_model_settled.json", "0003_tool_settled.json"):
        write_json(trace / name, {})
    missing = [item["file"] for item in audit_traces(tmp_path)["missing_exports"]]
    assert missing == ["0001_request.json", "0002_provider_raw.json", "0002_reply.json",
                       "0003_tool_result.json", "opening.json", "tools.json", "trace.md"]

    for name in missing:
        write_json(trace / name, {"code": "context-input-full"} if name.endswith("_reply.json") else {})
    # A local input preflight failure may legitimately have no provider bytes.
    (trace / "0002_provider_raw.json").write_bytes(b"")
    audit = audit_traces(tmp_path)
    assert audit["missing_exports"] == []
    assert audit["tasks"][0]["missing_exports"] == []
    assert audit["failures"] == {"context-input-full": 1}
