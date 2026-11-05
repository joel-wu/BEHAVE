"""Native serving wire fixtures; no GPU/tokenizer and no fabricated text IDs."""

from dataclasses import replace

import pytest
from test_chat_wire import body, endpoint, request

from behave.agent.model import ModelReply
from behave.agent.providers import chat_worker
from behave.agent.providers.chat_wire import response
from behave.agent.providers.http_transport import HttpResponse
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.providers.native_tokens import NativeChatTokens, attach_tokens
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.train.training_data import NativeChatVerifier, TokenTrace


def native(runtime):
    config = NativeChatTokens(runtime, *(BlobRef.of(value) for value in (b"weights", b"tokenizer", b"template")))
    ep = endpoint(tokens=config)
    packet = body()
    choice = packet["choices"][0]
    inputs, outputs = list(range(10)), list(range(20, 25))
    if runtime in {"sglang", "sglang-slime"}:
        choice.update(
            prompt_token_ids=inputs,
            response_token_ids=outputs,
            meta_info={"output_token_logprobs": [[-0.5, token, None] for token in outputs]},
        )
    else:
        packet["prompt_token_ids"] = inputs
        choice.update(
            token_ids=outputs,
            logprobs={
                "content": [{"token": f"token_id:{token}", "logprob": -0.5, "top_logprobs": []} for token in outputs]
            },
        )
    return ep, packet


@pytest.mark.parametrize("runtime", ["sglang", "vllm"])
def test_native_verifier_rejects_input_plus_output_beyond_runtime_context(runtime):
    from behave.agent.providers.native_tokens import decode_tokens

    ep, packet = native(runtime)
    req = replace(request(ep), context_tokens=15)
    raw = canonical_json(packet)
    trace = TokenTrace.from_dict(decode_tokens(ep.tokens, req, raw))
    NativeChatVerifier(ep).verify(trace, req, raw)
    with pytest.raises(ValueError):
        decode_tokens(ep.tokens, replace(req, context_tokens=14), raw)


@pytest.mark.parametrize("runtime", ["sglang", "vllm"])
@pytest.mark.parametrize("seed", [17, 1 << 63])
def test_native_wire_preserves_operator_seed_without_deriving_it_from_attempt_ids(runtime, seed, monkeypatch):
    base, packet = native(runtime)
    configured = replace(base, parameters={**base.parameters, "seed": seed})
    sent = []

    def send(url, payload, **kwargs):
        sent.append(parse_json(payload))
        return HttpResponse(200, canonical_json(packet), None)

    monkeypatch.setattr(chat_worker, "post_json", send)
    first = request(configured)
    for change in ({}, {"call_id": "other:0:0"}, {"call_id": "test:1:0", "turn": 1},
                   {"call_id": "test:1:1", "turn": 1, "dispatch": 1}):
        assert type(chat_worker.exchange(configured, replace(first, **change))) is ModelReply
    assert [body["seed"] for body in sent] == [seed] * 4
    assert type(chat_worker.exchange(base, request(base))) is ModelReply
    assert "seed" not in sent[-1]
    # This validates transport only; a runtime's sampler must separately honor the seed.


@pytest.mark.parametrize("top_k", [-1, 1, 20, 1_000_001])
def test_explicit_top_k_reaches_local_server_and_changes_identity(top_k):
    from behave.agent.providers.chat_wire import request_body

    ordinary, _ = native("sglang")
    configured = replace(ordinary, parameters={**ordinary.parameters, "top_k": top_k})
    assert LocalChatEndpoint.from_dict(configured.as_dict()) == configured
    assert configured.identity != ordinary.identity
    assert parse_json(request_body(configured, request(configured)))["top_k"] == top_k


@pytest.mark.parametrize("top_k", [0, -2, True, 1.5, "20"])
def test_invalid_local_top_k_is_rejected(top_k):
    ordinary, _ = native("sglang")
    with pytest.raises(ValueError, match="top_k"):
        replace(ordinary, parameters={**ordinary.parameters, "top_k": top_k})


