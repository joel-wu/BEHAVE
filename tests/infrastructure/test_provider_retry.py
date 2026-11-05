"""Retry certainty, server minimum waits, accounting and durable replay."""

from dataclasses import replace
from email.utils import formatdate

import pytest

from behave.agent import AttemptInterrupted, AttemptJournal, AttemptSpec, run_attempt
from behave.agent.model import Charge, ModelFailure, ModelReply, ModelRequest, ToolCall
from behave.agent.providers.hosted import DESTINATIONS
from behave.agent.providers.http_errors import http_failure
from behave.agent.retry import backoff_seconds, may_retry, retry_after_seconds
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.trajectory import trajectory
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore


def request():
    return ModelRequest("retry:0:0", 0, 0, (), (), {"generation_only": True}, 10)


def test_server_delay_is_never_shortened_and_dates_round_up():
    assert retry_after_seconds("900") == 900
    assert retry_after_seconds(formatdate(1000, usegmt=True), now=100.2) == 900
    assert retry_after_seconds("junk") == 0
    failure = ModelFailure("retryable", "busy", retry_after_seconds=900)
    assert backoff_seconds(failure, request()) == 900
    for dispatch in range(10):
        req = replace(request(), dispatch=dispatch)
        delay = backoff_seconds(ModelFailure("retryable", "busy"), req)
        assert min(60, 2**dispatch) <= delay <= 119
        assert delay == backoff_seconds(ModelFailure("retryable", "busy"), req)


def test_unrepresentable_numeric_retry_header_never_becomes_an_immediate_retry():
    body = {"error": {"type": "rate_limit_error"}}
    raw = canonical_json(body)
    charge = Charge("0.01", "USD", "fixture")
    failure = http_failure("chat", 429, body, raw, {"observed": 1}, charge, "9" * 5000)
    assert (failure.kind, failure.code) == ("permanent", "chat-retry-wait-unrepresentable")
    assert failure.raw == raw and failure.charge == charge and failure.usage["observed"] == 1
    assert not may_retry(failure, request(), uncertain_generation=True)
    assert retry_after_seconds("0" * 5000 + "7") == 7


def test_backoff_cap_comes_from_shared_defaults_without_a_separate_exponent_limit(monkeypatch):
    from behave.core import runtime_defaults as defaults

    monkeypatch.setattr(defaults, "AGENT_RETRY_BACKOFF_SECONDS", 5)
    failure = ModelFailure("retryable", "busy")
    for dispatch in (0, 1, 2, 3, 100000):
        req = replace(request(), dispatch=dispatch)
        base = min(5, 2 ** min(dispatch, 3))
        assert base <= backoff_seconds(failure, req) < 2 * base
        assert backoff_seconds(replace(failure, retry_after_seconds=900), req) == 900


def test_quota_is_not_rate_limiting_and_no_arbitrary_unknown_retries():
    raw = canonical_json({"error": {"code": "insufficient_quota"}})
    assert http_failure("openai", 429, {"error": {"code": "insufficient_quota"}}, raw, {}, None).kind == "permanent"
    for code in ("openai-response-invalid", "chat-model-mismatch", "model-worker-cleanup-incomplete"):
        assert not may_retry(ModelFailure("unknown", code), request(), uncertain_generation=True)
    failure = ModelFailure("unknown", "openai-exchange-interrupted")
    assert not may_retry(failure, request(), uncertain_generation=False)
    assert may_retry(failure, request(), uncertain_generation=True)
    assert may_retry(ModelFailure("unknown", "model-worker-timeout"), request(), uncertain_generation=True)
    assert not may_retry(failure, replace(request(), model_policy={}), uncertain_generation=True)
    with pytest.raises(ValueError):
        AttemptSpec("x", "test", {}, retry_uncertain_generation=True)


@pytest.mark.parametrize("provider", ["chat", *DESTINATIONS])
@pytest.mark.parametrize("status", [408, 500, 502, 503, 504, 529])
def test_all_http_adapters_share_opt_in_transient_retries(provider, status):
    raw = b'{"error":{"message":"temporarily unavailable"}}'
    charge = Charge("0.01", "USD", "fixture")
    failure = http_failure(provider, status, None, raw, {"observed": 1}, charge, "7")
    assert failure.kind == "unknown"
    assert may_retry(failure, request(), uncertain_generation=True)
    assert not may_retry(failure, request(), uncertain_generation=False)
    assert not may_retry(failure, replace(request(), model_policy={}), uncertain_generation=True)
    # Permission to retry is not evidence that the first call was free or unexecuted.
    assert failure.raw == raw and failure.charge == charge and failure.usage["observed"] == 1
    assert backoff_seconds(failure, request()) >= 7


