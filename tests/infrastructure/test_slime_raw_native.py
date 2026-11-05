"""CPU native dependency lane: real parsers, Sample ABI and DP scheduling.

No Ray cluster, inference engine, CUDA context, model weights or optimizer is
created. Native optimization remains a later real-learning acceptance gate.
"""

import argparse

import pytest
from test_slime_raw import configured

from behave.train.slime_data import arguments, convert_samples, validate_args


@pytest.fixture(scope="module")
def native():
    pytest.importorskip("slime", reason="run in the pinned native CPU dependency environment")
    from slime.utils.arguments import get_slime_extra_args_provider

    parser = get_slime_extra_args_provider()(argparse.ArgumentParser())
    args, unknown = parser.parse_known_args((*arguments(), "--rollout-batch-size", "2", "--global-batch-size", "2"))
    # These two are parsed by the outer application and Slime's own mode
    # pre-parser; neither belongs to the Slime-extra-arguments sub-parser.
    assert unknown == ["--behave-learning-mode", "raw", "--debug-train-only"]
    args.debug_train_only = True
    args.use_critic = args.advantage_estimator == "ppo"  # Native slime_validate_args assignment.
    validate_args(args)
    return args


def test_native_samples_conversion_scheduling_and_logs(tmp_path, monkeypatch, native):
    import torch
    from slime.observability.rollout_data_utils import validate_rollout_id_annotated
    from slime.observability.rollout_metrics import log_rollout_data
    from slime.rollout.base_types import call_rollout_fn
    from slime.utils.dp_schedule import build_dp_schedule
    from slime.utils.types import Sample

    from behave.train.slime_data import generate

    source = configured(tmp_path, monkeypatch)
    output = call_rollout_fn(generate, source.args, 0, source, evaluation=False)
    assert all(type(sample) is Sample and sample.status == Sample.Status.COMPLETED for sample in output.samples)
    validate_rollout_id_annotated(output.samples)
    data = convert_samples(native, output.samples)
    assert "rewards" not in data and "rollout_log_probs" not in data
    native.micro_batch_size = 1
    native.use_dynamic_batch_size = False
    native.balance_data = False
    native.balance_by_flops = False
    partitions, _, microbatches, batches = build_dp_schedule(native,
        {"dp_size": 2, "cp_size": 1, "vpp_size": 1, "microbatch_group_size_per_vp_stage": 1},
        [len(tokens) for tokens in data["tokens"]], global_batch_size=2, rollout_indices=data["rollout_ids"])
    assert sorted(i for rank in partitions for i in rank) == [0, 1]
    assert microbatches == [1] and batches == [2]
    # Invokes the configured production hook, which must bypass the native RL
    # reward-statistics path rather than manufacturing reward=0 for Raw data.
    log_rollout_data(0, native, output.samples, output.metrics, .01)
    assert all(sample.reward is None and sample.rollout_log_probs is None for sample in output.samples)
    assert not torch.cuda.is_initialized()
