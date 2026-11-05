"""Append-only chat continuation and loss-preserving packing, using fixture tokens."""

import json
from dataclasses import replace
from itertools import pairwise

import pytest

from behave.agent import AttemptJournal, AttemptSpec, run_attempt
from behave.agent.model import ModelFailure, ModelReply, ModelRequest
from behave.agent.providers import chat_worker, context
from behave.agent.providers.http_transport import HttpResponse
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.providers.native_history import append_text, model_continuation
from behave.agent.tools import file_tools
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.train.slime_rollout import coalesce_payloads

# A fixture template, not a substitute for the checkpoint's production template.
TEMPLATE = (
    "{% for m in messages %}"
    "{{ '<start>' + m.role + '\\n' + m.content|trim + '<end>\\n' }}"
    "{% endfor %}{% if add_generation_prompt %}{{ '<start>assistant\\n' }}{% endif %}"
)
PROFILE = {"chat_template": TEMPLATE, "eos_token": "<end>", "eos_token_id": 99}


def endpoint():
    from test_native_chat_tokens import native

    base, _ = native("sglang-slime")
    return replace(base, tokens=replace(base.tokens, continuation=PROFILE))


def test_profile_roundtrip_and_opt_in():
    from test_native_chat_tokens import native

    ep = endpoint()
    assert ep.model_policy()["native_continuation"] is True
    assert LocalChatEndpoint.from_dict(ep.as_dict()) == ep
    assert "native_continuation" not in native("sglang-slime")[0].model_policy()
    config = {"chat_template": TEMPLATE, "eos_token": {"content": "<end>"},
              "added_tokens_decoder": {"99": {"content": "<end>"}}}
    assert model_continuation(config) == PROFILE
    with pytest.raises(ValueError, match="SGLang"):
        replace(ep.tokens, runtime="vllm")


@pytest.mark.parametrize("ended", [False, True])
def test_only_environment_is_rendered_and_partial_output_separator_is_not_sampled(ended):
    suffix = append_text(PROFILE, [{"role": "tool", "content": " result "},
                                  {"role": "user", "content": " next "}], ended=ended)
    assert suffix == ("" if ended else "<end>") + (
        "\n<start>tool\nresult<end>\n<start>user\nnext<end>\n<start>assistant\n")


def test_incompatible_template_is_not_silently_reencoded():
    changed = {**PROFILE, "chat_template": "{{ messages|length }}" + TEMPLATE}
    with pytest.raises(ValueError, match="append-only"):
        append_text(changed, [{"role": "user", "content": "next"}])


class Wire:
    """Controlled native-ID responses whose normalized text is intentionally different."""
    def __init__(self, *, clipped=False):
        self.bodies, self.prefixes, self.outputs, self.suffixes = [], [], [], []
        self.clipped = clipped

    def reply(self, body, path):
        if path.endswith("/tokenize"):
            if "prompt" in body:
                assert body["add_special_tokens"] is False
                self.suffixes.append(body["prompt"])
                return {"count": 3, "tokens": [500, 501, 502]}
            return {"count": 2, "tokens": [10, 11]}
        self.bodies.append(body)
        index = len(self.bodies) - 1
        inputs = body.get("input_ids", [10, 11])
        outputs = [20 + index * 10, 21 + index * 10]
        if self.clipped and index == 0:
            outputs.extend([22, 23])
        else:
            outputs.append(99)
        self.prefixes.append(inputs)
        self.outputs.append(outputs)
        calls = [] if index == 2 else [{"id": f"call-{index}", "type": "function", "function": {
            "name": "write_file", "arguments": json.dumps({"path": "result.txt", "content": str(index)})}}]
        return {"model": "fixture", "choices": [{"index": 0,
            "finish_reason": "length" if self.clipped and index == 0 else "tool_calls" if calls else "stop",
            "message": {"role": "assistant", "content": "NORMALIZED_ASSISTANT_TEXT", "tool_calls": calls},
            "prompt_token_ids": inputs,
            "meta_info": {"output_token_logprobs": [[-.25, token, None] for token in outputs]}}],
            "usage": {"prompt_tokens": len(inputs), "completion_tokens": len(outputs)}}


def run_three(tmp_path, ep, backend):
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("continuation", "solver", ep.model_policy(), max_turns=3, context_tokens=128)
    kwargs = {"journal": journal, "spec": spec, "tools": file_tools(),
              "initial_messages": ({"role": "user", "content": "do work"},)}
    result = run_attempt(backend=backend, **kwargs)
    assert result.reason == "stopped" and result.turns == 3
    return journal, kwargs, result


def check_history(journal, wire):
    from test_slime_packing import assert_exact, payload

    requests, records = [], []
    for event in journal.load():
        if event.kind == "model_requested":
            requests.append(parse_json(journal.objects.read(BlobRef.from_dict(thaw_json(event.payload["request"])))))
        if event.kind == "model_settled":
            records.append(parse_json(journal.objects.read(BlobRef.from_dict(thaw_json(event.payload["record"])))))
    assert "native_prefix" not in requests[0]
    assert all("native_prefix" in row and "input_ids" not in canonical_json(row["native_prefix"]).decode()
               for row in requests[1:])
    assert all(ModelRequest.from_dict(row).as_dict() == row for row in requests)
    assert all("NORMALIZED_ASSISTANT_TEXT" not in text for text in wire.suffixes)
    assert len(wire.suffixes) == 2
    for previous, current in pairwise(records):
        prefix = previous["token_data"]["input_ids"] + previous["token_data"]["response_ids"]
        assert current["token_data"]["input_ids"][:len(prefix)] == prefix
    raw = tuple(payload(i, row["token_data"]["input_ids"], row["token_data"]["response_ids"])
                for i, row in enumerate(records))
    for item, row in zip(raw, records):
        item["rollout_log_probs"] = row["token_data"]["signals"]["old_log_probs"]
    packed = coalesce_payloads(raw)
    assert len(packed) == 1
    assert_exact(raw, packed)
    assert sum(packed[0]["loss_mask"]) == sum(map(len, wire.outputs))


