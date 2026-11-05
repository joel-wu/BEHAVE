"""Shared training-actor memory policy and rank-local OOM evidence.

No sample, loss, precision, parallelism or update-boundary changes. Actors are
configured before CUDA initialization, never the colocated SGL engines. OOM
propagates to the supervisor, not a retry inside a partly accumulated update.
"""

import json
import logging
import math
import os
import re
import time
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from time import monotonic

HOOK = "behave.train.slime_hooks.memory_before_step"
_ALIASES = ("PYTORCH_ALLOC_CONF", "PYTORCH_CUDA_ALLOC_CONF")
_memory_step = {}
_counts = {}
_directory = None


def actor_environment(environment):
    """Actor-only default, preserving explicit overrides and other options.

    Both aliases must agree: TorchMemorySaver and PyTorch may otherwise interpret
    different settings. Do not change the parent/serving process environment.
    """
    result = dict(environment)
    settings = {result[name].strip() for name in _ALIASES if name in result}
    if len(settings) > 1:
        raise ValueError("conflicting training allocator environment aliases")
    setting = next(iter(settings), "")
    backend = re.search(r"(?:^|,)\s*backend\s*:\s*([^,]+)", setting)
    expandable = re.search(r"(?:^|,)\s*expandable_segments\s*:\s*([^,]+)", setting)
    if expandable is None and (backend is None or backend[1].strip() == "native"):
        setting = ",".join(filter(None, (setting, "expandable_segments:True")))
    result.update({name: setting for name in _ALIASES})
    return result


def configure_memory(args):
    """Common driver policy for task RL, Syn and raw learning."""
    args.train_env_vars = actor_environment(args.train_env_vars)
    previous = args.custom_megatron_before_train_step_hook_path
    if previous != HOOK:
        args.behave_memory_next_hook = previous
        args.custom_megatron_before_train_step_hook_path = HOOK


def _record(event, **values):
    """No CUDA synchronization or distributed collectives, especially on OOM."""
    import torch
    import torch.distributed as dist

    try:
        stats = torch.cuda.memory_stats()
        free, total = torch.cuda.mem_get_info()
        row = {"event": event, "at": datetime.now(UTC).isoformat(),
               "rank": dist.get_rank(), **_memory_step, **values,
               "free_bytes": free, "total_bytes": total,
               "allocated_bytes": stats.get("allocated_bytes.all.current"),
               "reserved_bytes": stats.get("reserved_bytes.all.current"),
               "inactive_split_bytes": stats.get("inactive_split_bytes.all.current"),
               "peak_allocated_bytes": stats.get("allocated_bytes.all.peak"),
               "allocation_retries": stats.get("num_alloc_retries"),
               "oom_count": stats.get("num_ooms")}
        if event == "oom":
            row["summary"] = torch.cuda.memory_summary()
        if _directory is not None:
            _directory.mkdir(parents=True, exist_ok=True)
            with (_directory / f"rank_{row['rank']:02d}.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")
        if event in {"train_step_started", "oom"}:
            logger.warning("BEHAVE_TRAIN_MEMORY %s", json.dumps(row))
    except Exception:
        # Diagnostics must not replace the original OOM or strand peers because
        # this rank's filesystem/telemetry is unavailable.
        logger.exception("Could not record training memory event %s", event)


def _phase(call, name):
    @wraps(call)
    def measured(*args, **kwargs):
        import torch

        index = _counts.get(name, 0)
        _counts[name] = index + 1
        try:
            result = call(*args, **kwargs)
        except torch.OutOfMemoryError as error:
            _record("oom", phase=name, microbatch=index, error=str(error))
            raise
        _record(name + "_finished", microbatch=index)
        return result

    measured.behave_memory = True
    return measured


