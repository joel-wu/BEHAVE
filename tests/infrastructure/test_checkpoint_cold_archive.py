import hashlib
import json
import tarfile
import pytest
from behave.runtime.models import cold_model, digest, evict_model, publish_model, restore_model


def directory_fixture(tmp_path):
    cold=tmp_path/'cold-directory';cold.mkdir();(cold/'weights').write_bytes(b'weights')
    hot=tmp_path/'absent-hot'
    receipt=dict(state='verified',hot_source=str(hot),hot_source_retained=False,
        retained_directory=str(cold),identity=dict(schema='behave.cold-hf-directory/1',checkpoint=None,
        files=[dict(path='weights',size_bytes=7)]),sha256_members={'weights':hashlib.sha256(b'weights').hexdigest()})
    (tmp_path/'absent-hot.cold.json').write_text(json.dumps(receipt))
    return hot,cold


def test_imported_cold_directory_stages_privately_without_hot_symlink(tmp_path):
    hot,cold=directory_fixture(tmp_path)
    target=tmp_path/'private'
    assert restore_model(hot,destination=target)==target
    assert (target/'weights').read_bytes()==b'weights'
    assert not hot.exists() and not hot.is_symlink()
    assert (cold/'weights').read_bytes()==b'weights'


def test_imported_cold_directory_corruption_never_publishes(tmp_path):
    hot,cold=directory_fixture(tmp_path);(cold/'weights').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='content differs'):restore_model(hot,destination=tmp_path/'private')
    assert not (tmp_path/'private').exists() and not hot.exists()


def test_imported_directory_marker_preserves_runtime_profile_validation(tmp_path,monkeypatch):
    from behave.core import runtime_locations
    hot,cold=directory_fixture(tmp_path)
    profile=tmp_path/'profile.json'
    profile.write_text(json.dumps(dict(schema='behave.runtime-locations/1',
        mappings=[dict(source='/old/imported-model',destination=str(hot))],deny_unmapped_roots=[])))
    monkeypatch.setenv('BEHAVE_RUNTIME_LOCATIONS',str(profile))
    assert runtime_locations.runtime_path('/old/imported-model')==hot


def fixture(tmp_path):
    model=tmp_path/'model';model.mkdir();(model/'weights').write_bytes(b'weights')
    return model,[{'path':'weights','size_bytes':7}],dict(checkpoint={'sha256':'saved'},staging=tmp_path/'stage',destination=tmp_path/'cold')


def test_whole_packet_publication_retains_model_and_is_idempotent(tmp_path):
    model,files,args=fixture(tmp_path)
    r=publish_model(model,files,**args)
    assert publish_model(model,files,**args)==r
    assert (model/'weights').read_bytes()==b'weights'
    assert not list(args['staging'].iterdir())
    with tarfile.open(r['archive']) as tar:assert tar.extractfile('model/weights').read()==b'weights'
    assert sorted(p.suffix for p in args['destination'].iterdir())==['.json','.lock','.tar']


def test_corrupt_uncommitted_cold_object_is_rewritten_before_commit(tmp_path):
    model,files,args=fixture(tmp_path)
    identity={'schema':'behave.cold-hf-archive/1','checkpoint':args['checkpoint'],'files':files}
    key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    args['destination'].mkdir();(args['destination']/f'{key}.tar').write_bytes(b'corrupt')
    receipt=publish_model(model,files,**args)
    assert digest(receipt['archive'])==(receipt['sha256'],receipt['bytes'])
    assert (args['destination']/f'{key}.tar').read_bytes()!=b'corrupt'
    assert (model/'weights').exists()


def test_symlink_or_traversal_is_not_archived(tmp_path):
    model,files,args=fixture(tmp_path);(model/'linked').symlink_to(model/'weights')
    with pytest.raises(ValueError,match='unsafe'):publish_model(model,[{'path':'linked','size_bytes':7}],**args)
    with pytest.raises(ValueError,match='unsafe'):publish_model(model,[{'path':'../escape','size_bytes':7}],**args)


def test_eviction_and_verified_on_demand_restore(tmp_path):
    model,files,args=fixture(tmp_path)
    r=publish_model(model,files,**args)
    assert not evict_model(model,r,protected=[model])
    assert evict_model(model,r)
    assert not model.exists() and cold_model(model)['state']=='verified'
    assert restore_model(model)==model
    assert (model/'weights').read_bytes()==b'weights'
    assert evict_model(model,cold_model(model))


def test_unknown_or_changed_hot_files_are_never_deleted(tmp_path):
    model,files,args=fixture(tmp_path);r=publish_model(model,files,**args)
    (model/'other').write_text('keep')
    with pytest.raises(ValueError,match='inventory'):evict_model(model,r)
    assert (model/'weights').exists()


def test_corrupt_cold_restore_never_publishes_hot_model(tmp_path):
    model,files,args=fixture(tmp_path);r=publish_model(model,files,**args)
    evict_model(model,r);Path=type(model);Path(r['archive']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='SHA'):restore_model(model)
    assert not model.exists()


def test_cold_packet_stages_privately_without_nfs_rehydration(tmp_path):
    model,files,args=fixture(tmp_path);r=publish_model(model,files,**args)
    evict_model(model,r)
    before=cold_model(model)
    target=tmp_path/'local'/'weights';target.parent.mkdir()
    assert restore_model(model,destination=target)==target
    assert (target/'weights').read_bytes()==b'weights'
    assert not model.exists() and cold_model(model)==before
    assert list(target.parent.iterdir())==[target]


def test_failed_private_restore_leaves_no_model_or_transport(tmp_path):
    model,files,args=fixture(tmp_path);r=publish_model(model,files,**args)
    evict_model(model,r);type(model)(r['archive']).write_bytes(b'corrupt')
    target=tmp_path/'local'/'weights';target.parent.mkdir()
    with pytest.raises(ValueError,match='SHA'):restore_model(model,destination=target)
    assert not model.exists() and not list(target.parent.iterdir())


def test_private_restore_refuses_existing_destination(tmp_path):
    model,files,args=fixture(tmp_path);r=publish_model(model,files,**args);evict_model(model,r)
    target=tmp_path/'existing';target.mkdir();(target/'keep').write_text('keep')
    with pytest.raises(ValueError,match='new private'):restore_model(model,destination=target)
    assert (target/'keep').read_text()=='keep'


def test_private_restore_checks_capacity_before_store_read(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import shutil
    model,files,args=fixture(tmp_path);r=publish_model(model,files,**args);evict_model(model,r)
    monkeypatch.setattr(shutil,'disk_usage',lambda _:SimpleNamespace(free=1))
    type(model)(r['archive']).unlink()
    with pytest.raises(OSError,match='insufficient local'):restore_model(model,destination=tmp_path/'local')


def test_private_restore_rejects_signed_but_unsafe_tar(tmp_path):
    import io
    from pathlib import Path
    from behave.runtime.models import marker
    model,files,args=fixture(tmp_path);r=publish_model(model,files,**args);evict_model(model,r)
    with tarfile.open(r['archive'],'w') as tar:
        member=tarfile.TarInfo('model/../../outside');member.size=3
        tar.addfile(member,io.BytesIO(b'bad'))
    r['sha256'],r['bytes']=digest(r['archive'])
    marker(model).write_text(json.dumps(r))
    target=tmp_path/'local'/'weights';target.parent.mkdir()
    with pytest.raises(ValueError,match='unsafe'):restore_model(model,destination=target)
    assert not (tmp_path/'outside').exists() and not list(target.parent.iterdir())
