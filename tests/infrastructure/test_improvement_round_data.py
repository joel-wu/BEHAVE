"""Common-source handoff through real synthesis/admission/publication records.

Nonempty acquisitions are supplied as explicit fixtures in this lane. Their
process evidence is covered separately by supervised acquisition tests. The
synthesis roles are scripted and B1/B2 comparison is a named fixture, not proof
of simulator correctness or task quality.
"""

from dataclasses import replace

import pytest
from test_improvement_acquisition import configuration
from test_improvement_library import prepared
from test_improvement_materials import material_request, supplemental_library
from test_release_admission import admit, candidate
from test_slime_stage import native_worker
from test_slime_training_plan import inputs

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.published import ReleaseManifest, load_published_task
from behave.data.release_records import ReleaseDisposition, ReleaseRequest
from behave.improve.library import LibraryIndex, LookupPolicy, import_library
from behave.improve.materials import (
    FrozenMaterials,
    MaterialRequest,
    freeze_materials,
    raw_documents,
    supplement_materials,
)
from behave.improve.round_data import (
    admitted_training_manifest,
    committed_round_data,
    load_round_data,
    prepare_round_data,
)
from behave.train.plan import TrainingManifest, TrainingPlan
from behave.train.slime_data import prepare_task_plan
from behave.train.slime_stage import run_learning


class Unavailable:
    def read(self, ref):
        pytest.fail("cold replay read the old staging store")


def context(tmp_path, monkeypatch, *, selected=None):
    root, store, catalog, rows = prepared(tmp_path)
    library = import_library(root, catalog, store)
    index = LibraryIndex(store, library, LookupPolicy())
    request = MaterialRequest("round-1", store.put(b"checkpoint"), library,
        store.put(b"feedback fixture"), "fixture", ())
    selected = tuple(row["id"] for row in rows) if selected is None else selected
    material = FrozenMaterials(store.put(request.to_bytes()), tuple(index.units[key].content for key in selected))
    reference = store.put(material.to_bytes())
    # The source/task linkage is under test, not a forged acquisition receipt.
    monkeypatch.setattr("behave.improve.round_data.load_materials",
        lambda reader, ref: FrozenMaterials.from_bytes(read_verified(reader, ref)))
    index.inventory(tuple(row["id"] for row in rows), store)
    return store, index, reference, material


def package(tmp_path, monkeypatch, store, index, identifier, *, conclusion="supported", source=None):
    item = candidate(tmp_path, monkeypatch, source=source or index.source_record(identifier), source_reader=store,
        task_id="task-" + identifier[:10], conclusion=conclusion)
    admission = admit(item)
    store.publish(item.store, (item.ref, item.policy_ref, *admission.closure))
    return item


def test_empty_round_records_no_task_release_or_invented_raw_data(tmp_path, publication):
    store, role, _, _ = configuration(tmp_path, publication)
    request = material_request(store, role, ())
    store.put(request.to_bytes())
    frozen = freeze_materials(request, store)
    ref = store.put(frozen.to_bytes())
    run = JournalDirectory(tmp_path / "empty-round")
    result = prepare_round_data(materials=ref, publication=None, reader=store, run=run)
    assert result.release is result.publication is None
    assert result.materials == ref and frozen.documents == ()
    assert prepare_round_data(materials=ref, publication=None, reader=Unavailable(), run=run) == result
    head, closure = run.seal("round_data")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_round_data(exported, head) == result


@pytest.mark.parametrize("conclusion", ["supported", "unclear"])
def test_supplemented_sources_reach_both_routes_without_bypassing_admission(
        tmp_path, publication, monkeypatch, conclusion):
    store, role, _, _ = configuration(tmp_path, publication)
    request = material_request(store, role, ())
    store.put(request.to_bytes())
    original = store.put(freeze_materials(request, store).to_bytes())
    body = b"// Independent external material\nunsigned low_byte(unsigned x) { return x & 255; }\n"
    library = supplemental_library(tmp_path / "extra", store, (body,))
    revised = supplement_materials(materials=original, libraries=(library,), objects=store)
    index = LibraryIndex(store, library)
    identifier, = index.units
    index.inventory((identifier,), store)
    item = package(tmp_path / "task", monkeypatch, store, index, identifier, conclusion=conclusion)
    publication_request = ReleaseRequest("round-1", item.policy_ref, (item.ref,))
    with pytest.raises(RecordError, match="shared material"):
        prepare_round_data(materials=original, publication=publication_request, reader=store,
                           run=JournalDirectory(tmp_path / "old-material"))
    run = JournalDirectory(tmp_path / "updated-material")
    result = prepare_round_data(materials=revised, publication=publication_request, reader=store, run=run)
    release = ReleaseManifest.from_bytes(run.objects.read(result.release))
    assert len(release.tasks) == (1 if conclusion == "supported" else 0)
    assert raw_documents(store, original) == ()
    assert [file.data for file in raw_documents(run.objects, result.materials)] == [body]
    head, closure = run.seal("round_data")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_round_data(exported, head) == result
    assert [file.data for file in raw_documents(exported, result.materials)] == [body]


