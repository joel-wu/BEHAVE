import json
from pathlib import Path

import pytest

from behave.eval import checkpoint as checkpoint_module
from behave.eval import checkpoint as queue
from tools import eval_checkpoint_study as study


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.mark.parametrize('name,args', [('score', ('root', 'entry', 3)), ('run', ('root',)), ('launch', ('root',))])
def test_study_operator_preserves_source_and_working_directory(tmp_path, monkeypatch, name, args):
    calls = []
    monkeypatch.chdir(tmp_path)
    sentinel = object()
    monkeypatch.setattr(queue, name, lambda *a, **kw: calls.append((a, kw)) or sentinel)
    assert getattr(study, name)(*args) is sentinel
    repository = Path(study.__file__).resolve().parents[1]
    options = {'cwd': repository}
    if name != 'launch':
        options['repository'] = repository
    assert calls == [(args, options)]


@pytest.mark.parametrize('entrypoint', ['operator', 'package'])
def test_submission_retains_frozen_entry_and_resource_recipe(tmp_path, monkeypatch, entrypoint):
    from behave.runtime import jobs

    config = fixture(tmp_path)
    entry = {'model_line': 'main', 'update': 5}
    write(tmp_path / 'queue.json', {'checkpoints': [entry]})
    calls = []
    monkeypatch.setattr(jobs, 'submit',
                        lambda *a, **kw: calls.append((a, kw)) or {'state': 'submitted'})
    if entrypoint == 'operator':
        result = study.submit_checkpoint(tmp_path, 0)
        expected_cwd = Path(study.__file__).resolve().parents[1]
    else:
        result = checkpoint_module.submit_checkpoint(tmp_path, 0, cwd=tmp_path)
        expected_cwd = tmp_path
    assert result == {'state': 'submitted'}
    (control, command), options = calls[0]
    assert control == study.control_directory(tmp_path, entry)
    assert '--jobid=123' in command and '--cpus-per-task=188' in command and '--gpus=8' in command
    bootstrap = command[command.index('-c') + 1]
    assert 'from tools.eval_checkpoint_study import node;' in bootstrap
    assert command[-4:] == [str(tmp_path / 'code/snapshot'), str(tmp_path / 'code/snapshot/src'),
                            str(tmp_path), '0']
    assert options['wall_seconds'] is None and options['cleanup_seconds'] == 300
    assert options['cwd'] == expected_cwd
    assert options['metadata']['effective_cpu_affinity'] == config['resources']['generation_cpu_ids']


def test_queue_launch_preserves_checkout_cwd_and_frozen_bootstrap(tmp_path, monkeypatch):
    from behave.runtime import jobs

    fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(jobs, 'submit', lambda *a, **kw: calls.append((a, kw)) or {'state': 'submitted'})
    assert study.launch(tmp_path) == {'state': 'submitted'}
    (control, command), options = calls[0]
    assert control == study.control_directory(tmp_path)
    assert options['cwd'] == Path(study.__file__).resolve().parents[1]
    assert options['wall_seconds'] is None and options['cleanup_seconds'] == 300
    assert 'from tools.eval_checkpoint_study import run;' in command[command.index('-c') + 1]
    assert command[-3:] == [str(tmp_path / 'code/snapshot'), str(tmp_path / 'code/snapshot/src'), str(tmp_path)]


@pytest.mark.parametrize('state', ['supervising', 'completed', 'failed'])
def test_queue_launch_does_not_replace_an_existing_control(tmp_path, monkeypatch, state):
    from behave.runtime import jobs

    fixture(tmp_path)
    write(study.control_directory(tmp_path) / 'launch.json', {})
    monkeypatch.setattr(jobs, 'status_of', lambda _: {'state': state})
    monkeypatch.setattr(jobs, 'submit', lambda *a, **kw: pytest.fail('duplicate queue launch'))
    assert study.launch(tmp_path) == {'state': state}


