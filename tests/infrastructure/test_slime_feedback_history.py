"""Acquisition history survives independently prepared native jobs, without GPUs."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_slime_task_pool import source_at, completed_checkpoint, mock_scoring
from test_slime_training_plan import inputs  # noqa: F401

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.train import slime_acquisition as slime_acquisition_module


@pytest.mark.parametrize("jobs", [1, 2, 3])
def test_feedback_window_across_repeated_new_jobs(tmp_path, publication, inputs, monkeypatch, jobs):
    study = tmp_path / "study.json"
    study.write_bytes(canonical_json({"stages": [{"after_updates": 3, "runtime": "fixture.json"}]}))
    profile = ({**inputs[0], "self_improvement": {"study": str(study)}}, *inputs[1:])
    source = source_at(tmp_path / "job0", publication, profile)
    scoring = mock_scoring(monkeypatch, source)
    expected, head = [], None
    for step in range(3):
        if step and step < jobs:
            parent = source
            source = source_at(tmp_path / f"job{step}", publication, profile,
                parent=head, parent_reader=parent.run.objects)
            source.args.load = parent.args.save
            source.load(step - 1)
            scoring = mock_scoring(monkeypatch, source)
        scoring["version"] = step + 1
        source.generate(step)
        head = completed_checkpoint(source, step)
        expected.append(slime_acquisition_module.feedback_window(source, source.journal.load(),
            after_updates=step, completed_updates=step + 1)[0])
    actual = slime_acquisition_module.feedback_window(source, source.journal.load(),
        after_updates=0, completed_updates=3)
    assert actual == expected
    assert [row["update"] for row in actual] == [1, 2, 3]
    assert slime_acquisition_module.feedback_window(source, source.journal.load(),
        after_updates=1, completed_updates=3) == expected[1:]
    # No optimizer/model call is needed to read the window after another restart.
    cold = source_at(tmp_path / "reader_job", publication, profile,
        parent=head, parent_reader=source.run.objects)
    assert slime_acquisition_module.feedback_window(cold, (), after_updates=0, completed_updates=3) == expected
    # Reading receipt ancestry neither samples again nor ages task cooldowns.
    assert cold._parent_pool == source._pool_state(source.journal.load())
    assert len(scoring["requests"]) == 4 - jobs
    assert sum(event.payload.get("feedback_parent") is not None for event in source.journal.load()) == (jobs > 1)
    # Enter the real native boundary once; only the model worker is a fixture.
    cold.args.load = source.args.save
    cold.load(2)
    resumed = mock_scoring(monkeypatch, cold)
    acquisitions = []
    def acquire(argv, **kwargs):
        request = parse_json(Path(argv[-3]).read_bytes())
        acquisitions.append(request)
        assert request["feedback_window"] == expected
        Path(argv[-1]).write_bytes(canonical_json({"schema": "behave.native-acquisition/1",
            "completed_updates": 3, "parent_checkpoint": request["parent_checkpoint"],
            "training_binding": str(cold.path), "training_manifest": cold.binding["manifest"],
            "objects": cold.binding["objects"], "experience": None, "construction_history": None}))
        return SimpleNamespace(status="exited", returncode=0, stdout=b"", stderr=b"", elapsed_seconds=0,
                               cleanup_state=SimpleNamespace(quiescent=True))
    monkeypatch.setattr(slime_acquisition_module, "run_service", acquire)
    cold.generate(3)
    assert len(acquisitions) == 1 and len(resumed["requests"]) == 1


def test_feedback_rejects_uncommitted_or_abandoned_update(tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path / "job", publication, inputs)
    scoring = mock_scoring(monkeypatch, source)
    source.generate(0)
    completed_checkpoint(source, 0)
    scoring["version"] += 1
    source.generate(1)
    with pytest.raises(RecordError, match="missing committed feedback"):
        slime_acquisition_module.feedback_window(source, source.journal.load(), after_updates=0, completed_updates=2)
    source.native.abandon(reason="fixture interrupted update", workers_stopped=True)
    assert [row["update"] for row in slime_acquisition_module.feedback_window(source, source.journal.load(),
        after_updates=0, completed_updates=1)] == [1]
    with pytest.raises(RecordError, match="missing committed feedback"):
        slime_acquisition_module.feedback_window(source, source.journal.load(), after_updates=0, completed_updates=2)


def test_feedback_rejects_mismatched_save_receipt(tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path / "job", publication, inputs)
    mock_scoring(monkeypatch, source)
    source.generate(0)
    completed_checkpoint(source, 0)
    events = source.journal.load()
    events = (*events[:-1], replace(events[-1], payload={**events[-1].payload,
        "score": BlobRef.of(b"unrelated scored event").as_dict()}))
    with pytest.raises(RecordError, match="no matching scored receipt"):
        slime_acquisition_module.feedback_window(source, events, after_updates=0, completed_updates=1)


def test_legacy_missing_ancestor_fails_closed(tmp_path, publication, inputs, monkeypatch):
    study = tmp_path / "study.json"
    study.write_bytes(canonical_json({"stages": []}))
    profile = ({**inputs[0], "self_improvement": {"study": str(study)}}, *inputs[1:])
    first = source_at(tmp_path / "first", publication, profile)
    mock_scoring(monkeypatch, first)
    first.generate(0)
    head = completed_checkpoint(first, 0)
    second = source_at(tmp_path / "legacy", publication, profile, parent=head, parent_reader=first.run.objects)
    second.args.load = first.args.save
    second.load(0)
    mock_scoring(monkeypatch, second)
    # Existing jobs did not record a transitive parent link. Do not invent it.
    with monkeypatch.context() as old:
        old.setattr(slime_acquisition_module, "retain_feedback_parent", lambda *args: ({}, ()))
        second.generate(1)
        head = completed_checkpoint(second, 1)
    third = source_at(tmp_path / "third", publication, profile, parent=head, parent_reader=second.run.objects)
    assert [row["update"] for row in slime_acquisition_module.feedback_window(third, (),
        after_updates=1, completed_updates=2)] == [2]
    with pytest.raises(RecordError, match=r"missing committed feedback for updates \[1\]"):
        slime_acquisition_module.feedback_window(third, (), after_updates=0, completed_updates=2)
    # Publishing the exact legacy binding and authenticated parent closure is
    # sufficient; historical events and original rewards are not edited.
    from behave.core.storage import DirectoryStore
    from behave.train.slime_launch import import_parent
    objects=DirectoryStore(third.binding['objects'])
    objects.put(canonical_json(second.binding))
    import_parent(first.run.objects,BlobRef.from_dict(second.binding['parent_checkpoint']),objects,route='task')
    assert [r['update'] for r in slime_acquisition_module.feedback_window(third, (),
        after_updates=0,completed_updates=2)]==[1,2]


def test_non_syn_rounds_do_not_attach_feedback_ancestry(tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path / "first", publication, inputs)
    mock_scoring(monkeypatch, source)
    source.generate(0)
    head = completed_checkpoint(source, 0)
    cold = source_at(tmp_path / "second", publication, inputs, parent=head, parent_reader=source.run.objects)
    cold.args.load = source.args.save
    cold.load(0)
    mock_scoring(monkeypatch, cold)
    cold.generate(1)
    completed_checkpoint(cold, 1)
    assert all("feedback_parent" not in event.payload for event in cold.journal.load())


def legacy_transparent_chain(tmp_path, publication, inputs, monkeypatch, hops):
    from behave.core.storage import DirectoryStore

    first = source_at(tmp_path/'first/artifacts', publication, inputs)
    mock_scoring(monkeypatch, first)
    first.generate(0)
    head = completed_checkpoint(first, 0)
    parent_job, reader = tmp_path/'first', first.run.objects
    wrappers = []
    for i in range(hops):
        job = tmp_path/f'empty-resume-{i}'
        cold = source_at(job/'artifacts', publication, inputs, parent=head, parent_reader=reader)
        (job/'continuation.json').write_bytes(canonical_json({
            'checkpoint':head.as_dict(), 'first_update':1, 'job':str(parent_job)}))
        wrappers.append(job)
        parent_job, reader = job, DirectoryStore(cold.binding['objects'])
    job = tmp_path/'last'
    last = source_at(job/'artifacts', publication, inputs, parent=head, parent_reader=reader)
    last.args.load = first.args.save
    last.load(0)
    mock_scoring(monkeypatch, last)
    last.generate(1)
    completed_checkpoint(last, 1)
    (job/'continuation.json').write_bytes(canonical_json({
        'checkpoint':head.as_dict(), 'first_update':1, 'job':str(parent_job)}))
    return first, last, wrappers


@pytest.mark.parametrize('hops', [1, 2])
def test_retain_legacy_bindings_traverses_authenticated_empty_resumes(
        tmp_path, publication, inputs, monkeypatch, hops):
    from behave.core.storage import DirectoryStore
    first, last, _ = legacy_transparent_chain(tmp_path, publication, inputs, monkeypatch, hops)
    destination = DirectoryStore(tmp_path/'retained')
    before = tuple(event.to_bytes() for event in last.journal.load())
    slime_acquisition_module.retain_legacy_feedback_bindings(tmp_path/'last', last.journal.load(), destination)
    for source in (first, last):
        payload = canonical_json(source.binding)
        assert destination.read(BlobRef.of(payload)) == payload
    assert tuple(event.to_bytes() for event in last.journal.load()) == before


@pytest.mark.parametrize('change', ['missing', 'checkpoint', 'cursor', 'binding', 'cycle'])
def test_retain_legacy_bindings_rejects_unproven_empty_resume(
        tmp_path, publication, inputs, monkeypatch, change):
    from behave.core.storage import DirectoryStore
    _, last, wrappers = legacy_transparent_chain(tmp_path, publication, inputs, monkeypatch, 1)
    job = wrappers[0]
    path = job/'continuation.json'
    row = parse_json(path.read_bytes())
    if change == 'missing':
        path.unlink()
    elif change == 'binding':
        binding = job/'artifacts/plan/binding.json'
        value = parse_json(binding.read_bytes())
        value['parent_checkpoint'] = BlobRef.of(b'wrong parent').as_dict()
        binding.chmod(0o644)  # Deliberately corrupt only this isolated fixture.
        binding.write_bytes(canonical_json(value))
    else:
        row[{'checkpoint':'checkpoint', 'cursor':'first_update', 'cycle':'job'}[change]] = (
            BlobRef.of(b'wrong parent').as_dict() if change == 'checkpoint' else
            2 if change == 'cursor' else str(job))
        path.write_bytes(canonical_json(row))
    with pytest.raises(ValueError, match='feedback binding identity|feedback job ancestry'):
        slime_acquisition_module.retain_legacy_feedback_bindings(
            tmp_path/'last', last.journal.load(), DirectoryStore(tmp_path/'retained'))
