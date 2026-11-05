"""Shared launch inputs for Task RL and Raw continued learning.

This module prepares the existing native driver. It neither starts a process
nor implements an optimizer. Budgets and model architecture are configuration.
"""

import hashlib
import inspect
import json
import math
import os
import re
import shlex
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import wraps
from itertools import pairwise
from pathlib import Path
from tempfile import NamedTemporaryFile

from behave.agent.journal import load_chain
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, fields, version
from behave.core.storage import install_exclusive

from .slime_checkpoint import iteration
from .trainer import NativeAnchor


@dataclass(frozen=True)
class ContextSlices:
    gather: tuple
    scatter: tuple


def context_slices(packed, cp_size):
    plan = getattr(packed, "_behave_context_slices", None)
    if plan is None:
        boundaries = packed.cu_seqlens_q.tolist()
        gather, scatter = [], [[] for _ in range(cp_size)]
        for begin, end in pairwise(boundaries):
            local_begin, local_end = begin // cp_size, end // cp_size
            chunk = (end - begin) // (2 * cp_size)
            gather.extend((rank, slice(local_begin, local_begin + chunk))
                          for rank in range(cp_size))
            gather.extend((rank, slice(local_begin + chunk, local_end))
                          for rank in reversed(range(cp_size)))
            for rank in range(cp_size):
                front = begin + rank * chunk
                back = begin + (2 * cp_size - rank - 1) * chunk
                scatter[rank].extend((slice(front, front + chunk), slice(back, back + chunk)))
        plan = ContextSlices(tuple(gather), tuple(tuple(parts) for parts in scatter))
        packed._behave_context_slices = plan
    return plan


_REVISIONS = {
    "hf_to_megatron/__init__.py": "e6e7f98e92900d8a37ca95fae8327206b3b456e1a477d6d60653d4f772b4b5e5",
    "hf_to_megatron/common.py": "ecf8bf75640e0d5f2873c7a7af4e38b469a47a1284eb683c49071fa8e459503e",
    "update_weight/common.py": "bd32e39fb18fb0f78710d5e2edbe498a12050b4b7a90c5adf870c4f02ee42d39",
}


class _BackboneView:
    """Read-only parameter enumeration; original tensors and modules stay put."""

    def __init__(self, wrapped, unwrapped):
        from slime.backends.megatron_utils.model_provider import LinearForLastLayer

        self.config = wrapped.config
        self._wrapped = wrapped
        head = getattr(unwrapped, "output_layer", None)
        self._excluded = set()
        if head is not None:
            if type(head) is not LinearForLastLayer or head.out_features != 1:
                raise ValueError("critic HF initialization requires the native scalar value head")
            self._excluded = {id(parameter) for parameter in head.parameters()}

    def named_parameters(self):
        return ((name, parameter) for name, parameter in self._wrapped.named_parameters()
                if id(parameter) not in self._excluded)

    def named_buffers(self):
        return self._wrapped.named_buffers()


def install_critic_hf_loader() -> None:
    """Install once inside an opted-in native model worker, never on disk."""
    from megatron.training.utils import unwrap_model
    from slime.backends.megatron_utils import hf_to_megatron

    original = hf_to_megatron.load_hf_weights
    if getattr(original, "_behave_value_head_bridge", False):
        return
    root = Path(inspect.getfile(hf_to_megatron)).parent.parent
    for name, expected in _REVISIONS.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("critic HF bridge requires its audited Slime loader revision")

    @wraps(original)
    def load(args, model, path):
        if model and getattr(model[0], "role", None) == "critic":
            model = tuple(_BackboneView(wrapped, bare)
                          for wrapped, bare in zip(model, unwrap_model(model), strict=True))
        return original(args, model, path)

    load._behave_value_head_bridge = True
    hf_to_megatron.load_hf_weights = load


