from __future__ import annotations

import json
from collections import Counter
from dataclasses import FrozenInstanceError, replace

import pytest

from behave.core.artifacts import BlobRef, FileRef, SourceArtifact
from behave.core.records import RecordError, canonical_json
from behave.data.published import ReleaseManifest, TaskManifest, TaskRevision, load_published_task, task_files


@pytest.mark.parametrize('with_rtl', [False, True])
def test_task_file_inventory_preserves_declaration_order_without_reading_or_mutating(publication, with_rtl):
    task = replace(publication.task, reference_rtl=publication.task.reference_rtl if with_rtl else None,
                   metadata={'provenance': {'content': BlobRef.of(b'not an artifact').as_dict()}})
    before = task.to_bytes()
    expected = (task.spec, task.contract, *task.public_files, *task.reference_behavior.files,
                *(() if task.reference_rtl is None else task.reference_rtl.files))
    assert task_files(task) == expected
    assert task.to_bytes() == before
    assert TaskManifest.from_bytes(before) == task
    assert not {task.run_policy, task.judge_policy} & {file.content for file in task_files(task)}


def test_task_file_inventory_does_not_merge_public_and_private_namespaces(publication):
    public = publication.file('model.py', b'PUBLIC_DECLARATION')
    rtl = publication.file('model.py', b'PRIVATE_RTL')
    task = replace(publication.task, public_files=(public,),
                   reference_rtl=SourceArtifact((rtl,), 'model.py', 'top'))
    expected = (public, next(f for f in task.reference_behavior.files if f.path == 'model.py'), rtl)
    assert tuple(f for f in task_files(task) if f.path == 'model.py') == expected
    _, release = publication.publish(task)
    loaded = load_published_task(publication.reader, release, task.task_id)
    assert next(f.data for f in loaded.public.files if f.reference.path == 'model.py') == b'PUBLIC_DECLARATION'
    assert all(b'PRIVATE' not in f.data for f in loaded.public.files)
    assert loaded.judge.reference_rtl.files[0].data == b'PRIVATE_RTL'


def test_task_file_inventory_preserves_repeated_content_and_does_not_add_unreferenced_files(publication):
    task = replace(publication.task, public_files=(publication.task.reference_behavior.files[0],))
    shared = task.public_files[0]
    assert task_files(task).count(shared) == 2
    unrelated = publication.file('not-declared.py', b'NOT_REFERENCED')
    assert unrelated not in task_files(task)


def test_batch_reader_reads_and_parses_release_once_without_caching_task_payloads(publication, monkeypatch):
    import weakref

    from behave.data.published import PublishedReleaseReader
    from behave.data.published import reader as implementation

    tasks = tuple(replace(publication.task, task_id=f'task-{i}') for i in range(5))
    revisions = tuple(TaskRevision(task.task_id, publication.put(task.to_bytes())) for task in tasks)
    _, reference = publication.publish(tasks=revisions)
    reads, parses, loaders = Counter(), [], []
    original = ReleaseManifest.from_bytes
    original_loader = implementation._Loader

    def loader(reader):
        result = original_loader(reader)
        loaders.append(weakref.ref(result))
        return result

    def parse(payload):
        parses.append(payload)
        return original(payload)

    class CountingReader:
        def read(self, ref):
            reads[ref] += 1
            return publication.reader.read(ref)

    monkeypatch.setattr(ReleaseManifest, 'from_bytes', parse)
    monkeypatch.setattr(implementation, '_Loader', loader)
    batch = PublishedReleaseReader(CountingReader(), reference)
    for task in tasks:
        loaded = batch.load(task.task_id)
        assert loaded.manifest == task and loaded.public.release == reference
        del loaded
        assert all(handle() is None for handle in loaders), 'task payload caches must be released after each load'
    assert reads[reference] == 1 and len(parses) == 1
    # Shared files/policies still undergo each task's verified load. Only the
    # release inventory is retained, not an ever-growing cache of task content.
    assert reads[publication.task.spec.content] == len(tasks)


