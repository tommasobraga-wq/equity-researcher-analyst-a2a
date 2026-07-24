"""In-process event bus for live pipeline progress (gateway web UI).

The gateway runs the orchestrator in-process, so streaming "what is the
pipeline doing right now" to the browser needs nothing more than a per-run
asyncio.Queue: orchestrator code calls `emit(run_id, type, ...)` at the same
points where it already prints progress, and the gateway's SSE endpoint
drains `subscribe(run_id)`.

`emit` is deliberately a no-op when nobody subscribed to that run — the REPL
and `--tickers` paths keep working identically with zero overhead, and no
orchestrator code ever has to know whether a UI is attached.
"""
import asyncio
import time
from typing import Any

# Sentinel event type marking the end of a run's stream.
STREAM_END = "stream_end"

_queues: dict[str, list[asyncio.Queue]] = {}


def subscribe(run_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _queues.setdefault(run_id, []).append(queue)
    return queue


def unsubscribe(run_id: str, queue: asyncio.Queue) -> None:
    subscribers = _queues.get(run_id)
    if not subscribers:
        return
    if queue in subscribers:
        subscribers.remove(queue)
    if not subscribers:
        _queues.pop(run_id, None)


def emit(run_id: str | None, event_type: str, **payload: Any) -> None:
    """Non-blocking publish. Safe to call from anywhere in the orchestrator;
    does nothing unless someone subscribed to this run_id."""
    if not run_id:
        return
    subscribers = _queues.get(run_id)
    if not subscribers:
        return
    event = {"ts": time.time(), "type": event_type, **payload}
    for queue in subscribers:
        queue.put_nowait(event)


def end_stream(run_id: str | None) -> None:
    """Emits the sentinel that tells every subscriber the run is over."""
    emit(run_id, STREAM_END)
