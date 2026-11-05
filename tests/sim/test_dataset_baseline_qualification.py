"""Qualification orchestration never guesses a score from partial evaluation."""
from types import SimpleNamespace

import pytest

from tools.qualify_dataset_baseline import decision_status, task_order


@pytest.mark.parametrize(('decision', 'status'), [
    (None, 'no-result'),
    (SimpleNamespace(authoritative=False, passed=False), 'no-result'),
    (SimpleNamespace(authoritative=False, passed=True), 'no-result'),
    (SimpleNamespace(authoritative=True, passed=False), 'scored-fail'),
    (SimpleNamespace(authoritative=True, passed=True), 'scored-pass'),
])
def test_only_public_authoritative_decision_supplies_score(decision, status):
    assert decision_status(decision) == status


@pytest.mark.parametrize('artifact_kind', ['rtl', 'behavior'])
@pytest.mark.parametrize('explicit_contract', [False, True])
def test_worker_without_run_manifest_reaches_public_runtime_scoring(tmp_path, monkeypatch, artifact_kind, explicit_contract):
    from behave.core import runtime_defaults
    from behave.sim import api
    from tests.sim.test_settled_protocol import contract as settled_contract
    from tests.sim.test_task_interface import write_interface
    from tools.qualify_dataset_baseline import worker

    directory = tmp_path / 'task'
    directory.mkdir()
    contract = settled_contract()
    write_interface(directory, contract)
    (directory / 'behavior.py').write_text('# execution is stubbed in this orchestration test\n')
    (directory / 'rtl.sv').write_text('module m; endmodule\n')
    calls = []

    def prepare(admitted, config, generation=None):
        assert generation is None
        return SimpleNamespace(command_request=command_request)

    def command_request(**options):
        assert options['run_analysis'] is False
        baseline = api.build_baseline(contract, policy=options['baseline'])
        assert baseline.generation_spec is None
        assert all(seed.replay_policy is not None for seed in baseline.stimuli)
        return SimpleNamespace(baseline=baseline)

    def score(command):
        calls.append(command)
        # No execution is fabricated: the stub deliberately has no decision.
        return SimpleNamespace(decision=None, evaluation=None, record_path=None,
                               as_dict=lambda: {'trace': list(range(1000)), 'decided': False})

    monkeypatch.setattr(api, 'load_reference', lambda *args, **kwargs: object())
    monkeypatch.setattr('tools.qualify_dataset_baseline.behavior_bundle', lambda folder, entry:
                        SimpleNamespace(source=(folder / entry).read_text(), dependencies={},
                                        file=entry, model='Model'))
    monkeypatch.setattr(api, 'prepare_rtl_bundle', lambda *args:
                        SimpleNamespace(source='module m; endmodule\n'))
    monkeypatch.setattr(api, 'realization_of_rtl_source', lambda *args, **kwargs: object())
    monkeypatch.setattr(api, 'realization_of_behavior_source', lambda *args, **kwargs: object())
    monkeypatch.setattr(api, 'admit', lambda *args, **kwargs: object())
    monkeypatch.setattr(api, 'prepare_rtl', prepare)
    monkeypatch.setattr(api, 'prepare_behavior', prepare)
    monkeypatch.setattr(api, 'handle_command', score)
    row = {'task': 'task', 'missing': [], 'rtl_top': 'm'}
    if explicit_contract:
        import hashlib
        import json

        raw = json.dumps({'task': contract.as_dict()}).encode()
        (tmp_path / 'runtime-contracts.json').write_bytes(raw)
        row.update(runtime_contract_file='runtime-contracts.json',
                   runtime_contract_file_sha256=hashlib.sha256(raw).hexdigest())

        def forbidden_legacy_lookup(*args, **kwargs):
            raise AssertionError('runtime qualification must not consult legacy declarations')

        monkeypatch.setattr(api, 'declarations_at', forbidden_legacy_lookup)
        monkeypatch.setattr(api, 'contract_of_task', forbidden_legacy_lookup)
    result = worker(tmp_path, tmp_path / 'work', row, artifact_kind)
    assert result['status'] == 'no-result'
    assert result['phase'] == 'score'
    assert len(calls) == 1
    assert result['generation_spec'] is None
    assert result['artifact_kind'] == artifact_kind
    assert result['contract_source'] == ('frozen-runtime-contract' if explicit_contract
                                         else 'task-interface')
    assert result['baseline']['schedule'] == 'runtime-request'
    assert result['baseline']['requested'] == runtime_defaults.SIM_BASELINE_CASES
    assert result['baseline']['requested_length_satisfied']
    assert not (directory / 'run.toml').exists()
    import gzip
    import json

    assert 'command_result' not in result
    assert result['schema'] == 'dataset-baseline-result/2'
    with gzip.open(tmp_path / 'work' / result['command_result_file'], 'rt') as stream:
        assert json.load(stream) == {'trace': list(range(1000)), 'decided': False}


