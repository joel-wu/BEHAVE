"""Study preparation is an installed API, not a dated work-directory script."""

from pathlib import Path

import pytest

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.improve import study_plan
from behave.improve.library import SourceLibrary


def study_workspace(root):
    from test_improvement_library import prepared

    prepared(root)
    configuration = {
        "cpu_threads": 2, "build_jobs": 1, "workers": 8, "judge_workers": 2,
        "layout": {"rollout_gpus": 8}, "sampling": {"temperature": 1},
        "runtime": {"max_turns": 60}, "initial_updates": 1,
    }
    (root / "train.json").write_bytes(canonical_json(configuration))
    (root / "acquisition.md").write_text("Read only the selected source library.\n")
    (root / "construction.md").write_text("Construct the assigned task.\n")
    (root / "train.txt").write_text("fixture-training-task\n")
    settings = {
        "round_id": "fixture-study", "training_configuration": "train.json",
        "resources": "resources", "diagnosis_release": "fixture-release", "diagnosis_split": "train.txt",
        "guides": {"acquisition": "acquisition.md", "construction": "construction.md"},
        "study": {"seed_updates": 3, "acquisition_rounds": 2},
        "acquired_updates": 3, "raw_updates": 0, "generation_only": False,
        "require_recorded_feedback": True, "task_configuration": {}, "raw_configuration": {},
    }
    path = root / "settings.json"
    path.write_bytes(canonical_json(settings))
    return path, configuration


def study_profiles(root, configuration):
    from test_training_configuration import split

    resolved = {**configuration, 'model_path': str(root / 'model'),
                'native_python': str(root / 'bin/python'), 'slime_source': str(root / 'slime'),
                'endpoint_url': 'http://127.0.0.1:31000/v1/chat/completions',
                'training_seed': 20260919, 'hf_checkpoint_interval': 5,
                'checkpoint_retention': {'keep_full': 1, 'mode': 'delete',
                                         'protected_paths': [str(root / 'protected-anchor')]}}
    experiment, machine = split(resolved)
    (root / 'train.json').write_bytes(canonical_json(experiment))
    profile = root / 'machine.json'
    profile.write_bytes(canonical_json(machine))
    return profile, resolved