@dataclass(frozen=True, slots=True)
class SlimeLayout:
    actor_nodes: int = 1
    actor_gpus_per_node: int = 4
    tensor_parallel: int = 4
    pipeline_parallel: int = 1
    context_parallel: int = 1
    rollout_gpus: int = 4
    rollout_gpus_per_engine: int = 2
    colocate: bool = True
    gpus_per_node: int | None = None
    rollout_excluded_gpus: tuple[int, ...] = ()

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "rollout_excluded_gpus":
                if not isinstance(value, (list, tuple)) or any(type(i) is not int for i in value):
                    raise ValueError("rollout_excluded_gpus must be GPU slot indices")
                object.__setattr__(self, name, tuple(value))
                continue
            if name == "gpus_per_node" and value is None:
                continue
            if name == "colocate":
                if type(value) is not bool:
                    raise TypeError("colocate must be boolean")
            elif type(value) is not int or value < (0 if name == "rollout_gpus" else 1):
                raise ValueError(f"invalid GPU layout: {name}")
        if self.actor_gpus % (self.tensor_parallel * self.pipeline_parallel * self.context_parallel):
            raise ValueError("actor GPUs must be divisible by TP * PP * CP")
        if self.rollout_gpus % self.rollout_gpus_per_engine:
            raise ValueError("rollout GPUs must be divisible by engine TP")
        if self.node_gpus < self.actor_gpus_per_node or (self.rollout_gpus and self.node_gpus % self.rollout_gpus_per_engine):
            raise ValueError("node GPU capacity must fit actor ranks and whole rollout engines")
        if self.actor_nodes == 1 and self.required_gpus > self.node_gpus:
            raise ValueError("single-node layout exceeds its physical GPU capacity")
        excluded = self.rollout_excluded_gpus
        if excluded:
            if (self.actor_nodes != 1 or not self.colocate or self.rollout_gpus_per_engine != 1
                    or self.rollout_gpus != self.node_gpus):
                raise ValueError("rollout GPU exclusions require full-node colocated TP1 serving")
            if (len(set(excluded)) != len(excluded) or len(excluded) >= self.rollout_gpus
                    or any(i < 0 or i >= self.rollout_gpus for i in excluded)):
                raise ValueError("invalid rollout GPU exclusions")
            if self.actor_gpus < self.node_gpus and any(i < self.actor_gpus for i in excluded):
                raise ValueError("reduced actor placement includes an excluded unhealthy GPU")

    @property
    def rollout_engine_count(self) -> int:
        return (self.rollout_gpus - len(self.rollout_excluded_gpus)) // self.rollout_gpus_per_engine

    def deployment(self) -> dict | None:
        """Use upstream placeholder slots; actors own the first actor_gpus slots.

        Slots follow upstream's sorted visible GPU IDs. Full-node placement is
        required, and the launched physical IDs must be checked operationally.
        """
        if not self.rollout_excluded_gpus:
            return None
        groups = []
        for slot in range(self.rollout_gpus):
            kind = "placeholder" if slot in self.rollout_excluded_gpus else "regular"
            if groups and groups[-1]["worker_type"] == kind:
                groups[-1]["num_gpus"] += 1
            else:
                groups.append({"worker_type": kind, "num_gpus": 1, "num_gpus_per_engine": 1})
        return {"sglang": [{"name": "actor", "update_weights": True, "server_groups": groups}]}

    @property
    def actor_gpus(self) -> int:
        return self.actor_nodes * self.actor_gpus_per_node

    @property
    def required_gpus(self) -> int:
        return (max(self.actor_gpus, self.rollout_gpus) if self.colocate
                else self.actor_gpus + self.rollout_gpus)

    @property
    def node_gpus(self) -> int:
        if self.gpus_per_node is not None:
            return self.gpus_per_node
        if self.actor_nodes != 1 and not self.colocate:
            raise ValueError("separate multi-node placement requires explicit gpus_per_node")
        return self.required_gpus if self.actor_nodes == 1 else self.actor_gpus_per_node

    def validate_model(self, config: dict) -> None:
        config = config.get("text_config", config)
        # This dense recipe does not silently promise TP replication for KV
        # heads or introduce an expert-parallel layout for MoE models.
        if config.get("num_experts", 0):
            raise ValueError("MoE requires an explicit expert-parallel recipe")
        for name in ("hidden_size", "num_attention_heads", "num_key_value_heads", "intermediate_size"):
            value = config[name]
            if type(value) is not int or value <= 0 or value % self.tensor_parallel:
                raise ValueError(f"{name} must divide evenly across actor TP")
        if config["num_hidden_layers"] % self.pipeline_parallel:
            raise ValueError("uneven pipeline layers require an explicit pipeline layout")

    def arguments(self) -> tuple[str, ...]:
        return (
            "--actor-num-nodes", str(self.actor_nodes),
            "--actor-num-gpus-per-node", str(self.actor_gpus_per_node),
            "--num-gpus-per-node", str(self.node_gpus),
            "--tensor-model-parallel-size", str(self.tensor_parallel),
            "--pipeline-model-parallel-size", str(self.pipeline_parallel),
            "--context-parallel-size", str(self.context_parallel),
            "--expert-model-parallel-size", "1", "--expert-tensor-parallel-size", "1",
            "--rollout-num-gpus", str(self.rollout_gpus),
            "--rollout-num-gpus-per-engine", str(self.rollout_gpus_per_engine),
            *(("--sequence-parallel",) if self.tensor_parallel > 1 else ()),
            *(("--colocate",) if self.colocate else ()),
        )


