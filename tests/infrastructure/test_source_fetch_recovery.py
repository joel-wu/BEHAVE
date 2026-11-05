"""Acquisition publication failures must not discard or refetch obtained bytes."""

import json
import os
import shutil
from pathlib import Path

import pytest
from test_source_fetch import SOURCE, URL, downloaded, fake_service, selection

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.data.ingest import FetchPolicy, acquire_source, fetch_staging, remote, verify_acquisition
from behave.isolation.service import ServiceResult


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    directory = tmp_path / 'scratch'
    directory.mkdir()
    monkeypatch.setenv('BEHAVE_SCRATCH_DIR', str(directory))
    return directory


@pytest.mark.parametrize('partial', [False, True])
def test_fetch_publication_failure_retains_download_then_resumes(tmp_path, monkeypatch, scratch, partial):
    calls = fake_service(monkeypatch, {URL: downloaded()})
    run = JournalDirectory(tmp_path / 'run')
    publish = DirectoryStore.publish

    def interrupted(self, source, refs, **kwargs):
        if self.directory == run.objects.directory:
            if partial:
                publish(self, source, tuple(refs)[:3], **kwargs)
            raise OSError('archive unavailable')
        return publish(self, source, refs, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(DirectoryStore, 'publish', interrupted)
        with pytest.raises(OSError, match='archive unavailable'):
            acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    retained = list(scratch.iterdir())
    assert len(retained) == 1, 'downloaded bytes must survive failed archive publication'
    assert JournalDirectory(retained[0]).objects.read(BlobRef.of(SOURCE)) == SOURCE
    result = acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    assert result.status == 'acquired' and calls == [URL]
    assert verify_acquisition(run.objects, result.head) == result
    assert list(scratch.iterdir()) == []


def test_fetch_settlement_failure_resumes_without_another_request(tmp_path, monkeypatch):
    calls = fake_service(monkeypatch, {URL: downloaded()})
    run = JournalDirectory(tmp_path / 'run')
    original = run.journal

    def journal(*args):
        target = original(*args)
        append = target.append

        def interrupted(events, kind, payload, artifacts=()):
            if kind == 'fetch_settled':
                raise OSError('settlement interrupted')
            return append(events, kind, payload, artifacts)

        target.append = interrupted
        return target

    with monkeypatch.context() as fault:
        fault.setattr(run, 'journal', journal)
        with pytest.raises(OSError, match='settlement interrupted'):
            acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    result = acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    assert result.status == 'acquired' and calls == [URL]
    assert [e.kind for e in run.journal('acquisition').load()] == ['fetch_started', 'fetch_settled']


def retained_fetch(tmp_path, monkeypatch, *, result=None):
    calls = fake_service(monkeypatch, {URL: downloaded()}, result=result)
    run = JournalDirectory(tmp_path / 'run')
    with monkeypatch.context() as fault:
        def interrupted(*args, **kwargs):
            raise OSError('before archive publication')
        fault.setattr(remote, '_publish', interrupted)
        with pytest.raises(OSError):
            acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    owner = json.loads((run.directory / 'fetch-owner.json').read_text())
    return run, Path(owner['directory']), calls


@pytest.mark.parametrize('change', ['plan', 'policy', 'service-inputs', 'source-bytes',
                                  'marker', 'linked-owner', 'linked-service', 'replaced-scratch'])
def test_changed_retained_fetch_is_neither_promoted_nor_refetched(tmp_path, monkeypatch, change):
    from dataclasses import replace

    run, local, calls = retained_fetch(tmp_path, monkeypatch)
    plan, policy = selection(), FetchPolicy(('sources.example',))
    if change == 'plan':
        plan = replace(plan, entry_symbol='other')
    elif change == 'policy':
        policy = replace(policy, timeout_seconds=policy.timeout_seconds + 1)
    elif change == 'service-inputs':
        path = local / 'service.json'
        value = json.loads(path.read_text())
        value['plan'] = BlobRef.of(b'changed').as_dict()
        path.chmod(0o600)
        path.write_text(json.dumps(value))
    elif change == 'source-bytes':
        path = local / 'objects' / BlobRef.of(SOURCE).sha256
        path.chmod(0o600)
        path.write_bytes(b'corrupt')
    elif change == 'marker':
        path = local / fetch_staging.MARKER
        path.chmod(0o600)
        path.write_text('{}')
    elif change in {'linked-owner', 'linked-service'}:
        path = run.directory / 'fetch-owner.json' if change == 'linked-owner' else local / 'service.json'
        moved = path.with_suffix('.retained')
        path.rename(moved)
        path.symlink_to(moved)
    elif change == 'replaced-scratch':
        local.rename(local.with_name(local.name + '-original'))
        local.mkdir()
    with pytest.raises((ValueError, RecordError)):
        acquire_source(plan, policy, run=run)
    assert calls == [URL] and local.exists()
    assert len(run.journal('acquisition').load()) == 1


@pytest.mark.parametrize('missing', ['owner', 'service', 'scratch', 'different-boot'])
def test_ambiguous_or_nonlocal_fetch_still_requires_reconciliation(tmp_path, monkeypatch, missing):
    run, local, calls = retained_fetch(tmp_path, monkeypatch)
    if missing == 'owner':
        (run.directory / 'fetch-owner.json').unlink()
    elif missing == 'service':
        (local / 'service.json').unlink()
    elif missing == 'scratch':
        local.rename(local.with_name(local.name + '-retained'))
    else:
        monkeypatch.setattr(fetch_staging, '_transport_host', lambda: 'another-boot')
    with pytest.raises(AttemptInterrupted, match='fetch-dispatch-pending'):
        acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    assert calls == [URL]


def test_unknown_cleanup_publishes_unavailable_without_opening_trace(tmp_path, monkeypatch):
    run, local, calls = retained_fetch(tmp_path, monkeypatch,
        result=ServiceResult('cleanup-incomplete', -9, b'', b'', .01))
    seal = JournalDirectory.seal

    def guarded(self, *parts):
        assert self.directory != local, 'an unconfirmed worker trace is not safe to read'
        return seal(self, *parts)

    monkeypatch.setattr(JournalDirectory, 'seal', guarded)
    result = acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    assert result.status == 'unavailable' and result.code == 'fetch-cleanup-unconfirmed'
    assert result.source is None and calls == [URL] and local.exists()


@pytest.mark.parametrize('boundary', ['children', 'empty-directory', 'release-receipt'])
def test_fetch_cleanup_can_finish_after_partial_release(tmp_path, monkeypatch, scratch, boundary):
    calls = fake_service(monkeypatch, {URL: downloaded()})
    run = JournalDirectory(tmp_path / 'run')
    remove_tree, remove_directory, publish = shutil.rmtree, Path.rmdir, fetch_staging.publish_json

    def tree(path, *args, **kwargs):
        remove_tree(path, *args, **kwargs)
        if Path(path).name == 'objects':
            raise OSError('release interrupted')

    def directory(path, *args, **kwargs):
        if path.name.startswith('behave-source-fetch-'):
            raise OSError('release interrupted')
        return remove_directory(path, *args, **kwargs)

    def record(path, value):
        if path.name == 'fetch-released.json':
            raise OSError('release interrupted')
        return publish(path, value)

    with monkeypatch.context() as fault:
        if boundary == 'children':
            fault.setattr(shutil, 'rmtree', tree)
        elif boundary == 'empty-directory':
            fault.setattr(Path, 'rmdir', directory)
        else:
            fault.setattr(fetch_staging, 'publish_json', record)
        with pytest.raises(OSError, match='release interrupted'):
            acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    assert len(run.journal('acquisition').load()) == 2
    result = acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    assert result.status == 'acquired' and calls == [URL] and not list(scratch.iterdir())
    assert acquire_source(selection(), FetchPolicy(('sources.example',)), run=run) == result


def test_copied_completed_fetch_replays_without_releasing_original_scratch(tmp_path, monkeypatch):
    calls = fake_service(monkeypatch, {URL: downloaded()})
    run = JournalDirectory(tmp_path / 'run')
    with monkeypatch.context() as fault:
        fault.setattr(fetch_staging, 'release', lambda *args: None)
        result = acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    local = Path(json.loads((run.directory / 'fetch-owner.json').read_text())['directory'])
    copied = tmp_path / 'copied'
    shutil.copytree(run.directory, copied)
    assert acquire_source(selection(), FetchPolicy(('sources.example',)), run=JournalDirectory(copied)) == result
    assert local.exists() and calls == [URL]
    assert acquire_source(selection(), FetchPolicy(('sources.example',)), run=run) == result
    assert not local.exists()


def test_reappearing_released_path_is_not_deleted(tmp_path, monkeypatch):
    calls = fake_service(monkeypatch, {URL: downloaded()})
    run = JournalDirectory(tmp_path / 'run')
    acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    local = Path(json.loads((run.directory / 'fetch-owner.json').read_text())['directory'])
    local.mkdir()
    (local / 'unrelated').write_text('retain')
    with pytest.raises(ValueError, match='reappeared'):
        acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    assert (local / 'unrelated').read_text() == 'retain' and calls == [URL]


def test_recovery_uses_saved_scratch_not_new_environment_default(tmp_path, monkeypatch):
    run, local, calls = retained_fetch(tmp_path, monkeypatch)
    replacement = tmp_path / 'new-scratch'
    monkeypatch.setenv('BEHAVE_SCRATCH_DIR', str(replacement))
    result = acquire_source(selection(), FetchPolicy(('sources.example',)), run=run)
    assert result.status == 'acquired' and calls == [URL]
    assert not local.exists() and not replacement.exists()


def test_existing_acquisition_owner_blocks_another_coordinator(tmp_path, monkeypatch, scratch):
    calls = fake_service(monkeypatch, {URL: downloaded()})
    run = JournalDirectory(tmp_path / 'run')
    with run.owner(), pytest.raises(AttemptInterrupted, match='run-already-owned'):
        acquire_source(selection(), FetchPolicy(('sources.example',)), run=JournalDirectory(run.directory))
    assert not calls and not list(scratch.iterdir())


@pytest.mark.parametrize('boundary', ['publication', 'settled'])
def test_real_coordinator_death_keeps_completed_network_work(tmp_path, monkeypatch, boundary, scratch):
    from behave.data.ingest import fetch_http

    fake_service(monkeypatch, {URL: downloaded()})
    requests = tmp_path / 'requests'

    def request(*args, **kwargs):
        with requests.open('a') as stream:
            stream.write('request\n')
        return downloaded()

    monkeypatch.setattr(fetch_http, 'get', request)
    pid = os.fork()
    if pid == 0:
        if boundary == 'publication':
            remote._publish = lambda *args: os._exit(73)
        else:
            fetch_staging.release = lambda *args: os._exit(73)
        try:
            acquire_source(selection(), FetchPolicy(('sources.example',)), run=JournalDirectory(tmp_path / 'run'))
        finally:
            os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    result = acquire_source(selection(), FetchPolicy(('sources.example',)), run=JournalDirectory(tmp_path / 'run'))
    assert result.status == 'acquired' and requests.read_text() == 'request\n'
    assert not list(scratch.iterdir())


FETCH_RECOVERY_PROBE = r'''
import json, os, sys
from pathlib import Path
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.storage import DirectoryStore
from behave.data.ingest import FetchPlan, FetchPolicy, RemoteFile, acquire_source, fetch_http, fetch_worker
from behave.isolation import service

root = Path(sys.argv[1])
scratch = root / 'scratch'
scratch.mkdir(parents=True)
os.environ['BEHAVE_SCRATCH_DIR'] = str(scratch)
run = JournalDirectory(root / 'run')
body = b'int f(int x) { return x; }\n'
url = 'https://sources.example/model.c'
plan = FetchPlan('fixture', 'https://sources.example/project', 'pinned', None,
                 (RemoteFile('model.c', url),), 'model.c', 'f')
policy = FetchPolicy(('sources.example',))
calls = []
def get(url, **kwargs):
    calls.append(url)
    return fetch_http.Hop('downloaded', '8.8.8.8', 200, body=body)
def execute(argv, **kwargs):
    directory, digest, size, policy_digest, policy_size = argv[-5:]
    local = JournalDirectory(directory)
    selected = FetchPlan.from_bytes(local.objects.read(BlobRef(digest, int(size))))
    fixed = FetchPolicy.from_bytes(local.objects.read(BlobRef(policy_digest, int(policy_size))))
    fetch_worker.execute(selected, fixed, local.journal('trace'))
    return service.ServiceResult('exited', 0, b'', b'', .01)
fetch_http.get = get
service.run_service = execute
publish = DirectoryStore.publish
def interrupted(self, reader, refs, **kwargs):
    if self.directory == run.objects.directory:
        raise OSError('fixture publication interruption')
    return publish(self, reader, refs, **kwargs)
DirectoryStore.publish = interrupted
try:
    acquire_source(plan, policy, run=run)
except OSError:
    pass
else:
    raise AssertionError('publication interruption was not exercised')
finally:
    DirectoryStore.publish = publish
assert len(list(scratch.iterdir())) == 1
result = acquire_source(plan, policy, run=run)
assert result.status == 'acquired' and calls == [url]
assert run.objects.read(BlobRef.of(body)) == body
assert not list(scratch.iterdir())
assert acquire_source(plan, policy, run=run) == result and calls == [url]
assert not any(name == 'tools' or name.startswith('tools.') for name in sys.modules)
print('source-fetch-recovery-installed')
'''
