"""Exact corpus gates; fixture fingerprints do not claim semantic curation."""

from dataclasses import replace

import pytest

from behave.core.artifacts import BlobRef, FileData, FileRef, SourceArtifact
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.corpus import (
    CorpusEntry,
    CorpusGuard,
    CorpusIndex,
    conflicts,
    guard_admissions,
    load_index,
    make_entry,
    summary,
)
from behave.data.drafts import SourceRecord
from behave.data.release_records import ReleasePolicy


def test_membership_reader_uses_recorded_chunks_not_current_default(monkeypatch):
    from types import SimpleNamespace

    from behave.core import runtime_defaults as defaults
    from behave.train.plan import chunks, verify_chunks

    refs = tuple(BlobRef.of(str(i).encode()) for i in range(1030))
    monkeypatch.setattr(defaults, "PUBLICATION_CHUNK_REFS", 1024)
    events = tuple(SimpleNamespace(kind="training_membership", payload=p, artifacts=a) for p, a in chunks(refs))
    assert len(events[0].artifacts) == 1024
    monkeypatch.setattr(defaults, "PUBLICATION_CHUNK_REFS", 2)
    verify_chunks(events, refs)
    with pytest.raises(RecordError):
        verify_chunks(events[:-1], refs)
    with pytest.raises(RecordError):
        verify_chunks(events[::-1], refs)


def source_at(publication, *, source_id="original", body=b"def f(x): return x\n", symbol="f"):
    artifact = SourceArtifact((publication.file("original.py", body),), "original.py", symbol)
    source = SourceRecord(source_id, "fixture:" + source_id, "r1", "fixture-license", artifact)
    return publication.put(source.to_bytes())


def entry_at(publication, *, task=None, source=None, family="fixture-counter", partition="train-original"):
    return make_entry(publication.reader, revision=publication.put((task or publication.task).to_bytes()),
                      source=source or source_at(publication), family=family, partition=partition)


def index_at(publication, entries):
    index = CorpusIndex("fixture-corpus/1", "fixture-operator", "Explicit fixture split; not a benchmark audit.", entries)
    return index, publication.put(index.to_bytes())


def guarded_publication(publication, extra=()):
    _, index = index_at(publication, (entry_at(publication), *extra))
    guard = CorpusGuard(index, "train-original")
    policy = ReleasePolicy("fixture/1", "reviewed-benchmark", publication.put(b"fixture guidance"),
                           publication.task.judge_policy, {"model": "fixture"}, corpus=guard)
    policy_ref = publication.put(policy.to_bytes())
    publication.release, publication.ref = publication.publish(policy=policy_ref, metadata={"corpus": guard.as_dict()})
    return guard


def current_plan(plan, publication):
    from behave.data.published import load_published_task
    from behave.train.plan import manifest_for_release

    task = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    job = replace(plan.execution.jobs[0], task=task.public)
    manifest = manifest_for_release(publication.reader, publication.ref)
    return replace(plan, execution=replace(plan.execution, jobs=(job,)), manifest=publication.put(manifest.to_bytes()))


def independent_task(publication, name):
    return replace(publication.task, task_id=name,
                   spec=publication.file("spec.md", ("Different specification: " + name).encode()),
                   reference_behavior=SourceArtifact((publication.file("model.py", ("# " + name).encode()),),
                                                       "model.py", "M"))


def test_exact_keys_ignore_task_and_source_labels_but_retain_provenance(publication):
    first = entry_at(publication)
    renamed = entry_at(publication, task=replace(publication.task, task_id="renamed"),
                       source=source_at(publication, source_id="other-origin"), family="different-label")
    index, ref = index_at(publication, (renamed, first))
    assert first.source != renamed.source and first.revision != renamed.revision
    assert all(getattr(first, k) == getattr(renamed, k) for k in ("source_key", "spec_key", "computation_key"))
    report = summary(publication.reader, ref)
    assert [g["kind"] for g in report["conflicts"]] == ["source", "spec", "computation"]
    assert all(g["tasks"] == [first.task_id, "renamed"] for g in report["conflicts"])
    assert not report["clean"] and report["semantic_deduplication"] == "not_checked"
    with pytest.raises(RecordError, match="explicit curation"):
        load_index(publication.reader, ref)
    assert load_index(publication.reader, ref, require_clean=False) == index


