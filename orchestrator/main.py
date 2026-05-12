"""Orchestrator — LangGraph pipeline v2.

Grafo sequenziale con 5 nodi:
  data_collector      (8001) — fondamentali ticker
  news_sentiment      (8002) — news + temi di mercato
  fundamental_analyst (8003) — candidati equity con tesi
  risk_assessor       (8004) — scoring e scenari
  report_writer       (8005) — report finale italiano
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, TypedDict

import httpx
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.a2a_models import A2ATaskResult, JsonRpcRequest, JsonRpcResponse

load_dotenv(Path(__file__).parent.parent / ".env")

AGENTS = {
    "data_collector":      "http://localhost:8001",
    "news_sentiment":      "http://localhost:8002",
    "fundamental_analyst": "http://localhost:8003",
    "risk_assessor":       "http://localhost:8004",
    "report_writer":       "http://localhost:8005",
}


# ------------------------------------------------------------------ #
# Pipeline state                                                       #
# ------------------------------------------------------------------ #

class PipelineState(TypedDict):
    tickers: list[str]
    fundamentals: list
    news: list
    themes: list
    candidates: list
    risk_assessment: list
    report: dict
    executive_summary: str
    qa_verdict: str


# ------------------------------------------------------------------ #
# A2A client                                                           #
# ------------------------------------------------------------------ #

async def send_task(
    agent_url: str,
    message: str,
    data: dict[str, Any] | None = None,
    timeout: float = 300.0,
) -> A2ATaskResult:
    task_id = str(uuid.uuid4())
    parts: list[dict] = [{"type": "text", "text": message}]
    if data:
        parts.append({"type": "data", "data": data})

    rpc = JsonRpcRequest(
        method="tasks/send",
        params={
            "id": task_id,
            "message": {"role": "user", "parts": parts},
            "metadata": {},
        },
        id=1,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{agent_url}/tasks", json=rpc.model_dump())
        resp.raise_for_status()
        rpc_resp = JsonRpcResponse(**resp.json())

    if rpc_resp.error:
        raise RuntimeError(f"Agent error: {rpc_resp.error}")

    return A2ATaskResult(**rpc_resp.result)


async def send_task_with_retry(
    agent_url: str,
    message: str,
    data: dict[str, Any] | None = None,
    timeout: float = 300.0,
    max_retries: int = 3,
    retry_delay: float = 65.0,
) -> A2ATaskResult:
    for attempt in range(max_retries):
        result = await send_task(agent_url, message, data, timeout)
        if result.status != "failed" or "rate_limit" not in result.message.text().lower():
            return result
        if attempt < max_retries - 1:
            print(f"      ⚠ Rate limit — waiting {retry_delay:.0f}s (retry {attempt + 1}/{max_retries - 1})...")
            await asyncio.sleep(retry_delay)
    return result


def _extract_data(result: A2ATaskResult, key: str) -> Any:
    for part in result.message.parts:
        if hasattr(part, "data") and key in part.data:
            return part.data[key]
    return None


# ------------------------------------------------------------------ #
# Graph nodes                                                          #
# ------------------------------------------------------------------ #

async def node_data_collector(state: PipelineState) -> dict:
    ticker_list = ", ".join(state["tickers"])
    print(f"\n[1/5] DataCollector ← {ticker_list}")
    result = await send_task_with_retry(
        AGENTS["data_collector"],
        f"Fetch fundamental data for: {ticker_list}. Return a JSON array.",
    )
    if result.status == "failed":
        raise RuntimeError(f"DataCollector failed: {result.message.text()}")
    fundamentals = _extract_data(result, "fundamentals")
    if fundamentals is None:
        fundamentals = json.loads(result.message.text())
    print(f"      → {len(fundamentals)} ticker(s) fetched")
    return {"fundamentals": fundamentals}


async def node_news_sentiment(state: PipelineState) -> dict:
    print("\n[2/5] NewsSentiment ← fetching RSS feeds")
    result = await send_task_with_retry(
        AGENTS["news_sentiment"],
        "Technology, AI, Software, Semiconductors, Banking, Financial Services",
        timeout=180.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"NewsSentiment failed: {result.message.text()}")
    news = _extract_data(result, "news") or []
    themes = _extract_data(result, "themes") or []
    if not news:
        for part in result.message.parts:
            if hasattr(part, "data"):
                news = part.data.get("news", [])
                themes = part.data.get("themes", [])
                break
    print(f"      → {len(news)} news, {len(themes)} themes")
    return {"news": news, "themes": themes}


async def node_fundamental_analyst(state: PipelineState) -> dict:
    print(f"\n[3/5] FundamentalAnalyst ← {len(state['news'])} news, {len(state['themes'])} themes")
    result = await send_task_with_retry(
        AGENTS["fundamental_analyst"],
        "Analyse the provided news, themes and fundamentals. Return equity candidates.",
        data={
            "news": state["news"],
            "themes": state["themes"],
            "fundamentals": state["fundamentals"],
        },
        timeout=300.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"FundamentalAnalyst failed: {result.message.text()}")
    candidates = _extract_data(result, "candidates")
    if candidates is None:
        candidates = json.loads(result.message.text())
    print(f"      → {len(candidates)} candidate(s) identified")
    return {"candidates": candidates}


async def node_risk_assessor(state: PipelineState) -> dict:
    # Wait for Haiku rate limit window to reset after FundamentalAnalyst's tool calls
    await asyncio.sleep(65)
    print(f"\n[4/5] RiskAssessor ← {len(state['candidates'])} candidate(s)")
    result = await send_task_with_retry(
        AGENTS["risk_assessor"],
        "Perform risk assessment and scoring for each candidate.",
        data={"candidates": state["candidates"]},
        timeout=300.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"RiskAssessor failed: {result.message.text()}")
    risk_data = _extract_data(result, "risk_assessment") or []
    enriched_candidates = _extract_data(result, "candidates") or state["candidates"]
    print(f"      → {len(risk_data)} risk assessment(s) complete")
    return {"risk_assessment": risk_data, "candidates": enriched_candidates}


async def node_report_writer(state: PipelineState) -> dict:
    print("\n[5/5] ReportWriter ← generating final report")
    result = await send_task_with_retry(
        AGENTS["report_writer"],
        "Produce the final equity research report in Italian.",
        data={
            "candidates": state["candidates"],
            "risk_assessment": state["risk_assessment"],
            "news": state["news"],
            "themes": state["themes"],
        },
        timeout=300.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"ReportWriter failed: {result.message.text()}")
    report = _extract_data(result, "report") or {}
    summary = _extract_data(result, "executive_summary") or result.message.text()
    qa = _extract_data(result, "qa_verdict") or ""
    print("      → Report generated")
    return {"report": report, "executive_summary": summary, "qa_verdict": qa}


# ------------------------------------------------------------------ #
# Graph definition                                                     #
# ------------------------------------------------------------------ #

def _build_graph() -> StateGraph:
    builder = StateGraph(PipelineState)

    builder.add_node("data_collector", node_data_collector)
    builder.add_node("news_sentiment", node_news_sentiment)
    builder.add_node("fundamental_analyst", node_fundamental_analyst)
    builder.add_node("risk_assessor", node_risk_assessor)
    builder.add_node("report_writer", node_report_writer)

    builder.set_entry_point("data_collector")
    builder.add_edge("data_collector", "news_sentiment")
    builder.add_edge("news_sentiment", "fundamental_analyst")
    builder.add_edge("fundamental_analyst", "risk_assessor")
    builder.add_edge("risk_assessor", "report_writer")
    builder.add_edge("report_writer", END)

    return builder.compile()


_graph = _build_graph()


# ------------------------------------------------------------------ #
# Pipeline entry point                                                 #
# ------------------------------------------------------------------ #

async def run_pipeline(tickers: list[str]) -> dict:
    print("\n" + "=" * 60)
    print("  EQUITY RESEARCHER A2A — LangGraph Pipeline v2")
    print("=" * 60)

    initial_state: PipelineState = {
        "tickers": tickers,
        "fundamentals": [],
        "news": [],
        "themes": [],
        "candidates": [],
        "risk_assessment": [],
        "report": {},
        "executive_summary": "",
        "qa_verdict": "",
    }

    final_state = await _graph.ainvoke(initial_state)

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)
    return {
        "status": "completed",
        "executive_summary": final_state["executive_summary"],
        "qa_verdict": final_state["qa_verdict"],
        "report": final_state["report"],
    }


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Equity Researcher A2A Orchestrator")
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=["AAPL", "MSFT", "UCG.MI"],
        help="Initial ticker universe (enriched by FundamentalAnalyst)",
    )
    parser.add_argument("--output", default=None, help="Save JSON report to file")
    args = parser.parse_args()

    result = asyncio.run(run_pipeline(args.tickers))

    print("\n=== SINTESI ESECUTIVA ===")
    print(result.get("executive_summary", ""))
    print("\n=== QA VERDICT ===")
    print(result.get("qa_verdict", ""))

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nReport salvato in: {args.output}")
