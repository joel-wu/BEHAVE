import json

import pytest

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.eval.scoring import StagedObjects, select_indices, source_shards, stage_selected_journals
from behave.task_execution.run_store import judgment_directory


@pytest.mark.parametrize("name", ["baseline", "baseline-workers16", "retry.2"])
def test_judgment_directory_is_a_pure_path_lookup(tmp_path, name):
    root = tmp_path / "absent"
    assert judgment_directory(root, name) == root / "judgments" / name
    assert not root.exists()


@pytest.mark.parametrize("name", [None, True, 1, [], {}, "bad\0name", "../outside", "/outside"])
def test_judgment_directory_rejects_invalid_names_without_writing(tmp_path, name):
    with pytest.raises(ValueError, match="directory name"):
        judgment_directory(tmp_path / "absent", name)
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("field", ["name", "previous", "selected"])
@pytest.mark.parametrize("invalid", ["../outside", "", "bad\0name"])
def test_staging_validates_every_named_pass_before_any_copy(tmp_path, monkeypatch, field, invalid):
    from behave.eval import scoring

    def forbidden(*args, **kwargs):
        pytest.fail("invalid judgment reached a journal copy")

    monkeypatch.setattr(scoring.shutil, "copytree", forbidden)
    source = tmp_path / "source"
    (source / "report").mkdir(parents=True)
    selection = {"one": {"judgment": invalid if field == "selected" else "earlier"}}
    with pytest.raises(ValueError, match="directory name"):
        stage_selected_journals(source, tmp_path / "destination", selection,
            name=invalid if field == "name" else "new", previous=invalid if field == "previous" else "old")
    assert not (tmp_path / "destination").exists()


def indexed_row(tmp_path, key, *, dataset="wanted", submission=True):
    return {"source_run": str(tmp_path / "source"), "run_key": key, "archive_state": "archived", "task": "task",
            "attempt_id": key, "setting": "joint", "release": BlobRef.of(b"release").as_dict(),
            "revision": BlobRef.of(b"revision").as_dict(), "result": BlobRef.of(b"result").as_dict(),
            "submission": BlobRef.of(b"submission").as_dict() if submission else None,
            "judgment": "on_submit", "dataset": dataset, "model": "model", "sample": 0, "trace": "trace.md",
            "rollout_status": "submitted" if submission else "no_submission"}


def test_index_selection_is_exact_and_does_not_drop_unsubmitted(tmp_path):
    path = tmp_path / "index.json"
    rows = [indexed_row(tmp_path, "a"), indexed_row(tmp_path, "b", submission=False),
            indexed_row(tmp_path, "outside", dataset="other")]
    path.write_text(json.dumps(rows))
    selected = select_indices([path], datasets=["wanted"])
    assert set(selected[str(tmp_path / "source")]) == {"a", "b"}
    with pytest.raises(ValueError, match="duplicate"):
        select_indices([path, path], datasets=["wanted"])
    with pytest.raises(ValueError, match="no indexed"):
        select_indices([path], datasets=["absent"])


def test_source_shards_are_disjoint_and_complete():
    selected = {source: {str(i): {"setting": "joint", "submission": {}}
                        for i in range(count)} for source, count in (("a", 10), ("b", 6), ("c", 4))}
    assert source_shards(list(selected), selected, 2) == [["a"], ["b", "c"]]


def test_staging_reads_only_demanded_objects_and_prefers_local(tmp_path):
    archive, local = DirectoryStore(tmp_path / "archive"), DirectoryStore(tmp_path / "preferred")
    needed = archive.put(b"needed")
    local.put(b"needed")
    unused = archive.put(b"unrelated raw model response")
    store = StagedObjects(tmp_path / "staged", archive.directory, preferred=local.directory)
    store.authority.read = lambda _: pytest.fail("existing node-local blob should be reused")
    assert store.read(needed) == store.read(needed) == b"needed"
    assert store.fetched == {needed.sha256}
    produced = store.put(b"new score")
    assert produced.sha256 not in store.fetched
    assert not (store.directory / unused.sha256).exists()


def test_staging_corruption_is_not_silently_replaced(tmp_path):
    archive = DirectoryStore(tmp_path / "archive")
    ref = archive.put(b"data")
    preferred = tmp_path / "preferred"
    preferred.mkdir()
    (preferred / ref.sha256).write_bytes(b"nope")
    store = StagedObjects(tmp_path / "staged", archive.directory, preferred=preferred)
    with pytest.raises(RecordError, match="digest"):
        store.read(ref)
    assert not (store.directory / ref.sha256).exists()


@pytest.mark.parametrize("linked_root", ["authority", "preferred"])
def test_staging_resolves_shared_archive_roots_once(tmp_path, linked_root):
    archive = DirectoryStore(tmp_path / "archive")
    ref = archive.put(b"shared release content")
    alias = tmp_path / "shared-inputs"
    alias.symlink_to(archive.directory, target_is_directory=True)
    store = StagedObjects(tmp_path / "staged",
                          alias if linked_root == "authority" else archive.directory,
                          preferred=alias if linked_root == "preferred" else None)
    # Streaming subsets deliberately share an inputs directory by symlink.
    # Resolve that trusted root once, not each time a blob is demanded.
    reader = getattr(store, linked_root)
    assert reader.directory == archive.directory
    alias.unlink()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    alias.symlink_to(replacement, target_is_directory=True)
    assert store.read(ref) == b"shared release content"
    assert store.fetched == {ref.sha256}


