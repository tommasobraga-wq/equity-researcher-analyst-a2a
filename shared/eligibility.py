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

import re
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


# Geographic perimeter: US + EU-27 only (UK/LSE and Switzerland explicitly out,
# per the domain constraint "US and EU equities only"). Classification is by
# issuer DOMICILE (yfinance `country`), not listing venue — a US-listed ADR of a
# UK issuer (e.g. AZN on NYSE, market="us_market", country="United Kingdom") must
# be excluded, while an EU issuer trading as a US ADR (e.g. SAP/STM on NYSE,
# country="Germany"/"Netherlands") stays in. `market` is only a fallback when
# `country` is absent.
_ALLOWED_COUNTRIES = {
    c.lower()
    for c in (
        "United States",
        "Austria",
        "Belgium",
        "Bulgaria",
        "Croatia",
        "Cyprus",
        "Czechia",
        "Czech Republic",
        "Denmark",
        "Estonia",
        "Finland",
        "France",
        "Germany",
        "Greece",
        "Hungary",
        "Ireland",
        "Italy",
        "Latvia",
        "Lithuania",
        "Luxembourg",
        "Malta",
        "Netherlands",
        "Poland",
        "Portugal",
        "Romania",
        "Slovakia",
        "Slovenia",
        "Spain",
        "Sweden",
    )
}
# ISO-ish country codes as they appear in yfinance's `market` field ("<cc>_market").
_ALLOWED_MARKET_CC = {
    "us",
    "at",
    "be",
    "bg",
    "hr",
    "cy",
    "cz",
    "dk",
    "ee",
    "fi",
    "fr",
    "de",
    "gr",
    "hu",
    "ie",
    "it",
    "lv",
    "lt",
    "lu",
    "mt",
    "nl",
    "pl",
    "pt",
    "ro",
    "sk",
    "si",
    "es",
    "se",
}