@pytest.mark.parametrize('fault', ['changed-bytes', 'missing-task', 'invalid-image'])
def test_explicit_runtime_contract_is_fail_closed(tmp_path, fault):
    import hashlib
    import json

    from behave.sim import api
    from tests.sim.test_settled_protocol import contract as settled_contract
    from tools.qualify_dataset_baseline import frozen_runtime_contract

    image = {} if fault == 'invalid-image' else settled_contract().as_dict()
    raw = json.dumps({'other' if fault == 'missing-task' else 'task': image}).encode()
    (tmp_path / 'runtime.json').write_bytes(raw)
    row = {'task': 'task', 'runtime_contract_file': 'runtime.json',
           'runtime_contract_file_sha256': hashlib.sha256(raw).hexdigest()}
    if fault == 'changed-bytes':
        row['runtime_contract_file_sha256'] = '0' * 64
    expected = {'changed-bytes': ValueError, 'missing-task': KeyError,
                'invalid-image': api.Refused}[fault]
    with pytest.raises(expected):
        frozen_runtime_contract(tmp_path, row)


@pytest.mark.parametrize('keys', [('data/problems/example/t',), (), ('different/task',)])
def test_snapshot_freezes_complete_runtime_authority(tmp_path, monkeypatch, keys):
    import hashlib
    import json

    from tests.sim.test_settled_protocol import contract as settled_contract
    from tools import dataset_runtime_snapshot
    from tools.qualify_dataset_baseline import snapshot

    task_path = 'data/problems/example/t'
    task = tmp_path / task_path
    task.mkdir(parents=True)
    for name in ('spec.md', 'behavior.py', 'rtl.sv'):
        (task / name).write_text('')
    raw = json.dumps({key: settled_contract().as_dict() for key in keys}).encode()
    (tmp_path / 'contracts.json').write_bytes(raw)
    frozen = []

    def freeze(root, target, selections):
        frozen.extend(selections)
        target.mkdir()
        return {'files': {'contracts.json': hashlib.sha256(raw).hexdigest()}, 'digest': 'test'}

    monkeypatch.setattr(dataset_runtime_snapshot, 'freeze', freeze)
    if keys != (task_path,):
        with pytest.raises(ValueError, match='cover exactly'):
            snapshot(tmp_path, tmp_path / 'snapshot', 1, runtime_contracts='contracts.json')
        assert not frozen
        return
    plan = snapshot(tmp_path, tmp_path / 'snapshot', 1, runtime_contracts='contracts.json')
    assert 'contracts.json' in frozen
    row = plan['tasks'][0]
    assert row['runtime_contract_file'] == 'contracts.json'
    assert row['runtime_contract_file_sha256'] == hashlib.sha256(raw).hexdigest()


def test_round_robin_covers_categories_without_dropping_incomplete_tasks(tmp_path):
    for category, names in {'b': ['z', 'y'], 'a': ['q', 'p', 'r']}.items():
        for name in names:
            (tmp_path / category / name).mkdir(parents=True)
    (tmp_path / 'README.md').write_text('not a task')
    assert [p.relative_to(tmp_path).as_posix() for p in task_order(tmp_path)] == [
        'a/p', 'b/y', 'a/q', 'b/z', 'a/r']


