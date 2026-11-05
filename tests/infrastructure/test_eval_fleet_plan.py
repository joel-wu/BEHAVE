"""Fleet policy is installable without starting a scheduler or loading a model."""

import copy

import pytest

from behave.eval import fleet as fleet_module
from behave.runtime.records import write_json


@pytest.mark.parametrize("duplicate", ["key", "identity", "directory"])
def test_append_rejects_each_duplicate_identity(tmp_path, duplicate):
    first = {"key": "main-5-3", "entry": {"model_line": "main", "update": 5}, "seed": 3, "directory": "main/5/3"}
    second = {"key": "main-5-42", "entry": {"model_line": "main", "update": 5}, "seed": 42, "directory": "main/5/42"}
    if duplicate == "identity":
        second["seed"] = first["seed"]
    else:
        second[duplicate] = first[duplicate]
    previous = {"measurements": [first]}
    write_json(tmp_path / "fleet.json", {"measurements": [first, second]})
    with pytest.raises(ValueError, match="duplicate"):
        fleet_module.reload_fleet(tmp_path, previous)
    assert previous == {"measurements": [first]}


def test_slot_planning_never_changes_measurement_or_scoring_owner():
    fleet = {"nodes": {"7": {}, "8": {"overflow_lanes": ["syn"]}, "9": {"overflow_lanes": ["main"]}}}
    item = {"allocation": 7, "entry": {"model_line": "main"}, "seed": 42}
    before = copy.deepcopy((fleet, item))
    busy = {(7, gpu) for gpu in range(8)}
    assert fleet_module.generation_slot(fleet, item, busy, {7, 8}) is None
    assert fleet_module.generation_slot(fleet, item, busy, {7, 8, 9}) == {"allocation": 9, "gpu": 0}
    busy.remove((7, 3))
    assert fleet_module.generation_slot(fleet, item, busy, {7, 8, 9}) == {"allocation": 7, "gpu": 3}
    assert (fleet, item) == before


def test_heterogeneous_nodes_use_only_their_eligible_devices():
    fleet = {"nodes": {
        "7": {"allocated_gpus": 4, "generation_gpu_ids": [0, 2, 3]},
        "8": {"allocated_gpus": 2, "overflow_lanes": ["main"]}}}
    item = {"allocation": 7, "entry": {"model_line": "main"}, "seed": 42}
    before = copy.deepcopy((fleet, item))
    busy = set()
    expected = [(7, 0), (7, 2), (7, 3), (8, 0), (8, 1)]
    for allocation, gpu in expected:
        assert fleet_module.generation_slot(fleet, item, busy, {7, 8}) == {"allocation": allocation, "gpu": gpu}
        busy.add((allocation, gpu))
    assert fleet_module.generation_slot(fleet, item, busy, {7, 8}) is None
    assert (fleet, item) == before


@pytest.mark.parametrize("devices", [[], [0, 0], [-1], [4], [True], [1.0], "0,1"])
def test_invalid_eligible_devices_are_rejected(devices):
    with pytest.raises(ValueError, match="generation_gpu_ids"):
        fleet_module.generation_gpus({"allocated_gpus": 4, "generation_gpu_ids": devices})


