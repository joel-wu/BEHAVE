"""Explicit simulator-version updates retain the task and baseline definition."""

import json
from dataclasses import replace

import pytest

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import ReleaseManifest, TaskRevision, load_published_task
from behave.task_execution.judge_policy import JudgePolicy
from tools import eval_release


def source_release(publication, tmp_path):
    prior = JudgePolicy("old-command", "old-authority", baseline_cases=7, sequence_length=23,
                        search_seconds=0, score_timeout_seconds=321, seed="fixed-seed")
    attachments = [publication.file("LICENSE", b"PRIVATE_FIXTURE_LICENSE_NO_APPROVAL"),
                   publication.file("behavior2.py", b"raise AssertionError('PRIVATE_ARCHIVE_ONLY')\n")]
    first = replace(publication.task, judge_policy=publication.put(prior.to_bytes()),
                    metadata={**publication.task.as_dict()["metadata"],
                              "archive_files": [f.as_dict() for f in attachments]})
    second = replace(first, task_id="unselected")
    reader = DirectoryStore(publication.root)
    release = reader.put(ReleaseManifest("original", tuple(
        TaskRevision(task.task_id, reader.put(task.to_bytes())) for task in (first, second)),
        publication.policy, {"split": "fixture"}).to_bytes())
    (tmp_path / "release.json").write_bytes(canonical_json(release.as_dict()))
    tasks = tmp_path / "selected.txt"
    tasks.write_text(first.task_id + "\n")
    return first, second, prior, release, tasks


def test_refresh_preserves_exact_inputs_baseline_and_old_release_without_payload_reads(publication, tmp_path, monkeypatch):
    first, second, prior, release, tasks = source_release(publication, tmp_path)
    payloads = {first.spec.content, first.contract.content,
                *(file.content for file in first.reference_behavior.files),
                *(file.content for file in first.reference_rtl.files),
                *(file.content for file in first.archive_files)}
    original_read = DirectoryStore.read

    def metadata_only(store, reference):
        assert reference not in payloads, "version update must reuse saved task bytes without reading them"
        return original_read(store, reference)

    with monkeypatch.context() as patch:
        patch.setattr(DirectoryStore, "read", metadata_only)
        result = eval_release.refresh_simulator(tmp_path, tmp_path / "updated", [tasks], workers=2)
    objects = DirectoryStore(tmp_path / "updated/objects")
    new = load_published_task(objects, BlobRef.from_dict(result["release"]), first.task_id)
    assert replace(new.manifest, judge_policy=first.judge_policy) == first
    updated_policy = JudgePolicy.from_bytes(new.judge.judge_policy)
    assert replace(updated_policy, command_version=prior.command_version,
                   authority_version=prior.authority_version) == prior
    assert (updated_policy.command_version, updated_policy.authority_version) == (
        eval_release.api.COMMAND_VERSION, eval_release.api.RESULT_VERSION)
    assert len(new.release.tasks) == 1 and result["tasks"] == 1
    assert result["task_content_changed"] is result["old_scores_relabelled"] is False
    assert result["functional_validation_performed"] is False
    assert load_published_task(DirectoryStore(publication.root), release, first.task_id).manifest == first
    assert load_published_task(DirectoryStore(publication.root), release, second.task_id).manifest == second
    assert (objects.directory / first.spec.content.sha256).stat().st_ino == (
        publication.root / first.spec.content.sha256).stat().st_ino
    assert new.manifest.archive_files == first.archive_files
    for file in first.archive_files:
        assert objects.read(file.content) == publication.reader.read(file.content)
        assert file not in {f.reference for f in new.public.files}


def test_refresh_cli_does_not_read_current_workspace_or_start_sim(publication, tmp_path, monkeypatch, capsys):
    first, _, _, _, tasks = source_release(publication, tmp_path)
    monkeypatch.setattr(eval_release, "prepare", lambda *a, **kw: pytest.fail("workspace task rebuild"))
    monkeypatch.setattr(eval_release.api, "contract_of_task", lambda *a: pytest.fail("interface re-elaboration"))
    output = tmp_path / "updated"
    monkeypatch.setattr("sys.argv", ["eval_release", str(output), "--from-release", str(tmp_path),
                                   "--tasks", str(tasks), "--workers", "2"])
    eval_release.main()
    result = parse_json(capsys.readouterr().out.encode())
    assert result["tasks"] == 1
    inventory = json.loads((output / "inventory.json").read_bytes())
    assert inventory[0]["task_id"] == first.task_id and not inventory[0]["task_content_changed"]


def test_refresh_requires_selected_tasks_to_belong_to_the_source_release(publication, tmp_path):
    _, _, _, _, tasks = source_release(publication, tmp_path)
    tasks.write_text("not-in-release\n")
    with pytest.raises(KeyError):
        eval_release.refresh_simulator(tmp_path, tmp_path / "updated", [tasks], workers=2)
    assert not (tmp_path / "updated").exists()


def test_refresh_does_not_rewrite_a_corpus_membership_authority(publication, tmp_path):
    _, _, _, release, tasks = source_release(publication, tmp_path)
    reader = DirectoryStore(publication.root)
    original = ReleaseManifest.from_bytes(reader.read(release))
    protected = reader.put(replace(original, metadata={"corpus": "fixture-authority"}).to_bytes())
    (tmp_path / "release.json").write_bytes(canonical_json(protected.as_dict()))
    with pytest.raises(ValueError, match="corpus membership"):
        eval_release.refresh_simulator(tmp_path, tmp_path / "updated", [tasks], workers=2)
    assert not (tmp_path / "updated").exists()


def test_refresh_empty_selection_does_not_publish_a_release(publication, tmp_path):
    _, _, _, _, tasks = source_release(publication, tmp_path)
    tasks.write_text("\n")
    with pytest.raises(ValueError, match="at least one task"):
        eval_release.refresh_simulator(tmp_path, tmp_path / "updated", [tasks])
    assert not (tmp_path / "updated").exists()
