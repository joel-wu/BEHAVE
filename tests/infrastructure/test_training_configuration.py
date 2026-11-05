"""Release configuration separation, without model loading or job dispatch."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from test_slime_training_plan import inputs

from behave.train.plan import (
    EXPERIMENT_SCHEMA,
    MACHINE_FIELDS,
    MACHINE_SCHEMA,
    compose_configuration,
    load_configuration,
)
from tools.slime_training_job import job_configuration

ROOT = Path(__file__).resolve().parents[2]
training_inputs = inputs


def split(configuration):
    experiment = deepcopy(configuration)
    machine = {key: experiment.pop(key) for key in MACHINE_FIELDS if key in experiment}
    retention = experiment.get("checkpoint_retention")
    if retention is not None and "protected_paths" in retention:
        machine["checkpoint_retention"] = {"protected_paths": retention.pop("protected_paths")}
    return ({"schema": EXPERIMENT_SCHEMA, "settings": experiment},
            {"schema": MACHINE_SCHEMA, "settings": machine})


def write_pair(tmp_path, configuration=None):
    if configuration is None:
        configuration = json.loads((ROOT / "infra/train/qwen27b_acceptance.json").read_text())
    experiment, machine = split(configuration)
    paths = tmp_path / "experiment.json", tmp_path / "machine.json"
    for path, record in zip(paths, (experiment, machine), strict=True):
        path.write_text(json.dumps(record))
    return paths


def test_split_reconstructs_current_profile_exactly_without_mutation():
    original = json.loads((ROOT / "infra/train/qwen27b_acceptance.json").read_text())
    experiment, machine = split(original)
    before = deepcopy((experiment, machine))
    actual = compose_configuration(experiment, machine)
    assert actual == original and (experiment, machine) == before
    actual["sampling"]["temperature"] = 99
    actual["checkpoint_retention"]["protected_paths"].append("/some/other/anchor")
    assert (experiment, machine) == before


@pytest.mark.parametrize("slots", [None, 4, 90, 0, -1, True, "4"])
def test_startup_gate_is_validated_as_machine_setting(slots):
    original = json.loads((ROOT / "infra/train/qwen27b_acceptance.json").read_text())
    experiment, machine = split(original)
    machine["settings"]["isolation_startup_slots"] = slots
    if slots is None or type(slots) is int and slots > 0:
        assert compose_configuration(experiment, machine)["isolation_startup_slots"] == slots
    else:
        with pytest.raises(ValueError, match="positive integer or null"):
            compose_configuration(experiment, machine)


def test_public_examples_match_current_experiment_and_have_no_workspace_paths():
    original = json.loads((ROOT / "infra/train/qwen27b_acceptance.json").read_text())
    expected_experiment, actual_machine = split(original)
    experiment_path = ROOT / "configs/train/qwen27b_rl.example.json"
    machine_path = ROOT / "infra/train/machine.example.json"
    experiment = json.loads(experiment_path.read_text())
    assert experiment == expected_experiment
    assert compose_configuration(experiment, actual_machine) == original
    actual = load_configuration(experiment_path, machine_profile=machine_path)
    expected_machine = json.loads(machine_path.read_text())["settings"]
    for key in ("model_path", "native_python", "slime_source", "megatron_source", "kernel_cache"):
        assert actual[key] == expected_machine[key]
    assert actual["checkpoint_retention"]["protected_paths"] == []
    for path in (experiment_path, machine_path):
        assert "/home/yuheng" not in path.read_text()
        assert "/store/users/yuheng" not in path.read_text()


@pytest.mark.parametrize("seed", [20260918, 20260919, 20260920])
def test_same_job_overrides_resolve_identically_for_flat_and_split(tmp_path, seed):
    flat = ROOT / "infra/train/qwen27b_acceptance.json"
    experiment, machine = write_pair(tmp_path)
    before = experiment.read_bytes(), machine.read_bytes(), flat.read_bytes()
    expected = job_configuration(flat, updates=60, training_seed=seed, workers=24)
    assert job_configuration(experiment, machine_profile=machine, updates=60,
                             training_seed=seed, workers=24) == expected
    assert (experiment.read_bytes(), machine.read_bytes(), flat.read_bytes()) == before


def test_saved_configuration_needs_no_machine_profile_on_resume(tmp_path):
    experiment, machine = write_pair(tmp_path)
    resolved = job_configuration(experiment, machine_profile=machine, updates=60)
    saved = tmp_path / "configuration.json"
    saved.write_text(json.dumps(resolved))
    experiment.unlink()
    machine.unlink()
    assert job_configuration(saved) == resolved


@pytest.mark.parametrize("key", [
    "training_seed", "sampling", "runtime", "layout", "native_arguments",
    "hf_checkpoint_interval", "checkpoint_interval", "sampler", "curriculum",
    "self_improvement", "pool", "memory", "resume_from", "unknown",
])
def test_machine_cannot_override_experiment_or_runtime_state(tmp_path, key):
    experiment, machine = write_pair(tmp_path)
    value = json.loads(machine.read_text())
    value["settings"][key] = {}
    machine.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="cannot set experiment/state"):
        load_configuration(experiment, machine_profile=machine)


def test_machine_cannot_change_retention_policy(tmp_path):
    experiment, machine = write_pair(tmp_path)
    value = json.loads(machine.read_text())
    value["settings"]["checkpoint_retention"]["mode"] = "archive"
    machine.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Unexpected"):
        load_configuration(experiment, machine_profile=machine)


@pytest.mark.parametrize(("key", "value"), [
    ("native_python", "relative/python"), ("slime_source", "/"), ("model_path", None),
    ("megatron_source", "relative/megatron"), ("megatron_source", None),
    ("kernel_cache", "/tmp/../wrong"), ("workers", True), ("judge_workers", 0),
    ("process_limit", -1), ("preparation_start_method", "invalid"),
    ("endpoint_url", "file:///tmp/socket"), ("endpoint_url", "https://user:secret@localhost/v1"),
    ("checkpoint_retention", {"protected_paths": ["/"]}),
    ("checkpoint_retention", {"protected_paths": "not-a-list"}),
])
def test_invalid_machine_settings_are_rejected(tmp_path, key, value):
    experiment, machine = write_pair(tmp_path)
    doc = json.loads(machine.read_text())
    doc["settings"][key] = value
    machine.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        load_configuration(experiment, machine_profile=machine)


def test_missing_machine_field_is_rejected(tmp_path):
    experiment, machine = write_pair(tmp_path)
    doc = json.loads(machine.read_text())
    del doc["settings"]["native_python"]
    machine.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="missing required"):
        load_configuration(experiment, machine_profile=machine)


def test_split_recipe_requires_explicit_profile_and_legacy_rejects_overlay(tmp_path):
    experiment, machine = write_pair(tmp_path)
    with pytest.raises(ValueError, match="explicit --machine-profile"):
        load_configuration(experiment)
    with pytest.raises(ValueError, match="already resolved"):
        load_configuration(ROOT / "infra/train/qwen27b_acceptance.json", machine_profile=machine)


@pytest.mark.parametrize("key", ["model_path", "megatron_source", "protected_paths"])
def test_machine_paths_cannot_be_hidden_in_experiment(tmp_path, key):
    experiment, machine = write_pair(tmp_path)
    doc = json.loads(experiment.read_text())
    if key != "protected_paths":
        doc["settings"][key] = "/sources/other"
    else:
        doc["settings"]["checkpoint_retention"][key] = []
    experiment.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="machine profile"):
        load_configuration(experiment, machine_profile=machine)


def test_unknown_schema_and_duplicate_keys_are_rejected(tmp_path):
    experiment, machine = write_pair(tmp_path)
    doc = json.loads(experiment.read_text())
    doc["schema"] = "behave.training-experiment/99"
    experiment.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=EXPERIMENT_SCHEMA):
        load_configuration(experiment, machine_profile=machine)
    experiment, machine = write_pair(tmp_path)
    machine.write_text('{"schema":"one","schema":"two","settings":{}}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_configuration(experiment, machine_profile=machine)


def test_submit_saves_only_resolved_config_and_uses_existing_dispatch(tmp_path, training_inputs, monkeypatch):
    from tools import slime_training_job

    configuration, release, train_split, task_id = training_inputs
    experiment, machine = write_pair(tmp_path, configuration)
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **kw: {})
    monkeypatch.setattr("behave.train.checkpoint_storage.storage_destination", lambda *a: None)
    dispatched = []
    monkeypatch.setattr("behave.runtime.jobs.submit",
                        lambda *a, **kw: dispatched.append((a, kw)) or {"submitted": True})
    output = tmp_path / "job"
    assert slime_training_job.submit(output, 123, experiment, release, [task_id],
        train_split, cpus=144, machine_profile=machine) == {"submitted": True}
    assert json.loads((output / "configuration.json").read_text()) == configuration
    assert len(dispatched) == 1
    argv = dispatched[0][0][1]
    assert "--gpus=8" in argv and "--cpus-per-task=144" in argv
    assert str(machine) not in argv and str(experiment) not in argv


def test_job_cli_passes_machine_profile_to_existing_entry(monkeypatch):
    from tools import slime_training_job

    seen = []
    monkeypatch.setattr("behave.cli.training_job.submit", lambda *a, **kw: seen.append(kw))
    monkeypatch.setattr(sys, "argv", ["job", "output", "--allocation", "123",
        "--configuration", "experiment.json", "--machine-profile", "machine.json",
        "--release", "release", "--train-split", "train.txt", "--task", "a", "--cpus", "144"])
    slime_training_job.main()
    assert seen == [{"repository": Path(__file__).resolve().parents[2], "cpus": 144, "machine_profile": Path("machine.json")}]


def test_plan_cli_uses_same_composition(tmp_path, monkeypatch):
    from tools import slime_training_plan

    experiment, machine = write_pair(tmp_path)
    seen = []
    monkeypatch.setattr(slime_training_plan, "prepare", lambda *a, **kw: seen.append(a[1]))
    monkeypatch.setattr(sys, "argv", ["plan", str(tmp_path / "output"),
        "--configuration", str(experiment), "--machine-profile", str(machine),
        "--release", "release", "--train-split", "train.txt", "--task", "a"])
    slime_training_plan.main()
    assert seen == [load_configuration(experiment, machine_profile=machine)]
