import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from tools import local_batch as batch


def test_many_small_files_use_one_bundle_and_round_trip(tmp_path):
    source = tmp_path / 'results'
    source.mkdir()
    (source / 'empty').mkdir()
    for index in range(200):
        (source / f'goal-{index}.json').write_text(json.dumps(dict(goal=index)))
    tool = source / 'check.sh'
    tool.write_bytes(b'#!/bin/sh\nexit 0\n')
    tool.chmod(0o755)
    saved = tmp_path / 'cold'
    receipt = batch.save(source, saved)
    assert {p.name for p in saved.iterdir()} == {'bundle.tar', 'bundle.json'}
    assert receipt['files'] == 201
    assert receipt['sha256'] == hashlib.sha256((saved / 'bundle.tar').read_bytes()).hexdigest()
    restored = batch.load(saved, tmp_path / 'restored')
    assert (restored / 'empty').is_dir()
    assert (restored / 'check.sh').stat().st_mode & 0o777 == 0o755
    for path in source.rglob('*'):
        if path.is_file():
            assert (restored / path.relative_to(source)).read_bytes() == path.read_bytes()


def test_existing_bundle_and_existing_local_input_are_not_overwritten(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'result').write_text('retained')
    saved = tmp_path / 'saved'
    batch.save(source, saved)
    original = (saved / 'bundle.tar').read_bytes()
    with pytest.raises(FileExistsError):
        batch.save(source, saved)
    assert (saved / 'bundle.tar').read_bytes() == original
    with pytest.raises(FileExistsError):
        batch.load(saved, source)
    assert (source / 'result').read_text() == 'retained'


def test_failed_transfer_has_no_completion_receipt_and_keeps_local_results(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'result').write_text('retained')
    def broken_copy(source, target):
        target.write_bytes(b'incomplete')
        raise OSError('transfer failed')
    monkeypatch.setattr(batch, '_copy', broken_copy)
    with pytest.raises(OSError, match='transfer failed'):
        batch.save(source, tmp_path / 'saved')
    assert (source / 'result').read_text() == 'retained'
    assert [p.name for p in (tmp_path / 'saved').iterdir()] == ['bundle.tar.partial']


def test_corrupt_bundle_is_rejected_before_unpacking(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    saved = tmp_path / 'saved'
    batch.save(source, saved)
    with (saved / 'bundle.tar').open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='checksum or size'):
        batch.load(saved, tmp_path / 'unpacked')
    assert not (tmp_path / 'unpacked').exists()


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE),
    ('/absolute', tarfile.REGTYPE), ('link', tarfile.SYMTYPE),
    ('hardlink', tarfile.LNKTYPE), ('pipe', tarfile.FIFOTYPE)])
def test_bundle_paths_and_links_cannot_escape_local_root(tmp_path, name, kind):
    saved = tmp_path / 'saved'
    saved.mkdir()
    archive = saved / 'bundle.tar'
    with tarfile.open(archive, 'w') as writer:
        item = tarfile.TarInfo(name)
        item.type, item.linkname = kind, '/store/target'
        if kind == tarfile.REGTYPE:
            item.size = 1
        writer.addfile(item, io.BytesIO(b'x'))
    (saved / 'bundle.json').write_text(json.dumps(dict(schema='behave.local-batch-bundle/1',
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        size_bytes=archive.stat().st_size, files=1)))
    with pytest.raises(ValueError, match='unsafe bundle'):
        batch.load(saved, tmp_path / 'unpacked')
    assert not (tmp_path / 'escape').exists()


def test_packing_does_not_follow_links_into_other_storage(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'remote').symlink_to('/store', target_is_directory=True)
    with pytest.raises(ValueError, match='regular files'):
        batch.save(source, tmp_path / 'saved')
    assert not (tmp_path / 'saved').exists()


def test_one_local_environment_replaces_inherited_remote_caches(tmp_path, monkeypatch):
    for name in ('TMPDIR', 'BEHAVE_SCRATCH_DIR', 'BEHAVE_RUNTIME_DIR', 'BEHAVE_ISOLATION_STARTUP_DIR'):
        monkeypatch.setenv(name, '/store/wrong')
    environment = batch.environment(tmp_path / 'checkout', tmp_path / 'scratch', Path('/tools/eda'))
    assert environment['TMPDIR'] == environment['BEHAVE_SCRATCH_DIR'] == str(tmp_path / 'scratch')
    assert environment['BEHAVE_RUNTIME_DIR'] == str(tmp_path / 'scratch/runtime')
    assert environment['BEHAVE_ISOLATION_STARTUP_DIR'].startswith('/tmp/')
    assert not any(value.startswith('/store') for value in environment.values())


def test_remote_unpack_destination_is_rejected_without_reading_bundle(tmp_path):
    with pytest.raises(ValueError, match='node-local /tmp'):
        batch.load(tmp_path / 'missing', Path('/store/should-not-be-created'))


def test_default_local_run_does_not_create_an_archive(tmp_path):
    batch.environment(tmp_path / 'checkout', tmp_path / 'scratch', Path('/tools'))
    assert not list(tmp_path.iterdir())