def test_batch_reader_matches_single_task_views_and_keeps_membership_check(publication):
    from behave.data.published import PublishedReleaseReader

    batch = PublishedReleaseReader(publication.reader, publication.ref)
    assert batch.load(publication.task.task_id) == load_published_task(
        publication.reader, publication.ref, publication.task.task_id)
    with pytest.raises(RecordError) as error:
        batch.load('not-in-release')
    assert error.value.code == 'release-task-absent'
    with pytest.raises(TypeError):
        batch.load(1)
    with pytest.raises(TypeError):
        PublishedReleaseReader(publication.reader, 'latest')


def test_batch_reader_exposes_only_its_immutable_verified_manifest(publication):
    from behave.data.published import PublishedReleaseReader

    missing = TaskRevision('unselected', BlobRef.of(b'not stored'))
    release, reference = publication.publish(tasks=(*publication.release.tasks, missing))
    batch = PublishedReleaseReader(publication.reader, reference)
    assert batch.manifest is batch.manifest
    assert batch.manifest == release
    assert batch.manifest.to_bytes() == publication.reader.read(reference)
    with pytest.raises(AttributeError):
        batch.manifest = publication.release
    with pytest.raises(FrozenInstanceError):
        batch.manifest.release_id = 'different'
    with pytest.raises(TypeError):
        batch.manifest.metadata['split'] = 'different'


@pytest.mark.parametrize('part', ['manifest', 'spec', 'release-policy', 'reference-rtl'])
def test_batch_reader_rechecks_task_content_between_loads(publication, part):
    from behave.data.published import PublishedReleaseReader

    batch = PublishedReleaseReader(publication.reader, publication.ref)
    batch.load(publication.task.task_id)
    ref = {'manifest': publication.release.tasks[0].manifest,
           'spec': publication.task.spec.content, 'release-policy': publication.policy,
           'reference-rtl': publication.task.reference_rtl.files[0].content}[part]
    (publication.root / ref.sha256).write_bytes(b'!' * ref.size_bytes)
    with pytest.raises(RecordError) as error:
        batch.load(publication.task.task_id)
    assert error.value.code == 'content-identity-mismatch'


def test_batch_reader_pins_verified_inventory_but_does_not_preload_unselected_tasks(publication):
    from behave.data.published import PublishedReleaseReader

    missing = TaskRevision('unselected', BlobRef.of(b'not stored'))
    _, reference = publication.publish(tasks=(*publication.release.tasks, missing))
    batch = PublishedReleaseReader(publication.reader, reference)
    (publication.root / reference.sha256).write_bytes(b'!' * reference.size_bytes)
    assert batch.load(publication.task.task_id).manifest == publication.task
    with pytest.raises(RecordError) as error:
        batch.load('unselected')
    assert error.value.code == 'content-unavailable'
    with pytest.raises(RecordError) as error:
        PublishedReleaseReader(publication.reader, reference)
    assert error.value.code == 'content-identity-mismatch'


def test_pinned_publication_resolves_the_whole_task_without_executing_sources(publication):
    got = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    assert got.manifest == publication.task
    assert got.release == publication.release
    assert got.public.revision == publication.release.tasks[0].manifest
    assert got.public.release == publication.ref
    assert got.public.revision == got.judge.revision
    assert got.public.contract_path == "task.toml"
    assert got.judge.reference_behavior.artifact.entry_symbol == "Counter"
    assert {item.reference.path for item in got.judge.reference_behavior.files} == {"model.py", "helper.py"}
    assert got.judge.reference_rtl.artifact.entry_symbol == "top"
    assert got.judge.run_policy == b"PRIVATE_RUN_POLICY"
    assert got.judge.judge_policy == b"PRIVATE_JUDGE_POLICY"