def test_common_material_retains_sources_of_deferred_tasks(tmp_path, monkeypatch):
    store, index, reference, material = context(tmp_path / "sources", monkeypatch)
    identifiers = tuple(index.units)
    accepted = package(tmp_path / "accepted", monkeypatch, store, index, identifiers[0])
    deferred = package(tmp_path / "deferred", monkeypatch, store, index, identifiers[1], conclusion="unclear")
    request = ReleaseRequest("round-1", accepted.policy_ref, (accepted.ref, deferred.ref))
    run = JournalDirectory(tmp_path / "round")
    result = prepare_round_data(materials=reference, publication=request, reader=store, run=run)
    release = ReleaseManifest.from_bytes(run.objects.read(result.release))
    assert (release.metadata["included"], release.metadata["pending"]) == (1, 1)
    assert FrozenMaterials.from_bytes(run.objects.read(result.materials)).documents == material.documents
    dispositions = [ReleaseDisposition.from_bytes(run.objects.read(BlobRef.from_dict(ref)))
                    for ref in thaw_json(release.metadata)["dispositions"]]
    assert {row.package for row in dispositions} == {accepted.ref, deferred.ref}
    task = load_published_task(run.objects, result.release, release.tasks[0].task_id)
    assert task.manifest.reference_behavior is not None
    assert all(not file.reference.path.startswith("source/") for file in task.public.files)
    assert all(file.path.startswith("source/") for file in material.documents)
    assert all("notices/" not in file.path and "origins" not in file.path for file in material.documents)
    head, closure = run.seal("round_data")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_round_data(exported, head) == result
    assert prepare_round_data(materials=reference, publication=request, reader=Unavailable(), run=run) == result


@pytest.mark.parametrize("supporting_selected", [True, False])
def test_source_bundle_requires_all_supporting_material_and_replays(tmp_path, monkeypatch, supporting_selected):
    store, index, reference, material = context(tmp_path / "sources", monkeypatch)
    primary, supporting = tuple(index.units)
    source = index.source_record(primary, (supporting,))
    item = package(tmp_path / "task", monkeypatch, store, index, primary, source=source)
    run = JournalDirectory(tmp_path / "round")
    request = ReleaseRequest("round-1", item.policy_ref, (item.ref,))
    if not supporting_selected:
        reference = store.put(replace(material, documents=(index.units[primary].content,)).to_bytes())
        with pytest.raises(RecordError, match="shared material"):
            prepare_round_data(materials=reference, publication=request, reader=store, run=run)
        return
    result = prepare_round_data(materials=reference, publication=request, reader=store, run=run)
    assert len(ReleaseManifest.from_bytes(run.objects.read(result.release)).tasks) == 1
    head, closure = run.seal("round_data")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_round_data(exported, head) == result


@pytest.mark.parametrize("mismatch", ["unacquired", "origin", "bytes"])
def test_task_source_cannot_drift_outside_common_material(tmp_path, monkeypatch, mismatch):
    store, index, reference, material = context(tmp_path / "sources", monkeypatch)
    identifier, other = tuple(index.units)
    source = index.source_record(identifier)
    if mismatch == "unacquired":
        reference = store.put(replace(material, documents=(index.units[other].content,)).to_bytes())
    elif mismatch == "origin":
        source = replace(source, origin="fixture://another-origin")
    else:
        source = replace(source, selected=index.source_record(other).selected)
    item = package(tmp_path / "task", monkeypatch, store, index, identifier, source=source)
    run = JournalDirectory(tmp_path / "round")
    with pytest.raises(RecordError, match="shared material"):
        prepare_round_data(materials=reference,
            publication=ReleaseRequest("round-1", item.policy_ref, (item.ref,)), reader=store, run=run)
    assert [event.kind for event in run.journal("round_data").load()] == ["round_data_requested", "round_materials_ready"]


