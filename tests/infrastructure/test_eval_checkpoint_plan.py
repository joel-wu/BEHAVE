"""Installed checkpoint planning retains the existing measurement contracts."""

from pathlib import Path

import pytest
from test_eval_checkpoint_study import fixture

from behave.eval import checkpoint as plan
from behave.runtime.records import write_json


@pytest.mark.parametrize("field", ["allocated_cpus", "allocated_gpus"])
@pytest.mark.parametrize("value", [0, -1, True, 4.0, "8", None])
def test_capacity_rejects_invalid_counts(field, value):
    with pytest.raises(ValueError, match="positive integers"):
        plan.node_capacity({field: value})


@pytest.mark.parametrize("cpus", [[], [0, 0], [-1], [True], [1.0], ["1"], "0,1", None])
def test_affinity_rejects_invalid_cpu_ids(cpus):
    with pytest.raises(ValueError, match="execution CPU IDs"):
        plan.execution_cpu_ids({}, cpus)


def test_affinity_uses_count_and_optional_membership_not_maximum_id():
    resources = {"allocated_cpus": 4, "allocated_cpu_ids": [100, 103, 106, 109]}
    assert plan.execution_cpu_ids(resources, [103, 109]) == [103, 109]
    assert plan.execution_cpu_ids({"allocated_cpus": 4}, [103, 109]) == [103, 109]
    with pytest.raises(ValueError, match="outside"):
        plan.execution_cpu_ids(resources, [0, 1])
    with pytest.raises(ValueError, match="exceeds"):
        plan.execution_cpu_ids(resources, [100, 103, 106, 109, 112])
    with pytest.raises(ValueError, match="enumerate"):
        plan.execution_cpu_ids({**resources, "allocated_cpu_ids": [100, 103]}, [100])


@pytest.mark.parametrize("override", [
    {"device_groups": ["0,1,2,3", "3,4,5,6"]},
    {"device_groups": ["0,1,2,3", "4,5,6,8"]},
    {"device_groups": ["0,1", "2,3"]},
    {"device_groups": ["0,1,2,3"]},
    {"device_groups": ["-1,0,1,2", "3,4,5,6"]},
    {"tensor_parallel_per_engine": True},
    {"engines": 0},
    {"generation_cpu_ids": []},
])
def test_invalid_resources_fail_before_submission(tmp_path, monkeypatch, override):
    from behave.eval import checkpoint

    config = fixture(tmp_path)
    config["resources"].update(override)
    write_json(tmp_path / "configuration.json", config)
    write_json(tmp_path / "queue.json", {"checkpoints": [{"model_line": "main", "update": 5}]})
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("must not submit"))
    with pytest.raises(ValueError):
        checkpoint.submit_checkpoint(tmp_path, 0)


def test_explicit_capacity_submission_preserves_sampler_and_frozen_entry(tmp_path, monkeypatch):
    from behave.eval import checkpoint

    config = fixture(tmp_path)
    config["resources"].update(allocated_cpus=128, allocated_gpus=4, engines=1,
                               device_groups=["0,1,2,3"])
    write_json(tmp_path / "configuration.json", config)
    write_json(tmp_path / "queue.json", {"checkpoints": [{"model_line": "main", "update": 5}]})
    before = {name: (tmp_path / name).read_bytes()
              for name in ("configuration.json", "queue.json", "sampling.json")}
    captured = {}
    monkeypatch.setattr("behave.runtime.jobs.submit",
        lambda control, argv, **kw: captured.update(argv=argv, **kw))
    checkpoint.submit_checkpoint(tmp_path, 0, cwd=tmp_path)
    assert "--cpus-per-task=128" in captured["argv"] and "--gpus=4" in captured["argv"]
    assert "from tools.eval_checkpoint_study import node;" in captured["argv"][captured["argv"].index("-c") + 1]
    assert captured["metadata"]["allocated_cpus"] == 128
    assert captured["metadata"]["allocated_gpus"] == 4
    assert {name: (tmp_path / name).read_bytes() for name in before} == before


def test_dispatch_capacity_does_not_change_seeds_or_checkpoint_schedule(tmp_path):
    config = fixture(tmp_path)
    config["resources"].update(allocated_cpus=128, allocated_gpus=4)
    write_json(tmp_path / "configuration.json", config)
    entries = [{"model_line": line, "update": update, "inference_seeds": [17, 3, 42]}
               for line, updates in (("main", [5, 10]), ("syn", [3, 6]), ("seed-only", [3, 6]))
               for update in updates]
    write_json(tmp_path / "queue.json", {"checkpoints": entries})
    assignments = {"lane": {"allocation": 123, "model_lines": ["main", "syn", "seed-only"]}}
    options = {"code_directory": "code", "probe_request": tmp_path / "probe.json"}
    with pytest.raises(ValueError, match="divide allocated_gpus"):
        plan.prepare_dispatch(tmp_path, assignments, tensor_parallel=3, launch_id="bad", **options)
    assert not (tmp_path / "dispatch/bad").exists()
    manifest = plan.prepare_dispatch(tmp_path, assignments, tensor_parallel=2, launch_id="four-gpu", **options)
    lane = Path(manifest["lanes"]["lane"]["root"])
    planned = plan.read(lane / "configuration.json")
    assert planned["resources"]["engines"] == 2
    assert planned["resources"]["device_groups"] == ["0,1", "2,3"]
    assert planned["attempt"] == config["attempt"]
    assert plan.read(lane / "sampling.json") == plan.read(tmp_path / "sampling.json")
    assert plan.read(lane / "queue.json")["checkpoints"] == entries