@pytest.mark.parametrize("runtime", ["sglang", "vllm"])
def test_native_profile_requests_and_verifies_exact_ids_and_probability_alignment(runtime, monkeypatch):
    ep, packet = native(runtime)
    req, raw = request(ep), canonical_json(packet)
    seen = []

    def post(url, payload, **kwargs):
        seen.append(parse_json(payload))
        return HttpResponse(200, raw, None)

    monkeypatch.setattr(chat_worker, "post_json", post)
    reply = chat_worker.exchange(ep, req)
    assert type(reply) is ModelReply and reply.raw == raw
    assert all(seen[0][key] == value for key, value in ep.tokens.parameters().items())
    trace = TokenTrace.from_dict(thaw_json(reply.token_data))
    assert trace.input_ids == tuple(range(10)) and trace.response_ids == tuple(range(20, 25))
    assert trace.full_loss_mask == (0,) * 10 + (1,) * 5
    assert trace.signals["old_log_probs"] == (-0.5,) * 5
    NativeChatVerifier(ep).verify(trace, req, raw)
    assert LocalChatEndpoint.from_dict(ep.as_dict()) == ep
    assert ep.as_dict()["schema"] == "behave.local-chat/6" and ep.model_policy()["generation_only"] is True
    ordinary = replace(ep, tokens=None)
    assert ordinary.as_dict()["schema"] == "behave.local-chat/5" and "tokens" not in ordinary.as_dict()
    assert ordinary.identity != ep.identity


@pytest.mark.parametrize("runtime", ["sglang", "vllm"])
@pytest.mark.parametrize("mutation", ["missing", "probability-id", "probability-length", "boolean-id",
                                      "negative-id"])
def test_inference_reply_survives_invalid_native_metadata_without_training_readiness(runtime, mutation):
    ep, packet = native(runtime)
    choice = packet["choices"][0]
    token_owner, token_key = (choice, "response_token_ids") if runtime == "sglang" else (choice, "token_ids")
    if mutation == "missing":
        token_owner.pop(token_key)
    elif mutation == "boolean-id":
        token_owner[token_key][0] = True
    elif mutation == "negative-id":
        token_owner[token_key][0] = -1
    else:
        rows = choice["meta_info"]["output_token_logprobs"] if runtime == "sglang" else choice["logprobs"]["content"]
        if mutation == "probability-length":
            rows.pop()
        elif runtime == "sglang":
            rows[0][1] = 99
        else:
            rows[0]["token"] = "token_id:99"
    req, raw = request(ep), canonical_json(packet)
    reply = attach_tokens(ep.tokens, req, response(ep, 200, raw))
    assert type(reply) is ModelReply and reply.content == "done"
    assert reply.token_data["schema"] == "behave.native-token-unavailable/1"
    assert reply.training_exclusion is None and reply.raw == raw and reply.charge is not None


@pytest.mark.parametrize("runtime", ["sglang", "sglang-slime", "vllm"])
@pytest.mark.parametrize("reported_count", [3, 8])
def test_usage_count_difference_preserves_native_tokens_and_context_budget(runtime, reported_count):
    from behave.agent.providers.context import record_budget

    ep, packet = native(runtime)
    packet["usage"]["completion_tokens"] = reported_count
    packet["usage"]["total_tokens"] = 10 + reported_count
    req = replace(request(ep), context_tokens=15)
    raw = canonical_json(packet)
    reply = attach_tokens(ep.tokens, req, response(ep, 200, raw))
    reply = record_budget(reply, {"limit": 15, "input_tokens": 10, "max_output_tokens": 5})
    assert type(reply) is ModelReply and reply.training_exclusion is None
    trace = TokenTrace.from_dict(thaw_json(reply.token_data))
    assert trace.input_ids == tuple(range(10)) and trace.response_ids == tuple(range(20, 25))
    assert trace.full_loss_mask == (0,) * 10 + (1,) * 5
    assert trace.signals["old_log_probs"] == (-0.5,) * 5
    assert reply.usage["normalized"]["output_tokens"] == reported_count
    assert reply.raw == raw
    NativeChatVerifier(ep).verify(trace, req, raw)


