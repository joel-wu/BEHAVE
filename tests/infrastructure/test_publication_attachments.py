"""Private distribution files survive shared handoffs, never becoming solutions."""

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from behave.core.artifacts import DirectoryReader
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.data.published import TaskManifest, load_published_task, task_files


def archived_task(publication):
    files = (publication.file("LICENSE", b"PRIVATE_FIXTURE_NOTICE_NOT_APPROVAL"),
             publication.file("behavior2.py", b"raise AssertionError('PRIVATE_ARCHIVE_ONLY')\n"),
             publication.file("rtl.sv", b"PRIVATE_UNREVIEWED_AUXILIARY_RTL"),
             publication.file("source.json", b"opaque PRIVATE_SOURCE bytes, not a validated license"))
    task = replace(publication.task, metadata={**publication.task.as_dict()["metadata"],
                    "archive_files": [f.as_dict() for f in files]})
    return task, files


def test_declared_archive_is_verified_once_without_entering_execution_views(publication):
    task, files = archived_task(publication)
    task = replace(task, metadata={**task.as_dict()["metadata"],
                   "archive_files": [task.spec.as_dict(), *(f.as_dict() for f in files)]})
    _, ref = publication.publish(task)
    reads = Counter()

    class Reader:
        def read(self, reference):
            reads[reference] += 1
            return publication.reader.read(reference)

    loaded = load_published_task(Reader(), ref, task.task_id)
    assert set(files) <= set(task_files(task))
    assert TaskManifest.from_bytes(task.to_bytes()) == task
    assert all(reads[f.content] == 1 for f in (*files, task.spec))
    assert loaded.public.files == load_published_task(publication.reader, publication.ref, task.task_id).public.files
    assert loaded.judge.reference_behavior.artifact == publication.task.reference_behavior
    assert loaded.judge.reference_rtl.artifact == publication.task.reference_rtl
    assert not set(files) & {f.reference for f in loaded.public.files}


@pytest.mark.parametrize("part", ["missing", "corrupt"])
def test_unavailable_private_archive_refuses_task_load(publication, part):
    task, files = archived_task(publication)
    _, ref = publication.publish(task)
    path = publication.root / files[0].content.sha256
    if part == "missing":
        path.unlink()
    else:
        path.write_bytes(b"!" * files[0].content.size_bytes)
    with pytest.raises(RecordError) as error:
        load_published_task(publication.reader, ref, task.task_id)
    assert error.value.code == ("content-unavailable" if part == "missing" else "content-identity-mismatch")


@pytest.mark.parametrize("invalid", ["null", "mapping", "traversal", "duplicate", "parent", "bad-ref"])
def test_archive_inventory_is_typed_and_path_validated(publication, invalid):
    valid = publication.file("LICENSE", b"fixture").as_dict()
    values = {"null": None, "mapping": valid, "traversal": [{**valid, "path": "../outside"}],
              "duplicate": [valid, valid], "parent": [valid, {**valid, "path": "LICENSE/child"}],
              "bad-ref": [{**valid, "content": {"sha256": "invalid", "size_bytes": 1}}]}
    with pytest.raises((RecordError, TypeError)):
        replace(publication.task, metadata={"archive_files": values[invalid]})


def test_archiving_provenance_does_not_change_computation_or_spec_fingerprint(publication):
    from behave.data.corpus import task_keys

    task, _ = archived_task(publication)
    assert task_keys(task) == task_keys(publication.task)
    # Other metadata is not recursively treated as executable artifact references.
    plain = replace(publication.task, metadata={"provenance": {"private_file": "not-a-reference"}})
    assert plain.archive_files == ()
    assert task_files(plain) == task_files(publication.task)


def test_eval_preparation_retains_attachments_offline_without_exposing_them(tmp_path, publication):
    from behave.cli.evaluation_plan import prepare
    from behave.eval.plan import EvaluationPlan

    task, files = archived_task(publication)
    _, ref = publication.publish(task)
    config = json.loads((Path(__file__).resolve().parents[2] / "configs/eval/local.example.json").read_text())
    output = tmp_path / "eval"
    prepare(configuration=config, task_objects=publication.root, release=ref,
            tasks=[task.task_id], output=output)
    publication.root.rename(tmp_path / "source-offline")
    reader = DirectoryReader(output / "inputs")
    loaded = load_published_task(reader, ref, task.task_id)
    plan = EvaluationPlan.from_bytes((output / "plan.json").read_bytes(), reader)
    assert loaded.manifest.archive_files == tuple(sorted(files, key=lambda f: f.path))
    assert not set(files) & {f.reference for f in plan.jobs[0].task.files}
    assert not (output / "run").exists()


def test_training_pool_retains_archives_without_reloading_their_bodies(tmp_path, publication, monkeypatch):
    from behave.improve.training_pool import cumulative_training_manifest, retain_training_manifest
    from behave.train.plan import TrainingManifest

    task, files = archived_task(publication)
    release, ref = publication.publish(task)
    manifest = TrainingManifest(ref, tuple((r.task_id, r.manifest) for r in release.tasks))
    original = DirectoryStore.read
    source = DirectoryStore(publication.root)
    retained = DirectoryStore(tmp_path / "retained")
    cumulative = DirectoryStore(tmp_path / "cumulative")

    def metadata_only(store, reference):
        assert reference not in {f.content for f in files}, "pool merge must not reload archive payloads"
        return original(store, reference)

    with monkeypatch.context() as patch:
        patch.setattr(DirectoryStore, "read", metadata_only)
        kept = retain_training_manifest(manifest=source.put(manifest.to_bytes()), reader=source, objects=retained)
        merged = cumulative_training_manifest(manifests=(source.put(manifest.to_bytes()),),
            release_id="retained-fixture", reader=source, objects=cumulative)
    assert {f.content for f in files} <= set(kept)
    assert merged.tasks == manifest.tasks
    publication.root.rename(tmp_path / "source-offline")
    assert load_published_task(retained, ref, task.task_id).manifest == task
    assert load_published_task(cumulative, merged.release, task.task_id).manifest == task
