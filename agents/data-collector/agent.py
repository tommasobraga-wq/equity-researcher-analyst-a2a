"""Data Collector agent — OpenAI Agents SDK + FastAPI, porta 8001.

Receives a list of equity tickers via A2A and returns fundamentals
fetched from yfinance for each ticker.
"""
import json
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

# Make shared/ importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agents.extensions.models.litellm_model import LitellmModel

from agents import Agent, Runner, function_tool
from shared.a2a_models import A2ATask, A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.tools.yfinance_tool import get_stock_fundamentals

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ------------------------------------------------------------------ #
# Tool                                                                 #
# ------------------------------------------------------------------ #

@function_tool
def fetch_fundamentals(ticker: str) -> str:
    """Fetch real fundamental data for an equity ticker from yfinance.

    Args:
        ticker: Stock ticker symbol, e.g. AAPL, UCG.MI, ASML.AS
    """
    try:
        data = get_stock_fundamentals(ticker)
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"ticker": ticker, "error": str(e)})


# ------------------------------------------------------------------ #
# Agent                                                                #
# ------------------------------------------------------------------ #

_MODEL = LitellmModel(
    model="anthropic/claude-haiku-4-5-20251001",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

data_collector_agent = Agent(
    name="DataCollector",
    model=_MODEL,
    instructions=(
        "You are a financial data agent. Given a list of equity tickers, "
        "call fetch_fundamentals for EACH ticker individually and collect the results. "
        "Return a JSON array where each element is the fundamentals dict for one ticker. "
        "Do not add commentary — only the JSON array."
    ),
    tools=[fetch_fundamentals],
)


# ------------------------------------------------------------------ #
# Core logic                                                           #
# ------------------------------------------------------------------ #

def _strip_markdown_json(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # drop first line (```json or ```) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    return text


async def run_agent(task: A2ATask) -> A2ATaskResult:
    text_input = task.message.text()
    try:
        result = await Runner.run(data_collector_agent, input=text_input)
        output = _strip_markdown_json(result.final_output)
        try:
            data = json.loads(output)
            return A2ATaskResult.ok(
                task.id, "Fundamentals fetched successfully.", data={"fundamentals": data}
            )
        except json.JSONDecodeError:
            return A2ATaskResult.ok(task.id, output)
    except Exception as e:
        return A2ATaskResult.fail(task.id, str(e))


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="DataCollector A2A Agent")

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
    resp = JsonRpcResponse.ok(result.model_dump(), rpc.id)
    return JSONResponse(resp.model_dump())


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "DataCollector", "port": 8001}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