def test_consumer_views_cannot_wrap_mutable_collections(publication):
    got = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    with pytest.raises(TypeError):
        replace(got.public, files=list(got.public.files))
    with pytest.raises(TypeError):
        replace(got.judge, run_policy=bytearray(got.judge.run_policy))
    with pytest.raises(TypeError):
        replace(got.judge.reference_behavior, files=list(got.judge.reference_behavior.files))
    with pytest.raises(RecordError):
        replace(got.public, spec_path="private/not-in-public-view.py")
    with pytest.raises(RecordError):
        replace(got.judge.reference_behavior, files=got.public.files)


def test_metadata_roundtrips_without_becoming_an_agent_input(publication):
    source = {"family": "counter", "tier": 2, "refs": ["repo/source.c"],
              "licence": "license-ref", "provenance": {"commit": "source-revision"}}
    manifest = replace(publication.task, metadata=source)
    serialized = manifest.to_bytes()
    source["refs"].append("changed")
    source["provenance"]["commit"] = "changed"
    assert manifest.to_bytes() == serialized
    restored = TaskManifest.from_bytes(serialized)
    assert restored.as_dict()["metadata"] == {
        "family": "counter", "tier": 2, "refs": ["repo/source.c"],
        "licence": "license-ref", "provenance": {"commit": "source-revision"},
    }
    restored.as_dict()["metadata"]["refs"].append("no back-channel")
    assert restored.to_bytes() == serialized
    assert ReleaseManifest.from_bytes(publication.release.to_bytes()).to_bytes() == publication.release.to_bytes()


@pytest.mark.parametrize("change", ["unknown", "version", "missing", "nested-unknown", "unsorted", "duplicate"])
def test_task_manifest_rejects_alternate_or_incomplete_shapes(publication, change):
    row = publication.task.as_dict()
    if change == "unknown":
        row["passed"] = True
    elif change == "version":
        row["schema"] = "behave.published-task/999"
    elif change == "missing":
        del row["judge_policy"]
    elif change == "nested-unknown":
        row["reference_behavior"]["files"][0]["content"]["path"] = "/private"
    elif change == "unsorted":
        row["reference_behavior"]["files"].reverse()
    else:
        row["public_files"].append(row["spec"])
    with pytest.raises(RecordError):
        TaskManifest.from_bytes(canonical_json(row))


@pytest.mark.parametrize("record_type", [TaskManifest, ReleaseManifest])
def test_published_manifests_are_canonical_and_reject_duplicate_keys(publication, record_type):
    record = publication.task if record_type is TaskManifest else publication.release
    with pytest.raises(RecordError) as found:
        record_type.from_bytes(json.dumps(record.as_dict(), indent=2).encode())
    assert found.value.code == "record-not-canonical"
    duplicate = record.to_bytes()[:-1] + b',"schema":"forged"}'
    with pytest.raises(RecordError) as found:
        record_type.from_bytes(duplicate)
    assert found.value.code == "record-key-duplicate"


def test_release_membership_cannot_be_bypassed_by_a_task_blob(publication):
    with pytest.raises(RecordError) as found:
        load_published_task(publication.reader, publication.ref, "not-in-release")
    assert found.value.code == "release-task-absent"
    wrong_entry = TaskRevision("another-name", publication.release.tasks[0].manifest)
    _, ref = publication.publish(tasks=(wrong_entry,))
    with pytest.raises(RecordError) as found:
        load_published_task(publication.reader, ref, "another-name")
    assert found.value.code == "release-task-mismatch"
    with pytest.raises(RecordError) as found:
        replace(publication.release, tasks=(wrong_entry, wrong_entry))
    assert found.value.code == "release-task-duplicate"


def test_old_and_new_releases_select_their_own_immutable_revision(publication):
    changed = replace(publication.task, spec=publication.file("spec.md", b"PUBLIC revised specification"))
    _, newer_ref = publication.publish(changed)
    old = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    new = load_published_task(publication.reader, newer_ref, publication.task.task_id)
    assert old.public.revision != new.public.revision
    assert old.public.release != new.public.release
    assert next(item.data for item in old.public.files if item.reference.path == "spec.md").startswith(b"PUBLIC:")
    assert next(item.data for item in new.public.files if item.reference.path == "spec.md") == b"PUBLIC revised specification"


