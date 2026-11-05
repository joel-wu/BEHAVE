"""Node-local caches and authenticated, cached development headers.

Nothing is installed into a reused environment. This is trusted operator
provisioning, not an agent tool or a sandbox for candidate code.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from behave.core.file_input import read_regular_file
from behave.core.paths import scratch_root
from behave.core.records import fields, parse_json
from behave.isolation.service import run_service

from .lifecycle import quiescent_call
from .model_files import inspect_model_files
from .spec import ServingSpec


class InstallationMismatch(ValueError):
    code = "installation-version-mismatch"

    def __init__(self, expected: dict, observed: dict):
        self.expected, self.observed = dict(expected), dict(observed)
        super().__init__(self.code)


def environment(spec: ServingSpec, scratch: Path, allocation: Mapping[str, str], *, includes=()) -> dict[str, str]:
    scratch = scratch.resolve()
    root = scratch_root()
    if not scratch.is_relative_to(root) or scratch == root:
        raise ValueError("serving scratch must be a private directory below the configured scratch root")
    paths = {name: scratch / name for name in ("home", "tmp", "hf", "xdg", "triton", "torch")}
    for path in paths.values():
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Explicit inherited capabilities only. Provider keys, proxies, PYTHONPATH,
    # LD_PRELOAD and user package configuration never enter the model process.
    result = {"HOME": str(paths["home"]), "TMPDIR": str(paths["tmp"]),
              "HF_HOME": str(paths["hf"]), "XDG_CACHE_HOME": str(paths["xdg"]),
              "TRITON_CACHE_DIR": str(paths["triton"]), "TORCHINDUCTOR_CACHE_DIR": str(paths["torch"]),
              "PATH": f"{Path(spec.python).parent}:{spec.cuda_root}/bin:/usr/bin:/bin",
              "CUDA_HOME": spec.cuda_root, "CUDA_PATH": spec.cuda_root,
              "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
              "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
              "DO_NOT_TRACK": "1", "VLLM_NO_USAGE_STATS": "1", "SGLANG_NO_USAGE_STATS": "1",
              "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
              "TOKENIZERS_PARALLELISM": "false", "LANG": "C.UTF-8", "NO_PROXY": "127.0.0.1,localhost"}
    if spec.runtime == "sglang" and spec.deterministic_inference:
        # SGLang's batch-invariant DeepGEMM path rejects some non-aligned
        # prefill shapes (observed with 0.5.18 / Qwen3.8). Use its existing
        # batch-invariant Triton implementation, not batch-variant GEMM.
        result["SGLANG_BATCH_INVARIANT_OPS_ENABLE_MM_DEEPGEMM"] = "0"
    visible = allocation.get("CUDA_VISIBLE_DEVICES")
    if visible is None or re.fullmatch(r"[A-Za-z0-9_.,/-]+", visible) is None:
        raise ValueError("serving requires explicit Slurm CUDA_VISIBLE_DEVICES")
    result["CUDA_VISIBLE_DEVICES"] = visible
    cpus = allocation.get("SLURM_CPUS_PER_TASK", "1")
    if not cpus.isdecimal() or int(cpus) < 1:
        raise ValueError("invalid allocated CPU count")
    result["OMP_NUM_THREADS"] = cpus
    if includes:
        if any(not Path(path).is_absolute() or ":" in str(path) for path in includes):
            raise ValueError("invalid header search path")
        result["CPATH"] = ":".join(str(path) for path in includes)
    return result


def headers(binding: dict, scratch: Path, env: dict[str, str], *, deadline: float) -> tuple[Path, ...]:
    row = fields(binding, {"path", "sha256", "size", "include_paths"})
    if (type(row["size"]) is not int or row["size"] < 1
            or type(row["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None
            or type(row["path"]) is not str or not Path(row["path"]).is_absolute()
            or type(row["include_paths"]) is not list or not row["include_paths"]):
        raise ValueError("invalid cached header binding")
    for name in row["include_paths"]:
        if type(name) is not str or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("header paths must remain inside the extraction directory")
    descriptor = os.open(row["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != row["size"]:
            raise ValueError("cached headers size/type mismatch")
        data = stream.read(row["size"] + 1)
    if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("cached headers digest mismatch")
    package = scratch / "headers.deb"
    with package.open("xb") as stream:
        stream.write(data)
    target = scratch / "headers"
    result = quiescent_call("headers", lambda: run_service(
        ("/usr/bin/dpkg-deb", "--extract", str(package), str(target)),
        cwd=scratch, environment=env, deadline=deadline))
    if result.status != "exited" or result.returncode != 0:
        raise RuntimeError(f"header extraction failed: {result.status}")
    includes = tuple(target / name for name in row["include_paths"])
    if any(not path.resolve().is_relative_to(target.resolve()) or not path.is_dir() for path in includes):
        raise ValueError("header extraction lacks declared includes")
    if not any((p / "Python.h").is_file() for p in includes) or not any((p / "pyconfig.h").is_file() for p in includes):
        raise ValueError("incomplete Python development headers")
    return includes


def inspect_installation(spec: ServingSpec, scratch: Path, env: dict[str, str], *, deadline: float) -> dict:
    # Metadata only: no torch/CUDA import and no model download. The server
    # launch/readiness gate subsequently checks whether this binding can run.
    script = ("import importlib.metadata as m,json,sys; "
              "print(json.dumps({'runtime':m.version(sys.argv[1]),'torch':m.version('torch')}))")
    result = quiescent_call("installation", lambda: run_service(
        (spec.python, "-I", "-B", "-c", script, spec.runtime), cwd=scratch,
        environment=env, deadline=deadline, output_bytes=None))
    if result.status != "exited" or result.returncode != 0:
        raise RuntimeError(f"runtime inspection failed: {result.status}")
    row = fields(parse_json(result.stdout), {"runtime", "torch"})
    if any(type(value) is not str or not value for value in row.values()):
        raise ValueError("invalid installed version metadata")
    expected = {"runtime": spec.package_version, "torch": spec.torch_version}
    if row != expected:
        raise InstallationMismatch(expected, row)
    model = Path(spec.model_path)
    # HF snapshot symlinks into its blob store are normal. This inventories
    # availability, not weight-content attestation; no implicit online fallback.
    shards = inspect_model_files(model)
    config = read_regular_file(model / "config.json")
    return {**row, "config_sha256": hashlib.sha256(config).hexdigest(), "weight_shards": len(shards),
            "weight_content_attested": False}
