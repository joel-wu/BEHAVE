"""Command-local cancellation and environment policy; no model or native job."""

import os
import signal
from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.core.records import parse_json
from behave.runtime.records import command_scope


@pytest.mark.parametrize('inherited', [None, '7'])
@pytest.mark.parametrize('slots', [None, 4])
@pytest.mark.parametrize('lane', ['score', 'generation'])
@pytest.mark.parametrize('outcome', ['success', 'cancel', 'exception'])
def test_eval_command_restores_startup_policy(tmp_path, publication, monkeypatch, capsys,
                                            inherited, slots, lane, outcome):
    from behave.core import process_limits
    from behave.eval import __main__ as entry
    from behave.isolation.startup import startup_slots

    name = 'BEHAVE_ISOLATION_STARTUP_SLOTS'
    if inherited is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, inherited)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    calls = []

    def execute(**kwargs):
        calls.append(kwargs)
        assert startup_slots() == slots
        assert not kwargs['cancelled']()
        if outcome == 'success':
            return SimpleNamespace(content=SimpleNamespace(as_dict=lambda: {'fixture': True}),
                                   head=SimpleNamespace(as_dict=dict))
        if outcome == 'exception':
            raise RuntimeError('fixture unexpected error')
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert kwargs['cancelled']()
        raise AttemptInterrupted('fixture-cancelled')

    monkeypatch.setattr('behave.task_execution.rejudge.rejudge_batch', execute)
    monkeypatch.setattr('behave.eval.runner.run_evaluation', execute)
    monkeypatch.setattr('behave.task_execution.batch.execution_status', lambda *a: {})
    monkeypatch.setattr(process_limits, 'configure_worker_process_limit', lambda: None)
    args = ['--task-objects', str(publication.root), '--run-directory', str(tmp_path / 'run')]
    if lane == 'generation':
        from test_evaluation import plan_for
        from test_task_workflow import STOP, setup

        from behave.task_execution.comparison import COMPARISON_BACKEND

        job, _ = setup(tmp_path, publication, STOP)
        plan = replace(plan_for(job), judge_backend=COMPARISON_BACKEND)
        path = tmp_path / 'plan.json'
        path.write_bytes(plan.to_bytes())
        args += ['--plan', str(path)]
    else:
        args += ['--score', 'fixture']
    if slots is not None:
        args += ['--isolation-startup-slots', str(slots)]
    if outcome == 'exception':
        with pytest.raises(RuntimeError, match='fixture unexpected error'):
            entry.main(args)
    else:
        assert entry.main(args) == (0 if outcome == 'success' else 1)
        row = parse_json(capsys.readouterr().out.encode())
        assert row == ({'summary': {'fixture': True}, 'head': {}} if outcome == 'success' else {
            'status': 'unavailable', 'code': 'fixture-cancelled', 'message': 'fixture-cancelled'})
    assert len(calls) == 1 and not (tmp_path / 'run').exists()
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    restored = os.environ.get(name)
    assert restored == inherited


@pytest.mark.parametrize('failure', [None, ValueError('fixture body failure'), KeyboardInterrupt()])
@pytest.mark.parametrize('signum', [signal.SIGINT, signal.SIGTERM])
def test_scope_restores_controls_on_every_exit(monkeypatch, failure, signum):
    name, untouched = 'BEHAVE_TEST_COMMAND_SCOPE', 'BEHAVE_TEST_COMMAND_OTHER'
    monkeypatch.setenv(name, '')
    monkeypatch.setenv(untouched, 'before')
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def execute():
        with command_scope(environment={name: 'inside'}) as cancelled:
            value = os.environ[name]
            assert not cancelled() and value == 'inside'
            signal.getsignal(signum)(signum, None)
            assert cancelled()
            os.environ[untouched] = 'after'
            if failure is not None:
                raise failure

    if failure is None:
        execute()
    else:
        with pytest.raises(type(failure)) as caught:
            execute()
        assert caught.value is failure
    values = (os.environ[name], os.environ[untouched])
    assert values == ('', 'after')
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers


