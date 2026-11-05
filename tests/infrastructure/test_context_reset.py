"""Opt-in window reset keeps task, workspace, turn budget and sampled history."""

from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_attempts import Scripted, call, setup

from behave.agent import AttemptSpec, run_attempt
from behave.agent.model import ModelFailure, ModelReply, ToolCall
from behave.agent.providers.http_errors import http_failure
from behave.agent.providers.wire import history
from behave.agent.tools import Tool, ToolResult, file_tools, object_schema
from behave.agent.trajectory import trajectory
from behave.agent.workspace import Workspace
from behave.core.records import canonical_json, parse_json

NOTICE = "Fixture reset reminder: inspect the unchanged workspace and continue."
OPENING = ({"role": "system", "content": "solver"}, {"role": "user", "content": "original task"})


def kwargs(journal, spec):
    return dict(journal=journal, spec=replace(spec, context_reset_message=NOTICE),
                initial_messages=OPENING, tools=file_tools())


def test_optional_reset_roundtrips_without_changing_old_configurations(tmp_path):
    _, spec = setup(tmp_path)
    old = spec.as_dict()
    assert "context_reset_message" not in old
    assert AttemptSpec.from_dict(old).as_dict() == old
    new = replace(spec, context_reset_message=NOTICE)
    assert AttemptSpec.from_dict(new.as_dict()) == new


@pytest.mark.parametrize("runtime_turn_status", [True, False])
@pytest.mark.parametrize("http_rejection", [False, True])
def test_input_overflow_retries_same_turn_after_full_reset_and_replays(tmp_path, runtime_turn_status,
                                                                     http_rejection):
    journal, spec = setup(tmp_path)
    spec = replace(spec, max_turns=3, model_policy={"model": "fixture", "native_continuation": True})
    sampled = {"schema": "behave.token-trace/1"}
    failure = ModelFailure("context_limit", "full")
    if http_rejection:
        body = {"object": "error", "type": "BadRequestError", "code": 400,
                "message": "Input length (131071 tokens) exceeds the maximum allowed length (131068 tokens). "
                           "Use a shorter input or enable --allow-auto-truncate."}
        failure = http_failure("chat", 400, body, canonical_json(body), {}, None)
    backend = Scripted(replace(call("write_file", {"path": "a", "content": "saved"}), token_data=sampled),
        failure,
        call("read_file", {"path": "a"}), ModelReply("done"))
    options = {**kwargs(journal, spec), "runtime_turn_status": runtime_turn_status}
    result = run_attempt(backend=backend, **options)
    assert result.turns == 3 and result.reason == "stopped"
    before, after = backend.requests[1:3]
    assert (before.turn, before.dispatch, after.turn, after.dispatch) == (1, 0, 1, 1)
    assert before.native_prefix is not None and after.native_prefix is None
    assert after.messages[:2] == OPENING
    assert after.messages[2] == {"role": "user", "content": NOTICE}
    assert len(after.messages) == 3 + runtime_turn_status
    if runtime_turn_status:
        assert after.messages[-1] == before.messages[-1]
        assert "turn 2 of 3" in after.messages[-1]["content"]
    assert backend.requests[-1].messages[-1 - runtime_turn_status]["content"]["content"] == "saved"
    assert len(trajectory(journal).turns) == 3
    assert sum(e.kind == "context_reset" for e in journal.load()) == 1
    assert not any(e.kind == "context_evicted" for e in journal.load())
    for request in backend.requests:
        history(SimpleNamespace(identity=None, model="fixture"), request)
    assert run_attempt(backend=Scripted(), **options) == result


@pytest.mark.parametrize("finish_reason,reset", [("context_limit", True), ("length", False)])
def test_output_boundary_preserves_prior_tool_effects_and_continues(tmp_path, finish_reason, reset):
    journal, spec = setup(tmp_path)
    backend = Scripted(call("write_file", {"path": "a", "content": "saved"}),
                       ModelReply("unfinished", finish_reason=finish_reason),
                       call("read_file", {"path": "a"}), ModelReply("done"))
    result = run_attempt(backend=backend, **kwargs(journal, spec))
    assert result.reason == "stopped" and result.turns == 4
    request = backend.requests[2]
    assert request.messages[:2] == OPENING
    if reset:
        assert [m["role"] for m in request.messages] == ["system", "user", "user", "user"]
        events = [e.kind for e in journal.load()]
        assert events.index("tool_settled") < events.index("context_reset")
    else:
        assert any(m["role"] == "assistant" for m in request.messages)
        assert not any(e.kind == "context_reset" for e in journal.load())
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert workspace.read("a") == b"saved"
    assert backend.requests[-1].messages[-2]["content"]["content"] == "saved"


def test_completed_terminal_submit_finishes_without_an_extra_context_request(tmp_path):
    journal, spec = setup(tmp_path)
    terminal = Tool("submit", "fixture submit", object_schema({}),
                    lambda *_: ToolResult({"submitted": True}, terminal=True), "fixture-submit", terminal=True)
    backend = Scripted(call("submit", {}))
    result = run_attempt(backend=backend, **{**kwargs(journal, spec), "tools": (terminal,)})
    assert result.reason == "final_action" and result.final_action["submitted"]
    assert not any(e.kind == "context_reset" for e in journal.load())


