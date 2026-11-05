"""Serving budgets belong to the operator; logging must not stop the server."""

import hashlib
import sys
import time
from pathlib import Path

import pytest
from test_serving import lifecycle, spec  # noqa: F401

from behave.core import runtime_defaults as defaults
from behave.core.records import canonical_json
from behave.isolation.service import ServiceResult
from behave.serving import ServingSession, ServingSpec
from behave.serving import environment as provision
from behave.serving import session as control


@pytest.mark.parametrize("runtime,flag", [("sglang", "--random-seed"), ("vllm", "--seed")])
@pytest.mark.parametrize("seed", [0, 1 << 31, 1 << 63])
def test_serving_preserves_seed_without_a_private_integer_width(runtime, flag, seed):
    # Argument transport only. The selected sampler owns its supported range.
    configured = spec(runtime=runtime, seed=seed)
    command = configured.command()
    assert command[command.index(flag) + 1] == str(seed)
    assert ServingSpec.from_dict(configured.as_dict()) == configured


@pytest.mark.parametrize("seed", [-1, True, 0.5])
def test_serving_seed_still_requires_a_nonnegative_integer(seed):
    with pytest.raises(ValueError, match="seed"):
        spec(seed=seed)


@pytest.mark.parametrize("runtime,arguments", [
    ("sglang", ("--mem-fraction-static", "0.65", "--max-running-requests", "200")),
    ("vllm", ("--gpu-memory-utilization", "0.65")),
])
def test_native_runtime_options_are_preserved_and_recorded(runtime, arguments):
    configured = spec(runtime=runtime, native_arguments=arguments)
    assert configured.command()[-len(arguments):] == arguments
    assert configured.as_dict()["native_arguments"] == list(arguments)
    assert ServingSpec.from_dict(configured.as_dict()) == configured
    assert configured.identity != spec(runtime=runtime).identity


def test_omitted_native_options_keep_existing_serving_identity():
    configured = spec()
    row = configured.as_dict()
    assert "native_arguments" not in row
    assert ServingSpec.from_dict(row) == configured


@pytest.mark.parametrize("arguments", [("--port", "1234"), ("--context-length=32",), ("--model-path", "/other")])
def test_native_options_cannot_shadow_bound_serving_fields(arguments):
    with pytest.raises(ValueError, match="override a field"):
        spec(runtime="sglang", native_arguments=arguments)


@pytest.mark.parametrize("arguments", ["--flag", ["--flag"], (None,), ("",), ("bad\x00arg",)])
def test_native_options_require_an_argv_tuple(arguments):
    with pytest.raises(ValueError, match="native_arguments"):
        spec(native_arguments=arguments)


def test_serving_uses_configured_scratch_and_allocated_cpus(tmp_path, monkeypatch):
    root = tmp_path / "operator-scratch"
    root.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(root))
    scratch = root / "incarnation"
    allocation = {"CUDA_VISIBLE_DEVICES": "GPU-fixture-a,GPU-fixture-b", "SLURM_CPUS_PER_TASK": "1025"}
    env = provision.environment(spec(), scratch, allocation)
    assert env["CUDA_VISIBLE_DEVICES"] == allocation["CUDA_VISIBLE_DEVICES"]
    assert env["OMP_NUM_THREADS"] == "1025"
    assert Path(env["TMPDIR"]).is_relative_to(root)
    with pytest.raises(ValueError, match="scratch"):
        provision.environment(spec(), root, allocation)


@pytest.mark.parametrize("runtime,deterministic", [("vllm", False), ("sglang", False), ("sglang", True)])
def test_deterministic_sgl_uses_upstream_batch_invariant_triton(tmp_path, monkeypatch, runtime, deterministic):
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path))
    allocation = {"CUDA_VISIBLE_DEVICES": "0", "SLURM_CPUS_PER_TASK": "1",
                  "SGLANG_BATCH_INVARIANT_OPS_ENABLE_MM_FALLBACK_VARIANT": "1"}
    configured = spec(runtime=runtime, deterministic_inference=deterministic)
    env = provision.environment(configured, tmp_path / "service", allocation)
    assert env.get("SGLANG_BATCH_INVARIANT_OPS_ENABLE_MM_DEEPGEMM") == ("0" if deterministic else None)
    assert "SGLANG_BATCH_INVARIANT_OPS_ENABLE_MM_FALLBACK_VARIANT" not in env
    assert "--disable-radix-cache" not in configured.command()


