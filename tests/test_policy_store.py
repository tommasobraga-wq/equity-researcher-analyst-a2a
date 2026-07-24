"""Unit tests for the pure (no network/DB) parts of shared/policy_store.py.

embed()/search_policy()/ingest_documents() all require VOYAGE_API_KEY and a
live Postgres with pgvector — same "fail fast, no silent degrade" posture as
the Compliance Agent itself (see shared/db.py::get_vector_pool) — so they
aren't unit-testable without real credentials/infra, consistent with how the
other 5 agents' run_agent() are only exercised via tests/test_smoke.py /
tests/test_integration.py against live processes, not direct import.
"""
from shared.policy_store import _chunk_text


def test_chunk_text_splits_on_blank_lines():
    text = (
        "Paragraph one, long enough to count as a chunk.\n\n"
        "Paragraph two, also long enough to count as its own chunk."
    )
    chunks = _chunk_text(text)
    assert chunks == [
        "Paragraph one, long enough to count as a chunk.",
        "Paragraph two, also long enough to count as its own chunk.",
    ]


def test_chunk_text_drops_short_fragments():
    text = "ok\n\nThis paragraph is long enough to survive the minimum length filter."
    chunks = _chunk_text(text)
    assert len(chunks) == 1
    assert "long enough" in chunks[0]


def test_chunk_text_strips_whitespace():
    text = "  Padded paragraph with enough length to survive.  \n\n"
    chunks = _chunk_text(text)
    assert chunks == ["Padded paragraph with enough length to survive."]


def test_chunk_text_empty_input():
    assert _chunk_text("") == []
