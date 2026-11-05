"""Production wire/broker paths against fixtures; no provider key or paid call."""

from dataclasses import replace
from decimal import Decimal

import pytest

from behave.agent import AttemptJournal, AttemptSpec, run_attempt
from behave.agent.billing import TokenPrices, TokenUsage
from behave.agent.model import ModelFailure, ModelReply, ModelRequest
from behave.agent.process_model import ProcessModelBackend, decode_model_outcome, encode_model_outcome
from behave.agent.prompting import render
from behave.agent.providers.credentials import CredentialRef, Secret, load_credential
from behave.agent.providers.hosted import HostedEndpoint
from behave.agent.providers.hosted_worker import exchange, redact_reflection, request_body, response
from behave.agent.providers.http_transport import HttpResponse
from behave.agent.tools import file_tools
from behave.agent.trajectory import trajectory
from behave.agent.workspace import Workspace
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore


def endpoint(provider="openai", **changes):
    return replace(HostedEndpoint(provider, "fixture", "fixture/1",
                                  CredentialRef("/unavailable/private.env", "FIXTURE_KEY", "fixture/1"),
                                  parameters={"max_output_tokens": 4096} if provider == "anthropic" else {},
                                  prices=TokenPrices("test/1", "fixture", "USD", "2", "0.5", "8")), **changes)


def request(ep, messages=()):
    return ModelRequest("test:0:0", 0, 0, messages, tuple(t.declaration() for t in file_tools()), ep.model_policy(), 10)


