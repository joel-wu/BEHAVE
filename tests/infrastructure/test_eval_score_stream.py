"""Live committed submissions reuse the ordinary scorer and original identities."""
# Imported pytest fixture names are intentionally reused as test arguments.
# ruff: noqa: F811

import json

import pytest
from test_rejudge import Judge, initial_run
from test_rejudge import local_rollout as fixture_rollout  # noqa: F401

from behave.core.records import thaw_json
from behave.eval.score_stream import committed_cases, consolidate, import_scores, publish_subset, scoring_sources
from behave.runtime.records import write_json
from behave.task_execution.batch import load_batch_report
from behave.task_execution.rejudge import rejudge_batch
from behave.task_execution.run_store import RunStore


def source_fixture(tmp_path, publication):
    original, report = initial_run(tmp_path / "fixture", publication)
    source = tmp_path / "measurement/rollout"
    source.mkdir(parents=True)
    # Real published runs own a directory, not an object-owner symlink.
    original.directory.rename(source / "run")
    original = RunStore(source / "run")
    (source / "inputs").mkdir()
    return source, original, report


def test_subset_scoring_preserves_identity_and_does_not_reexecute(tmp_path, publication, fixture_rollout):
    source, original, _ = source_fixture(tmp_path, publication)
    events = committed_cases(source)
    before = {p.name: p.read_bytes() for p in (original.directory / "report").glob("*") if p.is_file()}
    calls = list(fixture_rollout)
    output = source.parent / "scoring/stream"
    chunks = []
    for index, selected in enumerate((events[:2], events[2:])):
        chunk = publish_subset(source, tmp_path / f"chunk-{index}", selected)
        rejudge_batch(directory=chunk / "run", name="baseline", reader=publication.reader,
                      judge=Judge(unavailable=False), workers=2, all_submitted=True)
        chunks.append(chunk)
        consolidate(source, chunks, output)
        if index == 0:
            assert json.loads((source.parent / "scoring.json").read_text())["state"] == "scoring"
    marker = json.loads((source.parent / "scoring.json").read_text())
    assert marker["state"] == "completed" and marker["quality"]["requested"] == 3
    result_run = RunStore(original.directory / "judgments/stream", objects=original.objects)
    head = result_run.journal("report").load()[-1]
    from behave.core.artifacts import BlobRef
    result = load_batch_report(original.objects, BlobRef.of(head.to_bytes()))
    assert result.summary["scored"] == 3
    assert {row["run_key"] for row in result.summary["candidates"]} == {
        event.payload["row"]["run_key"] for event in events}
    consolidate(source, chunks, output)  # exact restart is idempotent
    assert fixture_rollout == calls
    assert before == {p.name: p.read_bytes() for p in (original.directory / "report").glob("*") if p.is_file()}


def test_publish_reads_committed_prefix_without_generator_owner(tmp_path, publication, fixture_rollout):
    _, original, _ = source_fixture(tmp_path, publication)
    events = original.journal("report").load()
    live = tmp_path / "live"
    (live / "inputs").mkdir(parents=True)
    store = RunStore(live / "run")
    prefix = events[:2]
    store.objects.publish_owned(original.objects, tuple(ref for e in prefix for ref in e.artifacts))
    journal = store.journal("report")
    history = []
    for e in prefix:
        history.append(journal.append(history, e.kind, thaw_json(e.payload), e.artifacts))
    with store.owner():
        chunk = publish_subset(live, tmp_path / "sealed", committed_cases(live))
    assert (chunk / "source.json").exists()
    assert len(store.journal("report").load()) == 2


