"""Integration tests — chiamate LLM reali, un ticker leggero (AAPL).

Eseguiti solo con:  pytest -m integration
Saltati di default: pytest (senza flag) li esclude automaticamente.

Ogni test verifica che la risposta A2A sia strutturalmente corretta
e che il campo status sia "completed".
"""
import json
import pytest
import httpx
from conftest import base_url, a2a_payload

pytestmark = pytest.mark.integration

TICKER = "AAPL"


def _rpc_result(r: httpx.Response) -> dict:
    """Estrae il campo result dal JSON-RPC response e verifica l'assenza di errori."""
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("error") is None, f"RPC error: {body['error']}"
    return body["result"]


# ── DataCollector ─────────────────────────────────────────────────────────────

def test_data_collector(http: httpx.Client):
    r = http.post(
        f"{base_url(8001)}/tasks",
        json=a2a_payload(f"Fetch fundamentals for {TICKER}"),
    )
    result = _rpc_result(r)
    assert result["status"] == "completed"
    parts = result["message"]["parts"]
    data_parts = [p for p in parts if p["type"] == "data"]
    assert data_parts, "Nessun DataPart nella risposta"
    fundamentals = data_parts[0]["data"].get("fundamentals")
    assert fundamentals is not None


# ── NewsSentiment ─────────────────────────────────────────────────────────────

def test_news_sentiment(http: httpx.Client):
    r = http.post(
        f"{base_url(8002)}/tasks",
        json=a2a_payload(f"Analyze recent news sentiment for {TICKER}"),
    )
    result = _rpc_result(r)
    assert result["status"] == "completed"


# ── FundamentalAnalyst ────────────────────────────────────────────────────────

_DUMMY_FUNDAMENTALS = json.dumps([{
    "ticker": TICKER,
    "name": "Apple Inc.",
    "sector": "Technology",
    "pe_ratio": 28.5,
    "market_cap": 3e12,
    "revenue_growth": 0.08,
    "profit_margin": 0.25,
}])

_DUMMY_NEWS = json.dumps({
    "articles": [{"title": "Apple reports record revenue", "sentiment": "positive"}],
    "themes": ["AI expansion", "Services growth"],
})


def test_fundamental_analyst(http: httpx.Client):
    text = (
        f"Fundamentals: {_DUMMY_FUNDAMENTALS}\n"
        f"News and themes: {_DUMMY_NEWS}\n"
        f"Identify the best equity candidates."
    )
    r = http.post(f"{base_url(8003)}/tasks", json=a2a_payload(text))
    result = _rpc_result(r)
    assert result["status"] == "completed"


# ── RiskAssessor ──────────────────────────────────────────────────────────────

_DUMMY_CANDIDATES = json.dumps([{
    "ticker": TICKER,
    "name": "Apple Inc.",
    "rationale": "Strong AI services growth",
    "conviction": "high",
}])


def test_risk_assessor(http: httpx.Client):
    text = (
        f"Candidates: {_DUMMY_CANDIDATES}\n"
        f"Fundamentals: {_DUMMY_FUNDAMENTALS}\n"
        f"Assess risk and score each candidate."
    )
    r = http.post(f"{base_url(8004)}/tasks", json=a2a_payload(text))
    result = _rpc_result(r)
    assert result["status"] == "completed"


# ── ReportWriter ──────────────────────────────────────────────────────────────

_DUMMY_SCORED = json.dumps([{
    "ticker": TICKER,
    "name": "Apple Inc.",
    "scores": {"growth": 8, "quality": 9, "momentum": 7, "risk": 8, "valuation": 6},
    "total_score": 38,
    "recommendation": "BUY",
}])


def test_report_writer(http: httpx.Client):
    text = (
        f"Scored candidates: {_DUMMY_SCORED}\n"
        f"Fundamentals: {_DUMMY_FUNDAMENTALS}\n"
        f"News: {_DUMMY_NEWS}\n"
        f"Write the final Italian investment report."
    )
    r = http.post(f"{base_url(8005)}/tasks", json=a2a_payload(text))
    result = _rpc_result(r)
    assert result["status"] == "completed"
    text_parts = [p for p in result["message"]["parts"] if p["type"] == "text"]
    assert text_parts, "Nessun testo nel report"
    report_text = text_parts[0]["text"]
    assert "SINTESI" in report_text.upper() or len(report_text) > 200