def memory_before_step(args, rollout_id, step_id, model, optimizer, opt_param_scheduler):
    import torch
    from megatron.core.pipeline_parallel import schedules
    from slime.utils.misc import load_function

    global _directory
    _directory = Path(args.behave_rollout_binding).parent / "train_memory"
    _memory_step.update(rollout=rollout_id, step=step_id)
    _counts.clear()
    # Safe boundary before forward/backward: live model/optimizer tensors stay
    # intact. No per-microbatch flush or additional collective.
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    _record("train_step_started", allocator={key: os.environ.get(key) for key in _ALIASES})
    previous = getattr(args, "behave_memory_next_hook", None)
    if previous:
        load_function(previous)(args, rollout_id, step_id, model, optimizer, opt_param_scheduler)
    for name in ("forward_step", "backward_step"):
        call = getattr(schedules, name)
        if not getattr(call, "behave_memory", False):
            setattr(schedules, name, _phase(call, name))


STEP_HOOK = "behave.train.slime_hooks.before_train_step"


def configure_scheduler(args) -> None:
    interval = getattr(args, "behave_hf_checkpoint_interval", None)
    milestones = getattr(args, "behave_hf_checkpoint_updates", None)
    if milestones is not None:
        if not milestones or any(step < 1 or step % args.save_interval for step in milestones):
            raise ValueError("HF update milestones must coincide with native saves")
        args.behave_hf_save_pattern = args.save_hf
    elif interval is not None:
        if interval < 1 or interval % args.save_interval:
            raise ValueError("HF cadence must be a positive multiple of the native save interval")
        args.behave_hf_save_pattern = args.save_hf
    selected = getattr(args, "custom_megatron_before_train_step_hook_path", None)
    if selected != STEP_HOOK:
        args.behave_after_scheduler_hook = selected
    args.custom_megatron_before_train_step_hook_path = STEP_HOOK


def before_train_step(args, rollout_id, step_id, model, optimizer, opt_param_scheduler) -> None:
    """Native public hook signature; refresh LR/WD without advancing progress."""
    opt_param_scheduler.step(increment=0)
    interval = getattr(args, "behave_hf_checkpoint_interval", None)
    milestones = getattr(args, "behave_hf_checkpoint_updates", None)
    if milestones is not None or interval is not None:
        # Keep native optimizer/RNG serialization upstream. Only decide whether
        # its existing save_model call also emits the optional HF export.
        due = ((rollout_id + 1 in milestones) if milestones is not None
               else (rollout_id + 1) % interval == 0)
        due = due or rollout_id == args.num_rollout - 1
        args.save_hf = args.behave_hf_save_pattern if due else None
    hook = getattr(args, "behave_after_scheduler_hook", None)
    if hook is not None:
        from slime.utils.misc import load_function

        load_function(hook)(args, rollout_id, step_id, model, optimizer, opt_param_scheduler)


DEFAULT_STARTUP_SECONDS = 1800.0
logger = logging.getLogger(__name__)


def wait_ready(actors, *, ray, seconds, clock=time.monotonic):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("actor startup seconds must be finite and positive")
    began = clock()
    pending = {actor.get_master_addr_and_port.remote(): rank for rank, actor in enumerate(actors)}
    total = len(pending)
    logger.warning("BEHAVE_ACTOR_STARTUP waiting for %d actor constructors", total)
    while pending:
        remaining = seconds - (clock() - began)
        if remaining <= 0:
            raise TimeoutError(f"actor construction timed out after {seconds:g}s; "
                               f"pending ranks={sorted(pending.values())}; distributed initialization has not started")
        ready, _ = ray.wait(list(pending), num_returns=1, timeout=min(30.0, remaining))
        for ref in ready:
            ray.get(ref)  # Propagate constructor/import failures before any rank enters a collective.
            del pending[ref]
        logger.warning("BEHAVE_ACTOR_STARTUP ready=%d/%d elapsed=%.1fs pending_ranks=%s",
                       total - len(pending), total, clock() - began, sorted(pending.values()))


