"""Sanitization for externally-sourced text (RSS feeds, yfinance free-text
fields) before it reaches any prompt.

This is defense-in-depth, not the primary injection defense — the primary
defense is framing external content in prompts as clearly-delimited,
untrusted data (see each agent's system prompt). This module strips the
grossest injection vectors (HTML, control characters, oversized blobs)
at the source, so every consumer benefits without re-implementing it.
"""
import re

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")


def sanitize_external_text(text: str | None, max_len: int = 300) -> str:
    """Strips HTML tags and control characters, collapses whitespace, and
    truncates to `max_len`. Returns "" for None/empty input."""
    if not text:
        return ""
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = _CONTROL_CHARS_RE.sub("", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned[:max_len]