@pytest.mark.parametrize("tp", [1, 4])
@pytest.mark.parametrize("seed", [17, 3, 42])
def test_preparation_retains_explicit_topology_sampler_and_identity(tmp_path, tp, seed):
    config = fixture(tmp_path)
    config["resources"]["tensor_parallel_per_engine"] = tp
    config["launch_id"] = "same-launch"
    config["independent_measurement"] = True
    write_json(tmp_path / "configuration.json", config)
    entry = {"model_line": "rl-main", "update": 5, "inference_seeds": [seed]}
    handoff = {"model_path": "/fixture/checkpoint", "model_revision": "unchanged"}
    output = plan.prepare_measurement(tmp_path, entry, seed, handoff)
    assert output == tmp_path / "rl-main/update-005" / f"eval-seed-{seed:02d}" / "rollout"
    assert plan.control_directory(tmp_path, entry) == output.parent / "control/same-launch"
    assert plan.generation_directory(tmp_path, entry) == output.parent
    assert plan.requested_seeds(tmp_path, entry) == [seed]
    assert plan.snapshot_directory(tmp_path) == tmp_path / "code/snapshot"
    assert plan.read(output / "sampling.json") == {"temperature": 1, "top_p": .95, "top_k": 20}
    settings = plan.read(output / "settings.json")
    assert settings["attempt"] == config["attempt"] and settings["tasks"] == ["one"]
    assert settings["attempt_namespace"] == str(output.parent) and settings["score_on_submit"] is False
    assert settings["workers"] == settings["model_workers"] == 44
    assert settings["maximum_input_tokens"] == 131072 and settings["output_preview_bytes"] == 32768
    assert plan.read(output / "trained/configuration.json") == handoff
    served = plan.read(output.parent / "serving.json")["spec"]
    assert served["tensor_parallel"] == tp and served["seed"] == seed
    assert served["context_length"] == 131072 and served["tool_parser"] == "qwen3_coder"
    assert served["model_revision"] == "unchanged" and served["reasoning_parser"] == "qwen3"
    assert served["startup_seconds"] == 1800 and served["cleanup_seconds"] == 300
    assert served["lifetime_seconds"] is None
    assert served["native_arguments"] == ["--watchdog-timeout", "1800"]
    identity = plan.read(output.parent / "manifest.json")
    assert identity == {"model_line": "rl-main", "update": 5, "inference_seed": seed,
        "checkpoint": handoff, "setting": "joint", "sampling": plan.read(output / "sampling.json"),
        "attempt": config["attempt"], "tensor_parallel": tp, "release": {}, "code": "frozen-fixture"}
    files = [output / name for name in ("settings.json", "sampling.json", "trained/configuration.json")]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    assert plan.prepare_measurement(tmp_path, entry, seed, handoff) == output
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before


def test_revision_directory_keeps_seed_controls_separate(tmp_path):
    config = fixture(tmp_path)
    config.update(independent_measurement=True, launch_id="launch")
    write_json(tmp_path / "configuration.json", config)
    entry = {"model_line": "syn", "update": 18, "measurement_revision": "checked", "inference_seeds": [42]}
    expected = tmp_path / "syn/update-018/revisions/checked/eval-seed-42"
    assert plan.measurement_directory(tmp_path, entry, 42) == expected
    assert plan.control_directory(tmp_path, entry) == expected / "control/launch"
    with pytest.raises(ValueError, match="simple directory"):
        plan.measurement_directory(tmp_path, {**entry, "measurement_revision": "../old"}, 42)


def test_recovery_preparation_keeps_original_attempt_namespace(tmp_path):
    config = fixture(tmp_path)
    entry = {"model_line": "syn", "update": 18}
    handoff = {"model_path": "/fixture/checkpoint", "model_revision": "unchanged"}
    source = plan.prepare_measurement(tmp_path, entry, 3, handoff)
    recovery = {"source": str(source), "fenced": True}
    config["generation_recovery"] = recovery
    write_json(tmp_path / "configuration.json", config)
    revised = {**entry, "measurement_revision": "recovered"}
    output = plan.prepare_measurement(tmp_path, revised, 3, handoff)
    settings = plan.read(output / "settings.json")
    assert settings["generation_recovery"] == recovery
    assert settings["attempt_namespace"] == str(source.parent)
    assert plan.read(output.parent / "manifest.json")["generation_recovery"] == recovery
    config["attempt"]["max_turns"] = 61
    write_json(tmp_path / "configuration.json", config)
    with pytest.raises(ValueError, match="changes attempt"):
        plan.prepare_measurement(tmp_path, {**entry, "measurement_revision": "other"}, 3, handoff)