def test_empty_inventory(tmp_path):
    assert task_order(tmp_path) == []


@pytest.mark.parametrize('manifest', [False, True])
def test_snapshot_optional_declarations_still_preserve_present_files(tmp_path, monkeypatch, manifest):
    from tools import dataset_runtime_snapshot
    from tools.qualify_dataset_baseline import snapshot

    task = tmp_path / 'data/problems/example/runtime-only'
    task.mkdir(parents=True)
    for name in ('spec.md', 'behavior.py', 'rtl.sv'):
        (task / name).write_text('')
    if manifest:
        (task / 'task.toml').write_text('')
    copied = []

    def freeze(root, target, selections):
        target.mkdir()
        copied.extend(selections)
        return {'files': {}, 'digest': 'test-digest'}

    monkeypatch.setattr(dataset_runtime_snapshot, 'freeze', freeze)
    plan = snapshot(tmp_path, tmp_path / 'snapshot', 1)
    assert plan['tasks'][0]['missing'] == []
    assert ('data/problems/example/runtime-only/task.toml' in copied) is manifest
    # A missing reference remains incomplete; only task-owned declarations are optional.
    (task / 'behavior.py').unlink()
    plan = snapshot(tmp_path, tmp_path / 'missing-reference', 1)
    assert plan['tasks'][0]['missing'] == ['behavior.py']


def test_behavior_candidate_does_not_replace_reference(tmp_path, monkeypatch):
    from behave.sim import api
    from tests.sim.test_settled_protocol import contract as settled_contract
    from tests.sim.test_task_interface import write_interface
    from tools.qualify_dataset_baseline import worker

    directory = tmp_path / 'task'
    directory.mkdir()
    write_interface(directory, settled_contract())
    (directory / 'behavior.py').write_text('# reference only\n')
    (directory / 'behavior2.py').write_text('# independent candidate\n')
    observed = {}

    def load_reference(adapter, sources, **options):
        observed['reference'] = sources['behavior.py']
        return object()

    def realization(source, *args, **options):
        observed['candidate'] = source
        return object()

    def prepare(admitted, config):
        observed['config'] = config
        raise RuntimeError('stop after checking candidate preparation')

    monkeypatch.setattr(api, 'load_reference', load_reference)
    monkeypatch.setattr('tools.qualify_dataset_baseline.behavior_bundle', lambda folder, entry:
                        SimpleNamespace(source=(folder / entry).read_text(), dependencies={},
                                        file=entry, model='Model'))
    monkeypatch.setattr(api, 'realization_of_behavior_source', realization)
    monkeypatch.setattr(api, 'admit', lambda *args, **kwargs: object())
    monkeypatch.setattr(api, 'prepare_behavior', prepare)
    row = {'task': 'task', 'missing': [], 'behavior_entry': 'behavior2.py'}
    result = worker(tmp_path, tmp_path / 'work', row, 'behavior')
    assert observed['reference'] == b'# reference only\n'
    assert observed['candidate'] == '# independent candidate\n'
    assert observed['config'].behavior_path == str(tmp_path / 'work/prepared.py')
    assert observed['config'].model == 'Model'
    assert result['candidate_entry'] == 'behavior2.py'
    assert result['status'] == 'harness-error'
    assert 'stop after checking candidate preparation' in result['error']