def test_distinct_symbols_in_same_source_file_are_not_exact_duplicates(publication):
    body = b"def f(x): return x\ndef g(x): return x + 1\n"
    first = entry_at(publication, source=source_at(publication, body=body, symbol="f"))
    other = entry_at(publication, task=independent_task(publication, "other"),
                     source=source_at(publication, body=body, symbol="g"))
    assert first.source_key != other.source_key
    index, _ = index_at(publication, (first, other))
    assert not conflicts(index)  # Same declared family is allowed within training.


def test_same_behavior_with_distinct_contract_is_not_computation_duplicate(publication):
    first = entry_at(publication)
    other_task = replace(publication.task, task_id="other", spec=publication.file("spec.md", b"Another width"),
                         contract=publication.file("task.toml", b"version=2\n"))
    other = entry_at(publication, task=other_task, source=source_at(publication, body=b"width = 16"))
    assert first.computation_key != other.computation_key
    assert not conflicts(index_at(publication, (first, other))[0])


@pytest.mark.parametrize("partition", ["train-original", "train-acquired", "final-test"])
def test_family_cross_split_check_is_distinct_from_byte_dedup(publication, partition):
    first = entry_at(publication)
    other = entry_at(publication, task=independent_task(publication, "other"),
                     source=source_at(publication, body=b"def unrelated(): return 7"), partition=partition)
    index, _ = index_at(publication, (first, other))
    found = conflicts(index)
    assert bool(found) == (partition == "final-test")
    if found:
        assert found == ({"kind": "family", "key": first.family,
                          "tasks": [first.task_id, "other"], "cross_split": True},)


def test_records_closed_canonical_and_operator_pinned(publication):
    entry = entry_at(publication)
    index, ref = index_at(publication, (entry,))
    guard = CorpusGuard(ref, "train-original")
    assert CorpusEntry.from_dict(entry.as_dict()) == entry
    assert CorpusGuard.from_dict(guard.as_dict()) == guard
    assert CorpusIndex.from_bytes(index.to_bytes()) == index
    with pytest.raises(RecordError):
        CorpusIndex.from_bytes(index.to_bytes() + b"\n")
    with pytest.raises(ValueError):
        replace(index, entries=(entry, entry))
    for invalid in ("train", "eval", True, None):
        with pytest.raises(ValueError):
            replace(entry, partition=invalid)
    with pytest.raises((RecordError, ValueError, TypeError)):
        replace(entry, source_key="not-a-hash")
    row = parse_json(index.to_bytes())
    row["allow_conflicts"] = True
    with pytest.raises(RecordError):
        CorpusIndex.from_bytes(canonical_json(row))
    with pytest.raises(ValueError):
        ReleasePolicy("p", "acquired-training", ref, ref, {}, corpus=guard)
    legacy = ReleasePolicy("p", "reviewed-benchmark", ref, ref, {})
    assert parse_json(legacy.to_bytes())["schema"] == "behave.release-policy/1"
    extended = replace(legacy, corpus=guard)
    assert ReleasePolicy.from_bytes(extended.to_bytes()) == extended
    assert parse_json(extended.to_bytes())["schema"] == "behave.release-policy/3"
    row = parse_json(extended.to_bytes())
    row["corpus"] = None
    with pytest.raises((RecordError, TypeError)):
        ReleasePolicy.from_bytes(canonical_json(row))


def test_builder_verifies_actual_content_without_a_second_size_budget(publication):
    source = source_at(publication)
    (publication.root / publication.task.spec.content.sha256).write_bytes(b"corrupt")
    with pytest.raises(RecordError):
        entry_at(publication, source=source)
    body = b"Large public specification.\n" * 650000
    large = replace(publication.task, spec=publication.file("spec.md", body))
    assert len(body) > 16 << 20
    assert entry_at(publication, source=source, task=large).task_id == publication.task.task_id


def test_conflict_output_is_grouped_not_pairwise(publication):
    entry = entry_at(publication)
    entries = tuple(replace(entry, task_id=f"task-{i:05}") for i in range(10000))
    index = CorpusIndex("large-fixture", "fixture", "grouped diagnostic", entries)
    found = conflicts(index)
    assert len(found) == 3 and all(len(g["tasks"]) == 10000 for g in found)
    assert len(index.to_bytes()) < 16 << 20


