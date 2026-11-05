"""Study preparation reuses the trainer; these tests never launch a job."""

from pathlib import Path

import pytest

from behave.core.records import canonical_json, parse_json
from behave.improve.study_plan import load_settings, runtime_for, study_files


def test_settings_inheritance_is_repository_relative_and_replaces_whole_values(tmp_path):
    root = tmp_path / 'inputs'
    nested = root / 'nested'
    nested.mkdir(parents=True)
    parent = root / 'parent.json'
    child = nested / 'child.json'
    parent.write_bytes(canonical_json({'round_id': 'parent', 'sampling': {'a': 1, 'b': 2}}))
    child.write_bytes(canonical_json({'base_settings': 'parent.json', 'sampling': {'a': 3}}))
    before = parent.read_bytes(), child.read_bytes()
    assert load_settings(child, root) == {'round_id': 'parent', 'sampling': {'a': 3}}
    assert (parent.read_bytes(), child.read_bytes()) == before


@pytest.mark.parametrize('cycle', ['self', 'parents', 'alias'])
def test_settings_inheritance_reports_cycles_before_recursive_read(tmp_path, cycle):
    first, second = tmp_path / 'first.json', tmp_path / 'second.json'
    first.write_bytes(canonical_json({'base_settings': second.name, 'value': 'first'}))
    if cycle == 'alias':
        second.symlink_to(first)
    else:
        second.write_bytes(canonical_json({'base_settings': first.name if cycle == 'parents' else second.name}))
    with pytest.raises(ValueError, match='cyclic base_settings inheritance'):
        load_settings(first, tmp_path)


@pytest.mark.parametrize('base', ['', False, 3, []])
def test_invalid_base_settings_has_an_explicit_configuration_error(tmp_path, base):
    path = tmp_path / 'settings.json'
    path.write_bytes(canonical_json({'base_settings': base}))
    with pytest.raises(ValueError, match='base_settings must be a nonempty path or null'):
        load_settings(path, tmp_path)


def test_settings_chain_preserves_child_precedence_and_accepts_null_base(tmp_path):
    for index in range(32):
        (tmp_path / f'{index}.json').write_bytes(canonical_json({
            'base_settings': f'{index + 1}.json' if index < 31 else None,
            'overridden': index, f'key_{index}': index}))
    result = load_settings(tmp_path / '0.json', tmp_path)
    assert result == {'overridden': 0, **{f'key_{index}': index for index in range(32)}}


def study():
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(Path(__file__).parent / "fixtures/synthesis/study.json", root)
    configuration = parse_json((root / settings["training_configuration"]).read_bytes())
    runtime = runtime_for(settings, configuration)
    return settings, configuration, runtime


def test_formal_task_runtime_does_not_inherit_small_pilot_training_overrides():
    settings, configuration, runtime = study()
    assert "base_settings" not in settings and "study" not in runtime
    assert runtime["raw_updates"] == 0 and runtime["acquired_updates"] == 3
    assert not runtime["generation_only"] and runtime["require_recorded_feedback"]
    assert "diagnosis_tasks" not in runtime and "diagnosis_configuration" not in runtime
    assert runtime["task_configuration"] == {"output_preview_bytes": runtime["output_preview_bytes"]}
    effective = {**configuration, **runtime["task_configuration"]}
    assert effective["update_groups"] * effective["samples_per_task"] == 64
    assert effective["sampler"]["order"] == "shuffle"
    assert effective["curriculum"] == configuration["curriculum"]
    assert {key: effective["curriculum"][key] for key in ("mastery_cooldown_updates", "zero_cooldown_updates")} == {
        "mastery_cooldown_updates": 10, "zero_cooldown_updates": 2}
    assert effective["group_replenishment"] == {"max_groups": None}
    assert runtime["generation_target"] == {"admitted_tasks": 20, "proposal_tasks": 30, "max_batches": 2}
    assert runtime["cpus"] == 90 and runtime["acquisition_workers"] == 5
    assert runtime["acquisition_limits"] == {"max_tasks": 3, "max_observations": 24}
    assert all(role["max_turns"] == 100 for role in runtime["role_attempts"].values())


@pytest.mark.parametrize("seed,rounds,updates", [(3, 5, 3), (2, 2, 4)])
def test_stage_schedule_comes_from_runtime_without_mutating_training(seed, rounds, updates):
    settings, configuration, runtime = study()
    original = canonical_json(configuration)
    settings = {**settings, "study": {"seed_updates": seed, "acquisition_rounds": rounds}}
    runtime = {**runtime, "acquired_updates": updates}
    files = study_files(settings, runtime, configuration)
    seed_configuration = files["seed_configuration.json"]
    assert seed_configuration == {**configuration, **runtime["task_configuration"],
                                  "initial_updates": seed, "resume_updates": 0}
    schedule = files["study.json"]
    assert schedule["total_task_updates"] == seed + rounds * updates
    assert schedule["automatic_launch"] is False
    first, *cycles = schedule["stages"]
    assert first["first_update"] == 0 and first["stop_update"] == seed
    assert first["tasks_file"] == runtime["diagnosis_split"]
    assert [cycle["after_updates"] for cycle in cycles] == list(range(seed, seed + rounds * updates, updates))
    for cycle in cycles:
        assert cycle["first_update"] == cycle["after_updates"]
        assert cycle["stop_update"] - cycle["first_update"] == updates
        assert files[cycle["runtime"]]["raw_updates"] == 0
        assert files[cycle["runtime"]]["acquired_updates"] == updates
    assert len({files[cycle["runtime"]]["round_id"] for cycle in cycles}) == rounds
    milestones = [stage["stop_update"] for stage in schedule["stages"]]
    native = files[schedule["native_configuration"]]
    assert native["initial_updates"] == seed + rounds * updates
    assert native["hf_checkpoint_updates"] == milestones
    assert [row["completed_updates"] for row in schedule["checkpoint_milestones"]] == milestones
    assert [row["hf_relative_path"] for row in schedule["checkpoint_milestones"]] == [
        f"artifacts/plan/hf_{step - 1}" for step in milestones]
    assert not schedule["generation_binding"]["hf_reload_required"]
    assert schedule["generation_binding"]["native_callback_connected"]
    assert canonical_json(configuration) == original


def test_study_preserves_bash_preview_across_construction_and_training():
    settings, configuration, _ = study()
    original = canonical_json(configuration)
    for preview in (32768, 8192, None):
        selected = {**settings, "output_preview_bytes": preview}
        runtime = runtime_for(selected, configuration)
        files = study_files(selected, runtime, configuration)
        assert runtime["output_preview_bytes"] == preview
        assert files["seed_configuration.json"]["output_preview_bytes"] == preview
        for stage in files["study.json"]["stages"][1:]:
            acquisition = files[stage["runtime"]]
            learned = {**configuration, **acquisition["task_configuration"]}
            assert acquisition["output_preview_bytes"] == preview
            assert learned["output_preview_bytes"] == preview
    assert canonical_json(configuration) == original
