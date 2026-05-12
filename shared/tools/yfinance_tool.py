"""YFinance wrapper — standalone, no crewai dependency."""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError

import yfinance as yf

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
        "sector": info.get("sector"),
        "industry": info.get("industry"),
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
