"""News & Sentiment agent — Smolagents CodeAgent + FastAPI, porta 8002.

Legge i feed RSS finanziari e raggruppa le notizie in macro-temi
di mercato rilevanti per equity US/EU.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from smolagents import CodeAgent, LiteLLMModel, tool

from shared.a2a_models import A2ATask, A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.tools.rss_feed import fetch_rss_news

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ------------------------------------------------------------------ #
# Tool                                                                 #
# ------------------------------------------------------------------ #

@tool
def read_financial_rss(max_items_per_feed: int = 5) -> str:
    """Read financial news RSS feeds from Reuters, Yahoo Finance, MarketWatch, Investing.com.

    Args:
        max_items_per_feed: Maximum number of articles to fetch per source (default 5).

    Returns:
        Formatted string with headlines and summaries from all sources.
    """
    return fetch_rss_news(max_items_per_feed=max_items_per_feed)


# ------------------------------------------------------------------ #
# Agent                                                                #
# ------------------------------------------------------------------ #

_MODEL = LiteLLMModel(
    model_id="anthropic/claude-haiku-4-5-20251001",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

_SYSTEM_PROMPT = """You are a financial news analyst specializing in US and EU equity markets.

Your job:
1. Call read_financial_rss to fetch today's financial news.
2. Select the 10-12 most relevant articles for equity investors, focusing on:
   - Technology, AI, Software, Semiconductors
   - Banking, Financial Services, Investment Banking, Asset Management
3. EXCLUDE: energy, utilities, real estate, REITs, consumer staples, industrials,
   airlines, crypto, DeFi, Web3, digital assets.
4. Assign each selected article a unique ID (N1, N2, ...).
5. Cluster the articles into 3-4 macro market themes.
6. Return ONLY a JSON object with this exact structure (no prose, no markdown fences):
{
  "news": [
    {"id": "N1", "source": "Reuters Markets", "headline": "...", "summary": "max 2 sentences"}
  ],
  "themes": [
    {"id": "T1", "title": "...", "why_now": "1 sentence", "news_ids": ["N1", "N2"]}
  ]
}"""

news_sentiment_agent = CodeAgent(
    model=_MODEL,
    tools=[read_financial_rss],
    max_steps=5,
    additional_authorized_imports=["json"],
)


# ------------------------------------------------------------------ #
# Core logic                                                           #
# ------------------------------------------------------------------ #

def _extract_json(text: str) -> str:
    """Extract JSON object from text, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        return text[start:end]
    return text


async def run_agent(task: A2ATask) -> A2ATaskResult:
    focus = task.message.text() or "Technology, AI, Banking, Financial Services"
    prompt = (
        f"{_SYSTEM_PROMPT}\n\n"
        f"Focus on sectors/topics: {focus}\n"
        "Now fetch the news and return the JSON."
    )
    try:
        output = await asyncio.to_thread(news_sentiment_agent.run, prompt)
        # smolagents may return a dict directly or a JSON string
        if isinstance(output, dict):
            data = output
        else:
            raw = _extract_json(str(output))
            data = json.loads(raw)
        n = len(data.get("news", []))
        t = len(data.get("themes", []))
        return A2ATaskResult.ok(
            task.id,
            f"Fetched {n} news items, identified {t} themes.",
            data=data,
        )
    except Exception as e:
        return A2ATaskResult.fail(task.id, str(e))


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="NewsSentiment A2A Agent")

_WELL_KNOWN = Path(__file__).parent / ".well-known" / "agent.json"


@app.get("/.well-known/agent.json")
async def agent_card():
    return FileResponse(_WELL_KNOWN, media_type="application/json")


@app.post("/tasks")
async def receive_task(rpc: JsonRpcRequest) -> JSONResponse:
    if rpc.method != "tasks/send":
        resp = JsonRpcResponse.fail(-32601, f"Method not found: {rpc.method}", rpc.id)
        return JSONResponse(resp.model_dump(), status_code=404)
    try:
        task = A2ATask(**rpc.params)
    except Exception as e:
        resp = JsonRpcResponse.fail(-32602, f"Invalid params: {e}", rpc.id)
        return JSONResponse(resp.model_dump(), status_code=422)

    result = await run_agent(task)
    return JSONResponse(JsonRpcResponse.ok(result.model_dump(), rpc.id).model_dump())


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "NewsSentiment", "port": 8002}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")