@pytest.mark.parametrize("clipped", [False, True])
def test_three_native_turns_merge_without_losing_targets_or_reencoding_assistant(tmp_path, monkeypatch, clipped):
    wire, ep = Wire(clipped=clipped), endpoint()
    if clipped:
        ep = replace(ep, parameters={"max_tokens": 4})

    def post(url, packet, **kwargs):
        return HttpResponse(200, canonical_json(wire.reply(parse_json(packet), url)), None)

    monkeypatch.setattr(chat_worker, "post_json", post)
    monkeypatch.setattr(context, "post_json", post)

    class Backend:
        def generate(self, request):
            return chat_worker.exchange(ep, request)

    journal, kwargs, result = run_three(tmp_path, ep, Backend())
    check_history(journal, wire)
    assert wire.suffixes[0].startswith("<end>\n" if clipped else "\n")

    class Replay:
        def generate(self, request):
            pytest.fail("completed replay must not generate or tokenize")

    # Relocated closed records replay without referring to the old live backend.
    cold = DirectoryStore(tmp_path / "cold")
    cold.publish(journal.objects, tuple(BlobRef(path.name, path.stat().st_size)
                                      for path in journal.objects.directory.iterdir()))
    clone = AttemptJournal(journal.directory, cold)
    assert run_attempt(backend=Replay(), **{**kwargs, "journal": clone}) == result


def test_eviction_clears_native_prefix_without_changing_turn_budget(tmp_path):
    from test_attempts import Scripted, call

    ep = endpoint()
    captured = {"schema": "behave.token-trace/1"}
    backend = Scripted(replace(call("write_file", {"path": "a", "content": "a"}), token_data=captured),
        replace(call("read_file", {"path": "a"}), token_data=captured),
        ModelFailure("context_limit", "full"), ModelReply("done"))
    _, _, result = run_three(tmp_path, ep, backend)
    assert backend.requests[-2].native_prefix is not None
    assert backend.requests[-1].native_prefix is None
    assert backend.requests[-1].turn == backend.requests[-2].turn == 2
    assert result.turns == 3


@pytest.mark.parametrize("context_tokens", [None, 8, 12])
def test_continuation_budget_uses_actual_ids_without_recounting_old_messages(tmp_path, monkeypatch, context_tokens):
    from test_chat_wire import request

    ep, wire = endpoint(), Wire()
    store = DirectoryStore(tmp_path / "objects")
    previous = store.put(canonical_json({"token_data": {
        "input_ids": [10, 11, 12], "response_ids": [20, 99]}}))
    req = replace(request(ep), context_tokens=context_tokens,
        messages=({"role": "user", "content": "old"},
                  ModelReply("old reply").message(),
                  {"role": "user", "content": "continue"}),
        native_prefix={"objects": str(store.directory), "response": previous.as_dict(), "message_count": 2})

    def post(url, packet, **kwargs):
        body = parse_json(packet)
        if url.endswith("/tokenize"):
            assert "prompt" in body and "messages" not in body
        return HttpResponse(200, canonical_json(wire.reply(body, url)), None)

    monkeypatch.setattr(chat_worker, "post_json", post)
    monkeypatch.setattr(context, "post_json", post)
    if context_tokens == 8:
        def no_serialization(body):
            pytest.fail("An over-window native token array must not be serialized")

        monkeypatch.setattr(chat_worker, "canonical_json", no_serialization)
        monkeypatch.setattr(context, "canonical_json", lambda body: (
            no_serialization(body) if "input_ids" in body else canonical_json(body)))
    result = chat_worker.exchange(ep, req)
    assert len(wire.suffixes) == 1
    if context_tokens == 8:
        assert isinstance(result, ModelFailure) and result.kind == "context_limit"
        assert not wire.bodies  # No model generation with a full window.
    else:
        assert isinstance(result, ModelReply)
        assert wire.bodies[0]["input_ids"] == [10, 11, 12, 20, 99, 500, 501, 502]
        if context_tokens is not None:
            assert wire.bodies[0]["max_tokens"] == 4
            assert result.usage["context_budget"]["input_tokens"] == 8


@pytest.mark.isolation
def test_native_prefix_crosses_real_http_and_worker_boundary(tmp_path):
    from test_chat_transport import send, server

    from behave.agent.process_model import ProcessModelBackend

    wire = Wire()

    def respond(handler, requests, stopped):
        send(handler, wire.reply(requests[-1][2], handler.path))

    with server(respond, token_count=None) as (url, requests):
        ep = replace(endpoint(), url=url)
        journal, _, _ = run_three(tmp_path, ep, ProcessModelBackend(ep.command()))
    check_history(journal, wire)
    assert sum(path == "/tokenize" for path, _, _ in requests) == 3
