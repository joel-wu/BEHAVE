from dataclasses import replace

import pytest
from test_evaluation import Judge, plan_for
from test_role_process import Unavailable
from test_task_workflow import STOP, setup

from behave.agent.journal import Event, load_chain
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.train.learning_report import load_training_report
from behave.train.plan import TrainingManifest, TrainingPlan
from behave.train.rollouts import run_rollouts
from behave.train.training_data import TrainingRequirements


def plan_at(tmp_path, publication):
    job, _ = setup(tmp_path, publication, STOP)
    manifest = TrainingManifest(job.task.release, ((job.task.task_id, job.task.revision),))
    ref = publication.put(manifest.to_bytes())
    provenance = BlobRef.of(b"fixture-token-provenance")
    requirements = TrainingRequirements("fixture/1", provenance, provenance, provenance)
    return TrainingPlan(plan_for(job), ref, requirements, (None,))


def test_training_exports_the_complete_eval_journal(tmp_path, publication, monkeypatch):
    from behave.task_execution.run_store import RunStore
    from behave.train import rollouts

    written, calls = [], []
    marker = object()

    def batch(**kwargs):
        calls.append(kwargs)
        child = RunStore(kwargs["directory"])
        journal = child.journal("report")
        assert not hasattr(journal, "max_events")
        dependency = child.objects.put(b"evaluation evidence")
        for index in range(4):
            written.append(journal.append(written, "event", {"index": index}, (dependency,)))
        return marker

    def summarize(plan, objects, report, verifier, *, accounting, token_statuses):
        assert report is marker
        assert token_statuses == {}
        assert load_chain(objects, BlobRef.of(written[-1].to_bytes())) == tuple(written)
        assert objects.read(BlobRef.of(b"evaluation evidence")) == b"evaluation evidence"
        from types import SimpleNamespace

        return SimpleNamespace(summary={"fixture": "report copied"})

    monkeypatch.setattr(rollouts, "run_batch", batch)
    monkeypatch.setattr(rollouts, "prepare_rollouts", summarize)
    plan = plan_at(tmp_path, publication)
    run = JournalDirectory(tmp_path / "training")
    result = run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader,
                          run=run, judge=Judge())
    assert result == {"fixture": "report copied"}
    assert len(calls) == 1
    assert run.journal("training").load()[-1].kind == "training_finished"


@pytest.mark.isolation
def test_training_uses_same_runner_score_and_cold_recovery(tmp_path, publication, monkeypatch):
    plan = plan_at(tmp_path, publication)
    assert TrainingPlan.from_bytes(plan.to_bytes(), publication.reader) == plan
    run = JournalDirectory(tmp_path / "training")
    result = run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader, run=run, judge=Judge())
    assert (result["requested"], result["rewarded"], result["eligible_turns"]) == (1, 1, 0)
    row, = result["candidates"]
    assert row["group_id"] is None and row["examples"]["reward"] == 0
    assert row["examples"]["turns"][0]["reason"] == "token-data-missing"
    head, closure = run.seal("training")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_training_report(exported, head, approved_manifest=plan.manifest) == result
    # Read-only CLI validation of a resumed plan must use its local closure,
    # even when the original upstream store is unavailable.
    from test_infra_cli import command

    path, manifest = tmp_path / "plan.json", tmp_path / "manifest.json"
    path.write_bytes(plan.to_bytes())
    manifest.write_bytes(publication.reader.read(plan.manifest))
    before = {p.relative_to(run.directory): p.read_bytes() for p in run.directory.rglob("*") if p.is_file()}
    completed = command("train", "--plan", path, "--approved-manifest", manifest,
                        "--task-objects", tmp_path / "absent", "--run-directory", run.directory, "--dry-run")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert parse_json(completed.stdout)["status"] == "validated"
    assert before == {p.relative_to(run.directory): p.read_bytes() for p in run.directory.rglob("*") if p.is_file()}
    events = load_chain(exported, head)
    for key, change in (("reward", 1), ("group", "silent-regroup"), ("tokens", 1), ("billing", {})):
        changed = parse_json(canonical_json(result))
        if key == "reward":
            changed["candidates"][0]["examples"]["reward"] = change
        elif key == "group":
            changed["candidates"][0]["group_id"] = change
        elif key == "tokens":
            changed["eligible_turns"] = change
        else:
            changed["billing"] = change
        ref = exported.put(canonical_json(changed))
        altered = exported.put(Event(1, events[1].parent, "training_finished", {"result": ref.as_dict()}, (ref,)).to_bytes())
        with pytest.raises(RecordError, match="sealed execution evidence"):
            load_training_report(exported, altered, approved_manifest=plan.manifest)
    with pytest.raises(RecordError, match="operator-approved"):
        load_training_report(exported, head, approved_manifest=BlobRef.of(b"other split"))
    from behave.train import rollouts
    monkeypatch.setattr(rollouts, "run_batch", lambda **kw: pytest.fail("never rerun a committed rollout"))
    assert run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=Unavailable(), run=run, judge=Judge()) == result
    with pytest.raises(RecordError):
        run_rollouts(plan=replace(plan, group_ids=("new-group",)), approved_manifest=plan.manifest,
                     reader=Unavailable(), run=run, judge=Judge())


