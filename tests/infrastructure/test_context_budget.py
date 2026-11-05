"""The same input+generation budget serves hosted, local and training rollouts."""

from dataclasses import replace

import pytest
from test_chat_wire import body as chat_body
from test_chat_wire import endpoint as chat_endpoint
from test_chat_wire import request as chat_request
from test_hosted_providers import body as hosted_body
from test_hosted_providers import endpoint as hosted_endpoint
from test_hosted_providers import request as hosted_request

from behave.agent import AttemptSpec
from behave.agent.model import ModelFailure, ModelReply, ModelRequest
from behave.agent.providers import chat_worker, context, hosted_worker
from behave.agent.providers.credentials import Secret
from behave.agent.providers.http_transport import HttpResponse
from behave.agent.spending import SpendAccount, SpendPolicy
from behave.core.records import canonical_json, parse_json


def test_budget_defaults_and_legacy_records():
    spec = AttemptSpec("budget", "solver", {"model": "fixture"})
    assert spec.context_tokens == 131072 and spec.max_turns == 60
    assert AttemptSpec.from_dict(spec.as_dict()) == spec
    old = spec.as_dict()
    del old["context_tokens"]
    assert AttemptSpec.from_dict(old).context_tokens is None
    assert AttemptSpec.from_dict(old).as_dict() == old
    request = chat_request(chat_endpoint())
    assert "context_tokens" not in request.as_dict()
    request = replace(request, context_tokens=131072)
    assert ModelRequest.from_dict(request.as_dict()) == request


@pytest.mark.parametrize("count", [131072, 140000])
def test_full_input_requests_whole_turn_eviction_without_generation(count):
    req = replace(chat_request(chat_endpoint()), context_tokens=131072)
    result = context.limit_output({}, req, count, field="max_tokens")
    assert result.kind == "context_limit" and result.code == "context-input-full"


def test_anthropic_explicit_thinking_budget_must_fit_remaining_generation():
    req = replace(hosted_request(hosted_endpoint("anthropic")), context_tokens=2048)
    body = {"thinking": {"type": "enabled", "budget_tokens": 1024}}
    result = context.limit_output(body, req, 1024, field="max_tokens")
    assert result.kind == "context_limit"
    wire, _ = context.limit_output(body, req, 1023, field="max_tokens")
    assert parse_json(wire)["max_tokens"] == 1025


def test_anthropic_automatic_budget_follows_remaining_total_context():
    ep = hosted_endpoint("anthropic", parameters={"thinking": "enabled", "max_output_tokens": 64000})
    req = replace(hosted_request(ep), context_tokens=131072)
    packet = parse_json(hosted_worker.request_body(ep, req))
    wire, budget = context.limit_output(packet, req, 100000, field="max_tokens", automatic_thinking=True)
    assert parse_json(wire)["max_tokens"] == 31072
    assert parse_json(wire)["thinking"]["budget_tokens"] == 31071
    assert budget["input_tokens"] + budget["max_output_tokens"] == 131072