def test_explicit_repository_preparation_is_independent_of_cwd(tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    settings, configuration = study_workspace(root)
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    from behave.runtime import jobs
    monkeypatch.setattr(jobs, "submit", lambda *a, **kw: pytest.fail("preparation must not submit jobs"))
    output = tmp_path / "study"
    result = study_plan.prepare(settings, output, repository=root)
    assert result["model_started"] is False
    assert parse_json((root / "train.json").read_bytes()) == configuration
    assert parse_json((output / "configuration.json").read_bytes()) == configuration
    schedule = parse_json((output / "study.json").read_bytes())
    assert schedule["automatic_launch"] is False
    assert schedule["total_task_updates"] == 9
    assert [r["completed_updates"] for r in schedule["checkpoint_milestones"]] == [3, 6, 9]
    native = parse_json((output / "native_configuration.json").read_bytes())
    assert native["hf_checkpoint_updates"] == [3, 6, 9]
    assert native["self_improvement"] == {"study": str(output / "study.json")}
    assert not (output / "control").exists()
    for name in ("acquisition", "construction"):
        assert (output / f"guides/{name}.md").read_bytes() == (root / f"{name}.md").read_bytes()
    selection = parse_json((output / "library/selection.json").read_bytes())
    objects = DirectoryStore(output / "library/objects")
    library = SourceLibrary.from_bytes(objects.read(BlobRef.from_dict(selection["library"])))
    assert len(library.units) == 2
    assert selection["authored_answers_in_library"] is False
    with pytest.raises(FileExistsError):
        study_plan.prepare(settings, output, repository=root)


@pytest.mark.parametrize("entry", ["cli", "train", "infra", "legacy"])
@pytest.mark.parametrize('split', [False, True], ids=['flat', 'split'])
def test_command_entries_prepare_identical_stage_files(tmp_path, capsys, entry, split):
    from behave.cli.training_study import main

    root = tmp_path / "inputs"
    settings, configuration = study_workspace(root)
    arguments = ["--repository", str(root), "--settings", str(settings), "--output", str(tmp_path / "output")]
    if split:
        machine, configuration = study_profiles(root, configuration)
        arguments.extend(['--machine-profile', str(machine)])
    if entry == "train":
        from behave.train.__main__ import main
        arguments.insert(0, "prepare-study")
    elif entry == "infra":
        from behave.cli.__main__ import main
        arguments[:0] = ["train", "prepare-study"]
    elif entry == "legacy":
        from tools.synthesis_pilot_plan import main
    assert main(arguments) == 0
    result = parse_json(capsys.readouterr().out.encode())
    assert result["model_started"] is False
    study = parse_json(Path(result["study"]).read_bytes())
    assert [row["stop_update"] for row in study["stages"]] == [3, 6, 9]
    assert parse_json(Path(result['configuration']).read_bytes()) == configuration


@pytest.mark.parametrize('legacy', [False, True])
def test_split_study_saves_self_contained_configuration_and_same_schedule(tmp_path, monkeypatch, legacy):
    from behave.train.plan import load_configuration
    from tools import synthesis_pilot_plan

    root = tmp_path / 'inputs'
    settings, configuration = study_workspace(root)
    machine, resolved = study_profiles(root, configuration)
    sources = {path: path.read_bytes() for path in (settings, machine, root / 'train.json')}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('behave.runtime.jobs.submit', lambda *a, **k: pytest.fail('no dispatch'))
    prepare = synthesis_pilot_plan.prepare if legacy else study_plan.prepare
    flat_output = tmp_path / 'flat-study'
    (root / 'train.json').write_bytes(canonical_json(resolved))
    prepare(settings, flat_output, repository=root)
    (root / 'train.json').write_bytes(sources[root / 'train.json'])
    output = tmp_path / 'study'
    prepare(settings, output, repository=root, machine_profile=machine)
    assert all(path.read_bytes() == value for path, value in sources.items())
    native = parse_json((output / 'native_configuration.json').read_bytes())
    seed = parse_json((output / 'seed_configuration.json').read_bytes())
    runtime = parse_json((output / 'runtime.json').read_bytes())
    for name in ('configuration.json', 'seed_configuration.json', 'runtime.json',
                 'settings.json', 'study.json', 'acquisition_01.json', 'acquisition_02.json',
                 'library/selection.json'):
        assert (output / name).read_bytes() == (flat_output / name).read_bytes()
    flat_native = parse_json((flat_output / 'native_configuration.json').read_bytes())
    assert {key: value for key, value in native.items() if key != 'self_improvement'} == {
        key: value for key, value in flat_native.items() if key != 'self_improvement'}
    for key in ('sampling', 'training_seed', 'layout', 'checkpoint_retention', 'hf_checkpoint_interval'):
        assert native[key] == seed[key] == resolved[key]
    assert native['initial_updates'] == 9 and seed['initial_updates'] == 3
    assert native['hf_checkpoint_updates'] == [3, 6, 9]
    assert runtime['sampling'] == resolved['sampling']
    assert runtime['workers'] == resolved['workers']
    assert runtime['raw_configuration']['layout']['rollout_gpus'] == 0
    assert 'schema' not in native and 'machine_profile' not in native
    # Neither resume nor acquisition needs the original two configuration files.
    machine.rename(root / 'retained-machine.json')
    (root / 'train.json').rename(root / 'retained-train.json')
    assert load_configuration(output / 'configuration.json') == resolved
    assert load_configuration(output / 'native_configuration.json') == native
    for stage in parse_json((output / 'study.json').read_bytes())['stages'][1:]:
        acquired = parse_json((output / stage['runtime']).read_bytes())
        assert acquired['sampling'] == resolved['sampling'] and acquired['acquired_updates'] == 3


@pytest.mark.parametrize('problem', ['missing-profile', 'flat-overlay', 'semantic-override', 'state-override'])
def test_study_configuration_errors_precede_output_and_library_import(tmp_path, monkeypatch, problem):
    root = tmp_path / 'inputs'
    settings, configuration = study_workspace(root)
    machine, resolved = study_profiles(root, configuration)
    if problem == 'missing-profile':
        machine = None
    elif problem == 'flat-overlay':
        (root / 'train.json').write_bytes(canonical_json(resolved))
    else:
        document = parse_json(machine.read_bytes())
        document['settings']['sampling' if problem == 'semantic-override' else 'pool'] = {}
        machine.write_bytes(canonical_json(document))
    monkeypatch.setattr(study_plan, 'import_library', lambda *a, **k: pytest.fail('invalid configuration'))
    output = tmp_path / 'not-created'
    with pytest.raises(ValueError, match='machine profile|machine-profile'):
        study_plan.prepare(settings, output, repository=root, machine_profile=machine)
    assert not output.exists()


@pytest.mark.parametrize('field,value', [
    ('seed_updates', 0), ('seed_updates', -1), ('seed_updates', True), ('seed_updates', 1.5),
    ('acquisition_rounds', -1), ('acquisition_rounds', False), ('acquisition_rounds', '2'),
    ('acquired_updates', 0), ('acquired_updates', -1), ('acquired_updates', True)])
def test_invalid_schedule_is_rejected_before_publishing_study(tmp_path, monkeypatch, field, value):
    root = tmp_path / 'inputs'
    settings, _ = study_workspace(root)
    document = parse_json(settings.read_bytes())
    (document if field == 'acquired_updates' else document['study'])[field] = value
    settings.write_bytes(canonical_json(document))
    output = tmp_path / 'not-created'
    monkeypatch.setattr(study_plan, 'import_library', lambda *a, **k: pytest.fail('invalid schedule'))
    with pytest.raises(ValueError, match=field):
        study_plan.prepare(settings, output, repository=root)
    assert not output.exists()


def test_seed_only_study_needs_no_acquisition_updates(tmp_path):
    settings, configuration = study_workspace(tmp_path / 'inputs')
    document = parse_json(settings.read_bytes())
    document['study']['acquisition_rounds'] = 0
    document['acquired_updates'] = 0
    runtime = study_plan.runtime_for(document, configuration)
    files = study_plan.study_files(document, runtime, configuration)
    assert files['native_configuration.json']['hf_checkpoint_updates'] == [3]
    assert files['study.json']['total_task_updates'] == 3
    assert len(files['study.json']['stages']) == 1


def test_installed_command_requires_repository_before_preparing(tmp_path, monkeypatch, capsys):
    from behave.cli.training_study import main

    monkeypatch.setattr(study_plan, "prepare", lambda *a, **kw: pytest.fail("implicit repository"))
    with pytest.raises(SystemExit) as error:
        main(["--settings", "settings.json", "--output", str(tmp_path / "not-created")])
    assert error.value.code == 2 and "--repository" in capsys.readouterr().err
    assert not (tmp_path / "not-created").exists()
