"""Owned single-node Slime application; native Slime owns the training loop.

Run only inside the existing PID/network lifetime supervisor. This entry point
never attaches to an ambient Ray cluster or invokes upstream shell launchers.
It uses the approved model flags and native checkpoint/weight-transfer code.
"""

import os
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.core.resources import allocated_cpus

from .slime_checkpoint import checkpoint_due
from .slime_launch import SlimeLayout, SlimeRecipe
from .slime_rollout import read_binding

# Ray normalizes exactly 127.0.0.1/localhost to the host's advertised address.
# A distinct numeric address in Linux's loopback /8 stays inside this job's
# network namespace without modifying Ray's resolver or opening host ports.
CONTROL_ADDRESS = "127.0.0.2"


def extra_arguments(parser):
    from .slime_hooks import DEFAULT_STARTUP_SECONDS

    parser.add_argument("--behave-actor-startup-seconds", type=float, default=DEFAULT_STARTUP_SECONDS,
                        help="Actor construction deadline, separate from distributed communication timeouts")
    parser.add_argument("--behave-learning-mode", choices=("scored", "raw"), default="scored")
    parser.add_argument("--behave-rollout-binding", required=True)
    parser.add_argument("--behave-rollout-binding-sha256", required=True)
    parser.add_argument("--behave-native-entrypoint", required=True)
    parser.add_argument("--behave-native-entrypoint-sha256", required=True)
    parser.add_argument("--behave-host-netns", type=int, required=True)
    parser.add_argument("--behave-cpus", type=int, required=True)
    parser.add_argument("--behave-ray-cpus", type=int, default=None,
                        help="Ray logical CPU slots; defaults to the allocated CPU count, without changing affinity")
    parser.add_argument("--behave-hf-checkpoint-interval", type=int, default=None,
                        help="HF milestone cadence; native recovery saves use --save-interval")
    parser.add_argument("--behave-hf-checkpoint-updates", type=int, nargs="+", default=None,
                        help="Explicit completed-update HF milestones, overriding the periodic cadence")
    return parser


def ray_cpu_count(args, required_gpus: int) -> int:
    """Keep Ray worker admission separate from Slurm/NUMA CPU visibility."""
    allocated = args.behave_cpus
    slots = getattr(args, "behave_ray_cpus", None)
    slots = allocated if slots is None else slots
    if type(allocated) is not int or not 1 <= allocated <= len(allocated_cpus()):
        raise ValueError("explicit allocated CPU count required")
    if type(slots) is not int or not required_gpus < slots <= allocated:
        raise ValueError("Ray CPU slots must exceed GPU placement bundles and fit the CPU allocation")
    return slots


def validate(args):
    raw = getattr(args, "behave_learning_mode", "scored") == "raw"
    if raw:
        from .slime_data import read_raw_binding, validate_args

        binding = read_raw_binding(Path(args.behave_rollout_binding))
        validate_args(args)
    else:
        binding = read_binding(Path(args.behave_rollout_binding))
        SlimeRecipe.from_dict(binding["recipe"]).validate_args(args)
    if BlobRef.of(canonical_json(binding)).sha256 != args.behave_rollout_binding_sha256:
        raise ValueError("Slime binding changed after submission")
    # This pinned Slime path always enables Megatron's distributed optimizer;
    # its checkpoint implementation supports Adam, not SGD. Reject before Ray
    # launches engines or allocates the full actor model.
    if args.optimizer != "adam":
        raise ValueError("native Slime distributed checkpoints require the Adam optimizer")
    recorded = "recorded_rounds" in binding
    required = {"rollout_global_dataset": True,
        "async_save": False, "eval_interval": None, "use_fault_tolerance": False,
        "debug_train_only": raw or recorded, "debug_rollout_only": False, "load_debug_rollout_data": None,
        "rollout_external": False, "release_train": False, "model_name": None}
    if not raw:
        required.update(rollout_function_path="behave.train.slime_data.generate_rollout",
                        data_source_path="behave.train.slime_data.ScoredDataSource")
        if not recorded:
            required["router_disable_retries"] = True
    elif args.rollout_num_gpus != 0:
        raise ValueError("Raw continued learning must not allocate inference GPUs")
    if recorded and args.rollout_num_gpus != 0:
        raise ValueError("recorded-round training must not allocate inference GPUs")
    for name, expected in required.items():
        if getattr(args, name, object()) != expected:
            raise ValueError(f"unsupported synchronous Slime application override: {name}")
    if args.actor_num_nodes != 1 or not args.save or not args.load or type(args.num_rollout) is not int or args.num_rollout < 1:
        raise ValueError("explicit finite single-node training and native load/save paths required")
    checkpoint_due(args, 0)  # Validate explicit cadence before allocating native models.
    layout = SlimeLayout(args.actor_num_nodes, args.actor_num_gpus_per_node,
        args.tensor_model_parallel_size, args.pipeline_model_parallel_size, args.context_parallel_size,
        args.rollout_num_gpus, args.rollout_num_gpus_per_engine, args.colocate, args.num_gpus_per_node)
    devices = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")
    if not all(devices) or len(set(devices)) != layout.required_gpus:
        raise ValueError("Slurm visible devices differ from the complete actor/rollout allocation")
    # Upstream reserves one CPU in each GPU placement bundle and schedules the
    # rollout manager outside those bundles. Counting only GPUs deadlocks it.
    ray_cpu_count(args, layout.required_gpus)
    return layout


