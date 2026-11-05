"""Storage-only migration: opaque fixture shards, no GPUs or optimizer claims."""

import json
from pathlib import Path

import pytest
from test_slime_rollout_handoff import partial_run
from test_slime_training_job import job  # noqa: F401
from test_slime_training_plan import inputs  # noqa: F401

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.train.checkpoint_storage import (
    install_storage,
    job_checkpoint_directory,
    location_record,
    resolve_checkpoint,
    storage_destination,
)
from behave.train.slime_checkpoint import checkpoint_inventory


def relocate(source, target, step, checkpoint=None):
    files = checkpoint_inventory(source, step)
    source.rename(target)
    row = {"schema": "behave.checkpoint-location/1", "source": str(source),
           "destination": str(target), "step": step, "files": [f.as_dict() for f in files],
           "checkpoint": (checkpoint or BlobRef.of(b"fixture receipt")).as_dict()}
    location_record(source).write_bytes(canonical_json(row))
    return row, files


@pytest.fixture
def relocated(tmp_path):
    source = tmp_path / "legacy"
    (source / "iter_0000002").mkdir(parents=True)
    (source / "iter_0000002/state.pt").write_bytes(b"optimizer rng and model fixture")
    (source / "latest_checkpointed_iteration.txt").write_text("2")
    target = tmp_path / "archive"
    row, files = relocate(source, target, 2)
    return source, target, row, files


def test_explicit_relocation_survives_absent_original(relocated):
    source, target, row, files = relocated
    assert not source.exists()
    assert resolve_checkpoint(source, step=2, files=files) == target
    assert checkpoint_inventory(target, 2) == files
    with pytest.raises(ValueError, match="identity differs"):
        resolve_checkpoint(source, checkpoint=BlobRef.of(b"different checkpoint"))
    with pytest.raises(ValueError, match="step differs"):
        resolve_checkpoint(source, step=3)


@pytest.mark.parametrize("change", ["source", "schema", "step", "size", "missing", "symlink", "chain"])
def test_bad_location_or_target_fails_closed(relocated, change):
    source, target, row, files = relocated
    if change in ("source", "schema"):
        row[change] = "wrong"
    elif change == "step":
        row["step"] = True
    elif change == "size":
        (target / "iter_0000002/state.pt").write_bytes(b"wrong")
    elif change == "missing":
        target.rename(target.with_name("unavailable"))
    elif change == "symlink":
        other = target.with_name("real-archive")
        target.rename(other)
        target.symlink_to(other, target_is_directory=True)
    else:
        location_record(target).write_text("{}")
    location_record(source).write_bytes(canonical_json(row))
    with pytest.raises((ValueError, FileNotFoundError)):
        resolve_checkpoint(source)


def test_storage_profile_is_scoped_and_collision_free(tmp_path):
    repo, store = tmp_path / "repo", tmp_path / "store"
    profile = repo / "infra/train/checkpoint_storage.json"
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({"schema": "behave.checkpoint-storage-profile/1",
        "run_root": str(repo / "work"), "checkpoint_root": str(store)}))
    assert storage_destination(repo, repo / "work/exp-a/run01") == store / "exp-a/run01/native"
    assert storage_destination(repo, repo / "work/exp-b/run01") == store / "exp-b/run01/native"
    assert storage_destination(repo, tmp_path / "external-job") is None
    with pytest.raises(ValueError, match="absolute"):
        storage_destination(repo, tmp_path / "external-job", Path("relative"))


def test_migrated_work_profile_accepts_both_names_not_weight_aliases(tmp_path):
    repo, actual, weights = tmp_path / 'repo', tmp_path / 'archive/work', tmp_path / 'weights'
    profile = repo / 'infra/train/checkpoint_storage.json'
    profile.parent.mkdir(parents=True)
    actual.mkdir(parents=True)
    (repo / 'work').symlink_to(actual, target_is_directory=True)
    row = {'schema': 'behave.checkpoint-storage-profile/1',
           'run_root': str(repo / 'work'), 'checkpoint_root': str(weights)}
    profile.write_text(json.dumps(row))
    for run in (repo / 'work/exp/run01', actual / 'exp/run01'):
        assert storage_destination(repo, run) == weights / 'exp/run01/native'
    with pytest.raises(ValueError, match='run tree itself'):
        storage_destination(repo, actual)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (actual / 'external').symlink_to(outside, target_is_directory=True)
    assert storage_destination(repo, repo / 'work/external/run01') is None
    weights.symlink_to(tmp_path / 'actual-weights', target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        storage_destination(repo, actual / 'exp/run01')


def test_new_save_root_cannot_silently_remap_to_hot_storage(tmp_path, monkeypatch):
    repo, cold, hot = tmp_path / 'repo', tmp_path / 'cold', tmp_path / 'hot'
    profile = repo / 'infra/train/checkpoint_storage.json'
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({'schema': 'behave.checkpoint-storage-profile/1',
        'run_root': str(repo / 'work'), 'checkpoint_root': str(cold / 'direct-runs')}))
    locations = tmp_path / 'locations.json'
    mapping = [{'source': str(cold), 'destination': str(hot)}]
    locations.write_text(json.dumps({'schema': 'behave.runtime-locations/1',
        'mappings': mapping, 'deny_unmapped_roots': [str(cold)]}))
    monkeypatch.setenv('BEHAVE_RUNTIME_LOCATIONS', str(locations))
    with pytest.raises(ValueError, match='remaps the configured checkpoint save root'):
        storage_destination(repo, repo / 'work/experiment/run01')
    direct = tmp_path / 'locations-direct.json'
    direct.write_text(json.dumps({'schema': 'behave.runtime-locations/2',
        'mappings': mapping, 'deny_unmapped_roots': [str(cold)],
        'direct_roots': [str(cold / 'direct-runs')]}))
    monkeypatch.setenv('BEHAVE_RUNTIME_LOCATIONS', str(direct))
    assert storage_destination(repo, repo / 'work/experiment/run01') == (
        cold / 'direct-runs/experiment/run01/native')


