"""In-memory sliding-window rate limiter for the gateway's entry point
(gateway/app.py). Single global counter, not keyed per-caller: this is a
personal-use tool with one operator (the local browser), so a per-key
abstraction (e.g. keyed by IP) would only ever see one real key — it's the
overall call volume to the paid `/api/chat` endpoint that needs capping,
not any one caller's share of it.
"""
import time

_WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(self, max_per_window: int):
        self._max = max_per_window
        self._hits: list[float] = []

    def check(self) -> bool:
        """True if the request is allowed (and recorded); False if the
        limit is exceeded (not recorded — doesn't count as a hit)."""
        now = time.monotonic()
        self._hits = [t for t in self._hits if now - t < _WINDOW_SECONDS]
        if len(self._hits) >= self._max:
            return False
        self._hits.append(now)
        return True
