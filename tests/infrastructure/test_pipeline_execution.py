"""External-method commands share preparation, execution and publication owners."""

import json
import os
import signal
from threading import Event
from types import SimpleNamespace

import pytest

from behave.runtime.records import write_json


@pytest.mark.parametrize('arguments', [[], ['--method', 'mage'], ['--template', 'template.json']])
def test_prepare_cli_requires_explicit_inputs_without_creating_output(tmp_path, arguments, capsys):
    from behave.pipelines.__main__ import main

    output = tmp_path / 'absent'
    with pytest.raises(SystemExit) as error:
        main(['prepare', str(output), *arguments])
    assert error.value.code == 2
    assert 'prepare requires --template and --method' in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize('method,score_only', [('joint_sim', 'False'), ('joint_sim', 'True'), ('mage', 'False')])
def test_review_worker_uses_shared_execution_without_changing_its_sequence(tmp_path, monkeypatch, method, score_only):
    from behave.eval import generation as generation_module
    from behave.eval import report as report_module
    from behave.eval import scoring as scoring_module
    from behave.pipelines import execution
    from tools import pipeline_review_trial

    write_json(tmp_path / 'settings.json', {'judge_workers': 2, 'judge_cpu_capacity': 3})
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '4')
    calls = []
    monkeypatch.setattr(generation_module, 'run', lambda *a, **k: calls.append(('solver', a, k)) or 0)
    monkeypatch.setattr(execution, 'rollout', lambda *a, **k: calls.append(('external', a, k)))
    monkeypatch.setattr(execution, 'score', lambda *a, **k: calls.append(('external-score', a, k)))
    monkeypatch.setattr(scoring_module, 'score_review', lambda *a, **k: calls.append(('solver-score', a, k)))
    monkeypatch.setattr(report_module, 'export_review', lambda *a, **k: calls.append(('review', a, k)))
    final, policy = tmp_path / 'final', tmp_path / 'policy.json'
    assert pipeline_review_trial.worker(method, tmp_path, str(final), str(policy), '4', score_only) == 0
    if method == 'joint_sim':
        expected = [] if score_only == 'True' else [('solver', (tmp_path,), {'status_output': final})]
        expected += [('solver-score', (tmp_path, 'baseline'),
                      {'workers': 2, 'judge_policy': policy, 'all_submitted': True, 'cpu_capacity': 3}),
                     ('review', (tmp_path,), {})]
    else:
        expected = [('external', (tmp_path,), {}), ('external-score', (tmp_path,), {'policy_file': policy})]
    assert calls == expected


@pytest.mark.parametrize('failure', ['archive', 'receipt'])
def test_failed_archive_commit_keeps_owned_scratch_and_restores_process(tmp_path, monkeypatch, failure):
    from behave.pipelines import execution

    output = tmp_path / 'output'
    write_json(output / 'settings.json', {})
    write_json(output / 'rollout_runtime.json', {'state': 'archived', 'local_directory': 'old-run'})
    monkeypatch.setenv('BEHAVE_SCRATCH_DIR', str(tmp_path))
    monkeypatch.setattr('behave.core.process_limits.configure_worker_process_limit', lambda *_: None)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    publish = execution.write_json

    def record(path, value, **options):
        if failure == 'receipt' and path.name == 'rollout_runtime.json' and value['state'] == 'archived':
            raise OSError('receipt unavailable')
        return publish(path, value, **options)

    def fail_archive(*args):
        raise OSError('archive unavailable')

    monkeypatch.setattr(execution, 'write_json', record)
    if failure == 'archive':
        monkeypatch.setattr(execution, 'archive_run', fail_archive)
    with pytest.raises(OSError, match=failure + ' unavailable'), execution.local_run(output, 'rollout') as pair:
        local, stopped = pair
        assert not stopped.is_set()
        assert not (local / 'rollout_runtime.json').exists()
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert stopped.is_set()
        write_json(local / 'result.json', {'paid_evidence': 'must retain'})
    assert os.environ['BEHAVE_SCRATCH_DIR'] == str(tmp_path)
    assert handlers == {sig: signal.getsignal(sig) for sig in handlers}
    assert local.is_dir()
    assert json.loads((local / 'result.json').read_text()) == {'paid_evidence': 'must retain'}
    runtime = json.loads((output / 'rollout_runtime.json').read_text())
    assert runtime['state'] == 'running' and runtime['local_directory'] == str(local)
    assert (output / 'result.json').exists() == (failure == 'receipt')


@pytest.mark.parametrize('pre_cancelled', [False, True])
def test_command_scope_preserves_the_callers_cancellation_event(pre_cancelled):
    from behave.runtime.records import command_scope

    stopped = Event()
    if pre_cancelled:
        stopped.set()
    with command_scope(stopped=stopped) as cancelled:
        assert cancelled() == pre_cancelled
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert cancelled() and stopped.is_set()
    assert stopped.is_set()


