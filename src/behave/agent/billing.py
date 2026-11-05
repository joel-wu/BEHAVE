"""Explicit token-price snapshots; no live lookup and no missing-usage-as-zero.

Input totals include cache reads/writes; output totals include reasoning. Only
disjoint uncached/cache-read/cache-write/output counts are billed. Prices are decimal currency
units per million tokens. A zero-price local endpoint is an explicit policy,
not an inference from absent credentials or absent usage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, Inexact, InvalidOperation, Overflow

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, fields, text

from .model import Charge


def sum_amounts(values: Iterable[Decimal]) -> Decimal:
    """Exactly sum validated finite nonnegative amounts, independent of ambient context."""
    values = tuple(value for value in values if value)
    if not values:
        return Decimal(0)
    # Align actual decimal positions and allow carry from this many terms.
    precision = (max(value.adjusted() for value in values)
                 - min(value.as_tuple().exponent for value in values)
                 + 1 + len(str(len(values))))
    context = Context(prec=precision, Emin=MIN_EMIN, Emax=MAX_EMAX,
                      traps=[Inexact, InvalidOperation, Overflow])
    total = Decimal(0)
    for value in values:
        total = context.add(total, value)
    return total


def _token_amount(count: int, rate: str) -> Decimal:
    """One disjoint token category at a currency-per-million rate."""
    tokens, price = Decimal(count), Decimal(rate)
    if not tokens or not price:
        return Decimal(0)
    context = Context(prec=len(tokens.as_tuple().digits) + len(price.as_tuple().digits),
                      Emin=MIN_EMIN, Emax=MAX_EMAX, traps=[Inexact, InvalidOperation, Overflow])
    return context.divide(context.multiply(tokens, price), Decimal(1_000_000))


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        for name, value in self.as_dict().items():
            if value is None and name in {"cached_input_tokens", "reasoning_output_tokens"}:
                continue
            if type(value) is not int or value < 0:
                raise ValueError("invalid token count")
        if self.cached_input_tokens is not None and self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached tokens exceed input tokens")
        if self.reasoning_output_tokens is not None and self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("reasoning tokens exceed output tokens")
        if ((self.cached_input_tokens or 0) + self.cache_write_5m_tokens
                + self.cache_write_1h_tokens + self.cache_write_tokens > self.input_tokens):
            raise ValueError("cache categories exceed input tokens")

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "reasoning_output_tokens": self.reasoning_output_tokens,
                "cache_write_5m_tokens": self.cache_write_5m_tokens,
                "cache_write_1h_tokens": self.cache_write_1h_tokens,
                "cache_write_tokens": self.cache_write_tokens}


@dataclass(frozen=True, slots=True)
class TokenPrices:
    revision: str
    model: str
    currency: str
    input_per_million: str
    cached_input_per_million: str
    output_per_million: str
    cache_write_5m_per_million: str | None = None
    cache_write_1h_per_million: str | None = None
    maximum_input_tokens: int | None = None
    cache_write_per_million: str | None = None

    def __post_init__(self) -> None:
        for name in ("revision", "model", "currency"):
            text(getattr(self, name), name)
        if self.maximum_input_tokens is not None and (
                type(self.maximum_input_tokens) is not int or self.maximum_input_tokens < 1):
            raise ValueError("price input range must be a positive token bound")
        for name in ("input_per_million", "cached_input_per_million", "output_per_million",
                     "cache_write_5m_per_million", "cache_write_1h_per_million", "cache_write_per_million"):
            value = getattr(self, name)
            if value is None and name.startswith("cache_write_"):
                continue
            try:
                price = Decimal(value) if type(value) is str else Decimal("NaN")
            except InvalidOperation as error:
                raise ValueError("invalid token price") from error
            if not price.is_finite() or price < 0:
                raise ValueError("token price must be a finite nonnegative decimal")

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__ if getattr(self, name) is not None}

    @classmethod
    def from_dict(cls, value: object) -> TokenPrices:
        if type(value) is not dict:
            raise ValueError("prices must be an object")
        return cls(**fields({"cache_write_5m_per_million": None, "cache_write_1h_per_million": None,
                            "maximum_input_tokens": None, "cache_write_per_million": None, **value},
                            set(cls.__dataclass_fields__)))

    @property
    def identity(self) -> str:
        return BlobRef.of(canonical_json(self.as_dict())).sha256

    def charge(self, usage: TokenUsage) -> Charge | None:
        if type(usage) is not TokenUsage:
            raise TypeError("pricing requires normalized token usage")
        if self.maximum_input_tokens is not None and usage.input_tokens > self.maximum_input_tokens:
            return None
        # Without the cache count, different cache prices make the bill unknown.
        if usage.cached_input_tokens is None and Decimal(self.input_per_million) != Decimal(self.cached_input_per_million):
            return None
        cached = usage.cached_input_tokens or 0
        writes = ((usage.cache_write_5m_tokens, self.cache_write_5m_per_million),
                  (usage.cache_write_1h_tokens, self.cache_write_1h_per_million),
                  (usage.cache_write_tokens, self.cache_write_per_million))
        if any(count and rate is None for count, rate in writes):
            return None
        uncached = usage.input_tokens - cached - sum(count for count, _ in writes)
        terms = ((uncached, self.input_per_million), (cached, self.cached_input_per_million),
                 *writes, (usage.output_tokens, self.output_per_million))
        amount = sum_amounts(_token_amount(count, rate or "0") for count, rate in terms)
        return Charge(str(amount), self.currency, f"{self.revision}:{self.identity}")

    def reservation(self, input_tokens: int, output_tokens: int) -> Charge:
        """Worst priced declared input category, plus the full output allowance."""
        TokenUsage(input_tokens, output_tokens, 0)
        if self.maximum_input_tokens is not None and input_tokens > self.maximum_input_tokens:
            raise ValueError("reservation exceeds the price snapshot's input range")
        rates = (self.input_per_million, self.cached_input_per_million,
                 self.cache_write_5m_per_million, self.cache_write_1h_per_million,
                 self.cache_write_per_million)
        maximum_rate = max((rate for rate in rates if rate is not None), key=Decimal)
        amount = sum_amounts((_token_amount(input_tokens, maximum_rate),
                              _token_amount(output_tokens, self.output_per_million)))
        return Charge(str(amount), self.currency, f"{self.revision}:{self.identity}")
