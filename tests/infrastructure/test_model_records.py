"""Provider-neutral records preserve evidence, not executable guesses."""

from dataclasses import replace

import pytest

from behave.agent.model import Charge, ModelFailure, ModelReply, ModelRequest, ToolCall
from behave.core.records import RecordError, canonical_json, parse_json


@pytest.mark.parametrize("amount", ["0" * 300 + "1.25", "1e-120", "1e60", "0e999999"])
def test_charge_preserves_valid_decimal_text_without_application_numeric_caps(amount):
    charge = Charge(amount, "USD", "fixture")
    assert Charge(**parse_json(canonical_json(charge.as_dict()))) == charge
    assert charge.amount == amount


@pytest.mark.parametrize("amount", [True, None, 1.0, "", "NaN", "sNaN", "Infinity", "-1"])
def test_charge_requires_finite_nonnegative_decimal_text(amount):
    with pytest.raises(ValueError):
        Charge(amount, "USD", "fixture")


@pytest.mark.parametrize("delay", [-1, True, 1.5, 10**400])
def test_failure_delay_requires_nonnegative_integer_representable_by_native_clock(delay):
    with pytest.raises(ValueError):
        ModelFailure("retryable", "busy", retry_after_seconds=delay)


def test_tool_call_preserves_raw_arguments_and_rejects_conflicting_executable_values():
    raw = '{ "x": 1, "nested": [false, 0.0] }'
    call = ToolCall("a", "fixture", {"x": 1, "nested": [False, 0.]}, raw)
    assert ToolCall.from_dict(parse_json(canonical_json(call.as_dict()))) == call
    for arguments in ({"x": True, "nested": [False, 0.]}, {"x": 1, "nested": [False, 0]}):
        with pytest.raises(ValueError, match="disagree"):
            replace(call, arguments=arguments)
    broken = ToolCall("b", "fixture", {}, '{"unfinished":', "invalid-json")
    assert ToolCall.from_dict(broken.as_dict()) == broken
    with pytest.raises(ValueError):
        replace(broken, arguments={"inferred": 1})


def test_request_reply_and_failure_roundtrip_freeze_payloads_and_keep_views_separate():
    payload = {"nested": [1]}
    request = ModelRequest("a:0:0", 0, 0, (payload,), (payload,), payload, 10)
    reply = ModelReply("multiline\nreply", usage=payload, raw=b"raw\x00\xff", token_data=payload,
                       continuation=payload, finish_reason="stop")
    failure = ModelFailure("unknown", "disconnected", payload, reply.raw)
    payload["nested"].append(2)
    assert request.messages[0]["nested"] == reply.usage["nested"] == failure.usage["nested"] == (1,)
    assert ModelRequest.from_dict(parse_json(canonical_json(request.as_dict()))) == request
    assert ModelReply.from_dict(parse_json(canonical_json(reply.as_dict())), raw=reply.raw) == reply
    assert ModelFailure.from_dict(parse_json(canonical_json(failure.as_dict())), raw=failure.raw) == failure
    message = reply.message()
    assert set(message) == {"role", "content", "calls", "continuation"}
    message["continuation"]["nested"].append(3)
    assert reply.continuation["nested"] == (1,)
    assert "raw" not in reply.as_dict() and reply.charge is failure.charge is None


@pytest.mark.parametrize("reason,exclusion", [("length", "output-length"), ("context_limit", "context-limit")])
def test_truncated_reply_can_only_preserve_nonexecutable_call_evidence(reason, exclusion):
    broken = ToolCall("a", "fixture", {}, '{"partial":', "truncated")
    reply = ModelReply("partial", (broken,), training_exclusion=exclusion, finish_reason=reason)
    assert ModelReply.from_dict(reply.as_dict(), raw=b"") == reply
    eligible = replace(reply, training_exclusion=None)
    assert eligible.message() == reply.message()
    assert eligible.finish_reason == reply.finish_reason
    with pytest.raises(ValueError):
        replace(reply, calls=(ToolCall("a", "fixture", {}),))
    with pytest.raises(ValueError):
        replace(reply, finish_reason="stop")
    with pytest.raises(ValueError):
        ModelReply("", finish_reason="tool_calls")
    with pytest.raises(RecordError):
        ModelReply("", (broken, broken))


@pytest.mark.parametrize("field,value", [("turn", True), ("dispatch", -1), ("timeout_seconds", 0)])
def test_request_counters_remain_typed_and_nonnegative(field, value):
    with pytest.raises(ValueError):
        replace(ModelRequest("a", 0, 0, (), (), {}, 10), **{field: value})
