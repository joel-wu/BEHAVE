"""Cumulative inputs reuse published-task and native cooldown interfaces."""

from dataclasses import replace

import pytest
from test_corpus import guarded_publication
from test_slime_training_plan import inputs

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.data.published import ReleaseManifest, TaskRevision, load_published_task
from behave.improve.training_pool import (
    cumulative_training_manifest,
    expected_training_manifest,
    retain_training_manifest,
)
from behave.train.plan import TrainingManifest, manifest_for_release
from behave.train.pool import empty_state, sync_state


def selection(publication, names, selected):
    revisions = {name: publication.put(replace(publication.task, task_id=name).to_bytes()) for name in names}
    release = ReleaseManifest("fixture-selected-pool", tuple(TaskRevision(name, ref) for name, ref in revisions.items()),
                              publication.release.policy)
    reference = publication.put(release.to_bytes())
    manifest = TrainingManifest(reference, tuple((name, revisions[name]) for name in selected))
    return publication.put(manifest.to_bytes()), manifest


def test_only_selected_tasks_are_combined_without_changing_revisions_or_cooldowns(tmp_path, publication):
    seed_ref, seed = selection(publication, ("seed", "not-selected"), ("seed",))
    new_ref, new = selection(publication, ("new", "another-unselected"), ("new",))
    state = sync_state(empty_state(), seed.tasks)
    state["tasks"]["seed"].update(category="mastered", cooldown_until=11, observed_groups=3)
    output = DirectoryStore(tmp_path / "pool")
    result = cumulative_training_manifest(manifests=(seed_ref, new_ref, seed_ref), release_id="round-1-pool",
                                         reader=publication.reader, objects=output)
    assert dict(result.tasks) == {**dict(seed.tasks), **dict(new.tasks)}
    resumed = sync_state(state, result.tasks)
    assert resumed["tasks"]["seed"] == state["tasks"]["seed"]
    assert resumed["tasks"]["new"]["observed_groups"] == 0
    cold = DirectoryStore(output.directory)
    for task_id, revision in result.tasks:
        task = load_published_task(cold, result.release, task_id)
        assert task.public.revision == revision
        assert b"PRIVATE" not in b"\n".join(file.data for file in task.public.files)
    assert cold.read(seed_ref) == seed.to_bytes() and cold.read(new_ref) == new.to_bytes()
    assert cumulative_training_manifest(manifests=(seed_ref, new_ref, seed_ref), release_id="round-1-pool",
                                        reader=cold, objects=output) == result
    assert expected_training_manifest(manifests=(seed_ref, new_ref, seed_ref), release_id="round-1-pool",
                                      reader=cold) == result


def test_owned_input_retention_reads_metadata_not_behavior_or_rtl(tmp_path, publication):
    ref, manifest = selection(publication, ("seed",), ("seed",))
    metadata = {ref, manifest.release, *(revision for _, revision in manifest.tasks)}
    class MetadataOnly:
        def read(self, reference):
            assert reference in metadata, "pool composition loaded a source payload"
            return publication.reader.read(reference)
    output = DirectoryStore(tmp_path / "retained")
    retained = retain_training_manifest(manifest=ref, reader=MetadataOnly(), objects=output,
        publish=lambda store, refs: store.publish_owned(DirectoryStore(publication.root), refs))
    assert ref in retained
    assert load_published_task(output, manifest.release, "seed").public.task_id == "seed"


def test_next_round_uses_the_previous_pool_without_reopening_its_original_stores(tmp_path, publication):
    seed_ref, seed = selection(publication, ("seed", "held-out"), ("seed",))
    first_ref, first = selection(publication, ("first",), ("first",))
    first_store = DirectoryStore(tmp_path / "round-1")
    previous = cumulative_training_manifest(manifests=(seed_ref, first_ref), release_id="round-1",
        reader=publication.reader, objects=first_store)
    second_ref, second = selection(publication, ("second",), ("second",))
    retain_training_manifest(manifest=second_ref, reader=publication.reader, objects=first_store)

    output = DirectoryStore(tmp_path / "round-2")
    current = cumulative_training_manifest(manifests=(BlobRef.of(previous.to_bytes()), second_ref),
        release_id="round-2", reader=DirectoryStore(first_store.directory), objects=output)
    assert dict(current.tasks) == {**dict(seed.tasks), **dict(first.tasks), **dict(second.tasks)}
    cold = DirectoryStore(output.directory)
    for task_id, revision in current.tasks:
        assert load_published_task(cold, current.release, task_id).public.revision == revision


def test_merged_pool_uses_native_checkpoint_cooldown_without_a_new_rollout(tmp_path, publication, inputs, monkeypatch):
    from test_slime_task_pool import completed_checkpoint, mock_scoring, source_at

    from behave.train.pool import SamplingPolicy, select_pool

    before = source_at(tmp_path / "before", publication, inputs)
    scoring = mock_scoring(monkeypatch, before)
    before.generate(0)
    checkpoint = completed_checkpoint(before, 0)
    old = TrainingManifest.from_bytes(DirectoryStore(before.binding["objects"]).read(
        BlobRef.from_dict(before.binding["manifest"])))
    old_ref = publication.put(old.to_bytes())
    publication.task = replace(publication.task, reference_rtl=None, judge_policy=load_published_task(
        publication.reader, old.release, old.tasks[0][0]).manifest.judge_policy)
    added_ref, _ = selection(publication, ("e",), ("e",))
    pool = cumulative_training_manifest(manifests=(old_ref, added_ref), release_id="next-pool",
        reader=publication.reader, objects=DirectoryStore(publication.root))
    after = source_at(tmp_path / "after", publication, inputs, manifest=pool,
                      parent=checkpoint, parent_reader=before.run.objects)
    after.args.load = before.args.save
    after.load(0)
    state = after._pool_state(after.journal.load())
    assert state == before._pool_state(before.journal.load())
    assert state["tasks"]["a"]["category"] == "mastered"
    assert after.native.first_step == 1
    assert set(dict(after._pool_tasks)) == set("abcde")
    assert load_published_task(publication.reader, pool.release, "e").manifest.reference_rtl is None
    selection_rows = select_pool(after._pool_tasks, state, SamplingPolicy.from_dict(after.binding["sampler"]), 1)
    assert all(row["task_id"] not in "ab" for row in selection_rows)
    assert len(scoring["requests"]) == 1


def test_conflicting_revisions_are_not_silently_replaced(tmp_path, publication):
    first, _ = selection(publication, ("seed",), ("seed",))
    publication.task = replace(publication.task, metadata={"revision": "changed"})
    second, _ = selection(publication, ("seed",), ("seed",))
    with pytest.raises(RecordError, match="select one revision"):
        cumulative_training_manifest(manifests=(first, second), release_id="conflict",
                                     reader=publication.reader, objects=DirectoryStore(tmp_path / "pool"))


def test_parent_corpus_gate_still_applies_before_composition(tmp_path, publication):
    guarded_publication(publication)
    manifest = manifest_for_release(publication.reader, publication.ref)
    ref = publication.put(manifest.to_bytes())
    result = cumulative_training_manifest(manifests=(ref,), release_id="guarded-pool",
        reader=publication.reader, objects=DirectoryStore(tmp_path / "valid"))
    assert result.tasks == manifest.tasks
    wrong = publication.put(replace(manifest, corpus=BlobRef.of(b"another corpus")).to_bytes())
    with pytest.raises(RecordError, match="another corpus"):
        cumulative_training_manifest(manifests=(wrong,), release_id="invalid",
            reader=publication.reader, objects=DirectoryStore(tmp_path / "invalid"))