def test_bundle_uses_declared_model_and_preserves_local_helpers(tmp_path, monkeypatch):
    from behave.sim import api
    from tools.qualify_dataset_baseline import behavior_bundle

    (tmp_path / 'spec.md').write_text('## Behavior Interface\n\n```python\nclass Model:\n    pass\n```\n')
    (tmp_path / 'behavior.py').write_text('class Model: pass\n')
    (tmp_path / 'behavior2.py').write_text('from helper import value\nclass Model: pass\n')
    (tmp_path / 'helper.py').write_text('value = 5\n')
    observed = []
    monkeypatch.setattr(api, 'prepare_behavior_bundle', lambda bundle: observed.append(bundle) or bundle)
    bundle = behavior_bundle(tmp_path, 'behavior2.py')
    assert bundle.entry == 'behavior2.py'
    assert bundle.model == 'Model'
    assert bundle.files['helper.py'] == b'value = 5\n'
    assert bundle.files['behavior.py'] != bundle.files['behavior2.py']


def test_worker_preserves_unhandled_tool_execution_evidence(tmp_path, monkeypatch):
    import gzip
    import json

    from behave.isolation import IsolationStatus
    from behave.sim import api
    from behave.sim.rtl.tool_protocol import ToolExecution, ToolIsolationError
    from tools.qualify_dataset_baseline import worker

    execution = ToolExecution('elaborate', IsolationStatus.EXITED, 'policy', 'backend',
                              'workspace', 'job', exit_code=23, stderr_tail='original tool cause')

    def fail(*args, **kwargs):
        raise ToolIsolationError('rtl-tool-exited', 'the tool did not complete', execution)

    monkeypatch.setattr(api, 'declarations_at', fail)
    result = worker(tmp_path, tmp_path / 'work', {'task': 'task', 'missing': []})
    assert result['status'] == 'harness-error' and 'decision' not in result
    assert result['phase'] == 'declarations'
    assert result['tool_failure']['code'] == 'rtl-tool-exited'
    with gzip.open(tmp_path / 'work' / result['tool_failure']['evidence_file'], 'rt') as stream:
        assert json.load(stream) == [execution.as_dict()]


@pytest.mark.parametrize('previous', [None, {'status': 'harness-error', 'error': 'original cause'},
                                    {'status': 'scored-pass', 'elapsed_s': 1.25}])
@pytest.mark.parametrize('service_status', ['exited', 'timeout', 'cleanup-incomplete'])
def test_qualification_cleanup_preserves_the_published_result(
    tmp_path, monkeypatch, previous, service_status,
):
    import json

    from behave.core.lifecycle import CleanupState
    from behave.isolation.service import ServiceResult
    from tools import qualify_dataset_baseline as qualification

    row = {'task': 'data/problems/example/task'}
    work_root = tmp_path / 'work'
    work = work_root / 'example/task'
    calls = []
    lease = SimpleNamespace(cleanup=CleanupState.UNKNOWN)

    def launch(argv, **options):
        calls.append((argv, options))
        if previous is not None:
            qualification.write(work / 'result.json', previous)
        return ServiceResult(service_status, 0, b'output', b'error', 3.0, cleanup_elapsed_seconds=0.5)

    monkeypatch.setattr('behave.isolation.service.run_service', launch)
    monkeypatch.setattr('behave.agent.cpu_resources.current_cpu_lease', lambda: lease)
    monkeypatch.setattr(qualification.time, 'monotonic', lambda: 50.0)
    monkeypatch.setenv('PRIVATE_CREDENTIAL_FOR_TEST', 'must-not-be-forwarded')
    directory, log = qualification.run_one(tmp_path, work_root, row, 31, 1)
    result = json.loads((directory / 'result.json').read_text())
    assert calls[0][1]['deadline'] == 81
    assert calls[0][1]['output_bytes'] is None
    assert 'PRIVATE_CREDENTIAL_FOR_TEST' not in calls[0][1]['environment']
    assert calls[0][0][-2:] == ('--score-deadline', '81.0')
    assert log.read_bytes() == b'outputerror'
    if previous is None:
        assert result['status'] == 'no-result'
        assert result['reason'] == ('qualification-wall-timeout' if service_status == 'timeout'
                                    else 'worker-exited-without-result')
    else:
        assert {key: result[key] for key in previous} == previous
    assert result['supervision']['reason'] == service_status
    assert (result['supervision']['cleanup'] == 'complete') == (service_status != 'cleanup-incomplete')
    assert lease.cleanup.quiescent == (service_status != 'cleanup-incomplete')


