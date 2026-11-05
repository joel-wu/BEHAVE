"""Declared local wire contract; no remote model invocation or pricing guess."""

from dataclasses import replace
from decimal import Decimal

import pytest

from behave.agent import AttemptJournal, AttemptSpec, run_attempt
from behave.agent.billing import TokenPrices
from behave.agent.model import ModelFailure, ModelReply, ModelRequest, ToolCall
from behave.agent.providers.chat_wire import request_body, response
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.tools import file_tools
from behave.agent.trajectory import trajectory
from behave.agent.workspace import Workspace
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore


def endpoint(**changes):
    return replace(LocalChatEndpoint("http://127.0.0.1:12345/v1/chat/completions", "fixture", "fixture-runtime/1",
                                    prices=TokenPrices("fixture-prices/1", "fixture", "USD", "2", "0.5", "8")), **changes)


def body(message=None, *, finish="stop", **changes):
    return {"id": "response-1", "model": "fixture", "choices": [{"index": 0, "finish_reason": finish,
             "message": message or {"role": "assistant", "content": "done"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                      "prompt_tokens_details": {"cached_tokens": 4},
                      "completion_tokens_details": {"reasoning_tokens": 3}}, **changes}


def tool(arguments, *, identity="tool-1", name="write_file"):
    return {"id": identity, "type": "function", "function": {"name": name, "arguments": arguments}}


def request(ep, messages=(), tools=()):
    return ModelRequest("test:0:0", 0, 0, messages, tools, ep.model_policy(), 10)


def test_explicit_sampling_controls_reach_local_server_without_inserting_defaults():
    sampling = {"temperature": 1, "top_p": 1, "top_k": -1, "min_p": 0,
                "repetition_penalty": 1, "presence_penalty": 0, "frequency_penalty": 0}
    ep = endpoint(parameters=sampling)
    assert LocalChatEndpoint.from_dict(ep.as_dict()) == ep
    packet = parse_json(request_body(ep, request(ep)))
    assert {name: packet[name] for name in sampling} == sampling
    plain = endpoint()
    assert not set(sampling) & parse_json(request_body(plain, request(plain))).keys()


@pytest.mark.parametrize("name", ["min_p", "repetition_penalty", "presence_penalty", "frequency_penalty"])
@pytest.mark.parametrize("value", [None, True, "0.5", [], {}])
def test_numeric_sampling_extensions_reject_non_numeric_configuration(name, value):
    raw = endpoint().as_dict()
    raw["parameters"] = {name: value}
    with pytest.raises(ValueError, match=name):
        LocalChatEndpoint.from_dict(raw)
    with pytest.raises(ValueError, match=name):
        endpoint(parameters={name: value})


@pytest.mark.parametrize("sampling", [
    {"min_p": 0, "repetition_penalty": 1, "presence_penalty": 0, "frequency_penalty": 0},
    {"min_p": 0.1, "repetition_penalty": 1.1, "presence_penalty": -0.25, "frequency_penalty": 0.75},
])
def test_sampling_extensions_retain_exact_number_types_and_wire_bytes(sampling):
    ep = endpoint(parameters=sampling)
    restored = LocalChatEndpoint.from_dict(ep.as_dict())
    assert canonical_json(restored.as_dict()) == canonical_json(ep.as_dict())
    packet = parse_json(request_body(restored, request(restored)))
    assert canonical_json({name: packet[name] for name in sampling}) == canonical_json(sampling)
    assert {name: type(packet[name]) for name in sampling} == {name: type(value) for name, value in sampling.items()}


def test_model_specific_reasoning_effort_stays_in_template_keywords():
    ep = endpoint(parameters={"chat_template_kwargs": {
        "enable_thinking": True, "reasoning_effort": "xhigh"}})
    packet = parse_json(request_body(ep, request(ep)))
    assert "reasoning_effort" not in packet
    assert packet["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "xhigh"}
    assert LocalChatEndpoint.from_dict(ep.as_dict()) == ep


def test_attempt_affinity_is_stable_across_turns_retries_and_separate_across_policies():
    ep = endpoint(routing="attempt")
    assert ep.as_dict()["schema"] == "behave.local-chat/7"
    assert LocalChatEndpoint.from_dict(ep.as_dict()) == ep
    first = request(ep)
    key = ep.headers(first)
    assert list(key) == ["X-SMG-Routing-Key"] and len(next(iter(key.values()))) == 64
    assert ep.headers(replace(first, call_id="test:3:2", turn=3, dispatch=2)) == key
    assert ep.headers(replace(first, call_id="other:0:0")) != key
    assert replace(ep, runtime_revision="new-weights").headers(first) != key
    assert endpoint().headers(first) == {}
    assert endpoint().as_dict()["schema"] == "behave.local-chat/5"
    with pytest.raises(ValueError, match="canonical"):
        ep.headers(replace(first, call_id="test:3:2"))
    with pytest.raises(ValueError, match="canonical"):
        ep.headers(replace(first, call_id=":0:0"))
    with pytest.raises(ValueError):
        endpoint(routing="random")


@pytest.mark.parametrize("failed", [False, True])
def test_worker_records_actual_destination_without_changing_raw_reply(monkeypatch, failed):
    from behave.agent.providers import chat_worker
    from behave.agent.providers.http_transport import HttpResponse

    ep = endpoint(routing="attempt", worker_urls=(
        "http://127.0.0.1:12345/v1/chat/completions",
        "http://127.0.0.1:12346/v1/chat/completions",
    ))
    req = request(ep)
    raw = b"connection evidence" if failed else canonical_json(body())
    observed = []

    def post(url, packet, **kwargs):
        observed.append((url, kwargs["headers"]))
        return (ModelFailure("retryable", "chat-connect-failed", raw=raw) if failed
                else HttpResponse(200, raw, None))

    monkeypatch.setattr(chat_worker, "post_json", post)
    reply = chat_worker.exchange(ep, req)
    assert reply.raw == raw
    assert reply.usage["routing"] == {
        "url": observed[0][0], "key": observed[0][1]["X-SMG-Routing-Key"],
    }
    assert observed[0][0] == ep.request_url(req)
    if not failed:
        assert reply.usage["normalized"]["cached_input_tokens"] == 4


def test_full_history_reasoning_and_exact_argument_bytes_survive_round_trip():
    ep = endpoint()
    arguments = '{ "path": "hello", "content": "你好" }'
    raw = canonical_json(body({"role": "assistant", "content": None, "reasoning_content": "think\ncarefully",
                               "tool_calls": [tool(arguments)]}, finish="tool_calls"))
    reply = response(ep, 200, raw)
    assert type(reply) is ModelReply and reply.raw == raw
    assert Decimal(reply.charge.amount) == Decimal("0.000054")
    assert reply.usage["normalized"]["reasoning_output_tokens"] == 3
    messages = ({"role": "system", "content": "full system"}, {"role": "user", "content": "full task"},
                reply.message(), {"role": "tool", "call_id": "tool-1", "content": {"written": "hello"}})
    packet = parse_json(request_body(ep, request(ep, messages, tuple(t.declaration() for t in file_tools()))))
    assert len(packet["messages"]) == 4 and packet["stream"] is False and packet["n"] == 1
    assert packet["messages"][2]["reasoning_content"] == "think\ncarefully"
    assert packet["messages"][2]["tool_calls"][0]["function"]["arguments"] == arguments
    assert packet["messages"][3]["content"] == '{"written":"hello"}'
    assert not set(packet["tools"][0]["function"]) & {"revision", "terminal"}
    assert ModelReply.from_dict(reply.as_dict(), raw=raw) == reply


@pytest.mark.parametrize("reasoning", ["reasoning", "reasoning_content"])
@pytest.mark.parametrize("content", [None, "visible text"])
def test_refusal_and_reasoning_preserve_separate_native_fields(reasoning, content):
    ep = endpoint()
    message = {"role": "assistant", "content": content, "refusal": "Cannot comply.",
               reasoning: "retained reasoning"}
    raw = canonical_json(body(message))
    reply = response(ep, 200, raw)
    assert type(reply) is ModelReply and reply.content == (content or "")
    assert reply.continuation == {"refusal": "Cannot comply.", reasoning: "retained reasoning"}
    assert reply.raw == raw and reply.charge is not None
    packet = parse_json(request_body(ep, request(ep, (reply.message(),))))
    assistant = packet["messages"][0]
    assert assistant["content"] == (content or "")
    assert assistant["refusal"] == message["refusal"] and assistant[reasoning] == message[reasoning]
    message["refusal"] = {"not": "text"}
    assert type(response(ep, 200, canonical_json(body(message)))) is ModelFailure


@pytest.mark.parametrize("name", ["function=run_command", "x" * 65, "unknown_tool"])
def test_unknown_model_tool_name_is_feedback_not_an_aborted_rollout(tmp_path, name):
    ep = endpoint()
    arguments = '{"path":"must_not_be_written","content":"not authorized"}'
    raw = canonical_json(body({"role": "assistant", "content": "", "tool_calls": [tool(arguments, name=name)]},
                              finish="tool_calls"))
    first = response(ep, 200, raw)
    assert isinstance(first, ModelReply) and first.calls[0].name == name and first.raw == raw

    class Backend:
        def generate(self, req):
            if req.turn == 0:
                return first
            packet = parse_json(request_body(ep, req))
            assert packet["messages"][-3]["tool_calls"][0]["function"]["name"] == name
            assert parse_json(packet["messages"][-2]["content"].encode()) == {
                "error": "tool-not-available", "name": name}
            return ModelReply("done")

    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    result = run_attempt(journal=journal, spec=AttemptSpec("bad-name", "test", ep.model_policy()), backend=Backend(),
                         initial_messages=({"role": "user", "content": "task"},), tools=file_tools())
    assert result.turns == 2 and result.reason == "stopped"
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert not workspace.snapshot().files


@pytest.mark.parametrize("calls", [[], [tool('{ "path": "x", "content": "ok" }')]])
def test_empty_annotations_are_not_unsupported_output(calls):
    ep = endpoint()
    message = {"role": "assistant", "content": "text", "annotations": [], "tool_calls": calls}
    raw = canonical_json(body(message, finish="tool_calls" if calls else "stop"))
    reply = response(ep, 200, raw)
    assert type(reply) is ModelReply and reply.content == "text" and reply.raw == raw
    assert reply.charge is not None
    messages = (reply.message(), *({"role": "tool", "call_id": c.call_id, "content": {}} for c in reply.calls))
    outgoing = parse_json(request_body(ep, request(ep, messages)))
    assert outgoing["messages"][0]["content"] == "text"
    if calls:
        assert outgoing["messages"][0]["tool_calls"][0]["function"]["arguments"] == calls[0]["function"]["arguments"]


@pytest.mark.parametrize("arguments", ["{", "[]", "null", '{"a":1,"a":2}', '{"a":NaN}'])
def test_malformed_arguments_are_repairable_turns_and_do_not_drop_earlier_training(tmp_path, arguments):
    ep = endpoint()
    replies = [response(ep, 200, canonical_json(body({"role": "assistant", "content": "",
                "tool_calls": [tool(arguments)]}, finish="tool_calls"))),
               ModelReply("", (ToolCall("fixed", "write_file", {"path": "x", "content": "ok"}),)),
               ModelReply("done")]

    class Backend:
        def generate(self, req):
            if req.turn == 1:
                wire = parse_json(request_body(ep, req))
                assert wire["messages"][-2]["role"] == "tool"
                assert "tool-arguments-invalid-json-object" in wire["messages"][-2]["content"]
                assert wire["messages"][-3]["tool_calls"][0]["function"]["arguments"] == arguments
            return replies[req.turn]

    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    result = run_attempt(journal=journal, spec=AttemptSpec("repair", "test", ep.model_policy()), backend=Backend(),
                         initial_messages=({"role": "user", "content": "task"},), tools=file_tools())
    assert result.reason == "stopped" and result.turns == 3
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert workspace.read("x") == b"ok"
    assert len(trajectory(journal).turns) == 3


@pytest.mark.parametrize("calls", [[], [tool('{"path":"forbidden","content":"never"}')]])
def test_truncated_turn_is_excluded_not_a_submission_or_stop_and_no_tool_effect(tmp_path, calls):
    ep = endpoint()
    cut = response(ep, 200, canonical_json(body({"role": "assistant", "content": "cut", "tool_calls": calls}, finish="length")))

    class Backend:
        def generate(self, req):
            if req.turn == 1:
                # The next request must remain a complete valid wire history.
                request_body(ep, req)
                return cut
            return ModelReply("", (ToolCall("list", "list_files", {}),), token_data={"ids": [1]}) if req.turn == 0 else ModelReply("done")

    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    result = run_attempt(journal=journal, spec=AttemptSpec("cut", "test", ep.model_policy()), backend=Backend(),
                         initial_messages=(), tools=file_tools())
    assert result.turns == 3
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert not workspace.snapshot().files
    view = trajectory(journal)
    assert view.turns[0].training_status == "recorded"
    assert view.turns[1].exclusion is None and view.turns[2].exclusion is None
    assert view.charges


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_unparsed_tool_text_remains_text_without_executable_calls(finish):
    text = "<tool_call><function<function_results></function></tool_call>"
    raw = canonical_json(body({"role": "assistant", "content": text,
                               "tool_calls": []}, finish=finish))
    got = response(endpoint(), 200, raw)
    assert type(got) is ModelReply
    assert got.content == text and got.calls == () and got.finish_reason == finish
    assert got.raw == raw and got.usage["normalized"]["output_tokens"] == 5


def test_tool_finish_requires_an_actual_call():
    raw = canonical_json(body({"role": "assistant", "content": "<tool_call>",
                               "tool_calls": []}, finish="tool_calls"))
    got = response(endpoint(), 200, raw)
    assert type(got) is ModelFailure and got.code == "chat-response-invalid"
    assert got.raw == raw


@pytest.mark.parametrize("change", [
    {"choices": []}, {"choices": [{"index": 1, "finish_reason": "stop", "message": {}}]},
    {"choices": [{"index": 0, "finish_reason": "unexpected", "message": {}}]},
    {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [tool("{}"), tool("{}")], "content": ""}}]},
])
def test_invalid_provider_structure_preserves_usage_but_does_not_fake_stop(change):
    got = response(endpoint(), 200, canonical_json(body(**change)))
    assert type(got) is ModelFailure and got.kind == "unknown"
    assert got.charge is not None and got.usage["normalized"]["input_tokens"] == 10