def fixture(root):
    from behave.serving.spec import ServingSpec
    config = {'setting':'joint', 'model':'fixture', 'inference_seeds':[17,3,42],
        'attempt':{'max_turns':60,'context_tokens':131072,'request_timeout_seconds':None},
        'output_preview_bytes':32768, 'inputs':{'tasks':1},
        'resources':{'tensor_parallel_per_engine':4,'engines':2,
            'device_groups':['0,1,2,3','4,5,6,7'],'generation_cpu_ids':list(range(96,148)),
            'allocation':123, 'rollout_workers':44,'generation_cpus':52,
            'scoring_workers':90,'scoring_cpus':90}}
    write(root / 'configuration.json', config)
    write(root / 'sampling.json', {'temperature': 1, 'top_p': .95, 'top_k': 20})
    write(root / 'code/snapshot.json', {'sha256': 'frozen-fixture'})
    write(root / 'task_index.json', {'objects': '/fixtures', 'release': {},
        'tasks': [{'task': 'one', 'dataset': 'fixture'}]})
    spec=ServingSpec(runtime='sglang',python='/fixture/python',package_version='0.5.18',
        torch_version='2.13.0',model_path='/fixture/weights',model_revision='original',
        served_model='fixture',reasoning_parser='qwen3',tool_parser='qwen3_coder',
        cuda_root='/fixture/cuda',lifetime_seconds=None)
    write(root / 'serving_template.json', {'spec':spec.as_dict()})
    return config


def test_serving_ports_reject_actual_ephemeral_range_before_socket_open(tmp_path):
    resources = {"lease_serving_ports": True, "engines": 1,
                 "port_first": 24000, "nccl_port_first": 25000}
    with pytest.raises(ValueError, match="ephemeral"), \
            study.serving_port_lease(resources, lock_root=tmp_path, ephemeral_range=(20000, 30000)):
        pytest.fail("must reject before starting worker")


def archived_weights(root):
    from behave.core.artifacts import BlobRef
    from behave.runtime.models import evict_model, publish_model
    from tools.slime_checkpoint_input import model_handoff
    source=root/'nfs'/'hf';source.mkdir(parents=True)
    for name in ('config.json','tokenizer.json','tokenizer_config.json'):
        (source/name).write_text('{}')
    (source/'weight.safetensors').write_bytes(b'fixture weights')
    checkpoint=BlobRef.of(b'checkpoint')
    handoff=model_handoff(source,checkpoint)
    receipt=publish_model(source,handoff['weights']['files'],checkpoint=checkpoint.as_dict(),
        staging=root/'packet',destination=root/'store')
    evict_model(source,receipt)
    return source,checkpoint,handoff,receipt


def test_cold_handoff_reads_only_metadata_and_preserves_identity(tmp_path,monkeypatch):
    from tools.slime_checkpoint_input import model_handoff
    source,checkpoint,handoff,_receipt=archived_weights(tmp_path)
    original=Path.open
    def no_payload(path,*a,**kw):
        assert path.suffix not in {'.tar','.safetensors'}
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'open',no_payload)
    assert model_handoff(source,checkpoint,materialize=False)==handoff
    saved=tmp_path/'handoff.json';write(saved,dict(handoff,step=4,checkpoint=checkpoint.as_dict()))
    assert study.resolve_checkpoint({'recorded_handoff':str(saved),'update':5})==study.read(saved)
    assert not source.exists()


@pytest.mark.parametrize('cold',[False,True])
def test_eval_stages_and_checks_exact_weight_handoff(tmp_path,cold):
    from behave.runtime.models import restore_model
    source,_checkpoint,handoff,_receipt=archived_weights(tmp_path)
    if not cold:restore_model(source)
    target=tmp_path/'scratch'/'weights';target.parent.mkdir()
    assert study.stage_weights(handoff,target)==target
    assert (target/'weight.safetensors').read_bytes()==b'fixture weights'
    assert source.exists()==(not cold)


def test_eval_staging_rejects_changed_model_revision(tmp_path):
    source,_checkpoint,handoff,_receipt=archived_weights(tmp_path)
    with pytest.raises(ValueError,match='differs'):
        study.stage_weights(dict(handoff,model_revision='changed'),tmp_path/'local')
    assert not source.exists()


