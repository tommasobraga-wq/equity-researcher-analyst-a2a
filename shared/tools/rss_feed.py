"""RSS feed reader — standalone, no crewai dependency."""
import time

import feedparser

_MAX_RETRIES = 2
_RETRY_BACKOFF = 2  # seconds
_ITEMS_PER_FEED = 5

RSS_FEEDS = {
    "Reuters Markets": "https://feeds.reuters.com/reuters/businessNews",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Investing.com": "https://www.investing.com/rss/news.rss",
    "Reuters Technology": "https://feeds.reuters.com/reuters/technologyNews",
    "Investing.com EU": "https://www.investing.com/rss/news_14.rss",
}


def fetch_rss_news(max_items_per_feed: int = _ITEMS_PER_FEED) -> str:
    """Fetch financial news from all RSS feeds. Returns formatted string."""
    items: list[str] = []
    failed: list[str] = []

    for source, url in RSS_FEEDS.items():
        entries = None
        for attempt in range(1 + _MAX_RETRIES):
            try:
                feed = feedparser.parse(url)
                if feed.entries:
                    entries = feed.entries
                    break
            except Exception:
                pass
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF)

        if entries:
            for entry in entries[:max_items_per_feed]:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))[:300]
                link = entry.get("link", "")
                items.append(f"[{source}] {title}\n{summary}\nURL: {link}")
        else:
            failed.append(source)

    if not items:
        raise RuntimeError(
            f"No articles retrieved from any RSS feed "
            f"({', '.join(failed)}). Check network connectivity."
        )

    return "\n\n---\n\n".join(items)


def get_feed_status() -> dict[str, bool]:
    """Return {source: ok} dict after a quick probe of each feed."""
    status: dict[str, bool] = {}
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            status[source] = bool(feed.entries)
        except Exception:
            status[source] = False
    return status
