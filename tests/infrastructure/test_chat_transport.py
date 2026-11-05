"""Actual HTTP and worker namespaces, using only local controlled endpoints."""

import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from behave.agent import AttemptInterrupted, AttemptJournal, AttemptSpec, run_attempt
from behave.agent.billing import TokenPrices
from behave.agent.model import ModelRequest
from behave.agent.process_model import ProcessModelBackend
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.trajectory import trajectory
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.task_execution.capture import load_submission
from behave.task_execution.solver import SolverJob, run_solver_process


@contextmanager
def server(reply, *, token_count=10):
    observed = []
    stopped = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            packet = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/tokenize" and token_count is not None:
                # Most fixtures count generation requests, not the separate
                # non-generating preflight. Custom counting tests opt out.
                send(self, {"count": token_count})
                return
            observed.append((self.path, dict(self.headers), parse_json(packet)))
            reply(self, observed, stopped)

        def log_message(self, *args):
            pass

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=endpoint.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{endpoint.server_port}/v1/chat/completions", observed
    finally:
        stopped.set()
        endpoint.shutdown()
        endpoint.server_close()
        worker.join(timeout=5)


def send(handler, body, status=200, headers=None):
    raw = canonical_json(body)
    handler.send_response(status)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def reply(calls=()):
    return {"model": "fixture", "choices": [{"index": 0, "finish_reason": "tool_calls" if calls else "stop",
            "message": {"role": "assistant", "content": "", "tool_calls": list(calls)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def call(identity, name, arguments):
    return {"id": identity, "type": "function", "function": {"name": name,
            "arguments": canonical_json(arguments).decode("utf-8")}}


def config(url):
    return LocalChatEndpoint(url, "fixture", "test-server/1",
                             prices=TokenPrices("test/1", "fixture", "USD", "2", "2", "8"))


@pytest.mark.isolation
def test_real_local_http_is_used_by_supervised_solver_and_replay_never_calls_server(tmp_path, publication, monkeypatch):
    def answer(handler, requests, stopped):
        if len(requests) == 1:
            calls = [call("b", "run_command", {"command": "printf 'class M: pass\\n' > agent.py"}),
                     call("r", "run_command", {"command": "printf 'module top; endmodule\\n' > top.sv"})]
        else:
            calls = [call("s", "submit", {
                     "behavior": {"files": ["agent.py"], "entry_file": "agent.py", "entry_symbol": "M"},
                     "rtl": {"files": ["top.sv"], "entry_file": "top.sv", "entry_symbol": "top"},
                     "testbench": None})]
        send(handler, reply(calls))

    # No ambient proxy or provider key is forwarded to the fixed broker.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_CREDENTIAL_NOT_FORWARDED")
    with server(answer) as (url, observed):
        endpoint = replace(config(url), routing="attempt")
        public = load_published_task(publication.reader, publication.ref, "fixture_counter").public
        job = SolverJob(public, AttemptSpec("http", "solver", endpoint.model_policy(),
                                           request_timeout_seconds=10), endpoint.command())
        store = DirectoryStore(tmp_path / "objects")
        journal, supervisor = AttemptJournal(tmp_path / "events", store), AttemptJournal(tmp_path / "supervisor", store)
        result = run_solver_process(job, journal=journal, supervision=supervisor)
        assert result.status == "awaiting_judgment" and result.score is None
        assert load_submission(store, result.submission).behavior.artifact.entry_symbol == "M"
        assert len(observed) == 2
        messages = observed[1][2]["messages"]
        assert [m["role"] for m in messages] == ["system", "user", "user", "assistant", "tool", "tool", "user"]
        assert "turn 1 of 60" in messages[2]["content"]
        assert "turn 2 of 60" in messages[-1]["content"]
        assert all("Authorization" not in headers for _, headers, _ in observed)
        assert observed[0][1]["X-SMG-Routing-Key"] == observed[1][1]["X-SMG-Routing-Key"]
        assert b"TEST_CREDENTIAL_NOT_FORWARDED" not in job.to_bytes()
        assert b"PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE" not in canonical_json(observed[0][2])
        assert trajectory(journal).cost_by_currency() == {"USD": "0.00012"}
        assert run_solver_process(job, journal=journal, supervision=supervisor) == result
        assert len(observed) == 2


@pytest.mark.isolation
def test_native_refusal_is_no_submission_not_infrastructure_failure(tmp_path, publication):
    def answer(handler, requests, stopped):
        packet = reply()
        packet["choices"][0]["message"].update(content=None, refusal="Cannot complete this task.", annotations=[])
        send(handler, packet)

    with server(answer) as (url, observed):
        endpoint = config(url)
        public = load_published_task(publication.reader, publication.ref, "fixture_counter").public
        job = SolverJob(public, AttemptSpec("refusal", "solver", endpoint.model_policy(),
                                           request_timeout_seconds=10), endpoint.command())
        store = DirectoryStore(tmp_path / "objects")
        journal, supervisor = AttemptJournal(tmp_path / "events", store), AttemptJournal(tmp_path / "supervisor", store)
        result = run_solver_process(job, journal=journal, supervision=supervisor)
        assert result.status == "no_submission" and result.score == 0
        view = trajectory(journal)
        assert len(view.turns) == 1 and view.charges
        assert view.turns[0].exclusion is None
        assert run_solver_process(job, journal=journal, supervision=supervisor) == result
        assert len(observed) == 1


@pytest.mark.isolation
def test_native_context_rejection_keeps_prior_turn_and_cold_replay(tmp_path, publication):
    def answer(handler, requests, stopped):
        if len(requests) == 1:
            send(handler, reply([call("b", "run_command", {"command": "printf 'class M: pass\\n' > agent.py"})]))
        else:
            send(handler, {"object": "error", "type": "BadRequestError", "code": 400,
                "message": "The input (8192 tokens) is longer than the model's context length (8192 tokens)."}, 400)

    with server(answer) as (url, observed):
        endpoint = config(url)
        public = load_published_task(publication.reader, publication.ref, "fixture_counter").public
        job = SolverJob(public, AttemptSpec("context", "solver", endpoint.model_policy(),
                                           request_timeout_seconds=10), endpoint.command())
        store = DirectoryStore(tmp_path / "objects")
        journal, supervisor = AttemptJournal(tmp_path / "events", store), AttemptJournal(tmp_path / "supervisor", store)
        result = run_solver_process(job, journal=journal, supervision=supervisor)
        assert result.status == "no_submission" and result.score == 0
        assert journal.load()[-1].payload["reason"] == "context_limit"
        assert len(trajectory(journal).turns) == 1 and len(observed) == 2
        assert run_solver_process(job, journal=journal, supervision=supervisor) == result
        assert len(observed) == 2


@pytest.mark.isolation
def test_rate_rejection_retries_with_same_turn_and_full_context(tmp_path):
    def answer(handler, requests, stopped):
        if len(requests) == 1:
            send(handler, {"error": {"code": "busy"}}, 429, {"Retry-After": "1"})
        else:
            send(handler, reply())

    with server(answer) as (url, observed):
        endpoint = replace(config(url), routing="attempt")
        journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
        spec = AttemptSpec("retry", "test", endpoint.model_policy(), request_timeout_seconds=10)
        kwargs = {"journal": journal, "spec": spec, "backend": ProcessModelBackend(endpoint.command()),
                  "initial_messages": ({"role": "user", "content": "never prune this"},)}
        result = run_attempt(**kwargs)
        assert result.turns == 1 and len(observed) == 2
        assert observed[0][2] == observed[1][2]
        assert observed[0][1]["X-SMG-Routing-Key"] == observed[1][1]["X-SMG-Routing-Key"]
        assert sum(event.kind == "model_backoff" for event in journal.load()) == 1
        assert trajectory(journal).unpriced_dispatches == 1
        assert run_attempt(**kwargs) == result and len(observed) == 2


@pytest.mark.isolation
@pytest.mark.parametrize("failure", ["trickle", "redirect", "invalid-response", "disconnect", "server-error"])
def test_http_failures_are_bounded_unknown_and_not_automatically_repeated(tmp_path, failure):
    def answer(handler, requests, stopped):
        if failure == "trickle":
            handler.send_response(200)
            handler.end_headers()
            try:
                while not stopped.wait(.025):
                    handler.wfile.write(b" ")
                    handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif failure == "redirect":
            send(handler, {"error": {"code": "moved"}}, 307, {"Location": "http://127.0.0.1:1/secret"})
        elif failure == "invalid-response":
            # Size is allowed. The missing choices, not a byte quota, rejects it.
            send(handler, {"model": "fixture", "data": "x" * (2 << 20)})
        elif failure == "server-error":
            send(handler, {"error": {"code": "internal"}}, 500)
        # Returning without a response models disconnect after receiving the POST.

    with server(answer) as (url, observed):
        endpoint = config(url)
        journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
        # The process budget includes namespace setup and Python imports. Two
        # seconds can expire before any POST under parallel load, which tests
        # startup timeout instead of the intended HTTP failure. Keep a finite
        # startup allowance and require the exact failure mechanism below.
        budget = 30
        kwargs = {"journal": journal, "spec": AttemptSpec("bad", "test", endpoint.model_policy(),
                                                         request_timeout_seconds=budget),
                  "backend": ProcessModelBackend(endpoint.command()), "initial_messages": ()}
        began = time.monotonic()
        with pytest.raises(AttemptInterrupted, match="model-unknown"):
            run_attempt(**kwargs)
        assert time.monotonic() - began < budget + 10
        settled = [e for e in journal.load() if e.kind == "model_settled"]
        assert len(settled) == 1
        result = parse_json(journal.objects.read(BlobRef.from_dict(dict(settled[0].payload["record"]))))
        assert result["code"] == {"trickle": "model-worker-timeout", "redirect": "chat-http-307",
                                  "invalid-response": "chat-response-invalid", "disconnect": "chat-exchange-interrupted",
                                  "server-error": "chat-http-500"}[failure]
        with pytest.raises(AttemptInterrupted, match="model-unknown"):
            run_attempt(**kwargs)
        assert len(observed) == 1 and not any(e.kind == "terminal" for e in journal.load())


@pytest.mark.isolation
def test_public_policy_cannot_switch_endpoints_or_override_model(tmp_path):
    def answer(*args):
        pytest.fail("policy mismatch must not reach HTTP")

    with server(answer) as (url, observed):
        endpoint = config(url)
        req = ModelRequest("bad:0:0", 0, 0, (), (), {**endpoint.model_policy(), "model": "another"}, 5)
        got = ProcessModelBackend(endpoint.command()).generate(req)
        assert got.kind == "permanent" and got.code == "chat-request-invalid"
        assert not observed
