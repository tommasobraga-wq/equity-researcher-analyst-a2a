"""Unit tests for shared/eligibility.py — Gate 1 (restricted list, ESG exclusions).

Reads the real policies/restricted_list.yaml shipped with the repo (contains
a "RESTRICTEDCO" demo entry and ESG-excluded sectors "thermal coal",
"tobacco", "controversial weapons" — see that file for the source of truth).
"""
from shared.eligibility import check_esg_exclusions, check_restricted_list


def test_check_restricted_list_blocks_known_ticker():
    eligible, blocked = check_restricted_list(["AAPL", "RESTRICTEDCO", "MSFT"])
    assert eligible == ["AAPL", "MSFT"]
    assert len(blocked) == 1
    assert blocked[0]["ticker"] == "RESTRICTEDCO"
    assert blocked[0]["motivo_esclusione"]


def test_check_restricted_list_is_case_insensitive():
    eligible, blocked = check_restricted_list(["restrictedco"])
    assert eligible == []
    assert len(blocked) == 1


def test_check_restricted_list_no_blocks_passes_everything_through():
    eligible, blocked = check_restricted_list(["AAPL", "NVDA"])
    assert eligible == ["AAPL", "NVDA"]
    assert blocked == []


def test_check_restricted_list_empty_input():
    eligible, blocked = check_restricted_list([])
    assert eligible == []
    assert blocked == []


def test_check_esg_exclusions_blocks_excluded_sector():
    fundamentals = [
        {"ticker": "COAL1", "sector": "Energy", "industry": "Thermal Coal"},
        {"ticker": "AAPL", "sector": "Technology", "industry": "Consumer Electronics"},
    ]
    eligible, blocked = check_esg_exclusions(fundamentals)
    assert [f["ticker"] for f in eligible] == ["AAPL"]
    assert len(blocked) == 1
    assert blocked[0]["ticker"] == "COAL1"
    assert "coal" in blocked[0]["motivo_esclusione"].lower()


def test_check_esg_exclusions_handles_missing_sector_fields():
    fundamentals = [{"ticker": "AAPL"}]
    eligible, blocked = check_esg_exclusions(fundamentals)
    assert eligible == fundamentals
    assert blocked == []


def test_check_esg_exclusions_empty_input():
    eligible, blocked = check_esg_exclusions([])
    assert eligible == []
    assert blocked == []