def test_fireworks_zero_output_prefill_is_counted_billed_and_retained(monkeypatch, tmp_path):
    from behave.agent.providers import chat_prefill

    ep = hosted_endpoint("fireworks", spending=SpendPolicy(str(tmp_path / "budget"), "test", "USD", "1", 1000))
    req = replace(hosted_request(ep), context_tokens=15)
    monkeypatch.setattr(hosted_worker, "load_credential", lambda _: Secret("test-secret"))
    seen = []

    def post(url, raw, **kwargs):
        packet = parse_json(raw)
        seen.append(packet)
        count = packet["max_tokens"] == 0
        return HttpResponse(200, canonical_json({"model": ep.model,
            "usage": {"prompt_tokens": 10, "completion_tokens": 0 if count else 5,
                      "prompt_tokens_details": {"cached_tokens": 0}},
            "choices": [{"index": 0, "finish_reason": "length" if count else "stop",
                         "message": {"role": "assistant", "content": "" if count else "done", "tools": None}}]}), None)

    monkeypatch.setattr(chat_prefill, "post_json", post)
    monkeypatch.setattr(hosted_worker, "post_json", post)
    reply = hosted_worker.exchange(ep, req)
    assert isinstance(reply, ModelReply), reply
    assert [packet["max_tokens"] for packet in seen] == [0, 5]
    assert seen[0]["messages"] == seen[1]["messages"]
    assert seen[0]["tools"] == seen[1]["tools"]
    assert reply.usage["input_count"]["provider_response"]["usage"]["completion_tokens"] == 0
    ledger = SpendAccount(ep.spending).summary()
    assert ledger["calls"] == 2 and ledger["unpriced"] == 0
    from decimal import Decimal
    assert Decimal(ledger["spent"]) == Decimal(reply.charge.amount) + Decimal(reply.usage["input_count"]["charge"]["amount"])


