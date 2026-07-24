"""Gate 3 — deterministic aggregate portfolio limits (concentration,
correlation, drawdown).

Same spirit as shared/eligibility.py (Gate 1): zero LLM cost, policy-driven
(policies/portfolio_limits.yaml), runs in the orchestrator on the allocation
proposed by the Portfolio Manager agent. Violations feed the standard
validation-retry loop — the PM gets the violation text as feedback and
proposes a new allocation.

Correlation needs price history, which the orchestrator fetches best-effort
(yfinance can be flaky); a missing series produces a "warning" violation, not
an "error" — a data-vendor hiccup must not hard-fail an otherwise valid
allocation, unlike an actual limit breach which is always an "error".
"""
from math import sqrt
from pathlib import Path
from typing import Any

import yaml

from shared.validators import Violation

_LIMITS_PATH = Path(__file__).parent.parent / "policies" / "portfolio_limits.yaml"

_DEFAULT_LIMITS: dict[str, Any] = {
    "max_position_weight_pct": 30,
    "max_sector_weight_pct": 50,
    "max_positions": 5,
    "max_weighted_drawdown_pct": 40,
    "correlation": {"max_pairwise": 0.85, "applies_above_weight_pct": 15, "lookback_days": 180},
}


def load_limits() -> dict[str, Any]:
    if not _LIMITS_PATH.exists():
        return dict(_DEFAULT_LIMITS)
    with open(_LIMITS_PATH, encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    return {**_DEFAULT_LIMITS, **loaded}


def pearson(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation of two equal-length return series — pure Python,
    no numpy dependency. Returns None if undefined (short/constant series)."""
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = a[-n:], b[-n:]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    da = [x - mean_a for x in a]
    db = [x - mean_b for x in b]
    var_a = sum(x * x for x in da)
    var_b = sum(x * x for x in db)
    if var_a == 0 or var_b == 0:
        return None
    return sum(x * y for x, y in zip(da, db)) / sqrt(var_a * var_b)


def _weight(entry: dict[str, Any]) -> float:
    try:
        return float(entry.get("peso_pct", 0))
    except (TypeError, ValueError):
        return 0.0


def check_portfolio_limits(
    allocation: list[dict[str, Any]],
    fundamentals: list[dict[str, Any]],
    returns_by_ticker: dict[str, list[float]] | None = None,
    limits: dict[str, Any] | None = None,
) -> list[Violation]:
    """Checks the PM's proposed allocation against aggregate limits.

    `allocation`: [{"ticker", "peso_pct", "razionale"}, ...] (cash is implicit:
    100 - sum of weights). `fundamentals` supplies sector and 52-week data per
    ticker. `returns_by_ticker` supplies daily return series for the
    correlation check; tickers missing from it get a warning, not an error.
    """
    limits = limits or load_limits()
    violations: list[Violation] = []
    fund_by_ticker = {f.get("ticker"): f for f in fundamentals}

    # --- position count & per-position cap -------------------------------
    if len(allocation) > int(limits["max_positions"]):
        violations.append(Violation(
            rule="gate3_max_positions", severity="error", ticker=None,
            message=f"{len(allocation)} posizioni proposte — massimo {limits['max_positions']}.",
        ))

    total = 0.0
    for entry in allocation:
        ticker = entry.get("ticker", "")
        w = _weight(entry)
        total += w
        if w <= 0:
            violations.append(Violation(
                rule="gate3_weight_range", severity="error", ticker=ticker,
                message=f"{ticker}: peso_pct={entry.get('peso_pct')} non valido (deve essere > 0).",
            ))
        elif w > float(limits["max_position_weight_pct"]):
            violations.append(Violation(
                rule="gate3_position_concentration", severity="error", ticker=ticker,
                message=f"{ticker}: peso {w:.1f}% oltre il limite per singola posizione "
                        f"({limits['max_position_weight_pct']}%).",
            ))

    if total > 100.01:
        violations.append(Violation(
            rule="gate3_weights_sum", severity="error", ticker=None,
            message=f"Somma dei pesi {total:.1f}% > 100%.",
        ))

    # --- sector concentration --------------------------------------------
    sector_weights: dict[str, float] = {}
    for entry in allocation:
        fund = fund_by_ticker.get(entry.get("ticker"), {})
        sector = (fund.get("sector") or "sconosciuto").strip().lower()
        sector_weights[sector] = sector_weights.get(sector, 0.0) + _weight(entry)
    for sector, w in sector_weights.items():
        if w > float(limits["max_sector_weight_pct"]):
            violations.append(Violation(
                rule="gate3_sector_concentration", severity="error", ticker=None,
                message=f"Settore '{sector}': peso aggregato {w:.1f}% oltre il limite "
                        f"({limits['max_sector_weight_pct']}%).",
            ))

    # --- drawdown proxy (weighted distance from 52-week high) ------------
    dd_weighted, dd_weight_total = 0.0, 0.0
    for entry in allocation:
        fund = fund_by_ticker.get(entry.get("ticker"), {})
        price, high = fund.get("price"), fund.get("week52_high")
        try:
            drawdown_pct = (1 - float(price) / float(high)) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        w = _weight(entry)
        dd_weighted += drawdown_pct * w
        dd_weight_total += w
    if dd_weight_total > 0:
        portfolio_dd = dd_weighted / dd_weight_total
        if portfolio_dd > float(limits["max_weighted_drawdown_pct"]):
            violations.append(Violation(
                rule="gate3_drawdown", severity="error", ticker=None,
                message=f"Drawdown medio ponderato dai massimi 52w: {portfolio_dd:.1f}% — "
                        f"oltre il limite ({limits['max_weighted_drawdown_pct']}%).",
            ))

    # --- pairwise correlation --------------------------------------------
    corr_cfg = limits.get("correlation") or {}
    threshold = float(corr_cfg.get("max_pairwise", 0.85))
    min_weight = float(corr_cfg.get("applies_above_weight_pct", 15))
    heavy = [e for e in allocation if _weight(e) >= min_weight]
    returns_by_ticker = returns_by_ticker or {}
    for i, ei in enumerate(heavy):
        for ej in heavy[i + 1:]:
            ti, tj = ei.get("ticker", ""), ej.get("ticker", "")
            ri, rj = returns_by_ticker.get(ti), returns_by_ticker.get(tj)
            if not ri or not rj:
                violations.append(Violation(
                    rule="gate3_correlation_data", severity="warning", ticker=None,
                    message=f"Serie storica mancante per {ti if not ri else tj}: "
                            f"correlazione {ti}/{tj} non verificabile.",
                ))
                continue
            corr = pearson(ri, rj)
            if corr is not None and corr > threshold:
                violations.append(Violation(
                    rule="gate3_correlation", severity="error", ticker=None,
                    message=f"{ti}/{tj}: correlazione {corr:.2f} > {threshold} con entrambi "
                            f"i pesi >= {min_weight}% — ridurre uno dei due sotto quella soglia "
                            f"o sostituire un titolo.",
                ))
    return violations
