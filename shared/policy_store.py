"""RAG store for internal compliance policies — Voyage AI embeddings + pgvector.

Used by the Compliance Agent (agents/compliance-agent/agent.py) to ground its
judgments in actual policy text instead of relying on the model's own
(unverifiable) notion of "compliant". `search_policy` results are citable:
each chunk carries its `source` document.
"""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import voyageai

from shared.db import get_vector_pool
from shared.log import get_logger

_logger = get_logger("policy_store")

_EMBED_MODEL = os.getenv("VOYAGE_EMBED_MODEL", "voyage-4")
# Default output dim for voyage-4 — must match shared/db.py::_VECTOR_SCHEMA's vector(1024).
_EMBED_DIMENSION = 1024

# Chunking: split on blank lines (paragraph-level) — internal policy docs are
# short, structured markdown, not long-form prose, so a fixed-size sliding
# window would cut mid-clause more often than it would help.
_MIN_CHUNK_CHARS = 40

# Voyage's free tier (no payment method on file) caps requests at 3/minute
# and 10K tokens/minute — a single ingestion request for one of the
# regulatory PDFs blows past both. These constants throttle
# ingest_documents() to stay under that ceiling; they only matter for
# accounts without a payment method — remove the throttling (call embed()
# directly) once one is added, ingestion will be near-instant.
_INGEST_MAX_TOKENS_PER_REQUEST = 9_000  # 10K TPM cap, with headroom
_INGEST_SECONDS_PER_REQUEST = 21  # ceil(60 / 3 RPM) + margin
_CHARS_PER_TOKEN_ESTIMATE = 4  # rough heuristic, no tokenizer dependency


def _client() -> voyageai.AsyncClient:
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "VOYAGE_API_KEY is required for the Compliance Agent's policy retrieval."
        )
    return voyageai.AsyncClient(api_key=api_key)


def _chunk_text(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n")]
    return [p for p in paragraphs if len(p) >= _MIN_CHUNK_CHARS]


async def embed(texts: list[str], input_type: str) -> list[list[float]]:
    """`input_type` must be "document" when embedding policy chunks for
    storage, or "query" when embedding a search string — Voyage's models are
    tuned differently for each, and mismatching them measurably hurts
    retrieval quality."""
    result = await _client().embed(texts, model=_EMBED_MODEL, input_type=input_type)
    return result.embeddings


def _load_chunks(path: Path) -> list[dict[str, Any]]:
    """Returns [{"content", "source", "metadata"}, ...] for one policy file.
    Markdown files are short, already-atomic internal policies (paragraph
    split is enough); PDFs are long-form regulatory texts routed to the
    structure-aware parsers in shared/policy_pdf.py."""
    if path.suffix == ".pdf":
        from shared.policy_pdf import chunk_pdf
        return chunk_pdf(path)
    text = path.read_text(encoding="utf-8")
    return [
        {"content": c, "source": path.name, "metadata": {}}
        for c in _chunk_text(text)
    ]


def _batch_by_token_budget(texts: list[str], max_tokens: int) -> list[list[int]]:
    """Groups text indices into batches whose estimated combined token count
    stays under `max_tokens`. A single text that alone exceeds the budget
    (e.g. a long CONSOB article) still gets its own one-item batch — Voyage
    will accept or reject it as a single request either way, splitting it
    further would break the chunk's semantic boundary for no guaranteed gain."""
    batches: list[list[int]] = []
    current: list[int] = []
    current_tokens = 0
    for i, text in enumerate(texts):
        tokens = max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)
        if current and current_tokens + tokens > max_tokens:
            batches.append(current)
            current, current_tokens = [], 0
        current.append(i)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


_last_ingest_request_at: float | None = None


async def _throttled_embed(texts: list[str]) -> list[list[float]]:
    """Like embed(texts, input_type="document"), but paced to stay under
    Voyage's free-tier 3-requests/minute cap — see the _INGEST_* constants
    above. Retries once on a rate-limit error with a longer cooldown, in
    case the estimate above still undershot the actual token count."""
    global _last_ingest_request_at
    if _last_ingest_request_at is not None:
        elapsed = time.monotonic() - _last_ingest_request_at
        wait = _INGEST_SECONDS_PER_REQUEST - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    try:
        result = await embed(texts, input_type="document")
    except voyageai.error.RateLimitError:
        _logger.warning(
            "Voyage rate limit hit despite throttling, backing off 65s and retrying once",
            extra={"event_type": "policy_ingest"},
        )
        await asyncio.sleep(65)
        result = await embed(texts, input_type="document")
    finally:
        _last_ingest_request_at = time.monotonic()
    return result


async def ingest_documents(paths: list[Path]) -> int:
    """Chunks and embeds every file in `paths`, replacing any existing chunks
    from the same source (re-running ingestion after editing a policy
    document doesn't leave stale chunks behind). Returns the total chunk count.

    Embedding requests are throttled to Voyage's free-tier rate limit (see
    _INGEST_* constants) — with ~250 chunks across the 4 regulatory PDFs,
    expect this to take several minutes, not seconds. Safe to re-run if
    interrupted midway; already-ingested sources are simply re-deleted and
    redone."""
    pool = await get_vector_pool()
    total = 0
    async with pool.acquire() as conn:
        for path in paths:
            records = _load_chunks(path)
            if not records:
                continue
            source = path.name
            texts = [r["content"] for r in records]
            batches = _batch_by_token_budget(texts, _INGEST_MAX_TOKENS_PER_REQUEST)

            vectors: list[list[float]] = [None] * len(records)  # type: ignore[list-item]
            for batch_num, indices in enumerate(batches, start=1):
                batch_vectors = await _throttled_embed([texts[i] for i in indices])
                for idx, vector in zip(indices, batch_vectors):
                    vectors[idx] = vector
                _logger.info(
                    f"{source}: embedded batch {batch_num}/{len(batches)} "
                    f"({len(indices)} chunk(s))",
                    extra={"event_type": "policy_ingest"},
                )

            await conn.execute("DELETE FROM policy_chunks WHERE source = $1", source)
            for record, vector in zip(records, vectors):
                await conn.execute(
                    "INSERT INTO policy_chunks (content, embedding, source, metadata) "
                    "VALUES ($1, $2, $3, $4)",
                    record["content"], vector, source, json.dumps(record["metadata"]),
                )
            total += len(records)
            _logger.info(
                f"Ingested {len(records)} chunk(s) from {source}",
                extra={"event_type": "policy_ingest"},
            )
    return total


async def search_policy(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Returns up to `top_k` policy chunks most relevant to `query`, nearest
    first, each as {"content": ..., "source": ...}."""
    pool = await get_vector_pool()
    vectors = await embed([query], input_type="query")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT content, source, metadata FROM policy_chunks
            ORDER BY embedding <=> $1 LIMIT $2
            """,
            vectors[0], top_k,
        )
    return [
        {"content": r["content"], "source": r["source"], **json.loads(r["metadata"])}
        for r in rows
    ]