@pytest.mark.parametrize("runtime", ["sglang", "vllm"])
def test_native_verifier_refuses_mask_provenance_request_and_raw_generation_changes(runtime):
    ep, packet = native(runtime)
    req, raw = request(ep), canonical_json(packet)
    reply = attach_tokens(ep.tokens, req, response(ep, 200, raw))
    trace = TokenTrace.from_dict(thaw_json(reply.token_data))
    for changes in (
        {"response_mask": (1, 0, 1, 1, 1)},
        {"input_ids": (99,) + trace.input_ids[1:]},
        {"response_ids": (99,) + trace.response_ids[1:]},
        {"checkpoint": BlobRef.of(b"other")},
        {"request": BlobRef.of(b"other request")},
        {"signals": {"old_log_probs": [-0.2] * 5}},
    ):
        with pytest.raises(RecordError, match="trace differs"):
            NativeChatVerifier(ep).verify(replace(trace, **changes), req, raw)
    with pytest.raises(ValueError, match="policy differs"):
        NativeChatVerifier(replace(ep, runtime_revision="changed")).verify(trace, req, raw)
    packet["choices"][0]["finish_reason"] = "length"
    raw = canonical_json(packet)
    assert attach_tokens(ep.tokens, req, response(ep, 200, raw)).training_exclusion is None
    NativeChatVerifier(ep).verify(trace, req, raw)


def test_clamped_vllm_probabilities_are_not_advertised_as_exact():
    ep, packet = native("vllm")
    packet["choices"][0]["logprobs"]["content"][0]["logprob"] = -9999.0
    req, raw = request(ep), canonical_json(packet)
    reply = attach_tokens(ep.tokens, req, response(ep, 200, raw))
    trace = TokenTrace.from_dict(thaw_json(reply.token_data))
    assert trace.response_ids and not trace.signals
    NativeChatVerifier(ep).verify(trace, req, raw)


@pytest.mark.isolation
@pytest.mark.parametrize("runtime", ["sglang", "vllm"])
def test_native_ids_cross_the_actual_http_and_supervised_broker_boundary(runtime):
    from test_chat_transport import send, server

    from behave.agent.process_model import ProcessModelBackend

    ep, packet = native(runtime)

    def answer(handler, requests, stopped):
        send(handler, packet)

    with server(answer) as (url, requests):
        ep = replace(ep, url=url)
        req = request(ep)
        reply = ProcessModelBackend(ep.command()).generate(req)
    assert type(reply) is ModelReply
    assert requests[0][2]["return_token_ids"] is True
    trace = TokenTrace.from_dict(thaw_json(reply.token_data))
    NativeChatVerifier(ep).verify(trace, req, reply.raw)
    assert trace.response_mask == (1,) * 5


def native_training_run(tmp_path, publication, *, checkpoint=None, pooled=False, on_prepared=None,
                        update_groups=None):
    """A real supervised HTTP rollout with fixture wire responses, not a model."""
    from test_chat_transport import call, send, server
    from test_evaluation import Judge, plan_for

    from behave.agent.admission import AdmissionPolicy
    from behave.agent.attempt import AttemptSpec
    from behave.agent.run_store import JournalDirectory
    from behave.data.published import load_published_task
    from behave.task_execution.solver import SolverJob
    from behave.train.plan import TrainingManifest, TrainingPlan
    from behave.train.rollouts import run_rollouts
    from behave.train.training_data import TrainingRequirements

    ep, _ = native("sglang")
    ep = replace(ep, parameters={**ep.parameters, "temperature": 1, "top_p": 1, "top_k": -1})
    if checkpoint is not None:
        ep = replace(ep, runtime_revision=checkpoint.sha256, tokens=replace(ep.tokens, checkpoint=checkpoint))

    def answer(handler, requests, stopped):
        if handler.path == "/tokenize":
            send(handler, {"count": 10})
            return
        generations = sum(path == "/v1/chat/completions" for path, _, _ in requests)
        _, packet = native("sglang")
        packet["usage"]["prompt_tokens_details"] = {"cached_tokens": 0 if generations == 1 else 2}
        choice = packet["choices"][0]
        if generations == 1:
            choice.update(
                finish_reason="tool_calls",
                message={"role": "assistant", "content": "", "tool_calls": [call("list", "run_command", {"command": "ls"})]},
            )
        elif generations == 2:
            choice["finish_reason"] = "length"
        send(handler, packet)

    run = JournalDirectory(tmp_path / "training")
    with server(answer, token_count=None) as (url, requests):
        ep = replace(ep, url=url, **({"routing": "attempt", "worker_urls": (url,)} if pooled else {}))
        public = load_published_task(publication.reader, publication.ref, "fixture_counter").public
        job = SolverJob(
            public,
            AttemptSpec(
                "native-training",
                "solver",
                ep.model_policy(),
                max_turns=4,
                request_timeout_seconds=15,
            ),
            ep.command(),
        )
        manifest = publication.put(TrainingManifest(public.release, ((public.task_id, public.revision),)).to_bytes())
        requirements = TrainingRequirements(
            ep.tokens.adapter,
            ep.tokens.checkpoint,
            ep.tokens.tokenizer,
            ep.tokens.template,
            required_signals=("old_log_probs",),
        )
        plan = TrainingPlan(
            plan_for(job, model_admission=AdmissionPolicy("native-generation", 1)),
            manifest,
            requirements,
            ("fixed-candidate-group",),
        )
        verifier = NativeChatVerifier(ep)
        judge = Judge()
        result = run_rollouts(
            plan=plan, approved_manifest=manifest, reader=publication.reader, run=run, judge=judge, verifier=verifier,
            on_prepared=on_prepared, update_groups=update_groups,
        )
    assert sum(path == "/tokenize" for path, _, _ in requests) == 3
    assert sum(path == "/v1/chat/completions" for path, _, _ in requests) == 3
    assert not judge.requests  # Normal stop without submission = zero.
    return run, manifest, verifier, result, ep