def test_pending_only_round_keeps_its_raw_material(tmp_path, monkeypatch):
    store, index, reference, material = context(tmp_path / "sources", monkeypatch)
    item = package(tmp_path / "task", monkeypatch, store, index, next(iter(index.units)), conclusion="unclear")
    run = JournalDirectory(tmp_path / "round")
    result = prepare_round_data(materials=reference,
        publication=ReleaseRequest("round-1", item.policy_ref, (item.ref,)), reader=store, run=run)
    assert not ReleaseManifest.from_bytes(run.objects.read(result.release)).tasks
    assert FrozenMaterials.from_bytes(run.objects.read(result.materials)).documents == material.documents


def test_admitted_round_release_enters_existing_task_learning_stage(tmp_path, monkeypatch, inputs):
    from behave.sim import api

    configuration, _, _, _ = inputs
    store, index, reference, _ = context(tmp_path / "sources", monkeypatch)
    item = package(tmp_path / "task", monkeypatch, store, index, next(iter(index.units)))
    run = JournalDirectory(tmp_path / "round")
    result = prepare_round_data(materials=reference,
        publication=ReleaseRequest("round-1", item.policy_ref, (item.ref,)), reader=store, run=run)
    release = ReleaseManifest.from_bytes(run.objects.read(result.release))
    manifest = TrainingManifest(result.release, tuple((entry.task_id, entry.manifest) for entry in release.tasks))
    plan_path = tmp_path / "learning-plan"
    # Construction used the named fixture evaluator. Advertise that same
    # fixture runtime to preparation; this lane does not execute actual Sim.
    monkeypatch.setattr(api, "COMMAND_VERSION", "fixture")
    monkeypatch.setattr(api, "RESULT_VERSION", "fixture")
    binding = prepare_task_plan(plan_path, configuration, run.objects, manifest)
    objects = DirectoryStore(binding["objects"])
    plan = TrainingPlan.from_bytes(objects.read(BlobRef.from_dict(binding["plan"])), objects)
    assert {job.task.task_id for job in plan.execution.jobs} == {release.tasks[0].task_id}
    assert all(job.task.release == result.release for job in plan.execution.jobs)
    plan.preflight(objects, approved_manifest=BlobRef.from_dict(binding["manifest"]))
    options = {"plan": plan_path, "run": JournalDirectory(tmp_path / "learning"), "updates": 2,
        "load": tmp_path / "initial", "save": tmp_path / "checkpoints", "cpus": 16,
        "environment": {"PATH": "/usr/bin:/bin"}}
    # Native optimization is also a fixture; the shared stage,
    # binding, membership, checkpoint and recovery paths are production code.
    monkeypatch.setattr("behave.train.slime_stage.run_service", native_worker(options, configuration))
    learned = run_learning(**options)
    assert learned["status"] == "completed" and learned["route"] == "task"
    monkeypatch.setattr("behave.train.slime_stage.run_service", lambda *a, **kw: pytest.fail("cold native redispatch"))
    assert run_learning(**options) == learned


def test_generated_task_reuses_eval_and_eight_sample_training_inputs(tmp_path, monkeypatch, inputs):
    from behave.data.published import load_published_task
    from behave.sim import api
    from tools.eval_review_trial import load_tasks

    configuration, _, _, _ = inputs
    store, index, reference, _ = context(tmp_path / "sources", monkeypatch)
    item = package(tmp_path / "task", monkeypatch, store, index, next(iter(index.units)))
    run = JournalDirectory(tmp_path / "round")
    data = prepare_round_data(materials=reference,
        publication=ReleaseRequest("generated-rollout", item.policy_ref, (item.ref,)), reader=store, run=run)
    manifest = admitted_training_manifest(run.objects, data.release)
    task_id = manifest.tasks[0][0]
    task = load_published_task(run.objects, data.release, task_id)
    assert task.manifest.reference_rtl is None
    eval_objects, eval_tasks = load_tasks(tmp_path / "eval", {
        "task_objects": str(run.objects.directory), "release": data.release.as_dict(), "tasks": [task_id]})
    assert eval_tasks == (task.public,)
    assert eval_objects.read(task.public.revision) == run.objects.read(task.public.revision)

    # Explicit fixture runtime, not a changed real Sim identity or a model run.
    monkeypatch.setattr(api, "COMMAND_VERSION", "fixture")
    monkeypatch.setattr(api, "RESULT_VERSION", "fixture")
    configuration = {**configuration, "samples_per_task": 8}
    binding = prepare_task_plan(tmp_path / "rollout", configuration, run.objects, manifest)
    objects = DirectoryStore(binding["objects"])
    plan = TrainingPlan.from_bytes(objects.read(BlobRef.from_dict(binding["plan"])), objects)
    assert len(plan.execution.jobs) == 8
    assert plan.group_ids == (task_id,) * 8
    assert len({job.attempt.attempt_id for job in plan.execution.jobs}) == 8
    assert all(job.task == task.public for job in plan.execution.jobs)
    assert all(job.solver_experience is None for job in plan.execution.jobs)
    plan.preflight(objects, approved_manifest=BlobRef.from_dict(binding["manifest"]))


