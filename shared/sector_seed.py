"""Sector reference map lookup — see policies/sector_tickers.yaml for the
full rationale. Not a Gate 1 check by itself; it feeds two deterministic
mechanisms: fallback candidate tickers (orchestrator/main.py) and keyword
expansion for shared.eligibility.check_sector_scope."""
from pathlib import Path
from typing import Any

import yaml

_POLICY_PATH = Path(__file__).parent.parent / "policies" / "sector_tickers.yaml"


def _load_policy() -> dict[str, Any]:
    if not _POLICY_PATH.exists():
        return {}
    with open(_POLICY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _matches(requested: list[str], sector_name: str, entry: dict[str, Any]) -> bool:
    keywords = [k.lower() for k in (entry.get("keywords") or [])]
    candidates = [sector_name.lower(), *keywords]
    return any(c in r or r in c for c in candidates for r in requested)


def load_sector_seed_tickers(priority_sectors: list[str]) -> list[str]:
    """Case-insensitive match of `priority_sectors` (by sector name or
    keyword) against the curated sector map. Returns a deduplicated ticker
    list, or [] if no requested sector has an entry — the caller must treat
    that as "no fallback available", not retry with an empty list."""
    policy = _load_policy()
    requested = [s.lower() for s in priority_sectors]

    seen: set[str] = set()
    tickers: list[str] = []
    for sector_name, entry in policy.items():
        if not isinstance(entry, dict) or not _matches(requested, sector_name, entry):
            continue
        for ticker in entry.get("tickers") or []:
            if ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
    return tickers


def expand_sector_keywords(priority_sectors: list[str]) -> list[str]:
    """Expands user-facing sector names into the keyword set used to match
    yfinance's own sector/industry taxonomy, which often uses different
    wording (e.g. "automotive" vs yfinance's "Auto Manufacturers"). Always
    includes the original requested terms verbatim, so a sector with no
    curated entry still gets a literal match instead of matching nothing."""
    policy = _load_policy()
    requested = [s.lower() for s in priority_sectors]
    keywords: set[str] = set(requested)

    for sector_name, entry in policy.items():
        if not isinstance(entry, dict) or not _matches(requested, sector_name, entry):
            continue
        keywords.add(sector_name.lower())
        keywords.update(k.lower() for k in (entry.get("keywords") or []))
    return list(keywords)
