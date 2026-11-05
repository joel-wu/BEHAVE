import json
from pathlib import Path

import pytest
from test_slime_training_job import job  # noqa: F401
from test_slime_training_plan import inputs  # noqa: F401

from behave.train.checkpoint_storage import hf_save_pattern, install_storage, job_hf_directory, prepare_hf_storage


def test_alias_collision_is_not_overwritten(tmp_path):
    run = tmp_path / 'run'
    plan = run / 'artifacts/plan'
    (plan / 'hf_2').mkdir(parents=True)
    install_storage(run, tmp_path / 'store/native')
    with pytest.raises(ValueError, match='existing HF export'):
        prepare_hf_storage(run, plan, 5)
    assert not (plan / 'hf_storage.json').exists()


def test_hf_records_and_aliases_survive_work_root_migration(tmp_path):
    old = tmp_path / 'work'
    run, plan = old / 'run', old / 'run/artifacts/plan'
    plan.mkdir(parents=True)
    native = tmp_path / 'weights/native'
    install_storage(run, native)
    prepare_hf_storage(run, plan, 5)
    before = {p: p.read_bytes() for p in (run / 'hf_storage.json', plan / 'hf_storage.json')}
    actual = tmp_path / 'archive-work'
    old.rename(actual)
    old.symlink_to(actual, target_is_directory=True)
    for r, p in ((run, plan), (run.resolve(), plan.resolve())):
        assert job_hf_directory(r) == tmp_path / 'weights/hf'
        assert hf_save_pattern(p) == str(tmp_path / 'weights/hf/hf_{rollout_id}')
        prepare_hf_storage(r, p, 10)
    assert all(p.read_bytes() == content for p, content in before.items())
    assert (plan / 'hf_9').readlink() == tmp_path / 'weights/hf/hf_9'
    wrong_plan = run / 'unrelated'
    wrong_plan.mkdir()
    (wrong_plan / 'hf_storage.json').write_bytes(before[plan / 'hf_storage.json'])
    with pytest.raises(ValueError, match='another plan'):
        hf_save_pattern(wrong_plan)
    hf = tmp_path / 'weights/hf'
    hf.rename(tmp_path / 'actual-hf')
    hf.symlink_to(tmp_path / 'actual-hf', target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        job_hf_directory(run)


def test_hf_owner_mismatch_refuses_launch(tmp_path):
    run = tmp_path / 'run'
    plan = run / 'artifacts/plan'
    plan.mkdir(parents=True)
    install_storage(run, tmp_path / 'store/native')
    prepare_hf_storage(run, plan, 5)
    record = run / 'hf_storage.json'
    row = json.loads(record.read_text())
    row['job'] = str(tmp_path / 'other')
    record.chmod(0o600)
    record.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='another run'):
        hf_save_pattern(plan)


def test_training_saves_native_and_hf_externally_without_changing_settings(job, tmp_path):
    from tools.slime_checkpoint_input import prepare
    from tools.slime_training_job import execute
    output, local, calls = job
    before = (output / 'configuration.json').read_bytes()
    native = tmp_path / 'store/native'
    install_storage(output, native)
    assert execute(str(output), str(local)) == 0
    assert (output / 'configuration.json').read_bytes() == before
    hf = job_hf_directory(output)
    assert (hf / 'hf_2/weight.safetensors').is_file()
    assert (output / 'artifacts/final_hf').resolve() == hf / 'hf_2'
    assert (native / 'iter_0000002/state.pt').is_file()
    for argv, _ in calls:
        if '--save-hf' in argv:
            assert argv[argv.index('--save-hf') + 1] == str(hf / 'hf_{rollout_id}')
    for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json'):
        (hf / 'hf_2' / name).write_text('{}')
    result = prepare(output)
    assert result['checkpoint_directory'] == str(native)
    assert Path(result['model_path']).resolve() == hf / 'hf_2'
