"""Unit tests for the in-process event bus (shared/events.py)."""

import pytest

from shared import events


def test_emit_without_subscriber_is_noop():
    events.emit("run-x", "stage_start", stage="foo")  # must not raise
    assert "run-x" not in events._queues


@pytest.mark.asyncio
async def test_emit_reaches_subscriber_with_ts_and_payload():
    q = events.subscribe("run-1")
    events.emit("run-1", "stage_start", stage="risk_assessor")
    ev = q.get_nowait()
    assert ev["type"] == "stage_start"
    assert ev["stage"] == "risk_assessor"
    assert "ts" in ev
    events.unsubscribe("run-1", q)
    assert "run-1" not in events._queues


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive():
    q1, q2 = events.subscribe("run-2"), events.subscribe("run-2")
    events.emit("run-2", "gate2_flag", flagged=[{"ticker": "AAPL"}])
    assert q1.get_nowait()["flagged"] == [{"ticker": "AAPL"}]
    assert q2.get_nowait()["flagged"] == [{"ticker": "AAPL"}]
    events.unsubscribe("run-2", q1)
    events.unsubscribe("run-2", q2)


@pytest.mark.asyncio
async def test_end_stream_sends_sentinel():
    q = events.subscribe("run-3")
    events.end_stream("run-3")
    assert q.get_nowait()["type"] == events.STREAM_END
    events.unsubscribe("run-3", q)


def test_emit_with_none_run_id_is_noop():
    events.emit(None, "stage_start")  # REPL path — must not raise
