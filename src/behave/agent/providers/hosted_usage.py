"""Usage normalization precedes response validation; prices are never guessed."""

from ..billing import TokenUsage


def usage_of(endpoint, body):
    raw = body.get("usage")
    record = {"raw": raw, "normalized": None, "normalization_error": None,
              "pricing": None if endpoint.prices is None else endpoint.prices.identity}
    try:
        if type(raw) is not dict:
            raise ValueError("missing usage")
        if endpoint.provider == "openai":
            details = raw.get("input_tokens_details") or {}
            usage = TokenUsage(raw["input_tokens"], raw["output_tokens"],
                               details.get("cached_tokens"),
                               (raw.get("output_tokens_details") or {}).get("reasoning_tokens"),
                               cache_write_tokens=details.get("cache_write_tokens", 0))
            if "total_tokens" in raw and (type(raw["total_tokens"]) is not int
                                          or raw["total_tokens"] != usage.input_tokens + usage.output_tokens):
                raise ValueError("invalid total tokens")
        else:
            # Anthropic input_tokens EXCLUDES both cache read and creation.
            cached = raw.get("cache_read_input_tokens", 0)
            created = raw.get("cache_creation_input_tokens", 0)
            split = raw.get("cache_creation") or {}
            short, long = split.get("ephemeral_5m_input_tokens", 0), split.get("ephemeral_1h_input_tokens", 0)
            for value in (raw["input_tokens"], cached, created, short, long):
                if type(value) is not int or value < 0:
                    raise ValueError("invalid token count")
            if short + long != created:
                raise ValueError("cache creation lacks its billing categories")
            usage = TokenUsage(raw["input_tokens"] + cached + created, raw["output_tokens"],
                               cached, None, short, long)
        record["normalized"] = usage.as_dict()
        actual = body.get("service_tier") if endpoint.provider == "openai" else raw.get("service_tier")
        if endpoint.billing_tier is not None or actual is not None:
            # These profiles only support standard-tier prices. An omitted
            # request tier must not hide contrary evidence in the response.
            expected = endpoint.billing_tier or ("default" if endpoint.provider == "openai" else "standard")
            record["service_tier"] = actual
            if actual != expected:
                record["pricing_error"] = "service-tier-mismatch"
                return record, None
        return record, None if endpoint.prices is None else endpoint.prices.charge(usage)
    except (ValueError, TypeError, KeyError, AttributeError):
        record["normalization_error"] = "usage-missing-or-invalid"
        return record, None
