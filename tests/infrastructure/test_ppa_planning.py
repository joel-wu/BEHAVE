"""One editable experiment recipe feeds preparation, search and later compare."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_ppa import experiment, fixture_task

from behave.ppa.planning import build_experiment, gated_deltanet_rows, nvfp4_rows, read_study


def study():
    measurement = experiment().as_dict()
    measurement.pop('schema')
    measurement.pop('workloads')
    return {'schema': 'behave.ppa-study/1', 'task': 'identity',
        'workloads': [{'name': 'stream', 'generator': 'explicit', 'inputs': [[3], [7], [11], [19]]}],
        'measurement': measurement,
        'search': {'replicates': 3, 'parallelism': 2, 'turns': 60, 'request_seconds': None,
                   'replicate_seconds': 3600, 'final_seconds': 3600},
        'checks': {'baseline_cases': 2, 'sequence_length': 4}}


def test_explicit_recipe_preserves_existing_frozen_experiment():
    _, _, contract = fixture_task()
    assert build_experiment(study(), contract) == experiment()


@pytest.mark.parametrize('field', ['request_seconds', 'replicate_seconds', 'final_seconds'])
def test_study_allows_optional_wall_deadlines(tmp_path, field):
    recipe = study()
    recipe['search'][field] = None
    path = tmp_path / 'study.json'
    path.write_text(json.dumps(recipe))
    assert read_study(path)['search'][field] is None


@pytest.mark.parametrize('field', ['replicate_seconds', 'final_seconds'])
@pytest.mark.parametrize('value', [0, -1, True])
def test_study_rejects_invalid_optional_wall_deadlines(tmp_path, field, value):
    recipe = study()
    recipe['search'][field] = value
    path = tmp_path / 'study.json'
    path.write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match='invalid search setting'):
        read_study(path)


def test_nvfp4_recipe_matches_qualified_workload_and_preserves_prefix():
    import random
    import struct

    rng = random.Random(91032)
    expected = tuple({**{f'in_x{i}': struct.unpack('>I', struct.pack('>f', rng.uniform(-6, 6)))[0] >> 16
                         for i in range(16)}, 'in_global_scale': 0x3f400000} for _ in range(64))
    assert nvfp4_rows(64, 91032, .75) == expected
    assert nvfp4_rows(128, 91032, .75)[:64] == expected
    assert nvfp4_rows(32, 91032, .75) == expected[:32]


def test_nvfp4_recipe_uses_contract_port_order():
    from types import SimpleNamespace

    names = ['in_global_scale', *(f'in_x{i}' for i in reversed(range(16)))]
    ports = tuple(SimpleNamespace(port=name, type=SimpleNamespace(width=32 if name == 'in_global_scale' else 16))
                  for name in names)
    # Endpoint shape validation remains in the real performance service. This
    # fixture isolates conversion of the named generator to contract field order.
    from unittest.mock import patch

    from behave.ppa import performance
    contract = SimpleNamespace(inputs=lambda: (SimpleNamespace(fields=ports),),
                               clocks=(SimpleNamespace(clock='clk', reset_hold_cycles=2),))
    recipe = study()
    recipe['workloads'] = [{'name': 'blocks64', 'generator': 'nvfp4', 'blocks': 64,
                            'seed': 91032, 'global_scale': .75}]
    with patch.object(performance, 'endpoints'):
        built = build_experiment(recipe, contract)
    expected = nvfp4_rows(64, 91032, .75)
    assert built.workloads[0].inputs == tuple(tuple(row[name] for name in names) for row in expected)
    assert built.workloads[0].reset_cycles == 2
    ports[0].type.width = 16
    with pytest.raises(ValueError, match='NVFP4 input interface'):
        build_experiment(recipe, contract)


@pytest.mark.parametrize('scale', [0, -1, True, '0.75', float('nan'), float('inf'), 1e-40])
def test_nvfp4_recipe_rejects_non_normal_performance_scale(scale):
    with pytest.raises(ValueError, match='global scale'):
        nvfp4_rows(64, 91032, scale)


def test_length_and_sequence_boundary_are_independent():
    original = gated_deltanet_rows(16, 8)
    longer = gated_deltanet_rows(32, 8)
    uninterrupted = gated_deltanet_rows(32, 32)
    assert original == longer[:16]
    assert [i for i, row in enumerate(longer) if row['in_start']] == [0, 8, 16, 24]
    assert [i for i, row in enumerate(uninterrupted) if row['in_start']] == [0]
    assert gated_deltanet_rows(5, 5, key_lanes=3, value_lanes=2)[0]['in_q'].bit_length() <= 96


@pytest.mark.parametrize('tokens,sequence', [(0, 8), (16, 0), (True, 8)])
def test_bad_recipe_length_rejected(tokens, sequence):
    with pytest.raises(ValueError):
        gated_deltanet_rows(tokens, sequence)


def test_real_gated_recipe_compiles_from_published_contract():
    from behave.core.artifacts import DirectoryReader
    from behave.core.records import parse_json
    from behave.data.published import load_published_task
    from behave.ppa.records import release_reference
    from behave.sim import api

    root = Path(__file__).resolve().parents[2]
    release = root / 'results/data/releases/authored600_v2_train_20260919'
    if not release.is_dir():
        pytest.skip('local published dataset not installed')
    config = read_study(root / 'configs/ppa/gated_deltanet_preflight.json')
    loaded = load_published_task(DirectoryReader(release / 'objects'),
                                release_reference(release / 'release.json'), config['task'])
    contract = api.TaskContract.from_dict(parse_json(loaded.judge.contract.data))
    result = build_experiment(config, contract)
    names = [f.name for f in contract.inputs()[0].fields]
    assert result.timing.period_ns == 20
    assert len(result.workloads[0].inputs) == 16
    assert [i for i, row in enumerate(result.workloads[0].inputs)
            if row[names.index('in_start')]] == [0, 8]
    changed = deepcopy(config)
    changed['workloads'][0].update(tokens=64, sequence_tokens=64)
    extended = build_experiment(changed, contract)
    assert len(extended.workloads[0].inputs) == 64
    assert extended.identity != result.identity
    assert config['workloads'][0]['tokens'] == 16


@pytest.mark.parametrize('independent_final', [False, True])
@pytest.mark.parametrize('final_seconds', [None, 18000])
def test_prepare_cli_never_constructs_native_or_model_and_is_idempotent(tmp_path, monkeypatch, capsys, independent_final, final_seconds):
    from test_ppa_cli import published

    import behave.ppa.__main__ as cli
    import tools.ppa_campaign as campaign

    published(tmp_path)
    config = tmp_path / 'study.json'
    recipe = study()
    if independent_final:
        recipe['final_checks'] = {'baseline_cases': 50, 'sequence_length': 100}
    if final_seconds is not None:
        recipe['final_service_seconds'] = final_seconds
    config.write_text(json.dumps(recipe))
    methods = tmp_path / 'methods.json'
    methods.write_text(json.dumps({'methods': [{'name': 'one', 'provider': str(tmp_path / 'provider.json'),
        'prompt': str(tmp_path / 'prompt.txt'), 'behavior_source': 'provided'}]}))

    def no_execution(**kwargs):
        raise AssertionError('prepare must not initialize measurement tools')

    monkeypatch.setattr("behave.ppa.worker.NativePPA", no_execution)
    args = ['prepare', '--study', str(config), '--objects', str(tmp_path / 'inputs'),
        '--release', str(tmp_path / 'release.json'), '--output', str(tmp_path / 'prepared'),
        '--methods', str(methods), '--liberty', 'fixture.lib', '--sta', 'fixture-sta']
    assert cli.main(args) == 0
    before = capsys.readouterr().out
    assert cli.main(args) == 0
    assert capsys.readouterr().out == before
    plan = campaign.validate_plan(json.loads((tmp_path / 'prepared/plan.json').read_text()))
    from behave.task_execution.judge_policy import JudgePolicy
    policy = JudgePolicy.from_bytes(Path(plan['judge_policy']).read_bytes())
    assert (policy.baseline_cases, policy.sequence_length) == (2, 4)
    assert policy.search_seconds == 0 and policy.analysis is None and policy.max_retries == 0
    assert policy.score_timeout_seconds == experiment().service_seconds
    assert '--judge-policy' in campaign.common_arguments(plan)
    if independent_final or final_seconds is not None:
        final_policy = JudgePolicy.from_bytes(Path(plan['final_judge_policy']).read_bytes())
        assert (final_policy.baseline_cases, final_policy.sequence_length) == ((50, 100) if independent_final else (2, 4))
        assert final_policy.search_seconds == 0 and final_policy.analysis is None
        assert final_policy.score_timeout_seconds == (final_seconds or experiment().service_seconds)
        assert campaign.common_arguments(plan)[-1] == plan['judge_policy']
        assert campaign.common_arguments(plan, final=True)[-1] == plan['final_judge_policy']
    if final_seconds is not None:
        original = json.loads(Path(plan['experiment']).read_text())
        final = json.loads(Path(plan['final_experiment']).read_text())
        assert final == {**original, 'service_seconds': final_seconds}
        for is_final in (False, True):
            arguments = campaign.common_arguments(plan, final=is_final)
            assert arguments[arguments.index('--experiment') + 1] == plan['final_experiment' if is_final else 'experiment']
    assert plan['replicates'] == 3 and plan['turns'] == 60
    assert len(campaign.slots(plan, tmp_path)) == 3
    modified = study()
    modified['workloads'][0]['inputs'].append([23])
    config.write_text(json.dumps(modified))
    assert cli.main(args) == 1
    error = json.loads(capsys.readouterr().out)
    assert error['status'] == 'unavailable' and 'new output directory' in error['message']


@pytest.mark.parametrize('seconds', [0, -1, True, None, 1.5])
def test_final_service_budget_requires_positive_seconds(tmp_path, seconds):
    recipe = study()
    recipe['final_service_seconds'] = seconds
    path = tmp_path / 'study.json'
    path.write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match='final_service_seconds'):
        read_study(path)


@pytest.mark.parametrize('change', [
    {'name': '../escape'}, {'behavior_source': 'unknown'}, {'sim_feedback': 'disabled'},
    {'provider': None}, {'prompt': ''}, {'unexpected': True},
])
@pytest.mark.parametrize('entry', ['cli', 'library'])
def test_preparation_rejects_invalid_campaign_before_writing(tmp_path, monkeypatch, capsys, change, entry):
    from test_ppa_cli import published

    from behave.core.artifacts import DirectoryReader
    from behave.data.published import load_published_task
    from behave.ppa import __main__ as cli
    from behave.ppa.planning import prepare_study
    from behave.ppa.records import release_reference

    published(tmp_path)
    recipe = study()
    config = tmp_path / 'study.json'
    config.write_text(json.dumps(recipe))
    methods = [{'name': 'one', 'provider': str(tmp_path / 'provider.json'),
                'prompt': str(tmp_path / 'prompt.txt'), 'behavior_source': 'provided', **change}]
    method_file = tmp_path / 'methods.json'
    method_file.write_text(json.dumps({'methods': methods}))
    output = tmp_path / 'prepared'
    monkeypatch.setattr("behave.ppa.worker.NativePPA", lambda **kwargs: pytest.fail('preparation must not execute tools'))
    if entry == 'cli':
        assert cli.main(['prepare', '--study', str(config), '--objects', str(tmp_path / 'inputs'),
                         '--release', str(tmp_path / 'release.json'), '--output', str(output),
                         '--methods', str(method_file), '--liberty', 'fixture.lib', '--sta', 'fixture-sta']) == 1
        error = json.loads(capsys.readouterr().out)
        assert error['status'] == 'unavailable' and error['message']
    else:
        with pytest.raises(ValueError):
            task = load_published_task(DirectoryReader(tmp_path / 'inputs'),
                                      release_reference(tmp_path / 'release.json'), 'identity')
            prepare_study(recipe, task=task, output=output, objects=tmp_path / 'inputs',
                          release=tmp_path / 'release.json', methods=methods,
                          liberty='fixture.lib', sta='fixture-sta')
    assert not output.exists()


def test_direct_preparation_uses_study_validation_before_any_output(tmp_path):
    from types import SimpleNamespace

    from behave.ppa.planning import prepare_study

    recipe = study()
    recipe['search']['parallelism'] = True
    with pytest.raises(ValueError, match='invalid search setting'):
        prepare_study(recipe, task=SimpleNamespace(), output=tmp_path / 'prepared',
                      objects=tmp_path / 'objects', release=tmp_path / 'release.json')
    assert not (tmp_path / 'prepared').exists()


def test_prepared_campaign_matches_existing_record_and_recovers_partial_publication(tmp_path, monkeypatch):
    from test_ppa_cli import published

    from behave.core.artifacts import DirectoryReader
    from behave.core.records import canonical_json
    from behave.data.published import load_published_task
    from behave.ppa import planning
    from behave.ppa.records import release_reference

    published(tmp_path)
    recipe = study()
    methods = [{'name': 'one', 'provider': str(tmp_path / 'provider.json'),
                'prompt': str(tmp_path / 'prompt.txt'), 'behavior_source': 'provided'}]
    output = tmp_path / 'prepared'
    options = {'task': load_published_task(DirectoryReader(tmp_path / 'inputs'),
                                         release_reference(tmp_path / 'release.json'), 'identity'),
               'output': output, 'objects': tmp_path / 'inputs', 'release': tmp_path / 'release.json',
               'methods': methods, 'liberty': tmp_path / 'fixture.lib', 'sta': tmp_path / 'fixture-sta'}
    install = planning.install_exclusive

    def interrupted(root, name, data):
        if name == 'judge-policy.json':
            raise OSError('injected input publication failure')
        return install(root, name, data)

    monkeypatch.setattr(planning, 'install_exclusive', interrupted)
    with pytest.raises(OSError, match='publication failure'):
        planning.prepare_study(recipe, **options)
    assert (output / 'experiment.json').exists() and not (output / 'plan.json').exists()
    monkeypatch.setattr(planning, 'install_exclusive', install)
    first = planning.prepare_study(recipe, **options)
    expected = {'schema': 'behave.ppa-campaign/2', 'task': 'identity',
        'objects': str(tmp_path / 'inputs'), 'release': str(tmp_path / 'release.json'),
        'experiment': str(output / 'experiment.json'), 'retained_pool': str(output / 'retained.json'),
        'judge_policy': str(output / 'judge-policy.json'), 'liberty': str(tmp_path / 'fixture.lib'),
        'sta': str(tmp_path / 'fixture-sta'), 'methods': methods, **recipe['search']}
    assert (output / 'plan.json').read_bytes() == canonical_json(expected)
    assert (output / 'experiment.json').read_bytes() == canonical_json(experiment().as_dict())
    assert first == planning.prepare_study(recipe, **options)
    assert recipe == study()