@pytest.mark.parametrize("change,code", [
    ({"partition": "final-test"}, "corpus-partition-mismatch"),
    ({"revision": BlobRef.of(b"another revision")}, "corpus-task-unassigned"),
    ({"source": BlobRef.of(b"wrong source")}, "corpus-fingerprint-mismatch"),
    ({"source_key": "a" * 64}, "corpus-fingerprint-mismatch"),
    ({"spec_key": "b" * 64}, "corpus-fingerprint-mismatch"),
    ({"computation_key": "c" * 64}, "corpus-fingerprint-mismatch"),
])
def test_publication_guard_uses_actual_admitted_content(publication, change, code):
    from behave.data.admission import Admission
    from behave.data.release_records import ReleaseDisposition

    entry = entry_at(publication, partition="train-acquired")
    _, ref = index_at(publication, (replace(entry, **change),))
    disposition = ReleaseDisposition(entry.revision, ref, entry.source, entry.task_id, entry.revision,
                                     "include", ("fixture",))
    with pytest.raises(RecordError) as error:
        guard_admissions(publication.reader, CorpusGuard(ref, "train-acquired"),
                          (Admission(disposition, publication.task, ()),))
    assert error.value.code == code


def test_constructed_package_publishes_and_replays_with_guard(tmp_path, monkeypatch):
    from test_release_admission import admit, candidate

    from behave.agent.run_store import JournalDirectory
    from behave.data.publication import load_publication, publish_release
    from behave.data.published import load_published_task
    from behave.train.plan import manifest_for_release

    c = candidate(tmp_path / "construct", monkeypatch)
    admitted = admit(c)
    revision = c.store.put(admitted.task.to_bytes())
    entry = make_entry(c.store, revision=revision, source=admitted.disposition.source,
                       family="identity", partition="train-acquired")
    index = c.store.put(CorpusIndex("fixture", "fixture", "reviewed fixture membership", (entry,)).to_bytes())
    policy = c.store.put(replace(c.policy, corpus=CorpusGuard(index, "train-acquired")).to_bytes())
    run = JournalDirectory(tmp_path / "publish")
    opts = {"release_id": "fixture", "policy": policy, "packages": (c.ref,), "reader": c.store, "destination": run}
    published = publish_release(**opts)
    task = load_published_task(run.objects, published.release, "identity")
    assert task.manifest == admitted.task
    assert not any("corpus" in file.reference.path for file in task.public.files)
    head, refs = run.seal("publication")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, refs)
    assert index in refs and load_publication(exported, head) == published
    manifest = manifest_for_release(exported, published.release)
    assert manifest.corpus == index and manifest.tasks == (("identity", revision),)
    assert publish_release(**opts) == published


def test_conflict_blocks_publication_without_changing_disposition_or_score(tmp_path, monkeypatch):
    from test_release_admission import admit, candidate

    from behave.agent.run_store import JournalDirectory
    from behave.data.publication import publish_release

    c = candidate(tmp_path / "construct", monkeypatch)
    admitted = admit(c)
    entry = make_entry(c.store, revision=c.store.put(admitted.task.to_bytes()), source=admitted.disposition.source,
                       family="identity", partition="train-acquired")
    index = c.store.put(CorpusIndex("bad", "fixture", "unresolved copy",
                                   (entry, replace(entry, task_id="renamed"))).to_bytes())
    policy = c.store.put(replace(c.policy, corpus=CorpusGuard(index, "train-acquired")).to_bytes())
    run = JournalDirectory(tmp_path / "publish")
    with pytest.raises(RecordError) as error:
        publish_release(release_id="conflict", policy=policy, packages=(c.ref,), reader=c.store, destination=run)
    assert error.value.code == "corpus-conflict"
    assert all(e.kind != "publication_finished" for e in run.journal("publication").load())
    assert admit(c) == admitted  # No rewriting of concrete admission or its evidence.


def test_guarded_training_binds_release_full_allowlist_and_public_view(tmp_path, publication):
    from test_training_rollouts import plan_at

    from behave.train.plan import TrainingManifest

    plan = plan_at(tmp_path, publication)
    guard = guarded_publication(publication)
    plan = current_plan(plan, publication)
    manifest = TrainingManifest.from_bytes(publication.reader.read(plan.manifest))
    closure = plan.preflight(publication.reader, approved_manifest=plan.manifest)
    assert {guard.index, manifest.release, publication.release.policy} <= set(closure)
    job, = plan.execution.jobs
    evil = FileData(FileRef("spec.md", publication.put(b"Injected held-out spec")), b"Injected held-out spec")
    files = tuple(evil if f.reference.path == "spec.md" else f for f in job.task.files)
    changed = replace(plan, execution=replace(plan.execution, jobs=(replace(job, task=replace(job.task, files=files)),)))
    with pytest.raises(RecordError) as error:
        changed.preflight(publication.reader, approved_manifest=plan.manifest)
    assert error.value.code == "training-public-view-mismatch"
    forged = replace(manifest, tasks=(*manifest.tasks, ("held-out", BlobRef.of(b"test revision"))))
    changed = replace(plan, manifest=publication.put(forged.to_bytes()))
    with pytest.raises(RecordError) as error:
        changed.preflight(publication.reader, approved_manifest=changed.manifest)
    assert error.value.code == "training-task-forbidden"  # Check even an unused allowlist entry.