@pytest.mark.parametrize("provider", ["chat", *DESTINATIONS])
@pytest.mark.parametrize("suffix", ["response-invalid", "model-mismatch", "http-501",
                                   "http-505", "http-599", "quota-exhausted"])
def test_provider_parity_does_not_enable_arbitrary_unknown_retries(provider, suffix):
    assert not may_retry(ModelFailure("unknown", f"{provider}-{suffix}"),
                         request(), uncertain_generation=True)


def test_unknown_provider_does_not_inherit_generation_retry_permission():
    assert not may_retry(ModelFailure("unknown", "other-http-503"), request(), uncertain_generation=True)
    assert not may_retry(ModelFailure("unknown", "other-exchange-interrupted"),
                         request(), uncertain_generation=True)


@pytest.mark.parametrize("provider", ["chat", *DESTINATIONS])
@pytest.mark.parametrize("status,body", [
    (401, {"error": {"code": "invalid_api_key"}}),
    (403, {"error": {"code": "permission_denied"}}),
    (429, {"error": {"code": "insufficient_quota"}}),
])
def test_provider_auth_and_quota_rejections_still_stop(provider, status, body):
    failure = http_failure(provider, status, body, canonical_json(body), {}, None, "7")
    assert failure.kind == "permanent"
    assert not may_retry(failure, request(), uncertain_generation=True)


@pytest.mark.parametrize("code", ["insufficient_quota", "credit_balance_exhausted",
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded"])
def test_openai_billing_codes_never_retry_even_without_quota_type(code):
    body = {"error": {"type": "rate_limit_error", "code": code}}
    raw = canonical_json(body)
    charge = Charge("0.001", "USD", "test/1")
    failure = http_failure("openai", 429, body, raw, {"observed": 1}, charge, "60")
    assert (failure.kind, failure.code, failure.retry_after_seconds) == ("permanent", "openai-quota-exhausted", 0)
    assert failure.raw == raw and failure.charge == charge and failure.usage["observed"] == 1
    assert not may_retry(failure, request(), uncertain_generation=True)


@pytest.mark.parametrize("status,error", [
    (429, {"type": "rate_limit_error", "details": {"error_code": "enforced_spend_limit_reached"}}),
    (402, {"type": "billing_error"}),
    (400, {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API. Please add credits."}),
    (400, {"type": "invalid_request_error", "message": "You have reached your specified API usage limits. Access resumes next month."}),
    (400, {"type": "invalid_request_error", "message": "You have reached your specified workspace API usage limits."}),
])
def test_anthropic_spend_caps_are_not_temporary_throttles(status, error):
    body = {"type": "error", "error": error}
    failure = http_failure("anthropic", status, body, canonical_json(body), {}, None, "60")
    assert (failure.kind, failure.code) == ("permanent", "anthropic-quota-exhausted")
    assert not may_retry(failure, request(), uncertain_generation=True)


@pytest.mark.parametrize("provider", ["fireworks", "anthropic"])
@pytest.mark.parametrize("body", [None, {"error": "payment required"}])
def test_payment_required_does_not_need_a_machine_readable_body(provider, body):
    failure = http_failure(provider, 402, body, b"payment required", {}, None)
    assert failure.kind == "permanent" and failure.code == provider + "-quota-exhausted"
    assert failure.charge is None  # Rejection is not evidence of a zero bill.


@pytest.mark.parametrize("details", [None, "quota", {"error_code": "tokens_per_minute"}, {"error_code": []}])
def test_real_rate_limit_with_other_details_still_uses_backoff(details):
    body = {"error": {"type": "rate_limit_error", "details": details}}
    failure = http_failure("anthropic", 429, body, canonical_json(body), {}, None, "7")
    assert failure.kind == "retryable" and failure.retry_after_seconds == 7


def test_unrelated_billing_mentions_do_not_override_other_error_types():
    body = {"error": {"type": "invalid_request_error", "message": "Unknown billing parameter"}}
    assert http_failure("anthropic", 400, body, canonical_json(body), {}, None).code == "anthropic-http-400"
    body = {"error": {"code": "insufficient_quota"}}
    assert http_failure("openai", 500, body, canonical_json(body), {}, None).kind == "unknown"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("message", [
    ("Requested token count exceeds the model's maximum context length of 8192 tokens. "
    "You requested a total of 9011 tokens: 6963 tokens from the input messages and "
    "2048 tokens for the completion. Please reduce the number of tokens in the input "
    "messages or the completion to fit within the limit."),
    "The input (8192 tokens) is longer than the model's context length (8192 tokens).",
    ("Input length (131071 tokens) exceeds the maximum allowed length (131068 tokens). "
     "Use a shorter input or enable --allow-auto-truncate."),
])
def test_native_context_errors_preserve_evidence_and_do_not_retry(nested, message):
    error = {"object": "error", "message": message, "type": "BadRequestError", "param": None, "code": 400}
    body = {"error": error} if nested else error
    raw = canonical_json(body)
    failure = http_failure("chat", 400, body, raw, {}, None)
    assert failure.kind == "context_limit" and failure.code == "chat-context-limit"
    assert failure.raw == raw and not may_retry(failure, request(), uncertain_generation=True)
    # An internal server error is not an ordinary budget termination.
    assert http_failure("chat", 500, body, raw, {}, None).kind == "unknown"


@pytest.mark.parametrize("body", [
    {"object": "error", "type": "BadRequestError", "code": 400,
     "message": "Unknown context_length parameter"},
    {"object": "error", "type": "BadRequestError", "code": 400, "message": None},
    {"object": "error", "type": "BadRequestError", "code": 400, "message": {"context": "long"}},
    {"error": {"type": "invalid_request_error", "message": "context configuration invalid"}},
    {"error": "context_length_exceeded"},
])
def test_other_bad_requests_are_not_context_terminations(body):
    assert http_failure("chat", 400, body, canonical_json(body), {}, None).kind == "permanent"


@pytest.mark.parametrize("code", ["context_length_exceeded", "context_window_exceeded", "prompt_too_long"])
def test_context_error_codes_work_in_both_envelopes(code):
    for body in ({"error": {"code": code}}, {"object": "error", "code": code}):
        assert http_failure("chat", 400, body, canonical_json(body), {}, None).kind == "context_limit"


@pytest.mark.parametrize("provider", ["chat", *DESTINATIONS])
def test_uncertain_generation_retry_keeps_cost_uncertainty_and_one_turn(tmp_path, provider):
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))

    class Backend:
        calls = 0

        def generate(self, req):
            self.calls += 1
            if req.dispatch == 0:
                return ModelFailure("unknown", f"{provider}-exchange-interrupted")
            return ModelReply("done")

    backend = Backend()
    kwargs = {"journal": journal, "backend": backend, "initial_messages": (),
              "spec": AttemptSpec("retry", "solver", {"generation_only": True}, retry_uncertain_generation=True)}
    result = run_attempt(**kwargs)
    assert result.turns == 1 and backend.calls == 2
    assert run_attempt(**kwargs) == result and backend.calls == 2
    ledger = trajectory(journal)
    assert len(ledger.turns) == 1 and ledger.unresolved_dispatches == 1 and ledger.unpriced_dispatches == 2


@pytest.mark.parametrize("provider", ["chat", *DESTINATIONS])
def test_uncertain_generation_retries_stop_at_the_configured_budget(tmp_path, provider):
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))

    class Backend:
        calls = 0

        def generate(self, req):
            self.calls += 1
            return ModelFailure("unknown", f"{provider}-http-503")

    backend = Backend()
    kwargs = {"journal": journal, "backend": backend, "initial_messages": (),
              "spec": AttemptSpec("retry", "solver", {"generation_only": True},
                                  max_retries=1, retry_uncertain_generation=True)}
    for _ in range(2):
        with pytest.raises(AttemptInterrupted, match=f"model-unknown:{provider}-http-503"):
            run_attempt(**kwargs)
        # Reopening the same exhausted attempt must not mint another retry budget.
        assert backend.calls == 2


