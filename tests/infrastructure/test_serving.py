"""Serving control-plane fixtures. GPU acceptance is a separate, detached job."""

import errno
import hashlib
import importlib.util
import sys
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from behave.agent.model import ModelFailure, ModelReply, ModelRequest, ToolCall
from behave.agent.process_model import ProcessModelBackend
from behave.agent.providers.chat_wire import request_body, response
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.tools import file_tools
from behave.core.records import canonical_json, parse_json
from behave.isolation.service import ServiceResult
from behave.serving import ServingFailure, ServingSession, ServingSpec
from behave.serving import environment as provision
from behave.serving import session as control
from behave.serving.spec import bound_spec


def test_cold_server_probe_does_not_spawn_a_process(tmp_path, monkeypatch):
    def refused(*args, **kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(control.socket, "create_connection", refused)
    monkeypatch.setattr(control, "run_service", lambda *a, **kw: pytest.fail("cold server spawned a probe"))
    assert control.probe(spec(), scratch=tmp_path, deadline=time.monotonic() + 2) is None


def spec(**changes):
    return replace(ServingSpec("vllm", "/runtime/bin/python", "0.28.0", "2.13.0+cu130", "/cache/model",
                               "revision", "fixture-serving", "qwen3", "qwen3_xml", "/usr/local/cuda-13.1",
                               startup_seconds=1, lifetime_seconds=10), **changes)


def test_closed_spec_roundtrip_and_runtime_specific_flags():
    s = spec()
    assert ServingSpec.from_dict(s.as_dict()) == s
    assert s.identity == ServingSpec.from_dict(s.as_dict()).identity
    assert replace(s, cleanup_seconds=61).identity != s.identity
    command = s.command()
    assert command[:4] == (s.python, "-B", "-m", "vllm.entrypoints.openai.api_server")
    assert command[command.index("--model") + 1] == s.model_path
    assert "--enforce-eager" not in command and "--enable-auto-tool-choice" in command
    g = replace(s, runtime="sglang", tool_parser="qwen3_coder")
    assert "sglang.launch_server" in g.command() and "--disable-cuda-graph" not in g.command()
    assert g.identity != s.identity
    with pytest.raises(ValueError):
        ServingSpec.from_dict({**s.as_dict(), "trust_remote_code": True})


@pytest.mark.parametrize("runtime,flag", [("sglang", "--disable-cuda-graph"), ("vllm", "--enforce-eager")])
def test_eager_execution_is_an_explicit_recorded_choice(runtime, flag):
    normal = spec(runtime=runtime)
    eager = replace(normal, enforce_eager=True)
    assert flag not in normal.command() and flag in eager.command()
    assert eager.identity != normal.identity
    assert ServingSpec.from_dict(eager.as_dict()) == eager
    with pytest.raises(ValueError):
        replace(normal, enforce_eager="false")


def test_public_binding_uses_one_closed_policy_for_submit_and_run():
    root = Path(__file__).resolve().parents[2]
    binding = parse_json((root / "infra/model_runtimes/local-serving.json").read_bytes())
    for runtime in ("sglang", "vllm"):
        s = bound_spec(binding, runtime)
        assert s.tensor_parallel == 8 and s.runtime == runtime
        assert s.model_path.endswith(s.model_revision)
        assert s.enforce_eager is False
        assert s.python == binding["runtimes"][runtime]["python"]
        assert Path(s.python).parts[-2:] == ("bin", "python")
    # Both imports report the same CUDA build, but their installed distribution
    # metadata differs. Pin what inspection actually reads, per environment.
    assert bound_spec(binding, "sglang").torch_version == "2.13.0"
    assert bound_spec(binding, "vllm").torch_version == "2.13.0+cu130"
    with pytest.raises(ValueError):
        bound_spec({**binding, "extra": "option"}, "vllm")
    del binding["shared"]["enforce_eager"]
    with pytest.raises(ValueError):
        bound_spec(binding, "vllm")  # No guessing how a historical smoke was launched.


def test_sgl_deterministic_sampling_is_an_explicit_launch_mode():
    normal = spec(runtime="sglang")
    seeded = replace(normal, deterministic_inference=True)
    flag = "--enable-deterministic-inference"
    assert flag not in normal.command() and flag in seeded.command()
    assert "--disable-radix-cache" not in seeded.command()
    assert "deterministic_inference" not in normal.as_dict()
    assert ServingSpec.from_dict(normal.as_dict()) == normal
    assert ServingSpec.from_dict(seeded.as_dict()) == seeded
    assert seeded.identity != normal.identity
    with pytest.raises(ValueError, match="SGLang"):
        spec(deterministic_inference=True)
    with pytest.raises(ValueError, match="boolean"):
        replace(normal, deterministic_inference="false")


def test_deterministic_mode_belongs_to_the_selected_runtime_binding():
    root = Path(__file__).resolve().parents[2]
    binding = parse_json((root / "infra/model_runtimes/local-serving.json").read_bytes())
    binding["runtimes"]["sglang"]["deterministic_inference"] = True
    assert bound_spec(binding, "sglang").deterministic_inference
    assert not bound_spec(binding, "vllm").deterministic_inference
    del binding["runtimes"]["sglang"]["deterministic_inference"]
    assert not bound_spec(binding, "sglang").deterministic_inference


@pytest.mark.parametrize("name", ["reasoning", "reasoning_content"])
def test_installed_chat_protocol_preserves_native_reasoning_name(name):
    ep = LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions", "fixture", "fixture/1")
    message = {"role": "assistant", "content": "Done", name: "retained reasoning",
               "annotations": None, "refusal": None, "audio": None, "function_call": None}
    packet = {"model": "fixture", "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
              "usage": {"prompt_tokens": 3, "completion_tokens": 7}}
    reply = response(ep, 200, canonical_json(packet))
    assert type(reply) is ModelReply and reply.continuation == {name: "retained reasoning"}
    req = ModelRequest("r", 1, 0, ({"role": "user", "content": "go"}, reply.message(),
                                 {"role": "user", "content": "continue"}),
                       tuple(t.declaration() for t in file_tools()), ep.model_policy(), 10)
    outgoing = parse_json(request_body(ep, req))
    assert outgoing["messages"][1][name] == "retained reasoning"
    for field in ("annotations", "audio", "function_call"):
        message[field] = {}
        assert type(response(ep, 200, canonical_json(packet))) is ModelFailure
        message[field] = None
    message["reasoning_content" if name == "reasoning" else "reasoning"] = "other"
    assert type(response(ep, 200, canonical_json(packet))) is ModelFailure


@pytest.mark.parametrize("changes", [{"runtime": "slime"}, {"port": True}, {"port": 0},
    {"startup_seconds": 10}, {"log_bytes": 0}, {"python": "python"}, {"served_model": "x\n--evil"},
    {"model_path": "repo/name"}, {"tensor_parallel": 0}, {"tool_parser": "--new-flag"}])
def test_invalid_launch_policy_refused(changes):
    with pytest.raises(ValueError):
        spec(**changes)


def test_complete_calls_with_indices_preserve_order_and_require_real_ids():
    ep = LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions", "fixture", "fixture/1")
    calls = [{"id": f"call-{index}", "index": index, "type": "function", "function": {
        "name": "read_file", "arguments": canonical_json({"path": f"file-{index}"}).decode()}}
        for index in range(2)]
    packet = {"model": ep.model, "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "", "reasoning_content": None, "tool_calls": calls}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 3, "reasoning_tokens": 0}}
    raw = canonical_json(packet)
    reply = response(ep, 200, raw)
    assert type(reply) is ModelReply and reply.raw == raw
    assert [call.call_id for call in reply.calls] == ["call-0", "call-1"]
    assert reply.usage["normalized"]["reasoning_output_tokens"] == 0
    messages = ({"role": "user", "content": "read both files"}, reply.message(),
                *({"role": "tool", "call_id": call.call_id, "content": {"content": "fixture"}}
                  for call in reply.calls))
    outgoing = parse_json(request_body(ep, ModelRequest("r", 1, 0, messages, (), ep.model_policy(), 5)))
    assert outgoing["messages"][1]["tool_calls"] == [
        {key: value for key, value in call.items() if key != "index"} for call in calls]
    for bad in (0, 2, True, "1"):
        calls[1]["index"] = bad
        assert type(response(ep, 200, canonical_json(packet))) is ModelFailure
    calls[1]["index"] = None  # Nullable ordinal is equivalent to absent, not a generated ID.
    assert type(response(ep, 200, canonical_json(packet))) is ModelReply
    del calls[1]["id"]
    assert type(response(ep, 200, canonical_json(packet))) is ModelFailure


@pytest.mark.parametrize("top,detail,valid", [(0, None, True), (2, 2, True), (2, 1, False),
                                            (True, None, False), (4, None, False)])
def test_reasoning_usage_spellings_never_double_bill_or_hide_conflicts(top, detail, valid):
    ep = LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions", "fixture", "fixture/1")
    packet = {"model": ep.model, "choices": [{"index": 0, "finish_reason": "stop", "message": {
        "role": "assistant", "content": "done"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 3,
        "reasoning_tokens": top, "completion_tokens_details": {"reasoning_tokens": detail}}}
    reply = response(ep, 200, canonical_json(packet))
    assert type(reply) is ModelReply and reply.usage["raw"]["reasoning_tokens"] == top
    assert (reply.usage["normalized"] is not None) == valid
    if valid:
        assert reply.usage["normalized"]["output_tokens"] == 3
        assert reply.usage["normalized"]["reasoning_output_tokens"] == top


def test_environment_does_not_forward_credentials_proxies_or_import_paths():
    with TemporaryDirectory(prefix="serving-env-", dir="/tmp") as directory:
        env = provision.environment(spec(), Path(directory), {
            "CUDA_VISIBLE_DEVICES": "0,1", "SLURM_CPUS_PER_TASK": "4", "OPENAI_API_KEY": "secret",
            "HTTP_PROXY": "secret", "PYTHONPATH": "bad", "LD_PRELOAD": "bad", "HOME": "/secret"})
        assert not {"OPENAI_API_KEY", "HTTP_PROXY", "PYTHONPATH", "LD_PRELOAD"} & env.keys()
        assert env["HF_HUB_OFFLINE"] == "1" and env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
        assert env["OMP_NUM_THREADS"] == "4" and env["CUDA_VISIBLE_DEVICES"] == "0,1"
        for name in ("HOME", "TMPDIR", "HF_HOME", "TRITON_CACHE_DIR", "TORCHINDUCTOR_CACHE_DIR"):
            assert Path(env[name]).is_relative_to(directory) and Path(env[name]).is_dir()
        with pytest.raises(ValueError):
            provision.environment(spec(), Path(directory), {})
    with pytest.raises(ValueError):
        provision.environment(spec(), Path("/home/host-user"), {"CUDA_VISIBLE_DEVICES": "0"})


def test_cached_headers_authenticate_before_any_extraction(tmp_path, monkeypatch):
    payload = b"not a deb, but authenticated fixture bytes"
    source = tmp_path / "cached.deb"
    source.write_bytes(payload)
    binding = {"path": str(source), "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
               "include_paths": ["usr/include/python"]}
    called = []
    def extract(argv, **kwargs):
        called.append(argv)
        assert (tmp_path / "headers.deb").read_bytes() == payload
        include = Path(argv[-1]) / binding["include_paths"][0]
        include.mkdir(parents=True)
        for name in ("Python.h", "pyconfig.h"):
            (include / name).write_text("fixture")
        return ServiceResult("exited", 0, b"", b"", 0.)
    monkeypatch.setattr(provision, "run_service", extract)
    with pytest.raises(ValueError, match="digest"):
        provision.headers({**binding, "sha256": "0" * 64}, tmp_path, {}, deadline=time.monotonic() + 10)
    assert not called
    assert provision.headers(binding, tmp_path, {}, deadline=time.monotonic() + 10)
    assert len(called) == 1


def test_installation_mismatch_carries_only_exact_public_version_evidence(tmp_path, monkeypatch):
    observed = {"runtime": "0.28.0", "torch": "2.13.0"}
    monkeypatch.setattr(provision, "run_service", lambda *a, **kw: ServiceResult(
        "exited", 0, canonical_json(observed), b"not included in the finding", .01))
    with pytest.raises(provision.InstallationMismatch) as failure:
        provision.inspect_installation(spec(), tmp_path, {}, deadline=time.monotonic() + 10)
    assert failure.value.code == "installation-version-mismatch"
    assert failure.value.expected == {"runtime": "0.28.0", "torch": "2.13.0+cu130"}
    assert failure.value.observed == observed
    observed["extra"] = "unexpected output"
    with pytest.raises(ValueError) as malformed:
        provision.inspect_installation(spec(), tmp_path, {}, deadline=time.monotonic() + 10)
    assert not isinstance(malformed.value, provision.InstallationMismatch)


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    class Port:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def bind(self, address):
            assert address == ("127.0.0.1", 31000)
        def setsockopt(self, level, option, value):
            assert (level, option, value) == (control.socket.SOL_SOCKET, control.socket.SO_REUSEADDR, 1)
        def listen(self, backlog):
            assert backlog == 1
    monkeypatch.setattr(control.socket, "socket", Port)
    active = []
    def serve(argv, **kwargs):
        active.append(argv)
        try:
            while not kwargs["cancelled"]():
                time.sleep(.001)
            return ServiceResult("cancelled", -9, b"server stdout", b"server stderr", .01)
        finally:
            active.remove(argv)
    monkeypatch.setattr(control, "run_service", serve)
    monkeypatch.setattr(control, "probe", lambda *a, **kw: canonical_json({
        "object": "list", "data": [{"id": "fixture-serving"}]}))
    def make(name="service", **changes):
        return ServingSession(spec(**changes), scratch=tmp_path, environment={}, records=tmp_path / name)
    return make, active


def test_readiness_uses_the_whole_startup_deadline(lifecycle, monkeypatch):
    make, active = lifecycle
    deadlines = []

    def ready(*args, deadline, **kwargs):
        deadlines.append(deadline)
        return canonical_json({"object": "list", "data": [{"id": "fixture-serving"}]})

    monkeypatch.setattr(control.time, "monotonic", lambda: 100.)
    monkeypatch.setattr(control, "probe", ready)
    session = make(startup_seconds=30, lifetime_seconds=60)
    try:
        session.start()
        assert deadlines == [130.]
    finally:
        session.close()
    assert not active


def test_uncapped_serving_uses_runtime_none_through_shutdown(lifecycle, monkeypatch):
    make, active = lifecycle
    recorded = {}
    original_service = control.run_service
    original_join = control.threading.Thread.join

    def serve(*args, **kwargs):
        recorded["service"] = kwargs
        return original_service(*args, **kwargs)

    def ready(*args, **kwargs):
        recorded["probe"] = kwargs
        return canonical_json({"object": "list", "data": [{"id": "fixture-serving"}]})

    def join(thread, timeout=None):
        recorded["join"] = timeout
        return original_join(thread, timeout)

    monkeypatch.setattr(control, "run_service", serve)
    monkeypatch.setattr(control, "probe", ready)
    monkeypatch.setattr(control.threading.Thread, "join", join)
    session = make(startup_seconds=None, lifetime_seconds=None, cleanup_seconds=None, log_bytes=None)
    try:
        session.start()
    finally:
        result = session.close()
    assert result["cleanup_complete"] and not active
    assert recorded["service"]["deadline"] is recorded["service"]["cleanup_seconds"] is None
    assert recorded["service"]["output_bytes"] is recorded["probe"]["deadline"] is recorded["join"] is None
    assert recorded["probe"]["cancelled"]()


def test_readiness_shutdown_and_explicit_restart_keep_policy(lifecycle):
    make, active = lifecycle
    first = make("first")
    ep = first.start()
    assert ep.runtime_revision == first.spec.identity
    stopped = first.close()
    assert stopped["cleanup_complete"] and stopped["service_status"] == "cancelled"
    assert first.close() == stopped and not active
    with pytest.raises(ServingFailure, match="session-already-used"):
        first.start()
    second = make("second")
    try:
        assert second.start().model_policy() == ep.model_policy()
        assert second.incarnation != first.incarnation
    finally:
        second.close()
    assert not active


def test_server_death_after_readiness_is_not_hidden_by_cleanup(lifecycle):
    make, active = lifecycle
    s = make()
    s.start()
    s._cancel.set()  # the fixture server dies after the ready event
    assert s._done.wait(1)
    with pytest.raises(ServingFailure, match="server-cancelled"):
        s.check_alive()
    assert s.close()["cleanup_complete"] and not active


def test_port_conflict_does_not_start_or_kill_an_existing_listener(lifecycle, monkeypatch):
    make, active = lifecycle
    class Occupied:
        def __enter__(self):
            raise OSError(errno.EADDRINUSE, "port in use")
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(control.socket, "socket", Occupied)
    s = make()
    with pytest.raises(ServingFailure, match="^port-in-use$"):
        s.start()
    assert not active and s.close()["service_status"] is None
    assert s.close()["failure"] == "port-in-use"


@pytest.mark.isolation
def test_port_check_distinguishes_live_listener_from_tcp_time_wait():
    import socket

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        address = listener.getsockname()
        with pytest.raises(ServingFailure, match="^port-in-use$"):
            control.check_port(address[1])
        # Server closes first, leaving its local port in TIME_WAIT. No sleeps
        # or external process are needed to reproduce sequential server use.
        with socket.create_connection(address, timeout=2) as client:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                connection.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b""
                client.shutdown(socket.SHUT_WR)
                assert connection.recv(1) == b""
    with socket.socket() as plain:
        with pytest.raises(OSError) as failure:
            plain.bind(address)
        assert failure.value.errno == errno.EADDRINUSE
    control.check_port(address[1])


def test_port_reservation_error_is_typed_without_os_payload(monkeypatch):
    def denied():
        raise PermissionError(errno.EACCES, "private diagnostic payload")
    monkeypatch.setattr(control.socket, "socket", denied)
    with pytest.raises(ServingFailure, match="^port-reservation-failed$"):
        control.check_port(31000)


def test_supervisor_exception_is_not_confirmed_cleanup(lifecycle, monkeypatch):
    make, _ = lifecycle
    def broken(*args, **kwargs):
        raise RuntimeError("fixture")
    monkeypatch.setattr(control, "run_service", broken)
    monkeypatch.setattr(control, "probe", lambda *a, **kw: None)
    s = make()
    with pytest.raises(ServingFailure, match="server-supervisor-error") as error:
        s.start()
    assert not error.value.cleanup_state.quiescent
    assert any("cleanup-incomplete" in note for note in error.value.__notes__)
    with pytest.raises(ServingFailure, match="cleanup-incomplete"):
        s.close()


@pytest.mark.parametrize("packet", [b"{}", b"{bad", canonical_json({"object": "list", "data": [
    {"id": "other"}]}), canonical_json({"object": "list", "data": [{"id": "fixture-serving"}] * 2})])
def test_wrong_or_malformed_readiness_never_adopts_endpoint(lifecycle, monkeypatch, packet):
    make, active = lifecycle
    monkeypatch.setattr(control, "probe", lambda *a, **kw: packet)
    s = make()
    with pytest.raises(ValueError if packet == b"{bad" else ServingFailure):
        s.start()
    assert not active and s.close()["cleanup_complete"]
    assert not (s.records / "ready.json").exists()


def test_startup_timeout_cancels_server(lifecycle, monkeypatch):
    make, active = lifecycle
    monkeypatch.setattr(control, "probe", lambda *a, **kw: None)
    s = make()
    with pytest.raises(ServingFailure, match="startup-timeout"):
        s.start()
    assert not active and s.close()["failure"] == "startup-timeout"


@pytest.mark.parametrize("ready", [False, True])
def test_uncapped_startup_obeys_caller_cancellation(lifecycle, monkeypatch, ready):
    make, active = lifecycle
    stopped = control.threading.Event()

    def probe(*args, cancelled, **kwargs):
        stopped.set()
        assert cancelled()
        return canonical_json({"data": [{"id": "fixture-serving"}]}) if ready else None

    monkeypatch.setattr(control, "probe", probe)
    session = make(startup_seconds=None, lifetime_seconds=None, cleanup_seconds=None)
    with pytest.raises(ServingFailure, match="startup-cancelled"):
        session.start(cancelled=stopped.is_set)
    receipt = session.close()
    assert not active and receipt["cleanup_complete"] and not receipt["ready"]


def test_startup_can_be_cancelled_before_readiness_probe(lifecycle, monkeypatch):
    make, active = lifecycle
    monkeypatch.setattr(control, "probe", lambda *a, **kw: pytest.fail("already cancelled"))
    monkeypatch.setattr(control, "run_service", lambda *a, **kw: pytest.fail("already cancelled"))
    session = make(startup_seconds=None, lifetime_seconds=None, cleanup_seconds=None)
    with pytest.raises(ServingFailure, match="startup-cancelled"):
        session.start(cancelled=lambda: True)
    assert not active and session.close()["cleanup_complete"]


@pytest.mark.parametrize("status", ["exited", "timeout", "output-limit", "cleanup-incomplete"])
def test_early_failure_never_yields_a_ready_endpoint(lifecycle, monkeypatch, status):
    make, _ = lifecycle
    monkeypatch.setattr(control, "run_service", lambda *a, **kw: ServiceResult(status, 1, b"", b"why", 0.))
    monkeypatch.setattr(control, "probe", lambda *a, **kw: None)
    s = make()
    with pytest.raises(ServingFailure):
        s.start()
    row = parse_json((s.records / "stopped.json").read_bytes())
    assert row["cleanup_complete"] == (status != "cleanup-incomplete")
    if status == "cleanup-incomplete":
        with pytest.raises(ServingFailure, match="cleanup-incomplete"):
            s.close()


def load_tool(monkeypatch, name):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "tools"))
    info = importlib.util.spec_from_file_location(name, root / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(info)
    info.loader.exec_module(module)
    return module


def test_live_smoke_harness_uses_real_tools_and_cold_replay(tmp_path, monkeypatch):
    job = load_tool(monkeypatch, "model_service_job")
    settings = load_tool(monkeypatch, "model_task_smoke").smoke_config()
    settings["tool_attempt"].update(max_turns=4, request_timeout_seconds=19)
    seen = []
    def generate(self, request):
        seen.append(request)
        if request.turn == 0:
            return ModelReply("", (ToolCall("read", "read_file", {"path": "input.txt"}),))
        if request.turn == 1:
            content = request.messages[-2]["content"]["content"]
            return ModelReply("", (ToolCall("write", "write_file", {
                "path": "output.txt", "content": content + ":checked"}),))
        assert request.messages[-2]["content"]["written"] == "output.txt"
        return ModelReply("Done", finish_reason="stop")
    monkeypatch.setattr(ProcessModelBackend, "generate", generate)
    endpoint = LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions", "fixture", "fixture/1",
        parameters={"max_tokens": 3072, "temperature": .7, "seed": 29,
                    "chat_template_kwargs": {"enable_thinking": True}})
    result = job.tool_smoke(endpoint, tmp_path / "attempt", settings=settings)
    assert result["passed"] and len(seen) == 3
    assert result["endpoint"] == endpoint.as_dict()
    assert all(request.model_policy == endpoint.model_policy() for request in seen)
    assert all(0 < request.timeout_seconds <= 19 for request in seen)
    assert result["tool_calls"] == ["read_file", "write_file"] and result["unpriced_dispatches"] == 3


@pytest.mark.parametrize("mode", ["seeded", "ignores-seed", "same-output", "failure"])
def test_sampling_check_uses_operator_seeds_and_preserves_outcomes(tmp_path, monkeypatch, mode):
    job = load_tool(monkeypatch, "model_service_job")
    settings = load_tool(monkeypatch, "model_task_smoke").smoke_config()
    settings["sampling_check"] = {"seeds": [17, 29, 17, 29],
                                   "parameters": {"max_tokens": 64, "temperature": 1}}
    endpoint = LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions", "fixture", "fixture/1")
    expected = [replace(endpoint, parameters={**settings["sampling_check"]["parameters"], "seed": seed})
                for seed in settings["sampling_check"]["seeds"]]
    seen = []
    def generate(self, request):
        selected = expected[len(seen)]
        assert self.command == selected.command()
        assert request.model_policy == selected.model_policy()
        seen.append(request)
        if mode == "failure":
            return ModelFailure("permanent", "fixture-error", raw=b"failure")
        seed = selected.parameters["seed"]
        value = len(seen) if mode == "ignores-seed" else 0 if mode == "same-output" else seed
        return ModelReply(str(value), raw=b"response", finish_reason="stop")
    monkeypatch.setattr(ProcessModelBackend, "generate", generate)
    report = job.sampling_smoke(endpoint, tmp_path / "sampling", settings)
    count = 1 if mode == "failure" else 4
    assert len(seen) == count
    assert len({tuple(str(m) for m in r.messages) for r in seen}) == 1
    assert report["passed"] == (mode in {"seeded", "same-output"})
    assert len(list((tmp_path / "sampling").glob("*.outcome.json"))) == count
    assert report["distinct_outputs"] == {"seeded": 2, "ignores-seed": 4, "same-output": 1, "failure": 0}[mode]


def test_extra_snapshot_files_are_frozen_and_verified(tmp_path, monkeypatch):
    runner = load_tool(monkeypatch, "infra_validation_job")
    monkeypatch.setattr(runner, "FILES", ("base.txt", "data/splits/current.json"))
    monkeypatch.setattr(runner, "TREES", ())
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "base.txt").write_text("base")
    (repo / "binding.json").write_text("{}")
    (repo / "external").mkdir()
    split = repo / 'data/splits/fixture'
    split.mkdir(parents=True)
    (split.parent / 'current.json').write_text('{"authored":"fixture"}')
    for name in ('eval.txt', 'external_eval.txt'):
        (split / name).write_text('task\n')
    snapshot = tmp_path / "run" / "snapshot"
    frozen = runner.freeze(repo, snapshot, extra_files=("binding.json",))
    assert runner.stage(snapshot, tmp_path / "staged") == frozen
    (snapshot / "binding.json").chmod(0o600)
    (snapshot / "binding.json").write_text("changed")
    with pytest.raises(RuntimeError, match="differs from its frozen inventory"):
        runner.stage(snapshot, tmp_path / "bad-stage")


@pytest.mark.parametrize('cli', [False, True], ids=['imported-entry', 'isolated-cli'])
def test_serving_bootstrap_uses_verified_local_modules(tmp_path, monkeypatch, cli):
    import subprocess

    from behave.core.artifacts import BlobRef
    from behave.runtime import snapshots

    root = Path(__file__).resolve().parents[2]
    snapshot = tmp_path / "snapshot"
    probe = '''
import json, os, sys
from pathlib import Path
from behave.core import paths
Path(os.environ['BEHAVE_TEST_IMPORT_PROBE']).write_text(json.dumps({
    'serving': __file__, 'paths': paths.__file__,
    'smoke': sys.modules['model_task_smoke'].__file__,
    'worker': sys.modules['tools.model_service_job'].__file__}))
'''
    name = 'src/behave/serving/__init__.py'
    record = snapshots.freeze(root, snapshot, overrides={name: (root / name).read_bytes() + probe.encode()})
    scratch = tmp_path / 'configured-scratch'
    scratch.mkdir()
    marker, output = tmp_path / 'imports.json', tmp_path / 'evidence'
    monkeypatch.setenv('BEHAVE_SCRATCH_DIR', str(scratch))
    monkeypatch.setenv('BEHAVE_TEST_IMPORT_PROBE', str(marker))
    missing_binding = tmp_path / 'missing-binding.json'
    expected = BlobRef.of(b'no binding')
    if cli:
        result = subprocess.run([sys.executable, '-I', '-S', '-B',
            str(snapshot / 'tools/model_service_job.py'), '_run', str(snapshot), str(output), 'sglang',
            '--binding-input', str(missing_binding), '--binding-sha256', expected.sha256,
            '--binding-size', str(expected.size_bytes)], capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 1, result.stdout + result.stderr
    else:
        job = load_tool(monkeypatch, 'model_service_job')
        assert job.run(snapshot, output, 'sglang', binding_input=missing_binding,
                       binding_reference=expected.as_dict()) == 1
    origins = parse_json(marker.read_bytes())
    local = Path(origins['worker']).parents[1]
    assert local.is_relative_to(scratch)
    assert all(Path(path).is_relative_to(local) for path in origins.values())
    report = parse_json((output / 'result.json').read_bytes())
    assert report['snapshot'] == record['sha256'] and report['failure_phase'] == 'binding'
    assert report['cleanup_complete'] is True and report['passed'] is False
    assert not list(scratch.iterdir())  # No provisioning or GPU/model dispatch occurred.


@pytest.mark.parametrize("native_python", ["", "/configured/trainer/bin/python"])
def test_validation_command_declares_tools_and_cache_without_inheriting_environment(tmp_path, monkeypatch, native_python):
    from types import SimpleNamespace

    from behave.core import runtime_defaults as defaults

    runner = load_tool(monkeypatch, "infra_validation_job")
    monkeypatch.setenv("PROVIDER_KEY_CANARY", "must-not-copy")
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/configured/tools/uv" if name == "uv" else None)

    def query(argv, **kwargs):
        assert argv in (("/configured/tools/uv", "cache", "dir"), ("/configured/tools/uv", "python", "dir"))
        assert kwargs["timeout"] == defaults.TOOL_INSPECTION_SECONDS and kwargs["check"] is True
        return SimpleNamespace(stdout=f"/configured/{argv[1]}/uv\n")

    monkeypatch.setattr(runner.subprocess, "run", query)
    original = runner.validation_command(tmp_path / "snapshot", tmp_path / "run", "test_jobs", "")
    argv = runner.validation_command(tmp_path / "snapshot", tmp_path / "run", "test_jobs", "", native_python)
    assert argv[0] == "/usr/bin/env"
    assert "/configured/tools" in argv[1].removeprefix("PATH=").split(":")
    assert argv[2] == "UV_CACHE_DIR=/configured/cache/uv"
    assert argv[3] == "UV_PYTHON_INSTALL_DIR=/configured/python/uv"
    assert argv[4] == "UV_PYTHON=" + sys.executable
    assert "BEHAVE_TEST_SUITE_SECONDS=720" in argv
    longer = runner.validation_command(tmp_path / "snapshot", tmp_path / "run", "test_jobs", "", wall_seconds=4800)
    assert "BEHAVE_TEST_SUITE_SECONDS=4800" in longer
    ppa = runner.validation_command(tmp_path / "snapshot", tmp_path / "run", "test_ppa", "",
                                    ppa_root="/configured/ppa")
    assert "BEHAVE_PPA_ROOT=/configured/ppa" in ppa
    assert all(not arg.startswith("BEHAVE_PPA_ROOT=") for arg in original)
    assert all("must-not-copy" not in arg and "PROVIDER_KEY_CANARY" not in arg for arg in argv)
    assert original[-2:] == ("test_jobs", "")
    assert argv == (*original, *((native_python,) if native_python else ()))


@pytest.mark.isolation
@pytest.mark.parametrize("incarnation", range(5))
@pytest.mark.parametrize("uncapped", [False, True])
def test_namespace_service_lifecycle_with_actual_http_readiness(tmp_path, monkeypatch, incarnation, uncapped):
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    program = ("from http.server import BaseHTTPRequestHandler,HTTPServer\n"
               "class Handler(BaseHTTPRequestHandler):\n"
               " def do_GET(self):\n"
               "  self.send_response(200); self.end_headers(); "
               "self.wfile.write(b'{\"object\":\"list\",\"data\":[{\"id\":\"fixture-serving\"}]}')\n"
               f"HTTPServer(('127.0.0.1',{port}),Handler).serve_forever()")
    monkeypatch.setattr(ServingSpec, "command", lambda self: (sys.executable, "-I", "-B", "-c", program))
    limits = {"startup_seconds": None, "lifetime_seconds": None, "cleanup_seconds": None, "log_bytes": None} if uncapped else {}
    configuration = replace(spec(port=port, startup_seconds=5), **limits)
    s = ServingSession(configuration, scratch=tmp_path, environment={"PATH": "/usr/bin:/bin"},
                       records=tmp_path / "session")
    try:
        s.start()
    finally:
        result = s.close()
    assert result["ready"] and result["cleanup_complete"] and result["service_status"] == "cancelled"
    # Listener cannot remain alive after confirmed namespace cleanup.
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
