"""Shared Postgres connection pool + audit schema bootstrap.

One pool per process: the orchestrator and each of the 5 agents run as
separate processes and each lazily creates its own pool on first use.
"""
import json
import os
from typing import Any

import asyncpg

from shared.log import get_logger

_logger = get_logger("db")

_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    correlation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    agent TEXT NOT NULL,
    direction TEXT,
    payload JSONB NOT NULL,
    status TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit_log(correlation_id);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id UUID PRIMARY KEY,
    tickers TEXT[] NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    last_stage TEXT,
    state JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES conversation_sessions(session_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_session ON conversation_turns(session_id, created_at);
"""

# Separate from _SCHEMA: requires the `vector` extension, which may not be
# available on every Postgres instance (unlike pgcrypto, which ships with
# stock Postgres). Bootstrapped only by shared/policy_store.py — callers that
# don't need RAG (the other 5 agents, the orchestrator) never pay for it and
# never fail if it's missing.
_VECTOR_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    embedding vector(1024) NOT NULL,
    source TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool | None:
    """Returns the shared pool, creating and initializing it on first call.

    Returns None if DATABASE_URL isn't configured — callers must treat a
    missing pool as "audit logging unavailable", never as a hard failure.
    """
    global _pool
    if _pool is not None:
        return _pool

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None

    _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(_SCHEMA)
    return _pool


_vector_pool: asyncpg.Pool | None = None


async def get_vector_pool() -> asyncpg.Pool:
    """Returns a dedicated pool (separate from get_pool()'s general-purpose
    one) with the `vector` extension/schema bootstrapped and the pgvector
    codec registered on every connection it hands out.

    A dedicated pool — not the shared one — because `init=` (which registers
    the pgvector type codec) applies to every connection asyncpg creates for
    that pool; forcing it onto the general-purpose pool would break audit/
    pipeline_runs/conversation-memory writes on any Postgres without the
    `vector` extension installed.

    Unlike get_pool(), this raises instead of degrading to a no-op: a missing
    DATABASE_URL or a Postgres without the `vector` extension means the
    Compliance Agent cannot check policies at all — silently skipping a
    compliance check would be actively dangerous, so this must fail loudly
    at startup rather than let the agent run with retrieval quietly disabled.
    """
    global _vector_pool
    if _vector_pool is not None:
        return _vector_pool

    import pgvector.asyncpg

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required for the Compliance Agent (policy_chunks/pgvector) "
            "— compliance checks cannot silently run without policy retrieval."
        )

    async def _init(conn):
        await pgvector.asyncpg.register_vector(conn)

    _vector_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5, init=_init)
    async with _vector_pool.acquire() as conn:
        await conn.execute(_VECTOR_SCHEMA)
    return _vector_pool


async def save_run_state(run_id: str, tickers: list[str], status: str, last_stage: str, state: dict[str, Any]) -> None:
    """Upserts a snapshot of the pipeline's LangGraph state, so a crash
    mid-run can be resumed from the last completed stage instead of
    restarting the whole pipeline. Best-effort: never raises — if
    DATABASE_URL isn't configured, resume simply isn't available, same
    degrade-to-noop as shared/audit.py."""
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pipeline_runs (run_id, tickers, status, last_stage, state, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, now())
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    last_stage = EXCLUDED.last_stage,
                    state = EXCLUDED.state,
                    updated_at = now()
                """,
                run_id, tickers, status, last_stage,
                json.dumps(state, ensure_ascii=False, default=str),
            )
    except Exception as e:
        _logger.warning(f"Pipeline state save failed: {e}", extra={"correlation_id": run_id, "event_type": "pipeline_run"})


async def load_run_state(run_id: str) -> dict[str, Any] | None:
    """Returns the persisted state dict for `run_id`, or None if no run with
    that id was persisted (DATABASE_URL unset, unknown id, or a DB error —
    callers should treat all of these as "cannot resume, start fresh")."""
    try:
        pool = await get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state FROM pipeline_runs WHERE run_id = $1", run_id,
            )
        return json.loads(row["state"]) if row else None
    except Exception as e:
        _logger.warning(f"Pipeline state load failed: {e}", extra={"correlation_id": run_id, "event_type": "pipeline_run"})
        return None


async def get_or_create_session(session_id: str | None = None) -> str | None:
    """Resolves the conversation session to use for this CLI run.

    - `session_id` given: touches `last_active_at` and returns it unchanged
      (creating the row if it somehow doesn't exist).
    - `session_id` is None: resumes the most recently active session ("continue
      where you left off" — the natural default for a single local user), or
      creates a new one if none exist yet.
    - Returns None if DATABASE_URL isn't configured: the caller should treat
      this as "no cross-session memory available, this run is a one-off"."""
    try:
        pool = await get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            if session_id is not None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO conversation_sessions (session_id, last_active_at)
                    VALUES ($1, now())
                    ON CONFLICT (session_id) DO UPDATE SET last_active_at = now()
                    RETURNING session_id
                    """,
                    session_id,
                )
                return str(row["session_id"])

            row = await conn.fetchrow(
                "SELECT session_id FROM conversation_sessions ORDER BY last_active_at DESC LIMIT 1",
            )
            if row is not None:
                await conn.execute(
                    "UPDATE conversation_sessions SET last_active_at = now() WHERE session_id = $1",
                    row["session_id"],
                )
                return str(row["session_id"])

            row = await conn.fetchrow(
                "INSERT INTO conversation_sessions DEFAULT VALUES RETURNING session_id",
            )
            return str(row["session_id"])
    except Exception as e:
        _logger.warning(f"Session resolution failed: {e}", extra={"event_type": "conversation_session"})
        return None


async def append_turn(session_id: str, role: str, content: str, run_id: str | None = None) -> None:
    """Persists one conversation turn. Best-effort: never raises."""
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO conversation_turns (session_id, role, content, run_id) VALUES ($1, $2, $3, $4)",
                session_id, role, content, run_id,
            )
    except Exception as e:
        _logger.warning(
            f"Conversation turn save failed: {e}",
            extra={"correlation_id": session_id, "event_type": "conversation_turn"},
        )


async def load_recent_turns(session_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Returns the last `limit` turns for `session_id`, oldest first (ready to
    drop straight into an LLM prompt as conversation history)."""
    try:
        pool = await get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content FROM conversation_turns
                WHERE session_id = $1 ORDER BY created_at DESC LIMIT $2
                """,
                session_id, limit,
            )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except Exception as e:
        _logger.warning(
            f"Conversation history load failed: {e}",
            extra={"correlation_id": session_id, "event_type": "conversation_turn"},
        )
        return []


async def check_connection() -> str:
    """Readiness probe for /health: "not_configured" if DATABASE_URL is
    unset (audit persistence is an optional feature, not a hard dependency),
    "ok" if a trivial query succeeds, "unreachable" otherwise."""
    if not os.getenv("DATABASE_URL"):
        return "not_configured"
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return "ok"
    except Exception:
        return "unreachable"