def test_per_node_fleet_budgets_do_not_inherit_archived_allocation(tmp_path):
    from behave.eval.checkpoint import validate_generation_resources

    base = {"resources": {"allocated_cpus": 188, "allocated_gpus": 8,
                          "allocated_cpu_ids": list(range(188))},
            "inference_seeds": [17, 3, 42], "attempt": {"context_tokens": 131072},
            "checkpoint_policy": {"paired_updates": [3, 6, 9]}}
    write_json(tmp_path / "configuration.json", base)
    node = {"allocated_cpus": 12, "allocated_gpus": 4, "generation_gpu_ids": [0, 2, 3],
            "generation_cpu_ids": list(range(200, 212)), "rollout_workers_per_gpu": [6, 0, 5, 4]}
    fleet = {"archive_root": str(tmp_path), "code_directory": "code", "name": "fleet",
             "serving_probe_request": "probe.json", "rollout_workers_per_gpu": [6] * 8,
             "nodes": {"7": node}}
    write_json(tmp_path / "fleet.json", fleet)
    seen, ports, inflight = set(), set(), 0
    for gpu in [0, 2, 3]:
        result = fleet_module.measurement_config(tmp_path, {}, {"allocation": 7, "gpu": gpu})
        resources = result["resources"]
        validate_generation_resources(resources)
        assert resources["allocated_cpus"] == 12 and resources["allocated_gpus"] == 4
        assert "allocated_cpu_ids" not in resources
        assert resources["device_groups"] == [str(gpu)] and resources["tensor_parallel_per_engine"] == 1
        assert not seen.intersection(resources["generation_cpu_ids"])
        seen.update(resources["generation_cpu_ids"])
        inflight += resources["rollout_workers"]
        ports.update([resources["port_first"], resources["nccl_port_first"]])
        assert all(result[key] == base[key] for key in ("attempt", "inference_seeds", "checkpoint_policy"))
    assert seen == set(node["generation_cpu_ids"]) and inflight == 15 and len(ports) == 6
    node["allocated_cpu_ids"] = list(range(200, 212))
    write_json(tmp_path / "fleet.json", fleet)
    resources = fleet_module.measurement_config(tmp_path, {}, {"allocation": 7, "gpu": 0})["resources"]
    assert resources["allocated_cpu_ids"] == node["allocated_cpu_ids"]
    validate_generation_resources(resources)
    with pytest.raises(ValueError, match="not eligible"):
        fleet_module.measurement_config(tmp_path, {}, {"allocation": 7, "gpu": 1})
    node["rollout_workers_per_gpu"] = [6] * 8
    write_json(tmp_path / "fleet.json", fleet)
    with pytest.raises(ValueError, match="rollout_workers_per_gpu"):
        fleet_module.measurement_config(tmp_path, {}, {"allocation": 7, "gpu": 0})
    node["rollout_workers_per_gpu"] = [6] * 4
    node["generation_cpu_ids"] = [200, 201]
    write_json(tmp_path / "fleet.json", fleet)
    with pytest.raises(ValueError, match="at least one CPU"):
        fleet_module.measurement_config(tmp_path, {}, {"allocation": 7, "gpu": 3})


def test_scorer_validates_affinity_before_start_without_requesting_gpus(tmp_path, monkeypatch):
    from behave.eval.fleet import start_scores

    fleet = {"archive_root": str(tmp_path), "code_directory": "code", "scoring_workers": 2,
             "nodes": {"7": {"allocated_cpus": 4, "allocated_gpus": 2,
                             "allocated_cpu_ids": [100, 101, 102, 103], "scoring_cpu_ids": [100, 101]}}}
    write_json(tmp_path / "fleet.json", fleet)
    calls = []
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: calls.append((a, kw)))
    start_scores(tmp_path, 7)
    command = calls[0][0][1]
    assert "--cpus-per-task=4" in command and "--gres=none" in command
    assert not any(arg.startswith("--gpus") for arg in command)
    fleet["nodes"]["7"]["scoring_cpu_ids"] = [100, 999]
    write_json(tmp_path / "fleet.json", fleet)
    with pytest.raises(ValueError, match="outside allocated_cpu_ids"):
        start_scores(tmp_path, 7)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "ports",
    [
        {"http_first": 24000, "nccl_first": 24000, "slot_stride": 32},
        {"http_first": 24000, "nccl_first": 25000, "slot_stride": 0},
        {"http_first": 1000, "nccl_first": 25000, "slot_stride": 32},
        {"http_first": 32700, "nccl_first": 25000, "slot_stride": 32},
    ],
)
def test_measurement_rejects_colliding_or_ephemeral_ports(tmp_path, ports):
    write_json(tmp_path / "configuration.json", {"resources": {}})
    write_json(tmp_path / "fleet.json", {"archive_root": str(tmp_path), "serving_ports": ports})
    with pytest.raises(ValueError, match="serving ports"):
        fleet_module.measurement_config(tmp_path, {}, {"allocation": 7, "gpu": 0})
