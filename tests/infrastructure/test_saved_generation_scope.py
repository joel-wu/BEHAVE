"""Saved generation owns process controls only for its own phase."""

import json
import os
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.artifacts import BlobRef
from behave.eval import generation as generation_module
from behave.isolation.startup import startup_slots


@pytest.mark.parametrize('inherited', [None, '7'])
@pytest.mark.parametrize('slots', [None, 2])
@pytest.mark.parametrize('outcome', ['complete', 'preflight_error', 'execution_error', 'cancel'])
def test_generation_restores_controls_and_preserves_cancelled_results(
        tmp_path, publication, monkeypatch, inherited, slots, outcome):
    name = 'BEHAVE_ISOLATION_STARTUP_SLOTS'
    if inherited is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, inherited)
    monkeypatch.setenv('BEHAVE_SCRATCH_DIR', str(tmp_path))
    monkeypatch.setenv('BEHAVE_RUNTIME_DIR', str(tmp_path / 'parent-runtime'))
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    output = tmp_path / 'review'
    output.mkdir()
    endpoint = LocalChatEndpoint('http://127.0.0.1:31000/v1/chat/completions', 'fixture', 'fixture')
    settings = {
        'provider': 'sglang', 'endpoint': endpoint.as_dict(),
        'tasks': [publication.task.task_id], 'task_objects': str(publication.root),
        'release': publication.ref.as_dict(), 'setting': 'rtl_only', 'score_on_submit': False,
        'attempt': {'max_turns': 3}, 'workers': 1, 'model_workers': 1, 'judge_workers': 1,
        'isolation_startup_slots': slots,
    }
    (output / 'settings.json').write_text(json.dumps(settings))
    monkeypatch.setattr('behave.core.process_limits.configure_worker_process_limit', lambda *_: {})

    def prepare_runtime(**kwargs):
        assert startup_slots() == slots
        os.environ['BEHAVE_RUNTIME_DIR'] = str(kwargs['directory'])

    def preflight(*_):
        assert startup_slots() == slots
        assert Path(os.environ['BEHAVE_SCRATCH_DIR']).parent == tmp_path
        if outcome == 'preflight_error':
            raise RuntimeError('fixture preflight failure')

    calls = []

    def evaluate(**kwargs):
        calls.append(kwargs)
        assert startup_slots() == slots and not kwargs['cancelled']()
        if outcome == 'execution_error':
            raise RuntimeError('fixture execution failure')
        if outcome == 'cancel':
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            assert kwargs['cancelled']()
        return SimpleNamespace(summary={'candidates': [
            {'run_key': BlobRef.of(job.to_bytes()).sha256, 'status': 'pending_judgment'}
            for job in kwargs['plan'].jobs]})

    monkeypatch.setattr('behave.sim.api.prepare_runtime', prepare_runtime)
    monkeypatch.setattr(generation_module, 'check_development_shell', preflight)
    monkeypatch.setattr('behave.eval.runner.run_evaluation', evaluate)
    try:
        if outcome.endswith('_error'):
            with pytest.raises(RuntimeError, match='fixture .* failure'):
                generation_module.run(output, status_output=output)
        else:
            assert generation_module.run(output, status_output=output) == (1 if outcome == 'cancel' else 0)
            assert json.loads((output / 'report.json').read_text())['candidates']
            assert (output / 'progress_final.json').exists()
        assert len(calls) == (0 if outcome == 'preflight_error' else 1)
        restored = os.environ.get(name)
        assert restored == inherited
        assert os.environ['BEHAVE_SCRATCH_DIR'] == str(tmp_path)
        assert os.environ['BEHAVE_RUNTIME_DIR'] == str(tmp_path / 'parent-runtime')
        assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    finally:
        # The pre-fix regression must not leave its leaked handlers in pytest.
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
