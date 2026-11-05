"""Native scheduler hook ownership and zero-progress refresh contract."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from behave.train.slime_hooks import STEP_HOOK, before_train_step
from behave.train.slime_hooks import configure_scheduler as configure


def test_scheduler_hook_preserves_the_explicit_extension():
    args = SimpleNamespace(custom_megatron_before_train_step_hook_path=None)
    configure(args)
    assert args.custom_megatron_before_train_step_hook_path == STEP_HOOK
    configure(args)
    args.custom_megatron_before_train_step_hook_path = "another.before_step"
    configure(args)
    assert args.custom_megatron_before_train_step_hook_path == STEP_HOOK
    assert args.behave_after_scheduler_hook == "another.before_step"
    configure(args)
    assert args.behave_after_scheduler_hook == "another.before_step"


def test_extension_runs_after_the_zero_progress_refresh(monkeypatch):
    import sys

    calls = []
    extension = lambda *args: calls.append("extension")
    monkeypatch.setitem(sys.modules, "slime.utils.misc", SimpleNamespace(load_function=lambda name: extension))
    scheduler = SimpleNamespace(step=lambda **kwargs: calls.append(kwargs))
    before_train_step(SimpleNamespace(behave_after_scheduler_hook="timing"), 1, 0, None, None, scheduler)
    assert calls == [{"increment": 0}, "extension"]


def test_scheduler_hook_reuses_native_step_without_consuming_samples():
    scheduler = Mock()
    before_train_step(None, 2, 0, None, None, scheduler)
    scheduler.step.assert_called_once_with(increment=0)


def test_native_recovery_each_update_with_separate_hf_milestones():
    args = SimpleNamespace(behave_hf_checkpoint_interval=5, save_interval=1,
                           save_hf="plan/hf_{rollout_id}", num_rollout=20)
    configure(args)
    scheduler = Mock()
    for update in range(5, 20):
        before_train_step(args, update, 0, None, None, scheduler)
        assert args.save_interval == 1
        assert args.save_hf == ("plan/hf_{rollout_id}" if update in (9, 14, 19) else None)
    assert scheduler.step.call_count == 15


def test_final_update_exports_hf_even_between_milestones():
    args = SimpleNamespace(behave_hf_checkpoint_interval=5, save_interval=1,
                           save_hf="hf_{rollout_id}", num_rollout=7)
    configure(args)
    before_train_step(args, 6, 0, None, None, Mock())
    assert args.save_hf == "hf_{rollout_id}"


def test_explicit_study_boundaries_override_five_update_cadence_on_resume():
    args = SimpleNamespace(behave_hf_checkpoint_interval=5,
                           behave_hf_checkpoint_updates=[3, 6, 9, 12, 15, 18],
                           save_interval=1, save_hf="hf_{rollout_id}", num_rollout=18)
    configure(args)
    for rollout_id in range(3, 18):
        before_train_step(args, rollout_id, 0, None, None, Mock())
        assert args.save_hf == ("hf_{rollout_id}" if rollout_id + 1 in (6, 9, 12, 15, 18) else None)
        assert args.save_interval == 1


def test_explicit_hf_milestones_do_not_replace_native_checkpoint_saves():
    args = SimpleNamespace(behave_hf_checkpoint_updates=[3, 6], save_interval=2,
                           save_hf="hf_{rollout_id}")
    with pytest.raises(ValueError, match="coincide with native saves"):
        configure(args)


@pytest.mark.parametrize("interval", [0, 3])
def test_hf_milestones_must_coincide_with_native_saves(interval):
    args = SimpleNamespace(behave_hf_checkpoint_interval=interval, save_interval=2,
                           save_hf="hf_{rollout_id}")
    with pytest.raises(ValueError, match="positive multiple"):
        configure(args)
