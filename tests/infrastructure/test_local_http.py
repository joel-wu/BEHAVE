"""Real loopback HTTP through the isolated broker; no paid API or GPU model."""

import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from behave.agent import AttemptInterrupted, AttemptJournal, AttemptSpec, run_attempt
from behave.agent.model import ModelRequest
from behave.agent.process_model import ProcessModelBackend
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.tools import file_tools
from behave.agent.trajectory import trajectory
from behave.agent.workspace import Workspace
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.serving.session import probe


def reply(model, *, call=False, content="accepted"):
    message = {"role": "assistant", "content": "", "reasoning": "preserve this"}
    if call:
        message["tool_calls"] = [{"id": "write", "type": "function", "function": {
            "name": "write_file", "arguments": canonical_json({"path": "result", "content": content}).decode()}}]
    return canonical_json({"model": model, "choices": [{"index": 0, "message": message,
        "finish_reason": "tool_calls" if call else "stop"}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 5}})


@contextmanager
def fixture_peer(mode):
    stopped = threading.Event()
    requests = []
    model = "loopback-fixture"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_body(self, raw, *, incomplete=False):
            self.send_response(200)
            if mode == "disconnect-chunked" and incomplete:
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
            else:
                self.send_header("Content-Length", str(len(raw) + (20 if incomplete else 0)))
                self.end_headers()
                self.wfile.write(raw)
            self.wfile.flush()
            self.close_connection = True

        def do_GET(self):
            requests.append({"path": self.path})
            self.send_body(canonical_json({"object": "list", "data": [{"id": model}]}),
                           incomplete=mode != "complete")

        def do_POST(self):
            packet = self.rfile.read(int(self.headers["Content-Length"]))
            assert self.headers.get("Authorization") is None
            if self.path == "/tokenize":
                # Counting is a separate, non-generating request. These
                # fixtures exercise interruption of the following generation.
                self.send_body(canonical_json({"count": 9}))
                return
            requests.append(parse_json(packet))
            assert self.path == "/v1/chat/completions"
            try:
                if mode in {"slow-header", "slow-body"}:
                    if mode == "slow-header":
                        self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Trickle: ")
                    else:
                        self.send_response(200)
                        self.send_header("Content-Length", "100000")
                        self.end_headers()
                    while not stopped.wait(.03):
                        self.connection.sendall(b"x")
                    return
                if len(requests) == 1:
                    # Even a syntactically complete JSON body cannot commit if
                    # the HTTP message is incomplete. Its apparent action is bait.
                    raw = reply(model, call=True, content="must-not-execute")
                    self.send_body(raw, incomplete=True)
                    return
                raw = reply(model, call=len(requests) == 2)
                self.send_body(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The broker's absolute deadline deliberately closes this connection.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    owner = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    owner.start()
    try:
        yield LocalChatEndpoint(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", model, "fixture/1"), requests
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        owner.join(timeout=2)
        assert not owner.is_alive()


@pytest.mark.isolation
@pytest.mark.parametrize("mode", ["disconnect", "disconnect-chunked"])
def test_disconnect_retry_preserves_history_uncertainty_and_exactly_one_tool_effect(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("HTTP_PROXY", "http://must-not-be-used.invalid:1")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-must-not-enter-local-request")
    with fixture_peer(mode) as (endpoint, packets):
        journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
        kwargs = {"journal": journal, "backend": ProcessModelBackend(endpoint.command()),
                  "spec": AttemptSpec("http", "fixture", endpoint.model_policy(), retry_uncertain_generation=True,
                                       request_timeout_seconds=3),
                  "initial_messages": ({"role": "user", "content": "write then stop"},), "tools": file_tools()}
        result = run_attempt(**kwargs)
        assert result.reason == "stopped" and result.turns == 2
        assert len(packets) == 3 and packets[0] == packets[1]
        assert all(packet["max_tokens"] == 131072 - 9 for packet in packets)
        assert packets[2]["messages"][2]["reasoning"] == "preserve this"
        assert packets[2]["messages"][-2]["role"] == "tool"
        assert "Runtime status: turn 2" in packets[2]["messages"][-1]["content"]
        assert packets[2]["messages"][:len(packets[1]["messages"])] == packets[1]["messages"]
        workspace = Workspace(journal.objects)
        workspace.restore(result.workspace)
        assert workspace.read("result") == b"accepted"
        assert sum(event.kind == "tool_requested" for event in journal.load()) == 1
        view = trajectory(journal)
        assert view.unresolved_dispatches == 1 and view.unpriced_dispatches == 3
        assert run_attempt(**kwargs) == result and len(packets) == 3


@pytest.mark.isolation
@pytest.mark.parametrize("mode", ["disconnect", "disconnect-chunked"])
def test_disconnect_stops_without_explicit_uncertain_retry(tmp_path, mode):
    with fixture_peer(mode) as (endpoint, packets):
        journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
        with pytest.raises(AttemptInterrupted, match="chat-exchange-interrupted"):
            run_attempt(journal=journal, backend=ProcessModelBackend(endpoint.command()),
                        spec=AttemptSpec("http", "fixture", endpoint.model_policy(), request_timeout_seconds=3),
                        initial_messages=({"role": "user", "content": "go"},), tools=file_tools())
        assert len(packets) == 1
        assert not any(e.kind == "tool_requested" for e in journal.load())


@pytest.mark.isolation
@pytest.mark.parametrize("mode", ["complete", "disconnect", "disconnect-chunked"])
def test_readiness_requires_the_same_complete_http_body(tmp_path, mode):
    from urllib.parse import urlsplit
    with fixture_peer(mode) as (endpoint, packets):
        raw = probe(SimpleNamespace(port=urlsplit(endpoint.url).port, cleanup_seconds=None), scratch=tmp_path,
                    deadline=time.monotonic() + 3)
        assert (raw is not None) == (mode == "complete")
        assert packets == [{"path": "/v1/models"}]


@pytest.mark.isolation
@pytest.mark.parametrize("mode", ["slow-header", "slow-body"])
def test_http_trickle_does_not_extend_the_broker_absolute_deadline(mode):
    with fixture_peer(mode) as (endpoint, packets):
        # Include cold isolated-worker startup under the parallel test lane.
        # The peer never finishes: this still requires the absolute deadline,
        # not an idle-read timeout that trickling bytes could keep extending.
        request = ModelRequest("http:0:0", 0, 0, ({"role": "user", "content": "go"},), (),
                               endpoint.model_policy(), 5)
        began = time.monotonic()
        outcome = ProcessModelBackend(endpoint.command()).generate(request)
        assert len(packets) == 1
        assert outcome.kind == "unknown" and outcome.code == "model-worker-timeout"
        assert time.monotonic() - began < 12  # Includes the separate, bounded cleanup grace.
