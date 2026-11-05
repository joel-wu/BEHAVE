"""Native long-context memory probe, NOT a scored rollout or learning acceptance.

Use Slime's forge-load extension so both layouts train exactly the same token
batch while real SGLang engines, Adam and weight/offload transfers stay alive.
The separate pipeline mode replaces the responses with real fixed-length
generation; both modes use synthetic rewards, not BEHAVE scores.
"""

import json
import os
import random
import runpy
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from urllib.request import Request, urlopen


def shape_only_logprobs(args, response_length):
    """Forge-load needs a KL tensor shape even when old-policy recompute is fused.

    These are NOT generation evidence. Under the guarded configuration native
    Slime uses zeros_like for KL, and the actual training forward supplies the
    detached old-policy probabilities. Pipeline mode must replace this field
    with real generated logprobs before handing the batch to Slime.
    """
    if (args.kl_coef != 0 or args.use_rollout_logprobs or args.use_tis or args.get_mismatch_metrics
            or args.use_kl_loss or args.use_opd or args.use_opsm or args.advantage_estimator != "grpo"):
        raise ValueError("shape-only logprobs cannot be used by the training objective")
    return [0.0] * response_length


def generated_tokens(body, prompt_length, response_length):
    """Require actual native token/logprob evidence, never count requested tokens."""
    import math

    meta = body["meta_info"]
    rows = meta["output_token_logprobs"]
    if (meta["prompt_tokens"] != prompt_length or meta["completion_tokens"] != response_length
            or len(rows) != response_length):
        raise ValueError("generation did not actually reach the requested context length")
    if any(type(row[1]) is not int or row[1] < 0 or type(row[0]) not in (int, float)
           or not math.isfinite(row[0]) for row in rows):
        raise ValueError("invalid native generation evidence")
    return [row[1] for row in rows], [row[0] for row in rows]


