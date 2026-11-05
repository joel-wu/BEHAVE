"""CPU identifiers are topology labels, not an allocation size."""

import json

import pytest

from behave.eval import checkpoint as checkpoint_module
from behave.eval import scoring as scoring_module
from behave.runtime.records import write_json
from tools import eval_score_job
from behave.eval import resources


@pytest.mark.parametrize("changes,valid", [
    ({"allocated_cpus": 188}, True),
    ({"allocated_cpus": 4}, False),
    ({"allocated_cpu_ids": list(range(188))}, False),
])
def test_stages_share_effective_capacity_and_inventory(tmp_path, monkeypatch, changes, valid):
    roots = [tmp_path / "a", tmp_path / "b"]
    resources = {"allocation": 7, "scoring_cpus": 2, "scoring_workers": 1, "scoring_cpu_ids": [0, 1]}
    for root, extra in zip(roots, [{}, changes], strict=True):
        write_json(root / "configuration.json", {"resources": {**resources, **extra}})
    calls = []
    monkeypatch.setattr(checkpoint_module, "_generate", lambda *a, **kw: calls.append(a) or 0)
    if valid:
        assert checkpoint_module.run_stages(roots, repository=tmp_path) == 0
        assert len(calls) == 2
    else:
        with pytest.raises(ValueError, match="share one allocation and scoring resource budget"):
            checkpoint_module.run_stages(roots, repository=tmp_path)
        assert not calls


@pytest.mark.parametrize("cpu_ids", [None, [100, 103], (100, 103)])
def test_scoring_budget_is_distinct_from_step_allocation(tmp_path, monkeypatch, cpu_ids):
    write_json(tmp_path / "plan.json", {"shards": [[]]})
    calls = []
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: calls.append((a, kw)) or {})
    inventory = [100, 103, 106, 109]
    scoring_module.launch(tmp_path, 7, 2, cpu_ids=cpu_ids, allocated_cpus=4, allocated_cpu_ids=inventory)
    command = calls[0][0][1]
    assert f"--cpus-per-task={2 if cpu_ids is None else 4}" in command
    assert ("--cpu-bind=threads" in command) == (cpu_ids is None)
    metadata = calls[0][1]["metadata"]
    assert metadata["cpus"] == 2 and metadata["allocated_cpus"] == 4
    assert metadata["allocated_cpu_ids"] == inventory


@pytest.mark.parametrize("options", [
    {"allocated_cpus": 1},
    {"allocated_cpus": False},
    {"allocated_cpu_ids": [100, 103]},
    {"allocated_cpus": 4, "allocated_cpu_ids": [100, 103]},
    {"allocated_cpus": 4, "cpu_ids": 3},
])
def test_invalid_allocation_rejected_even_without_a_usable_mask(tmp_path, monkeypatch, options):
    write_json(tmp_path / "plan.json", {"shards": [[]]})
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("invalid launch"))
    with pytest.raises(ValueError):
        scoring_module.launch(tmp_path, 7, 2, **options)


@pytest.mark.parametrize("entrypoint", [scoring_module.launch, eval_score_job.launch])
@pytest.mark.parametrize("ids,capacity", [([100, 103], 4), (list(range(40, 94)), 188)])
def test_explicit_affinity_uses_declared_allocation(tmp_path, monkeypatch, entrypoint, ids, capacity):
    write_json(tmp_path / "plan.json", {"shards": [[]]})
    calls = []
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: calls.append((a, kw)) or {})
    entrypoint(tmp_path, 7, len(ids), cpu_ids=ids, allocated_cpus=capacity)
    command = calls[0][0][1]
    assert f"--cpus-per-task={capacity}" in command
    assert f"--cpu-bind=mask_cpu:{hex(sum(1 << cpu for cpu in ids))}" in command
    assert calls[0][1]["metadata"]["cpus"] == len(ids)
    assert calls[0][1]["metadata"]["allocated_cpus"] == capacity


