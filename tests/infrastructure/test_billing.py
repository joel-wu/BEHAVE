"""Pricing arithmetic is independent of valid tool output and local GPU costs."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from random import Random

import pytest

from behave.agent.billing import TokenPrices, TokenUsage
from behave.agent.model import Charge
from behave.agent.trajectory import CostLedger, Trajectory


def prices(**changes):
    return replace(TokenPrices("fixture/1", "fixture", "USD", "2", "0.5", "8"), **changes)


def test_price_counts_cache_and_reasoning_once_and_binds_actual_price_snapshot():
    policy = prices()
    usage = TokenUsage(1_000_000, 500_000, 200_000, 300_000)
    bill = policy.charge(usage)
    assert Decimal(bill.amount) == Decimal("5.7")
    assert bill.pricing_revision == "fixture/1:" + policy.identity
    assert TokenPrices.from_dict(policy.as_dict()) == policy
    assert prices(output_per_million="9").identity != policy.identity
    assert policy.charge(TokenUsage(1, 0, 0)).amount == "0.000002"


def test_missing_cache_information_only_matters_when_it_changes_the_bill():
    assert prices().charge(TokenUsage(10, 3)) is None
    assert Decimal(prices(cached_input_per_million="2").charge(TokenUsage(10, 3)).amount) == Decimal("0.000044")
    assert prices(input_per_million="0", cached_input_per_million="0", output_per_million="0").charge(TokenUsage(10, 3)).amount == "0"


@pytest.mark.parametrize("usage", [(-1, 0), (True, 1), (1, 2, 3), (1, 2, 0, 3)])
def test_invalid_token_counts_are_not_billed_as_zero(usage):
    with pytest.raises((TypeError, ValueError)):
        TokenUsage(*usage)


@pytest.mark.parametrize("price", ["nan", "Infinity", "-1", 1.0, ""])
def test_invalid_prices_are_refused(price):
    with pytest.raises(ValueError):
        prices(input_per_million=price)


def test_ledger_sum_is_exact_even_when_global_decimal_precision_is_small():
    from decimal import localcontext

    charges = (Charge("1000000000000000000000000000000", "USD", "fixture"),
               Charge("0.000000000000000000000001", "USD", "fixture"))
    with localcontext() as context:
        context.prec = 6
        totals = Trajectory((), charges, 0, 0).cost_by_currency()
    assert totals["USD"] == "1000000000000000000000000000000.000000000000000000000001"
    assert CostLedger((Charge("0e999999", "USD", "fixture"),), 0, 0).cost_by_currency() == {"USD": "0"}


def test_pricing_and_reservations_use_actual_precision_not_fixed_numeric_caps():
    from decimal import localcontext

    rate = "0." + "1234567890" * 25
    count = 10**90 + 123
    policy = prices(input_per_million=rate, cached_input_per_million="0", output_per_million="1e-180",
                    maximum_input_tokens=count)
    with localcontext() as context:
        context.prec, context.Emin, context.Emax = 3, -9, 9
        bill = policy.charge(TokenUsage(count, 7, 0))
        reserved = policy.reservation(count, 7)
    expected = (count * Fraction(Decimal(rate)) + 7 * Fraction(Decimal("1e-180"))) / 1_000_000
    assert Fraction(Decimal(bill.amount)) == Fraction(Decimal(reserved.amount)) == expected
    assert TokenPrices.from_dict(policy.as_dict()) == policy


@pytest.mark.parametrize("price", ["0" * 300 + "2", "1e1000", "1e-1000", "0e999999"])
def test_valid_price_text_is_not_rejected_or_forced_into_fixed_point_expansion(price):
    policy = prices(input_per_million=price, cached_input_per_million="0", output_per_million="0")
    amount = policy.charge(TokenUsage(1_000_000, 0, 0)).amount
    assert Decimal(amount) == Decimal(price)
    assert len(amount) < 30  # Computed scientific notation stays compact, unlike forced fixed point.


def test_ledger_precision_is_derived_from_amounts_and_keeps_currencies_separate():
    from decimal import localcontext

    values = ("1e150", "1e-180", "0e999999", "9" * 300)
    charges = tuple(Charge(value, "USD", "fixture") for value in values)
    with localcontext() as context:
        context.prec, context.Emin, context.Emax = 3, -9, 9
        totals = CostLedger((*charges, Charge("0.02", "EUR", "fixture")), 2, 1).cost_by_currency()
    assert Fraction(Decimal(totals["USD"])) == sum(Fraction(Decimal(value)) for value in values)
    assert totals["EUR"] == "0.02"


@pytest.mark.parametrize("seed", range(16))
def test_disjoint_category_costs_match_an_independent_rational_oracle(seed):
    from decimal import localcontext

    rng = Random(seed)
    counts = [rng.randrange(10**rng.randrange(1, 90)) for _ in range(5)]
    rates = [f"{rng.randrange(1, 10**20)}e{rng.randrange(-250, 100)}" for _ in counts]
    policy = TokenPrices("fixture", "fixture", "USD", *rates[:3], *rates[3:])
    uncached, cached, output, short, long = counts
    usage = TokenUsage(uncached + cached + short + long, output, cached, output, short, long)
    with localcontext() as context:
        context.prec, context.Emin, context.Emax = 2, -9, 9
        bill, ceiling = policy.charge(usage), policy.reservation(usage.input_tokens, output)
    expected = sum(count * Fraction(Decimal(rate)) for count, rate in zip(counts, rates)) / 1_000_000
    reserve = (usage.input_tokens * max(Fraction(Decimal(rate)) for rate in (rates[0], rates[1], *rates[3:]))
               + output * Fraction(Decimal(rates[2]))) / 1_000_000
    assert Fraction(Decimal(bill.amount)) == expected
    assert Fraction(Decimal(ceiling.amount)) == reserve >= expected


def test_cost_aggregation_preserves_equal_dispatches_currencies_and_unknown_usage():
    first = CostLedger((Charge("0.03", "USD", "price-a"),), 1, 0)
    second = CostLedger((Charge("0.03", "USD", "price-a"), Charge("2", "EUR", "price-b")), 0, 1)
    ledger = CostLedger.combine((first, second))
    assert ledger.cost_by_currency() == {"USD": "0.06", "EUR": "2"}
    assert len(ledger.charges) == 3
    assert (ledger.unpriced_dispatches, ledger.unresolved_dispatches) == (1, 1)
    assert CostLedger.combine(()) == CostLedger((), 0, 0)
    with pytest.raises(TypeError):
        CostLedger.combine((first, None))


def test_cost_only_projection_does_not_read_requests_or_raw_responses(tmp_path):
    from behave.agent.journal import Event
    from behave.agent.model import ModelFailure, ModelReply
    from behave.agent.trajectory import billing_of, trajectory_of
    from behave.core.artifacts import BlobRef
    from behave.core.records import canonical_json
    from behave.core.storage import DirectoryStore

    objects, events, forbidden = DirectoryStore(tmp_path / "objects"), [], set()
    replies = (ModelReply("done", charge=Charge("0.03", "USD", "fixture"),
                          usage={"input_count": {"charge": Charge("0.01", "USD", "fixture").as_dict()}}),
               ModelFailure("unknown", "lost-response"),
               ModelReply("done", charge=Charge("0.03", "USD", "fixture")))
    for turn, reply in enumerate(replies):
        request = objects.put(canonical_json({"turn": turn, "text": "not needed for billing"}))
        raw = objects.put(f"raw-{turn}".encode())
        forbidden.update((request, raw))
        events.append(Event(len(events), None, "model_requested", {"call_id": str(turn), "request": request.as_dict()}, (request,)))
        record = objects.put(canonical_json(reply.as_dict()))
        events.append(Event(len(events), None, "model_settled", {"call_id": str(turn),
            "record": record.as_dict(), "raw": raw.as_dict(),
            "outcome": "failure" if isinstance(reply, ModelFailure) else "reply"}, (record, raw)))
    events.append(Event(len(events), None, "model_requested", {"call_id": "pending",
                        "request": BlobRef.of(b"unfinished dispatch").as_dict()}, ()))
    expected = trajectory_of(tuple(events), objects).billing

    class RecordsOnly:
        def read(self, ref):
            assert ref not in forbidden, "cost-only projection reread a large payload"
            return objects.read(ref)

    actual = billing_of(tuple(events), RecordsOnly())
    assert actual == expected
    assert actual.cost_by_currency() == {"USD": "0.07"}
    assert (actual.unpriced_dispatches, actual.unresolved_dispatches) == (1, 2)
