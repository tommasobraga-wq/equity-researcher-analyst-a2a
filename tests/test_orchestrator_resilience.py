"""Unit tests for orchestrator/main.py — resume entry routing + router logic.

No network/DB involved: pure in-memory logic.
"""
import orchestrator.main as om

_EMPTY_STAGES = {
    "tickers": [], "candidate_tickers": [], "fundamentals": [], "news": [], "themes": [],
    "candidates": [], "risk_assessment": [],
}


def test_entry_router_specific_fresh_state_starts_at_parallel_node():
    state = {**_EMPTY_STAGES, "mode": "specific"}
    assert om._entry_router(state) == ["data_news_parallel"]


def test_entry_router_specific_resumes_at_fundamental_analyst_once_both_done():
    state = {
        **_EMPTY_STAGES, "mode": "specific",
        "fundamentals": [{"ticker": "AAPL"}], "news": [{"id": "N1"}],
    }
    assert om._entry_router(state) == "fundamental_analyst"


def test_entry_router_discovery_fresh_state_starts_at_news_sentiment():
    state = {**_EMPTY_STAGES, "mode": "discovery"}
    assert om._entry_router(state) == "news_sentiment_discovery"


def test_entry_router_discovery_resumes_at_data_collector_from_candidates():
    state = {
        **_EMPTY_STAGES, "mode": "discovery",
        "news": [{"id": "N1"}], "themes": [{"id": "T1"}],
    }
    assert om._entry_router(state) == "data_collector_from_candidates"


def test_entry_router_discovery_resumes_at_fundamental_analyst_once_both_done():
    state = {
        **_EMPTY_STAGES, "mode": "discovery",
        "news": [{"id": "N1"}], "fundamentals": [{"ticker": "AAPL"}],
    }
    assert om._entry_router(state) == "fundamental_analyst"


def test_entry_router_resumes_after_fundamental_analyst():
    state = {
        **_EMPTY_STAGES, "mode": "specific",
        "fundamentals": [{"ticker": "AAPL"}], "news": [{"id": "N1"}],
        "candidates": [{"ticker": "AAPL"}],
    }
    assert om._entry_router(state) == "risk_assessor"


def test_entry_router_resumes_after_risk_assessor():
    state = {
        **_EMPTY_STAGES, "mode": "specific",
        "fundamentals": [{"ticker": "AAPL"}], "news": [{"id": "N1"}],
        "candidates": [{"ticker": "AAPL"}], "risk_assessment": [{"ticker": "AAPL"}],
        "compliance_checked": False,
    }
    assert om._entry_router(state) == "compliance_agent"


def test_entry_router_resumes_after_compliance_agent():
    state = {
        **_EMPTY_STAGES, "mode": "specific",
        "fundamentals": [{"ticker": "AAPL"}], "news": [{"id": "N1"}],
        "candidates": [{"ticker": "AAPL"}], "risk_assessment": [{"ticker": "AAPL"}],
        "compliance_checked": True,
    }
    assert om._entry_router(state) == "portfolio_manager"


def test_entry_router_resumes_after_portfolio_manager():
    state = {
        **_EMPTY_STAGES, "mode": "specific",
        "fundamentals": [{"ticker": "AAPL"}], "news": [{"id": "N1"}],
        "candidates": [{"ticker": "AAPL"}], "risk_assessment": [{"ticker": "AAPL"}],
        "compliance_checked": True,
        "allocation": [{"ticker": "AAPL", "peso_pct": 25.0, "razionale": "x"}],
    }
    assert om._entry_router(state) == "report_writer"


def test_dual_router_advances_only_when_both_stages_clean():
    router = om._make_dual_router(
        "data_collector", "news_sentiment", "fundamental_analyst", "data_news_parallel",
    )
    assert router({"retries": {}, "validation_feedback": {}}) == "fundamental_analyst"
    assert router(
        {"retries": {"data_collector": 1}, "validation_feedback": {}}
    ) == "data_news_parallel"
    assert router(
        {"retries": {"news_sentiment": 1}, "validation_feedback": {}}
    ) == "data_news_parallel"


def test_dual_router_raises_after_max_retries():
    router = om._make_dual_router(
        "data_collector", "news_sentiment", "fundamental_analyst", "data_news_parallel",
    )
    try:
        router({
            "retries": {"news_sentiment": om.MAX_VALIDATION_RETRIES + 1},
            "validation_feedback": {},
        })
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_make_router_loop_node_overrides_stage_name():
    router = om._make_router(
        "news_sentiment", "data_collector_from_candidates",
        loop_node="news_sentiment_discovery",
    )
    assert router(
        {"retries": {}, "validation_feedback": {}}
    ) == "data_collector_from_candidates"
    assert router(
        {"retries": {"news_sentiment": 1}, "validation_feedback": {}}
    ) == "news_sentiment_discovery"


def test_merge_partial_results_does_not_clobber_concurrent_updates():
    state = {"retries": {"data_collector": 0, "news_sentiment": 0}, "validation_feedback": {}}
    r1 = {
        "fundamentals": [{"ticker": "AAPL"}],
        "retries": {"data_collector": 0, "news_sentiment": 0},
    }
    r2 = {
        "retries": {"data_collector": 0, "news_sentiment": 1},
        "validation_feedback": {"news_sentiment": "bad format"},
    }
    merged = om._merge_partial_results(state, [r1, r2])
    assert merged["fundamentals"] == [{"ticker": "AAPL"}]
    assert merged["retries"] == {"data_collector": 0, "news_sentiment": 1}
    assert merged["validation_feedback"] == {"news_sentiment": "bad format"}