def test_training_allowlist_and_split_checked_before_dispatch(tmp_path, publication):
    plan = plan_at(tmp_path, publication)
    plan.preflight(publication.reader, approved_manifest=plan.manifest)
    with pytest.raises(RecordError, match="operator-approved"):
        plan.preflight(publication.reader, approved_manifest=BlobRef.of(b"wrong"))
    job = plan.execution.jobs[0]
    forbidden = replace(job, task=replace(job.task, revision=BlobRef.of(b"held-out-revision")))
    with pytest.raises(RecordError, match="outside"):
        replace(plan, execution=replace(plan.execution, jobs=(forbidden,))).preflight(
            publication.reader, approved_manifest=plan.manifest)
    row = parse_json(publication.reader.read(plan.manifest))
    row["split"] = "eval"
    with pytest.raises(RecordError, match="training split"):
        TrainingManifest.from_bytes(canonical_json(row))


@pytest.mark.isolation
def test_partial_judgment_settles_before_export_and_reads_legacy_reports(tmp_path, publication):
    plan = plan_at(tmp_path / "plan", publication)
    submitted, _ = setup(tmp_path / "submitted", publication)
    plan = replace(plan, execution=plan_for(submitted))
    run = JournalDirectory(tmp_path / "training")
    result = run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader,
                          run=run, judge=Judge(no_result=True))
    assert result["rewarded"] == 1
    assert result["candidates"][0]["examples"]["reward"] == .5
    head, _ = run.seal("training")
    assert load_training_report(run.objects, head, approved_manifest=plan.manifest) == result

    # Earlier reports kept the nullable aggregate. Read them without rewriting
    # the underlying result or changing the newly settled training reward.
    legacy = parse_json(canonical_json(result))
    legacy["candidates"][0]["examples"]["reward"] = None
    legacy["rewarded"] = 0
    ref = run.objects.put(canonical_json(legacy))
    terminal = load_chain(run.objects, head)[-1]
    legacy_head = run.objects.put(replace(terminal, payload={"result": ref.as_dict()}, artifacts=(ref,)).to_bytes())
    assert load_training_report(run.objects, legacy_head, approved_manifest=plan.manifest) == result
    assert parse_json(run.objects.read(ref))["candidates"][0]["examples"]["reward"] is None


def test_groups_are_optional_and_candidate_sized(tmp_path, publication):
    plan = plan_at(tmp_path, publication)
    assert replace(plan, group_ids=("experiment-group",)).group_ids == ("experiment-group",)
    with pytest.raises(ValueError, match="candidates, not turns"):
        replace(plan, group_ids=("a", "b"))


def test_training_cli_preflight_has_no_execution_effects(tmp_path, publication):
    import subprocess
    import sys

    plan = plan_at(tmp_path, publication)
    path, manifest, run = tmp_path / "plan.json", tmp_path / "train.json", tmp_path / "not_created"
    path.write_bytes(plan.to_bytes())
    manifest.write_bytes(publication.reader.read(plan.manifest))
    completed = subprocess.run([sys.executable, "-B", "-m", "behave.train", "--plan", str(path),
        "--approved-manifest", str(manifest), "--task-objects", str(publication.reader.directory),
        "--run-directory", str(run), "--dry-run"], capture_output=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert parse_json(completed.stdout)["status"] == "validated"
    assert not run.exists()


@pytest.mark.parametrize("invalid", [False, True])
def test_training_preflight_uses_only_read_capabilities(tmp_path, publication, monkeypatch, capsys, invalid):
    from behave.train import __main__ as entry

    plan = plan_at(tmp_path, publication)
    path, manifest = tmp_path / "plan.json", tmp_path / "manifest.json"
    path.write_bytes(b"malformed" if invalid else plan.to_bytes())
    manifest.write_bytes(publication.reader.read(plan.manifest))
    monkeypatch.setattr("behave.agent.run_store.JournalDirectory",
                        lambda *a: pytest.fail("preflight requested a writer"))
    monkeypatch.setattr("behave.train.rollouts.run_rollouts",
                        lambda **kw: pytest.fail("preflight dispatched a rollout"))
    status = entry.main(["--plan", str(path), "--approved-manifest", str(manifest),
                         "--task-objects", str(publication.reader.directory),
                         "--run-directory", str(tmp_path / "unused"), "--dry-run"])
    assert status == int(invalid)
    assert parse_json(capsys.readouterr().out.encode())["status"] == ("unavailable" if invalid else "validated")
    assert not (tmp_path / "unused").exists()
