"""Money reservations survive concurrency, failures, lost delivery and cold load."""

import json
import multiprocessing
from contextlib import ExitStack
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest
from test_hosted_providers import body, credential_file, endpoint, request

from behave.agent import spending as spending_module
from behave.agent.attempt import AttemptInterrupted
from behave.agent.model import Charge, ModelFailure, ModelReply
from behave.agent.providers.hosted import HostedEndpoint
from behave.agent.providers.hosted_worker import exchange
from behave.agent.providers.http_transport import HttpResponse
from behave.agent.spending import SpendAccount, SpendPolicy
from behave.core import runtime_defaults as defaults
from behave.core.records import canonical_json


def account(path, limit="1"):
    return SpendAccount(SpendPolicy(str(path), "test", "USD", limit, 1000))


def charged(amount="0.2"):
    return ModelReply("ok", charge=Charge(amount, "USD", "price/1"))


CEILING = Charge("0.6", "USD", "price/1")


def test_operator_addition_preserves_original_account_and_call_receipts(tmp_path):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    budget.dispatch(req, CEILING, lambda _: charged("0.7"))
    assert budget.dispatch(replace(req, call_id="next"), CEILING,
                           lambda _: pytest.fail()).code == "model-budget-exhausted"
    original = budget.journal.load()
    assert budget.add_budget("500", authorization="user-20260917-500") == "501"
    assert budget.add_budget("500", authorization="user-20260917-500") == "501"
    assert budget.journal.load() == original
    cold = account(tmp_path / "budget")
    assert cold.summary()["limit"] == "501" and cold.summary()["spent"] == "0.7"
    assert spending_module.spending_snapshot(cold.run.directory)["limit"] == "501"
    assert cold.dispatch(req, CEILING, lambda _: pytest.fail("repeated paid call")) == charged("0.7")
    assert cold.dispatch(replace(req, call_id="next"), CEILING, lambda _: charged()) == charged()
    assert cold.summary()["spent"] == "0.9"
    with pytest.raises(ValueError, match="different addition"):
        cold.add_budget("600", authorization="user-20260917-500")


@pytest.mark.parametrize("amount", ["0", "-1"])
def test_operator_addition_requires_positive_amount(tmp_path, amount):
    with pytest.raises(ValueError):
        account(tmp_path / "budget").add_budget(amount, authorization="user-test")


def test_operator_addition_keeps_pending_call_reservation(tmp_path):
    budget, req = account(tmp_path / "budget"), request(endpoint())

    def generate(_request):
        assert budget.add_budget("500", authorization="user-live") == "501"
        current = budget.summary()
        assert current["pending"] == 1 and current["reserved"] == "0.6"
        return ModelFailure("unknown", "transport-interrupted")

    budget.dispatch(req, CEILING, generate)
    final = budget.summary()
    assert final["limit"] == "501" and final["reserved"] == "0.6"
    assert final["pending"] == 0 and final["unpriced"] == 1


@pytest.mark.parametrize("outcome", [charged(), charged("0.7"),
    ModelFailure("permanent", "openai-quota-exhausted"), ModelFailure("permanent", "openai-http-400")])
def test_live_spending_snapshot_reuses_balance_semantics_without_locking_or_writes(tmp_path, outcome):
    budget, req = account(tmp_path / "budget", "2"), request(endpoint())
    budget.dispatch(req, CEILING, lambda _: outcome)
    expected = budget.summary()
    path = budget.run.directory / "spending_cache.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    with budget.run.owner():
        observed = spending_module.spending_snapshot(budget.run.directory)
    assert {key: observed[key] for key in expected} == expected
    assert observed["provisional"] is True and observed["ledger_sequence"] >= 0
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_actual_cost_releases_only_unused_reservation_and_cold_replay_is_free(tmp_path):
    budget = account(tmp_path / "budget")
    req = request(endpoint())
    assert budget.dispatch(req, CEILING, lambda _: charged()) == charged()
    summary = account(tmp_path / "budget").summary()
    assert Decimal(summary["spent"]) == Decimal("0.2") and Decimal(summary["reserved"]) == 0
    assert account(tmp_path / "budget").dispatch(req, CEILING, lambda _: pytest.fail("redispatched")) == charged()
    with pytest.raises(ValueError, match="different request"):
        budget.dispatch(replace(req, messages=({"role": "user", "content": "changed"},)), CEILING, lambda _: charged())
    with pytest.raises(ValueError, match="configuration changed"):
        account(tmp_path / "budget", "2").summary()


