"""Deterministic constraint validators — zero LLM cost."""
from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from shared.models import Report
from shared.schemas import (
    AllocationItem,
    CandidateItem,
    FundamentalRecord,
    NewsThemesOutput,
    RiskItem,
)


@dataclass
class Violation:
    rule: str
    severity: str   # "error" | "warning"
    ticker: str | None
    message: str

    def as_dict(self) -> dict:
        return {
            "rule": self.rule, "severity": self.severity,
            "ticker": self.ticker, "message": self.message,
        }


_CRYPTO_KEYWORDS = {
    "btc", "bitcoin", "eth", "ethereum", "crypto", "cryptocurrency",
    "defi", "nft", "blockchain", "web3", "token", "altcoin",
    "binance", "solana", "cardano", "ripple", "xrp", "dogecoin",
    "stablecoin", "litecoin", "polkadot", "avalanche",
}

_CRYPTO_RE = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in _CRYPTO_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_DIRECTIVE_RE = re.compile(
    r"\b(comprat[eio]|vendete?|acquistat[eio]|shortate?|"
    r"buy\s+now|sell\s+now|acquistare\s+subito|comprare\s+subito|"
    r"investite\s+in|entrate\s+su)\b",
    re.IGNORECASE,
)

_NEWS_ID_RE = re.compile(r"^N\d+$")

# Deterministic first line of defense against prompt injection smuggled in via
# external content (RSS headlines/summaries, yfinance sector/industry) that
# survived into a stage's LLM-produced output — catches instruction-like
# phrasing, not just topical keywords. Complements (doesn't replace) the
# framing/delimiting defense in each agent's system prompt and the
# sanitization in shared/sanitize.py.
_INJECTION_RE = re.compile(
    r"(ignor[ae]\s+(tutte\s+le\s+|le\s+)?istruzioni|"
    r"disregard\s+(all\s+|previous\s+|above\s+)?instructions|"
    r"ignore\s+(all\s+|previous\s+|above\s+)?instructions|"
    r"new\s+instructions\s*:|nuove\s+istruzioni\s*:|"
    r"system\s*:|assistant\s*:|\bhuman\s*:|###\s*(system|instruction)|you\s+are\s+now|"
    r"sei\s+ora\s+un|d'ora\s+in\s+poi\s+(sei|agisci))",
    re.IGNORECASE,
)

_SCORING_DIMS = [
    "forza_catalizzatore", "fit_orizzonte", "asimmetria_narrativa",
    "qualita_evidenze", "rischio_crowding",
]

_VALID_GIUDIZI = {"strong buy", "buy", "hold", "sell", "strong sell", "n/a"}
_VALID_RATINGS = {"alta", "media", "bassa"}
_VALID_MARKETS = {"US", "EU"}


def _check_crypto(text: str) -> set[str]:
    return {m.group().lower() for m in _CRYPTO_RE.finditer(text)}


def _check_directive(text: str):
    return _DIRECTIVE_RE.search(text)


def _check_injection(text: str):
    return _INJECTION_RE.search(text)


def _collect_strings(obj) -> list[str]:
    """Recursively flattens all string leaf values out of a pydantic model /
    dict / list, for a broad prompt-injection sweep across every field —
    unlike the narrower per-field crypto/directive checks, injection markers
    can show up anywhere (a fabricated 'source' name, a theme title, ...)."""
    if isinstance(obj, BaseModel):
        return _collect_strings(obj.model_dump())
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out.extend(_collect_strings(v))
        return out
    if isinstance(obj, (list, tuple)):
        out = []
        for v in obj:
            out.extend(_collect_strings(v))
        return out
    if isinstance(obj, str):
        return [obj]
    return []


def _check_scoring_arithmetic(scoring) -> tuple[bool, int]:
    """Returns (matches, expected_total)."""
    expected = sum(getattr(scoring, d) for d in _SCORING_DIMS)
    total = getattr(scoring, "totale", None)
    return total == expected, expected


