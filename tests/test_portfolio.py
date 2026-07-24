"""Unit tests for Gate 3 (shared/portfolio.py) and the allocation validation stage."""
import pytest

from shared.portfolio import check_portfolio_limits, pearson
from shared.validators import validate_stage

LIMITS = {
    "max_position_weight_pct": 30,
    "max_sector_weight_pct": 50,
    "max_positions": 3,
    "max_weighted_drawdown_pct": 40,
    "correlation": {"max_pairwise": 0.85, "applies_above_weight_pct": 15, "lookback_days": 180},
}

FUNDAMENTALS = [
    {"ticker": "AAPL", "sector": "Technology", "price": 200, "week52_high": 220},
    {"ticker": "MSFT", "sector": "Technology", "price": 400, "week52_high": 430},
    {"ticker": "UCG.MI", "sector": "Financial Services", "price": 40, "week52_high": 42},
    {"ticker": "DEEP", "sector": "Technology", "price": 40, "week52_high": 100},
]


def rules(violations, severity=None):
    return {v.rule for v in violations if severity is None or v.severity == severity}


def test_clean_allocation_passes():
    alloc = [
        {"ticker": "AAPL", "peso_pct": 25, "razionale": "x"},
        {"ticker": "UCG.MI", "peso_pct": 20, "razionale": "y"},
    ]
    violations = check_portfolio_limits(alloc, FUNDAMENTALS, {"AAPL": [0.01, -0.02, 0.005] * 10, "UCG.MI": [-0.01, 0.02, 0.01] * 10}, LIMITS)
    assert rules(violations, "error") == set()


def test_position_cap():
    alloc = [{"ticker": "AAPL", "peso_pct": 45, "razionale": "x"}]
    assert "gate3_position_concentration" in rules(check_portfolio_limits(alloc, FUNDAMENTALS, {}, LIMITS), "error")


def test_sector_concentration():
    alloc = [
        {"ticker": "AAPL", "peso_pct": 30, "razionale": "x"},
        {"ticker": "MSFT", "peso_pct": 30, "razionale": "y"},
    ]
    assert "gate3_sector_concentration" in rules(check_portfolio_limits(alloc, FUNDAMENTALS, {}, LIMITS), "error")


def test_max_positions_and_weights_sum():
    alloc = [{"ticker": t, "peso_pct": 30, "razionale": "x"} for t in ("AAPL", "MSFT", "UCG.MI", "DEEP")]
    got = rules(check_portfolio_limits(alloc, FUNDAMENTALS, {}, LIMITS), "error")
    assert "gate3_max_positions" in got
    assert "gate3_weights_sum" in got


def test_nonpositive_weight():
    alloc = [{"ticker": "AAPL", "peso_pct": 0, "razionale": "x"}]
    assert "gate3_weight_range" in rules(check_portfolio_limits(alloc, FUNDAMENTALS, {}, LIMITS), "error")


def test_drawdown_proxy():
    # DEEP trades 60% below its 52w high — far past the 40% aggregate limit.
    alloc = [{"ticker": "DEEP", "peso_pct": 25, "razionale": "x"}]
    assert "gate3_drawdown" in rules(check_portfolio_limits(alloc, FUNDAMENTALS, {}, LIMITS), "error")


def test_correlation_breach_and_missing_series():
    series = [0.01, -0.02, 0.03, -0.01, 0.02] * 8
    alloc = [
        {"ticker": "AAPL", "peso_pct": 20, "razionale": "x"},
        {"ticker": "MSFT", "peso_pct": 20, "razionale": "y"},
    ]
    # identical series → correlation 1.0 → error
    violations = check_portfolio_limits(alloc, FUNDAMENTALS, {"AAPL": series, "MSFT": series}, LIMITS)
    assert "gate3_correlation" in rules(violations, "error")
    # missing series → warning only, never an error
    violations = check_portfolio_limits(alloc, FUNDAMENTALS, {}, LIMITS)
    assert "gate3_correlation_data" in rules(violations, "warning")
    assert "gate3_correlation" not in rules(violations, "error")


def test_correlation_ignored_below_weight_threshold():
    series = [0.01, -0.02, 0.03] * 10
    alloc = [
        {"ticker": "AAPL", "peso_pct": 10, "razionale": "x"},
        {"ticker": "MSFT", "peso_pct": 10, "razionale": "y"},
    ]
    violations = check_portfolio_limits(alloc, FUNDAMENTALS, {"AAPL": series, "MSFT": series}, LIMITS)
    assert "gate3_correlation" not in rules(violations, "error")


def test_pearson():
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None  # constant series
    assert pearson([1], [2]) is None  # too short


# ------------------------------------------------------------------ #
# validate_stage("allocation")                                         #
# ------------------------------------------------------------------ #

def test_allocation_stage_clean():
    parsed, violations = validate_stage("allocation", [
        {"ticker": "AAPL", "peso_pct": 25.0, "razionale": "Scoring elevato e catalizzatore vicino."},
    ])
    assert parsed is not None
    assert [v for v in violations if v.severity == "error"] == []


def test_allocation_stage_bad_weights_and_missing_rationale():
    _, violations = validate_stage("allocation", [
        {"ticker": "AAPL", "peso_pct": 120.0, "razionale": "x"},
        {"ticker": "MSFT", "peso_pct": 20.0, "razionale": ""},
    ])
    got = {v.rule for v in violations}
    assert "allocation_weight_range" in got
    assert "allocation_rationale" in got


def test_allocation_stage_sum_over_100():
    _, violations = validate_stage("allocation", [
        {"ticker": "AAPL", "peso_pct": 60.0, "razionale": "a"},
        {"ticker": "MSFT", "peso_pct": 55.0, "razionale": "b"},
    ])
    assert "allocation_weights_sum" in {v.rule for v in violations}


def test_allocation_stage_malformed():
    parsed, violations = validate_stage("allocation", [{"peso_pct": "molto"}])
    assert parsed is None
    assert "schema_parse" in {v.rule for v in violations}