def test_cached_admission_does_not_reopen_old_request_artifacts(tmp_path, monkeypatch):
    budget, req = account(tmp_path / "budget", "2"), request(endpoint())
    budget.dispatch(req, CEILING, lambda _: charged())
    expected = budget.summary()
    cold = account(tmp_path / "budget", "2")
    monkeypatch.setattr(cold.run.objects, "read", lambda *_: pytest.fail("read a settled artifact"))
    assert cold.summary() == expected


@pytest.mark.parametrize("cache_contents", [None, b"{", "stale"])
def test_derived_cache_can_be_missing_truncated_or_behind_the_ledger(tmp_path, cache_contents):
    budget, req = account(tmp_path / "budget", "2"), request(endpoint())
    budget.dispatch(req, CEILING, lambda _: charged())
    cache = budget.run.directory / "spending_cache.json"
    old = cache.read_bytes()
    budget.dispatch(replace(req, call_id="second"), CEILING, lambda _: charged("0.3"))
    expected = budget.summary()
    ledger = budget.journal.load()
    if cache_contents is None:
        cache.unlink()
    else:
        cache.write_bytes(old if cache_contents == "stale" else cache_contents)
    assert account(tmp_path / "budget", "2").summary() == expected
    assert budget.journal.load() == ledger  # Derived recovery never bills twice.
    assert expected["spent"] == "0.5" and expected["calls"] == 2


def test_old_writer_after_cached_checkpoint_is_reconciled_before_admission(tmp_path):
    from behave.core.artifacts import BlobRef

    budget, req = account(tmp_path / "budget"), request(endpoint())
    budget.dispatch(req, CEILING, lambda _: charged())
    budget.summary()
    next_request = replace(req, call_id="old_writer")
    ref = budget.run.objects.put(canonical_json(next_request.as_dict()))
    assert ref == BlobRef.of(canonical_json(next_request.as_dict()))
    # Legacy writers know only full load/append and do not update the cache.
    with budget.run.owner():
        budget.journal.append(budget.journal.load(), "spend_reserved", {
            "call_id": next_request.call_id, "request": ref.as_dict(), "ceiling": CEILING.as_dict()}, (ref,))
    cold = account(tmp_path / "budget")
    result = cold.dispatch(replace(req, call_id="third"), CEILING, lambda _: pytest.fail("overspent"))
    assert result.code == "model-budget-exhausted"
    summary = cold.summary()
    assert summary["spent"] == "0.2" and summary["reserved"] == "0.6" and summary["pending"] == 1


def test_rolling_update_with_an_already_imported_legacy_journal(tmp_path):
    budget, req = account(tmp_path / "budget", "2"), request(endpoint())
    journal = budget.journal
    budget.journal = SimpleNamespace(load=journal.load, append=journal.append)
    budget.dispatch(req, CEILING, lambda _: charged())
    cold = account(tmp_path / "budget", "2")
    cold.dispatch(replace(req, call_id="second"), CEILING, lambda _: charged("0.3"))
    assert budget.summary() == cold.summary()
    assert cold.summary()["spent"] == "0.5"


def test_cache_replacement_failure_keeps_ledger_and_receipt_recoverable(tmp_path, monkeypatch):
    budget, req = account(tmp_path / "budget", "2"), request(endpoint())
    budget.dispatch(req, CEILING, lambda _: charged())
    cache = budget.run.directory / "spending_cache.json"
    old = cache.read_bytes()
    real_replace = spending_module.os.replace
    replacements = 0

    def interrupt_cache(source, destination):
        nonlocal replacements
        if destination == cache:
            replacements += 1
            if replacements == 2:  # New receipt and settlement already committed.
                raise KeyboardInterrupt
        return real_replace(source, destination)

    monkeypatch.setattr(spending_module.os, "replace", interrupt_cache)
    with pytest.raises(KeyboardInterrupt):
        budget.dispatch(replace(req, call_id="second"), CEILING, lambda _: charged("0.3"))
    assert json.loads(cache.read_bytes())["calls"] == json.loads(old)["calls"]
    cold = account(tmp_path / "budget", "2")
    assert cold.summary()["spent"] == "0.5"
    assert cold.dispatch(replace(req, call_id="second"), CEILING,
                         lambda _: pytest.fail("must not redispatch")) == charged("0.3")


