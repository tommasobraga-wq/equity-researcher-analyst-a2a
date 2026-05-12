"""Fundamental Analyst agent — BeeAI ReActAgent + FastAPI, porta 8003.

Riceve news/temi dal News & Sentiment e fondamentali dal Data Collector,
identifica fino a 5 candidati equity con tesi d'investimento specifica.
Mappa theme_analyst + stock_screener di CrewAI.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from beeai_framework.adapters.anthropic.backend.chat import AnthropicChatModel
from beeai_framework.agents.react import ReActAgent
from beeai_framework.memory import UnconstrainedMemory
from beeai_framework.emitter.emitter import Emitter
from beeai_framework.tools.tool import StringToolOutput, Tool
from pydantic import BaseModel

from shared.a2a_models import A2ATask, A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.tools.yfinance_tool import get_stock_fundamentals_text

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ------------------------------------------------------------------ #
# Tool                                                                 #
# ------------------------------------------------------------------ #

class FetchFundamentalsInput(BaseModel):
    ticker: str


class FetchFundamentalsTool(Tool[FetchFundamentalsInput, None, StringToolOutput]):
    name = "fetch_fundamentals"
    description = (
        "Fetch real fundamental data for a stock ticker from yfinance. "
        "Input: ticker symbol, e.g. AAPL, UCG.MI, ASML.AS"
    )
    input_schema = FetchFundamentalsInput

    def _create_emitter(self) -> Emitter:
        return Emitter.root().child(namespace=["tool", "fetch_fundamentals"], creator=self)

    async def _run(self, input: FetchFundamentalsInput, options=None, context=None) -> StringToolOutput:  # noqa: E501
        result = await asyncio.to_thread(get_stock_fundamentals_text, input.ticker)
        return StringToolOutput(result)


# ------------------------------------------------------------------ #
# Agent factory                                                        #
# ------------------------------------------------------------------ #

def _make_agent() -> ReActAgent:
    model = AnthropicChatModel(
        model_id="claude-haiku-4-5-20251001",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )
    return ReActAgent(
        llm=model,
        tools=[FetchFundamentalsTool()],
        memory=UnconstrainedMemory(),
    )


# ------------------------------------------------------------------ #
# Core logic                                                           #
# ------------------------------------------------------------------ #

_INSTRUCTIONS = """You are a fundamental equity analyst for US and EU markets (UK/LSE excluded).

Given news items and market themes, your job is to:
1. Identify up to 3 equity candidates that best fit the themes.
2. For each candidate call fetch_fundamentals to get real data.
3. Build a company-specific investment thesis (not just macro commentary).

SECTOR EXCLUSIONS — reject any candidate in:
energy, utilities, real estate, REITs, consumer staples, industrials,
airlines, crypto, DeFi, Web3.

PRIORITY SECTORS: Technology, AI, Software, Semiconductors, Banking,
Financial Services, Investment Banking, Private Equity, Asset Management.

Return ONLY a JSON array (no prose, no markdown fences):
[{
  "ticker": "X",
  "company": "Full Name",
  "market": "US|EU",
  "theme_id": "T1",
  "thesis": "3-4 sentences, company-specific",
  "catalyst": "2 sentences, specific trigger and timeline",
  "news_ids": ["N1", "N2"],
  "fundamentals": {
    "price": "X", "pe_ttm": "X", "eps": "X",
    "52w_range": "X-X", "analyst_target": "X"
  },
  "analyst_consensus": {
    "total_analysts": 0, "strong_buy": 0, "buy": 0,
    "hold": 0, "sell": 0, "strong_sell": 0,
    "recommendation_key": "X", "recommendation_mean": "X",
    "giudizio_sintetico": "X"
  }
}]"""


def _extract_json_array(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start != -1 and end > start:
        return text[start:end]
    return text


async def run_agent(task: A2ATask) -> A2ATaskResult:
    # Extract structured input from message parts
    input_data: dict[str, Any] = {}
    for part in task.message.parts:
        if hasattr(part, "data"):
            input_data.update(part.data)

    news_text = json.dumps(input_data.get("news", []), ensure_ascii=False)
    themes_text = json.dumps(input_data.get("themes", []), ensure_ascii=False)
    fundamentals_hint = json.dumps(input_data.get("fundamentals", []), ensure_ascii=False)

    prompt = (
        f"{_INSTRUCTIONS}\n\n"
        f"NEWS ITEMS:\n{news_text}\n\n"
        f"MARKET THEMES:\n{themes_text}\n\n"
        f"PRE-FETCHED FUNDAMENTALS (use as starting point, verify with tool if needed):\n{fundamentals_hint}\n\n"  # noqa: E501
        "Now identify the best candidates and return the JSON array."
    )

    try:
        agent = _make_agent()
        response = await agent.run(prompt)
        # Extract final_answer from the last iteration that has one
        raw_text = ""
        for iteration in reversed(response.iterations):
            if iteration.state.final_answer:
                raw_text = iteration.state.final_answer
                break
        output = _extract_json_array(raw_text)
        try:
            candidates = json.loads(output)
            return A2ATaskResult.ok(
                task.id,
                f"Identified {len(candidates)} equity candidate(s).",
                data={"candidates": candidates},
            )
        except json.JSONDecodeError:
            return A2ATaskResult.ok(task.id, raw_text)
    except Exception as e:
        return A2ATaskResult.fail(task.id, str(e))


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="FundamentalAnalyst A2A Agent")

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
    return {"status": "ok", "agent": "FundamentalAnalyst", "port": 8003}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="info")
