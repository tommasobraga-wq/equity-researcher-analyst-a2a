"""Gateway API tests — pipeline and coordinator monkeypatched, no agents needed."""
import json

import httpx
import pytest

import gateway.app as gw
from shared import events
from shared.rate_limit import RateLimiter


class _FakeIntent:
    mode = "specific"
    tickers = ["AAPL"]
    focus = ""
    priority_sectors = []
    excluded_sectors = []


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=gw.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def patched(monkeypatch, tmp_path):
    async def fake_interpret(client, text, history):
        return _FakeIntent()

    report_file = tmp_path / "report_test.html"
    report_file.write_text("<h1>report</h1>", encoding="utf-8")

    async def fake_pipeline(**kwargs):
        run_id = kwargs["run_id"]
        events.emit(run_id, "stage_start", stage="data_news_parallel")
        events.emit(run_id, "stage_end", stage="data_news_parallel", duration_s=1.0, clean=True)
        events.emit(run_id, "run_complete", report_path=str(report_file),
                    executive_summary="Sintesi.", qa_verdict="QA: APPROVATO",
                    execution_seconds=1, analyzed_tickers=["AAPL"])
        events.end_stream(run_id)
        return {"status": "completed", "run_id": run_id, "executive_summary": "Sintesi.",
                "qa_verdict": "QA: APPROVATO", "report": {}, "report_path": str(report_file)}

    monkeypatch.setattr(gw, "interpret_prompt", fake_interpret)
    monkeypatch.setattr(gw, "run_pipeline", fake_pipeline)
    return report_file


async def test_chat_returns_run_and_intent(client, patched):
    async with client:
        resp = await client.post("/api/chat", json={"text": "confrontami AAPL"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"]
        assert data["intent"]["mode"] == "specific"
        assert data["intent"]["tickers"] == ["AAPL"]
        # default sectors from config.yaml are filled in when the intent has none
        assert data["intent"]["priority_sectors"]


async def test_chat_rejects_empty_text(client):
    async with client:
        resp = await client.post("/api/chat", json={"text": "   "})
        assert resp.status_code == 422


async def test_stream_unknown_run_404(client):
    async with client:
        resp = await client.get("/api/stream/nope")
        assert resp.status_code == 404


async def test_stream_replays_status_when_run_already_finished(client, patched):
    async with client:
        resp = await client.post("/api/chat", json={"text": "confrontami AAPL"})
        run_id = resp.json()["run_id"]
        await gw._runs[run_id]["task"]  # let the fake pipeline finish first
        stream = await client.get("/api/stream/" + run_id)
        assert stream.status_code == 200
        payloads = [
            json.loads(line[6:])
            for line in stream.text.splitlines() if line.startswith("data: ")
        ]
        assert payloads and payloads[0]["type"] == "run_completed"


async def test_report_served_after_completion(client, patched):
    async with client:
        resp = await client.post("/api/chat", json={"text": "confrontami AAPL"})
        run_id = resp.json()["run_id"]
        await gw._runs[run_id]["task"]
        report = await client.get("/api/report/" + run_id)
        assert report.status_code == 200
        assert "report" in report.text


async def test_report_404_before_completion(client):
    gw._runs["pending"] = {"status": "running", "report_path": None}
    async with client:
        resp = await client.get("/api/report/pending")
        assert resp.status_code == 404
    gw._runs.pop("pending")


async def test_chat_rate_limited_returns_429(client, patched, monkeypatch):
    monkeypatch.setattr(gw, "_rate_limiter", RateLimiter(max_per_window=1))
    async with client:
        first = await client.post("/api/chat", json={"text": "confrontami AAPL"})
        assert first.status_code == 200
        second = await client.post("/api/chat", json={"text": "confrontami AAPL"})
        assert second.status_code == 429
        assert "Retry-After" in second.headers


async def test_chat_rejects_when_too_many_concurrent_runs(client, monkeypatch):
    monkeypatch.setattr(gw, "_MAX_CONCURRENT_RUNS", 0)
    async with client:
        resp = await client.post("/api/chat", json={"text": "confrontami AAPL"})
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
