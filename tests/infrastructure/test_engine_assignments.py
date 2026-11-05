"""Shared Eval/Train coordinator routing; no GPU or hosted model calls."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from threading import Barrier

import pytest

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.providers.engine_assignments import EngineAssignments, engine_assignments

URLS = tuple(f"http://127.0.0.1:{32000 + i}/v1/chat/completions" for i in range(8))


def test_fifty_concurrent_attempts_are_balanced_over_eight_engines():
    allocator = EngineAssignments(URLS)
    assigned, release = Barrier(51), Barrier(51)

    def hold(i):
        with allocator.acquire(str(i)) as url:
            assigned.wait(timeout=10)
            release.wait(timeout=10)
            return url

    with ThreadPoolExecutor(max_workers=50) as workers:
        futures = [workers.submit(hold, i) for i in range(50)]
        assigned.wait(timeout=10)
        assert sorted(allocator.active.values()) == [6] * 6 + [7] * 2
        release.wait(timeout=10)
        assert {f.result(timeout=10) for f in futures} == set(URLS)
    assert not allocator.sessions
    assert sum(allocator.active.values()) == 0


def test_assignment_survives_tool_gaps_and_shared_session_holders():
    allocator = EngineAssignments(URLS[:2])
    with allocator.acquire("slow") as slow:
        with allocator.acquire("same-round-fast") as fast:
            assert fast != slow
            with allocator.acquire("slow") as repeated:
                assert repeated == slow
                assert sum(allocator.active.values()) == 2
        # The first session remains active while its agent executes tools.
        with allocator.acquire("next") as next_url:
            assert next_url == fast
    assert allocator.sessions == {}


def test_exception_releases_assignment_and_round_owners_are_independent():
    first = ResourcePool(AdmissionPolicy("first-round", 50))
    second = ResourcePool(AdmissionPolicy("second-round", 50))
    pool = engine_assignments(first, URLS)
    assert pool is engine_assignments(first, URLS[::-1])
    assert pool is not engine_assignments(second, URLS)
    with pytest.raises(RuntimeError, match="cancelled"), pool.acquire("cancelled"):
        raise RuntimeError("cancelled")
    assert sum(pool.active.values()) == 0


def test_broker_assignment_must_belong_to_the_pinned_engine_pool():
    from test_chat_wire import endpoint, request

    ep = endpoint(routing="attempt", worker_urls=URLS)
    assert ep.request_url(request(ep), worker_url=URLS[-1]) == URLS[-1]
    with pytest.raises(ValueError, match="not in"):
        ep.request_url(request(ep), worker_url="http://127.0.0.1:9/v1/chat/completions")


@pytest.mark.isolation
def test_real_relays_balance_and_keep_raw_training_tokens(tmp_path):
    from test_chat_transport import send, server
    from test_model_relay import make
    from test_native_chat_tokens import native

    from behave.agent.model import ModelFailure, ModelReply
    from behave.agent.relay_wire import RelayModelBackend
    from behave.core.records import canonical_json, thaw_json
    from behave.train.training_data import NativeChatVerifier, TokenTrace

    base, packet = native("sglang")
    base = replace(base, tokens=replace(base.tokens, runtime="sglang-slime"))
    del packet["choices"][0]["response_token_ids"]
    reject = [False]

    def answer(handler, calls, stopped):
        sent = calls[-1][2]
        assert sent["return_prompt_token_ids"] is True
        assert sent["return_meta_info"] is True
        send(handler, {"error": {"code": "busy"}} if reject[0] else packet,
             status=429 if reject[0] else 200)

    with server(answer) as (a, calls_a), server(answer) as (b, calls_b):
        endpoint = replace(base, url=a, routing="attempt", worker_urls=(a, b))
        command = endpoint.command()
        shared = ResourcePool(AdmissionPolicy("eval-and-train-models", 50))
        first, request_a, _ = make(tmp_path / "a", command, shared, policy=endpoint.model_policy())
        second, request_b, _ = make(tmp_path / "b", command, shared, identity="b", policy=endpoint.model_policy())
        with second, ExitStack() as scope:
            scope.enter_context(first)
            client_a, client_b = RelayModelBackend(first.endpoint), RelayModelBackend(second.endpoint)
            result_a = client_a.generate(request_a)
            result_b = client_b.generate(request_b)
            assert type(result_a) is type(result_b) is ModelReply
            assert result_a.usage["routing"]["url"] != result_b.usage["routing"]["url"]
            assert result_a.usage["routing"]["policy"] == "least-active-attempts"
            for req, result in ((request_a, result_a), (request_b, result_b)):
                assert result.raw == canonical_json(packet)
                NativeChatVerifier(endpoint).verify(
                    TokenTrace.from_dict(thaw_json(result.token_data)), req, result.raw)
            original_url = result_a.usage["routing"]["url"]
            # Replay reads the ledger, without routing or another generation.
            assert client_a.generate(request_a) == result_a
            assert len(calls_a) + len(calls_b) == 2
            later = replace(request_a, call_id="a:1:0", turn=1)
            assert client_a.generate(later).usage["routing"]["url"] == original_url
            reject[0] = True
            failed = client_a.generate(replace(later, call_id="a:2:0", turn=2))
            assert type(failed) is ModelFailure
            assert failed.usage["routing"]["url"] == original_url
            reject[0] = False
            scope.close()  # Release the first attempt, while the other still lives.
            third, request_c, _ = make(tmp_path / "c", command, shared, identity="c", policy=endpoint.model_policy())
            with third:
                result_c = RelayModelBackend(third.endpoint).generate(request_c)
                assert result_c.usage["routing"]["url"] == original_url
        assert sum(engine_assignments(shared, (a, b)).active.values()) == 0


@pytest.mark.isolation
def test_training_rollout_uses_the_same_attempt_lifetime_allocator(tmp_path, publication, monkeypatch):
    from test_native_chat_tokens import native_training_run

    original = EngineAssignments.acquire
    acquired = []

    def observe(self, key):
        acquired.append((self, key))
        return original(self, key)

    monkeypatch.setattr(EngineAssignments, "acquire", observe)
    # Full run_rollouts -> run_batch -> supervised solver -> ModelRelay,
    # with native IDs/logprobs verified by the ordinary training report loader.
    native_training_run(tmp_path, publication, pooled=True)
    assert len(acquired) == 1  # One reservation, not one for every model turn.
    assert sum(acquired[0][0].active.values()) == 0