def test_consolidation_publishes_each_reference_once_per_owner(
        tmp_path, publication, fixture_rollout, monkeypatch):
    from behave.core.storage import DirectoryStore

    source, original, _ = source_fixture(tmp_path, publication)
    events = committed_cases(source)
    chunks = []
    for index, selected in enumerate((events[:2], events[2:])):
        chunk = publish_subset(source, tmp_path / f"chunk-{index}", selected)
        rejudge_batch(directory=chunk / "run", name="baseline", reader=publication.reader,
                      judge=Judge(unavailable=False), workers=2, all_submitted=True)
        chunks.append(chunk)
    calls = []
    publish = DirectoryStore.publish_owned

    def counted(self, reader, refs, **options):
        if self.directory.resolve() == original.objects.directory.resolve():
            calls.append(refs)
        return publish(self, reader, refs, **options)

    monkeypatch.setattr(DirectoryStore, "publish_owned", counted)
    cache, output = set(), source.parent / "scoring/incremental"
    consolidate(source, chunks[:1], output, published=cache)
    assert len(calls) == 1  # Two case closures, one durable publication.
    first = list(calls)
    consolidate(source, chunks[:1], output, published=cache)
    assert calls == first
    consolidate(source, chunks, output, published=cache)
    refs = [ref for call in calls for ref in call]
    assert len(refs) == len(set(refs)) and set(refs) == cache
    expected = json.loads((output / "progress-0.json").read_text())
    # Restart has no cache. Existing durable objects remain valid, and the
    # canonical outcome and original candidate identities do not change.
    consolidate(source, chunks, output)
    assert json.loads((output / "progress-0.json").read_text()) == expected


def test_failed_consolidation_publication_does_not_advance_cache(
        tmp_path, publication, fixture_rollout, monkeypatch):
    from behave.core.storage import DirectoryStore

    source, _, _ = source_fixture(tmp_path, publication)
    chunk = publish_subset(source, tmp_path / "chunk", committed_cases(source)[:2])
    rejudge_batch(directory=chunk / "run", name="baseline", reader=publication.reader,
                  judge=Judge(unavailable=False), workers=2, all_submitted=True)
    cache = set()

    def fail(*args, **options):
        raise OSError("publication interrupted")

    monkeypatch.setattr(DirectoryStore, "publish_owned", fail)
    with pytest.raises(OSError, match="publication interrupted"):
        consolidate(source, [chunk], source.parent / "scoring/interrupted", published=cache)
    assert not cache
    assert not (source.parent / "scoring.json").exists()


def test_duplicate_cases_rejected_before_publication(tmp_path, publication, fixture_rollout):
    source, _, _ = source_fixture(tmp_path, publication)
    event = committed_cases(source)[0]
    target = tmp_path / "duplicate"
    with pytest.raises(ValueError, match="duplicate"):
        publish_subset(source, target, [event, event])
    assert not target.exists()


def test_import_preserves_scores_and_leaves_only_missing_cases(tmp_path, publication, fixture_rollout):
    source, _, _ = source_fixture(tmp_path, publication)
    events = committed_cases(source)
    old = publish_subset(source, tmp_path / 'old', events[:2])
    rejudge_batch(directory=old / 'run', name='baseline', reader=publication.reader,
                  judge=Judge(unavailable=False), workers=2, all_submitted=True)
    calls = list(fixture_rollout)
    target = tmp_path / 'imported'
    assert import_scores(source, [old], target) == 2
    assert fixture_rollout == calls
    assert json.loads((target / 'scored.json').read_text())['reused_committed_scores'] == 2
    used = set(json.loads((target / 'source.json').read_text())['keys'])
    remaining = [event for event in events if event.payload['row']['run_key'] not in used]
    assert len(remaining) == 1
    prior_rows = [e.payload['row'] for e in RunStore(old / 'run/judgments/baseline',
                  objects=RunStore(old / 'run').objects).journal('report').load()
                  if e.kind == 'evaluation_case']
    copied_rows = [e.payload['row'] for e in RunStore(target / 'run/judgments/baseline',
                   objects=RunStore(target / 'run').objects).journal('report').load()
                   if e.kind == 'evaluation_case']
    assert {row['run_key']: row for row in copied_rows} == {row['run_key']: row for row in prior_rows}
    consolidate(source, [target], tmp_path / 'scoring/imported')
    assert json.loads((source.parent / 'scoring.json').read_text())['quality']['requested'] == 2