@pytest.mark.parametrize('sampling', ['upstream', 'provider_default'])
def test_rollout_keeps_method_policy_attempt_identity_and_per_task_errors(tmp_path, monkeypatch, sampling):
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.pipelines import configuration, execution, provider, workflow
    from behave.sim import api

    output, template = tmp_path / 'output', tmp_path / 'template.json'
    write_json(template, {'model': 'fixture', 'tasks': ['ok', 'broken'], 'workers': 2,
        'model_workers': 2, 'judge_workers': 1, 'attempt': {'max_turns': 3}})
    configuration.prepare(output, template, 'chipmate', provider_sampling=sampling)
    endpoint = LocalChatEndpoint('http://127.0.0.1:31000/v1/chat/completions', 'fixture', 'fixture',
                                 parameters={'temperature': 0.3, 'top_p': 0.6, 'top_k': 20})
    monkeypatch.setattr(execution, 'endpoint_for', lambda *a: endpoint)
    monkeypatch.setattr(execution, 'load_tasks',
                        lambda *a: (None, tuple(SimpleNamespace(task_id=name) for name in ('ok', 'broken'))))
    monkeypatch.setattr(api, 'prepare_tool_environment', lambda *_: None)
    monkeypatch.setattr('behave.core.process_limits.configure_worker_process_limit', lambda *_: None)
    monkeypatch.setenv('BEHAVE_SCRATCH_DIR', str(tmp_path))
    calls, endpoints = [], []

    def factory(value, **options):
        endpoints.append((value, options))
        return object()

    def run(**options):
        calls.append(options)
        if options['task'].task_id == 'broken':
            write_json(options['directory'] / 'evidence.json', {'keep': True})
            raise RuntimeError('fixture execution error')
        return {'task': 'ok', 'status': 'no_submission', 'submission': None}

    monkeypatch.setattr(provider, 'model_factory', factory)
    monkeypatch.setattr(workflow, 'run', run)
    monkeypatch.setattr(workflow, 'export_trace', lambda *a: 'retained trace')
    result = execution.rollout(output)
    assert [row['status'] for row in result] == ['no_submission', 'execution_error']
    assert result[0]['submission'] is None and result[1]['error'] == 'fixture execution error'
    assert len(calls) == len(endpoints) == 2
    parameters = {'top_k': 20}
    if sampling == 'upstream':
        parameters.update(temperature=1.0, top_p=0.95)
    assert all(dict(value.parameters) == parameters and opts == {'provider_sampling': sampling}
               for value, opts in endpoints)
    assert {call['spec'].attempt_id for call in calls} == {f'{output}-{name}' for name in ('ok', 'broken')}
    assert all(call['spec'].max_turns == 3 and call['method'] == 'chipmate' for call in calls)
    assert json.loads((output / 'rollout.json').read_text()) == result
    assert json.loads((output / 'candidates/broken/evidence.json').read_text()) == {'keep': True}
    assert (output / 'candidates/broken/error.json').exists()
    assert (output / 'candidates/ok/trace.md').read_text() == 'retained trace'
    assert json.loads((output / 'rollout_runtime.json').read_text())['state'] == 'archived'
    assert os.environ['BEHAVE_SCRATCH_DIR'] == str(tmp_path)


PIPELINE_INSTALL_PROBE = '''
import importlib.abc
import json
import os
import subprocess
import sys
from pathlib import Path
class NoOperators(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tools' or fullname.startswith('tools.'):
            raise AssertionError('installed pipeline imported a checkout operator: ' + fullname)
sys.meta_path.insert(0, NoOperators())
from behave.core.artifacts import BlobRef
from behave.pipelines import configuration, execution
from behave.runtime.records import write_json
from behave.eval.followup import prepare_review as prepare
root = Path(sys.argv[1])
root.mkdir()
os.environ['BEHAVE_SCRATCH_DIR'] = str(root)
template = root / 'template.json'
write_json(template, {'model': 'fixture', 'tasks': ['one', 'missing'], 'workers': 1, 'model_workers': 1,
    'judge_workers': 1, 'attempt': {'max_turns': 60}, 'release': BlobRef.of(b'unread').as_dict()})
try:
    configuration.prepare(root / 'invalid', template, 'mage', provider_sampling='unknown')
except ValueError as exc:
    assert 'sampling' in str(exc)
else:
    raise AssertionError('installed preparation accepted unknown sampling')
assert not (root / 'invalid').exists()
output = root / 'output'
subprocess.run([sys.executable, '-I', '-B', '-m', 'behave.pipelines', 'prepare', str(output),
                '--template', str(template), '--method', 'mage'], check=True)
assert json.loads((output / 'settings.json').read_text())['attempt_namespace'] == str(output)
with execution.local_run(output, 'rollout') as (local, stopped):
    assert not stopped.is_set() and local != output
    write_json(local / 'candidates/one/result.json', {'task': 'one', 'status': 'no_submission', 'submission': None})
assert not local.exists()
result = execution.score(output)
assert result == [{'task': 'one', 'status': 'no_submission'}, {'task': 'missing', 'status': 'rollout_unavailable'}]
assert json.loads((output / 'judgments/baseline/summary.json').read_text()) == result
assert json.loads((output / 'score_runtime.json').read_text())['state'] == 'archived'
assert 'behave.pipelines.mage_transport' not in sys.modules
subprocess.run([sys.executable, '-I', '-B', '-m', 'behave.pipelines', 'score', str(output),
                '--judgment', 'cli-score', '--workers', '1'], check=True)
assert json.loads((output / 'judgments/cli-score/summary.json').read_text()) == result
before = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
children = set(root.iterdir())
for options in ({'name': '../outside'}, {'name': ''}, {'workers': 0}, {'workers': False}):
    try:
        execution.score(output, **options)
    except ValueError:
        pass
    else:
        raise AssertionError('installed scoring accepted invalid boundary input')
assert before == {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
assert children == set(root.iterdir())
'''