def test_deepseek_one_token_count_preserves_history_billing_and_reasoning(monkeypatch, tmp_path):
    from behave.agent.providers import chat_prefill

    ep = hosted_endpoint("deepseek", parameters={"thinking": "enabled"},
        spending=SpendPolicy(str(tmp_path / "budget"), "test", "USD", "1", 1000))
    req = replace(hosted_request(ep), context_tokens=15)
    monkeypatch.setattr(hosted_worker, "load_credential", lambda _: Secret("test-secret"))
    seen = []

    def post(url, raw, **kwargs):
        packet = parse_json(raw)
        seen.append(packet)
        count = len(seen) == 1
        return HttpResponse(200, canonical_json({"model": ep.model,
            "usage": {"prompt_tokens": 10, "completion_tokens": 1 if count else 5,
                      "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6},
            "choices": [{"index": 0, "finish_reason": "length" if count else "stop",
                         "message": {"role": "assistant", "content": "" if count else "done",
                                     "reasoning_content": "I" if count else "think"}}]}), None)

    monkeypatch.setattr(chat_prefill, "post_json", post)
    monkeypatch.setattr(hosted_worker, "post_json", post)
    reply = hosted_worker.exchange(ep, req)
    assert isinstance(reply, ModelReply), reply
    assert [packet["max_tokens"] for packet in seen] == [1, 5]
    assert seen[0]["messages"] == seen[1]["messages"]
    assert seen[0]["tools"] == seen[1]["tools"]
    assert seen[1]["thinking"] == {"type": "enabled"}
    assert reply.continuation["reasoning_content"] == "think"
    assert reply.usage["normalized"]["cached_input_tokens"] == 4
    assert reply.usage["input_count"]["provider_response"]["usage"]["completion_tokens"] == 1
    ledger = SpendAccount(ep.spending).summary()
    assert ledger["calls"] == 2 and ledger["unpriced"] == 0


@pytest.mark.parametrize("provider", ["fireworks", "deepseek", "openrouter"])
@pytest.mark.parametrize("case", ["content", "excess-output", "missing-usage", "tool-call", "transport"])
@pytest.mark.parametrize("spending", [False, True])
def test_count_outcomes_are_redacted_before_return_or_spend_receipt(monkeypatch, tmp_path, provider, case, spending):
    from behave.agent.process_model import decode_model_outcome
    from behave.agent.providers import chat_prefill
    from behave.core.artifacts import BlobRef

    policy = SpendPolicy(str(tmp_path / "budget"), "test", "USD", "1", 1000) if spending else None
    ep = hosted_endpoint(provider, spending=policy)
    req = replace(hosted_request(ep), context_tokens=15)
    secret = Secret("fixture-prefill-secret")
    cap = 0 if provider == "fireworks" else 1
    native = hosted_body(provider, call=case == "tool-call")
    native["choices"][0]["message"]["content"] = "fixture-prefill-secret"
    native["usage"].update(completion_tokens=cap + (case == "excess-output"), cost="0.000123")
    if case == "missing-usage":
        del native["usage"]
    # JSON-escaped reflection is just as private as a literal header value.
    raw = canonical_json(native).replace(b"fixture-prefill-secret", b"\\u0066ixture-prefill-secret")
    expected = hosted_worker.response(ep, 200, raw)
    seen = []

    def post(url, packet, **kwargs):
        seen.append(parse_json(packet)["max_tokens"])
        if case == "transport":
            return ModelFailure("unknown", "fixture-transport-interrupted", raw=raw)
        return HttpResponse(200, raw, None)

    monkeypatch.setattr(hosted_worker, "load_credential", lambda _: secret)
    monkeypatch.setattr(chat_prefill, "post_json", post)
    monkeypatch.setattr(hosted_worker, "post_json", lambda *a, **k: pytest.fail("must not generate after failed count"))
    result = hosted_worker.exchange(ep, req)
    assert seen == [cap]
    assert isinstance(result, ModelFailure) and result.code == "provider-credential-reflected"
    assert result.raw == b"" and not secret.reflected_in(canonical_json(result.as_dict()))
    if case != "transport":
        assert result.usage == expected.usage and result.charge == expected.charge
    if spending:
        account = SpendAccount(policy)
        assert account.summary()["calls"] == 1
        counting = replace(req, call_id=req.call_id + ":input-count")
        reference = BlobRef.of(canonical_json(counting.as_dict()))
        receipt, = account.run.journal("outcomes", "r_" + reference.sha256).load()
        saved = account.run.objects.read(BlobRef.from_dict(dict(receipt.payload["outcome"])))
        assert decode_model_outcome(counting, saved) == result


@pytest.mark.parametrize("provider", ["fireworks", "deepseek", "openrouter"])
def test_replayed_count_receipt_is_redacted_without_redispatch_or_rewrite(tmp_path, monkeypatch, provider):
    from behave.agent.providers import chat_prefill

    ep = hosted_endpoint(provider, spending=SpendPolicy(str(tmp_path / "budget"), "test", "USD", "1", 1000))
    req = replace(hosted_request(ep), context_tokens=15)
    counting = replace(req, call_id=req.call_id + ":input-count")
    cap = 0 if provider == "fireworks" else 1
    ceiling = ep.prices.reservation(ep.spending.maximum_input_tokens, cap)
    previous = ModelFailure("permanent", "fixture-old-prefill-invalid", {"receipt": "safe"},
                            raw=b"fixture-prefill-secret", charge=ceiling)
    account = SpendAccount(ep.spending)
    account.dispatch(counting, ceiling, lambda _: previous)
    before = account.journal.load()
    monkeypatch.setattr(chat_prefill, "post_json", lambda *a, **k: pytest.fail("must reuse saved count"))
    got = chat_prefill.count_prompt(ep, req, {}, Secret("fixture-prefill-secret"))
    assert got.code == "provider-credential-reflected" and got.raw == b""
    assert got.charge == previous.charge and got.usage == previous.usage
    assert account.journal.load() == before


@pytest.mark.parametrize("runtime", ["sglang", "sglang-slime", "vllm"])
def test_local_count_and_generation_use_same_engine_template_tools_and_native_ids(monkeypatch, runtime):
    from test_native_chat_tokens import native

    from behave.agent.tools import file_tools
    from behave.core.records import thaw_json
    from behave.train.training_data import NativeChatVerifier, TokenTrace

    base, reply_body = native("sglang" if runtime == "sglang-slime" else runtime)
    base = replace(base, tokens=replace(base.tokens, runtime=runtime))
    ep = replace(base, routing="attempt", worker_urls=(
        "http://127.0.0.1:12345/v1/chat/completions",
        "http://127.0.0.1:12346/v1/chat/completions"),
        parameters={"reasoning_effort": "xhigh"})
    req = replace(chat_request(ep, messages=({"role": "user", "content": "work"},),
                               tools=tuple(tool.declaration() for tool in file_tools())), context_tokens=15)
    observed = []

    def count(url, raw, **kwargs):
        observed.append((url, parse_json(raw), kwargs["headers"]))
        return HttpResponse(200, canonical_json({"count": 10}), None)

    def generate(url, raw, **kwargs):
        observed.append((url, parse_json(raw), kwargs["headers"]))
        return HttpResponse(200, canonical_json(reply_body), None)

    monkeypatch.setattr(context, "post_json", count)
    monkeypatch.setattr(chat_worker, "post_json", generate)
    reply = chat_worker.exchange(ep, req)
    assert isinstance(reply, ModelReply), reply
    assert observed[0][0] == observed[1][0].removesuffix("/v1/chat/completions") + "/tokenize"
    assert observed[0][2] == observed[1][2]
    assert observed[0][1]["messages"] == observed[1][1]["messages"]
    assert observed[0][1]["tools"] == observed[1][1]["tools"] and observed[0][1]["tools"]
    assert observed[1][1]["max_tokens"] == 5
    kwargs = {"reasoning_effort": "xhigh", "enable_thinking": True}
    assert observed[0][1]["chat_template_kwargs"] == kwargs
    assert "reasoning_effort" not in observed[0][1]
    if runtime != "vllm":
        assert observed[1][1]["chat_template_kwargs"] == kwargs
        assert "reasoning_effort" not in observed[1][1]
    trace = TokenTrace.from_dict(thaw_json(reply.token_data))
    assert len(trace.input_ids) + len(trace.response_ids) == 15
    NativeChatVerifier(ep).verify(trace, req, reply.raw)
    assert reply.usage["context_budget"]["limit"] == 15
    assert reply.usage["normalized"]["output_tokens"] == 5  # Includes 3 reasoning tokens, not 5+3.


@pytest.mark.parametrize("effort, overrides, expected", [
    ("none", {}, {"reasoning_effort": "none", "enable_thinking": False}),
    ("xhigh", {"enable_thinking": False}, {"reasoning_effort": "xhigh", "enable_thinking": False}),
    ("medium", {"reasoning_effort": "low"}, {"reasoning_effort": "low", "enable_thinking": True}),
])
def test_sglang_template_options_preserve_explicit_overrides_without_context_count(
        monkeypatch, effort, overrides, expected):
    from test_native_chat_tokens import native

    ep, reply_body = native("sglang")
    ep = replace(ep, parameters={"reasoning_effort": effort, "chat_template_kwargs": overrides})
    observed = []

    def post(url, raw, **kwargs):
        observed.append(parse_json(raw))
        return HttpResponse(200, canonical_json(reply_body), None)

    monkeypatch.setattr(chat_worker, "post_json", post)
    reply = chat_worker.exchange(ep, chat_request(ep))
    assert isinstance(reply, ModelReply), reply
    assert observed[0]["chat_template_kwargs"] == expected
    assert "reasoning_effort" not in observed[0]


def test_generation_only_roles_can_share_sglang_template_without_training_tokens(monkeypatch):
    options = {"reasoning_effort": "xhigh", "enable_thinking": True}
    ep = chat_endpoint(parameters={"chat_template_kwargs": options})
    assert ep.tokens is None
    req = replace(chat_request(ep), context_tokens=15)
    observed = []

    def post(url, raw, **kwargs):
        packet = parse_json(raw)
        observed.append(packet)
        return HttpResponse(200, canonical_json({"count": 10} if url.endswith("/tokenize") else chat_body()), None)

    monkeypatch.setattr(context, "post_json", post)
    monkeypatch.setattr(chat_worker, "post_json", post)
    reply = chat_worker.exchange(ep, req)
    assert isinstance(reply, ModelReply), reply
    assert reply.token_data is None
    assert len(observed) == 2
    assert all(packet["chat_template_kwargs"] == options for packet in observed)
    assert all("reasoning_effort" not in packet for packet in observed)
    assert observed[0]["messages"] == observed[1]["messages"]


@pytest.mark.parametrize("provider, suffix, field", [
    ("openai", "/input_tokens", "max_output_tokens"),
    ("anthropic", "/count_tokens", "max_tokens"),
])
def test_hosted_runtime_budget_supplies_output_field_without_profile_cap(monkeypatch, provider, suffix, field):
    ep = hosted_endpoint(provider, parameters={})
    req = replace(hosted_request(ep), context_tokens=15)
    observed = []
    monkeypatch.setattr(hosted_worker, "load_credential", lambda _: Secret("test-secret"))

    def count(url, raw, **kwargs):
        observed.append((url, parse_json(raw)))
        return HttpResponse(200, canonical_json({"input_tokens": 10}), None)

    def generate(url, raw, **kwargs):
        observed.append((url, parse_json(raw)))
        return HttpResponse(200, canonical_json(hosted_body(provider, call=False)), None)

    monkeypatch.setattr(context, "post_json", count)
    monkeypatch.setattr(hosted_worker, "post_json", generate)
    reply = hosted_worker.exchange(ep, req)
    assert isinstance(reply, ModelReply), reply
    assert observed[0][0] == ep.url + suffix and observed[1][0] == ep.url
    assert observed[1][1][field] == 5 and field not in observed[0][1]
    assert observed[0][1]["tools"] == observed[1][1]["tools"]
    assert "cache_control" not in observed[0][1]  # Counting does not populate a cache.
    assert observed[1][1].get("cache_control") == ({"type": "ephemeral"} if provider == "anthropic" else None)
    assert reply.usage["context_budget"] == {"limit": 15, "input_tokens": 10, "max_output_tokens": 5}


def test_spending_uses_counted_input_and_remaining_output(monkeypatch, tmp_path):
    ep = hosted_endpoint(parameters={}, spending=SpendPolicy(str(tmp_path / "budget"), "test", "USD", "1", 1000))
    req = replace(hosted_request(ep), context_tokens=15)
    monkeypatch.setattr(hosted_worker, "load_credential", lambda _: Secret("test-secret"))
    monkeypatch.setattr(context, "post_json", lambda *a, **k: HttpResponse(200, b'{"input_tokens":10}', None))
    monkeypatch.setattr(hosted_worker, "post_json", lambda *a, **k:
                        HttpResponse(200, canonical_json(hosted_body("openai", call=False)), None))
    real_dispatch = SpendAccount.dispatch
    ceilings = []

    def dispatch(self, request, ceiling, invoke):
        ceilings.append(ceiling)
        return real_dispatch(self, request, ceiling, invoke)

    monkeypatch.setattr(SpendAccount, "dispatch", dispatch)
    reply = hosted_worker.exchange(ep, req)
    assert isinstance(reply, ModelReply), reply
    assert ceilings == [ep.prices.reservation(10, 5)]


def test_reply_over_budget_retains_usage_charge_and_raw_but_does_not_execute_tools():
    from behave.agent.providers.chat_wire import response

    ep = chat_endpoint()
    raw = canonical_json(chat_body())
    reply = response(ep, 200, raw)
    result = context.record_budget(reply, {"limit": 14, "input_tokens": 10, "max_output_tokens": 4})
    assert result.kind == "permanent" and result.code == "context-budget-exceeded"
    assert result.raw == raw and result.charge == reply.charge
    assert result.usage["normalized"]["output_tokens"] == 5


@pytest.mark.parametrize("finish, maximum, expected", [
    ("length", 20, "context_limit"), ("length", 10, "length"), ("stop", 20, "stop"),
])
def test_context_limited_generation_is_distinct_from_an_output_cap(finish, maximum, expected):
    reply = ModelReply("", usage={"normalized": {"input_tokens": 80, "output_tokens": maximum - 2}},
                       finish_reason=finish, raw=b"original provider response",
                       training_exclusion="output-length" if finish == "length" else None)
    result = context.record_budget(reply, {"limit": 100, "input_tokens": 80, "max_output_tokens": maximum})
    assert result.finish_reason == expected
    assert result.raw == reply.raw and result.charge == reply.charge
    assert result.training_exclusion == reply.training_exclusion


def test_context_limited_reply_evicts_before_the_next_generation(tmp_path):
    from test_attempts import call, setup

    from behave.agent import run_attempt
    from behave.agent.tools import file_tools
    from behave.agent.workspace import Workspace

    journal, spec = setup(tmp_path)
    requests = []
    limited = context.record_budget(ModelReply("", finish_reason="length", training_exclusion="output-length",
        usage={"normalized": {"input_tokens": 98, "output_tokens": 2}}),
        {"limit": 100, "input_tokens": 98, "max_output_tokens": 2})
    replies = iter((call("write_file", {"path": "a", "content": "kept"}), limited, ModelReply("done")))

    class Backend:
        def generate(self, request):
            requests.append(request)
            return next(replies)

    opening = ({"role": "system", "content": "setting"}, {"role": "user", "content": "task"})
    result = run_attempt(backend=Backend(), journal=journal, spec=replace(spec, context_tokens=100),
                         initial_messages=opening, tools=file_tools())
    assert result.reason == "stopped" and result.turns == 3
    assert requests[2].messages[:2] == opening
    assert all(message["role"] != "tool" for message in requests[2].messages)
    assert sum(event.kind == "context_evicted" for event in journal.load()) == 1
    restored = Workspace(journal.objects)
    restored.restore(result.workspace)
    assert restored.read("a") == b"kept"


@pytest.mark.parametrize("outcome, code", [
    (HttpResponse(200, b'{}', None), "chat-input-token-count-invalid"),
    (ModelFailure("unknown", "chat-exchange-failed"), "chat-exchange-failed"),
])
def test_count_failure_never_sends_a_guessed_generation(monkeypatch, outcome, code):
    monkeypatch.setattr(context, "post_json", lambda *a, **k: outcome)
    monkeypatch.setattr(chat_worker, "post_json", lambda *a, **k: pytest.fail("generation must not be sent"))
    ep = chat_endpoint()
    reply = chat_worker.exchange(ep, replace(chat_request(ep), context_tokens=131072))
    assert reply.code == code
    assert reply.kind == ("retryable" if isinstance(outcome, ModelFailure) else "permanent")


def test_runtime_context_preflight_uses_existing_whole_turn_eviction(tmp_path):
    from test_attempts import call, setup

    from behave.agent import run_attempt
    from behave.agent.tools import file_tools

    journal, spec = setup(tmp_path)
    requests = []
    outcomes = iter((call("write_file", {"path": "a", "content": "kept"}),
                     call("read_file", {"path": "a"}, "read"), ModelReply("done")))

    class Backend:
        def generate(self, request):
            requests.append(request)
            assert request.context_tokens == spec.context_tokens
            count = 140000 if len(requests) == 3 else 100
            result = context.limit_output({}, request, count, field="max_tokens")
            return result if isinstance(result, ModelFailure) else next(outcomes)

    opening = ({"role": "system", "content": "setting"}, {"role": "user", "content": "task"})
    result = run_attempt(backend=Backend(), journal=journal, spec=spec, initial_messages=opening, tools=file_tools())
    assert result.reason == "stopped" and result.turns == 3
    assert requests[-1].turn == requests[-2].turn == 2
    assert requests[-1].messages[:2] == opening
    assert requests[-1].messages[-2]["role"] == "tool"
    assert len(requests[-1].messages) < len(requests[-2].messages)