def private_loopback(host_namespace: int):
    """Bring up only lo in the supervisor-created private network namespace."""
    import fcntl
    import socket
    import struct

    if os.stat("/proc/self/ns/net").st_ino == host_namespace:
        raise ValueError("Slime must run in a private network namespace, not the host network")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        current = fcntl.ioctl(control.fileno(), 0x8913, struct.pack("16sH", b"lo", 0))
        flags = struct.unpack_from("H", current, 16)[0]
        fcntl.ioctl(control.fileno(), 0x8914, struct.pack("16sH", b"lo", flags | 1))


def main():
    # These packages are imported only in the explicitly selected native ABI.
    import runpy

    # Megatron validates this during parse_args, before constructing any actor.
    os.environ.update(SLIME_HOST_IP=CONTROL_ADDRESS, RAY_USAGE_STATS_ENABLED="0", CUDA_DEVICE_MAX_CONNECTIONS="1")
    import ray
    from slime.utils.arguments import parse_args

    from behave.agent.providers.sglang_tools import register_qwen_tools

    # Register before the native CLI enumerates tool-parser choices. Spawned
    # HTTP/scheduler processes use SGLang's installed plugin entry point.
    register_qwen_tools()
    args = parse_args(extra_arguments)
    import json
    import resource

    print(json.dumps({"event": "native_process_limit",
                      "soft_hard": resource.getrlimit(resource.RLIMIT_NPROC)}), flush=True)
    layout = validate(args)
    from .slime_hooks import configure_memory, configure_scheduler
    from .slime_launch import role_configuration, write_role_configuration

    configure_memory(args)
    configure_scheduler(args)
    from .slime_hooks import configure_startup

    configure_startup()
    role_configuration(args)  # Fail before Ray/native checkpoint deserialization.
    entry = Path(args.behave_native_entrypoint)
    if not entry.is_absolute() or BlobRef.of(entry.read_bytes()).sha256 != args.behave_native_entrypoint_sha256:
        raise ValueError("native Slime entrypoint differs from its pinned source")
    # Authenticate a cold checkpoint before any native deserializer sees it.
    from .slime_checkpoint import iteration
    if args.behave_learning_mode == "raw":
        from .slime_data import RawDataSource as DataSource
    else:
        from .slime_data import ScoredDataSource as DataSource

    marker = Path(args.load) / "latest_checkpointed_iteration.txt"
    DataSource(args).load(iteration(Path(args.load)) if marker.exists() else -1)
    # Each native process owns a fresh private Ray directory. The actor and
    # critic retain separate native checkpoints across cold processes.
    write_role_configuration(args, Path(os.environ["RAY_TMPDIR"]))
    private_loopback(args.behave_host_netns)
    if ray.is_initialized():
        raise ValueError("refusing to reuse an ambient Ray cluster")
    # address='local' explicitly starts a new cluster. Its management listeners
    # have no route out of this job's network namespace; no dashboard/Jobs API.
    ray.init(address="local", include_dashboard=False, num_cpus=ray_cpu_count(args, layout.required_gpus),
             num_gpus=layout.required_gpus, _node_ip_address=CONTROL_ADDRESS,
             _temp_dir=os.environ["RAY_TMPDIR"], log_to_driver=True)
    try:
        runpy.run_path(str(entry), run_name="behave_pinned_slime")["train"](args)
    finally:
        ray.shutdown()


if __name__ == "__main__":
    main()