def test_decided_score_does_not_hide_unconfirmed_qualification_cleanup():
    from tools.review_baseline_qualification import execution_gap

    result = {'decision': {'authority': {'authoritative': True, 'passed': True},
                           'evidence': {'complete': True}, 'failures': []},
              'supervision': {'cleanup': 'unconfirmed'}}
    assert execution_gap(result) == 'qualification-cleanup-unconfirmed'
    result['supervision']['cleanup'] = 'complete'
    assert execution_gap(result) is None


@pytest.mark.parametrize(('passed', 'mismatches', 'self_comparison', 'expected'), [
    (True, 0, False, None),
    (False, 1, False, None),  # A real B/R or B1/B2 mismatch is not a Sim defect.
    (False, 0, False, None),  # An observed candidate safety violation can also score zero.
    (False, 1, True, 'self-comparison-mismatch'),
])
def test_execution_acceptance_is_not_candidate_correctness(passed, mismatches, self_comparison, expected):
    from tools.review_baseline_qualification import execution_gap

    result = {'decision': {'authority': {'authoritative': True, 'passed': passed},
                          'evidence': {'complete': True, 'mismatch_count': mismatches}, 'failures': []}}
    assert execution_gap(result, self_comparison=self_comparison) == expected


@pytest.mark.parametrize(('decision', 'expected'), [
    (None, 'no-authoritative-decision'),
    ({'authority': {'authoritative': False}}, 'no-authoritative-decision'),
    ({'authority': {'authoritative': True}, 'evidence': {'complete': False}}, 'incomplete-baseline'),
    ({'authority': {'authoritative': True}, 'evidence': {'complete': True},
      'failures': [[0, {'origin': 'resource-policy'}]]}, 'non-candidate-execution-failure'),
])
def test_execution_acceptance_does_not_hide_missing_or_truncated_replay(decision, expected):
    from tools.review_baseline_qualification import execution_gap

    assert execution_gap({'decision': decision}) == expected


def test_execution_gate_requires_every_frozen_task_and_accepts_real_mismatch(tmp_path):
    import json

    from tools.review_baseline_qualification import review_execution_gate

    tasks = [{'task': f'data/problems/example/{name}', 'batch': 1} for name in ('a', 'b')]
    (tmp_path / 'plan.json').write_text(json.dumps({'tasks': tasks}))
    for index, row in enumerate(tasks):
        gate = review_execution_gate(tmp_path, [1])
        assert not gate['execution_complete']
        assert len(gate['gaps']) == 2 - index
        result = {'status': 'scored-pass' if index == 0 else 'scored-fail',
                  'decision': {'authority': {'authoritative': True, 'passed': index == 0},
                               'evidence': {'complete': True, 'mismatch_count': index}, 'failures': []}}
        path = tmp_path / 'tasks/example' / row['task'].rsplit('/', 1)[1] / 'result.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(result))
    gate = review_execution_gate(tmp_path, [1])
    assert gate['execution_complete']
    assert gate['counts'] == {'scored-pass': 1, 'scored-fail': 1}
    assert not review_execution_gate(tmp_path, [1], self_comparison=True)['execution_complete']
    assert not review_execution_gate(tmp_path, [99])['execution_complete']