def test_header_extraction_uses_the_callers_deadline(tmp_path, monkeypatch):
    payload = b"fixture package"
    package = tmp_path / "package.deb"
    package.write_bytes(payload)
    include = "usr/include/python"
    binding = {"path": str(package), "sha256": hashlib.sha256(payload).hexdigest(),
               "size": len(payload), "include_paths": [include]}
    deadline = time.monotonic() + 300

    def extract(argv, **kwargs):
        assert kwargs["deadline"] == deadline
        directory = Path(argv[-1]) / include
        directory.mkdir(parents=True)
        for name in ("Python.h", "pyconfig.h"):
            (directory / name).write_text("fixture")
        return ServiceResult("exited", 0, b"", b"", 0.)

    monkeypatch.setattr(provision, "run_service", extract)
    assert provision.headers(binding, tmp_path, {}, deadline=deadline)


def test_installation_inspection_uses_the_callers_deadline(tmp_path, monkeypatch):
    deadline = time.monotonic() + 300
    (tmp_path / "model.safetensors").write_bytes(b"fixture weights")
    (tmp_path / "config.json").write_bytes(b"{}")

    def inspect(*args, **kwargs):
        assert kwargs["deadline"] == deadline
        assert kwargs["output_bytes"] is None
        return ServiceResult("exited", 0, canonical_json({"runtime": "0.28.0", "torch": "2.13.0+cu130"}), b"", 0.)

    monkeypatch.setattr(provision, "run_service", inspect)
    assert provision.inspect_installation(spec(model_path=str(tmp_path)), tmp_path, {}, deadline=deadline)


def test_model_server_logs_are_truncated_without_terminating_the_service(lifecycle, monkeypatch):  # noqa: F811
    make, active = lifecycle
    serve = control.run_service

    def observe(*args, **kwargs):
        assert kwargs["output_bytes"] == defaults.SERVING_LOG_BYTES
        result = serve(*args, **kwargs)
        return ServiceResult(result.status, result.returncode, result.stdout, result.stderr,
                             result.elapsed_seconds, dropped_output_bytes=1234)

    monkeypatch.setattr(control, "run_service", observe)
    session = make()
    session.start()
    stopped = session.close()
    assert stopped["cleanup_complete"] and stopped["dropped_output_bytes"] == 1234
    assert not active


@pytest.mark.isolation
def test_live_serving_survives_log_overflow_and_releases_its_port(tmp_path, monkeypatch):
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    program = (
        "from http.server import BaseHTTPRequestHandler,HTTPServer\n"
        "print('verbose model log ' * 4096, flush=True)\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  self.send_response(200); self.end_headers(); "
        "self.wfile.write(b'{\"object\":\"list\",\"data\":[{\"id\":\"fixture-serving\"}]}')\n"
        f"HTTPServer(('127.0.0.1',{port}),Handler).serve_forever()"
    )
    monkeypatch.setattr(ServingSpec, "command", lambda self: (sys.executable, "-I", "-B", "-c", program))
    session = ServingSession(spec(port=port, startup_seconds=5, log_bytes=1024), scratch=tmp_path,
                             environment={"PATH": "/usr/bin:/bin"}, records=tmp_path / "session")
    try:
        session.start()
        session.check_alive()
    finally:
        stopped = session.close()
    assert stopped["ready"] and stopped["cleanup_complete"] and stopped["service_status"] == "cancelled"
    assert stopped["dropped_output_bytes"] > 0
    assert sum((session.records / name).stat().st_size for name in ("stdout.log", "stderr.log")) <= 1024
    control.check_port(port)