@pytest.mark.parametrize("options,match", [
    ({"cpu_ids": [100, 103]}, "allocated_cpus"),
    ({"cpu_ids": [100, 103], "allocated_cpus": 1}, "exceed"),
    ({"cpu_ids": [100, 103], "allocated_cpus": True}, "positive integer"),
    ({"cpu_ids": [100, 103], "allocated_cpus": 4,
      "allocated_cpu_ids": [100, 101, 102, 104]}, "outside allocated_cpu_ids"),
    ({"cpu_ids": [100, 103], "allocated_cpus": 4,
      "allocated_cpu_ids": [100, 101, 102]}, "enumerate"),
    ({"cpu_ids": [100, 103], "allocated_cpus": 4,
      "allocated_cpu_ids": [100, 101, 103, 103]}, "enumerate"),
])
def test_bad_affinity_fails_before_submit(tmp_path, monkeypatch, options, match):
    write_json(tmp_path / "plan.json", {"shards": [[]]})
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("invalid launch"))
    with pytest.raises(ValueError, match=match):
        scoring_module.launch(tmp_path, 7, 2, **options)
    assert not (tmp_path / "control").exists()


def test_real_noncontiguous_topology_rejects_missing_cpus(tmp_path, monkeypatch):
    write_json(tmp_path / "plan.json", {"shards": [[]]})
    allocated = [*range(94), *range(96, 190)]
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("invalid topology"))
    with pytest.raises(ValueError, match="outside allocated_cpu_ids"):
        scoring_module.launch(tmp_path, 7, 90, cpu_ids=list(range(40, 130)),
                         allocated_cpus=188, allocated_cpu_ids=allocated)


@pytest.mark.parametrize("field,value", [
    ("scoring_cpu_ids", [0, 100]),
    ("scoring_cpu_ids", [100, 102]),
    ("scoring_cpu_ids", [100]),
    ("allocated_cpus", 1),
])
def test_study_rejects_invalid_resources_before_freezing_inputs(tmp_path, monkeypatch, field, value):
    resources = {"allocation": 7, "allocated_cpus": 4, "allocated_cpu_ids": [100, 101, 102, 103],
                 "scoring_cpus": 2, "scoring_workers": 2, "scoring_cpu_ids": [100, 101],
                 "generation_cpu_ids": [102, 103]}
    resources[field] = value
    write_json(tmp_path / "configuration.json", {"resources": resources})
    monkeypatch.setattr(scoring_module, "prepare", lambda *a, **kw: pytest.fail("invalid preparation"))
    monkeypatch.setattr(scoring_module, "launch", lambda *a, **kw: pytest.fail("invalid launch"))
    with pytest.raises(ValueError):
        checkpoint_module.score(tmp_path, {"model_line": "model", "update": 3}, 17, repository=tmp_path)
    assert not (tmp_path / "model").exists()


def test_study_forwards_capacity_and_available_ids(tmp_path, monkeypatch):
    resources = {"allocation": 7, "allocated_cpus": 4, "allocated_cpu_ids": [100, 101, 102, 103],
                 "scoring_cpus": 2, "scoring_workers": 2, "scoring_cpu_ids": [100, 101],
                 "generation_cpu_ids": [102, 103]}
    write_json(tmp_path / "configuration.json", {"resources": resources, "inputs": {"tasks": 1}})
    output = tmp_path / "model/update-003/eval-seed-17/scoring/baseline"
    write_json(output / "plan.json", {})
    calls = []
    monkeypatch.setattr(scoring_module, "launch", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(checkpoint_module, "wait_control", lambda *a, **kw: {"state": "completed"})
    monkeypatch.setattr(checkpoint_module, "scoring_quality", lambda *a: {"complete": True})
    assert checkpoint_module.score(tmp_path, {"model_line": "model", "update": 3}, 17, repository=tmp_path)
    assert calls[0]["allocated_cpus"] == 4
    assert calls[0]["allocated_cpu_ids"] == [100, 101, 102, 103]
    assert calls[0]["cpu_ids"] == [100, 101]


@pytest.mark.parametrize("state", ["supervising", "completed", "failed"])
def test_saved_launch_is_observed_without_reinterpreting_old_affinity(tmp_path, monkeypatch, state):
    write_json(tmp_path / "plan.json", {"shards": [[]]})
    write_json(tmp_path / "control/shard-0/launch.json", {"historical": True})
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda *a: {"state": state})
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("duplicate launch"))
    assert scoring_module.launch(tmp_path, 7, 2, cpu_ids=[100, 103]) == {"state": state}
    assert json.loads((tmp_path / "control/shard-0/launch.json").read_text()) == {"historical": True}