def test_report_batches_do_not_serialize_work_and_resume_does_not_repeat(tmp_path, monkeypatch):
    import json
    import threading

    from tools import dataset_runtime_snapshot
    from tools.qualify_dataset_baseline import run, write

    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    tasks = [{'task': f'data/problems/example/{batch}', 'batch': batch} for batch in (1, 2)]
    write(checkout / 'plan.json', {'runtime_digest': 'frozen', 'tasks': tasks})
    tool = tmp_path / 'eda/libexec/behave-rtl-frontend'
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b'test tool identity')
    monkeypatch.setenv('BEHAVE_EDA_ROOT', str(tmp_path / 'eda'))
    monkeypatch.delenv('BEHAVE_RUNTIME_DIR', raising=False)
    monkeypatch.setattr(dataset_runtime_snapshot, 'verify', lambda root: None)
    second_started = threading.Event()
    calls = []

    def fake_run_one(root, work_root, row, seconds, cpus, kind):
        import os

        from behave.agent.cpu_resources import current_cpu_lease
        from behave.core.lifecycle import CleanupState

        assert os.environ['BEHAVE_RUNTIME_DIR'] == str(checkout.parent / 'runtime')
        lease = current_cpu_lease()
        assert len(lease.cpus) == cpus
        assert os.sched_getaffinity(0) == set(lease.cpus)
        lease.cleanup = CleanupState.COMPLETE
        calls.append(row['task'])
        if row['batch'] == 1:
            assert second_started.wait(5), 'batch 2 must start before batch 1 completes'
        else:
            second_started.set()
        work = work_root / str(row['batch'])
        write(work / 'result.json', dict(row, status='scored-pass', elapsed_s=1, phase='score'))
        (work / 'command-result.json.gz').write_bytes(b'already-compressed-record')
        (work / 'tool-failure.json.gz').write_bytes(b'original-tool-evidence')
        log = work.with_suffix('.log')
        log.write_text('completed')
        return work, log

    monkeypatch.setattr('tools.qualify_dataset_baseline.run_one', fake_run_one)
    output = tmp_path / 'results'
    run(checkout, tmp_path / 'work', output, [1, 2], 2, 1, 30)
    assert json.loads((output / 'launch.json').read_text())['runtime_directory'] == str(
        checkout.parent / 'runtime')
    assert len(calls) == 2
    for batch in (1, 2):
        result = json.loads((output / f'batch-{batch:03}.json').read_text())
        assert result['completed'] == result['expected'] == 1
        assert (output / f'tasks/example/{batch}/worker.log').is_file()
        assert (output / f'tasks/example/{batch}/command-result.json.gz').read_bytes() == b'already-compressed-record'
        assert (output / f'tasks/example/{batch}/tool-failure.json.gz').read_bytes() == b'original-tool-evidence'
    # Concurrency may change; the frozen inputs and receipts do not.
    run(checkout, tmp_path / 'resume-work', output, [1, 2], 4, 1, 30, resume=True)
    assert len(calls) == 2
    with pytest.raises(FileExistsError, match='--resume'):
        run(checkout, tmp_path / 'extra-work', output, [1, 2], 2, 1, 30)
    write(checkout / 'plan.json', {'runtime_digest': 'changed', 'tasks': tasks})
    with pytest.raises(ValueError, match='same frozen plan'):
        run(checkout, tmp_path / 'changed-work', output, [1, 2], 2, 1, 30, resume=True)


def test_explicit_entries_are_frozen_inputs_not_first_module_guesses(tmp_path, monkeypatch):
    import json

    from tools import dataset_runtime_snapshot
    from tools.qualify_dataset_baseline import snapshot

    root, destination = tmp_path / 'root', tmp_path / 'snapshot'
    task = root / 'data/problems/example/multiple-roots'
    task.mkdir(parents=True)
    for name in ('spec.md', 'behavior.py', 'rtl.sv', 'task.toml'):
        (task / name).write_text('')
    (root / 'entries.json').write_text(json.dumps({'data/problems/example/multiple-roots': 'Selected'}))
    copied = []

    def freeze(_, target, selections):
        target.mkdir()
        copied.extend(selections)
        return {'files': {}, 'digest': 'test-digest'}

    monkeypatch.setattr(dataset_runtime_snapshot, 'freeze', freeze)
    plan = snapshot(root, destination, 50, rtl_entries='entries.json')
    assert 'entries.json' in copied
    assert plan['tasks'][0]['rtl_top'] == 'Selected'
    assert plan['rtl_entries'] == 'entries.json'
    (root / 'entries.json').write_text(json.dumps({'data/problems/missing': 'Selected'}))
    with pytest.raises(ValueError, match='outside this snapshot'):
        snapshot(root, tmp_path / 'invalid', 50, rtl_entries='entries.json')