def model_template(path: Path, *, spec: tuple[str, str] | None = None) -> tuple[str, ...]:
    """Read upstream's literal MODEL_ARGS array, never execute its launch script.

    Slime's model templates are data. Templates requiring shell expansion or
    execution are not silently evaluated on a shared cluster.
    """
    tokens = shlex.split(path.read_text(), comments=True)
    if not tokens or tokens[0] != "MODEL_ARGS=(" or tokens[-1] != ")":
        raise ValueError("expected a literal Slime MODEL_ARGS array")
    values = tuple(tokens[1:-1])
    if not values or any(any(c in value for c in "$`;\n\r") for value in values):
        raise ValueError("Slime model template must not execute shell expansions")
    if spec is not None:
        if (type(spec) is not tuple or len(spec) != 2 or any(type(s) is not str or not s
                or not all(p.isidentifier() for p in s.split(".")) for s in spec)):
            raise ValueError("model spec override requires a module and function name")
        if values.count("--spec") != 1:
            raise ValueError("model template must contain one spec to override")
        i = values.index("--spec")
        if len(values) < i + 3 or any(s.startswith("--") for s in values[i+1:i+3]):
            raise ValueError("invalid upstream model spec")
        values = (*values[:i+1], *spec, *values[i+3:])
    return values


