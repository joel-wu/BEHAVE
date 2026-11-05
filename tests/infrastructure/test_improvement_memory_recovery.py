"""Memory interruption is recoverable without repeating successful Analysts."""

from types import SimpleNamespace

import pytest

from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.records import RecordError, canonical_json
from behave.improve import memory_recovery
from behave.improve.concurrent import run_stage_tasks
from behave.improve.round_evidence import _stage_order_matches


def interrupted(directory, config):
    run = JournalDirectory(directory)
    ref = run.objects.put(config.to_bytes())
    tape = Tape(run.journal("acquisition"))
    tape.record("acquisition_started", {"configuration": ref.as_dict()}, (ref,))
    tape.record("acquisition_requested", {})
    tape.record("acquisition_finished", {"status": "interrupted"})
    return run


def test_memory_recovery_keeps_old_attempt_and_checks_cleanup(tmp_path, monkeypatch):
    config = SimpleNamespace(to_bytes=lambda: b"fixed config")
    root = tmp_path / "experience"
    first = interrupted(root, config)
    second = interrupted(root / "retries/001", config)
    before = [run.journal("acquisition").load() for run in (first, second)]
    checked = []
    monkeypatch.setattr(memory_recovery, "seal_process", lambda run, *_: checked.append(run.directory))
    selected = memory_recovery.memory_child(root, config)
    assert selected.directory == root / "retries/002"
    assert checked == [first.directory, second.directory]
    assert [run.journal("acquisition").load() for run in (first, second)] == before
    changed = SimpleNamespace(to_bytes=lambda: b"different inputs")
    with pytest.raises(RecordError, match="different inputs"):
        memory_recovery.memory_child(root, changed)
    def unknown(*args):
        raise RuntimeError("cleanup unknown")
    monkeypatch.setattr(memory_recovery, "seal_process", unknown)
    with pytest.raises(RuntimeError, match="cleanup unknown"):
        memory_recovery.memory_child(root, config)


def test_interrupted_memory_does_not_freeze_parent_but_builder_is_retained(tmp_path):
    run = JournalDirectory(tmp_path / "round")
    tape = Tape(run.journal("round"))
    calls = []
    failed = True
    def work(name, stage):
        child = JournalDirectory(tmp_path / name / str(failed))
        def execute():
            calls.append((name, failed))
            child.journal("child").append([], "outcome", {})
            return {"status": "interrupted" if name == "experience" and failed else "completed"}
        return stage(name, execute, child, "child")
    settings = dict(objects=run.objects, workers=2,
        commit_stage=lambda row: row["outcome"]["status"] != "interrupted")
    rows = run_stage_tasks(["experience", "construction:a"], work, tape=tape, **settings)
    assert len(rows) == 2
    assert [e.payload["name"] for e in tape.events] == ["construction:a"]
    failed = False
    resumed = Tape(run.journal("round"))
    run_stage_tasks(["experience", "construction:a"], work, tape=resumed, **settings)
    resumed.finish()
    assert sorted(calls) == [("construction:a", True), ("experience", False), ("experience", True)]
    assert [e.payload["name"] for e in resumed.events] == ["construction:a", "experience"]


def test_memory_retry_preserves_legacy_prefix_and_allows_parallel_construction():
    for middle in (["experience:retry", "construction:a"], ["construction:a", "experience:retry"]):
        actual = ["acquisition:0", "experience", *middle, "data"]
        assert _stage_order_matches(actual, [], ["acquisition:0"],
            ["experience", "experience:retry"], ["construction:a"], ["data"], parallel_memory=True)
    assert not _stage_order_matches(["acquisition:0", "construction:a", "experience", "data"],
        [], ["acquisition:0"], ["experience"], ["construction:a"], ["data"])


def test_fence_requires_actual_native_owner_cleanup_and_exact_journal_heads(tmp_path):
    owner, study = tmp_path / "job", tmp_path / "study"
    root = study / "acquisitions/update_18/anchor"
    directory = root / "round/batches/batch_000/experience"
    child = JournalDirectory(directory)
    config = SimpleNamespace(to_bytes=lambda: b"fixed merge")
    ref = child.objects.put(config.to_bytes())
    child.journal("acquisition").append([], "acquisition_started", {"configuration": ref.as_dict()}, (ref,))
    owner.mkdir()
    for path, value in ((owner / "configuration.json", {"self_improvement": {"study": str(study / "study.json")}}),
        (owner / "node_status.json", {"state": "stopped"}),
        (owner / "runtime.json", {"state": "interrupted", "local_directory": "/tmp/fixture-native", "phase": "initial"}),
        (root / "request.json", {}), (root / "connection.json", {"control_package": "/tmp/fixture-native/installed"})):
        path.write_bytes(canonical_json(value))
    learning = JournalDirectory(owner / "artifacts/plan/learning/initial")
    with pytest.raises(ValueError, match="cleanup is not confirmed"):
        memory_recovery.fence_memory(directory, owner)
    learning.journal("learning").append([], "learning_process_settled", {"cleanup": "complete"})
    record = memory_recovery.fence_memory(directory, owner)
    assert record == memory_recovery.fence_memory(directory, owner)
    assert memory_recovery.memory_child(directory, config).directory == directory / "retries/001"
    child.journal("revisions", "r0", "source_acquirer").append([], "late_write", {})
    with pytest.raises(RecordError, match="memory changed"):
        memory_recovery.memory_child(directory, config)


@pytest.mark.parametrize("suffix", ["batches/batch1", "batches/0000/anything", "scratch/0001", "retries/not-an-index"])
def test_fencing_rejects_non_memory_children(tmp_path, suffix):
    study = tmp_path / "study/study.json"
    directory = study.parent / "acquisitions/update18/anchor/round/experience" / suffix
    with pytest.raises(ValueError, match="not a memory batch"):
        memory_recovery._memory_target(directory, study)