@pytest.mark.parametrize("attack", ["test-partition", "unassigned", "wrong-index", "conflicting-index"])
def test_training_cannot_relabel_heldout_or_drop_index(tmp_path, publication, attack):
    from test_training_rollouts import plan_at

    from behave.train.plan import TrainingManifest

    plan = plan_at(tmp_path, publication)
    guard = guarded_publication(publication)
    index = load_index(publication.reader, guard.index)
    if attack == "test-partition":
        replacement = replace(index, entries=(replace(index.entries[0], partition="final-test"),))
    elif attack == "unassigned":
        replacement = replace(index, entries=(replace(index.entries[0], task_id="not-this-task"),))
    elif attack == "conflicting-index":
        replacement = replace(index, entries=(*index.entries, replace(index.entries[0], task_id="held-out",
                                                                      partition="final-test")))
    else:
        replacement = index
    ref = publication.put(replacement.to_bytes())
    rules = ReleasePolicy.from_bytes(publication.reader.read(publication.release.policy))
    changed = replace(rules, corpus=replace(guard, index=ref))
    publication.release, publication.ref = publication.publish(policy=publication.put(changed.to_bytes()),
                                                               metadata={"corpus": changed.corpus.as_dict()})
    from behave.data.published import load_published_task

    task = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    plan = replace(plan, execution=replace(plan.execution, jobs=(replace(plan.execution.jobs[0], task=task.public),)))
    manifest = TrainingManifest(publication.ref, ((publication.task.task_id, index.entries[0].revision),),
                                BlobRef.of(b"other index") if attack == "wrong-index" else ref)
    plan = replace(plan, manifest=publication.put(manifest.to_bytes()))
    with pytest.raises(RecordError):
        plan.preflight(publication.reader, approved_manifest=plan.manifest)


@pytest.mark.isolation
def test_guarded_training_cold_export_never_needs_heldout_bodies(tmp_path, publication, monkeypatch):
    from test_evaluation import Judge
    from test_role_process import Unavailable
    from test_training_rollouts import plan_at

    from behave.agent.journal import Event, load_chain
    from behave.agent.run_store import JournalDirectory
    from behave.train.learning_report import load_training_report
    from behave.train.rollouts import run_rollouts

    plan = plan_at(tmp_path, publication)
    heldout_task = independent_task(publication, "held-out")
    heldout = entry_at(publication, task=heldout_task, source=source_at(publication, body=b"PRIVATE_HELD_OUT_SOURCE"),
                       family="another-family", partition="final-test")
    guard = guarded_publication(publication, (heldout,))
    plan = current_plan(plan, publication)
    run = JournalDirectory(tmp_path / "training")
    result = run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader, run=run, judge=Judge())
    head, refs = run.seal("training")
    assert guard.index in refs
    private = {heldout.revision, heldout.source, heldout_task.spec.content,
               *(f.content for f in heldout_task.reference_behavior.files)}
    assert private.isdisjoint(refs)
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, refs)
    assert load_training_report(exported, head, approved_manifest=plan.manifest) == result
    from behave.train import rollouts
    monkeypatch.setattr(rollouts, "run_batch", lambda **kw: pytest.fail("cold read must not run a model"))
    assert run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=Unavailable(), run=run, judge=Judge()) == result
    events = load_chain(exported, head)
    assert [e.kind for e in events] == ["training_started", "training_membership", "training_finished"]
    dropped = exported.put(Event(1, BlobRef.of(events[0].to_bytes()), "training_finished",
                                 thaw_json(events[-1].payload), events[-1].artifacts).to_bytes())
    with pytest.raises(RecordError) as error:
        load_training_report(exported, dropped, approved_manifest=plan.manifest)
    assert error.value.code == "training-membership-mismatch"


