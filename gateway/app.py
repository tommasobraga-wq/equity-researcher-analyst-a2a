"""Gateway web — chat con l'orchestratore + trace live della pipeline, porta 8000.

Esegue l'orchestratore in-process (run_pipeline/interpret_prompt sono libreria):
il browser parla con questo servizio, mai direttamente con gli agenti.

  POST /api/chat            → interpreta il prompt, avvia la pipeline in background,
                              risponde subito con {run_id, session_id, intent}
  GET  /api/stream/{run_id} → SSE degli eventi pipeline (shared/events.py)
  GET  /api/report/{run_id} → HTML del report per l'iframe inline
  GET  /api/health          → fan-out sugli /health dei 7 agenti
  GET  /                    → frontend single-page (gateway/static/)

Avvio: uv run uvicorn gateway.app:app --port 8000
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.coordinator import interpret_prompt
from orchestrator.main import AGENTS, _coordinator_client, run_pipeline
from shared.auth import enforce_secret_policy
from shared.db import append_turn, get_or_create_session, load_recent_turns
from shared.events import STREAM_END, end_stream, subscribe, unsubscribe
from shared.log import get_logger

load_dotenv(Path(__file__).parent.parent / ".env")
enforce_secret_policy()

_logger = get_logger("gateway")

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
_config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) if _CONFIG_PATH.exists() else {}
_DEFAULT_PRIORITY = (_config or {}).get("priority_sectors", ["Technology", "Banking"])
_DEFAULT_EXCLUDED = (_config or {}).get("excluded_sectors", ["energy"])

app = FastAPI(title="Equity Researcher A2A — Gateway")

# run_id → info sulla run (task, report_path quando disponibile)
_runs: dict[str, dict] = {}


class ChatRequest(BaseModel):
    text: str
    session_id: str | None = None


@app.post("/api/chat")
async def chat(req: ChatRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Richiesta vuota.")

    session_id = await get_or_create_session(req.session_id)
    history = await load_recent_turns(session_id) if session_id else []
    if session_id:
        await append_turn(session_id, "user", text)

    try:
        intent = await interpret_prompt(_coordinator_client, text, history)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Coordinator non disponibile: {e}")

    run_id = str(uuid.uuid4())
    _runs[run_id] = {"status": "running", "report_path": None, "session_id": session_id}

    async def _run() -> None:
        try:
            result = await run_pipeline(
                mode=intent.mode,
                tickers=intent.tickers,
                priority_sectors=intent.priority_sectors or _DEFAULT_PRIORITY,
                excluded_sectors=intent.excluded_sectors or _DEFAULT_EXCLUDED,
                focus=intent.focus,
                run_id=run_id,
                open_browser=False,
            )
            _runs[run_id].update(status="completed", report_path=result.get("report_path"))
            if session_id:
                await append_turn(session_id, "assistant",
                                  result.get("executive_summary", ""), run_id=run_id)
        except Exception as e:
            _logger.error(f"Pipeline failed: {e}", extra={"event_type": "pipeline_failed"})
            _runs[run_id].update(status="failed", error=str(e))
            end_stream(run_id)  # idempotente: run_pipeline la chiude già nei suoi path
            if session_id:
                await append_turn(session_id, "assistant", f"[errore] {e}", run_id=run_id)

    _runs[run_id]["task"] = asyncio.create_task(_run())

    return {
        "run_id": run_id,
        "session_id": session_id,
        "intent": {
            "mode": intent.mode,
            "tickers": intent.tickers,
            "focus": intent.focus,
            "priority_sectors": intent.priority_sectors or _DEFAULT_PRIORITY,
            "excluded_sectors": intent.excluded_sectors or _DEFAULT_EXCLUDED,
        },
    }


@app.get("/api/stream/{run_id}")
async def stream(run_id: str):
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Run sconosciuta.")

    async def _events():
        queue = subscribe(run_id)
        try:
            # La run potrebbe essere già finita prima che il browser si iscriva.
            info = _runs.get(run_id, {})
            if info.get("status") != "running":
                yield f"data: {json.dumps({'type': 'run_' + info.get('status', 'failed'), 'error': info.get('error', '')})}\n\n"
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event["type"] == STREAM_END:
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            unsubscribe(run_id, queue)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/report/{run_id}")
async def report(run_id: str):
    info = _runs.get(run_id)
    if not info or not info.get("report_path"):
        raise HTTPException(status_code=404, detail="Report non (ancora) disponibile.")
    path = Path(info["report_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File del report non trovato.")
    return FileResponse(path, media_type="text/html")


@app.get("/api/health")
async def health():
    async def check(name: str, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{url}/health")
                return {"agent": name, "url": url, "ok": resp.status_code == 200}
        except Exception:
            return {"agent": name, "url": url, "ok": False}

    results = await asyncio.gather(*(check(n, u) for n, u in AGENTS.items()))
    return {"agents": results, "all_ok": all(r["ok"] for r in results)}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
