"""Training-only allocator policy and transparent native OOM instrumentation."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

from behave.train import slime_hooks as slime_hooks_module


def test_default_policy_is_actor_only_and_preserves_other_environment(monkeypatch):
    monkeypatch.setenv("PYTORCH_ALLOC_CONF", "max_split_size_mb:64")
    before = dict(os.environ)
    supplied = {"OMP_NUM_THREADS": "2"}
    result = slime_hooks_module.actor_environment(supplied)
    assert result == {**supplied, "PYTORCH_ALLOC_CONF": "expandable_segments:True",
                      "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}
    assert supplied == {"OMP_NUM_THREADS": "2"} and dict(os.environ) == before


@pytest.mark.parametrize("alias", slime_hooks_module._ALIASES)
@pytest.mark.parametrize("setting,expected", [
    ("max_split_size_mb:128", "max_split_size_mb:128,expandable_segments:True"),
    ("backend:native,roundup_power2_divisions:[256:1,512:2,>:8]",
     "backend:native,roundup_power2_divisions:[256:1,512:2,>:8],expandable_segments:True"),
    ("expandable_segments:False", "expandable_segments:False"),
    ("backend:cudaMallocAsync", "backend:cudaMallocAsync"),
])
def test_explicit_allocator_options_survive(alias, setting, expected):
    result = slime_hooks_module.actor_environment({alias: setting})
    assert all(result[key] == expected for key in slime_hooks_module._ALIASES)
    assert slime_hooks_module.actor_environment(result) == result


def test_conflicting_aliases_are_rejected():
    with pytest.raises(ValueError, match="conflicting"):
        slime_hooks_module.actor_environment({"PYTORCH_ALLOC_CONF": "expandable_segments:True",
                                       "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:False"})


@pytest.mark.parametrize("previous", [None, "behave.train.slime_hooks.timing_before_step", "custom.hook"])
def test_configure_preserves_hook_and_training_settings(previous):
    args = SimpleNamespace(train_env_vars={}, custom_megatron_before_train_step_hook_path=previous,
                           seq_length=131072, micro_batch_size=1, tensor_model_parallel_size=4,
                           context_parallel_size=2, seed=20260920)
    before = vars(args).copy()
    slime_hooks_module.configure_memory(args)
    slime_hooks_module.configure_memory(args)
    assert args.custom_megatron_before_train_step_hook_path == slime_hooks_module.HOOK
    assert args.behave_memory_next_hook == previous
    for key in before.keys() - {"train_env_vars", "custom_megatron_before_train_step_hook_path"}:
        assert getattr(args, key) == before[key]


def test_native_calls_hook_and_oom_are_preserved(monkeypatch, tmp_path):
    events, calls = [], []
    sentinel = object()

    class OOM(RuntimeError):
        pass

    def phase(value):
        calls.append(value)
        if isinstance(value, Exception):
            raise value
        return value

    cuda = SimpleNamespace(empty_cache=lambda: calls.append("clear"),
                           reset_peak_memory_stats=lambda: calls.append("reset"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda, OutOfMemoryError=OOM))
    schedules = SimpleNamespace(forward_step=phase, backward_step=phase)
    monkeypatch.setitem(sys.modules, "megatron.core.pipeline_parallel", SimpleNamespace(schedules=schedules))
    monkeypatch.setitem(sys.modules, "slime.utils.misc", SimpleNamespace(
        load_function=lambda _: lambda *a: calls.append("previous")))
    monkeypatch.setattr(slime_hooks_module, "_memory_step", {})
    monkeypatch.setattr(slime_hooks_module, "_counts", {})
    monkeypatch.setattr(slime_hooks_module, "_directory", None)
    monkeypatch.setattr(slime_hooks_module, "_record", lambda name, **kw: events.append(
        {"event": name, **slime_hooks_module._memory_step, **kw}))
    args = SimpleNamespace(behave_rollout_binding=str(tmp_path / "binding.json"),
                           behave_memory_next_hook="previous")
    for rollout in (10, 11):
        slime_hooks_module.memory_before_step(args, rollout, 0, None, None, None)
        assert schedules.forward_step(sentinel) is sentinel
        assert schedules.backward_step(sentinel) is sentinel
    error = OOM("allocation failed")
    with pytest.raises(OOM) as caught:
        schedules.forward_step(error)
    assert caught.value is error
    assert calls == ["clear", "reset", "previous", sentinel, sentinel] * 2 + [error]
    assert events[-1] == {"event": "oom", "rollout": 11, "step": 0,
                           "phase": "forward_step", "microbatch": 1, "error": str(error)}


def test_oom_evidence_uses_no_synchronization_or_collectives(monkeypatch, tmp_path):
    cuda = SimpleNamespace(memory_stats=lambda: {"inactive_split_bytes.all.current": 123},
                           mem_get_info=lambda: (100, 1000), memory_summary=lambda: "allocator summary")
    torch = SimpleNamespace(cuda=cuda)
    dist = SimpleNamespace(get_rank=lambda: 3)
    torch.distributed = dist
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch.distributed", dist)
    monkeypatch.setattr(slime_hooks_module, "_memory_step", {"rollout": 11, "step": 0})
    monkeypatch.setattr(slime_hooks_module, "_directory", tmp_path)
    slime_hooks_module._record("oom", phase="forward_step", microbatch=2)
    row = json.loads((tmp_path / "rank_03.jsonl").read_text())
    assert row["inactive_split_bytes"] == 123 and row["summary"] == "allocator summary"
    assert row["rollout"] == 11 and row["microbatch"] == 2


def test_scheduler_and_memory_hooks_chain_without_recursion(monkeypatch, tmp_path):
    from behave.train import slime_hooks as slime_hooks_module

    calls = []
    cuda = SimpleNamespace(empty_cache=lambda: None, reset_peak_memory_stats=lambda: None)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))
    schedules = SimpleNamespace(forward_step=lambda: None, backward_step=lambda: None)
    monkeypatch.setitem(sys.modules, "megatron.core.pipeline_parallel", SimpleNamespace(schedules=schedules))
    hooks = {slime_hooks_module.HOOK: slime_hooks_module.memory_before_step, "original": lambda *a: calls.append("original")}
    monkeypatch.setitem(sys.modules, "slime.utils.misc", SimpleNamespace(load_function=hooks.__getitem__))
    monkeypatch.setattr(slime_hooks_module, "_record", lambda *a, **kw: None)
    monkeypatch.setattr(slime_hooks_module, "_directory", None)
    monkeypatch.setattr(slime_hooks_module, "_memory_step", {})
    monkeypatch.setattr(slime_hooks_module, "_counts", {})
    args = SimpleNamespace(train_env_vars={}, custom_megatron_before_train_step_hook_path="original",
                           behave_rollout_binding=str(tmp_path / "binding.json"))
    slime_hooks_module.configure_memory(args)
    slime_hooks_module.configure_scheduler(args)
    scheduler = SimpleNamespace(step=lambda **kw: calls.append(kw))
    slime_hooks_module.before_train_step(args, 11, 0, None, None, scheduler)
    assert calls == [{"increment": 0}, "original"]
