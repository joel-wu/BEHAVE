"""Compose explicit training allowlists for the existing trainer.

This is not admission or a new sampler. Inputs are the selected seed manifest
and already-admitted generated manifests, not every task in their source releases.
Task revisions stay unchanged, so the native task-pool state can retain cooldowns.
"""

from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore
from behave.data.published import ReleaseManifest, TaskManifest, TaskRevision
from behave.data.published.files import task_files
from behave.train.plan import TrainingManifest, validate_membership

from .input_stores import ReadSet


def _pool_records(manifests, release_id, reader):
    """Compose metadata and enumerate payloads, without loading B/R source bytes."""
    tracked = ReadSet(reader)
    parents = tuple(dict.fromkeys(manifests))
    tasks, payloads = {}, {}
    for reference in parents:
        manifest = TrainingManifest.from_bytes(read_verified(tracked, reference))
        release = ReleaseManifest.from_bytes(read_verified(tracked, manifest.release))
        payloads[release.policy] = None
        if manifest.corpus is not None or "corpus" in release.metadata:
            validate_membership(tracked, manifest, ())
        revisions = {item.task_id: item.manifest for item in release.tasks}
        for task_id, revision in manifest.tasks:
            if revisions.get(task_id) != revision:
                raise RecordError("training-task-forbidden", "selected revision differs from its published release")
            task = TaskManifest.from_bytes(read_verified(tracked, revision))
            if task.task_id != task_id:
                raise RecordError("training-task-forbidden", "selected revision belongs to another task")
            if task_id in tasks and tasks[task_id] != revision:
                raise RecordError("training-pool-conflict", "select one revision before merging the training pool")
            tasks[task_id] = revision
            files = task_files(task)
            payloads.update(dict.fromkeys((task.run_policy, task.judge_policy, *(f.content for f in files))))
    if not tasks:
        raise ValueError("the cumulative training pool requires an approved seed or admitted task")
    provenance = {"schema": "behave.training-pool/1", "inputs": [ref.as_dict() for ref in parents]}
    policy = canonical_json(provenance)
    release = ReleaseManifest(release_id, tuple(TaskRevision(task, revision) for task, revision in tasks.items()),
                              BlobRef.of(policy), {"training_pool": provenance})
    return release, policy, tuple(dict.fromkeys((*tracked.references, *payloads)))


def retain_training_manifest(*, manifest, reader, objects, publish=None):
    """Keep selected task inputs once, using trusted producer links when supplied."""
    _, _, references = _pool_records((manifest,), "retained-inputs", reader)
    if publish is not None:
        publish(objects, references)
    elif isinstance(reader, DirectoryStore):
        objects.publish_owned(reader, references)
    else:
        objects.publish(reader, references)
    return references


def cumulative_training_manifest(*, manifests, release_id, reader, objects):
    """Publish the approved union, retaining task revisions and native cooldown IDs.

    Original membership guards apply to each input. No admission, sampling,
    rescoring or optimizer execution occurs here.
    """
    release, policy, references = _pool_records(manifests, release_id, reader)
    if isinstance(reader, DirectoryStore):
        objects.publish_owned(reader, references)
    else:
        objects.publish(reader, references)
    objects.put(policy)
    reference = objects.put(release.to_bytes())
    manifest = TrainingManifest(reference, tuple((task.task_id, task.manifest) for task in release.tasks))
    objects.put(manifest.to_bytes())
    return manifest


def expected_training_manifest(*, manifests, release_id, reader):
    """Read-only counterpart for explicit offline round checks."""
    release, _, _ = _pool_records(manifests, release_id, reader)
    return TrainingManifest(BlobRef.of(release.to_bytes()),
                            tuple((task.task_id, task.manifest) for task in release.tasks))