def test_staging_still_rejects_symlinked_content_files(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    payload = tmp_path / "outside"
    payload.write_bytes(b"data")
    ref = BlobRef.of(b"data")
    (archive / ref.sha256).symlink_to(payload)
    alias = tmp_path / "shared-inputs"
    alias.symlink_to(archive, target_is_directory=True)
    store = StagedObjects(tmp_path / "staged", alias)
    with pytest.raises(RecordError, match="cannot read content"):
        store.read(ref)
    assert not store.fetched


def test_staging_preserves_selected_cleanup_receipts_and_orphans(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    for name in ("report/000000.json", "candidates/a/supervision/000000.json",
                 "candidates/a/solver/000000.json", "candidates/a/judge/000000.json",
                 "candidates/b/solver/000000.json", "candidates/outside/judge/000000.json",
                 "judgments/earlier/candidates/a/judge/000000.json"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("preserved")
    stage_selected_journals(source, destination, {"a": {"judgment": "earlier"},
        "b": {"judgment": "on_submit"}}, name="new")
    assert (destination / "candidates/a/judge/000000.json").exists()
    assert (destination / "judgments/earlier/candidates/a/judge/000000.json").exists()
    assert (destination / "candidates/b/solver/000000.json").exists()
    assert not (destination / "candidates/a/solver").exists()
    assert not (destination / "candidates/outside").exists()


def test_score_snapshot_retains_legacy_checkpoint_command():
    from tools.infra_validation_job import FILES

    # The shared helpers live in src/behave; retain the standalone operator CLI.
    assert "tools/eval_score_checkpoint.py" in FILES


def test_indexed_plan_pins_current_baseline_policy_and_shards(tmp_path, monkeypatch):
    from behave.sim import api
    from tools.eval_score_job import prepare

    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **kw: None)
    selected = {str(tmp_path / "source"): {"a": indexed_row(tmp_path, "a")}}
    plan = prepare(tmp_path / "pass", workers=4, all_submitted=True, selection=selected,
                   shards=2, retry_testbench=False)
    assert plan["policy"]["baseline_cases"] == api.DEFAULT_BASELINE_CASES == 50
    assert plan["policy"]["sequence_length"] == api.DEFAULT_SEQUENCE_LENGTH == 100
    assert plan["policy"]["score_timeout_seconds"] == 1200
    assert plan["policy"]["search_seconds"] == 0 and plan["policy"]["analysis"] is None
    assert plan["selection"] == selected and plan["retry_testbench"] is False
    assert len(plan["shards"]) == 2


def test_search_rejudge_changes_only_explicit_search_budget(tmp_path, monkeypatch):
    from tools.eval_score_job import prepare

    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **kw: None)
    selected = {str(tmp_path / "source"): {"a": indexed_row(tmp_path, "a")}}
    baseline = prepare(tmp_path / "baseline", workers=4, selection=selected,
                       all_submitted=True, retry_testbench=False)
    search = prepare(tmp_path / "search", workers=4, selection=selected,
                     all_submitted=True, retry_testbench=False, search_seconds=600)
    assert search["policy"] == {**baseline["policy"], "search_seconds": 600}
    assert search["selection"] == baseline["selection"]
    assert search["policy"]["analysis"] is None
    with pytest.raises(ValueError, match="different pass"):
        prepare(tmp_path / "search", workers=4, selection=selected,
                all_submitted=True, retry_testbench=False, search_seconds=900)


@pytest.mark.parametrize("budget", [-1, float("nan"), float("inf"), True])
def test_search_rejudge_uses_public_policy_budget_validation(tmp_path, budget):
    from tools.eval_score_job import prepare

    with pytest.raises(ValueError, match="search_seconds"):
        prepare(tmp_path / "invalid", [tmp_path / "source"], workers=1,
                search_seconds=budget)
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("limit", [None, -1, 4096])
def test_score_launch_records_host_limit_without_changing_worker_policy(tmp_path, monkeypatch, limit):
    from tools.eval_score_job import launch

    (tmp_path / "plan.json").write_text(json.dumps({"shards": [["source"]]}))
    calls = []
    def submit(directory, argv, **options):
        calls.append((argv, options))
        return {"state": "submitted"}
    monkeypatch.setattr("behave.runtime.jobs.submit", submit)
    assert launch(tmp_path, 123, 100, process_limit=limit)["state"] == "submitted"
    argv, options = calls[0]
    assert "--cpus-per-task=100" in argv
    assert "--propagate=NONE" in argv
    assert options["metadata"]["process_limit"] == limit
    bootstrap = argv[argv.index("-c") + 1]
    compile(bootstrap, "score-launch", "exec")
    selected = None if limit == -1 else limit
    assert f"process_limit={selected!r}" in bootstrap
    assert "setrlimit" not in bootstrap  # Shared implementation in the node worker.


def test_score_accepts_durable_rollout_without_local_directory(tmp_path, monkeypatch):
    from tools.eval_review_trial import _score_local

    output = tmp_path / "rollout"
    output.mkdir()
    (output / "runtime.json").write_text(json.dumps({
        "state": "rollout-complete-awaiting-eval", "host": "compute-node",
        "durable_directory": str(output)}))

    class ReachedStaging(Exception):
        pass

    def stop_at_staging(*args, **kwargs):
        raise ReachedStaging

    monkeypatch.setattr("behave.task_execution.rejudge.judgment_run", stop_at_staging)
    with pytest.raises(ReachedStaging):
        _score_local(output, "baseline", workers=1, previous=None, judge_policy=None,
                     cpu_pool=None, stage_from=None)