@pytest.mark.isolation
def test_native_multiturn_training_export_keeps_verified_truncated_prefix(
    tmp_path, publication, monkeypatch
):
    from test_infra_cli import command

    from behave.core.storage import DirectoryStore
    from behave.train.learning_report import load_training_report

    run, manifest, verifier, result, ep = native_training_run(tmp_path, publication)
    assert result["rewarded"] == 1 and result["eligible_turns"] == 3
    (row,) = result["candidates"]
    assert row["group_id"] == "fixed-candidate-group" and row["examples"]["reward"] == 0
    assert [turn["status"] for turn in row["examples"]["turns"]] == ["eligible"] * 3
    assert row["examples"]["turns"][1]["reason"] is None
    head, closure = run.seal("training")
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(run.objects, closure)
    assert load_training_report(exported, head, approved_manifest=manifest, verifier=verifier) == result
    from behave.train.slime_launch import preset
    from behave.train.slime_rollout import from_training_report

    verified = []
    accounted = []
    from behave.task_execution import batch as batch_module
    from behave.train import training_data as training_data_module
    original_accounting = batch_module.load_accounting

    def count_accounting(reader, result):
        accounted.append(result.attempt_id)
        return original_accounting(reader, result)

    monkeypatch.setattr(batch_module, "load_accounting", count_accounting)
    monkeypatch.setattr(training_data_module, "load_accounting", count_accounting)
    original_verify = NativeChatVerifier.verify

    def count_verification(self, trace, request, raw_response):
        verified.append(trace.request)
        return original_verify(self, trace, request, raw_response)

    monkeypatch.setattr(NativeChatVerifier, "verify", count_verification)
    batch = from_training_report(exported, head, approved_manifest=manifest, verifier=verifier,
                                 recipe=preset("ppo"))
    # Ordinary training consumes the producer export, with no audit replay.
    assert verified == [] and accounted == []
    assert result["eligible_turns"] == 3
    assert [p["metadata"]["turn"] for p in batch.payloads] == [0, 1, 2]
    # Packing already reads each native response for its training tokens. Reuse
    # its recorded cache counters without replaying HTTP or fabricating misses.
    assert [p["metadata"]["behave_prefix_cache"] for p in batch.payloads] == [
        {"cached_tokens": cached, "total_prompt_tokens": 10} for cached in (0, 2, 2)
    ]
    assert all(p["metadata"]["behave_cache_measured_turns"] == 1 for p in batch.payloads)
    assert len(batch.candidates) == 1 and batch.candidates[0]["excluded_turns"] == 0
    assert all(p["reward"] == 0 and p["rollout_log_probs"] == [-.5] * 5 for p in batch.payloads)
    manifest_file, endpoint_file, head_file = (
        tmp_path / name for name in ("manifest.json", "endpoint.json", "head.json")
    )
    manifest_file.write_bytes(publication.reader.read(manifest))
    endpoint_file.write_bytes(canonical_json(ep.as_dict()))
    head_file.write_bytes(canonical_json(head.as_dict()))
    inspected = command(
        "inspect",
        "--kind",
        "train",
        "--objects",
        exported.directory,
        "--record",
        head_file,
        "--approved-manifest",
        manifest_file,
        "--native-endpoint",
        endpoint_file,
    )
    assert inspected.returncode == 0, inspected.stderr + inspected.stdout
    assert parse_json(inspected.stdout) == result
