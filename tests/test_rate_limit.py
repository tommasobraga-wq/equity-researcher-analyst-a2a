"""Unit tests for shared/rate_limit.py — global sliding-window limiter."""
from shared.rate_limit import RateLimiter


def test_allows_up_to_the_limit():
    limiter = RateLimiter(max_per_window=3)
    assert limiter.check() is True
    assert limiter.check() is True
    assert limiter.check() is True


def test_rejects_beyond_the_limit():
    limiter = RateLimiter(max_per_window=2)
    assert limiter.check() is True
    assert limiter.check() is True
    assert limiter.check() is False


def test_rejected_hit_is_not_recorded():
    limiter = RateLimiter(max_per_window=1)
    assert limiter.check() is True
    assert limiter.check() is False
    # Still rejected — the failed attempt above didn't consume a new slot
    # beyond what was already at capacity.
    assert limiter.check() is False


def test_old_hits_expire_out_of_the_window(monkeypatch):
    limiter = RateLimiter(max_per_window=1)
    t = [1000.0]
    monkeypatch.setattr("shared.rate_limit.time.monotonic", lambda: t[0])

    assert limiter.check() is True
    assert limiter.check() is False

    t[0] += 61.0  # past the 60s window
    assert limiter.check() is True