# ------------------------------------------------------------------ #
# Pre-QA deterministic checks — run on the agent's raw dict output    #
# before the LLM-QA pass, so format/arithmetic issues are caught at   #
# zero LLM cost and never rely on a model to re-derive a fixed rule.  #
# ------------------------------------------------------------------ #

def check_candidates_deterministic(candidates: list[dict]) -> list[str]:
    """Perimeter/format checks for FundamentalAnalyst output: UK ticker
    suffix and explicit buy/sell directives — both fixed rules, not
    judgment calls, so no LLM is needed to verify them."""
    errors: list[str] = []
    for c in candidates:
        ticker = c.get("ticker", "")
        if ticker.upper().endswith(".L"):
            errors.append(f"{ticker}: ticker LSE (suffisso .L) — fuori dall'universo US/EU.")

        text = " ".join(str(c.get(f, "")) for f in ("thesis", "catalyst"))
        match = _check_directive(text)
        if match:
            errors.append(
                f"{ticker}: direttiva esplicita di acquisto/vendita trovata — '{match.group()}'."
            )
    return errors


def check_risk_scoring_deterministic(risk_assessment: list[dict]) -> list[str]:
    """scoring.totale must equal the exact sum of the 5 dimensions — pure
    arithmetic, unless the candidate is marked dati_insufficienti (all
    scores intentionally 0)."""
    errors: list[str] = []
    for r in risk_assessment:
        if r.get("quality") == "dati_insufficienti":
            continue
        ticker = r.get("ticker", "")
        scoring = r.get("scoring", {}) or {}
        expected = sum(scoring.get(d, 0) for d in _SCORING_DIMS)
        total = scoring.get("totale")
        if total != expected:
            errors.append(f"{ticker}: scoring.totale={total} ma somma dimensioni={expected}.")
    return errors


def check_compliance_format_deterministic(
    compliance_results: list[dict], expected_tickers: list[str],
) -> list[str]:
    """Presence/non-empty checks on compliance verdicts: every expected
    ticker has exactly one verdict, and a compliant=false verdict always
    carries a non-empty motivo + at least one policy_ref."""
    errors: list[str] = []
    seen: dict[str, int] = {}
    for r in compliance_results:
        ticker = r.get("ticker", "")
        seen[ticker] = seen.get(ticker, 0) + 1
        if not r.get("compliant", True):
            if not str(r.get("motivo", "")).strip():
                errors.append(f"{ticker}: compliant=false ma 'motivo' vuoto.")
            if not r.get("policy_refs"):
                errors.append(f"{ticker}: compliant=false ma 'policy_refs' vuoto.")

    dupes = [t for t, n in seen.items() if n > 1]
    if dupes:
        errors.append(f"Verdetti duplicati per: {', '.join(sorted(dupes))}.")

    missing = set(expected_tickers) - set(seen)
    if missing:
        errors.append(f"Verdetti mancanti per: {', '.join(sorted(missing))}.")

    return errors


def _full_text(c) -> str:
    parts = [
        c.tesi, c.catalizzatore, c.trigger_falsificazione,
        c.scenari.base, c.scenari.bull, c.scenari.bear,
        c.rischi.macro, c.rischi.settore, c.rischi.azienda,
        c.rischi.regolatorio, c.rischi.valutazione,
    ] + c.prossime_verifiche
    return " ".join(p for p in parts if p)


def _full_text_tema(t) -> str:
    parts = [t.titolo, t.perche_ora] + list(t.indicatori_da_monitorare)
    return " ".join(p for p in parts if p)