def test_recovery_matches_reservation_value_without_rewriting_decimal_spelling(tmp_path):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    old_ceiling = replace(CEILING, amount="0.0000005")
    returned = charged("0.0000002")
    assert budget.dispatch(req, old_ceiling, lambda _: returned) == returned
    original = budget.journal.load()
    cold = account(tmp_path / "budget")
    assert cold.dispatch(req, replace(old_ceiling, amount="5E-7"),
                         lambda _: pytest.fail("must replay the old receipt")) == returned
    assert cold.journal.load() == original
    for ceiling in (replace(old_ceiling, amount="6E-7"), replace(old_ceiling, pricing_revision="different")):
        with pytest.raises(ValueError, match="different request or prices"):
            cold.dispatch(req, ceiling, lambda _: pytest.fail("conflicting reservation dispatched"))


@pytest.mark.parametrize("outcome", [ModelFailure("unknown", "disconnected"), ModelReply("no usage"),
    ModelFailure("permanent", "bad-envelope"), ModelReply("wrong price", charge=Charge("0.2", "USD", "other"))])
def test_unknown_charge_retains_reservation_without_stopping_other_calls(tmp_path, outcome):
    budget, req = account(tmp_path / "budget", "1.5"), request(endpoint())
    assert budget.dispatch(req, CEILING, lambda _: outcome) == outcome
    summary = budget.summary()
    assert not summary["stopped"] and summary["reserved"] == "0.6"
    assert summary["unpriced"] == 1 and summary["pending"] == 0
    # Reopening never resends the uncertain request or treats it as free.
    cold = account(tmp_path / "budget", "1.5")
    assert cold.dispatch(req, CEILING, lambda _: pytest.fail("redispatched")) == outcome
    assert cold.dispatch(replace(req, call_id="next"), CEILING, lambda _: outcome) == outcome
    assert cold.summary()["reserved"] == "1.2"
    assert cold.dispatch(replace(req, call_id="third"), CEILING,
                         lambda _: pytest.fail("budget exceeded")).code == "model-budget-exhausted"


def test_observed_overrun_is_not_clipped_to_limit(tmp_path):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    assert budget.dispatch(req, CEILING, lambda _: charged("1.1")).charge.amount == "1.1"
    assert budget.summary()["spent"] == "1.1" and not budget.summary()["stopped"]
    assert budget.dispatch(replace(req, call_id="next"), CEILING,
                           lambda _: pytest.fail("total budget exceeded")).code == "model-budget-exhausted"


def test_input_count_estimate_overrun_does_not_stop_other_models(tmp_path):
    budget, req = account(tmp_path / "budget", "2"), request(endpoint())
    estimate = replace(CEILING, amount="0.0393228")
    budget.dispatch(replace(req, call_id="input-count"), estimate,
                    lambda _: charged("0.03961842"))
    assert budget.dispatch(req, CEILING, lambda _: charged()) == charged()
    assert budget.summary()["spent"] == "0.23961842"
    assert not budget.summary()["stopped"]


def test_budget_does_not_round_away_a_small_overrun_after_a_large_charge(tmp_path):
    from decimal import localcontext

    budget, req = account(tmp_path / "budget", "1e150"), request(endpoint())
    with localcontext() as context:
        context.prec, context.Emin, context.Emax = 3, -9, 9
        budget.dispatch(req, replace(CEILING, amount="1e150"), lambda _: charged("1e150"))
        result = budget.dispatch(replace(req, call_id="next"), replace(CEILING, amount="1e-180"),
                                 lambda _: pytest.fail("rounded reservation exceeded the account limit"))
        summary = budget.summary()
    assert result.code == "model-budget-exhausted" and result.charge is None
    assert Decimal(summary["spent"]) == Decimal("1e150") and summary["calls"] == 1