def test_corpus_inspector_is_offline_and_read_only(tmp_path, publication, capsys):
    from behave.cli.inspect import main

    _, ref = index_at(publication, (entry_at(publication),))
    path = tmp_path / "reference.json"
    path.write_bytes(canonical_json(ref.as_dict()))
    before = set(publication.root.iterdir())
    assert main(["--kind", "corpus", "--record", str(path), "--objects", str(publication.root)]) == 0
    assert parse_json(capsys.readouterr().out.encode())["clean"] is True
    assert set(publication.root.iterdir()) == before


def test_legacy_manifest_cannot_replace_approved_guarded_manifest(tmp_path, publication):
    from test_training_rollouts import plan_at

    from behave.train.plan import TrainingManifest

    plan = plan_at(tmp_path, publication)
    guarded_publication(publication)
    plan = current_plan(plan, publication)
    manifest = TrainingManifest.from_bytes(publication.reader.read(plan.manifest))
    legacy = replace(manifest, corpus=None)
    assert parse_json(legacy.to_bytes())["schema"] == "behave.training-manifest/1"
    downgraded = replace(plan, manifest=publication.put(legacy.to_bytes()))
    with pytest.raises(RecordError) as error:
        downgraded.preflight(publication.reader, approved_manifest=plan.manifest)
    assert error.value.code == "training-manifest-unapproved"


def test_large_membership_closure_is_bounded_and_exact():
    from behave.agent.journal import Event
    from behave.train.plan import chunks, verify_chunks

    refs = tuple(BlobRef.of(str(i).encode()) for i in range(10000))
    parts = tuple(chunks((*refs, refs[0])))
    assert len(parts) == 20 and all(len(chunk) <= 512 for _, chunk in parts)
    assert tuple(chunks(tuple(reversed(refs)))) == parts
    events = tuple(Event(i, None, "training_membership", payload, chunk) for i, (payload, chunk) in enumerate(parts))
    assert all(len(e.to_bytes()) < 1 << 20 for e in events)
    verify_chunks(events, refs)
    for changed in (events[:-1], tuple(reversed(events)), (*events, events[0]),
                    (replace(events[0], artifacts=events[0].artifacts[:-1]), *events[1:])):
        with pytest.raises(RecordError) as error:
            verify_chunks(changed, refs)
        assert error.value.code == "training-membership-mismatch"


def test_guarded_training_cli_preflight_does_not_create_run(tmp_path, publication, capsys):
    from test_training_rollouts import plan_at

    from behave.train.__main__ import main

    plan = plan_at(tmp_path, publication)
    guarded_publication(publication)
    plan = current_plan(plan, publication)
    plan_file, approved = tmp_path / "plan.json", tmp_path / "approved.json"
    plan_file.write_bytes(plan.to_bytes())
    approved.write_bytes(publication.reader.read(plan.manifest))
    run = tmp_path / "no-dispatch"
    assert main(["--plan", str(plan_file), "--approved-manifest", str(approved), "--task-objects",
                 str(publication.root), "--run-directory", str(run), "--dry-run"]) == 0
    assert parse_json(capsys.readouterr().out.encode())["status"] == "validated"
    assert not run.exists()


@pytest.mark.isolation
def test_interrupted_membership_commit_resumes_before_dispatch(tmp_path, publication, monkeypatch):
    from test_evaluation import Judge
    from test_training_rollouts import plan_at

    from behave.agent.journal import AttemptJournal
    from behave.agent.run_store import JournalDirectory
    from behave.train.learning_report import load_training_report
    from behave.train.rollouts import run_rollouts

    plan = plan_at(tmp_path, publication)
    guarded_publication(publication)
    plan = current_plan(plan, publication)
    run = JournalDirectory(tmp_path / "training")
    append = AttemptJournal.append

    def interrupted(self, expected, kind, payload, artifacts=()):
        result = append(self, expected, kind, payload, artifacts)
        if kind == "training_membership":
            raise OSError("fixture post-commit loss")
        return result

    monkeypatch.setattr(AttemptJournal, "append", interrupted)
    with pytest.raises(OSError, match="post-commit loss"):
        run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader, run=run, judge=Judge())
    assert not (run.directory / "execution").exists()
    monkeypatch.setattr(AttemptJournal, "append", append)
    result = run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader, run=run, judge=Judge())
    assert result["requested"] == 1 and result["rewarded"] == 1
    head, _ = run.seal("training")
    assert load_training_report(run.objects, head, approved_manifest=plan.manifest) == result