def validate(report: Report | None) -> list[Violation]:
    violations: list[Violation] = []

    if report is None:
        violations.append(Violation(
            rule="report_parsable", severity="error", ticker=None,
            message="Il report non è parsabile: JSON mancante, troncato o malformato.",
        ))
        return violations

    if len(report.candidati) > 5:
        violations.append(Violation(
            rule="candidate_count", severity="warning", ticker=None,
            message=f"Report contiene {len(report.candidati)} candidati (massimo 5).",
        ))

    for c in report.candidati:
        text = _full_text(c)

        if c.ticker.upper().endswith(".L"):
            violations.append(Violation(rule="no_uk_stocks", severity="error", ticker=c.ticker,
                message=f"{c.ticker}: titolo LSE (UK) — escluso dall'universo."))

        crypto_hits = _check_crypto(f"{c.ticker} {c.azienda}")
        if crypto_hits:
            violations.append(Violation(rule="no_crypto", severity="error", ticker=c.ticker,
                message=f"{c.ticker}: keyword crypto rilevata ({', '.join(sorted(crypto_hits))})."))

        if c.mercato not in _VALID_MARKETS:
            violations.append(Violation(rule="market_scope", severity="error", ticker=c.ticker,
                message=f"{c.ticker}: mercato='{c.mercato}' — solo US e EU ammessi."))

        match = _check_directive(text)
        if match:
            violations.append(Violation(
                rule="no_buy_sell_directives", severity="error", ticker=c.ticker,
                message=f"{c.ticker}: direttiva esplicita trovata — '{match.group()}'.",
            ))

        if len(c.evidenze_citate) < 2:
            violations.append(Violation(
                rule="citation_count", severity="warning", ticker=c.ticker,
                message=f"{c.ticker}: {len(c.evidenze_citate)} news citate (minimo 2).",
            ))
        for nid in c.evidenze_citate:
            if not _NEWS_ID_RE.match(nid):
                violations.append(Violation(
                    rule="citation_format", severity="warning", ticker=c.ticker,
                    message=(
                        f"{c.ticker}: ID news non valido '{nid}' "
                        "(formato atteso: N1, N2, ...)."
                    ),
                ))

        matches, expected = _check_scoring_arithmetic(c.scoring)
        if not matches:
            violations.append(Violation(
                rule="score_arithmetic", severity="error", ticker=c.ticker,
                message=(
                    f"{c.ticker}: scoring.totale={c.scoring.totale} "
                    f"ma somma dimensioni={expected}."
                ),
            ))

        for dim in _SCORING_DIMS:
            val = getattr(c.scoring, dim)
            if not (1 <= val <= 10):
                violations.append(Violation(
                    rule="score_range", severity="error", ticker=c.ticker,
                    message=f"{c.ticker}: scoring.{dim}={val} fuori range 1–10.",
                ))

        if c.rating_qualita.lower() not in _VALID_RATINGS:
            violations.append(Violation(
                rule="quality_rating", severity="warning", ticker=c.ticker,
                message=(
                    f"{c.ticker}: rating_qualita='{c.rating_qualita}' "
                    "non è uno di alta|media|bassa."
                ),
            ))

        if c.consenso_analisti.giudizio_sintetico.lower() not in _VALID_GIUDIZI:
            violations.append(Violation(
                rule="consensus_giudizio", severity="warning", ticker=c.ticker,
                message=(
                    f"{c.ticker}: giudizio_sintetico="
                    f"'{c.consenso_analisti.giudizio_sintetico}' non è un valore standard."
                ),
            ))

    for t in report.temi:
        text = _full_text_tema(t)
        crypto_hits = _check_crypto(text)
        if crypto_hits:
            crypto_str = ", ".join(sorted(crypto_hits))
            violations.append(Violation(
                rule="no_crypto", severity="error", ticker=None,
                message=f"Tema '{t.tema_id}': keyword crypto rilevata ({crypto_str}).",
            ))
        match = _check_directive(text)
        if match:
            violations.append(Violation(
                rule="no_buy_sell_directives", severity="error", ticker=None,
                message=f"Tema '{t.tema_id}': direttiva esplicita trovata — '{match.group()}'.",
            ))

    return violations


# ------------------------------------------------------------------ #
# Per-stage validation gate (schema + business rules)                  #
# ------------------------------------------------------------------ #

_STAGE_SCHEMAS: dict[str, type[BaseModel]] = {
    "fundamentals": FundamentalRecord,
    "news_themes": NewsThemesOutput,
    "candidates": CandidateItem,
    "risk_assessment": RiskItem,
    "allocation": AllocationItem,
    "report": Report,
}