def test_budget_preserves_tiny_settlements_and_pending_reservations(tmp_path):
    from decimal import localcontext

    budget, req = account(tmp_path / "budget", "2e150"), request(endpoint())
    with localcontext() as context:
        context.prec, context.Emin, context.Emax = 3, -9, 9
        for index, amount in enumerate(("1e150", "1e-180")):
            budget.dispatch(replace(req, call_id=str(index)), replace(CEILING, amount=amount),
                            lambda _, amount=amount: charged(amount))
        budget.dispatch(replace(req, call_id="pending"), replace(CEILING, amount="1e-200"),
                        lambda _: ModelFailure("unknown", "disconnected"))
        summary = account(tmp_path / "budget", "2e150").summary()
    assert Fraction(Decimal(summary["spent"])) == Fraction(Decimal("1e150")) + Fraction(Decimal("1e-180"))
    assert Decimal(summary["reserved"]) == Decimal("1e-200") and not summary["stopped"]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks"])
def test_provider_quota_does_not_stop_shared_account(tmp_path, provider):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    failure = ModelFailure("permanent", provider + "-quota-exhausted", charge=Charge("0", "USD", "price/1"))
    assert budget.dispatch(req, CEILING, lambda _: failure) == failure
    assert budget.summary()["spent"] == "0" and not budget.summary()["stopped"]
    assert budget.dispatch(replace(req, call_id="next"), CEILING, lambda _: charged()) == charged()
    assert budget.dispatch(req, CEILING, lambda _: pytest.fail()) == failure