def generate_samples(args, rollout_id, samples, versions):
    """Bounded fixed-worker requests; all samples use one frozen policy version."""
    def generate(index):
        sample = samples[index]
        worker, version = versions[index % len(versions)]
        prompt = sample.tokens[:-sample.response_length]
        payload = {"input_ids": prompt, "return_logprob": True, "logprob_start_len": len(prompt) - 1,
                   "sampling_params": {"temperature": 1, "top_p": 1, "top_k": -1, "ignore_eos": True,
                                       "max_new_tokens": 1024, "min_new_tokens": 1024,
                                       "sampling_seed": 17 + index}}
        started = time.monotonic()
        with urlopen(Request(worker + "/generate", data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"})) as response:
            data = response.read()
        body = json.loads(data)
        tokens, logprobs = generated_tokens(body, len(prompt), 1024)
        sample.tokens = prompt + tokens
        sample.rollout_log_probs = logprobs
        sample.response = body["text"]
        sample.weight_versions = [version]
        (Path(args.capacity_directory) / f"generation-{rollout_id}-{index}.json").write_bytes(data)
        return {"index": index, "seconds": time.monotonic() - started,
                "prompt_tokens": len(prompt), "response_tokens": len(tokens),
                "cached_tokens": body["meta_info"].get("cached_tokens"), "engine": worker,
                "weight_version": version, "group": sample.group_index}

    with ThreadPoolExecutor(max_workers=args.capacity_concurrency) as pool:
        return list(pool.map(generate, range(len(samples))))


def event(args, kind, **values):
    with (Path(args.capacity_directory) / "events.jsonl").open("a") as stream:
        stream.write(json.dumps({"kind": kind, "monotonic": time.monotonic(), **values}) + "\n")


def generate_rollout(args, rollout_id, data_source, evaluation=False):
    from slime.rollout.forge_load import generate_rollout as load

    from behave.train.slime_data import engine_versions, require_weight_advance

    assert not evaluation
    event(args, "round_start", round=rollout_id)
    versions = engine_versions(f"http://{args.sglang_router_ip}:{args.sglang_router_port}",
                               args.rollout_num_gpus // args.rollout_num_gpus_per_engine)
    previous = data_source.metadata.get("capacity_versions")
    require_weight_advance(previous, versions)
    data_source.metadata["capacity_versions"] = versions
    output = load(args, rollout_id, data_source)
    if args.capacity_mode == "pipeline":
        measurements = generate_samples(args, rollout_id, output.samples, versions)
        event(args, "generation", round=rollout_id, requests=measurements)
        if versions != engine_versions(f"http://{args.sglang_router_ip}:{args.sglang_router_port}", len(versions)):
            raise ValueError("engines changed versions during the measured generation")
    count = args.rollout_batch_size * args.n_samples_per_prompt
    assert [len(s.tokens) for s in output.samples] == [args.seq_length] * count
    assert [s.response_length for s in output.samples] == [1024] * count
    assert [s.group_index for s in output.samples] == [i // args.n_samples_per_prompt for i in range(count)]
    event(args, "batch", round=rollout_id, versions=versions, total_lengths=[len(s.tokens) for s in output.samples],
          response_lengths=[s.response_length for s in output.samples])
    return output


def extra_arguments(parser):
    parser.add_argument("--capacity-directory", required=True)
    parser.add_argument("--capacity-native-entrypoint", required=True)
    parser.add_argument("--capacity-host-netns", type=int, required=True)
    parser.add_argument("--capacity-cpus", type=int, required=True)
    parser.add_argument("--capacity-mode", choices=("capacity", "pipeline"), default="capacity")
    parser.add_argument("--capacity-concurrency", type=int, default=2)
    return parser


def sample_records(args, sample_class):
    """Same prompt within each group; distinct seeds and rewards within the group.

    Preserve the original two-sample probe's distinct random prompts so its
    before/after throughput comparison does not acquire a cache-hit advantage.
    These are token-load fixtures, not eight semantic BEHAVE tasks.
    """
    count = args.rollout_batch_size * args.n_samples_per_prompt
    if args.global_batch_size != count or not 1 <= args.capacity_concurrency <= count:
        raise ValueError("capacity batch must retain every sample with bounded concurrency")
    if args.rollout_batch_size < 1 or args.n_samples_per_prompt < 2:
        raise ValueError("capacity probe requires nonempty groups with distinct rewards")
    samples = []
    for index in range(count):
        group, within = divmod(index, args.n_samples_per_prompt)
        rng = random.Random(17 + (index if count == 2 else group))
        tokens = [rng.randrange(1000, 17000) for _ in range(args.seq_length)]
        sample = sample_class(index=index, group_index=group, tokens=tokens, response_length=1024,
            reward=float(within % 2), loss_mask=[1] * 1024, status=sample_class.Status.COMPLETED,
            rollout_log_probs=shape_only_logprobs(args, 1024) if args.capacity_mode == "capacity" else None)
        samples.append(sample.to_dict())
    return samples


def main():
    os.environ.update(SLIME_HOST_IP="127.0.0.2", RAY_USAGE_STATS_ENABLED="0", CUDA_DEVICE_MAX_CONNECTIONS="1")
    import ray
    import torch
    from slime.utils.arguments import parse_args
    from slime.utils.types import Sample

    from behave.train.slime_checkpoint import iteration
    from behave.train.slime_driver import CONTROL_ADDRESS, private_loopback
    from behave.train.slime_hooks import configure_scheduler
    from behave.train.slime_launch import SlimeLayout

    args = parse_args(extra_arguments)
    layout = SlimeLayout(args.actor_num_nodes, args.actor_num_gpus_per_node, args.tensor_model_parallel_size,
                        args.pipeline_model_parallel_size, args.context_parallel_size, args.rollout_num_gpus,
                        args.rollout_num_gpus_per_engine, args.colocate, args.num_gpus_per_node)
    layout.validate_model(json.loads((Path(args.hf_checkpoint) / "config.json").read_text()))
    assert layout.required_gpus == len(os.environ["CUDA_VISIBLE_DEVICES"].split(",")) == 8
    assert args.capacity_cpus > 8 and args.capacity_cpus <= len(os.sched_getaffinity(0))
    assert args.seq_length in (65536, 131072) and args.num_rollout >= 2
    assert not args.debug_train_only and not args.debug_rollout_only and not args.release_train
    assert args.eval_interval is None
    if args.save is not None:
        assert args.capacity_mode == "pipeline" and args.save_interval > 0 and args.save_hf
        assert args.use_checkpoint_opt_param_scheduler
        assert not args.no_load_optim and not args.no_load_rng
        configure_scheduler(args)
    else:
        assert args.save_interval is None
    assert args.optimizer == "adam" and args.optimizer_cpu_offload
    assert args.offload_train == args.offload_rollout == args.colocate
    assert args.use_rollout_logprobs == (args.capacity_mode == "pipeline")
    # Distinct synthetic rewards exercise a nonzero gradient. They are
    # deliberately not BEHAVE scores and never enter a training-report ledger.
    samples = sample_records(args, Sample)
    torch.save({"samples": samples}, args.load_forge_rollout_data)
    event(args, "start", scope=args.capacity_mode, synthetic_rewards=True, score_acceptance=False,
          logprobs="shape-only" if args.capacity_mode == "capacity" else "native-generation",
          layout=asdict(layout), torch=torch.__version__, cpu_affinity=sorted(os.sched_getaffinity(0)),
          ray_cpus=min(args.capacity_cpus, 32), pid=os.getpid(),
          pid_namespace=os.stat("/proc/self/ns/pid").st_ino, load=args.load,
          loaded_iteration=iteration(Path(args.load))
              if (Path(args.load) / "latest_checkpointed_iteration.txt").exists() else -1)
    private_loopback(args.capacity_host_netns)
    if ray.is_initialized():
        raise RuntimeError("capacity fixture cannot reuse an ambient Ray cluster")
    # CPU allocation/NUMA reachability and Ray's logical worker slots are
    # separate. This two-engine fixture needs headroom, not 188 idle workers.
    ray.init(address="local", include_dashboard=False, num_cpus=min(args.capacity_cpus, 32), num_gpus=8,
             _node_ip_address=CONTROL_ADDRESS, _temp_dir=os.environ["RAY_TMPDIR"], log_to_driver=True)
    try:
        runpy.run_path(args.capacity_native_entrypoint, run_name="capacity_native_slime")["train"](args)
        event(args, "complete", pid=os.getpid(),
              saved_iteration=iteration(Path(args.save)) if args.save is not None else None)
    finally:
        ray.shutdown()


if __name__ == "__main__":
    main()