# Stages whose payload is a bare JSON array, validated item-by-item.
_LIST_STAGES = {"fundamentals", "candidates", "risk_assessment", "allocation"}


def validate_stage(stage: str, payload) -> tuple[object | None, list[Violation]]:
    """Validate a single pipeline stage's raw output.

    Returns (parsed_model_or_list, violations). `parsed_model_or_list` is
    None if schema validation failed outright. Business-rule checks (crypto
    keywords, buy/sell directives, scoring arithmetic) run only on
    schema-valid data.
    """
    schema = _STAGE_SCHEMAS.get(stage)
    if schema is None:
        raise ValueError(f"Unknown validation stage: {stage!r}")

    violations: list[Violation] = []

    if stage == "report":
        return None, validate(payload) if payload is not None else [
            Violation(rule="report_parsable", severity="error", ticker=None,
                message="Il report non è parsabile: JSON mancante, troncato o malformato.")
        ]

    try:
        if stage in _LIST_STAGES:
            parsed = [schema.model_validate(item) for item in payload]
        else:
            parsed = schema.model_validate(payload)
    except (ValidationError, TypeError) as e:
        violations.append(Violation(
            rule="schema_parse", severity="error", ticker=None,
            message=f"{stage}: payload non conforme allo schema atteso — {e}",
        ))
        return None, violations

    items = parsed if stage in _LIST_STAGES else [parsed]
    for item in items:
        ticker = getattr(item, "ticker", None)

        injection_hit = _check_injection(" ".join(_collect_strings(item)))
        if injection_hit:
            violations.append(Violation(
                rule="injection_marker", severity="error", ticker=ticker,
                message=(
                    f"{stage}: marker di prompt injection rilevato — "
                    f"'{injection_hit.group()}'."
                ),
            ))

        text_fields = [
            getattr(item, f, "") for f in ("thesis", "catalyst", "falsification")
            if isinstance(getattr(item, f, ""), str)
        ]
        text = " ".join(text_fields)

        crypto_hits = _check_crypto(f"{ticker or ''} {text}")
        if crypto_hits:
            crypto_str = ", ".join(sorted(crypto_hits))
            violations.append(Violation(
                rule="no_crypto", severity="error", ticker=ticker,
                message=f"{stage}: keyword crypto rilevata ({crypto_str}).",
            ))

        match = _check_directive(text)
        if match:
            violations.append(Violation(
                rule="no_buy_sell_directives", severity="error", ticker=ticker,
                message=f"{stage}: direttiva esplicita trovata — '{match.group()}'.",
            ))

        scoring = getattr(item, "scoring", None)
        if scoring is not None and hasattr(scoring, "totale"):
            matches, expected = _check_scoring_arithmetic(scoring)
            if not matches:
                violations.append(Violation(
                    rule="score_arithmetic", severity="error", ticker=ticker,
                    message=(
                        f"{stage}: scoring.totale={scoring.totale} "
                        f"ma somma dimensioni={expected}."
                    ),
                ))

    if stage == "allocation":
        # Basic arithmetic sanity here (schema-level); the aggregate limit
        # checks — concentration, correlation, drawdown — are Gate 3
        # (shared/portfolio.py), which needs fundamentals/price history.
        total_weight = 0.0
        for item in parsed:
            if not (0 < item.peso_pct <= 100):
                violations.append(Violation(
                    rule="allocation_weight_range", severity="error", ticker=item.ticker,
                    message=f"{item.ticker}: peso_pct={item.peso_pct} fuori range (0, 100].",
                ))
            else:
                total_weight += item.peso_pct
            if not item.razionale.strip():
                violations.append(Violation(
                    rule="allocation_rationale", severity="error", ticker=item.ticker,
                    message=f"{item.ticker}: razionale mancante per il peso assegnato.",
                ))
        if total_weight > 100.01:
            violations.append(Violation(
                rule="allocation_weights_sum", severity="error", ticker=None,
                message=f"Somma dei pesi {total_weight:.1f}% > 100%.",
            ))

    return parsed, violations