def test_import_rejects_duplicate_scores_before_publication(tmp_path, publication, fixture_rollout):
    source, _, _ = source_fixture(tmp_path, publication)
    old = publish_subset(source, tmp_path / 'old', committed_cases(source)[:1])
    rejudge_batch(directory=old / 'run', name='baseline', reader=publication.reader,
                  judge=Judge(unavailable=False), workers=2, all_submitted=True)
    target = tmp_path / 'bad-import'
    with pytest.raises(ValueError, match='duplicate prior score'):
        import_scores(source, [old, old], target)
    assert not target.exists()


def test_scoring_backlog_waits_for_verified_inputs_and_reloads(tmp_path):
    live, old = tmp_path / "live", tmp_path / "old"
    config = {"archive_root":str(tmp_path), "measurements":[
        {"allocation":123,"directory":str(live)}]}
    ready = tmp_path / "ready.json"
    item = {"allocation":123,"key":"old","directory":str(old),"ready":str(ready)}
    assert scoring_sources(tmp_path, config, 123) == [live / "rollout"]
    write_json(tmp_path / "score_backlog.json", {
        "schema":"behave.eval-score-backlog/1","measurements":[item]})
    assert scoring_sources(tmp_path, config, 123) == [live / "rollout"]
    write_json(ready, {"key":"old","source":str(old / "rollout")})
    write_json(old / "generation.json", {"state":"completed"})
    assert scoring_sources(tmp_path, config, 123) == [live / "rollout",old / "rollout"]
    assert scoring_sources(tmp_path, config, 456) == []
    # The same source already in the ordinary fleet must still have one scorer.
    config["measurements"].append({"allocation":123,"directory":str(old)})
    assert scoring_sources(tmp_path, config, 123).count(old / "rollout") == 1


@pytest.mark.parametrize("fault", ["identity", "incomplete", "outside", "duplicate"])
def test_scoring_backlog_rejects_wrong_identity_or_owner(tmp_path, fault):
    old = tmp_path / "old"
    ready = tmp_path / "ready.json"
    item = {"allocation":123,"key":"old","directory":str(old),"ready":str(ready)}
    write_json(ready, {"key":"wrong" if fault == "identity" else "old",
                       "source":str(old / "rollout")})
    write_json(old / "generation.json", {"state":"interrupted" if fault == "incomplete" else "completed"})
    if fault == "outside":
        item["directory"] = str(tmp_path.parent / "outside")
    write_json(tmp_path / "score_backlog.json", {"schema":"behave.eval-score-backlog/1",
        "measurements":[item,item] if fault == "duplicate" else [item]})
    with pytest.raises(ValueError):
        scoring_sources(tmp_path, {"archive_root":str(tmp_path),"measurements":[]}, 123)


