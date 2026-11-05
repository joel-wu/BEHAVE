"""Parse-once verification retains the ordinary chat and token wire checks."""

from dataclasses import replace

import pytest
from test_chat_wire import request
from test_native_chat_tokens import native

from behave.agent.model import ModelReply
from behave.agent.providers import chat_wire, native_tokens
from behave.core.records import RecordError, canonical_json, parse_json
from behave.train import training_data
from behave.train.training_data import TokenTrace


def capture(runtime):
    endpoint, packet = native("vllm" if runtime == "vllm" else "sglang")
    if runtime == "sglang-slime":
        endpoint = replace(endpoint, tokens=replace(endpoint.tokens, runtime=runtime))
        packet["choices"][0].pop("response_token_ids")
    req, raw = request(endpoint), canonical_json(packet)
    trace = TokenTrace.from_dict(native_tokens.decode_tokens(endpoint.tokens, req, raw))
    return endpoint, req, packet, raw, trace


def separate_passes(endpoint, trace, req, raw):
    """The previous verifier, using the still-supported ordinary byte decoders."""
    chat_wire.request_body(endpoint, req)
    reply = chat_wire.response(endpoint, 200, raw)
    if type(reply) is not ModelReply or reply.training_exclusion is not None:
        raise RecordError("native-token-response-invalid", "response is not eligible native generation")
    expected = native_tokens.decode_tokens(endpoint.tokens, req, raw)
    if canonical_json(expected) != canonical_json(trace.as_dict()):
        raise RecordError("native-token-trace-mismatch", "trace differs from the recorded native response")


@pytest.mark.parametrize("runtime", ["sglang-slime", "sglang", "vllm"])
def test_native_verification_parses_wire_record_once(runtime, monkeypatch):
    endpoint, req, _, raw, trace = capture(runtime)
    parsed = []

    def counted(payload):
        if payload == raw:
            parsed.append(payload)
        return parse_json(payload)

    for module in (chat_wire, native_tokens, training_data):
        monkeypatch.setattr(module, "parse_json", counted)
    training_data.NativeChatVerifier(endpoint).verify(trace, req, raw)
    assert len(parsed) == 1
    parsed.clear()
    separate_passes(endpoint, trace, req, raw)
    assert len(parsed) == 2


@pytest.mark.parametrize("runtime", ["sglang-slime", "sglang", "vllm"])
@pytest.mark.parametrize("mutation", [
    "unchanged", "model", "finish", "choices", "usage-count", "usage-total", "response-length",
    "token", "logprob", "duplicate-key",
    "surrogate", "overflow", "nan", "invalid-json",
])
def test_parse_once_and_separate_passes_have_identical_decisions(runtime, mutation):
    endpoint, req, packet, raw, trace = capture(runtime)
    choice = packet["choices"][0]
    if mutation == "model":
        packet["model"] = "wrong-model"
    elif mutation == "finish":
        choice["finish_reason"] = "length"
    elif mutation == "choices":
        packet["choices"].append(dict(choice))
    elif mutation in {"usage-count", "usage-total"}:
        packet["usage"]["completion_tokens"] += 1
        if mutation == "usage-count":
            packet["usage"]["total_tokens"] += 1
    elif mutation == "response-length":
        # Change the sampled sequence, keeping its IDs/logprobs aligned. This
        # still must fail against the originally recorded training trace.
        if runtime == "vllm":
            choice["token_ids"].pop()
            choice["logprobs"]["content"].pop()
        else:
            choice["meta_info"]["output_token_logprobs"].pop()
            if runtime == "sglang":
                choice["response_token_ids"].pop()
    elif mutation == "token":
        if runtime == "vllm":
            choice["token_ids"][0] = True
        elif runtime == "sglang":
            choice["response_token_ids"][0] = True
        else:
            choice["meta_info"]["output_token_logprobs"][0][1] = True
    elif mutation == "logprob":
        if runtime == "vllm":
            choice["logprobs"]["content"][0]["logprob"] = 0.5
        else:
            choice["meta_info"]["output_token_logprobs"][0][0] = 0.5
    raw = canonical_json(packet)
    if mutation == "duplicate-key":
        raw = b'{"x":0,"x":1,' + raw[1:]
    elif mutation == "surrogate":
        raw = b'{"x":"\\ud800",' + raw[1:]
    elif mutation == "overflow":
        raw = b'{"x":1e999,' + raw[1:]
    elif mutation == "nan":
        raw = b'{"x":NaN,' + raw[1:]
    elif mutation == "invalid-json":
        raw = raw[:-1]

    def decision(check):
        try:
            check(trace, req, raw)
        except RecordError as error:
            return error.code
        return "accepted"

    previous = decision(lambda *args: separate_passes(endpoint, *args))
    assert decision(training_data.NativeChatVerifier(endpoint).verify) == previous
    assert (previous == "accepted") == (mutation in {"unchanged", "finish", "usage-count", "usage-total"})
    if mutation in {"usage-count", "usage-total"}:
        reply = chat_wire.response(endpoint, 200, raw)
        assert reply.raw == raw and reply.usage["raw"]["completion_tokens"] == 6
        # Accounting can differ from native token evidence; preserve the raw
        # usage and flag inconsistent totals without fabricating training IDs.
        assert len(trace.response_ids) == 5
        if mutation == "usage-count":
            assert reply.usage["normalized"]["output_tokens"] == 6
            assert reply.usage["normalization_error"] is None
        else:
            assert reply.usage["normalized"] is None
            assert reply.usage["normalization_error"] == "usage-missing-or-invalid"