def import_parent(reader, head, destination, *, route, _visited=None):
    """Transfer the sealed parent receipt, not its host path or weight files."""
    visited=set() if _visited is None else _visited
    if head in visited:raise ValueError('checkpoint ancestry repeats')
    visited.add(head)
    parent = NativeAnchor.load(reader, head)
    if parent.route != route:
        raise ValueError("parent checkpoint belongs to the other learning route")
    events = load_chain(reader, head)
    closure = tuple(dict.fromkeys(ref for event in events
        for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
    destination.publish(reader, closure)
    if route == 'task':
        from behave.core.records import parse_json, thaw_json
        ref=BlobRef.from_dict(thaw_json(events[0].payload['binding']))
        if (reader.directory/ref.sha256).is_file():
            binding=parse_json(reader.read(ref))
            destination.publish(reader,(ref,))
            if binding.get('parent_checkpoint') is not None:
                ancestor=import_parent(reader,BlobRef.from_dict(binding['parent_checkpoint']),
                    destination,route=route,_visited=visited)
                first=min(e.payload['round'] for e in events if e.kind=='round_scored')
                if ancestor.step+1!=first:raise ValueError('checkpoint binding skips its predecessor')
    return parent


def common_arguments(configuration, output: Path, *, updates: int, load: Path,
                     cpus: int, checkpoint_directory: Path, context: int):
    """Flags shared by both learners; each caller adds its own data/loss hooks."""
    if type(updates) is not int or updates < 1:
        raise ValueError("an explicit final native update index is required")
    from .checkpoint_storage import hf_save_pattern

    upstream = Path(configuration["slime_source"])
    template = upstream / configuration["model_template"]
    spec = configuration.get("model_spec")
    binding_path = output.resolve() / "binding.json"
    layout = SlimeLayout(**configuration["layout"])
    deployment = layout.deployment()
    deployment_args = ()
    if deployment is not None:
        if any(v == "--sglang-config" or v.startswith("--sglang-config=")
               for v in configuration["native_arguments"]):
            raise ValueError("rollout GPU exclusions own the upstream sglang-config")
        name = "rollout_deployment.json"
        payload = canonical_json(deployment)  # JSON is also valid YAML.
        if not install_exclusive(output, name, payload) and (output / name).read_bytes() != payload:
            raise ValueError("rollout deployment changed in an existing plan")
        deployment_args = ("--sglang-config", str(output.resolve() / name))
    return (*model_template(template, spec=None if spec is None else tuple(spec)),
        *layout.arguments(), *deployment_args,
        "--hf-checkpoint", configuration["model_path"], "--load", str(load),
        "--save", str(checkpoint_directory), "--save-interval", str(configuration["checkpoint_interval"]),
        "--save-hf", hf_save_pattern(output),
        *(("--behave-hf-checkpoint-interval", str(configuration["hf_checkpoint_interval"]))
          if "hf_checkpoint_interval" in configuration else ()),
        *(("--behave-hf-checkpoint-updates", *(str(step) for step in configuration["hf_checkpoint_updates"]))
          if "hf_checkpoint_updates" in configuration else ()),
        "--num-rollout", str(updates),
        "--seq-length", str(context), "--max-position-embeddings", str(context),
        "--behave-rollout-binding", str(binding_path),
        "--behave-rollout-binding-sha256", BlobRef.of(binding_path.read_bytes()).sha256,
        "--behave-native-entrypoint", str(upstream / "train.py"),
        "--behave-native-entrypoint-sha256", BlobRef.of((upstream / "train.py").read_bytes()).sha256,
        "--behave-host-netns", str(os.stat("/proc/self/ns/net").st_ino),
        "--behave-cpus", str(cpus), "--behave-ray-cpus", str(configuration["ray_cpus"]))


def native_arguments(output: Path, *, updates: int, load: Path, cpus: int,
                     checkpoint_directory: Path) -> tuple[str, ...]:
    """Assemble either prepared learning route for the same native application."""
    configuration = json.loads((output / "configuration.json").read_text())
    selection = json.loads((output / "training_selection.json").read_text())
    record = json.loads((output / "binding.json").read_text())
    if record["schema"] == "behave.slime-raw-binding/1":
        from .slime_data import arguments, read_raw_binding, sampling

        binding = read_raw_binding(output / "binding.json")
        count = sampling(binding).tasks_per_update
        context = selection["encoding"]["context_tokens"]
        route = (*arguments(), "--rollout-batch-size", str(count), "--global-batch-size", str(count))
    else:
        from .slime_rollout import read_binding

        binding = read_binding(output / "binding.json")
        context = configuration["runtime"]["context_tokens"]
        route = (*preset(configuration["algorithm"]).arguments(serving="recorded_rounds" not in binding),
            "--rollout-function-path", "behave.train.slime_data.generate_rollout",
            "--data-source-path", "behave.train.slime_data.ScoredDataSource",
            "--rollout-batch-size", str(selection.get("tasks_per_update", len(selection["tasks"]))),
            "--n-samples-per-prompt", str(selection["samples_per_task"]),
            "--global-batch-size", str(selection["candidates_per_update"]),
            *(("--debug-train-only",) if "recorded_rounds" in binding else (
                "--sglang-context-length", str(configuration.get("serving_context_tokens", context)),
                "--sglang-served-model-name", binding["endpoint"]["model"])))
    return (*common_arguments(configuration, output, updates=updates, load=load, cpus=cpus,
                              checkpoint_directory=checkpoint_directory, context=context),
            *route, *configuration["native_arguments"])


def logged_arguments(python: str, module: str, arguments: tuple[str, ...], stdout: Path,
                     stderr: Path) -> tuple[str, ...]:
    """Redirect native output once inside the existing PID lifetime supervisor."""
    bootstrap = ("import os,sys; "
        "out=os.open(sys.argv.pop(1),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); "
        "err=os.open(sys.argv.pop(1),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); "
        "os.dup2(out,1); os.dup2(err,2); os.close(out); os.close(err); "
        "module=sys.argv.pop(1); "
        "os.execv(sys.executable,[sys.executable,'-B','-u','-m',module,*sys.argv[1:]])")
    return (python, "-B", "-u", "-c", bootstrap, str(stdout), str(stderr), module, *arguments)


ALGORITHMS = ("grpo", "gspo", "cispo", "reinforce_plus_plus", "reinforce_plus_plus_baseline", "ppo")
GROUPED = frozenset(("grpo", "gspo", "cispo", "reinforce_plus_plus_baseline"))
REWARD_HOOK = "behave.train.slime_rollout.post_process_rewards"


def require_sampling_profile(parameters: Mapping) -> None:
    """Do not inherit a model's generation_config in an on-policy recipe.

    The trainer evaluates untruncated, temperature-one probabilities. An omitted
    top_k can silently select the model's top-20 default at the serving boundary.
    """
    for name, value in (("temperature", 1), ("top_p", 1), ("top_k", -1)):
        if name not in parameters or type(parameters[name]) not in (int, float) or parameters[name] != value:
            raise ValueError(f"Slime sampling requires explicit {name}={value}")


@dataclass(frozen=True, slots=True)
class SlimeRecipe:
    algorithm: str = "grpo"
    clip_low: float = 0.2
    clip_high: float = 0.2
    group_std: bool = True
    kl_loss: float = 0.0
    entropy: float = 0.0

    def __post_init__(self):
        if self.algorithm not in ALGORITHMS:
            raise ValueError("unknown Slime algorithm")
        if type(self.group_std) is not bool:
            raise TypeError("group_std must be boolean")
        for name in ("clip_low", "clip_high", "kl_loss", "entropy"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")
        if self.clip_low > 1 or self.clip_high == 0:
            raise ValueError("invalid clipping interval")

    def as_dict(self) -> dict:
        return {"schema": "behave.slime-recipe/1", **asdict(self)}

    @property
    def normalize_advantages(self) -> bool:
        # Upstream REINFORCE++ requires masked global advantage whitening.
        # This is distinct from GRPO's candidate-group reward normalization.
        return self.algorithm in ("reinforce_plus_plus", "reinforce_plus_plus_baseline")

    @classmethod
    def from_dict(cls, value: object):
        row = fields(value, {"schema", "algorithm", "clip_low", "clip_high", "group_std", "kl_loss", "entropy"})
        version(row["schema"], "behave.slime-recipe/1")
        return cls(**{k: v for k, v in row.items() if k != "schema"})

    def arguments(self, *, serving: bool = True) -> tuple[str, ...]:
        """Append to upstream model/optimizer flags, not its destructive shell scripts.

        gamma=lambda=1 and zero reward-shaped KL give R-V returns for PPO
        on every retained turn. Discounted cross-turn GAE needs a different
        trajectory adapter. Loss-side KL remains optional and explicit.
        """
        return (
            "--advantage-estimator", self.algorithm, "--loss-type", "policy_loss",
            "--custom-reward-post-process-path", REWARD_HOOK,
            "--disable-rewards-normalization", "--disable-grpo-std-normalization",
            "--use-rollout-logprobs", "--gamma", "1", "--lambd", "1", "--kl-coef", "0",
            *(("--normalize-advantages",) if self.normalize_advantages else ()),
            "--rollout-temperature", "1", "--rollout-top-p", "1", "--rollout-top-k", "-1",
            "--eps-clip", str(self.clip_low), "--eps-clip-high", str(self.clip_high),
            "--entropy-coef", str(self.entropy), "--kl-loss-type", "low_var_kl",
            "--kl-loss-coef", str(self.kl_loss),
            *(("--use-kl-loss",) if self.kl_loss else ()),
            *(("--router-policy", "consistent_hashing", "--router-disable-retries") if serving else ()),
        )

    def validate_args(self, args) -> None:
        """Reject semantic overrides before flattened Samples enter the trainer."""
        expected = {
            "advantage_estimator": self.algorithm, "loss_type": "policy_loss",
            "custom_reward_post_process_path": REWARD_HOOK,
            "rewards_normalization": False, "grpo_std_normalization": False,
            "use_rollout_logprobs": True, "gamma": 1.0, "lambd": 1.0, "kl_coef": 0.0,
            "rollout_temperature": 1.0, "rollout_top_p": 1.0, "rollout_top_k": -1,
            "eps_clip": self.clip_low, "eps_clip_high": self.clip_high,
            "kl_loss_coef": self.kl_loss, "use_kl_loss": bool(self.kl_loss),
            "entropy_coef": self.entropy, "kl_loss_type": "low_var_kl",
            "calculate_per_token_loss": False, "normalize_advantages": self.normalize_advantages,
            "custom_advantage_function_path": None,
            "compute_advantages_and_returns": True, "eps_clip_c": None,
            "use_opsm": False, "use_tis": False, "use_opd": False,
            "get_mismatch_metrics": False, "custom_pg_loss_reducer_function_path": None,
        }
        for name, value in expected.items():
            if getattr(args, name, object()) != value:
                raise ValueError(f"Slime recipe override is not supported: {name}")


def preset(name: str) -> SlimeRecipe:
    """Convenience starting points, not approved experiment hyperparameters."""
    if name == "grpo_no_std":
        return SlimeRecipe(group_std=False)
    if name == "gspo":
        return SlimeRecipe("gspo", clip_low=0.0003, clip_high=0.0004)
    if name == "cispo":
        return SlimeRecipe("cispo", clip_low=1.0, clip_high=4.0)
    return SlimeRecipe(name)


_DRIVER_LIBRARY_DIRS = tuple(map(Path, (
    "/usr/local/nvidia/lib64", "/usr/local/nvidia/lib",
    "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64",
)))


def megatron_source(configuration: dict) -> Path:
    """Select the explicit checkout, preserving the legacy sibling layout."""
    if "megatron_source" in configuration:
        path = configuration["megatron_source"]
        if (not isinstance(path, str) or not path or any(ord(char) < 32 for char in path)
                or not Path(path).is_absolute() or ".." in Path(path).parts
                or Path(path) == Path(Path(path).anchor)):
            raise ValueError("megatron_source must be an explicit absolute source path")
        return Path(path)
    return Path(configuration["slime_source"]).parent / "megatron-upstream"


def tokenize_environment(environment: dict[str, str], workers: int | None) -> dict[str, str]:
    """Apply the same explicit serving option to ordinary and improvement RL."""
    if workers is None:
        return environment
    if type(workers) is not int or workers < 1:
        raise ValueError("tokenize_workers must be a positive integer or None")
    plugins = [name for name in environment.get("SGLANG_PLUGINS", "").split(",") if name]
    if "behave_tokenize_workers" not in plugins:
        plugins.append("behave_tokenize_workers")
    return {**environment, "SGLANG_PLUGINS": ",".join(plugins), "BEHAVE_TOKENIZE_WORKERS": str(workers)}


def environment(prefix: Path, scratch: Path, *, cuda_visible_devices: str,
                megatron_source: Path | None = None,
                cpu_threads: int = defaults.TRAIN_CPU_THREADS,
                build_jobs: int = defaults.TRAIN_BUILD_JOBS,
                kernel_cache: Path | None = None) -> dict[str, str]:
    prefix, scratch = prefix.resolve(), scratch.resolve()
    if not (prefix / "bin/python").is_file():
        raise ValueError("explicit native Python prefix required")
    if any(type(value) is not int or value < 1 for value in (cpu_threads, build_jobs)):
        raise ValueError("CPU threads and build jobs must be positive integers")
    if not cuda_visible_devices or re.fullmatch(r"[A-Za-z0-9_.,/-]+", cuda_visible_devices) is None:
        raise ValueError("explicit allocated CUDA devices required")
    # Conda can leave empty directories or version aliases (e.g. python3.1
    # pointing to python3.12). Count installations, not directory spellings.
    sites = sorted({p.resolve() for p in prefix.glob("lib/python*/site-packages")
                    if (p / "torch/lib").is_dir()})
    if len(sites) != 1:
        raise ValueError("native runtime must contain exactly one Torch installation")
    libraries = [sites[0] / "torch/lib", *sorted((sites[0] / "nvidia").glob("*/lib")), prefix / "lib"]
    # Container runtimes mount the driver separately from the CUDA toolkit.
    # ld.so's cache can find libcuda.so.1 while the C++ linker's -lcuda still
    # fails. Supply actual driver directories to both loaders; never select
    # toolkit stubs or inherit an arbitrary login-shell LIBRARY_PATH.
    drivers = [path for path in _DRIVER_LIBRARY_DIRS if (path / "libcuda.so").is_file()]
    libraries.extend(drivers)
    # TE's system-loader searches CUDA_HOME recursively before consulting the
    # Python wheels. A conda toolkit root includes all site-packages, which is
    # particularly expensive over NFS in every spawned scheduler. Its supported
    # per-library hints select the same wheel directories as LD_LIBRARY_PATH;
    # retain CUDA_HOME for nvcc/headers and leave non-wheel installs unchanged.
    cuda_libraries = {}
    for library, package in (("cudnn", "cudnn"), ("nvrtc", "cuda_nvrtc"), ("curand", "curand")):
        directory = sites[0] / "nvidia" / package / "lib"
        if any(path.is_file() for path in directory.glob(f"lib{library}.so*")):
            cuda_libraries[f"{library.upper()}_HOME"] = str(directory)
    caches = {name: scratch / name for name in (
        "tmp", "triton", "extensions", "inductor", "ray", "xdg", "flashinfer", "cuda", "hf", "torch", "numba", "tvm-ffi",
    )}
    # Operators may reuse compilation artifacts for the same native runtime
    # and device architecture. Process sockets and temporary files remain
    # private to this invocation; never reuse Ray's working directory.
    if kernel_cache is not None:
        kernel_cache = Path(kernel_cache).resolve()
        for name in ("triton", "extensions", "inductor", "cuda", "numba", "tvm-ffi", "flashinfer"):
            caches[name] = kernel_cache / name
    for path in caches.values():
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    source = {}
    if megatron_source is not None:
        # The wheel installs megatron.core only. Slime's application also uses
        # megatron.training from the operator-selected, pinned source checkout.
        megatron_source = megatron_source.resolve()
        if not (megatron_source / "megatron/training/arguments.py").is_file():
            raise ValueError("complete Megatron training source required")
        source["PYTHONPATH"] = str(megatron_source)
    return {
        **source,
        **cuda_libraries,
        "PATH": f"{prefix}/bin:/usr/bin:/bin", "CUDA_HOME": str(prefix), "CUDA_PATH": str(prefix),
        "LD_LIBRARY_PATH": ":".join(str(p) for p in libraries if p.is_dir()),
        **({"LIBRARY_PATH": ":".join(map(str, drivers))} if drivers else {}),
        "CUDA_VISIBLE_DEVICES": cuda_visible_devices, "TMPDIR": str(caches["tmp"]),
        "BEHAVE_SCRATCH_DIR": str(caches["tmp"]),
        "TRITON_CACHE_DIR": str(caches["triton"]), "TORCH_EXTENSIONS_DIR": str(caches["extensions"]),
        "TORCHINDUCTOR_CACHE_DIR": str(caches["inductor"]), "RAY_TMPDIR": str(caches["ray"]),
        "XDG_CACHE_HOME": str(caches["xdg"]), "OMP_NUM_THREADS": str(cpu_threads), "MKL_NUM_THREADS": str(cpu_threads),
        "FLASHINFER_WORKSPACE_BASE": str(caches["flashinfer"]), "CUDA_CACHE_PATH": str(caches["cuda"]),
        "HF_HOME": str(caches["hf"]), "TORCH_HOME": str(caches["torch"]), "NUMBA_CACHE_DIR": str(caches["numba"]),
        "TVM_FFI_CACHE_DIR": str(caches["tvm-ffi"]),
        "MAX_JOBS": str(build_jobs), "TOKENIZERS_PARALLELISM": "false", "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1", "SGLANG_NO_USAGE_STATS": "1",
        "SGLANG_PLUGINS": "behave_qwen_tools",
    }


def require_checkpoint_options(args) -> None:
    for name in ("no_save_optim", "no_save_rng"):
        if getattr(args, name, False):
            raise ValueError(f"complete Slime checkpoints require {name}=False")
    root = Path(args.load)
    if (root / "latest_checkpointed_iteration.txt").exists():
        if (getattr(args, "use_checkpoint_opt_param_scheduler", False) is not True
                or getattr(args, "override_opt_param_scheduler", False)):
            raise ValueError("Slime cold continuation requires --use-checkpoint-opt-param-scheduler without override")
        for name in ("no_load_optim", "no_load_rng", "finetune"):
            if getattr(args, name, False):
                raise ValueError(f"Slime cold continuation requires {name}=False")
        if getattr(args, "start_rollout_id", None) not in (None, iteration(root) + 1):
            raise ValueError("Slime cold continuation cannot override the next round")
    elif getattr(args, "start_rollout_id", None) not in (None, 0):
        raise ValueError("Slime initial weights must start at round zero")


def role_configuration(args) -> dict | None:
    """Build only ownership overrides, not another arbitrary CLI/YAML parser."""
    if getattr(args, "megatron_config_path", None) is not None:
        raise ValueError("this scored application owns its native role configuration")
    require_checkpoint_options(args)
    if not args.use_critic:
        return None
    if args.num_critic_only_steps != 0:
        raise ValueError("scored PPO rounds require both actor and critic updates")
    root = Path(args.load)
    cold = (root / "latest_checkpointed_iteration.txt").exists()
    critic_load = root / "critic" if cold else root
    if cold and iteration(critic_load) != iteration(root):
        raise ValueError("actor and critic checkpoint rounds disagree")
    return {"megatron": [
        {"name": "behave-actor", "role": "actor", "overrides": {}},
        {"name": "behave-critic", "role": "critic", "overrides": {
            "load": str(critic_load), "save": str(Path(args.save) / "critic"),
        }},
    ]}


def write_role_configuration(args, directory: Path) -> None:
    configuration = role_configuration(args)
    if configuration is not None:
        # JSON is a YAML subset accepted by Slime's own safe_load parser.
        # Unique, exclusively created files stay within the lifetime-owned cache.
        with NamedTemporaryFile(prefix="behave-roles-", suffix=".json", dir=directory, delete=False) as stream:
            stream.write(canonical_json(configuration))
            args.megatron_config_path = stream.name