def test_saved_storage_owner_survives_run_root_migration(tmp_path):
    old = tmp_path / 'work'
    run = old / 'experiment/run01'
    run.mkdir(parents=True)
    native = tmp_path / 'weights/native'
    install_storage(run, native)
    before = (run / 'checkpoint_storage.json').read_bytes()
    actual = tmp_path / 'archive-work'
    old.rename(actual)
    old.symlink_to(actual, target_is_directory=True)
    assert job_checkpoint_directory(run) == native
    assert job_checkpoint_directory(run.resolve()) == native
    assert (run / 'checkpoint_storage.json').read_bytes() == before
    other = actual / 'experiment/run02'
    other.mkdir()
    (other / 'checkpoint_storage.json').write_bytes(before)
    with pytest.raises(ValueError, match='another run'):
        job_checkpoint_directory(other)


def test_relocation_source_record_survives_parent_alias(tmp_path):
    old = tmp_path / 'work'
    source = old / 'run/artifacts/checkpoint'
    (source / 'iter_0000002').mkdir(parents=True)
    (source / 'iter_0000002/state.pt').write_bytes(b'fixture')
    (source / 'latest_checkpointed_iteration.txt').write_text('2')
    target = tmp_path / 'weights'
    _, files = relocate(source, target, 2)
    actual = tmp_path / 'archive-work'
    old.rename(actual)
    old.symlink_to(actual, target_is_directory=True)
    assert resolve_checkpoint(source, step=2, files=files) == target
    assert resolve_checkpoint(source.resolve(), step=2, files=files) == target


def test_job_save_and_handoff_use_external_directory(job, tmp_path):
    from tools.slime_checkpoint_input import prepare
    from tools.slime_training_job import execute

    output, local, calls = job
    config = (output / "configuration.json").read_bytes()
    target = tmp_path / "external/native"
    install_storage(output, target)
    assert execute(str(output), str(local)) == 0
    assert not (output / "artifacts/checkpoint").exists()
    assert (target / "iter_0000002/state.pt").is_file()
    assert (output / "configuration.json").read_bytes() == config
    for argv, _ in calls:
        if "--save" in argv:
            assert argv[argv.index("--save") + 1] == str(target)
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (output / "artifacts/final_hf" / name).write_text("{}")
    assert prepare(output)["checkpoint_directory"] == str(target)


def test_archived_job_handoff_and_resume_preserve_checkpoint_identity(job, tmp_path, monkeypatch):
    from tools.slime_checkpoint_input import prepare
    from tools.slime_pause_after_checkpoint import verify_checkpoint
    from tools.slime_training_job import execute, submit

    output, local, _ = job
    assert execute(str(output), str(local)) == 0
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (output / "artifacts/final_hf" / name).write_text("{}")
    before = prepare(output)
    target = tmp_path / "archive"
    relocate(output / "artifacts/checkpoint", target, 2, BlobRef.from_dict(before["checkpoint"]))
    after = prepare(output)
    assert after == {**before, "checkpoint_directory": str(target)}
    assert verify_checkpoint(output, 3).is_file()
    selection = json.loads((output / "selection.json").read_text())
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **k: {})
    calls = []
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **k: calls.append((a, k)))
    resumed = tmp_path / "resume"
    submit(resumed, 1, output / "configuration.json", Path(selection["release"]), selection["tasks"],
           output / "train.txt", cpus=8, resume_from=output, updates=5)
    continuation = json.loads((resumed / "continuation.json").read_text())
    assert continuation["load"] == str(target)
    assert continuation["checkpoint"] == before["checkpoint"]
    assert continuation["first_update"] == 3 and len(calls) == 1


def test_partial_rollout_recovery_survives_migration(tmp_path, inputs):
    from behave.train.slime_rollout import inspect_handoff

    root, config, release, split, task, _, _, _ = partial_run(tmp_path, inputs, saved=True)
    before = inspect_handoff(root, config, release, [task], workers_stopped=True)
    target = tmp_path / "archive"
    relocate(root / "artifacts/checkpoint", target, 0, before["parent"])
    after = inspect_handoff(root, config, release, [task], workers_stopped=True)
    assert after["load"] == str(target)
    assert after["parent"] == before["parent"] and after["step"] == before["step"]
    assert after["policy_ref"] == before["policy_ref"]
    assert after["recovery"].indices == before["recovery"].indices