def test_usage_absence_and_invalid_cache_are_unknown_cost_not_zero():
    for raw in (None, {}, {"prompt_tokens": 1, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 3}}):
        got = response(endpoint(), 200, canonical_json(body(usage=raw)))
        assert type(got) is ModelReply and got.charge is None
        assert got.usage["normalization_error"] == "usage-missing-or-invalid"
    got = response(endpoint(), 200, canonical_json(body(usage={"prompt_tokens": 1, "completion_tokens": 2})))
    assert got.charge is None and got.usage["normalized"]["cached_input_tokens"] is None
    wrong = response(endpoint(), 200, canonical_json(body(model="another-model")))
    assert type(wrong) is ModelFailure and wrong.charge is None


def test_only_conclusive_rejections_are_retried_no_timeout_or_5xx_replay():
    raw = canonical_json({"error": {"code": "busy"}})
    rate = response(endpoint(), 429, raw, retry_after="900")
    assert rate.kind == "retryable" and rate.retry_after_seconds == 900
    assert response(endpoint(), 500, raw).kind == "unknown"
    assert response(endpoint(), 408, raw).kind == "unknown"
    assert response(endpoint(), 401, raw).kind == "permanent"
    assert response(endpoint(), 400, canonical_json({"error": {"code": "context_length_exceeded"}})).kind == "context_limit"


