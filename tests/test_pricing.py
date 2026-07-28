"""Unit tests for shared/pricing.py — approximate LLM cost estimation."""
from types import SimpleNamespace

from shared.pricing import estimate_cost_usd


def _usage(
    input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0,
):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )


def test_unknown_model_returns_zero():
    assert estimate_cost_usd("some-unknown-model", _usage(input_tokens=1_000_000)) == 0.0


def test_known_model_computes_expected_cost():
    usage = _usage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = estimate_cost_usd("claude-haiku-4-5-20251001", usage)
    assert cost == 6.0  # 1.0 (input) + 5.0 (output) $/M at 1M tokens each


def test_cache_tokens_are_included():
    usage = _usage(cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    cost = estimate_cost_usd("claude-haiku-4-5-20251001", usage)
    assert cost == 1.35  # 1.25 (cache write) + 0.10 (cache read) $/M


def test_zero_usage_is_zero_cost():
    assert estimate_cost_usd("claude-sonnet-5", _usage()) == 0.0


def test_missing_usage_fields_default_to_zero():
    usage = SimpleNamespace(input_tokens=1_000_000)  # no output/cache fields at all
    cost = estimate_cost_usd("claude-sonnet-5", usage)
    assert cost == 3.0