@pytest.mark.parametrize("shared", [False, True], ids=["legacy-flat", "shared-owner"])
def test_recovery_reuses_child_publication_after_an_interrupted_parent_commit(tmp_path, monkeypatch, shared):
    store, index, reference, _ = context(tmp_path / "sources", monkeypatch)
    item = package(tmp_path / "task", monkeypatch, store, index, next(iter(index.units)))
    run = JournalDirectory(tmp_path / "round")
    publication_directory = run.directory / "publication"
    if not shared:
        # An existing flat phase must keep its original storage during replay.
        (publication_directory / "objects").mkdir(parents=True)
    request = ReleaseRequest("round-1", item.policy_ref, (item.ref,))
    original = DirectoryStore.publish_owned

    def disconnected(self, reader, refs, **kwargs):
        child = JournalDirectory(publication_directory)
        if self.directory == run.objects.directory and reader.directory == child.objects.directory:
            # Resolve the durable owner, not an assumed physical child path.
            # The failure is after child commit but before parent commit in
            # both layouts, even when transferring shared objects is a no-op.
            assert child.journal("publication").load()[-1].kind == "publication_finished"
            raise OSError("fixture parent disconnected after child commit")
        return original(self, reader, refs, **kwargs)

    monkeypatch.setattr(DirectoryStore, "publish_owned", disconnected)
    with pytest.raises(OSError, match="parent disconnected"):
        prepare_round_data(materials=reference, publication=request, reader=store, run=run)
    assert tuple(event.kind for event in run.journal("round_data").load()) == (
        "round_data_requested", "round_materials_ready")
    child = JournalDirectory(publication_directory)
    assert (child.objects.directory == run.objects.directory) is shared
    before, _ = child.seal("publication")
    monkeypatch.setattr(DirectoryStore, "publish_owned", original)
    result = prepare_round_data(materials=reference, publication=request, reader=Unavailable(), run=run)
    assert result.publication == before
    assert child.seal("publication")[0] == before
    assert load_round_data(run.objects, result.head) == result
    with pytest.raises(RecordError, match="replay differs"):
        prepare_round_data(materials=reference, publication=replace(request, release_id="other"), reader=store, run=run)


def test_committed_handoff_reads_metadata_and_preserves_full_audit_closure(tmp_path, monkeypatch):
    store, index, reference, _ = context(tmp_path / "sources", monkeypatch)
    item = package(tmp_path / "task", monkeypatch, store, index, next(iter(index.units)))
    run = JournalDirectory(tmp_path / "round")
    result = prepare_round_data(materials=reference,
        publication=ReleaseRequest("round-1", item.policy_ref, (item.ref,)), reader=store, run=run)
    events = run.journal("round_data").load(verify_artifacts=False)
    metadata = {BlobRef.of(event.to_bytes()) for event in events}

    class ReceiptReader:
        def read(self, ref):
            assert ref in metadata, "receipt handoff reread an artifact payload"
            return run.objects.read(ref)

    receipt, closure = committed_round_data(ReceiptReader(), result.head)
    assert receipt == result
    assert metadata <= set(closure)
    exported = DirectoryStore(tmp_path / "export")
    exported.publish_owned(run.objects, closure)
    assert load_round_data(exported, result.head) == result

    with pytest.raises(AssertionError, match="artifact payload"):
        load_round_data(ReceiptReader(), result.head)
