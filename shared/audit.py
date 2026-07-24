"""Compliance audit trail — every A2A call and external data fetch is logged.

Logging failures (DB unreachable, DATABASE_URL unset) must never break the
pipeline: `log_event` swallows its own errors and emits a structured warning
via shared/log.py instead.
"""
import json
from typing import Any

from shared.db import get_pool
from shared.log import get_logger

_logger = get_logger("audit")


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