def configure_startup(*, group_class=None, ray_module=None):
    """Wrap the pinned allocation boundary; do not copy upstream create/train."""
    if group_class is None:
        from slime.ray.actor_group import RayTrainGroup

        group_class = RayTrainGroup
    if ray_module is None:
        import ray as ray_module
    allocate = group_class._allocate_gpus_for_actor
    if getattr(allocate, "behave_startup_ready", False):
        return

    @wraps(allocate)
    def allocate_ready(self, *args, **kwargs):
        result = allocate(self, *args, **kwargs)
        wait_ready(self._actor_handlers, ray=ray_module,
                   seconds=getattr(self.args, "behave_actor_startup_seconds", DEFAULT_STARTUP_SECONDS))
        return result

    allocate_ready.behave_startup_ready = True
    group_class._allocate_gpus_for_actor = allocate_ready


HOOK_ARGUMENTS = ("--custom-megatron-before-train-step-hook-path", "behave.train.slime_hooks.timing_before_step")
_timing_step = {}
_microbatch_counts = {}
_microbatch_limit = 0


def _event(name, **values):
    import torch
    import torch.distributed as dist

    torch.cuda.synchronize()
    row = {"event": name, "at": datetime.now(UTC).isoformat(),
           "monotonic_seconds": monotonic(), "rank": dist.get_rank(), **_timing_step, **values}
    root = Path(os.environ["BEHAVE_TRAIN_TIMING_DIRECTORY"])
    root.mkdir(parents=True, exist_ok=True)
    with (root / f"rank_{row['rank']:02d}.jsonl").open("a") as stream:
        stream.write(json.dumps(row) + "\n")


def timing_before_step(args, rollout_id, step_id, model, optimizer, opt_param_scheduler):
    from slime.backends.megatron_utils import model as native_model

    _timing_step.update(rollout=rollout_id, step=step_id)
    global _microbatch_limit
    _microbatch_limit = int(os.environ.get("BEHAVE_TRAIN_TIMING_MICROBATCHES", "0"))
    _microbatch_counts.clear()
    if _microbatch_limit:
        from megatron.core.pipeline_parallel import schedules

        for name in ("forward_step", "backward_step"):
            call = getattr(schedules, name)
            if not getattr(call, "behave_timed", False):
                setattr(schedules, name, _time_microbatch(call, name))
    if not getattr(native_model.get_forward_backward_func, "behave_timed", False):
        select = native_model.get_forward_backward_func

        @wraps(select)
        def timed_select(*args, **kwargs):
            forward_backward = select(*args, **kwargs)

            @wraps(forward_backward)
            def timed_forward_backward(*args, **kwargs):
                _event("forward_backward_started", microbatches=kwargs["num_microbatches"])
                result = forward_backward(*args, **kwargs)
                _event("forward_backward_finished")
                return result

            return timed_forward_backward

        timed_select.behave_timed = True
        native_model.get_forward_backward_func = timed_select
    if not getattr(optimizer.step, "behave_timed", False):
        advance = optimizer.step

        @wraps(advance)
        def timed_advance(*args, **kwargs):
            _event("optimizer_started")
            result = advance(*args, **kwargs)
            _event("optimizer_finished", update_successful=bool(result[0]), grad_norm=float(result[1]))
            return result

        timed_advance.behave_timed = True
        optimizer.step = timed_advance
    _event("train_step_started")


def _time_microbatch(call, name):
    """Profile the runtime-selected prefix, without shortening native training."""
    @wraps(call)
    def timed(*args, **kwargs):
        index = _microbatch_counts.get(name, 0)
        _microbatch_counts[name] = index + 1
        if index >= _microbatch_limit:
            return call(*args, **kwargs)
        _event(name + "_started", microbatch=index)
        result = call(*args, **kwargs)
        _event(name + "_finished", microbatch=index)
        return result

    timed.behave_timed = True
    return timed
