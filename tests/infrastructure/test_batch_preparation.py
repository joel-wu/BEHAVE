"""Syn preflight streams selected tasks; no model or native worker is started."""

import weakref
from collections import Counter
from dataclasses import replace

import pytest
from test_improvement_learning_round import prepared as learning_options
from test_improvement_round import prepared as round_options
from test_slime_training_plan import inputs as inputs  # noqa: PLC0414 -- pytest fixture re-export

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.data.published import PublishedReleaseReader, ReleaseManifest, TaskManifest, TaskRevision
from behave.improve import learning_round
from behave.improve import round as improvement_round
from behave.train.plan import TrainingManifest


@pytest.mark.parametrize("stage", ["round", "learning"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_preflight_reads_one_inventory_and_releases_each_task(
        tmp_path, monkeypatch, publication, inputs, stage, corrupt):
    if stage == "round":
        options, calls = round_options(tmp_path, monkeypatch, publication, inputs)
        module, execute = improvement_round, improvement_round.run_round
        manifest_ref = options["training_manifest"]
    else:
        options = learning_options(tmp_path, monkeypatch, inputs)
        calls = []
        module, execute = learning_round, learning_round.learn_round
        manifest_ref = options["task_passes"][0][0]
    source = options["reader"]
    manifest = TrainingManifest.from_bytes(source.read(manifest_ref))
    release = ReleaseManifest.from_bytes(source.read(manifest.release))
    original = TaskManifest.from_bytes(source.read(manifest.tasks[0][1]))
    tasks = tuple(replace(original, task_id=f"selected-{i}") for i in range(3))
    revisions = tuple(TaskRevision(task.task_id, source.put(task.to_bytes())) for task in tasks)
    unselected = TaskRevision("not-selected", BlobRef.of(b"not stored"))
    reference = source.put(replace(release, tasks=(*revisions, unselected)).to_bytes())
    training = replace(manifest, release=reference,
                       tasks=tuple((item.task_id, item.manifest) for item in revisions))
    if stage == "round":
        options["training_manifest"] = source.put(training.to_bytes())
    else:
        options["task_passes"] = ((source.put(training.to_bytes()), 2),)
    if corrupt:
        ref = revisions[-1].manifest
        path = source.directory / ref.sha256
        path.chmod(0o600)
        path.write_bytes(b"!" * ref.size_bytes)

    reads, retained, visited, publications = Counter(), [], [], []
    read = source.read

    def counted(ref):
        reads[ref] += 1
        return read(ref)

    class TaskLifetime:
        # Preserve all loaded payloads while this object is reachable; a list
        # of validation-only results would keep these alive until batch end.
        def __init__(self, task):
            self.task, self.release = task, task.release

    class ObservedReader(PublishedReleaseReader):
        def load(self, task_id):
            assert all(item() is None for item in retained)
            visited.append(task_id)
            loaded = TaskLifetime(super().load(task_id))
            retained.append(weakref.ref(loaded))
            return loaded

    def stop_before_publication(tracked, destination, **kwargs):
        assert all(item() is None for item in retained)
        publications.append(tuple(tracked.references))
        raise InterruptedError("fixture stops after preflight")

    monkeypatch.setattr(source, "read", counted)
    monkeypatch.setattr(module, "PublishedReleaseReader", ObservedReader)
    monkeypatch.setattr(module.ReadSet, "publish", stop_before_publication)
    if corrupt:
        with pytest.raises(RecordError) as error:
            execute(**options)
        assert error.value.code == "content-identity-mismatch"
        assert publications == []
    else:
        with pytest.raises(InterruptedError, match="fixture stops after preflight"):
            execute(**options)
        assert len(publications) == 1
        assert reference in publications[0]
        assert all(item.manifest in publications[0] for item in revisions)
    assert reads[reference] == 1
    assert all(reads[item.manifest] == 1 for item in revisions)
    assert reads[unselected.manifest] == 0
    assert visited == [task.task_id for task in tasks]
    assert all(item() is None for item in retained)
    assert calls == []
    journal = "round" if stage == "round" else "paired_learning"
    assert options["run"].journal(journal).load() == ()