@pytest.mark.parametrize('override', [None, 61])
def test_qualification_deadline_uses_runtime_default_or_explicit_override(tmp_path, monkeypatch, override):
    import sys

    from behave.core import runtime_defaults
    from tools import qualify_dataset_baseline as qualification

    monkeypatch.setattr(runtime_defaults, 'SIM_BASELINE_QUALIFICATION_SECONDS', 4321)
    calls = []
    monkeypatch.setattr(qualification, 'run', lambda *args, **kwargs: calls.append(args))
    arguments = ['qualify_dataset_baseline.py', 'run', str(tmp_path),
                 str(tmp_path / 'work'), str(tmp_path / 'out'),
                 '--batches', '1', '--workers', '2', '--cpus-per-task', '2']
    if override is not None:
        arguments += ['--task-seconds', str(override)]
    monkeypatch.setattr(sys, 'argv', arguments)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    qualification.main()
    assert len(calls) == 1
    assert calls[0][-1] == (4321 if override is None else override)


def test_paired_lanes_share_preparation_and_run_concurrently(tmp_path, monkeypatch):
    import json
    import threading

    from tools import qualify_dataset_baseline as qualification

    prepared = qualification.PreparedQualification(
        {'runtime_digest': 'frozen', 'tasks': [{'batch': 1, 'behavior_entry': 'behavior2.py'}]},
        tmp_path / 'runtime', 'frontend')
    preparations = []
    monkeypatch.setattr(qualification, '_prepare_qualification',
                        lambda root: preparations.append(root) or prepared)
    monkeypatch.setattr(qualification.os, 'sched_getaffinity', lambda _: set(range(6)))
    monkeypatch.setattr(qualification.os, 'sched_setaffinity', lambda *args: None)
    barrier = threading.Barrier(2)
    calls = []

    def lane(checkout, work, output, batches, workers, cpus, seconds, **kwargs):
        assert kwargs['prepared'] is prepared
        assert workers * cpus == (4 if kwargs['artifact_kind'] == 'rtl' else 2)
        barrier.wait(timeout=5)
        calls.append((kwargs['artifact_kind'], kwargs['resume']))

    monkeypatch.setattr(qualification, '_run_lane', lane)
    arguments = {'rtl_workers': 2, 'behavior_workers': 2, 'rtl_cpus': 2, 'behavior_cpus': 1, 'seconds': 120}
    output = tmp_path / 'results'
    qualification.run_pairs(tmp_path, tmp_path / 'work', output, [1], **arguments)
    assert preparations == [tmp_path]
    assert sorted(calls) == [('behavior', False), ('rtl', False)]
    scope = json.loads((output / 'scope.json').read_text())
    assert scope['requested_cpus'] == 6
    assert scope['behavior_reference'] == 'behavior.py'
    assert scope['behavior_candidate'] == 'behavior2.py'
    assert scope['search_seconds'] == 0 and not scope['analysis']
    assert len(json.loads((output / 'lanes.json').read_text())) == 2
    with pytest.raises(FileExistsError, match='--resume'):
        qualification.run_pairs(tmp_path, tmp_path / 'work', output, [1], **arguments)
    qualification.run_pairs(tmp_path, tmp_path / 'resume', output, [1], resume=True, **arguments)
    assert sorted(calls[-2:]) == [('behavior', True), ('rtl', True)]
    with pytest.raises(ValueError, match='same frozen qualification'):
        qualification.run_pairs(tmp_path, tmp_path / 'work', output, [2], resume=True, **arguments)


