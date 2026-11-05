"""Reproduce the horizon mismatch; compare native resumed Adam to continuation."""

import argparse
import copy

import torch
from megatron.core.optimizer_param_scheduler import OptimizerParamScheduler
from megatron.training.arguments import add_megatron_arguments

from behave.train.slime_hooks import STEP_HOOK, before_train_step
from behave.train.slime_hooks import configure_scheduler as configure


def build(style, horizon, *, saved=True, wd_style="constant"):
    parameter = torch.nn.Parameter(torch.tensor([1., -2.], dtype=torch.float64))
    optimizer = torch.optim.AdamW([parameter], lr=.01)
    scheduler = OptimizerParamScheduler(optimizer, init_lr=0., max_lr=.01, min_lr=.001,
        lr_warmup_steps=1, lr_decay_steps=horizon, lr_decay_style=style,
        start_wd=.1, end_wd=.1 if wd_style == "constant" else .3,
        wd_incr_steps=horizon, wd_incr_style=wd_style,
        use_checkpoint_opt_param_scheduler=saved, override_opt_param_scheduler=False)
    return parameter, optimizer, scheduler


def step(state, *, refresh=True):
    parameter, optimizer, scheduler = state
    optimizer.zero_grad()
    if refresh:
        before_train_step(None, 0, 0, None, optimizer, scheduler)
    (parameter.square().sum()).backward()
    optimizer.step()
    scheduler.step(1)


# Use the pinned parser, not an invented CLI switch.
parser = add_megatron_arguments(argparse.ArgumentParser())
args, _ = parser.parse_known_args(["--use-checkpoint-opt-param-scheduler"])
assert args.use_checkpoint_opt_param_scheduler and not args.override_opt_param_scheduler
configure(args)
assert args.custom_megatron_before_train_step_hook_path == STEP_HOOK


def check(style, wd_style, restored_horizon):
    original = build(style, 4, wd_style=wd_style)
    step(original)
    step(original)
    saved = copy.deepcopy(original[1].state_dict()), copy.deepcopy(original[2].state_dict())
    rejected = build(style, restored_horizon, saved=False, wd_style=wd_style)
    try:
        rejected[2].load_state_dict(saved[1])
    except AssertionError as error:
        assert "total number of iterations" in str(error)
    else:
        raise AssertionError("negative control did not reproduce the native horizon mismatch")
    resumed = build(style, restored_horizon, wd_style=wd_style)
    with torch.no_grad():
        resumed[0].copy_(original[0])
    resumed[1].load_state_dict(saved[0])
    resumed[2].load_state_dict(saved[1])
    assert resumed[2].state_dict() == original[2].state_dict()
    assert resumed[2].num_steps == 2 and resumed[2].lr_decay_steps == 4
    assert resumed[1].param_groups[0]["lr"] == original[1].param_groups[0]["lr"]
    if wd_style != "constant":
        # Exact native negative control: restored metadata agrees, but the
        # optimizer still holds WD computed with the new launch horizon.
        assert resumed[1].param_groups[0]["weight_decay"] != original[1].param_groups[0]["weight_decay"]
    before_train_step(None, 0, 0, None, resumed[1], resumed[2])
    assert resumed[2].num_steps == 2
    for key in ("lr", "weight_decay"):
        assert resumed[1].param_groups[0][key] == original[1].param_groups[0][key]
    for _ in range(3):
        step(original, refresh=False)  # Uninterrupted upstream execution.
        step(resumed)
        torch.testing.assert_close(original[0], resumed[0], rtol=0, atol=0)
        for key in ("step", "exp_avg", "exp_avg_sq"):
            torch.testing.assert_close(original[1].state[original[0]][key],
                                       resumed[1].state[resumed[0]][key], rtol=0, atol=0)
        assert resumed[2].state_dict() == original[2].state_dict()


for style in ("constant", "linear", "cosine"):
    for wd_style in ("constant", "linear", "cosine"):
        for restored_horizon in (3, 6):
            check(style, wd_style, restored_horizon)
assert not torch.cuda.is_initialized()
print("BEHAVE_SCHEDULER_RESUME_OK")
