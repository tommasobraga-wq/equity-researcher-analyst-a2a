"""Shared FastAPI `/tasks` handler for all 5 A2A agents.

Every agent's route used to duplicate the same ~12 lines (validate JsonRpcRequest
-> parse A2ATask -> call run_agent -> wrap result). This module centralizes that,
plus the two cross-cutting concerns that hook into exactly the same place:
inbound HMAC verification / outbound HMAC signing, and audit logging.
"""
import json
import os
import time
from typing import Any, Awaitable, Callable

from fastapi import Request, Response

from shared.a2a_models import A2ATask, A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.audit import log_event
from shared.auth import sign, verify
from shared.db import check_connection
from shared.log import get_logger

RunAgentFn = Callable[[A2ATask], Awaitable[A2ATaskResult]]

_logger = get_logger("a2a_server")


async def health_status(agent_name: str, port: int) -> dict[str, Any]:
    """Readiness check shared by every agent's /health route: liveness
    (process responds) plus the DB dependency's actual state, rather than
    the static {"status": "ok"} that can't distinguish a healthy agent from
    one whose audit DB has gone unreachable."""
    db = await check_connection()
    status = "degraded" if db == "unreachable" else "ok"
    return {"status": status, "agent": agent_name, "port": port, "db": db}


def _secret() -> str | None:
    return os.getenv("A2A_SHARED_SECRET") or None


def _signed_response(payload: dict[str, Any], secret: str | None, status_code: int = 200) -> Response:
    """Serializes `payload` exactly once and signs those exact bytes — the
    orchestrator verifies the response signature unconditionally (success or
    error), so every reply on this route (including 401/404/422) must be
    signed consistently, not just the 200 happy path."""
    body = json.dumps(payload).encode()
    headers: dict[str, str] = {}
    if secret is not None:
        timestamp = str(time.time())
        headers["X-A2A-Timestamp"] = timestamp
        headers["X-A2A-Signature"] = sign(secret, body, timestamp)
    return Response(content=body, media_type="application/json", headers=headers, status_code=status_code)


async def handle_task(request: Request, run_agent: RunAgentFn, agent_name: str) -> Response:
    body = await request.body()
    secret = _secret()

    # Inbound signature verification. If no shared secret is configured, auth
    # is skipped entirely (dev-friendly default, matches today's zero-auth
    # behavior) — set A2A_SHARED_SECRET in .env to enforce it.
    if secret is not None:
        timestamp = request.headers.get("X-A2A-Timestamp", "")
        signature = request.headers.get("X-A2A-Signature", "")
        if not verify(secret, body, timestamp, signature):
            await log_event(
                correlation_id="unknown", event_type="a2a_request", agent=agent_name,
                direction="inbound", payload={"error": "signature verification failed"},
                status="unauthorized",
            )
            _logger.warning(
                "Rejected request: invalid or missing A2A signature",
                extra={"correlation_id": "unknown", "agent": agent_name, "event_type": "a2a_request"},
            )
            resp = JsonRpcResponse.fail(-32001, "Unauthorized: invalid or missing A2A signature")
            return _signed_response(resp.model_dump(), secret, status_code=401)

    try:
        rpc = JsonRpcRequest(**json.loads(body))
    except Exception as e:
        resp = JsonRpcResponse.fail(-32700, f"Parse error: {e}")
        return _signed_response(resp.model_dump(), secret, status_code=400)

    if rpc.method != "tasks/send":
        resp = JsonRpcResponse.fail(-32601, f"Method not found: {rpc.method}", rpc.id)
        return _signed_response(resp.model_dump(), secret, status_code=404)

    try:
        task = A2ATask(**rpc.params)
    except Exception as e:
        resp = JsonRpcResponse.fail(-32602, f"Invalid params: {e}", rpc.id)
        return _signed_response(resp.model_dump(), secret, status_code=422)

    await log_event(
        correlation_id=task.id, event_type="a2a_request", agent=agent_name,
        direction="inbound", payload=task.model_dump(),
    )
    _logger.info(
        "Task received",
        extra={"correlation_id": task.id, "agent": agent_name, "event_type": "a2a_request"},
    )

    t0 = time.perf_counter()
    result = await run_agent(task)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    await log_event(
        correlation_id=task.id, event_type="a2a_response", agent=agent_name,
        direction="outbound", payload=result.model_dump(),
        status=result.status, duration_ms=duration_ms,
    )
    _logger.info(
        f"Task completed with status={result.status}",
        extra={
            "correlation_id": task.id, "agent": agent_name,
            "event_type": "a2a_response", "duration_ms": duration_ms,
        },
    )

    resp = JsonRpcResponse.ok(result.model_dump(), rpc.id)
    return _signed_response(resp.model_dump(), secret, status_code=200)