@pytest.mark.parametrize("url", ["http://localhost:123/v1/chat/completions", "http://10.1.2.3:123/v1/chat/completions",
                                "https://127.0.0.1:123/v1/chat/completions", "http://x:y@127.0.0.1:123/v1/chat/completions",
                                "http://127.0.0.1:123/v1/chat/completions?secret=x"])
def test_endpoint_never_discovers_credentials_or_routes_to_arbitrary_remote_hosts(url):
    with pytest.raises(ValueError):
        endpoint(url=url)


def test_policy_is_hash_bound_closed_and_round_trips():
    ep = endpoint()
    assert LocalChatEndpoint.from_dict(ep.as_dict()) == ep
    assert ep.command().identity == ep.model_policy()["transport"]
    assert replace(ep, runtime_revision="new-runtime").identity != ep.identity
    for parameters in ({"model": "override", "max_tokens": 1}, {"max_tokens": True}, {"max_tokens": 3, "stream": True}):
        with pytest.raises(ValueError):
            endpoint(parameters=parameters)
    with pytest.raises(ValueError):
        request_body(ep, replace(request(ep), model_policy={"model": "fixture", "provider": "wrong"}))
    with pytest.raises(ValueError):
        request_body(ep, request(ep, ({"role": "tool", "call_id": "ghost", "content": {}},)))


def test_local_output_cap_is_optional_and_thinking_settings_reach_the_wire():
    ep = endpoint()
    assert "max_tokens" not in parse_json(request_body(ep, request(ep)))
    parameters = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "reasoning_effort": "xhigh",
                  "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True}}
    ep = endpoint(parameters=parameters)
    assert LocalChatEndpoint.from_dict(ep.as_dict()) == ep
    packet = parse_json(request_body(ep, request(ep)))
    assert "max_tokens" not in packet
    assert all(packet[name] == value for name, value in parameters.items())
    capped = endpoint(parameters={"max_tokens": 512})
    assert parse_json(request_body(capped, request(capped)))["max_tokens"] == 512
