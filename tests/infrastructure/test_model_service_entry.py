"""Service wrapper ownership before and after delegating to the native worker."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_serving import load_tool

from behave.core.records import parse_json
from behave.runtime import records as records_module
from behave.runtime import snapshots as snapshots_module


@pytest.mark.parametrize('failure', ['stage', 'entry-write', 'dispatch', 'interrupt', 'worker-exit'])
def test_wrapper_releases_only_pre_dispatch_scratch(tmp_path, monkeypatch, capsys, failure):
    job = load_tool(monkeypatch, 'model_service_job')
    monkeypatch.setenv('BEHAVE_SCRATCH_DIR', str(tmp_path))
    staged, commands = [], []

    def stage(snapshot, source):
        assert snapshot == tmp_path / 'snapshot'
        source.mkdir()
        (source / 'marker').write_text('owned temporary code')
        staged.append(source)
        if failure == 'stage':
            raise OSError('fixture stage failed')
        return {'sha256': 'frozen'}

    def execute(command, *, check):
        commands.append(command)
        assert check is False and command[:5] == [job.sys.executable, '-I', '-B', '-u', '-c']
        assert Path(command[-2]) == staged[0]
        options = parse_json(Path(command[-1]).read_bytes())
        assert options == {'frozen': {'sha256': 'frozen'}, 'output': str(tmp_path / 'output'),
            'runtime': 'sglang', 'native_tokens': True, 'task_rollout': True, 'settings': {'fixture': 1},
            'weight_input': str(tmp_path / 'weights.json'), 'weight_reference': {'fixture': 'weights'},
            'binding_input': str(tmp_path / 'binding.json'), 'binding_reference': {'fixture': 'binding'}}
        if failure == 'dispatch':
            raise OSError('fixture dispatch failed')
        if failure == 'interrupt':
            raise KeyboardInterrupt
        return SimpleNamespace(returncode=23)

    monkeypatch.setattr(snapshots_module, 'stage', stage)
    monkeypatch.setattr(job, 'subprocess', SimpleNamespace(run=execute))
    if failure == 'entry-write':
        def write(*args, **kwargs):
            raise OSError('fixture entry write failed')
        monkeypatch.setattr(records_module, 'write_json', write)
    options = {'native_tokens': True, 'task_rollout': True, 'settings': {'fixture': 1},
        'weight_input': tmp_path / 'weights.json', 'weight_reference': {'fixture': 'weights'},
        'binding_input': tmp_path / 'binding.json', 'binding_reference': {'fixture': 'binding'}}
    if failure == 'worker-exit':
        assert job.run(tmp_path / 'snapshot', tmp_path / 'output', 'sglang', **options) == 23
    elif failure == 'interrupt':
        with pytest.raises(KeyboardInterrupt):
            job.run(tmp_path / 'snapshot', tmp_path / 'output', 'sglang', **options)
    else:
        with pytest.raises(OSError, match='fixture'):
            job.run(tmp_path / 'snapshot', tmp_path / 'output', 'sglang', **options)
    delegated = failure in {'dispatch', 'interrupt', 'worker-exit'}
    assert len(commands) == int(delegated)
    assert staged[0].parent.exists() is delegated
    assert not (tmp_path / 'output').exists()  # The wrapper cannot invent a serving receipt.
    events = [parse_json(line.encode()) for line in capsys.readouterr().out.splitlines()]
    assert (events[-1]['phase'] == 'worker-scratch-retained') is delegated