def test_nested_scope_restores_outer_policy_and_cancellation(monkeypatch):
    name = 'BEHAVE_TEST_COMMAND_SCOPE'
    monkeypatch.delenv(name, raising=False)
    with command_scope(environment={name: 'outer'}) as outer:
        with command_scope(environment={name: 'inner'}) as inner:
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            assert inner() and not outer()
        value = os.environ[name]
        assert value == 'outer'
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert outer()
    present = name in os.environ
    assert not present


def test_partial_signal_installation_restores_environment_and_first_handler(monkeypatch):
    from behave.runtime import records

    name = 'BEHAVE_TEST_COMMAND_SCOPE'
    monkeypatch.delenv(name, raising=False)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    install = signal.signal

    def signal_change(signum, handler):
        if signum == signal.SIGTERM:
            raise ValueError('fixture second handler failed')
        return install(signum, handler)

    monkeypatch.setattr(records.signal, 'signal', signal_change)
    with pytest.raises(ValueError, match='second handler failed'), command_scope(environment={name: 'inside'}):
        pytest.fail('scope entered despite failed signal setup')
    present = name in os.environ
    assert not present
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers


def test_partial_environment_installation_restores_previous_values(monkeypatch):
    first, second = 'BEHAVE_TEST_COMMAND_FIRST', 'BEHAVE_TEST_COMMAND_SECOND'
    monkeypatch.setenv(first, 'before')
    monkeypatch.delenv(second, raising=False)
    with pytest.raises(ValueError), command_scope(environment={first: 'inside', second: '\0'}):
        pytest.fail('scope entered despite invalid environment')
    restored = os.environ[first]
    present = second in os.environ
    assert restored == 'before' and not present


@pytest.mark.parametrize('outcome', ['dry-run', 'success', 'cancel', 'exception'])
def test_training_cli_retains_preflight_and_restores_handlers(tmp_path, publication, monkeypatch, capsys, outcome):
    from test_corpus import current_plan, guarded_publication
    from test_training_rollouts import plan_at

    from behave.core import process_limits
    from behave.train import __main__ as entry

    plan = plan_at(tmp_path, publication)
    guarded_publication(publication)
    plan = current_plan(plan, publication)
    path, approved = tmp_path / 'plan.json', tmp_path / 'approved.json'
    path.write_bytes(plan.to_bytes())
    approved.write_bytes(publication.reader.read(plan.manifest))
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    calls = []

    def rollout(**kwargs):
        calls.append(kwargs)
        assert kwargs['plan'] == plan and kwargs['approved_manifest'] == plan.manifest
        assert not kwargs['cancelled']()
        if outcome == 'success':
            return {'fixture': True}
        if outcome == 'exception':
            raise RuntimeError('fixture unexpected error')
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert kwargs['cancelled']()
        raise AttemptInterrupted('fixture-cancelled')

    monkeypatch.setattr('behave.train.rollouts.run_rollouts', rollout)
    monkeypatch.setattr(process_limits, 'configure_worker_process_limit', lambda: None)
    args = ['--plan', str(path), '--approved-manifest', str(approved), '--task-objects', str(publication.root),
            '--run-directory', str(tmp_path / 'run')]
    if outcome == 'dry-run':
        args.append('--dry-run')
        monkeypatch.setattr('behave.runtime.records.command_scope',
                            lambda **kw: pytest.fail('dry-run installed process controls'))
    if outcome == 'exception':
        with pytest.raises(RuntimeError, match='fixture unexpected error'):
            entry.main(args)
    else:
        assert entry.main(args) == (1 if outcome == 'cancel' else 0)
        result = parse_json(capsys.readouterr().out.encode())
        if outcome == 'dry-run':
            assert result['status'] == 'validated' and result['optimizer'] is False
        elif outcome == 'cancel':
            assert result == {'status': 'unavailable', 'code': 'fixture-cancelled'}
        else:
            assert result == {'fixture': True}
    assert len(calls) == (0 if outcome == 'dry-run' else 1)
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
