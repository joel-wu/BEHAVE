"""Retention moves named old saves after commit and leaves the latest restorable."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_native_updates import save_fixture
from test_slime_application import configured
from test_slime_training_plan import inputs

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.train.checkpoint_storage import retain_checkpoints, retention_policy
from behave.train.slime_checkpoint import checkpoint_due
from behave.train.slime_data import ScoredDataSource


def test_latest_save_is_committed_before_old_state_moves_and_cold_load_works(tmp_path, monkeypatch):
    source = configured(tmp_path)
    policy = {"keep_full": 1, "archive_root": str(tmp_path / "pending_cleanup")}
    source.path.write_bytes(canonical_json({**source.binding, "checkpoint_retention": policy}))
    source.args.behave_rollout_binding_sha256 = BlobRef.of(source.path.read_bytes()).sha256
    source = ScoredDataSource(source.args)
    save_fixture(source, 0)
    root = Path(source.args.save)
    stray = root / "iter_0000999"
    stray.mkdir()
    (stray / "uncommitted.pt").write_bytes(b"keep uncommitted state")
    hf = root / "hf_0"
    hf.mkdir()
    (hf / "model.safetensors").write_bytes(b"keep HF")
    read = Path.read_bytes
    def no_state_reads(path):
        assert path.suffix != ".pt", "retention reread a tensor file"
        return read(path)
    monkeypatch.setattr(Path, "read_bytes", no_state_reads)
    save_fixture(source, 1)
    assert source.journal.load()[-1].kind == "checkpoint_saved"
    assert not (root / "iter_0000000").exists()
    assert (root / "iter_0000001/state.pt").is_file()
    assert stray.is_dir() and hf.is_dir()
    receipt, = (tmp_path / "pending_cleanup").rglob("moves.json")
    record = json.loads(receipt.read_bytes())
    assert record["kept_steps"] == [1]
    move, = record["moves"]
    assert Path(move["destination"]).as_posix().endswith(Path(move["source"]).as_posix())
    assert (Path(move["destination"]) / "state.pt").is_file()
    ScoredDataSource(source.args).load(1)
    # Subsequent retention is idempotent; no empty archives or receipt overwrite.
    assert retain_checkpoints(root, source.journal.load(), policy) is None
    assert list((tmp_path / "pending_cleanup").rglob("moves.json")) == [receipt]


def test_failed_new_save_never_moves_previous_recovery_point(tmp_path):
    source = configured(tmp_path)
    source.native.retention = {"keep_full": 1, "archive_root": str(tmp_path / "pending_cleanup")}
    save_fixture(source, 0)
    source.journal.append(source.journal.load(), "round_scored", {"round": 1, "binding": source.identity.as_dict()})
    with pytest.raises(ValueError, match="not completed"):
        source.save(1)
    assert (Path(source.args.save) / "iter_0000000/state.pt").is_file()
    assert not (tmp_path / "pending_cleanup").exists()


def test_critic_moves_with_actor_but_other_run_is_untouched(tmp_path):
    root = tmp_path / "checkpoints"
    for step in (4, 9):
        for base in (root, root / "critic"):
            directory = base / f"iter_{step:07d}"
            directory.mkdir(parents=True)
            (directory / "state.pt").write_bytes(b"fixture state")
    events = [SimpleNamespace(kind="checkpoint_saved", payload={"round": step, "native_directory": str(root)})
              for step in (4, 9)]
    events.insert(0, SimpleNamespace(kind="checkpoint_saved", payload={"round": 0, "native_directory": str(tmp_path / "other")}))
    receipt = retain_checkpoints(root, events,
        {"keep_full": 1, "archive_root": str(tmp_path / "pending_cleanup")}, critic=True)
    assert len(json.loads(receipt.read_bytes())["moves"]) == 2
    assert (root / "iter_0000009/state.pt").is_file()
    assert (root / "critic/iter_0000009/state.pt").is_file()


@pytest.mark.parametrize("count", [0, -1, True])
def test_retention_never_removes_all_recovery_points(count):
    with pytest.raises(ValueError, match="at least one"):
        retention_policy({"keep_full": count, "archive_root": "/archive"})


def test_native_five_update_cadence_saves_final_partial_interval():
    args = SimpleNamespace(save_interval=5, num_rollout=12)
    assert [i for i in range(12) if checkpoint_due(args, i)] == [4, 9, 11]


def test_task_and_raw_plans_propagate_the_same_retention_policy(tmp_path, monkeypatch, inputs):
    from test_slime_raw_plan import raw_inputs

    from behave.train.slime_data import prepare_raw_plan
    from behave.train.slime_data import read_raw_binding as read_raw
    from behave.train.slime_launch import native_arguments
    from behave.train.slime_rollout import read_binding as read_task
    from tools.slime_training_plan import prepare

    config, release, split, task_id = inputs
    policy = {"keep_full": 1, "archive_root": str(tmp_path / "pending_cleanup")}
    config = {**config, "checkpoint_retention": policy, "checkpoint_interval": 5}
    prepare(tmp_path / "task", config, release, [task_id], split)
    raw, store, ref, _ = raw_inputs(tmp_path, monkeypatch, inputs)
    prepare_raw_plan(tmp_path / "raw", {**raw, "checkpoint_retention": policy, "checkpoint_interval": 5}, store.directory, ref)
    assert read_task(tmp_path / "task/binding.json")["checkpoint_retention"] == policy
    assert read_raw(tmp_path / "raw/binding.json")["checkpoint_retention"] == policy
    for name in ("task", "raw"):
        argv = native_arguments(tmp_path / name, updates=12, load=tmp_path / "initial", cpus=8,
                                checkpoint_directory=tmp_path / name / "checkpoints")
        assert argv[argv.index("--save-interval") + 1] == "5"
        assert "--save-hf" in argv
