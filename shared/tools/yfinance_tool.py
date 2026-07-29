"""YFinance wrapper — standalone, no crewai dependency."""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError

import yfinance as yf

from shared.sanitize import sanitize_external_text

_TIMEOUT = 15  # seconds per ticker

_REC_KEY_MAP = {
    "strongbuy": "Strong Buy",
    "buy": "Buy",
    "hold": "Hold",
    "neutral": "Hold",
    "underperform": "Sell",
    "sell": "Sell",
    "strongsell": "Strong Sell",
}


def _derive_consensus(rec_key: str, rec_mean) -> str:
    normalized = rec_key.lower().replace(" ", "").replace("_", "")
    if normalized in _REC_KEY_MAP:
        return _REC_KEY_MAP[normalized]
    try:
        mean = float(rec_mean)
        if mean <= 1.5:
            return "Strong Buy"
        if mean <= 2.5:
            return "Buy"
        if mean <= 3.5:
            return "Hold"
        if mean <= 4.5:
            return "Sell"
        return "Strong Sell"
    except (TypeError, ValueError):
        return "N/A"


def _fetch(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.info

    strong_buy = buy = hold = sell = strong_sell = 0
    try:
        recs = stock.recommendations_summary
        if recs is not None and not recs.empty:
            latest = recs.iloc[0]
            strong_buy = int(latest.get("strongBuy", 0))
            buy = int(latest.get("buy", 0))
            hold = int(latest.get("hold", 0))
            sell = int(latest.get("sell", 0))
            strong_sell = int(latest.get("strongSell", 0))
    except Exception:
        pass

    rec_key = info.get("recommendationKey", "") or ""
    rec_mean = info.get("recommendationMean", "")

    return {
        "ticker": ticker,
        "price": info.get("currentPrice", info.get("regularMarketPrice")),
        "pe_ttm": info.get("trailingPE"),
        "pe_forward": info.get("forwardPE"),
        "eps_ttm": info.get("trailingEps"),
        "week52_low": info.get("fiftyTwoWeekLow"),
        "week52_high": info.get("fiftyTwoWeekHigh"),
        "market_cap": info.get("marketCap"),
        "analyst_target_avg": info.get("targetMeanPrice"),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "rec_key": rec_key or None,
        "rec_mean": rec_mean or None,
        "consensus": _derive_consensus(str(rec_key), rec_mean),
        "analyst_breakdown": {
            "strong_buy": strong_buy,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": strong_sell,
        },
        # sector/industry/longName are free-text fields sourced from Yahoo Finance's
        # own metadata — externally influenceable, sanitized before reaching any prompt.
        "sector": sanitize_external_text(info.get("sector"), max_len=100) or None,
        "industry": sanitize_external_text(info.get("industry"), max_len=100) or None,
        # Geographic-perimeter fields (Gate 1, shared/eligibility.py::check_market_perimeter).
        # `country` = issuer domicile (e.g. "United Kingdom" even for a US-listed ADR);
        # `market` = listing venue country code (e.g. "us_market", "de_market", "gb_market").
        "country": sanitize_external_text(info.get("country"), max_len=60) or None,
        "market": sanitize_external_text(info.get("market"), max_len=40) or None,
    }


def get_stock_fundamentals(ticker: str) -> dict:
    """Fetch fundamentals for a ticker. Returns dict; raises on timeout/error."""
    ticker = ticker.strip().upper()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_fetch, ticker)
            return future.result(timeout=_TIMEOUT)
    except FuturesTimeoutError:
        raise TimeoutError(f"yfinance timeout after {_TIMEOUT}s for {ticker}")


def get_ticker_news(ticker: str, max_items: int = 5) -> list[dict]:
    """Per-ticker news via yfinance (`Ticker.news`) — unlike the generic RSS
    feeds (shared/tools/rss_feed.py, top-headlines, not company-targeted),
    this is scoped to the requested ticker itself, so it can surface a
    niche/local story (e.g. an EU-listed company's tender offer) that would
    never make a generic top-30 global-headlines pool. Best-effort: returns
    [] on timeout/error/no coverage rather than raising, since thin news
    coverage for a given ticker is an expected outcome, not a failure."""
    ticker = ticker.strip().upper()

    def _fetch_news() -> list[dict]:
        items = yf.Ticker(ticker).news or []
        out = []
        for item in items[:max_items]:
            content = item.get("content", {}) or {}
            provider = content.get("provider", {}) or {}
            url = content.get("canonicalUrl", {}) or {}
            out.append({
                "headline": sanitize_external_text(content.get("title"), max_len=200),
                "summary": sanitize_external_text(content.get("summary"), max_len=300),
                "source": (
                    sanitize_external_text(provider.get("displayName"), max_len=100)
                    or "Yahoo Finance"
                ),
                "url": sanitize_external_text(url.get("url"), max_len=500),
                "published": content.get("pubDate"),
            })
        return out

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_fetch_news).result(timeout=_TIMEOUT)
    except Exception:
        return []


def get_daily_returns(ticker: str, days: int = 180) -> list[float]:
    """Daily close-to-close returns over the last `days` calendar days —
    used by Gate 3's pairwise-correlation check. Raises on timeout/error;
    the caller (orchestrator) treats failures as best-effort."""
    ticker = ticker.strip().upper()

    def _history() -> list[float]:
        closes = yf.Ticker(ticker).history(period=f"{days}d")["Close"].tolist()
        return [
            curr / prev - 1
            for prev, curr in zip(closes, closes[1:])
            if prev
        ]

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_history).result(timeout=_TIMEOUT)
    except FuturesTimeoutError:
        raise TimeoutError(f"yfinance history timeout after {_TIMEOUT}s for {ticker}")


def get_stock_fundamentals_text(ticker: str) -> str:
    """Same as get_stock_fundamentals but returns a formatted string."""
    try:
        d = get_stock_fundamentals(ticker)
        bd = d["analyst_breakdown"]
        return (
            f"Ticker: {d['ticker']}\n"
            f"Price: {d['price']}\n"
            f"P/E (TTM): {d['pe_ttm']}\n"
            f"Forward P/E: {d['pe_forward']}\n"
            f"EPS (TTM): {d['eps_ttm']}\n"
            f"52-week range: {d['week52_low']} - {d['week52_high']}\n"
            f"Market Cap: {d['market_cap']}\n"
            f"Analyst target (avg): {d['analyst_target_avg']}\n"
            f"Analyst count: {d['analyst_count']}\n"
            f"Recommendation key: {d['rec_key'] or 'N/A'}\n"
            f"Recommendation mean: {d['rec_mean'] or 'N/A'}\n"
            f"Consensus: {d['consensus']}\n"
            f"Breakdown — StrongBuy:{bd['strong_buy']} Buy:{bd['buy']} "
            f"Hold:{bd['hold']} Sell:{bd['sell']} StrongSell:{bd['strong_sell']}\n"
            f"Sector: {d['sector']}\n"
            f"Industry: {d['industry']}"
        )
    except TimeoutError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching {ticker}: {e}"
