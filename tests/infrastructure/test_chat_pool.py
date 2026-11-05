"""Fixed worker pools preserve native extensions and retry/session identity."""

from dataclasses import replace

import pytest
from test_chat_wire import endpoint, request

from behave.agent.providers.local_chat import LocalChatEndpoint

WORKERS = ("http://127.0.0.1:15000/v1/chat/completions", "http://127.0.0.1:15002/v1/chat/completions")


def test_direct_pool_is_canonical_bound_and_sticky_across_turns_and_retries():
    ep = endpoint(routing="attempt", worker_urls=WORKERS)
    assert ep.as_dict()["schema"] == "behave.local-chat/8"
    assert LocalChatEndpoint.from_dict(ep.as_dict()) == ep
    assert replace(ep, worker_urls=WORKERS[::-1]) == ep
    assert replace(ep, worker_urls=WORKERS[:1]).identity != ep.identity
    req = request(ep)
    selected = ep.request_url(req)
    assert selected in WORKERS
    for turn, dispatch in ((0, 1), (1, 0), (19, 7)):
        assert ep.request_url(replace(req, turn=turn, dispatch=dispatch,
                                     call_id=f"test:{turn}:{dispatch}")) == selected
    assert {ep.request_url(replace(req, call_id=f"candidate-{i}:0:0")) for i in range(100)} == set(WORKERS)
    with pytest.raises(ValueError, match="canonical"):
        ep.request_url(replace(req, call_id="bad-id"))
    assert endpoint().request_url(req) == endpoint().url


@pytest.mark.parametrize("workers", [WORKERS * 2, ("http://8.8.8.8:80/v1/chat/completions",),
    ("http://localhost:80/v1/chat/completions",), ("http://127.0.0.1:80/wrong",),
    ("http://user:pass@127.0.0.1:80/v1/chat/completions",), (True,), WORKERS * 129, list(WORKERS)])
def test_pool_never_accepts_ambient_or_ambiguous_targets(workers):
    with pytest.raises((ValueError, TypeError)):
        endpoint(routing="attempt", worker_urls=workers)


def test_pool_requires_affinity_and_nonempty_versioned_wire():
    with pytest.raises(ValueError, match="affinity"):
        endpoint(worker_urls=WORKERS)
    row = endpoint(routing="attempt", worker_urls=WORKERS).as_dict()
    row["worker_urls"] = []
    with pytest.raises(ValueError, match="explicit worker"):
        LocalChatEndpoint.from_dict(row)


@pytest.mark.isolation
def test_real_http_pool_preserves_native_ids_and_does_not_retry_on_another_worker():
    from test_chat_transport import send, server
    from test_native_chat_tokens import native

    from behave.agent.model import ModelFailure, ModelReply
    from behave.agent.providers.chat_worker import exchange

    base, packet = native("sglang")
    base = replace(base, tokens=replace(base.tokens, runtime="sglang-slime"))
    del packet["choices"][0]["response_token_ids"]
    reject = [False]

    def answer(handler, requests, stopped):
        body = requests[-1][2]
        assert body["return_prompt_token_ids"] is True and body["return_meta_info"] is True
        send(handler, {"error": {"code": "busy"}} if reject[0] else packet,
             status=429 if reject[0] else 200)

    with server(answer) as (a, calls_a), server(answer) as (b, calls_b):
        ep = replace(base, routing="attempt", worker_urls=(a, b))
        req = request(ep)
        for turn in range(3):
            out = exchange(ep, replace(req, turn=turn, call_id=f"test:{turn}:0"))
            assert type(out) is ModelReply
            assert out.token_data["schema"] == "behave.token-trace/1"
            assert tuple(out.token_data["response_ids"]) == tuple(range(20, 25))
        assert sorted((len(calls_a), len(calls_b))) == [0, 3]
        reject[0] = True
        out = exchange(ep, req)
        assert type(out) is ModelFailure
        assert sorted((len(calls_a), len(calls_b))) == [0, 4]


def test_record_error_retains_code_across_trusted_native_exception_transport():
    import pickle

    from behave.core.records import RecordError

    original = RecordError("training-tokens-unavailable", "missing native evidence")
    restored = pickle.loads(pickle.dumps(original))
    assert type(restored) is RecordError
    assert restored.code == original.code and str(restored) == str(original)
