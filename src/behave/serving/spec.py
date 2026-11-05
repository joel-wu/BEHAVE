"""Explicit standalone runtime binding; no installation or sibling imports.

The identity pins an operator declaration, not every byte of a Python environment
or a weight file. Provisioning/weight provenance and endpoint acceptance remain
separate evidence. Only local cached models are supported by this launcher.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from dataclasses import fields as dc_fields
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, fields, text


@dataclass(frozen=True, slots=True)
class ServingSpec:
    runtime: str
    python: str
    package_version: str
    torch_version: str
    model_path: str
    model_revision: str
    served_model: str
    reasoning_parser: str
    tool_parser: str
    cuda_root: str
    tensor_parallel: int = defaults.SERVING_TENSOR_PARALLEL
    context_length: int = defaults.SERVING_CONTEXT_TOKENS
    port: int = defaults.SERVING_PORT
    startup_seconds: int | None = defaults.SERVING_STARTUP_SECONDS
    lifetime_seconds: int | None = defaults.SERVING_LIFETIME_SECONDS
    log_bytes: int | None = defaults.SERVING_LOG_BYTES
    seed: int = defaults.SERVING_SEED
    cleanup_seconds: int | None = defaults.SERVING_CLEANUP_SECONDS
    enforce_eager: bool = False
    deterministic_inference: bool = False
    native_arguments: tuple[str, ...] = ()

    def __post_init__(self):
        if self.runtime not in {"sglang", "vllm"}:
            raise ValueError("only standalone sglang/vllm runtimes are supported")
        for item in dc_fields(self):
            value = getattr(self, item.name)
            if item.type == "str":
                text(value, item.name)
            elif item.type == "bool":
                if type(value) is not bool:
                    raise ValueError(f"{item.name} must be a boolean")
            elif item.name == "native_arguments":
                if type(value) is not tuple or any(type(arg) is not str or not arg or "\x00" in arg for arg in value):
                    raise ValueError("native_arguments must be a tuple of nonempty argv strings")
            elif item.type == "int | None" and value is None:
                continue
            elif type(value) is not int:
                raise ValueError(f"{item.name} must be an integer")
        for name in ("python", "model_path", "cuda_root"):
            if not Path(getattr(self, name)).is_absolute():
                raise ValueError(f"{name} must be an absolute local path")
        for name in ("served_model", "reasoning_parser", "tool_parser"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", getattr(self, name)) is None:
                raise ValueError(f"invalid {name}")
        for name in ("tensor_parallel", "context_length", "startup_seconds", "lifetime_seconds",
                     "log_bytes", "cleanup_seconds"):
            if getattr(self, name) is not None and getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if not 1 <= self.port <= 65535:
            raise ValueError("invalid port")
        if self.seed < 0:
            raise ValueError("invalid seed")
        if (self.startup_seconds is not None and self.lifetime_seconds is not None
                and self.startup_seconds >= self.lifetime_seconds):
            raise ValueError("startup must leave time within the service lifetime")
        if self.deterministic_inference and self.runtime != "sglang":
            raise ValueError("deterministic_inference is an SGLang launch mode")
        self.command()

    def as_dict(self) -> dict:
        row = {"schema": "behave.serving/2", **asdict(self)}
        # Absence keeps the prior launch behavior and existing record identity.
        if not self.deterministic_inference:
            del row["deterministic_inference"]
        if self.native_arguments:
            row["native_arguments"] = list(self.native_arguments)
        else:
            del row["native_arguments"]
        return row

    @classmethod
    def from_dict(cls, value: object) -> ServingSpec:
        if type(value) is dict:
            value = {"deterministic_inference": False, "native_arguments": [], **value}
        row = fields(value, {"schema", *(item.name for item in dc_fields(cls))})
        if row["schema"] != "behave.serving/2":
            raise ValueError("unsupported serving schema")
        if type(row["native_arguments"]) is not list:
            raise ValueError("native_arguments must be an argv array")
        return cls(**{key: value for key, value in row.items() if key not in {"schema", "native_arguments"}},
                   native_arguments=tuple(row["native_arguments"]))

    @property
    def identity(self) -> str:
        return BlobRef.of(canonical_json(self.as_dict())).sha256

    def command(self) -> tuple[str, ...]:
        common = ("--served-model-name", self.served_model, "--host", "127.0.0.1",
                  "--port", str(self.port), "--reasoning-parser", self.reasoning_parser,
                  "--tool-call-parser", self.tool_parser)
        if self.runtime == "sglang":
            command = (self.python, "-B", "-m", "sglang.launch_server", "--model-path", self.model_path,
                    "--revision", self.model_revision, "--weight-version", self.model_revision,
                    "--tp-size", str(self.tensor_parallel), "--context-length", str(self.context_length),
                    "--random-seed", str(self.seed),
                    *(("--enable-deterministic-inference",) if self.deterministic_inference else ()),
                    *(("--disable-cuda-graph",) if self.enforce_eager else ()), *common)
        else:
            command = (self.python, "-B", "-m", "vllm.entrypoints.openai.api_server", "--model", self.model_path,
                    "--revision", self.model_revision, "--tensor-parallel-size", str(self.tensor_parallel),
                    "--max-model-len", str(self.context_length), "--seed", str(self.seed),
                    *(("--enforce-eager",) if self.enforce_eager else ()),
                    "--enable-auto-tool-choice", "--disable-uvicorn-access-log", *common)
        owned = {arg for arg in command if arg.startswith("--")}
        if any(arg.partition("=")[0] in owned for arg in self.native_arguments):
            raise ValueError("native arguments cannot override a field already declared by ServingSpec")
        return (*command, *self.native_arguments)


def bound_spec(binding: object, runtime: str) -> ServingSpec:
    row = fields(binding, {"schema", "provenance", "shared", "runtimes", "headers"})
    if row["schema"] != "behave.local-serving-bindings/3" or runtime not in {"sglang", "vllm"}:
        raise ValueError("unsupported serving binding")
    text(row["provenance"], "binding provenance")
    runtimes = fields(row["runtimes"], {"sglang", "vllm"})
    selected = runtimes[runtime]
    if type(selected) is dict:
        selected = {"deterministic_inference": False, **selected}
    selected = fields(selected, {"python", "package_version", "torch_version", "tool_parser",
                                 "deterministic_inference"})
    shared = fields({"native_arguments": [], **row["shared"]},
                    {item.name for item in dc_fields(ServingSpec)} - {"runtime", *selected})
    if type(shared["native_arguments"]) is not list:
        raise ValueError("native_arguments must be an argv array")
    shared["native_arguments"] = tuple(shared["native_arguments"])
    return ServingSpec(runtime=runtime, **shared, **selected)
