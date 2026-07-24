"""Gate 1 — deterministic eligibility filter (restricted list, ESG exclusions).

Same spirit as shared/validators.py: zero LLM cost, runs before/alongside
fetching so obviously-non-compliant tickers never reach the (expensive)
analysis pipeline. Two checks, because the data they need becomes available
at two different points in the pipeline:

- `check_restricted_list`: pure ticker-symbol match, runs on the initial
  ticker list before any agent is called.
- `check_esg_exclusions`: needs each ticker's sector/industry, which only
  exists after DataCollector has fetched fundamentals — runs immediately
  after that, before FundamentalAnalyst spends any further work on them.
"""
from pathlib import Path
from typing import Any

import yaml

_POLICY_PATH = Path(__file__).parent.parent / "policies" / "restricted_list.yaml"


def _load_policy() -> dict[str, Any]:
    if not _POLICY_PATH.exists():
        return {"restricted_tickers": [], "esg_excluded_sectors": []}
    with open(_POLICY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check_restricted_list(tickers: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Splits `tickers` into (eligible, blocked). Each blocked entry is
    {"ticker": ..., "motivo_esclusione": ...} — same shape as
    shared/models.py::CandidatoEscluso, so it can be merged straight into
    the final report's candidati_esclusi."""
    policy = _load_policy()
    restricted = {
        entry["ticker"].upper(): entry.get("motivo", "Ticker in restricted list.")
        for entry in policy.get("restricted_tickers", [])
    }

    eligible: list[str] = []
    blocked: list[dict[str, str]] = []
    for ticker in tickers:
        reason = restricted.get(ticker.upper())
        if reason:
            blocked.append({"ticker": ticker, "motivo_esclusione": reason})
        else:
            eligible.append(ticker)
    return eligible, blocked


def check_esg_exclusions(
    fundamentals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Splits `fundamentals` records into (eligible, blocked) by matching each
    record's sector/industry fields against esg_excluded_sectors (case-insensitive
    substring match)."""
    policy = _load_policy()
    excluded_sectors = [s.lower() for s in policy.get("esg_excluded_sectors", [])]

    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    for record in fundamentals:
        haystack = f"{record.get('sector', '')} {record.get('industry', '')}".lower()
        hit = next((s for s in excluded_sectors if s in haystack), None)
        if hit:
            blocked.append({
                "ticker": record.get("ticker", ""),
                "motivo_esclusione": f"Settore escluso da policy ESG: {hit}.",
            })
        else:
            eligible.append(record)
    return eligible, blocked
