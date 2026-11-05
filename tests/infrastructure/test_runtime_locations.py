import json
from pathlib import Path

import pytest
from test_slime_training_plan import inputs

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import DirectoryReader
from behave.core.runtime_locations import profile_identity, runtime_path
from behave.core.storage import DirectoryStore
from behave.train.checkpoint_storage import job_checkpoint_directory, storage_destination


def profile(tmp_path, monkeypatch, mappings=None):
    old, hot = tmp_path/'cold', tmp_path/'hot'
    hot.mkdir(exist_ok=True)
    file = tmp_path/'locations.json'
    file.write_text(json.dumps({'schema':'behave.runtime-locations/1',
        'mappings':mappings or [{'source':str(old),'destination':str(hot)}],
        'deny_unmapped_roots':['/store','/legacy']}))
    monkeypatch.setenv('BEHAVE_RUNTIME_LOCATIONS',str(file))
    return old,hot


def test_opt_in_only(tmp_path, monkeypatch):
    monkeypatch.delenv('BEHAVE_RUNTIME_LOCATIONS',raising=False)
    assert runtime_path(tmp_path/'unknown') == tmp_path/'unknown'
    assert profile_identity() is None


def test_readers_and_journals_follow_hot_without_touching_cold(tmp_path, monkeypatch):
    old,hot=profile(tmp_path,monkeypatch)
    ref=DirectoryStore(hot/'objects').put(b'unchanged evidence')
    assert DirectoryReader(old/'objects').read(ref)==b'unchanged evidence'
    assert JournalDirectory(old/'run').directory==hot/'run'
    assert not old.exists()


def test_missing_hot_does_not_fall_back(tmp_path, monkeypatch):
    old,hot=profile(tmp_path,monkeypatch)
    old.mkdir()
    (old/'only-in-cold').write_text('must not read')
    assert runtime_path(old/'only-in-cold')==hot/'only-in-cold'
    assert not runtime_path(old/'only-in-cold').exists()
    with pytest.raises(ValueError,match='unmapped'):
        runtime_path('/store/other/run')


def test_reject_traversal_and_symlink(tmp_path,monkeypatch):
    old,hot=profile(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='traversal'):
        runtime_path(old/'../escape')
    (hot/'escape').symlink_to(tmp_path)
    with pytest.raises(ValueError,match='symlink'):
        runtime_path(old/'escape/file')


def test_unchanged_storage_owner_accepts_relocated_job(tmp_path,monkeypatch):
    old,hot=profile(tmp_path,monkeypatch)
    job,native=hot/'job',hot/'native'
    job.mkdir();native.mkdir()
    row={'schema':'behave.checkpoint-storage/1','job':str(old/'job'),'directory':str(old/'native')}
    data=json.dumps(row)
    (job/'checkpoint_storage.json').write_text(data)
    (native/'storage_owner.json').write_text(data)
    assert job_checkpoint_directory(job)==native
    assert (job/'checkpoint_storage.json').read_text()==data


def test_profile_places_new_saves_on_nfs(tmp_path,monkeypatch):
    monkeypatch.delenv('BEHAVE_RUNTIME_LOCATIONS',raising=False)
    repo=tmp_path/'repo';profile_file=repo/'infra/train/checkpoint_storage.json'
    profile_file.parent.mkdir(parents=True)
    profile_file.write_text(json.dumps({'schema':'behave.checkpoint-storage-profile/1',
        'run_root':str(tmp_path/'hot/work'),'checkpoint_root':str(tmp_path/'hot/weights')}))
    assert storage_destination(repo,tmp_path/'hot/work/family/run02')==tmp_path/'hot/weights/family/run02/native'


def test_reject_chained_maps(tmp_path,monkeypatch):
    profile(tmp_path,monkeypatch,[{'source':str(tmp_path/'a'),'destination':str(tmp_path/'b')},
        {'source':str(tmp_path/'b'),'destination':str(tmp_path/'c')}])
    with pytest.raises(ValueError,match='chained'):
        runtime_path(tmp_path/'a/x')


def test_explicit_weight_root_bypasses_mapping_but_not_for_work(tmp_path,monkeypatch):
    cold,hot=profile(tmp_path,monkeypatch)
    p=tmp_path/'locations.json';row=json.loads(p.read_text())
    row.update(schema='behave.runtime-locations/2',direct_roots=[str(cold/'checkpoints/direct-runs')])
    p.write_text(json.dumps(row))
    weight=cold/'checkpoints/direct-runs/seed19/native'
    assert runtime_path(weight)==weight
    assert runtime_path(cold/'work/pool.json')==hot/'work/pool.json'
    assert runtime_path(cold/'checkpoints/old/native')==hot/'checkpoints/old/native'
    with pytest.raises(ValueError,match='unmapped'):runtime_path('/legacy/other')
    weight.parent.mkdir(parents=True);weight.symlink_to(hot,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):runtime_path(weight/'file')


def test_partial_rollout_survives_move_without_old_directory(tmp_path, inputs, monkeypatch):
    from test_slime_rollout_handoff import partial_run

    from behave.train.slime_rollout import export_handoff, inspect_handoff
    old,config,release,split,task,*_=partial_run(tmp_path,inputs,saved=True)
    hot=tmp_path/'moved'
    old.rename(hot)
    before=(hot/'artifacts/plan/binding.json').read_bytes()
    profile(tmp_path,monkeypatch,[{'source':str(old),'destination':str(hot)}])
    info=inspect_handoff(hot,config,release,[task],workers_stopped=True)
    assert info['step']==0
    assert str(hot) in info['load']
    assert sorted(info['recovery'].indices)==[0,1,2,3,4]
    exported=export_handoff(info,tmp_path/'new-recovery')
    assert exported['objects']==str(tmp_path/'new-recovery')
    assert not old.exists()
    assert (hot/'artifacts/plan/binding.json').read_bytes()==before


def test_feedback_does_not_read_cold_training_receipts(tmp_path, monkeypatch):
    from behave.improve.feedback import prepare
    old, hot = profile(tmp_path, monkeypatch)
    (old / 'round/training').mkdir(parents=True)
    (old / 'round/training/000.json').write_text('invalid cold JSON must not be read')
    with pytest.raises(ValueError, match='completed training batch'):
        prepare(old / 'round', old / 'binding.json', hot / 'output')


def test_frozen_profile_is_required_even_without_environment(tmp_path,monkeypatch):
    import behave.core.runtime_locations as locations
    old,hot=profile(tmp_path,monkeypatch)
    identity=locations.profile_identity()
    monkeypatch.delenv('BEHAVE_RUNTIME_LOCATIONS')
    monkeypatch.setattr(locations,'DEFAULT_PROFILE',identity['path'])
    monkeypatch.setattr(locations,'EXPECTED_PROFILE_SHA256',identity['sha256'])
    assert runtime_path(old/'x')==hot/'x'
    Path(identity['path']).write_text('{}')
    locations._profile.cache_clear()
    with pytest.raises(ValueError,match='frozen snapshot'):
        runtime_path(old/'x')