def test_openai_strict_tools_preserve_eval_schemas(publication):
    from behave.agent.commands import shell_commands
    from behave.core.records import thaw_json
    from behave.data.published import load_published_task
    from behave.task_execution.capture import submission_tool

    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    tools = (shell_commands().tool(), submission_tool(public))
    declarations = tuple(tool.declaration() for tool in tools)
    ep = endpoint()
    packet = parse_json(request_body(ep, replace(request(ep), tools=declarations)))

    def check_objects(schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                assert schema["additionalProperties"] is False
                assert set(schema["required"]) == set(schema["properties"])
            for value in schema.values():
                check_objects(value)
        elif isinstance(schema, list):
            for value in schema:
                check_objects(value)

    for tool, declaration in zip(packet["tools"], declarations, strict=True):
        assert tool["strict"] is True
        assert tool["parameters"] == thaw_json(declaration["parameters"])
        check_objects(tool["parameters"])
    assert set(packet["tools"][0]["parameters"]["properties"]) == {"command"}
    # No new requirement to emit a tool instead of ordinary text.
    assert "tool_choice" not in packet


def test_openai_cache_writes_are_disjoint_and_require_an_explicit_price():
    ep = endpoint(prices=TokenPrices("luna-test", "fixture", "USD", ".20", ".02", "1.20",
                                    cache_write_per_million=".25"))
    data = body("openai", call=False)
    data["usage"]["input_tokens_details"]["cache_write_tokens"] = 5
    got = response(ep, 200, canonical_json(data))
    assert got.usage["normalized"]["cache_write_tokens"] == 5
    # 1 uncached + 4 cached + 5 written + 5 output, not 6 uncached + writes.
    assert Decimal(got.charge.amount) == Decimal(".00000753")
    assert Decimal(ep.prices.reservation(10, 5).amount) == Decimal(".0000085")
    missing = replace(ep, prices=replace(ep.prices, cache_write_per_million=None))
    assert response(missing, 200, canonical_json(data)).charge is None
    data["usage"]["input_tokens_details"]["cache_write_tokens"] = 7
    invalid = response(ep, 200, canonical_json(data))
    assert invalid.usage["normalization_error"] == "usage-missing-or-invalid"
    assert invalid.charge is None


def body(provider, *, call=True, truncated=False):
    args = {"path": "written", "content": "ok"}
    if provider == "openai":
        return {"id": "resp-test", "model": "fixture", "status": "incomplete" if truncated else "completed",
                "incomplete_details": {"reason": "max_output_tokens"} if truncated else None,
                "output": [{"type": "reasoning", "id": "reason", "summary": [], "encrypted_content": "opaque"},
                           {"type": "message", "id": "message", "role": "assistant", "phase": "commentary",
                            "content": [{"type": "output_text", "text": "thinking done", "annotations": []}]}]
                          + ([{"type": "function_call", "id": "fc", "call_id": "call", "name": "write_file",
                               "arguments": canonical_json(args).decode()}] if call else []),
                "usage": {"input_tokens": 10, "output_tokens": 5, "input_tokens_details": {"cached_tokens": 4}}}
    if provider == "anthropic":
        return {"id": "msg-test", "model": "fixture", "type": "message", "role": "assistant",
                "stop_reason": "max_tokens" if truncated else "tool_use" if call else "end_turn",
                "content": [{"type": "thinking", "thinking": "private reasoning", "signature": "signature"},
                            {"type": "redacted_thinking", "data": "opaque"},
                            {"type": "text", "text": "thinking done"}]
                           + ([{"type": "tool_use", "id": "call", "name": "write_file", "input": args}] if call else []),
                "usage": {"input_tokens": 6, "output_tokens": 5, "cache_read_input_tokens": 4,
                          "cache_creation_input_tokens": 0}}
    return {"id": "chat-test", "model": "fixture", "choices": [{"index": 0,
            "finish_reason": "length" if truncated else "tool_calls" if call else "stop",
            "message": {"role": "assistant", "content": "thinking done", "reasoning_content": "keep",
                        "tool_calls": [{"type": "function", "id": "call", "function": {
                            "name": "write_file", "arguments": canonical_json(args).decode()}}] if call else []}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 4}}}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
def test_native_multiturn_history_and_one_tool_execution_survive_recovery(tmp_path, provider):
    ep = endpoint(provider)
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    packets = []

    class Backend:
        def generate(self, req):
            packets.append(parse_json(request_body(ep, req)))
            return response(ep, 200, canonical_json(body(provider, call=req.turn == 0)))

    kwargs = {"journal": journal, "spec": AttemptSpec("test", "solver", ep.model_policy()), "backend": Backend(),
              "initial_messages": ({"role": "system", "content": "system"}, {"role": "user", "content": "task"}),
              "tools": file_tools()}
    result = run_attempt(**kwargs)
    assert result.turns == 2 and run_attempt(**kwargs) == result and len(packets) == 2
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert workspace.read("written") == b"ok"
    assert len(trajectory(journal).charges) == 2
    packet = packets[1]
    key = "input" if provider == "openai" else "messages"
    assert packet[key][:len(packets[0][key])] == packets[0][key]
    if provider == "openai":
        assert packet["store"] is False and "previous_response_id" not in packet
        assert packet["input"][3:6] == body(provider)["output"]
        assert packet["input"][-2]["type"] == "function_call_output"
        assert "Runtime status: turn 2" in packet["input"][-1]["content"]
    elif provider == "anthropic":
        assert all(p["cache_control"] == {"type": "ephemeral"} for p in packets)
        assert packet["messages"][1]["content"] == body(provider)["content"]
        assert packet["messages"][-1]["content"][0]["type"] == "tool_result"
    else:
        assert packet["messages"][3]["reasoning_content"] == "keep"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
def test_parallel_tool_history_matches_ids_without_dropping_or_reordering_results(provider):
    from copy import deepcopy

    ep = endpoint(provider)
    data = body(provider)
    if provider == "openai":
        calls, identity = data["output"], "call_id"
    elif provider == "anthropic":
        calls, identity = data["content"], "id"
    else:
        calls, identity = data["choices"][0]["message"]["tool_calls"], "id"
    second = deepcopy(calls[-1])
    second[identity] = "second"
    if provider == "openai":
        second["id"] = "fc-second"
    calls.append(second)
    reply = response(ep, 200, canonical_json(data))
    assert type(reply) is ModelReply and len(reply.calls) == 2
    prefix = ({"role": "user", "content": "task"}, reply.message())
    results = ({"role": "tool", "call_id": "second", "content": {"result": 2}},
               {"role": "tool", "call_id": "call", "content": {"result": 1}})
    packet = parse_json(request_body(ep, request(ep, (*prefix, *results))))
    if provider == "openai":
        assert packet["input"][1:-2] == data["output"]
        assert [item["call_id"] for item in packet["input"][-2:]] == ["second", "call"]
    elif provider == "anthropic":
        assert packet["messages"][1]["content"] == data["content"]
        assert [item["tool_use_id"] for item in packet["messages"][-1]["content"]] == ["second", "call"]
    else:
        assert [item["tool_call_id"] for item in packet["messages"][-2:]] == ["second", "call"]
    for broken in (results[:1], (results[0], results[0]),
                   (results[0], {**results[1], "call_id": "unknown"}),
                   (results[0], {"role": "user", "content": "premature next turn"}, results[1])):
        with pytest.raises(ValueError):
            request_body(ep, request(ep, (*prefix, *broken)))


@pytest.mark.parametrize("extra", [None, "write_file", "another_tool"])
def test_fireworks_nullable_alternate_tool_name_is_not_a_second_authority(extra):
    data = body("fireworks")
    call = data["choices"][0]["message"]["tool_calls"][0]
    call.update(index=0, name=extra)
    result = response(endpoint("fireworks"), 200, canonical_json(data))
    assert result.charge is not None  # A decoder failure never erases billing.
    if extra is None:
        assert type(result) is ModelReply and result.calls[0].name == "write_file"
    else:
        assert type(result) is ModelFailure and result.code == "chat-response-invalid"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
@pytest.mark.parametrize("call", [False, True])
def test_truncated_turn_has_no_effect_but_next_turn_survives(tmp_path, provider, call):
    ep = endpoint(provider)
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    workspace = Workspace(journal.objects)
    dispatches = []
    cut = response(ep, 200, canonical_json(body(provider, call=call, truncated=True)))
    assert type(cut) is ModelReply and cut.finish_reason == "length"

    class Backend:
        def generate(self, req):
            request_body(ep, req)
            dispatches.append(req.turn)
            if req.turn == 0:
                return cut
            if req.turn == 1:
                assert not workspace.snapshot().files
                assert req.messages[0] == {"role": "user", "content": "task"}
                assert canonical_json(req.messages[2]) == canonical_json(cut.message())
                if call:
                    assert req.messages[-2]["role"] == "tool"
                    assert req.messages[-2]["content"]["error"] == "response-output-limit"
                else:
                    assert req.messages[-2] == {"role": "user", "content": render("shared/runtime/output_limit")}
            return response(ep, 200, canonical_json(body(provider, call=req.turn == 1)))

    kwargs = {"journal": journal, "backend": Backend(), "spec": AttemptSpec("test", "solver", ep.model_policy()),
              "initial_messages": ({"role": "user", "content": "task"},), "tools": file_tools(), "workspace": workspace}
    result = run_attempt(**kwargs)
    assert result.reason == "stopped" and result.turns == 3
    assert workspace.read("written") == b"ok"
    view = trajectory(journal)
    assert [turn.exclusion for turn in view.turns] == [None, None, None]
    assert len(view.charges) == 3
    recovered = run_attempt(**{**kwargs, "workspace": Workspace(journal.objects)})
    assert recovered == result and dispatches == [0, 1, 2]


def test_anthropic_complete_tool_block_is_not_discarded_for_end_turn():
    ep = endpoint("anthropic")
    data = body("anthropic", call=True)
    data["stop_reason"] = "end_turn"
    raw = canonical_json(data)
    decoded = response(ep, 200, raw)
    assert isinstance(decoded, ModelReply)
    assert decoded.calls and decoded.finish_reason == "tool_calls"
    assert decoded.raw == raw
    assert decoded.charge is not None


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
def test_explicit_refusal_is_a_model_reply_and_normal_no_submission(tmp_path, publication, provider):
    from behave.data.published import load_published_task
    from behave.task_execution.solver import run_solver

    ep = endpoint(provider)
    data = body(provider, call=False)
    refusal = "I cannot complete this task."
    if provider == "openai":
        data["output"][1]["content"] = [{"type": "refusal", "refusal": refusal}]
    elif provider == "anthropic":
        data["stop_reason"] = "refusal"
        data["content"][-1]["text"] = refusal
    else:
        data["choices"][0]["message"].update(content=None, refusal=refusal)
    raw = canonical_json(data)
    decoded = response(ep, 200, raw)
    assert type(decoded) is ModelReply and decoded.finish_reason == "stop"
    assert not decoded.calls and decoded.training_exclusion is None
    assert decoded.charge is not None and decoded.raw == raw
    # A follow-up must retain the provider's refusal field/block, not turn it
    # into a transport error or silently omit it from the native history.
    packet = request_body(ep, request(ep, ({"role": "user", "content": "task"}, decoded.message(),
                                         {"role": "user", "content": "follow up"})))
    assert refusal.encode() in packet
    dispatches = []

    class Backend:
        def generate(self, req):
            dispatches.append(req.call_id)
            return decoded

    task = load_published_task(publication.reader, publication.ref, "fixture_counter").public
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    kwargs = {"task": task, "spec": AttemptSpec("refusal", "solver", ep.model_policy()),
              "backend": Backend(), "journal": journal}
    result = run_solver(**kwargs)
    assert result.status == "no_submission" and result.score == 0
    assert run_solver(**kwargs) == result and len(dispatches) == 1
    assert len(trajectory(journal).turns) == 1 and trajectory(journal).charges


@pytest.mark.parametrize("call", [False, True])
def test_anthropic_context_window_stop_evicts_history_without_executing_partial_tools(tmp_path, call):
    ep = endpoint("anthropic")
    data = body("anthropic", call=call)
    data["stop_reason"] = "model_context_window_exceeded"
    cut = response(ep, 200, canonical_json(data))
    assert type(cut) is ModelReply and cut.finish_reason == "context_limit"
    assert cut.training_exclusion is None and cut.finish_reason == "context_limit" and cut.charge is not None
    assert all(c.argument_error == "response-output-limit" for c in cut.calls)
    req = request(ep)
    assert decode_model_outcome(req, encode_model_outcome(req, cut)) == cut
    # Preserve completed work, but never execute the cut turn's write.
    data = body("anthropic")
    data["content"][-1]["input"] = {"path": "existing", "content": "kept"}
    replies = [response(ep, 200, canonical_json(data)), cut,
               response(ep, 200, canonical_json(body("anthropic", call=False)))]
    dispatches = []

    class Backend:
        def generate(self, req):
            request_body(ep, req)
            dispatches.append(req.turn)
            return replies[req.turn]

    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    kwargs = {"journal": journal, "backend": Backend(), "spec": AttemptSpec("cut", "solver", ep.model_policy()),
              "initial_messages": ({"role": "user", "content": "task"},), "tools": file_tools()}
    result = run_attempt(**kwargs)
    assert result.turns == 3 and result.reason == "stopped"
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert workspace.read("existing") == b"kept"
    assert [file.path for file in workspace.snapshot().files] == ["existing"]
    tool_events = [event for event in journal.load() if event.kind == "tool_requested"]
    assert [event.payload["turn"] for event in tool_events] == ([0, 1] if call else [0])
    if call:
        assert tool_events[-1].payload["call"]["argument_error"] == "response-output-limit"
    assert len([event for event in journal.load() if event.kind == "context_evicted"]) == 1
    view = trajectory(journal)
    assert [turn.exclusion for turn in view.turns] == [None, None, None]
    assert len(view.charges) == 3 and view.charges[1] == cut.charge
    saved = view.turns[1]
    assert ModelReply.from_dict(parse_json(journal.objects.read(saved.response)),
                               raw=journal.objects.read(saved.raw_response)) == cut
    assert run_attempt(**kwargs) == result and dispatches == [0, 1, 2]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
def test_bad_structure_retains_usage_wrong_model_does_not_get_the_requested_price(provider):
    ep = endpoint(provider)
    data = body(provider)
    key = {"openai": "output", "anthropic": "content", "fireworks": "choices"}[provider]
    data[key] = "not an array"
    result = response(ep, 200, canonical_json(data))
    assert type(result) is ModelFailure and result.charge is not None
    data = body(provider)
    data["model"] = "wrong-model"
    assert response(ep, 200, canonical_json(data)).charge is None
    ep = replace(ep, response_models=("wrong-model",))
    assert type(response(ep, 200, canonical_json(data))) is ModelReply


@pytest.mark.parametrize("provider,status,error", [
    ("openai", 429, {"type": "rate_limit_error", "code": "project_spend_limit_exceeded"}),
    ("anthropic", 429, {"type": "rate_limit_error", "details": {"error_code": "enforced_spend_limit_reached"}}),
    ("fireworks", 402, {"message": "Payment required"}),
])
def test_billing_rejection_wire_preserves_usage_before_error_decoding(provider, status, error):
    ep = endpoint(provider)
    data = {"error": error, "usage": body(provider)["usage"]}
    failure = response(ep, status, canonical_json(data), retry_after="60")
    assert (failure.kind, failure.code) == ("permanent", provider + "-quota-exhausted")
    assert failure.usage["normalized"] is not None and failure.charge is not None
    data.pop("usage")
    assert response(ep, status, canonical_json(data)).charge is None


@pytest.mark.parametrize("provider", ["anthropic", "fireworks"])
def test_non_json_payment_rejection_reaches_the_production_decoder(provider):
    failure = response(endpoint(provider), 402, b"Payment required")
    assert (failure.kind, failure.code) == ("permanent", provider + "-quota-exhausted")
    assert failure.raw == b"Payment required" and failure.charge is None


def test_hosted_quota_semantics_require_a_new_endpoint_identity():
    config = endpoint().as_dict()
    assert config["schema"] == "behave.hosted-provider/6"
    config["schema"] = "behave.hosted-provider/5"
    with pytest.raises(ValueError):
        HostedEndpoint.from_dict(config)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_native_continuation_cannot_smuggle_different_calls(provider):
    ep = endpoint(provider)
    reply = response(ep, 200, canonical_json(body(provider)))
    message = reply.message()
    message["content"] = "forged"
    with pytest.raises(ValueError):
        request_body(ep, request(ep, (message, {"role": "tool", "call_id": "call", "content": {}})))


def test_hosted_envelope_retains_raw_native_and_normalized_copies_without_a_quota():
    ep = endpoint()
    data = body("openai", call=False)
    data["output"][1]["content"][0]["text"] = "x" * (2 << 20)
    raw = canonical_json(data)
    assert len(raw) > 1 << 20
    encoded = encode_model_outcome(request(ep), response(ep, 200, raw))
    assert len(encoded) > 4 << 20
    assert "response_bytes" not in ep.command().as_dict()


def test_anthropic_cache_categories_are_disjoint_and_unknown_prices_stay_unknown():
    ep = endpoint("anthropic", prices=replace(endpoint().prices, cache_write_5m_per_million="2.5",
                                             cache_write_1h_per_million="4"))
    data = body("anthropic")
    data["usage"].update(cache_creation_input_tokens=7, cache_creation={
        "ephemeral_5m_input_tokens": 5, "ephemeral_1h_input_tokens": 2})
    got = response(ep, 200, canonical_json(data))
    assert got.usage["normalized"]["input_tokens"] == 17
    assert Decimal(got.charge.amount) == Decimal("0.0000745")
    assert response(replace(ep, prices=endpoint().prices), 200, canonical_json(data)).charge is None
    data["usage"].pop("cache_creation")
    assert response(ep, 200, canonical_json(data)).usage["normalization_error"] is not None
    with pytest.raises(ValueError):
        TokenUsage(1, 0, 0, None, 2, 0)


def credential_file(tmp_path, content="export FIXTURE_KEY='fixture-secret-value'\n"):
    path = tmp_path / "private.env"
    path.write_text(content)
    path.chmod(0o600)
    return CredentialRef(str(path), "FIXTURE_KEY", "test/1")


def test_private_credential_loading_no_interpolation_fallback_or_printing(tmp_path, monkeypatch):
    ref = credential_file(tmp_path)
    secret = load_credential(ref)
    assert "fixture-secret-value" not in repr(secret)
    assert secret.authorization("openai")["Authorization"] == "Bearer fixture-secret-value"
    monkeypatch.setenv("FIXTURE_KEY", "ambient-must-not-win")
    for content in ("FIXTURE_KEY=$(echo boom)", "# absent", "FIXTURE_KEY=one-secret\nFIXTURE_KEY=two-secret"):
        credential_file(tmp_path, content)
        with pytest.raises(ValueError):
            load_credential(ref)
    path = tmp_path / "private.env"
    credential_file(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        load_credential(ref)
    link = tmp_path / "link.env"
    link.symlink_to(path)
    with pytest.raises(OSError):
        load_credential(replace(ref, path=str(link)))


@pytest.mark.parametrize("value", ["", "contains space", "has\ttab", "line\nbreak", "non-ascii-密钥"])
def test_credentials_remain_nonempty_header_tokens(value):
    with pytest.raises(ValueError):
        Secret(value)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
def test_fixed_destination_broker_request_binding_and_secret_reflection(tmp_path, monkeypatch, provider):
    ep = endpoint(provider, credential=credential_file(tmp_path))
    observed = []

    def transport(url, packet, **kwargs):
        observed.append((url, kwargs["headers"]))
        return HttpResponse(200, canonical_json(body(provider)), None)

    monkeypatch.setattr("behave.agent.providers.hosted_worker.post_json", transport)
    assert type(exchange(ep, request(ep))) is ModelReply
    assert len(observed) == 1 and observed[0][0] == ep.url
    assert b"fixture-secret-value" not in canonical_json(ep.command().as_dict())
    assert HostedEndpoint.from_dict(ep.as_dict()) == ep
    bad = replace(request(ep), model_policy={})
    assert exchange(ep, bad).code == "provider-request-invalid" and len(observed) == 1
    good = response(ep, 200, canonical_json(body(provider)))
    bad = replace(good, content="fixture-secret-value", raw=b"fixture-secret-value")
    safe = redact_reflection(Secret("fixture-secret-value"), bad)
    assert safe.code == "provider-credential-reflected" and safe.raw == b"" and safe.charge == good.charge
    assert b"fixture-secret-value" not in canonical_json(safe.as_dict())
    escaped = ModelFailure("unknown", "upstream", raw=b'{"error":"\\u0066ixture-secret-value"}')
    assert redact_reflection(Secret("fixture-secret-value"), escaped).raw == b""


@pytest.mark.parametrize("unsafe_usage", [False, True])
@pytest.mark.parametrize("unsafe_charge", [False, True])
def test_shared_redaction_preserves_only_independently_safe_billing(unsafe_usage, unsafe_charge):
    from behave.agent.model import Charge
    from behave.agent.providers import chat_prefill, credentials

    assert redact_reflection is chat_prefill.redact_reflection is credentials.redact_reflection
    secret = Secret("fixture-secret-value")
    result = ModelReply("done", (), {"receipt": "fixture-secret-value" if unsafe_usage else "safe"},
        raw=b"fixture-secret-value",
        charge=Charge("1", "USD", "fixture-secret-value" if unsafe_charge else "fixture-prices"))
    safe = redact_reflection(secret, result)
    assert safe.code == "provider-credential-reflected" and safe.raw == b""
    assert safe.usage == ({} if unsafe_usage else result.usage)
    assert safe.charge == (None if unsafe_charge else result.charge)
    assert not secret.reflected_in(canonical_json(safe.as_dict()))


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks", "deepseek", "openrouter"])
def test_replayed_generation_receipt_is_redacted_without_provider_call(tmp_path, monkeypatch, provider):
    from behave.agent.spending import SpendAccount, SpendPolicy

    ep = endpoint(provider, parameters={"max_output_tokens": 4096})
    ep = replace(ep, prices=replace(ep.prices, cache_write_5m_per_million="2", cache_write_1h_per_million="2"),
                 spending=SpendPolicy(str(tmp_path / "budget"), "test", "USD", "1", 1000))
    req = request(ep)
    ceiling = ep.prices.reservation(1000, 4096)
    previous = ModelReply("fixture-secret-value", (), {"receipt": "safe"}, raw=b"fixture-secret-value", charge=ceiling)
    account = SpendAccount(ep.spending)
    account.dispatch(req, ceiling, lambda _: previous)
    before = account.journal.load()
    monkeypatch.setattr("behave.agent.providers.hosted_worker.load_credential", lambda _: Secret("fixture-secret-value"))
    monkeypatch.setattr("behave.agent.providers.hosted_worker.post_json",
                        lambda *a, **k: pytest.fail("must reuse saved generation"))
    result = exchange(ep, req)
    assert result.code == "provider-credential-reflected" and result.raw == b""
    assert result.charge == previous.charge and result.usage == previous.usage
    assert account.journal.load() == before


@pytest.mark.parametrize("parameters", [{"max_output_tokens": True}, {"max_output_tokens": 1, "tools": []},
                                       {"max_output_tokens": 1, "base_url": "https://elsewhere"},
                                       {"max_output_tokens": 1, "temperature": float("nan")}])
def test_closed_provider_configuration(parameters):
    with pytest.raises(ValueError):
        endpoint(parameters=parameters)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
def test_hosted_request_omits_unspecified_output_limit(provider):
    ep = endpoint(provider, parameters={})
    assert ep.parameters == {}
    packet = parse_json(request_body(ep, request(ep)))
    assert not {"max_output_tokens", "max_tokens", "max_completion_tokens"} & packet.keys()
    assert HostedEndpoint.from_dict(ep.as_dict()).parameters == {}


def test_anthropic_thinking_can_use_the_runtime_output_budget():
    ep = endpoint("anthropic", parameters={"thinking": "enabled", "thinking_budget_tokens": 1024})
    packet = parse_json(request_body(ep, request(ep)))
    assert packet["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert "max_tokens" not in packet  # The broker supplies the remaining context.


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
def test_hosted_output_budget_is_not_capped_again_by_the_profile(provider):
    ep = endpoint(provider, parameters={"max_output_tokens": 1_000_001})
    assert HostedEndpoint.from_dict(ep.as_dict()) == ep
    packet = parse_json(request_body(ep, request(ep)))
    assert packet["max_output_tokens" if provider == "openai" else "max_tokens"] == 1_000_001


@pytest.mark.parametrize("provider,standard", [("openai", "default"), ("anthropic", "standard")])
def test_observed_billing_tier_is_checked_even_without_a_requested_tier(provider, standard):
    ep = endpoint(provider)
    assert ep.billing_tier is None
    data = body(provider, call=False)
    tier_record = data if provider == "openai" else data["usage"]
    tier_record["service_tier"] = standard
    good = response(ep, 200, canonical_json(data))
    assert type(good) is ModelReply and good.charge is not None
    tier_record["service_tier"] = "priority"
    changed = response(ep, 200, canonical_json(data))
    assert type(changed) is ModelReply and changed.content == good.content
    assert changed.charge is None
    assert changed.usage["normalized"] == good.usage["normalized"]
    assert changed.usage["service_tier"] == "priority"
    assert changed.usage["pricing_error"] == "service-tier-mismatch"


@pytest.mark.isolation
@pytest.mark.parametrize("reflected", [False, True])
def test_hosted_worker_private_loader_and_envelope_in_real_process(tmp_path, monkeypatch, reflected):
    ep = endpoint(credential=credential_file(tmp_path))
    monkeypatch.setenv("UNRELATED_PRIVATE_KEY", "must-not-enter-worker")
    command = ep.command()
    argv = list(command.argv)
    # Replace only the trusted test transport, not the production profile's
    # destination policy. The worker/loader/codec/namespace path is unchanged.
    reply = (HttpResponse(503, b'{"error":{"message":"fixture-secret-value"}}', "0") if reflected
             else HttpResponse(200, canonical_json(body("openai", call=False)), None))
    argv[4] = (
        "import sys,os; sys.path.insert(0,sys.argv.pop(1)); "
        "assert 'UNRELATED_PRIVATE_KEY' not in os.environ; "
        "import behave.agent.providers.hosted_worker as worker; "
        "from behave.agent.providers.http_transport import HttpResponse; "
        f"worker.post_json=lambda *a,**k: HttpResponse({reply.status!r},{reply.raw!r},{reply.retry_after!r}); "
        "worker.main()"
    )
    command = replace(command, argv=tuple(argv))
    req = replace(request(ep, ({"role": "user", "content": "task"},)),
                  model_policy={**ep.model_policy(), "transport": command.identity})
    got = ProcessModelBackend(command).generate(req)
    if reflected:
        assert type(got) is ModelFailure and got.code == "provider-credential-reflected"
    else:
        assert type(got) is ModelReply and got.content == "thinking done"
    assert b"fixture-secret-value" not in got.raw
    assert b"fixture-secret-value" not in canonical_json(got.as_dict())
