"""Scoring helpers are installable; legacy commands keep the same objects."""

import os
import signal
import sys

import pytest

from behave.eval import scoring as scoring_module
from behave.runtime import snapshots as snapshots_module


@pytest.mark.parametrize('explicit', [False, True])
@pytest.mark.parametrize('inherited', [None, '1'])
@pytest.mark.parametrize('failure', [None, 'preparation', 'body'])
def test_scoring_scope_preserves_batch_runtime_and_restores_controls(
        tmp_path, monkeypatch, explicit, inherited, failure):
    from behave.isolation import runtime_environment as runtime
    from behave.isolation.startup import startup_environment

    name = 'BEHAVE_ISOLATION_STARTUP_SLOTS'
    if inherited is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, inherited)
    monkeypatch.setenv('BEHAVE_RUNTIME_DIR', str(tmp_path / 'batch-runtime'))
    shared = runtime.runtime_environment()
    marker = shared.directory / 'retained-resource'
    marker.write_bytes(b'batch-owned resource')
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    get_runtime, calls = runtime.runtime_environment, []

    def prepare():
        calls.append(os.environ.get(name))
        if failure == 'preparation':
            raise ValueError('fixture preparation failure')
        return get_runtime()

    monkeypatch.setattr(runtime, 'runtime_environment', prepare)

    def execute():
        options = {'environment': startup_environment(4)} if explicit else {}
        with scoring_module.scoring_environment(**options) as cancelled:
            assert get_runtime() is shared and not cancelled()
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            assert cancelled()
            if failure == 'body':
                raise ValueError('fixture body failure')

    if failure is None:
        execute()
    else:
        with pytest.raises(ValueError, match=f'fixture {failure} failure'):
            execute()
    assert calls == ['4' if explicit else inherited]
    restored = os.environ.get(name)
    assert restored == inherited
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    assert get_runtime() is shared
    assert marker.read_bytes() == b'batch-owned resource'
    runtime_path = os.environ.get('BEHAVE_RUNTIME_DIR')
    assert runtime_path == str(shared.directory)


def test_scoring_node_stages_recorded_source_before_exec(tmp_path, monkeypatch):
    from behave.runtime.records import write_json

    output, local = tmp_path / 'scoring', tmp_path / 'node-code'
    stages, commands = [], []
    limits = {'soft': 64, 'hard': 128}
    monkeypatch.setattr('behave.core.process_limits.configure_worker_process_limit',
                        lambda limit: limits if limit == 64 else pytest.fail('changed process limit'))
    monkeypatch.setattr(scoring_module, 'mkdtemp', lambda **kw: str(local))
    monkeypatch.setattr(snapshots_module, 'stage', lambda source, target: stages.append((source, target)))
    monkeypatch.setattr(scoring_module.os, 'execv', lambda executable, argv: commands.append((executable, argv)))
    scoring_module.staged(output, 2, process_limit=64)
    assert stages == [(output / 'snapshot', local / 'checkout')]
    executable, argv = commands[0]
    assert executable == sys.executable and argv[-2:] == [str(output), '2']
    assert argv[-4:-2] == [str(local / 'checkout'), str(local / 'checkout/src')]
    assert 'from tools.eval_score_job import run;' in argv[argv.index('-c') + 1]
    assert 'process_limit=64' in argv[argv.index('-c') + 1]
    expected = tmp_path / 'expected.json'
    write_json(expected, limits)
    assert (output / 'process-2.json').read_bytes() == expected.read_bytes()