def check_market_perimeter(
    fundamentals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Splits `fundamentals` into (eligible, excluded) by geographic perimeter:
    only US/EU-27-domiciled issuers pass. Deterministic Gate 1 check — replaces
    reliance on the LLM to honor the "US and EU only, UK/LSE excluded" rule
    (which leaked, e.g. AstraZeneca's US-listed ADR). Blocked entries use the
    CandidatoEscluso shape for merging into the final report's candidati_esclusi."""
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for record in fundamentals:
        country = (record.get("country") or "").strip()
        market = (record.get("market") or "").strip().lower()

        if country:
            in_perimeter = country.lower() in _ALLOWED_COUNTRIES
            reason = f"Fuori perimetro US/EU: emittente domiciliato in {country}."
        elif market.endswith("_market"):
            in_perimeter = market[: -len("_market")] in _ALLOWED_MARKET_CC
            reason = f"Fuori perimetro US/EU: quotazione sul mercato '{market}'."
        else:
            in_perimeter = False  # can't determine domicile/venue → exclude conservatively
            reason = "Perimetro US/EU non determinabile (paese e mercato mancanti nei dati)."

        if in_perimeter:
            eligible.append(record)
        else:
            excluded.append({"ticker": record.get("ticker", ""), "motivo_esclusione": reason})
    return eligible, excluded


def check_data_quality(
    fundamentals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Splits `fundamentals` records into (eligible, dropped). A record is
    dropped when it has no usable price (``price`` missing, non-numeric, or
    ``<= 0``) — i.e. the symbol did not resolve to a real, priced equity
    (invalid/misspelled ticker, delisted, or no market data on yfinance).

    Deterministic, zero LLM cost — the same "drop it, don't process it" spirit
    as the restricted-list/ESG checks. Both pipeline topologies converge on the
    shared DataCollector node, so filtering here covers user-supplied tickers
    ("specific" mode) and NewsSentiment-proposed candidates ("discovery" mode)
    alike. Blocked entries use the CandidatoEscluso shape so they can be merged
    straight into the final report's candidati_esclusi."""
    eligible: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for record in fundamentals:
        price = record.get("price")
        try:
            usable = price is not None and float(price) > 0
        except (TypeError, ValueError):
            usable = False
        if usable:
            eligible.append(record)
        else:
            dropped.append(
                {
                    "ticker": record.get("ticker", ""),
                    "motivo_esclusione": (
                        "Dati fondamentali non disponibili (prezzo mancante) — "
                        "ticker non risolto a un'azione quotata."
                    ),
                }
            )
    return eligible, dropped


def check_sector_scope(
    fundamentals: list[dict[str, Any]],
    priority_sectors: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Splits `fundamentals` into (eligible, blocked) by matching each record's
    sector/industry against `priority_sectors`, expanded via
    shared.sector_seed.expand_sector_keywords into the keyword set that
    actually matches yfinance's own sector/industry taxonomy — the user's
    free-text sector ("automotive") often does not literally appear there
    (Tesla is sector="Consumer Cyclical", industry="Auto Manufacturers"), so
    a plain substring match on `priority_sectors` alone would wrongly reject
    genuine matches. Same "keep only matches" mechanics as
    check_esg_exclusions but inverted (that one drops matches).

    Only call this when the user explicitly scoped the request to specific
    sector(s) — NewsSentiment's discovery prompt treats priority_sectors as a
    soft preference ("prefer, don't exclude"), so an out-of-scope candidate
    (e.g. a Healthcare ticker surfacing from generic RSS coverage when the
    user asked for "resources/utilities") can otherwise slip through to the
    final report. Do not use this for the default soft priority-sector nudge."""
    from shared.sector_seed import expand_sector_keywords

    # Word-boundary match, not plain substring: a naive "s in haystack" lets
    # a short keyword like "car" (from the automotive keyword set) falsely
    # match inside an unrelated word (e.g. "Healthcare").
    patterns = [
        re.compile(r"\b" + re.escape(s) + r"\b") for s in expand_sector_keywords(priority_sectors)
    ]

    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    for record in fundamentals:
        haystack = f"{record.get('sector', '')} {record.get('industry', '')}".lower()
        if any(p.search(haystack) for p in patterns):
            eligible.append(record)
        else:
            blocked.append(
                {
                    "ticker": record.get("ticker", ""),
                    "motivo_esclusione": (
                        f"Fuori dal settore richiesto ({', '.join(priority_sectors)})."
                    ),
                }
            )
    return eligible, blocked


def check_country_scope(
    fundamentals: list[dict[str, Any]],
    countries: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Splits `fundamentals` into (eligible, blocked) by matching each
    record's issuer domicile (yfinance `country`) against `countries`,
    case-insensitive exact match — unlike check_sector_scope this needs no
    keyword expansion, since yfinance's `country` field is already a precise
    name (e.g. "Italy"), not a free-text taxonomy to bridge.

    Only call this when the user explicitly scoped the request to specific
    country/countries — NewsSentiment's discovery prompt otherwise treats the
    coordinator's `focus` text as a soft hint, so without this backstop a
    candidate from an unrelated country (e.g. a US tech ticker surfacing from
    the generic RSS scan when the user asked for "mercato azionario
    italiano") can reach the final report. Do not use this for the default
    unscoped discovery flow."""
    requested = {c.lower() for c in countries}

    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    for record in fundamentals:
        country = (record.get("country") or "").strip()
        if country.lower() in requested:
            eligible.append(record)
        else:
            blocked.append(
                {
                    "ticker": record.get("ticker", ""),
                    "motivo_esclusione": (
                        f"Fuori dal paese/mercato richiesto ({', '.join(countries)}): "
                        f"emittente domiciliato in {country or 'paese non determinabile'}."
                    ),
                }
            )
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
            blocked.append(
                {
                    "ticker": record.get("ticker", ""),
                    "motivo_esclusione": f"Settore escluso da policy ESG: {hit}.",
                }
            )
        else:
            eligible.append(record)
    return eligible, blocked