@pytest.mark.parametrize("part", ["spec", "contract", "helper", "reference_rtl", "run", "judge", "release-policy"])
def test_every_declared_blob_is_verified_before_returning_a_task(publication, part):
    task = publication.task
    refs = {
        "spec": task.spec.content, "contract": task.contract.content,
        "helper": next(item.content for item in task.reference_behavior.files if item.path == "helper.py"),
        "reference_rtl": task.reference_rtl.files[0].content,
        "run": task.run_policy, "judge": task.judge_policy, "release-policy": publication.policy,
    }
    ref = refs[part]
    (publication.root / ref.sha256).write_bytes(b"!" * ref.size_bytes)
    with pytest.raises(RecordError) as found:
        load_published_task(publication.reader, publication.ref, task.task_id)
    assert found.value.code == "content-identity-mismatch"


def test_loaded_bytes_do_not_follow_later_store_mutation(publication):
    got = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    before = tuple(item.data for item in got.judge.reference_behavior.files)
    blob = got.judge.reference_behavior.files[0].reference.content
    (publication.root / blob.sha256).write_bytes(b"x" * blob.size_bytes)
    assert tuple(item.data for item in got.judge.reference_behavior.files) == before
    with pytest.raises(RecordError):
        load_published_task(publication.reader, publication.ref, publication.task.task_id)


def test_optional_reference_rtl_is_not_invented(publication):
    task = replace(publication.task, reference_rtl=None)
    _, ref = publication.publish(task)
    got = load_published_task(publication.reader, ref, task.task_id)
    assert got.judge.reference_rtl is None


@pytest.mark.parametrize("file_count, file_size", [(1, (16 << 20) + 1), (5, 13 << 20)])
def test_published_content_has_no_loader_specific_byte_caps(publication, file_count, file_size):
    # Cross both former limits: 16 MiB per object and 64 MiB per task.
    refs = tuple(publication.file(f"data/{index}.bin", bytes([index]) * file_size)
                 for index in range(file_count))
    task = replace(publication.task, public_files=refs)
    _, ref = publication.publish(task)
    got = load_published_task(publication.reader, ref, task.task_id)
    actual = {item.reference: item.data for item in got.public.files}
    for index, expected in enumerate(refs):
        assert len(actual[expected]) == file_size
        assert actual[expected] == bytes([index]) * file_size


def test_many_file_paths_share_one_read_per_content_object(publication):
    data = b"PUBLIC_SHARED_CONTENT"
    content = publication.put(data)
    refs = tuple(FileRef(f"data/{index}.bin", content) for index in range(1025))
    task = replace(publication.task, public_files=refs)
    _, ref = publication.publish(task)
    reads = Counter()

    class CountingReader:
        def read(self, reference):
            reads[reference] += 1
            return publication.reader.read(reference)

    got = load_published_task(CountingReader(), ref, task.task_id)
    loaded = [item.data for item in got.public.files if item.reference.content == content]
    assert len(loaded) == len(refs)
    assert loaded[0] == data
    assert all(item is loaded[0] for item in loaded)
    assert set(reads.values()) == {1}


def test_tampered_custom_resolver_and_missing_closure_refuse(publication):
    class WrongReader:
        def read(self, ref):
            return b"{}"

    with pytest.raises(RecordError) as found:
        load_published_task(WrongReader(), publication.ref, publication.task.task_id)
    assert found.value.code == "content-identity-mismatch"
    missing = BlobRef.of(b"not stored")
    task = replace(publication.task, public_files=(FileRef("missing.txt", missing),))
    _, ref = publication.publish(task)
    with pytest.raises(RecordError) as found:
        load_published_task(publication.reader, ref, task.task_id)
    assert found.value.code == "content-unavailable"