def test_serving_port_lease_prevents_concurrent_owner_and_releases(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr(checkpoint_module.socket, "socket", MagicMock())
    resources = {"lease_serving_ports": True, "engines": 1,
                 "port_first": 24000, "nccl_port_first": 25000}
    with study.serving_port_lease(resources, lock_root=tmp_path), pytest.raises(BlockingIOError), \
            study.serving_port_lease(resources, lock_root=tmp_path):
        pytest.fail("duplicate port owner")
    with study.serving_port_lease(resources, lock_root=tmp_path):
        pass


def test_serving_port_preflight_rejects_existing_listener(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    sock = MagicMock()
    sock.return_value.__enter__.return_value.bind.side_effect = OSError("address already in use")
    monkeypatch.setattr(checkpoint_module.socket, "socket", sock)
    resources = {"lease_serving_ports": True, "engines": 1,
                 "port_first": 24000, "nccl_port_first": 25000}
    with pytest.raises(OSError, match="already in use"), study.serving_port_lease(resources, lock_root=tmp_path):
        pytest.fail("must reject before starting worker")


def test_serving_port_preflight_accepts_closed_connection_time_wait(tmp_path):
    import errno
    import socket

    with socket.socket() as listener, socket.socket() as spare:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        with socket.create_connection(("127.0.0.1", port)) as client:
            accepted, _ = listener.accept()
            accepted.close()  # Server actively closes, leaving TIME_WAIT.
            assert client.recv(1) == b""
        listener.close()
        spare.bind(("127.0.0.1", 0))
        nccl_port = spare.getsockname()[1]
        spare.close()
    with socket.socket() as strict_probe, pytest.raises(OSError) as error:
        strict_probe.bind(("0.0.0.0", port))
    assert error.value.errno == errno.EADDRINUSE
    resources = {"lease_serving_ports": True, "engines": 1,
                 "port_first": port, "nccl_port_first": nccl_port}
    with study.serving_port_lease(resources, lock_root=tmp_path, ephemeral_range=(1, 1023)):
        pass


def test_serving_port_reuse_still_rejects_real_listener(tmp_path):
    import errno
    import socket

    with socket.socket() as listener, socket.socket() as spare:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        spare.bind(("127.0.0.1", 0))
        resources = {"lease_serving_ports": True, "engines": 1,
                     "port_first": listener.getsockname()[1], "nccl_port_first": spare.getsockname()[1]}
        spare.close()
        with pytest.raises(OSError) as error, \
                study.serving_port_lease(resources, lock_root=tmp_path, ephemeral_range=(1, 1023)):
            pytest.fail("an active listener must never be reused")
        assert error.value.errno == errno.EADDRINUSE


def test_measurement_identity_and_explicit_tp4_seed(tmp_path):
    config = fixture(tmp_path)
    entry = {'model_line': 'rl-main', 'update': 5}
    handoff = {'model_path': '/fixture/weights', 'model_revision': 'weights-v1'}
    paths = []
    for seed in (17, 3, 42):
        output = study.prepare_measurement(tmp_path, entry, seed, handoff)
        paths.append(output)
        spec = study.read(output.parent / 'serving.json')['spec']
        assert spec['tensor_parallel'] == 4 and spec['seed'] == seed
        assert spec['context_length'] == 131072
        settings = study.read(output / 'settings.json')
        assert settings['attempt'] == config['attempt']
        assert not settings['score_on_submit']
        assert 'seed' not in study.read(output / 'sampling.json')
        assert study.prepare_measurement(tmp_path, entry, seed, handoff) == output
    assert len(set(paths)) == 3
    assert str(paths[0]).endswith('rl-main/update-005/eval-seed-17/rollout')
    with pytest.raises(ValueError, match='identity changed'):
        study.prepare_measurement(tmp_path, entry, 17, {**handoff, 'model_revision':'different'})


def test_existing_submission_is_not_relaunched(tmp_path, monkeypatch):
    from behave.runtime import jobs
    fixture(tmp_path)
    entry = {'model_line': 'base', 'update': 0}
    write(tmp_path / 'queue.json', {'checkpoints': [entry]})
    write(study.checkpoint_directory(tmp_path, entry) / 'control/launch.json', {})
    monkeypatch.setattr(jobs, 'status_of', lambda _: {'state':'failed'})
    monkeypatch.setattr(jobs, 'submit', lambda *a, **k: pytest.fail('duplicate launch'))
    assert study.submit_checkpoint(tmp_path, 0)['state'] == 'failed'


def test_checkpoint_first_seed_order_and_score_handoff(tmp_path, monkeypatch):
    from behave.runtime import jobs
    fixture(tmp_path)
    entries = [{'model_line':'base', 'update':0}, {'model_line':'rl-main','update':5}]
    write(tmp_path / 'queue.json', {'checkpoints':entries})
    monkeypatch.setattr(queue, 'resolve_checkpoint', lambda _: {'model_path':'/fixtures','model_revision':'v1'})
    calls=[]
    def prepare(root, entry, seed, handoff):
        calls.append((entry['model_line'],entry['update'],seed))
        write(study.measurement_directory(root, entry, seed) / 'generation.json', {'state':'completed'})
    monkeypatch.setattr(queue, 'prepare_measurement', prepare)
    monkeypatch.setattr(queue, 'submit_checkpoint', lambda *_, **kw: {})
    monkeypatch.setattr(jobs, 'status_of', lambda _: {'state':'completed'})
    scored=[]
    monkeypatch.setattr(queue, 'score', lambda root, entry, seed, **kw: scored.append((entry['model_line'],seed)) or True)
    assert queue.run(tmp_path, repository=tmp_path)==0
    assert calls==[(arm,update,seed) for arm,update in [('base',0),('rl-main',5)] for seed in [17,3,42]]
    assert len(scored)==6


def test_interruption_pauses_without_starting_next_checkpoint(tmp_path, monkeypatch):
    from behave.runtime import jobs
    fixture(tmp_path)
    write(tmp_path / 'queue.json', {'checkpoints':[{'model_line':'base','update':0}, {'model_line':'rl-main','update':5}]})
    monkeypatch.setattr(queue, 'resolve_checkpoint', lambda _: {'model_path':'/fixtures','model_revision':'v1'})
    monkeypatch.setattr(queue, 'prepare_measurement', lambda *args: None)
    launched=[]
    monkeypatch.setattr(queue, 'submit_checkpoint', lambda _, index, **kw: launched.append(index))
    monkeypatch.setattr(jobs, 'status_of', lambda _: {'state':'failed'})
    assert queue.run(tmp_path, repository=tmp_path)==1
    assert launched==[0]
    assert study.read(tmp_path / 'progress.json')['state']=='paused_for_recovery'


def test_existing_seed_is_not_regenerated(tmp_path, monkeypatch):
    from behave.runtime import jobs
    fixture(tmp_path)
    entry = {'model_line':'rl-main', 'update':5, 'inference_seeds':[3,42]}
    write(tmp_path / 'queue.json', {'checkpoints':[entry]})
    monkeypatch.setattr(queue, 'resolve_checkpoint', lambda _: {'model_path':'/fixture','model_revision':'v1'})
    requested=[]
    def prepare(root, entry, seed, handoff):
        requested.append(seed)
        write(study.measurement_directory(root, entry, seed) / 'generation.json', {'state':'completed'})
    monkeypatch.setattr(queue, 'prepare_measurement', prepare)
    monkeypatch.setattr(queue, 'submit_checkpoint', lambda *_, **kw: {})
    monkeypatch.setattr(jobs, 'status_of', lambda _: {'state':'completed'})
    monkeypatch.setattr(queue, 'score', lambda *_, **kw: True)
    assert queue.run(tmp_path, repository=tmp_path)==0
    assert requested==[3,42]


def test_completed_seed_keeps_identity_while_next_seed_uses_repaired_code(tmp_path, monkeypatch):
    from behave.runtime import jobs
    fixture(tmp_path)
    entry = {'model_line': 'rl-main', 'update': 5, 'inference_seeds': [3, 42]}
    write(tmp_path / 'queue.json', {'checkpoints': [entry]})
    completed = study.measurement_directory(tmp_path, entry, 3)
    write(completed / 'generation.json', {'state': 'completed'})
    write(completed / 'manifest.json', {'code': 'original-code'})
    monkeypatch.setattr(queue, 'resolve_checkpoint', lambda _: {'model_path': '/fixture', 'model_revision': 'v1'})
    requested = []

    def prepare(root, item, seed, handoff):
        assert seed == 42
        requested.append(seed)
        write(study.measurement_directory(root, item, seed) / 'generation.json', {'state': 'completed'})

    monkeypatch.setattr(queue, 'prepare_measurement', prepare)
    monkeypatch.setattr(queue, 'submit_checkpoint', lambda *_, **kw: {})
    monkeypatch.setattr(jobs, 'status_of', lambda _: {'state': 'completed'})
    scored = []
    monkeypatch.setattr(queue, 'score', lambda root, item, seed, **kw: scored.append(seed) or True)
    assert queue.run(tmp_path, repository=tmp_path) == 0
    assert requested == [42] and scored == [3, 42]
    assert study.read(completed / 'manifest.json') == {'code': 'original-code'}


def test_repaired_preparation_cannot_replace_executed_measurement(tmp_path):
    fixture(tmp_path)
    entry={'model_line':'base','update':0}
    handoff={'model_path':'/fixture','model_revision':'v1'}
    output=study.prepare_measurement(tmp_path,entry,3,handoff)
    write(tmp_path/'code/snapshot.json',{'sha256':'new-code'})
    study.prepare_measurement(tmp_path,entry,3,handoff)
    assert (output.parent/'preparations/frozen-fixture.json').exists()
    write(output.parent/'generation.json',{'state':'interrupted'})
    write(tmp_path/'code/snapshot.json',{'sha256':'third-code'})
    with pytest.raises(ValueError,match='identity changed'):
        study.prepare_measurement(tmp_path,entry,3,handoff)


def test_split_dispatch_preserves_shared_archive_and_code(tmp_path):
    archive = tmp_path / 'archive'
    lane = archive / 'dispatch/seed-syn'
    config = fixture(lane)
    write(lane / 'configuration.json', {**config, 'archive_root': str(archive),
        'code_directory': 'code/revisions/checked', 'launch_id': 'seed-syn-checked'})
    entry = {'model_line': 'seed-only', 'update': 6}
    assert study.checkpoint_directory(lane, entry) == archive / 'seed-only/update-006'
    assert study.snapshot_directory(lane) == archive / 'code/revisions/checked/snapshot'
    assert study.control_directory(lane) == lane / 'control/seed-syn-checked'


def test_completed_archive_never_reprepared_or_resubmitted(tmp_path, monkeypatch):
    fixture(tmp_path)
    entry = {'model_line': 'base', 'update': 0, 'inference_seeds': [3]}
    write(tmp_path / 'queue.json', {'checkpoints': [entry]})
    output = study.measurement_directory(tmp_path, entry, 3)
    write(output / 'generation.json', {'state': 'completed'})
    write(output / 'scoring.json', {'state': 'completed', 'output': str(output / 'scoring/baseline')})
    for name in ('resolve_checkpoint', 'prepare_measurement', 'submit_checkpoint', 'score'):
        monkeypatch.setattr(queue, name, lambda *a, **kw: pytest.fail('completed measurement reopened'))
    assert queue.run(tmp_path, repository=tmp_path) == 0


@pytest.mark.parametrize('score_failure', [False, True])
def test_next_stage_generates_before_previous_score_finishes(tmp_path, monkeypatch, score_failure):
    from threading import Event

    from behave.runtime import jobs

    roots = [tmp_path / name for name in ('recovery', 'next-checkpoint')]
    entries = [{'model_line': 'main', 'update': update, 'inference_seeds': [3]}
               for update in (5, 10)]
    for root, entry in zip(roots, entries):
        fixture(root)
        write(root / 'queue.json', {'checkpoints': [entry]})
    # The old generation is already archived; only its CPU score remains.
    write(queue.measurement_directory(roots[0], entries[0], 3) / 'generation.json',
          {'state': 'completed'})
    started, release = Event(), Event()
    scores, prepared = [], []

    def score(root, entry, seed, **kwargs):
        scores.append(entry['update'])
        if root == roots[0]:
            started.set()
            assert release.wait(5), 'GPU queue waited for the previous CPU stage'
            if score_failure:
                raise RuntimeError('scoring observer unavailable')
        return True

    def prepare(root, entry, seed, handoff):
        assert root == roots[1], 'completed recovery generation must not be repeated'
        assert started.wait(5)
        assert scores == [5], 'only one CPU scoring pass may occupy the resource budget'
        prepared.append(entry['update'])
        release.set()
        write(queue.measurement_directory(root, entry, seed) / 'generation.json', {'state': 'completed'})

    monkeypatch.setattr(queue, 'score', score)
    monkeypatch.setattr(queue, 'prepare_measurement', prepare)
    monkeypatch.setattr(queue, 'resolve_checkpoint', lambda _: {'model_path': '/fixtures', 'model_revision': 'v1'})
    monkeypatch.setattr(queue, 'submit_checkpoint', lambda *a, **kw: {})
    monkeypatch.setattr(jobs, 'status_of', lambda _: {'state': 'completed'})
    assert queue.run_stages(roots, repository=tmp_path, progress_directory=tmp_path) == int(score_failure)
    assert scores == ([5] if score_failure else [5, 10]) and prepared == [10]
    second = queue.read(roots[1] / 'progress.json')
    assert second['state'] == ('scoring_needs_recovery' if score_failure else 'completed')
    assert second['generation_completed']
    first = queue.read(roots[0] / 'progress.json')
    assert first['state'] == ('scoring_needs_recovery' if score_failure else 'completed')
    if score_failure:
        assert first['scoring_errors'] == ['RuntimeError: scoring observer unavailable']


def test_generation_failure_blocks_later_stages_not_completed_scores(tmp_path, monkeypatch):
    from behave.runtime import jobs

    roots = [tmp_path / name for name in ('first', 'second')]
    for root in roots:
        fixture(root)
        write(root / 'queue.json', {'checkpoints': [{'model_line': root.name, 'update': 5}]})
    monkeypatch.setattr(queue, 'resolve_checkpoint', lambda _: {'model_path': '/fixtures', 'model_revision': 'v1'})
    monkeypatch.setattr(queue, 'prepare_measurement', lambda *a: None)
    launched = []
    monkeypatch.setattr(queue, 'submit_checkpoint', lambda root, *a, **kw: launched.append(root))
    monkeypatch.setattr(jobs, 'status_of', lambda _: {'state': 'failed'})
    assert queue.run_stages(roots, repository=tmp_path, progress_directory=tmp_path) == 1
    assert launched == roots[:1]
    assert queue.read(tmp_path / 'progress.json')['paused_stage'] == str(roots[0])


def test_multi_stage_score_budget_must_match_before_launch(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ('first', 'second')]
    for root in roots:
        fixture(root)
    config = queue.read(roots[1] / 'configuration.json')
    config['resources']['scoring_workers'] += 1
    write(roots[1] / 'configuration.json', config)
    monkeypatch.setattr(queue, '_generate', lambda *a, **kw: pytest.fail('invalid budget launched'))
    with pytest.raises(ValueError, match='scoring resource budget'):
        queue.run_stages(roots, repository=tmp_path)


def test_multi_stage_shared_measurement_is_scored_only_once(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ('first', 'second')]
    entry = {'model_line': 'main', 'update': 5, 'inference_seeds': [3]}
    for root in roots:
        config = fixture(root)
        write(root / 'configuration.json', {**config, 'archive_root': str(tmp_path / 'archive')})
        write(root / 'queue.json', {'checkpoints': [entry]})
    write(queue.measurement_directory(roots[0], entry, 3) / 'generation.json', {'state': 'completed'})
    scores = []
    monkeypatch.setattr(queue, 'score', lambda *a, **kw: scores.append(a) or True)
    assert queue.run_stages(roots, repository=tmp_path) == 0
    assert len(scores) == 1


def test_unresolved_score_does_not_block_other_stages(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ('first', 'second')]
    for root in roots:
        fixture(root)
        entry = {'model_line': root.name, 'update': 5, 'inference_seeds': [3]}
        write(root / 'queue.json', {'checkpoints': [entry]})
        write(queue.measurement_directory(root, entry, 3) / 'generation.json', {'state': 'completed'})
    scores = []

    def score(root, *args, **kwargs):
        scores.append(root)
        return root == roots[1]

    monkeypatch.setattr(queue, 'score', score)
    assert queue.run_stages(roots, repository=tmp_path) == 1
    assert scores == roots
    assert queue.read(roots[1] / 'progress.json')['state'] == 'completed'


def test_serving_health_rejects_repetition_not_long_reasoning():
    from tools.eval_serving_probe import reply_health
    def raw(text):
        return json.dumps({'choices': [{'message': {'reasoning_content': text}, 'finish_reason': 'length'}]})
    assert not reply_health(raw('!' * 256))['healthy']
    assert reply_health(raw('I need to inspect the port definitions and implement the state machine. ' * 6))['healthy']
    assert not reply_health('{}')['healthy']


@pytest.mark.parametrize('statuses,complete', [
    (['scored', 'no_submission'], True),
    (['scored', 'no_result'], False),
    (['pending_judgment', 'scored'], False),
    (['scored'], False),
])
def test_scoring_quality_requires_resolved_complete_report(tmp_path, statuses, complete):
    summary = {'requested': 2, 'candidates': [{'status': status} for status in statuses],
        'no_result': statuses.count('no_result'),
        'pending_judgment': statuses.count('pending_judgment')}
    write(tmp_path/'progress-0.json', [{'state': 'completed', 'report': {'summary': summary}}])
    assert study.scoring_quality(tmp_path, 2)['complete'] is complete


def test_zero_exit_with_unresolved_scores_is_not_complete(tmp_path, monkeypatch):
    from behave.eval import scoring as scoring_module

    fixture(tmp_path)
    entry = {'model_line': 'rl-main', 'update': 5}
    directory = study.measurement_directory(tmp_path, entry, 3)
    output = directory/'scoring/baseline'
    write(output/'plan.json', {})
    write(output/'progress-0.json', [{'state': 'completed', 'report': {'summary': {
        'requested': 1, 'no_result': 1, 'candidates': [{'status': 'no_result'}]}}}])
    monkeypatch.setattr(scoring_module, 'launch', lambda *a, **k: None)
    monkeypatch.setattr(queue, 'wait_control', lambda *a, **k: {'state': 'completed', 'returncode': 0})
    assert study.score(tmp_path, entry, 3) is False
    receipt = study.read(directory/'scoring.json')
    assert receipt['state'] == 'needs_recovery'
    assert receipt['process_state'] == 'completed'
    assert receipt['quality']['no_result'] == 1


@pytest.mark.parametrize('cpu_ids', [None, list(range(90))])
@pytest.mark.parametrize('pass_name', ['baseline', 'startup-recovery'])
def test_checkpoint_scoring_passes_its_cpu_binding(tmp_path, monkeypatch, cpu_ids, pass_name):
    from behave.eval import scoring as scoring_module

    config = fixture(tmp_path)
    config['scoring_pass'] = pass_name
    if cpu_ids is not None:
        config['resources']['scoring_cpu_ids'] = cpu_ids
    write(tmp_path / 'configuration.json', config)
    entry = {'model_line': 'rl-main', 'update': 5}
    output = study.measurement_directory(tmp_path, entry, 3) / 'scoring' / pass_name
    write(output / 'plan.json', {})
    calls = []
    monkeypatch.setattr(scoring_module, 'launch', lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(queue, 'wait_control', lambda *a, **kw: {'state': 'completed'})
    monkeypatch.setattr(queue, 'scoring_quality', lambda *a: {'complete': True})
    assert study.score(tmp_path, entry, 3)
    assert calls[0][0] == (output, 123, 90)
    assert calls[0][1]['cpu_ids'] == cpu_ids


def test_checkpoint_scoring_rejects_overlapping_cpu_binding(tmp_path, monkeypatch):
    from behave.eval import scoring as scoring_module

    config = fixture(tmp_path)
    config['resources']['scoring_cpu_ids'] = list(range(80, 170))
    write(tmp_path / 'configuration.json', config)
    entry = {'model_line': 'rl-main', 'update': 5}
    output = study.measurement_directory(tmp_path, entry, 3) / 'scoring/baseline'
    write(output / 'plan.json', {})
    monkeypatch.setattr(scoring_module, 'launch', lambda *a, **kw: pytest.fail('overlapping CPU launch'))
    with pytest.raises(ValueError, match='must be disjoint'):
        study.score(tmp_path, entry, 3)


def test_dispatch_is_disjoint_and_reuses_completed_base(tmp_path):
    fixture(tmp_path)
    entries = [{'model_line': 'base', 'update': 0, 'inference_seeds': [3]},
        {'model_line': 'rl-main', 'update': 5, 'inference_seeds': [3, 42]},
        {'model_line': 'seed-only', 'update': 6, 'inference_seeds': [3, 42]}]
    write(tmp_path / 'queue.json', {'checkpoints': entries})
    done = study.measurement_directory(tmp_path, entries[0], 3)
    write(done / 'generation.json', {'state': 'completed'})
    write(done / 'scoring.json', {'state': 'completed', 'output': str(done / 'scoring/baseline')})
    manifest = study.prepare_dispatch(tmp_path, {
        'main': {'allocation': 11, 'model_lines': ['rl-main']},
        'seed': {'allocation': 12, 'model_lines': ['seed-only']}},
        code_directory='code', probe_request=tmp_path/'probe.json', launch_id='tp1',
        revisions={'rl-main/5': 'tp1-recovery'})
    assert len(manifest['measurements']) == 4
    assert len(manifest['completed_reused']) == 1
    keys = [tuple(row['key']) for row in manifest['measurements']]
    assert len(set(keys)) == 4
    main = Path(manifest['lanes']['main']['root'])
    config = study.read(main/'configuration.json')
    assert config['resources']['device_groups'] == [str(i) for i in range(8)]
    assert config['resources']['tensor_parallel_per_engine'] == 1
    entry = study.read(main/'queue.json')['checkpoints'][0]
    assert 'revisions/tp1-recovery/eval-seed-03' in str(study.measurement_directory(main, entry, 3))


def test_dispatch_rejects_duplicate_owner(tmp_path):
    fixture(tmp_path)
    write(tmp_path/'queue.json', {'checkpoints': []})
    with pytest.raises(ValueError, match='assigned twice'):
        study.prepare_dispatch(tmp_path, {
            'a': {'allocation': 11, 'model_lines': ['x']},
            'b': {'allocation': 12, 'model_lines': ['x']}},
            code_directory='code', probe_request=tmp_path/'probe', launch_id='bad')


def test_serving_preflight_binds_the_new_endpoint(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from behave.agent.providers import chat_worker
    from behave.agent.providers.chat_wire import request_body
    from behave.serving.spec import ServingSpec
    from tools import eval_serving_probe
    fixture(tmp_path)
    spec = ServingSpec.from_dict(study.read(tmp_path/'serving_template.json')['spec'])
    request = {'call_id': 'archived:0:0', 'turn': 0, 'dispatch': 0,
        'messages': [{'role': 'user', 'content': 'Inspect the interface.'}], 'tools': [],
        'model_policy': {'provider': 'old-provider', 'model': 'fixture'},
        'timeout_seconds': None, 'context_tokens': 131072}
    write(tmp_path/'probe.json', request)
    def exchange(endpoint, request):
        request_body(endpoint, request)
        assert 'seed' not in endpoint.parameters
        assert endpoint.parameters['max_tokens'] == 256
        assert request.timeout_seconds == (spec.startup_seconds or 1800)
        return SimpleNamespace(raw=json.dumps({'choices': [{'message': {'content': 'Inspecting the port definitions.'},
            'finish_reason': 'stop'}]}).encode())
    monkeypatch.setattr(chat_worker, 'exchange', exchange)
    assert eval_serving_probe.check_running_session(spec, tmp_path/'probe.json', {}, tmp_path/'health')


@pytest.mark.parametrize('exit_code,clean,requested,expected', [
    (0, True, 1, 0), (0, False, 1, 1), (7, True, 1, 7), (0, True, 0, 1),
])
@pytest.mark.parametrize('startup_option', [{}, {'isolation_startup_slots': None}, {'isolation_startup_slots': 4}])
def test_node_handoff_uses_maintained_worker_and_cleanup_witness(
        tmp_path, monkeypatch, exit_code, clean, requested, expected, startup_option):
    from contextlib import contextmanager, nullcontext

    from behave.core.lifecycle import CleanupState
    from behave.isolation import scratch as scratch_module
    from behave.isolation.startup import startup_slots
    from behave.runtime import jobs
    from behave.runtime import snapshots as snapshots_module

    config = fixture(tmp_path)
    config.update(startup_option)
    monkeypatch.setenv('BEHAVE_ISOLATION_STARTUP_SLOTS', '1')
    config['inference_seeds'] = [3]
    write(tmp_path / 'configuration.json', config)
    entry = {'model_line': 'main', 'update': 5}
    write(tmp_path / 'queue.json', {'checkpoints': [entry]})
    handoff = {'model_path': '/fixture/model', 'model_revision': 'recorded-revision'}
    write(study.checkpoint_directory(tmp_path, entry) / 'checkpoint.json', handoff)
    output = study.prepare_measurement(tmp_path, entry, 3, handoff)
    observed = []

    @contextmanager
    def scratch(*, cleanup, **kwargs):
        local = tmp_path / 'scratch'
        local.mkdir()
        try:
            yield local
        finally:
            observed.append(cleanup())

    def child(argv, log, stopped):
        assert startup_slots() == startup_option.get('isolation_startup_slots')
        assert 'from behave.eval.generation import worker;' in argv[5]
        assert 'from tools.' not in argv[5]
        assert 'from work.' not in argv[5]
        assert Path(argv[-1]) == output
        runtime = study.read(output / 'runtime_settings.json')
        assert runtime['isolation_startup_slots'] == startup_option.get('isolation_startup_slots')
        assert study.read(output / 'settings.json')['isolation_startup_slots'] == startup_option.get('isolation_startup_slots')
        assert runtime['devices'] == config['resources']['device_groups']
        assert runtime['staged_weights'] == str(tmp_path / 'scratch/weights')
        write(output / 'cleanup.json', {'cleanup_complete': clean})
        write(output / 'report.json', {'requested': requested, 'no_result': 0})
        return exit_code

    monkeypatch.setattr(scratch_module, 'ScratchDirectory', scratch)
    monkeypatch.setattr(checkpoint_module, 'serving_port_lease', lambda resources: nullcontext())
    monkeypatch.setattr(checkpoint_module, 'stage_weights', lambda *args: None)
    monkeypatch.setattr(snapshots_module, 'stage', lambda *args: None)
    monkeypatch.setattr(checkpoint_module.os, 'sched_setaffinity', lambda *args: None)
    monkeypatch.setattr(checkpoint_module.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(jobs, 'run_child', child)
    assert study.node(tmp_path, 0) == expected
    generation = study.read(output.parent / 'generation.json')
    assert generation['state'] == ('completed' if expected == 0 else 'interrupted')
    assert generation['cleanup_complete'] is clean
    assert observed == [CleanupState.COMPLETE if clean else CleanupState.UNKNOWN]
