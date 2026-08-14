"""Country reference map lookup — see policies/country_tickers.yaml for the
full rationale. Not a Gate 1 check by itself; feeds the fallback candidate
tickers used by orchestrator/main.py when an explicit country/market request
finds no coverage in the generic (US/international) RSS scan."""

from pathlib import Path
from typing import Any

import yaml

_POLICY_PATH = Path(__file__).parent.parent / "policies" / "country_tickers.yaml"


def _load_policy() -> dict[str, Any]:
    if not _POLICY_PATH.exists():
        return {}
    with open(_POLICY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_country_seed_tickers(countries: list[str]) -> list[str]:
    """Case-insensitive match of `countries` against the curated country map
    (keys are yfinance-style English country names, e.g. "Italy"). Returns a
    deduplicated ticker list, or [] if no requested country has an entry —
    the caller must treat that as "no fallback available", not retry with an
    empty list."""
    policy = _load_policy()
    requested = {c.lower() for c in countries}

    seen: set[str] = set()
    tickers: list[str] = []
    for country_name, entry in policy.items():
        if country_name.lower() not in requested or not isinstance(entry, dict):
            continue
        for ticker in entry.get("tickers") or []:
            if ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
    return tickers