def test_paired_lanes_reject_oversubscription_before_preparing(tmp_path, monkeypatch):
    from tools import qualify_dataset_baseline as qualification

    monkeypatch.setattr(qualification.os, 'sched_getaffinity', lambda _: {0, 1})
    monkeypatch.setattr(qualification, '_prepare_qualification',
                        lambda _: pytest.fail('must not prepare an oversubscribed launch'))
    with pytest.raises(ValueError, match='requests 3 CPUs'):
        qualification.run_pairs(tmp_path, tmp_path / 'work', tmp_path / 'output', [1],
                                rtl_workers=1, behavior_workers=1, rtl_cpus=2,
                                behavior_cpus=1, seconds=120)


def test_paired_cli_uses_shared_runtime_deadline(tmp_path, monkeypatch):
    import sys

    from behave.core import runtime_defaults
    from tools import qualify_dataset_baseline as qualification

    monkeypatch.setattr(runtime_defaults, 'SIM_BASELINE_QUALIFICATION_SECONDS', 4321)
    calls = []
    monkeypatch.setattr(qualification, 'run_pairs', lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, 'argv', ['qualification', 'run-pairs', str(tmp_path),
        str(tmp_path / 'work'), str(tmp_path / 'output'), '--batches', '1', '2',
        '--rtl-workers', '2', '--behavior-workers', '2', '--rtl-cpus', '2', '--behavior-cpus', '1'])
    monkeypatch.setattr(sys, 'path', list(sys.path))
    qualification.main()
    assert calls == [{'rtl_workers': 2, 'behavior_workers': 2, 'rtl_cpus': 2, 'behavior_cpus': 1,
                     'seconds': 4321, 'resume': False}]


def test_cli_loads_deadline_from_selected_snapshot_not_live_installation(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    package = tmp_path / 'src' / 'behave'
    (package / 'core').mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'core' / '__init__.py').write_text('')
    (package / 'core' / 'runtime_defaults.py').write_text('SIM_BASELINE_QUALIFICATION_SECONDS = 4321\n')
    tool = Path(__file__).resolve().parents[2] / 'tools' / 'qualify_dataset_baseline.py'
    script = """
import runpy, sys
namespace = runpy.run_path(sys.argv[1])
assert 'behave' not in sys.modules
def checked_run(*args, **kwargs):
    from behave.core import runtime_defaults
    assert args[-1] == 4321
    assert str(runtime_defaults.__file__).startswith(sys.argv[2])
namespace['main'].__globals__['run'] = checked_run
sys.argv = ['qualification', 'run', sys.argv[2], 'work', 'output',
            '--batches', '1', '--workers', '1', '--cpus-per-task', '1']
namespace['main']()
"""
    completed = subprocess.run([sys.executable, '-c', script, str(tool), str(tmp_path)],
                               capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr


def test_review_keeps_resource_score_distinct_from_output_mismatch():
    from tools.review_baseline_qualification import review_task

    failure = {"origin": "resource-policy", "phase": "candidate-run",
               "disposition": "score-zero", "reason": {"kind": "rtl-artifact-timed-out",
                   "why": "replay deadline", "detail": {"seconds": 65,
                   "tool_execution": {"stderr": "large log stays in its receipt"}}}}
    result = {"task": "task", "status": "scored-fail", "phase": "score", "elapsed_s": 80,
              "evaluation": {"passed": False, "mismatch_count": 0},
              "replay": {"runs": [{"phase": "continuous", "failure": failure}]}}
    row = review_task(result)
    assert row["status"] == "scored-fail"
    assert row["evaluation"]["mismatch_count"] == 0
    assert row["run_failures"][0]["origin"] == "resource-policy"
    assert row["run_failures"][0]["seconds"] == 65
    assert "large log" not in str(row)


def test_review_preserves_verifier_refusal_without_inventing_a_score():
    from tools.review_baseline_qualification import review_task

    result = {"task": "task", "status": "no-result", "phase": "prepare", "elapsed_s": 1,
              "refusal": {"kind": "native-events-source-mapping-unavailable",
                          "why": "unavailable", "detail": {"detail": "explicit control"}}}
    row = review_task(result)
    assert row["status"] == "no-result" and row["evaluation"] is None
    assert row["failures"][0]["detail"] == "explicit control"
    assert row["run_failures"] == []
