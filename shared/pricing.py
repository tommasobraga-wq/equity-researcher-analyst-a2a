"""Approximate Anthropic per-token pricing, for relative cost visibility in
the observability dashboard (Grafana) — NOT for billing reconciliation.

Rates are hardcoded, manually-maintained $/million-token figures and will
drift out of date. Verify/update against https://www.anthropic.com/pricing
before relying on absolute numbers; they're accurate enough to compare
"which agent/run costs more", not to reconcile an invoice.
"""

# $ per million tokens: (input, output, cache_write, cache_read)
_RATES_PER_MILLION: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-4-5-20251001": (1.0, 5.0, 1.25, 0.10),
    "claude-sonnet-5": (3.0, 15.0, 3.75, 0.30),
}


def estimate_cost_usd(model: str, usage) -> float:
    """Best-effort cost estimate from an Anthropic response's `usage` object.

    Returns 0.0 for unrecognized models rather than raising — cost tracking
    is observability, not a correctness gate; a missing rate must never
    break the caller.
    """
    rates = _RATES_PER_MILLION.get(model)
    if rates is None:
        return 0.0
    input_rate, output_rate, cache_write_rate, cache_read_rate = rates

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

    return (
        input_tokens * input_rate
        + output_tokens * output_rate
        + cache_write_tokens * cache_write_rate
        + cache_read_tokens * cache_read_rate
    ) / 1_000_000