def test_process_death_keeps_pending_reservation_and_does_not_retry(tmp_path):
    budget, req = account(tmp_path / "budget"), request(endpoint())

    def interrupted(_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        budget.dispatch(req, CEILING, interrupted)
    cold = account(tmp_path / "budget")
    assert cold.summary()["pending"] == 1 and cold.summary()["reserved"] == "0.6"
    assert cold.dispatch(req, CEILING, lambda _: pytest.fail()).code == "budget-dispatch-pending"
    assert cold.dispatch(replace(req, call_id="next"), CEILING, lambda _: pytest.fail()).code == "model-budget-exhausted"


def block_settlement(monkeypatch, budget, error):
    transaction = budget._transaction
    calls = 0

    def patched(action, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error
        return transaction(action, **kwargs)

    monkeypatch.setattr(budget, "_transaction", patched)


def test_lock_contention_after_provider_does_not_lose_billable_reply(monkeypatch, tmp_path):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    block_settlement(monkeypatch, budget, AttemptInterrupted("run-already-owned"))
    assert budget.dispatch(req, CEILING, lambda _: charged()) == charged()
    # A new process reconciles the durable per-call receipt before admission.
    cold = account(tmp_path / "budget")
    assert cold.summary()["spent"] == "0.2"
    assert cold.summary()["pending"] == 0
    assert cold.dispatch(req, CEILING, lambda _: pytest.fail("redispatched")) == charged()
    assert [e.kind for e in cold.journal.load()].count("spend_settled") == 1


def test_death_after_result_receipt_is_recoverable_without_provider_retry(monkeypatch, tmp_path):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    block_settlement(monkeypatch, budget, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        budget.dispatch(req, CEILING, lambda _: charged())
    cold = account(tmp_path / "budget")
    assert cold.dispatch(req, CEILING, lambda _: pytest.fail("redispatched")) == charged()
    assert cold.summary()["spent"] == "0.2"


@pytest.mark.parametrize("outcome", [
    charged("1.1"),
    ModelFailure("permanent", "openai-quota-exhausted", charge=Charge("0", "USD", "price/1")),
])
def test_deferred_receipt_admission_uses_actual_total(monkeypatch, tmp_path, outcome):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    block_settlement(monkeypatch, budget, AttemptInterrupted("run-already-owned"))
    assert budget.dispatch(req, CEILING, lambda _: outcome) == outcome
    cold = account(tmp_path / "budget")
    if outcome.charge.amount == "1.1":
        assert cold.dispatch(replace(req, call_id="next"), CEILING,
                             lambda _: pytest.fail("budget exceeded")).code == "model-budget-exhausted"
    else:
        assert cold.dispatch(replace(req, call_id="next"), CEILING,
                             lambda _: charged("0")) == charged("0")
    assert not cold.summary()["stopped"]
    assert cold.summary()["reserved"] == ("0.6" if outcome.charge is None else "0")


def test_deferred_unknown_receipt_does_not_stop_unrelated_work(monkeypatch, tmp_path):
    budget, req = account(tmp_path / "budget", "2"), request(endpoint())
    block_settlement(monkeypatch, budget, AttemptInterrupted("run-already-owned"))
    outcome = ModelFailure("unknown", "openai-http-524")
    assert budget.dispatch(req, CEILING, lambda _: outcome) == outcome
    cold = account(tmp_path / "budget", "2")
    assert cold.dispatch(replace(req, call_id="next"), CEILING, lambda _: charged()) == charged()
    summary = cold.summary()
    assert summary["spent"] == "0.2" and summary["reserved"] == "0.6"
    assert summary["unpriced"] == 1 and not summary["stopped"]


def test_busy_admission_neither_dispatches_nor_invents_zero_charge(monkeypatch, tmp_path):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    def busy(_, **kwargs):
        raise AttemptInterrupted("run-already-owned")
    monkeypatch.setattr(budget, "_transaction", busy)
    outcome = budget.dispatch(req, CEILING, lambda _: pytest.fail("dispatched without admission"))
    assert outcome.kind == "unknown" and outcome.code == "budget-account-busy"
    assert outcome.charge is None
    assert account(tmp_path / "budget").summary()["calls"] == 0


@pytest.fixture
def spending_clock(monkeypatch):
    clock = SimpleNamespace(now=0., sleeps=[], on_sleep=lambda: None)

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds
        clock.on_sleep()

    monkeypatch.setattr(spending_module, "time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=sleep))
    monkeypatch.setattr(defaults, "ADMISSION_POLL_SECONDS", 10.)
    return clock


def test_spending_wait_does_not_admit_after_request_deadline(tmp_path, spending_clock):
    budget = account(tmp_path / "budget")
    with ExitStack() as held:
        held.enter_context(budget.run.owner())
        # Even if the lock becomes free at the deadline, do not dispatch late.
        spending_clock.on_sleep = held.close
        result = budget.dispatch(replace(request(endpoint()), timeout_seconds=3), CEILING,
                                 lambda _: pytest.fail("expired request dispatched"))
    assert result.code == "budget-account-busy" and result.charge is None
    assert spending_clock.sleeps == [3.]
    assert budget.summary()["calls"] == 0


def test_durable_reply_does_not_wait_for_shared_settlement_lock(tmp_path, spending_clock):
    budget, req = account(tmp_path / "budget"), request(endpoint())
    with ExitStack() as held:
        def generate(_):
            held.enter_context(budget.run.owner())
            return charged()

        assert budget.dispatch(req, CEILING, generate) == charged()
        assert spending_clock.sleeps == []
    cold = account(tmp_path / "budget")
    assert cold.summary()["spent"] == "0.2"
    assert cold.dispatch(req, CEILING, lambda _: pytest.fail("redispatched")) == charged()
    assert [e.kind for e in cold.journal.load()].count("spend_settled") == 1


def test_spending_summary_is_fail_fast_while_owned(tmp_path, spending_clock):
    budget = account(tmp_path / "budget")
    with budget.run.owner(), pytest.raises(AttemptInterrupted, match="run-already-owned"):
        budget.summary()
    assert spending_clock.sleeps == []


def test_spending_transaction_never_retries_its_body(tmp_path, spending_clock):
    budget, calls = account(tmp_path / "budget"), []

    def action():
        calls.append("entered")
        raise AttemptInterrupted("run-already-owned")

    with pytest.raises(AttemptInterrupted, match="run-already-owned"):
        budget._transaction(action, deadline=3.)
    assert calls == ["entered"] and spending_clock.sleeps == []
    with budget.run.owner():
        pass  # The failed transaction released its lock.


def competing(path, index, entered, release, results):
    budget, req = account(path), replace(request(endpoint()), call_id=f"concurrent-{index}")

    def provider(_):
        entered.set()
        assert release.wait(10)
        return charged()

    outcome = budget.dispatch(req, CEILING, provider)
    results.put((index, outcome.code if type(outcome) is ModelFailure else "reply"))


def test_two_processes_cannot_spend_the_same_remaining_money(tmp_path):
    context = multiprocessing.get_context("fork")
    entered, release, results = context.Event(), context.Event(), context.Queue()
    first = context.Process(target=competing, args=(tmp_path / "budget", 0, entered, release, results))
    second = context.Process(target=competing, args=(tmp_path / "budget", 1, entered, release, results))
    try:
        first.start()
        assert entered.wait(10)
        second.start()
        assert results.get(timeout=10) == (1, "model-budget-exhausted")
        release.set()
        assert results.get(timeout=10) == (0, "reply")
        for process in (first, second):
            process.join(10)
            assert process.exitcode == 0
    finally:
        release.set()
        for process in (first, second):
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(10)
        results.close()
    assert account(tmp_path / "budget").summary()["spent"] == "0.2"


def test_hosted_broker_owns_budget_and_retains_exact_usage(monkeypatch, tmp_path):
    ep = endpoint(credential=credential_file(tmp_path),
        parameters={"max_output_tokens": 4096},
        spending=SpendPolicy(str(tmp_path / "budget"), "shared", "USD", "1", 1000))
    assert HostedEndpoint.from_dict(ep.as_dict()) == ep
    sent = []

    def post(*args, **kwargs):
        sent.append(args)
        return HttpResponse(200, canonical_json(body("openai", call=False)), None)

    monkeypatch.setattr("behave.agent.providers.hosted_worker.post_json", post)
    outcome = exchange(ep, request(ep))
    assert type(outcome) is ModelReply and outcome.charge is not None
    assert exchange(ep, request(ep)) == outcome and len(sent) == 1
    assert Decimal(SpendAccount(ep.spending).summary()["spent"]) == Decimal(outcome.charge.amount)
    with pytest.raises(ValueError, match="prices"):
        replace(ep, prices=None)
    with pytest.raises(ValueError, match="cache-write"):
        replace(ep, provider="anthropic")
    assert replace(ep, parameters={}).parameters == {}  # Runtime context can supply the ceiling.


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_returned_nonstandard_tier_keeps_reservation_and_allows_other_calls(monkeypatch, tmp_path, provider):
    prices = replace(endpoint().prices, cache_write_5m_per_million="2.5", cache_write_1h_per_million="4")
    ep = endpoint(provider, credential=credential_file(tmp_path), prices=prices,
                  parameters={"max_output_tokens": 4096},
                  spending=SpendPolicy(str(tmp_path / "budget"), "shared", "USD", "1", 1000))
    data = body(provider, call=False)
    (data if provider == "openai" else data["usage"])["service_tier"] = "priority"
    sent = []

    def post(*args, **kwargs):
        sent.append(args)
        return HttpResponse(200, canonical_json(data), None)

    monkeypatch.setattr("behave.agent.providers.hosted_worker.post_json", post)
    req = request(ep)
    outcome = exchange(ep, req)
    assert type(outcome) is ModelReply and outcome.charge is None
    summary = SpendAccount(ep.spending).summary()
    assert not summary["stopped"] and Decimal(summary["reserved"]) > 0 and summary["spent"] == "0"
    assert exchange(ep, replace(req, call_id="next")) == outcome
    assert exchange(ep, req) == outcome and len(sent) == 2
    after = SpendAccount(ep.spending).summary()
    assert Decimal(after["reserved"]) == 2 * Decimal(summary["reserved"])
    assert after["unpriced"] == 2


def test_reservation_includes_expensive_cache_writes():
    prices = replace(endpoint().prices, cache_write_5m_per_million="2.5", cache_write_1h_per_million="4")
    assert Decimal(prices.reservation(1000, 100).amount) == Decimal("0.0048")


def test_price_range_and_billing_tier_are_not_guessed():
    from behave.agent.billing import TokenUsage
    from behave.agent.providers.hosted_worker import request_body, response
    from behave.core.records import parse_json

    prices = replace(endpoint().prices, maximum_input_tokens=10)
    assert prices.charge(TokenUsage(11, 1, 0)) is None
    with pytest.raises(ValueError, match="input range"):
        prices.reservation(11, 1)
    for provider, tier in (("openai", "default"), ("anthropic", "standard")):
        ep = endpoint(provider, billing_tier=tier)
        packet = parse_json(request_body(ep, request(ep)))
        assert packet["service_tier"] == ("default" if provider == "openai" else "standard_only")
        data = body(provider, call=False)
        assert response(ep, 200, canonical_json(data)).charge is None
        (data if provider == "openai" else data["usage"])["service_tier"] = tier
        assert response(ep, 200, canonical_json(data)).charge is not None
