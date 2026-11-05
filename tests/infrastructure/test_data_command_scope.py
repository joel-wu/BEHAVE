"""Data/Syn commands share process controls, not their execution policies."""

import signal
from types import SimpleNamespace

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.core.records import parse_json


def command(tmp_path, monkeypatch, lane, execute):
    """Keep real command routing and input reads; replace only the dispatch."""
    if lane == 'native':
        from behave.improve import native_acquisition as entry

        paths = [tmp_path / name for name in ('request', 'endpoint', 'response')]
        monkeypatch.setattr(entry.sys, 'argv', ['acquisition', *map(str, paths)])

        def acquire(*args, **kwargs):
            assert args == tuple(paths)
            return execute(**kwargs)

        monkeypatch.setattr(entry, 'execute', acquire)
        return entry.main
    if lane == 'fetch':
        from test_source_fetch import selection

        from behave.data import __main__ as router
        from behave.data.ingest import fetch_cli as entry
        from behave.data.ingest.fetch_records import FetchPolicy

        plan, policy = tmp_path / 'plan.json', tmp_path / 'policy.json'
        plan.write_bytes(selection().to_bytes())
        policy.write_bytes(FetchPolicy(('sources.example',)).to_bytes())

        def fetch(*args, **kwargs):
            result = execute(**kwargs)
            return SimpleNamespace(as_dict=lambda: result)

        monkeypatch.setattr(entry, 'acquire_source', fetch)
        return lambda: router.main(['fetch', '--plan', str(plan), '--policy', str(policy),
                                    '--run-directory', str(tmp_path / 'run')])
    from behave.data import __main__ as entry
    from behave.improve import acquisition as acquisition_module

    config = tmp_path / 'configuration.json'
    config.write_bytes(b'{}')
    # Input/schema validation has its own tests. This command takes an early
    # return after acquisition; process controls must still be restored.
    monkeypatch.setattr(acquisition_module.AcquisitionConfiguration, 'from_bytes', lambda _: object())
    monkeypatch.setattr(acquisition_module, 'acquire_materials', execute)
    return lambda: entry.main(['--stage', 'acquisition', '--configuration', str(config),
                              '--source-objects', str(tmp_path / 'objects'),
                              '--run-directory', str(tmp_path / 'run')])


@pytest.mark.parametrize('lane', ['data', 'fetch', 'native'])
def test_partial_setup_restores_first_handler_without_dispatch(tmp_path, monkeypatch, capsys, lane):
    invoke = command(tmp_path, monkeypatch, lane, lambda **kw: pytest.fail('setup failure dispatched work'))
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    install = signal.signal

    def fail_second(signum, handler):
        if signum == signal.SIGTERM:
            raise ValueError('fixture handler installation failed')
        return install(signum, handler)

    monkeypatch.setattr(signal, 'signal', fail_second)
    try:
        if lane == 'native':
            with pytest.raises(ValueError, match='fixture handler installation failed'):
                invoke()
        else:
            assert invoke() == 1
            assert parse_json(capsys.readouterr().out.encode()) == {
                'status': 'unavailable',
                'code': 'fetch-command-invalid' if lane == 'fetch' else 'synthesis-command-invalid'}
        assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
        assert not (tmp_path / 'run').exists()
    finally:
        # Also isolate the regression test when run against the broken code.
        for sig, handler in handlers.items():
            install(sig, handler)


@pytest.mark.parametrize('lane', ['data', 'fetch', 'native'])
@pytest.mark.parametrize('outcome', ['success', 'cancel', 'exception', 'interrupt'])
def test_command_controls_restore_on_every_exit(tmp_path, monkeypatch, capsys, lane, outcome):
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    calls = []
    result = {'status': 'acquired' if lane == 'fetch' else 'completed'}

    def execute(**kwargs):
        cancelled = kwargs['control'].cancelled if lane == 'data' else kwargs['cancelled']
        assert not cancelled()
        assert all(signal.getsignal(sig) != handler for sig, handler in handlers.items())
        calls.append(cancelled)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert cancelled()
        if outcome == 'cancel':
            raise AttemptInterrupted('fixture-cancelled')
        if outcome == 'exception':
            raise RuntimeError('fixture failure')
        if outcome == 'interrupt':
            raise KeyboardInterrupt()
        return result

    invoke = command(tmp_path, monkeypatch, lane, execute)
    install = signal.signal
    try:
        expected = {'exception': RuntimeError, 'interrupt': KeyboardInterrupt}
        if lane == 'native':
            expected['cancel'] = AttemptInterrupted
        if outcome in expected:
            with pytest.raises(expected[outcome]):
                invoke()
            assert capsys.readouterr().out == ''
        else:
            assert invoke() == (None if lane == 'native' else 1 if outcome == 'cancel' else 0)
            if lane != 'native':
                assert parse_json(capsys.readouterr().out.encode()) == (
                    {'status': 'unavailable', 'code': 'fixture-cancelled'} if outcome == 'cancel' else result)
        assert len(calls) == 1 and calls[0]()
        assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    finally:
        for sig, handler in handlers.items():
            install(sig, handler)


def test_commands_use_the_shared_scope():
    from behave.data import __main__ as data
    from behave.data.ingest import fetch_cli
    from behave.improve import native_acquisition
    from behave.runtime.records import command_scope

    for entry in (data, fetch_cli, native_acquisition):
        assert entry.command_scope is command_scope
