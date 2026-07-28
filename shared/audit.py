"""Compliance audit trail — every A2A call and external data fetch is logged.

Logging failures (DB unreachable, DATABASE_URL unset) must never break the
pipeline: `log_event` swallows its own errors and emits a structured warning
via shared/log.py instead.
"""
import asyncio
import contextvars
import json
from contextlib import contextmanager
from typing import Any, Iterator

from shared.db import get_pool
from shared.log import get_logger

_logger = get_logger("audit")

# Set once per A2A request (shared/a2a_server.py::handle_task, which already
# knows both values before calling run_agent) so shared/react_agent.py and
# shared/qa.py can pick up correlation_id/agent without every one of their
# ~13 call sites across 7 agents having to thread them through manually.
_correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None,
)
_agent_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("agent", default=None)


@contextmanager
def audit_context(correlation_id: str, agent: str) -> Iterator[None]:
    """Scopes correlation_id/agent for the duration of one A2A task's
    processing. Safe across `await` (contextvars follow the coroutine, no
    concurrent task is spawned here — each A2A request is handled by its own
    asyncio Task with its own copied context)."""
    token_c = _correlation_id_var.set(correlation_id)
    token_a = _agent_var.set(agent)
    try:
        yield
    finally:
        _correlation_id_var.reset(token_c)
        _agent_var.reset(token_a)


def current_correlation_id() -> str:
    return _correlation_id_var.get() or "unknown"


def current_agent() -> str:
    return _agent_var.get() or "unknown"


async def log_event(
    correlation_id: str,
    event_type: str,
    agent: str,
    payload: dict[str, Any],
    status: str | None = None,
    duration_ms: int | None = None,
    direction: str | None = None,
) -> None:
    """Insert one audit row. Best-effort: never raises."""
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log
                    (correlation_id, event_type, agent, direction, payload, status, duration_ms)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                """,
                correlation_id, event_type, agent, direction,
                json.dumps(payload, ensure_ascii=False, default=str),
                status, duration_ms,
            )
    except Exception as e:
        _logger.warning(
            f"Audit log write failed: {e}",
            extra={"correlation_id": correlation_id, "agent": agent, "event_type": event_type},
        )


def log_event_fire_and_forget(
    correlation_id: str,
    event_type: str,
    agent: str,
    payload: dict[str, Any],
    status: str | None = None,
    duration_ms: int | None = None,
    direction: str | None = None,
) -> None:
    """Sync-context bridge to log_event, for callers that aren't themselves
    async (shared/qa.py::run_llm_qa, ReportWriter's _call_claude) but are
    always invoked from inside a running event loop (the agent's async
    run_agent()). Schedules the write as a background task — never blocks,
    never raises, same best-effort posture as log_event itself. Silently
    skipped if there's no running loop (e.g. a script/test calling this
    outside asyncio)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        log_event(correlation_id, event_type, agent, payload, status, duration_ms, direction)
    )
