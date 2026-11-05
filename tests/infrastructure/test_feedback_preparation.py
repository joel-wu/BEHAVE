"""Feedback preparation retains safe inputs, not a cache of private goldens."""

import weakref
from collections import Counter
from dataclasses import replace

import pytest
from test_evaluation import plan_for
from test_improvement_feedback import report_at
from test_task_workflow import setup

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.data.published import PublishedReleaseReader, TaskRevision, load_published_task
from behave.improve import feedback as feedback_module
from behave.improve.feedback import FeedbackWindow
from behave.improve.input_stores import ReadSet
from behave.train.plan import TrainingManifest


def prepared(tmp_path, publication):
    job, _ = setup(tmp_path / "config", publication)
    tasks = tuple(replace(publication.task, task_id=f"feedback-{i}") for i in range(3))
    revisions = tuple(TaskRevision(task.task_id, publication.put(task.to_bytes())) for task in tasks)
    absent = TaskRevision("unselected", BlobRef.of(b"unselected manifest is not stored"))
    _, release = publication.publish(tasks=(*revisions, absent))
    public = tuple(load_published_task(publication.reader, release, task.task_id).public for task in tasks)
    jobs = tuple(replace(job, task=public[index], attempt=replace(job.attempt, attempt_id=f"sample-{i}"))
                 for i, index in enumerate((2, 0, 2, 1)))
    store, report = report_at(tmp_path / "batch", publication, jobs[0],
                             ("pass", "partial", "no_submission", "unavailable"), plan=plan_for(*jobs))
    manifest = store.put(TrainingManifest(release, tuple((r.task_id, r.manifest) for r in revisions)).to_bytes())
    request = feedback_module.FeedbackRequest("batch", store.put(b"weights"), manifest, job.model.identity, (report.head,))
    return store, request, release, revisions, absent


class SingleTaskReader:
    """Reference path: repeat the original single-task loader for each task."""

    def __init__(self, reader, reference):
        self.reader, self.reference = reader, reference

    def load(self, task_id):
        return load_published_task(self.reader, self.reference, task_id)


@pytest.mark.parametrize("recorded", [False, True])
@pytest.mark.parametrize("schema", ["behave.train-feedback-request/1", "behave.train-feedback-request/2"])
def test_batch_projection_keeps_exact_files_and_consumed_references(
        tmp_path, publication, monkeypatch, recorded, schema):
    store, request, release, revisions, absent = prepared(tmp_path, publication)
    request = replace(request, schema=schema)
    counts = Counter()

    class CountingReader:
        def read(self, reference):
            counts[reference] += 1
            return store.read(reference)

    consumed = ReadSet(CountingReader())
    actual = feedback_module.export_feedback(request, consumed, approved_manifest=request.training_manifest,
                                      recorded_reports=recorded)
    assert counts[release] == 1
    assert all(counts[revision.manifest] == 1 for revision in revisions)
    assert counts[absent.manifest] == 0
    monkeypatch.setattr(feedback_module, "PublishedReleaseReader", SingleTaskReader)
    prior_reads = ReadSet(store)
    previous = feedback_module.export_feedback(request, prior_reads, approved_manifest=request.training_manifest,
                                        recorded_reports=recorded)
    assert actual == previous  # Every attachment, identity, statistic and byte.
    assert tuple(consumed.references) == tuple(prior_reads.references)
    contents = b"\n".join(file.data for file in actual.files)
    assert b"PRIVATE_BEHAVIOR" not in contents and b"PRIVATE_REFERENCE_RTL" not in contents
    assert b"UNSELECTED_PRIVATE_CONTEXT" not in contents


def test_private_payloads_are_released_before_candidate_diagnostics(
        tmp_path, publication, monkeypatch):
    store, request, _, _, _ = prepared(tmp_path, publication)
    retained, visited = [], []

    class TaskLifetime:
        def __init__(self, loaded):
            self.loaded = loaded  # Includes all private reference payloads.
            self.public, self.manifest = loaded.public, loaded.manifest

    class ObservedReader(PublishedReleaseReader):
        def load(self, task_id):
            assert all(ref() is None for ref in retained)
            loaded = TaskLifetime(super().load(task_id))
            retained.append(weakref.ref(loaded))
            visited.append(task_id)
            return loaded

    original = feedback_module.execution_feedback

    def diagnostics(*args, **kwargs):
        assert all(ref() is None for ref in retained)
        return original(*args, **kwargs)

    monkeypatch.setattr(feedback_module, "PublishedReleaseReader", ObservedReader)
    monkeypatch.setattr(feedback_module, "execution_feedback", diagnostics)
    feedback_module.export_feedback(request, store, approved_manifest=request.training_manifest, recorded_reports=True)
    assert visited == ["feedback-2", "feedback-0", "feedback-1"]
    assert all(ref() is None for ref in retained)


@pytest.mark.parametrize("part", ["inventory", "task", "private-rtl", "private-behavior", "policy"])
def test_recorded_projection_still_verifies_declared_private_inputs(
        tmp_path, publication, part):
    store, request, release, revisions, _ = prepared(tmp_path, publication)
    reference = {"inventory": release, "task": revisions[0].manifest,
                 "private-rtl": publication.task.reference_rtl.files[0].content,
                 "private-behavior": publication.task.reference_behavior.files[0].content,
                 "policy": publication.task.run_policy}[part]
    path = store.directory / reference.sha256
    path.chmod(0o600)
    path.write_bytes(b"!" * reference.size_bytes)
    with pytest.raises(RecordError) as error:
        feedback_module.export_feedback(request, store, approved_manifest=request.training_manifest, recorded_reports=True)
    assert error.value.code == "content-identity-mismatch"


def test_next_export_does_not_reuse_a_previous_private_validation(tmp_path, publication):
    store, request, _, _, _ = prepared(tmp_path, publication)
    feedback_module.export_feedback(request, store, approved_manifest=request.training_manifest, recorded_reports=True)
    reference = publication.task.reference_rtl.files[0].content
    path = store.directory / reference.sha256
    path.chmod(0o600)
    path.write_bytes(b"!" * reference.size_bytes)
    with pytest.raises(RecordError, match="digest differs"):
        feedback_module.export_feedback(request, store, approved_manifest=request.training_manifest, recorded_reports=True)


@pytest.mark.parametrize("recorded", [False, True])
def test_window_preserves_update_boundaries_and_files(tmp_path, publication, monkeypatch, recorded):
    from test_improvement_feedback_window import window_fixture

    store, window = window_fixture(tmp_path, publication)
    assert isinstance(window, FeedbackWindow)
    actual = feedback_module.export_feedback(window, store, approved_manifest=window.training_manifest,
                                      recorded_reports=recorded)
    monkeypatch.setattr(feedback_module, "PublishedReleaseReader", SingleTaskReader)
    previous = feedback_module.export_feedback(window, store, approved_manifest=window.training_manifest,
                                        recorded_reports=recorded)
    assert actual == previous