@pytest.mark.parametrize("provider", ["deepseek", "openrouter"])
def test_transport_retry_and_replay_do_not_repeat_completed_tools(tmp_path, provider):
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    requests, effects = [], []

    def execute(workspace, arguments):
        effects.append("applied")
        return ToolResult({"status": "done"})

    class Backend:
        def generate(self, req):
            requests.append((req.turn, req.dispatch))
            if req.turn == 0:
                return ModelReply("", (ToolCall("effect-1", "effect", {}),))
            if req.dispatch == 0:
                return http_failure(provider, 503, None, b"unavailable", {}, None)
            return ModelReply("done")

    kwargs = {"journal": journal, "backend": Backend(), "initial_messages": (),
              "tools": (Tool("effect", "fixture action", object_schema({}), execute, "fixture"),),
              "spec": AttemptSpec("retry", "solver", {"generation_only": True},
                                  max_retries=1, retry_uncertain_generation=True)}
    result = run_attempt(**kwargs)
    assert result.turns == 2
    assert run_attempt(**kwargs) == result
    assert requests == [(0, 0), (1, 0), (1, 1)] and effects == ["applied"]


def test_uncertain_generation_stops_by_default(tmp_path):
    class Backend:
        def generate(self, req):
            return ModelFailure("unknown", "openai-exchange-interrupted")

    with pytest.raises(AttemptInterrupted):
        run_attempt(journal=AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects")),
                    backend=Backend(), spec=AttemptSpec("retry", "solver", {"generation_only": True}),
                    initial_messages=())