@pytest.mark.parametrize("failed_publication", [False, True])
@pytest.mark.parametrize("startup_option", [{}, {"isolation_startup_slots": None}, {"isolation_startup_slots": 4}])
@pytest.mark.parametrize("exit_mode", ["cancel", "exception"])
def test_stream_admits_sealed_subsets_immediately_with_shared_cap(
        tmp_path, monkeypatch, failed_publication, startup_option, exit_mode):
    import os
    import signal
    from contextlib import contextmanager
    from types import SimpleNamespace

    from behave.agent import cpu_resources
    from behave.core import process_limits
    from behave.eval import score_stream as stream
    from behave.eval import scoring as scoring_module
    from behave.isolation.startup import startup_slots

    config = {"nodes": {"123": {"scoring_cpu_ids": [0]}},
              "scoring_workers": 24, **startup_option,
              "score_chunk_size": 24, "score_name": "same-score"}
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "1")
    write_json(tmp_path / "fleet.json", config)
    sources = [tmp_path / f"measurement-{i}" / "rollout" for i in range(9)]
    existing = sources[0].parent / "scoring/same-score/chunks/00000"
    write_json(existing / "source.json", {"keys": ["existing"]})
    submitted, prepared, closed = [], [], []
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def submit(fn, chunk, name, **options):
        assert startup_slots() == startup_option.get("isolation_startup_slots")
        assert fn is scoring_module.score_saved and name == "baseline"
        assert options["workers"] == 24 and options["all_submitted"] is True
        assert options["retry_testbench"] is True
        assert options["judge_policy"] == tmp_path / "judge_policy.json"
        assert (chunk / "source.json").is_file()
        assert chunk not in submitted
        assert len(submitted) < 4
        submitted.append(chunk)
        return object()  # No completion is polled during this single-loop test.

    def publish(source, chunk, events):
        # Every previously sealed subset must be admitted before preparing
        # another source, including the resumed subset at the start.
        assert len(submitted) == 1 + len(prepared)
        if failed_publication and source == sources[1]:
            raise OSError("injected publication interruption")
        assert len(events) == 1
        write_json(chunk / "source.json", {"keys": [str(source)]})
        prepared.append(chunk)

    pool = SimpleNamespace(submit=submit)
    candidates = SimpleNamespace(activity=lambda: {"active": len(submitted)})

    @contextmanager
    def worker_scope(worker, name):
        try:
            yield worker
        finally:
            closed.append((name, startup_slots()))

    def stop(_):
        if exit_mode == "exception":
            raise RuntimeError("fixture coordinator failure")
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    monkeypatch.setattr(stream.os, "sched_setaffinity", lambda *a: None)
    monkeypatch.setattr(process_limits, "configure_worker_process_limit", dict)
    monkeypatch.setattr(cpu_resources, "CpuResources", lambda *a: SimpleNamespace(snapshot=dict))
    monkeypatch.setattr(stream, "ThreadPoolExecutor", lambda **kw: worker_scope(pool, "archives"))
    monkeypatch.setattr(scoring_module, "CandidateQueue", lambda *a: worker_scope(candidates, "candidates"))
    monkeypatch.setattr("behave.isolation.runtime_environment.runtime_environment", lambda: None)
    monkeypatch.setattr(stream, "scoring_sources", lambda *a: sources)
    monkeypatch.setattr(stream, "committed_cases", lambda source: [] if source == sources[0]
                        else [SimpleNamespace(payload={"row": {"run_key": str(source)}})])
    monkeypatch.setattr(stream, "publish_subset", publish)
    monkeypatch.setattr(stream, "consolidate", lambda *a, **kw: None)
    monkeypatch.setattr(stream.time, "sleep", stop)

    if exit_mode == "exception":
        with pytest.raises(RuntimeError, match="fixture coordinator failure"):
            stream.run(tmp_path, 123)
    else:
        stream.run(tmp_path, 123)
    restored = os.environ.get("BEHAVE_ISOLATION_STARTUP_SLOTS")
    assert restored == "1"
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    expected = startup_option.get("isolation_startup_slots")
    assert closed == [("archives", expected), ("candidates", expected)]
    status = json.loads((tmp_path / "scoring/123/progress.json").read_text())
    assert status["isolation_startup_slots"] == startup_option.get("isolation_startup_slots")
    assert len(submitted) == 4 and submitted == [existing, *prepared]
    assert len(prepared) == 3  # No fifth input is prepared above the shared cap.
    assert status["active_chunks"] == [str(p) for p in submitted]
    assert len(status["blocked"]) == int(failed_publication)
    if failed_publication:
        failed = sources[1].parent / "scoring/same-score/chunks/00000"
        assert (failed / "failure.json").is_file() and failed not in submitted
