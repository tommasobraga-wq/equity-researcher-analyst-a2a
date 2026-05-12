"""Risk Assessor agent — BeeAI ReActAgent + Conditional Constraints + FastAPI, porta 8004.

Riceve i candidati equity dal Fundamental Analyst e produce:
- Scenari base/bull/bear per ogni candidato
- Scoring su 5 dimensioni (1-10 ciascuna, max 50 totale)
- Guardrail: rifiuta l'output se i dati di volatilità (52w range, P/E) sono assenti.
Mappa risk_assessor di CrewAI.
"""
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from beeai_framework.adapters.anthropic.backend.chat import AnthropicChatModel
from beeai_framework.agents.react import ReActAgent
from beeai_framework.agents.types import AgentExecutionConfig
from beeai_framework.emitter.emitter import Emitter
from beeai_framework.memory import UnconstrainedMemory
from beeai_framework.tools.tool import StringToolOutput, Tool

from shared.a2a_models import A2ATask, A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.tools.yfinance_tool import get_stock_fundamentals

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ------------------------------------------------------------------ #
# Conditional constraint check                                         #
# ------------------------------------------------------------------ #

def _has_volatility_data(candidate: dict) -> bool:
    """Guardrail: candidate must have 52w range and P/E to score risk."""
    fund = candidate.get("fundamentals", {})
    has_range = fund.get("52w_range") not in (None, "N/A", "None-None", "")
    has_pe = fund.get("pe_ttm") not in (None, "N/A", "")
    return has_range and has_pe


# ------------------------------------------------------------------ #
# Tool                                                                 #
# ------------------------------------------------------------------ #

class VolatilityCheckInput(BaseModel):
    ticker: str


class VolatilityCheckTool(Tool[VolatilityCheckInput, None, StringToolOutput]):
    name = "check_volatility_data"
    description = (
        "Fetch 52-week range and P/E ratio for a ticker to verify volatility data "
        "is available before scoring risk. Returns 'OK' or 'MISSING: <fields>'."
    )
    input_schema = VolatilityCheckInput

    def _create_emitter(self) -> Emitter:
        return Emitter.root().child(namespace=["tool", "volatility_check"], creator=self)

    async def _run(self, input: VolatilityCheckInput, options=None, context=None) -> StringToolOutput:
        try:
            data = await asyncio.to_thread(get_stock_fundamentals, input.ticker.strip().upper())
            missing = []
            if data.get("week52_low") is None or data.get("week52_high") is None:
                missing.append("52w_range")
            if data.get("pe_ttm") is None:
                missing.append("pe_ttm")
            if missing:
                return StringToolOutput(f"MISSING: {', '.join(missing)}")
            return StringToolOutput(
                f"OK — 52w: {data['week52_low']}-{data['week52_high']}, P/E TTM: {data['pe_ttm']}"
            )
        except Exception as e:
            return StringToolOutput(f"ERROR: {e}")


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
        tools=[VolatilityCheckTool()],
        memory=UnconstrainedMemory(),
        execution=AgentExecutionConfig(max_iterations=25),
    )


# ------------------------------------------------------------------ #
# Core logic                                                           #
# ------------------------------------------------------------------ #

_INSTRUCTIONS = """You are a CFA-aligned risk analyst. Today is {today}.

For each equity candidate:
1. Call check_volatility_data to verify that 52w range and P/E are available.
   - If MISSING for a candidate: mark that candidate with "quality": "dati_insufficienti"
     and skip the scoring for it (set all scores to 0).
   - If OK: proceed to score.
2. Produce scenario analysis and scoring.

SCORING RULES:
- Each dimension: integer 1-10.
- totale = exact arithmetic sum of the 5 dimensions (max 50).
- Be specific: reference actual company financials, product cycles, named events.
- All forward-looking dates must be AFTER {today}.

Return ONLY a JSON array (no prose, no markdown fences):
[{{
  "ticker": "X",
  "scenarios": {{
    "base": "1 sentence",
    "bull": "1 sentence",
    "bear": "1 sentence"
  }},
  "risks": {{
    "macro": "1 sentence",
    "sector": "1 sentence",
    "company": "1 sentence",
    "regulatory": "1 sentence",
    "valuation": "1 sentence"
  }},
  "falsification": "1 sentence — what would prove the thesis wrong",
  "next_checks": ["item1", "item2"],
  "quality": "alta|media|bassa|dati_insufficienti",
  "scoring": {{
    "forza_catalizzatore": 0,
    "fit_orizzonte": 0,
    "asimmetria_narrativa": 0,
    "qualita_evidenze": 0,
    "rischio_crowding": 0,
    "totale": 0
  }}
}}]"""


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
    input_data: dict[str, Any] = {}
    for part in task.message.parts:
        if hasattr(part, "data"):
            input_data.update(part.data)

    candidates = input_data.get("candidates", [])
    if not candidates:
        return A2ATaskResult.fail(task.id, "No candidates received from FundamentalAnalyst.")

    today = date.today().isoformat()
    candidates_json = json.dumps(candidates, ensure_ascii=False)

    prompt = (
        _INSTRUCTIONS.format(today=today) + "\n\n"
        f"EQUITY CANDIDATES:\n{candidates_json}\n\n"
        "Now perform the risk assessment for each candidate."
    )

    try:
        agent = _make_agent()
        response = await agent.run(prompt)
        raw_text = ""
        for iteration in reversed(response.iterations):
            if iteration.state.final_answer:
                raw_text = iteration.state.final_answer
                break
        output = _extract_json_array(raw_text)
        try:
            risk_data = json.loads(output)
            return A2ATaskResult.ok(
                task.id,
                f"Risk assessment complete for {len(risk_data)} candidate(s).",
                data={"risk_assessment": risk_data, "candidates": candidates},
            )
        except json.JSONDecodeError:
            return A2ATaskResult.ok(task.id, raw_text)
    except Exception as e:
        return A2ATaskResult.fail(task.id, str(e))


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="RiskAssessor A2A Agent")

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
    return {"status": "ok", "agent": "RiskAssessor", "port": 8004}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004, log_level="info")