def test_repeated_resets_keep_original_turn_budget_and_partial_calls_are_not_run(tmp_path):
    journal, spec = setup(tmp_path)
    effects = []
    tool = Tool("write", "fixture", object_schema({}),
                lambda *_: effects.append("ran") or ToolResult({}), "fixture-write")
    partial = ToolCall("partial", "write", {}, raw_arguments="{", argument_error="truncated")
    backend = Scripted(ModelReply("unfinished", finish_reason="context_limit"),
                       ModelReply("unfinished", (partial,), finish_reason="context_limit"))
    options = {**kwargs(journal, replace(spec, max_turns=2)), "tools": (tool,)}
    result = run_attempt(backend=backend, **options)
    assert result.reason == "turn_limit" and result.turns == 2 and not effects
    assert "turn 2 of 2" in backend.requests[-1].messages[-1]["content"]
    assert sum(e.kind == "context_reset" for e in journal.load()) == 2
    assert [turn.exclusion for turn in trajectory(journal).turns] == [None, None]
    assert run_attempt(backend=Scripted(), **options) == result


@pytest.mark.parametrize("with_history", [False, True])
def test_oversized_opening_or_reset_input_stops_instead_of_resetting_forever(tmp_path, with_history):
    journal, spec = setup(tmp_path)
    replies = [ModelFailure("context_limit", "full")]
    if with_history:
        replies = [call("write_file", {"path": "a", "content": "saved"}),
                   ModelFailure("context_limit", "full"), *replies]
    backend = Scripted(*replies)
    result = run_attempt(backend=backend, **kwargs(journal, spec))
    assert result.reason == "context_limit" and result.turns == int(with_history)
    assert sum(e.kind == "context_reset" for e in journal.load()) == int(with_history)


@pytest.mark.parametrize("transport", ["in_process", pytest.param("process", marks=pytest.mark.isolation)])
def test_native_turns_pack_with_exact_targets_on_each_side_of_reset(tmp_path, monkeypatch, transport):
    from test_native_continuation import Wire, endpoint
    from test_slime_packing import assert_exact, payload

    from behave.agent.providers import chat_worker, context
    from behave.agent.providers.http_transport import HttpResponse
    from behave.core.records import canonical_json
    from behave.train.slime_rollout import coalesce_payloads

    class ResetWire(Wire):
        def reply(self, body, path):
            reset_input = any(m.get("content") == NOTICE for m in body.get("messages", ()))
            if path.endswith("/tokenize") and reset_input:
                return {"count": 5, "tokens": [10, 11, 70, 71, 72]}
            row = super().reply(body, path)
            if path.endswith("/tokenize"):
                return row
            choice = row["choices"][0]
            if len(self.bodies) == 1:
                outputs = list(range(20, 32))
                self.outputs[-1] = outputs
                choice["meta_info"]["output_token_logprobs"] = [[-.25, token, None] for token in outputs]
                choice["finish_reason"] = "length"
                row["usage"]["completion_tokens"] = len(outputs)
            elif reset_input and "input_ids" not in body:
                self.prefixes[-1] = [10, 11, 70, 71, 72]
                choice["prompt_token_ids"] = self.prefixes[-1]
                row["usage"]["prompt_tokens"] = 5
            return row

    wire, ep = ResetWire(), endpoint()

    def post(url, packet, **_):
        return HttpResponse(200, canonical_json(wire.reply(parse_json(packet), url)), None)

    with ExitStack() as stack:
        if transport == "in_process":
            monkeypatch.setattr(chat_worker, "post_json", post)
            monkeypatch.setattr(context, "post_json", post)
            backend = SimpleNamespace(generate=lambda request: chat_worker.exchange(ep, request))
        else:
            from test_chat_transport import send, server

            from behave.agent.process_model import ProcessModelBackend

            def respond(handler, requests, stopped):
                send(handler, wire.reply(requests[-1][2], handler.path))

            url, _ = stack.enter_context(server(respond, token_count=None))
            ep = replace(ep, url=url)
            backend = ProcessModelBackend(ep.command())
        journal, _ = setup(tmp_path)
        spec = AttemptSpec("reset-native", "solver", ep.model_policy(), max_turns=3, context_tokens=14,
                           context_reset_message=NOTICE)
        options = dict(journal=journal, spec=spec, initial_messages=OPENING, tools=file_tools())
        result = run_attempt(backend=backend, **options)
    assert result.reason == "stopped" and result.turns == 3
    assert sum(e.kind == "context_reset" for e in journal.load()) == 1
    turns = trajectory(journal).turns
    assert [t.exclusion for t in turns] == [None, None, None]
    raw = []
    for t in turns:
        trace = t.token_data
        item = payload(t.turn, list(trace["input_ids"]), list(trace["response_ids"]))
        item["rollout_log_probs"] = list(trace["signals"]["old_log_probs"])
        raw.append(item)
    packed = coalesce_payloads(tuple(raw))
    assert len(packed) == 2
    assert_exact(raw, packed)
    assert sum(sum(item["loss_mask"]) for item in packed) == 18
    assert run_attempt(backend=Scripted(), **options) == result
