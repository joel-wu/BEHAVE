"""Continuous replay groups exact memory policies without repeated RAM expansion."""

from dataclasses import replace

import pytest

from behave.sim import api
from tests.sim.test_memory_result_score import setup


def test_replay_suite_expands_a_shared_memory_image_once_for_grouping(monkeypatch):
    task, _ = setup(width=6)
    environment = api.MemoryEnvironment([api.MemoryRun.from_policy(
        task.contract.memories[0], None, initial={i: i for i in range(64)})])
    requests = tuple(api.Stimulus(queues={"in": [(i,)] * 100},
        replay_policy=api.ReplayPolicy(), memory_environment=environment) for i in range(50))
    expansions = []
    original = api.MemoryEnvironment.as_dict

    def observe(self):
        expansions.append(self)
        return original(self)

    monkeypatch.setattr(api.MemoryEnvironment, "as_dict", observe)
    suite = api.build_replay_suite(task.contract, requests)
    continuous, = suite.continuous
    # Once for grouping, once for the ordinary new stimulus's identity.
    assert expansions == [environment, environment]
    assert continuous.memory_environment is environment
    assert continuous.depth("in") == 5000
    assert dict(continuous.queues)["in"] == tuple((i,) for i in range(50) for _ in range(100))
    assert continuous.replay_policy == replace(requests[0].replay_policy, stall_every=0)
    assert suite.members == ((continuous.fingerprint, tuple(s.fingerprint for s in requests)),)


@pytest.mark.parametrize("change", [None, "initial", "read_latency", "ready_seed", "stall_rate"])
def test_replay_suite_groups_equal_memory_contents_not_object_identity(change):
    task, _ = setup()
    options = {"initial": {1: 7}, "read_latency": 2, "seed": 3, "stall_rate": 0.25}
    first = api.MemoryEnvironment([api.MemoryRun.from_policy(task.contract.memories[0], None, **options)])
    modified = dict(options)
    if change is not None:
        key, value = {"initial": ("initial", {1: 8}), "read_latency": ("read_latency", 3),
                      "ready_seed": ("seed", 4), "stall_rate": ("stall_rate", 0.5)}[change]
        modified[key] = value
    second = api.MemoryEnvironment([api.MemoryRun.from_policy(task.contract.memories[0], None, **modified)])
    assert first is not second
    requests = tuple(api.Stimulus(queues={"in": [(i,)]},
        replay_policy=api.ReplayPolicy(stall_every=3), memory_environment=environment)
        for i, environment in enumerate((first, second)))
    suite = api.build_replay_suite(task.contract, requests)
    assert len(suite.continuous) == (1 if change is None else 2)
    assert [s.depth("in") for s in suite.continuous] == ([2] if change is None else [1, 1])
    assert [s.memory_environment.as_dict() for s in suite.continuous] == (
        [first.as_dict()] if change is None else [first.as_dict(), second.as_dict()])
