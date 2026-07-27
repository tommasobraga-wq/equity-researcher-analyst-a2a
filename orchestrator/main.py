"""Orchestrator — LangGraph pipeline v2.

Coordinatore in linguaggio naturale (orchestrator/coordinator.py) davanti a un
grafo con due topologie scelte dinamicamente in base al prompt utente:

  modalità "specific" (ticker noti, es. "confrontami NVDA e AMD"):
    data_news_parallel (8001+8002 in parallelo) → fundamental_analyst (8003)
      → risk_assessor (8004) → report_writer (8005)

  modalità "discovery" (nessun ticker noto, es. "opportunità nel settore
  bancario europeo ora") — data_collector non sa cosa cercare finché
  news_sentiment non ha proposto candidati, quindi è sequenziale e invertito:
    news_sentiment_discovery (8002) → data_collector_from_candidates (8001)
      → fundamental_analyst (8003) → risk_assessor (8004) → report_writer (8005)
"""
import asyncio
import contextvars
import json
import os
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, TypedDict

import anthropic
import httpx
import yaml
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.coordinator import CoordinatorIntent, interpret_prompt
from shared.a2a_models import A2ATaskResult, JsonRpcRequest, JsonRpcResponse
from shared.audit import log_event
from shared.auth import enforce_secret_policy, sign, verify
from shared.db import append_turn, get_or_create_session, load_recent_turns, load_run_state, save_run_state
from shared.eligibility import check_esg_exclusions, check_restricted_list
from shared.events import emit, end_stream
from shared.log import get_logger
from shared.models import Report
from shared.portfolio import check_portfolio_limits, load_limits
from shared.report import generate_html
from shared.tools.yfinance_tool import get_daily_returns
from shared.validators import validate_stage

load_dotenv(Path(__file__).parent.parent / ".env")
enforce_secret_policy()

_logger = get_logger("orchestrator")
_coordinator_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

AGENTS = {
    "data_collector":      "http://localhost:8001",
    "news_sentiment":      "http://localhost:8002",
    "fundamental_analyst": "http://localhost:8003",
    "risk_assessor":       "http://localhost:8004",
    "report_writer":       "http://localhost:8005",
    "compliance_agent":    "http://localhost:8006",
    "portfolio_manager":   "http://localhost:8007",
}

MAX_VALIDATION_RETRIES = 2

# The run_id of the pipeline currently executing in this task tree — lets
# code without access to PipelineState (e.g. the circuit breaker) emit events
# for the right run even when the gateway runs several pipelines concurrently.
_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)


def _feedback_text(violations) -> str:
    return "\n".join(f"- [{v.rule}] {v.message}" for v in violations)


# ------------------------------------------------------------------ #
# Pipeline state                                                       #
# ------------------------------------------------------------------ #

class PipelineState(TypedDict):
    run_id: str
    mode: str  # "specific" | "discovery" — chosen once by the coordinator, drives entry topology
    tickers: list[str]
    candidate_tickers: list[str]  # populated by news_sentiment_discovery in "discovery" mode
    focus: str
    priority_sectors: list[str]
    excluded_sectors: list[str]
    fundamentals: list
    news: list
    themes: list
    candidates: list
    risk_assessment: list
    gate1_excluded: list[dict]  # tickers blocked by shared/eligibility.py (restricted list, ESG) before analysis
    compliance_flagged: list[dict]  # candidates blocked by ComplianceAgent (Gate 2, policy RAG)
    compliance_checked: bool  # True once ComplianceAgent has run for this state
    allocation: list[dict]  # PM's proposed weights, already past Gate 3 when persisted clean
    allocation_esclusi: list[dict]  # candidates the PM deliberately left at 0 weight
    nota_strategia: str
    report: dict
    executive_summary: str
    qa_verdict: str
    retries: dict[str, int]
    validation_feedback: dict[str, str]


# ------------------------------------------------------------------ #
# A2A client                                                           #
# ------------------------------------------------------------------ #

def _a2a_secret() -> str | None:
    return os.getenv("A2A_SHARED_SECRET") or None


async def send_task(
    agent_url: str,
    agent_name: str,
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
    body = json.dumps(rpc.model_dump()).encode()

    headers = {"Content-Type": "application/json"}
    secret = _a2a_secret()
    if secret is not None:
        req_timestamp = str(time.time())
        headers["X-A2A-Timestamp"] = req_timestamp
        headers["X-A2A-Signature"] = sign(secret, body, req_timestamp)

    await log_event(
        task_id, "a2a_request", agent_name, direction="outbound",
        payload={"message": message, "data": data},
    )

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{agent_url}/tasks", content=body, headers=headers)
        resp.raise_for_status()
        resp_body = resp.content
        rpc_resp = JsonRpcResponse(**json.loads(resp_body))
    duration_ms = int((time.perf_counter() - t0) * 1000)

    if secret is not None:
        resp_timestamp = resp.headers.get("X-A2A-Timestamp", "")
        resp_signature = resp.headers.get("X-A2A-Signature", "")
        if not verify(secret, resp_body, resp_timestamp, resp_signature):
            raise RuntimeError(f"Invalid A2A response signature from {agent_name}")

    if rpc_resp.error:
        await log_event(
            task_id, "a2a_response", agent_name, direction="inbound",
            payload={"error": rpc_resp.error}, status="error", duration_ms=duration_ms,
        )
        raise RuntimeError(f"Agent error: {rpc_resp.error}")

    result = A2ATaskResult(**rpc_resp.result)
    await log_event(
        task_id, "a2a_response", agent_name, direction="inbound",
        payload=result.model_dump(), status=result.status, duration_ms=duration_ms,
    )
    return result


_RATE_LIMIT_KEYWORDS = ("rate_limit", "rate limit", "too many requests", "concurrent connections", "429")

# ------------------------------------------------------------------ #
# Circuit breaker — per agent, in-memory (one orchestrator process =    #
# one pipeline run, so process-lifetime state is enough)                #
# ------------------------------------------------------------------ #

_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN_SECONDS = 60.0

_circuit_state: dict[str, dict[str, Any]] = {}


def _circuit_check(agent_name: str) -> None:
    """Raises immediately (no HTTP call attempted) if the circuit for
    `agent_name` is open. After `_CIRCUIT_COOLDOWN_SECONDS`, the circuit
    goes half-open — one call is let through to test recovery."""
    state = _circuit_state.get(agent_name)
    if not state or state["opened_at"] is None:
        return
    elapsed = time.time() - state["opened_at"]
    if elapsed < _CIRCUIT_COOLDOWN_SECONDS:
        raise RuntimeError(
            f"Circuit open for {agent_name}: {state['failures']} consecutive failures, "
            f"retrying in {_CIRCUIT_COOLDOWN_SECONDS - elapsed:.0f}s."
        )
    state["opened_at"] = None  # half-open: let the next call through


def _circuit_record_success(agent_name: str) -> None:
    _circuit_state[agent_name] = {"failures": 0, "opened_at": None}


def _circuit_record_failure(agent_name: str) -> None:
    state = _circuit_state.setdefault(agent_name, {"failures": 0, "opened_at": None})
    state["failures"] += 1
    if state["failures"] >= _CIRCUIT_FAILURE_THRESHOLD and state["opened_at"] is None:
        state["opened_at"] = time.time()
        emit(_current_run_id.get(), "circuit_open", agent=agent_name, failures=state["failures"])
        _logger.warning(
            f"Circuit opened for {agent_name} after {state['failures']} consecutive failures",
            extra={"agent": agent_name, "event_type": "circuit_open"},
        )


async def send_task_with_retry(
    agent_url: str,
    agent_name: str,
    message: str,
    data: dict[str, Any] | None = None,
    timeout: float = 300.0,
    max_retries: int = 5,
    retry_delay: float = 90.0,
) -> A2ATaskResult:
    _circuit_check(agent_name)
    try:
        for attempt in range(max_retries):
            result = await send_task(agent_url, agent_name, message, data, timeout)
            if result.status != "failed":
                _circuit_record_success(agent_name)
                return result
            error_text = result.message.text().lower()
            if not any(kw in error_text for kw in _RATE_LIMIT_KEYWORDS):
                _circuit_record_failure(agent_name)
                return result
            if attempt < max_retries - 1:
                print(f"      ⚠ Rate limit — waiting {retry_delay:.0f}s (retry {attempt + 1}/{max_retries - 1})...")
                _logger.warning(
                    f"Rate limit — retrying in {retry_delay:.0f}s (attempt {attempt + 1}/{max_retries - 1})",
                    extra={"agent": agent_name, "event_type": "rate_limit_retry"},
                )
                await asyncio.sleep(retry_delay)
        _circuit_record_failure(agent_name)
        return result
    except Exception:
        _circuit_record_failure(agent_name)
        raise


def _extract_data(result: A2ATaskResult, key: str) -> Any:
    for part in result.message.parts:
        if hasattr(part, "data") and key in part.data:
            return part.data[key]
    return None


def _record_invalid(state: PipelineState, stage: str, feedback: str) -> dict:
    """A stage's output failed validation — bump its retry counter and stash
    feedback for the next attempt. Leaves stage data fields untouched so
    LangGraph's partial-state merge keeps whatever was there before."""
    retries = state["retries"].get(stage, 0) + 1
    print(f"      ⚠ Validation failed (attempt {retries}/{MAX_VALIDATION_RETRIES}): {feedback}")
    emit(state.get("run_id"), "stage_retry", stage=stage, attempt=retries,
         max_retries=MAX_VALIDATION_RETRIES, feedback=feedback)
    _logger.warning(
        f"Validation failed (attempt {retries}/{MAX_VALIDATION_RETRIES}): {feedback}",
        extra={"agent": stage, "event_type": "validation_failed"},
    )
    return {
        "retries": {**state["retries"], stage: retries},
        "validation_feedback": {**state["validation_feedback"], stage: feedback},
    }


# ------------------------------------------------------------------ #
# Graph nodes                                                          #
# ------------------------------------------------------------------ #

async def _collect_fundamentals(state: PipelineState, tickers: list[str], stage: str = "data_collector") -> dict:
    """Core DataCollector call — reused by both the "specific" (parallel with
    news_sentiment) and "discovery" (fed by news_sentiment's candidates)
    topologies. `stage` is always the retry/feedback key regardless of which
    graph node calls this, so validation state stays consistent across modes."""
    ticker_list = ", ".join(tickers)
    print(f"      DataCollector ← {ticker_list}")
    feedback = state["validation_feedback"].get(stage, "")
    message = f"Fetch fundamental data for: {ticker_list}. Return a JSON array."
    if feedback:
        message += f"\n\nPREVIOUS ATTEMPT FEEDBACK — fix these issues:\n{feedback}"

    result = await send_task_with_retry(AGENTS["data_collector"], stage, message)
    if result.status == "failed":
        raise RuntimeError(f"DataCollector failed: {result.message.text()}")
    if result.status == "invalid":
        return _record_invalid(state, stage, result.message.text())

    fundamentals = _extract_data(result, "fundamentals")
    if fundamentals is None:
        fundamentals = json.loads(result.message.text())

    _, violations = validate_stage("fundamentals", fundamentals)
    errors = [v for v in violations if v.severity == "error"]
    if errors:
        return _record_invalid(state, stage, _feedback_text(errors))

    # Gate 1 (ESG) — needs sector/industry, only available now that fundamentals
    # are fetched. Blocked entries are folded into gate1_excluded (which already
    # carries any earlier restricted-list blocks from the caller's state) so
    # nothing downstream sees an excluded ticker again.
    eligible_fundamentals, esg_blocked = check_esg_exclusions(fundamentals)
    if esg_blocked:
        print(f"      ⚠ Gate 1 (ESG): {len(esg_blocked)} ticker(s) blocked — {', '.join(b['ticker'] for b in esg_blocked)}")
        emit(state.get("run_id"), "gate1_block", check="esg", blocked=esg_blocked)

    print(f"      → {len(eligible_fundamentals)} ticker(s) fetched")
    return {
        "fundamentals": eligible_fundamentals,
        "gate1_excluded": [*state.get("gate1_excluded", []), *esg_blocked],
        "retries": {**state["retries"], stage: 0},
    }


async def _fetch_news(state: PipelineState, focus: str, stage: str = "news_sentiment") -> dict:
    """Core NewsSentiment call — reused by both topologies. Also returns
    `candidate_tickers` (companies mentioned in the selected news), which
    "discovery" mode uses to feed DataCollector next; "specific" mode ignores
    it (tickers are already known from the user)."""
    print("      NewsSentiment ← fetching RSS feeds")
    feedback = state["validation_feedback"].get(stage, "")
    result = await send_task_with_retry(
        AGENTS["news_sentiment"],
        stage,
        "Fetch and analyze market themes.",
        data={
            "priority_sectors": state["priority_sectors"],
            "excluded_sectors": state["excluded_sectors"],
            "focus": focus,
            "validation_feedback": feedback,
        },
        timeout=180.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"NewsSentiment failed: {result.message.text()}")
    if result.status == "invalid":
        return _record_invalid(state, stage, result.message.text())

    news = _extract_data(result, "news") or []
    themes = _extract_data(result, "themes") or []
    candidate_tickers = _extract_data(result, "candidate_tickers") or []
    if not news:
        for part in result.message.parts:
            if hasattr(part, "data"):
                news = part.data.get("news", [])
                themes = part.data.get("themes", [])
                candidate_tickers = part.data.get("candidate_tickers", [])
                break

    _, violations = validate_stage("news_themes", {"news": news, "themes": themes})
    errors = [v for v in violations if v.severity == "error"]
    if errors:
        return _record_invalid(state, stage, _feedback_text(errors))

    print(f"      → {len(news)} news, {len(themes)} themes, {len(candidate_tickers)} candidate ticker(s)")
    return {
        "news": news, "themes": themes, "candidate_tickers": candidate_tickers,
        "retries": {**state["retries"], stage: 0},
    }


def _merge_partial_results(state: PipelineState, results: list[dict]) -> dict:
    """Merges the dicts returned by two concurrently-run sub-stages
    (`asyncio.gather`) without clobbering each other's `retries`/
    `validation_feedback` — both sub-results carry a *full copy* of these
    dicts (taken from the same `state` snapshot before either ran), so a
    naive `dict.update` would let whichever result is merged last silently
    discard the other's retry-count/feedback update."""
    retries = dict(state["retries"])
    feedback = dict(state["validation_feedback"])
    merged: dict = {}
    for r in results:
        if "retries" in r:
            retries.update(r["retries"])
        if "validation_feedback" in r:
            feedback.update(r["validation_feedback"])
        merged.update({k: v for k, v in r.items() if k not in ("retries", "validation_feedback")})
    merged["retries"] = retries
    merged["validation_feedback"] = feedback
    return merged


async def node_data_news_parallel(state: PipelineState) -> dict:
    """"specific" mode: DataCollector and NewsSentiment run concurrently.
    Skips whichever sub-stage already has clean data from a previous pass
    (retries==0 and data present) — a validation retry of one sub-stage
    doesn't re-run the one that already succeeded."""
    stage_dc, stage_ns = "data_collector", "news_sentiment"
    need_dc = not state.get("fundamentals") or state["retries"].get(stage_dc, 0) > 0
    need_ns = not (state.get("news") or state.get("themes")) or state["retries"].get(stage_ns, 0) > 0

    print(f"\n[1/4] DataCollector + NewsSentiment (parallel) ← {', '.join(state['tickers'])}")
    coros = []
    if need_dc:
        coros.append(_collect_fundamentals(state, state["tickers"], stage_dc))
    if need_ns:
        coros.append(_fetch_news(state, focus=state.get("focus", ""), stage=stage_ns))

    results = await asyncio.gather(*coros) if coros else []
    return _merge_partial_results(state, results)


async def node_news_sentiment_discovery(state: PipelineState) -> dict:
    """"discovery" mode, stage 1: no tickers known yet — NewsSentiment scans
    the market and proposes candidate_tickers for DataCollector to fetch next."""
    print("\n[1/5] NewsSentiment (discovery) ← scanning for opportunities")
    return await _fetch_news(state, focus=state.get("focus", ""), stage="news_sentiment")


async def node_data_collector_from_candidates(state: PipelineState) -> dict:
    """"discovery" mode, stage 2: fetch fundamentals for the candidates
    NewsSentiment proposed in stage 1 — after Gate 1's restricted-list check
    (pure ticker match, no fundamentals needed for this part of Gate 1)."""
    tickers = state.get("candidate_tickers") or []
    eligible_tickers, restricted_blocked = check_restricted_list(tickers)
    if restricted_blocked:
        print(f"      ⚠ Gate 1 (restricted list): {len(restricted_blocked)} candidate ticker(s) blocked")
        emit(state.get("run_id"), "gate1_block", check="restricted_list", blocked=restricted_blocked)
    if not eligible_tickers:
        raise RuntimeError("All candidate tickers were blocked by Gate 1 (restricted list) — nothing left to analyze.")

    print(f"\n[2/5] DataCollector (from candidates) ← {', '.join(eligible_tickers)}")
    augmented_state = {**state, "gate1_excluded": [*state.get("gate1_excluded", []), *restricted_blocked]}
    return await _collect_fundamentals(augmented_state, eligible_tickers, "data_collector")


async def node_fundamental_analyst(state: PipelineState) -> dict:
    stage = "fundamental_analyst"
    print(f"\n[+1] FundamentalAnalyst ← {len(state['news'])} news, {len(state['themes'])} themes")
    feedback = state["validation_feedback"].get(stage, "")
    result = await send_task_with_retry(
        AGENTS["fundamental_analyst"],
        stage,
        "Analyse the provided news, themes and fundamentals. Return equity candidates.",
        data={
            "news": state["news"],
            "themes": state["themes"],
            "fundamentals": state["fundamentals"],
            "priority_sectors": state["priority_sectors"],
            "excluded_sectors": state["excluded_sectors"],
            "validation_feedback": feedback,
        },
        timeout=300.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"FundamentalAnalyst failed: {result.message.text()}")
    if result.status == "invalid":
        return _record_invalid(state, stage, result.message.text())

    candidates = _extract_data(result, "candidates")
    if candidates is None:
        candidates = json.loads(result.message.text())

    _, violations = validate_stage("candidates", candidates)
    errors = [v for v in violations if v.severity == "error"]
    if errors:
        return _record_invalid(state, stage, _feedback_text(errors))

    print(f"      → {len(candidates)} candidate(s) identified")
    return {"candidates": candidates, "retries": {**state["retries"], stage: 0}}


async def node_risk_assessor(state: PipelineState) -> dict:
    stage = "risk_assessor"
    # Wait for Haiku rate limit window to reset after FundamentalAnalyst's token usage
    await asyncio.sleep(90)
    print(f"\n[+2] RiskAssessor ← {len(state['candidates'])} candidate(s)")
    feedback = state["validation_feedback"].get(stage, "")
    result = await send_task_with_retry(
        AGENTS["risk_assessor"],
        stage,
        "Perform risk assessment and scoring for each candidate.",
        data={"candidates": state["candidates"], "validation_feedback": feedback},
        timeout=300.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"RiskAssessor failed: {result.message.text()}")
    if result.status == "invalid":
        return _record_invalid(state, stage, result.message.text())

    risk_data = _extract_data(result, "risk_assessment") or []
    enriched_candidates = _extract_data(result, "candidates") or state["candidates"]

    _, violations = validate_stage("risk_assessment", risk_data)
    errors = [v for v in violations if v.severity == "error"]
    if errors:
        return _record_invalid(state, stage, _feedback_text(errors))

    print(f"      → {len(risk_data)} risk assessment(s) complete")
    return {
        "risk_assessment": risk_data,
        "candidates": enriched_candidates,
        "retries": {**state["retries"], stage: 0},
    }


async def node_compliance_agent(state: PipelineState) -> dict:
    """Gate 2 — validates every candidate against internal policies via RAG
    (agents/compliance-agent). Runs after risk_assessor and before
    report_writer, so non-compliant candidates never reach the final report.
    Unlike a validation retry, a "non-compliant" verdict is a legitimate
    outcome, not an agent error — it partitions candidates/risk_assessment
    rather than triggering a retry loop."""
    stage = "compliance_agent"
    print(f"\n[+3] ComplianceAgent ← {len(state['candidates'])} candidate(s)")
    feedback = state["validation_feedback"].get(stage, "")
    result = await send_task_with_retry(
        AGENTS["compliance_agent"],
        stage,
        "Check each candidate against internal compliance policies.",
        data={
            "candidates": state["candidates"],
            "risk_assessment": state["risk_assessment"],
            "validation_feedback": feedback,
        },
        timeout=300.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"ComplianceAgent failed: {result.message.text()}")
    if result.status == "invalid":
        return _record_invalid(state, stage, result.message.text())

    compliance_results = _extract_data(result, "compliance_results") or []
    verdict_by_ticker = {r["ticker"]: r for r in compliance_results}

    compliant_candidates = [
        c for c in state["candidates"] if verdict_by_ticker.get(c.get("ticker"), {}).get("compliant", True)
    ]
    compliant_tickers = {c.get("ticker") for c in compliant_candidates}
    compliant_risk = [r for r in state["risk_assessment"] if r.get("ticker") in compliant_tickers]

    newly_flagged = [
        {"ticker": t, "motivo_esclusione": v.get("motivo", "Non conforme alle policy interne.")}
        for t, v in verdict_by_ticker.items() if not v.get("compliant", True)
    ]
    if newly_flagged:
        print(f"      ⚠ Gate 2: {len(newly_flagged)} candidate(s) flagged non-compliant — {', '.join(f['ticker'] for f in newly_flagged)}")
        emit(state.get("run_id"), "gate2_flag", flagged=newly_flagged)

    print(f"      → {len(compliant_candidates)}/{len(state['candidates'])} candidate(s) compliant")
    return {
        "candidates": compliant_candidates,
        "risk_assessment": compliant_risk,
        "compliance_flagged": [*state.get("compliance_flagged", []), *newly_flagged],
        "compliance_checked": True,
        "retries": {**state["retries"], stage: 0},
    }


async def _fetch_returns(tickers: list[str], lookback_days: int) -> dict[str, list[float]]:
    """Best-effort price-history fetch for Gate 3's correlation check — a
    yfinance hiccup degrades to a warning in check_portfolio_limits, it never
    blocks an otherwise valid allocation."""
    async def one(ticker: str) -> tuple[str, list[float]]:
        try:
            return ticker, await asyncio.to_thread(get_daily_returns, ticker, lookback_days)
        except Exception:
            return ticker, []
    pairs = await asyncio.gather(*(one(t) for t in tickers))
    return {t: r for t, r in pairs if r}


async def node_portfolio_manager(state: PipelineState) -> dict:
    """Portfolio Manager + Gate 3. The PM agent proposes weights; Gate 3
    (shared/portfolio.py) then checks the aggregate limits deterministically
    — concentration, pairwise correlation, drawdown proxy. A breach feeds the
    violation text back to the PM through the standard validation-retry loop,
    so the PM re-allocates instead of the pipeline failing outright."""
    stage = "portfolio_manager"
    limits = load_limits()
    print(f"\n[+4] PortfolioManager ← {len(state['candidates'])} compliant candidate(s)")
    feedback = state["validation_feedback"].get(stage, "")
    result = await send_task_with_retry(
        AGENTS["portfolio_manager"],
        stage,
        "Propose a portfolio allocation for the compliant candidates.",
        data={
            "candidates": state["candidates"],
            "risk_assessment": state["risk_assessment"],
            "fundamentals": state["fundamentals"],
            "portfolio_limits": limits,
            "validation_feedback": feedback,
        },
        timeout=300.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"PortfolioManager failed: {result.message.text()}")
    if result.status == "invalid":
        return _record_invalid(state, stage, result.message.text())

    allocation = _extract_data(result, "allocation") or []
    allocation_esclusi = _extract_data(result, "allocation_esclusi") or []
    nota_strategia = _extract_data(result, "nota_strategia") or ""

    _, violations = validate_stage("allocation", allocation)
    errors = [v for v in violations if v.severity == "error"]
    if errors:
        return _record_invalid(state, stage, _feedback_text(errors))

    # The PM may only drop a candidate by listing it in esclusi with a reason —
    # a ticker silently missing from both lists is a malformed allocation.
    covered = {e.get("ticker") for e in allocation} | {e.get("ticker") for e in allocation_esclusi}
    missing = [c.get("ticker") for c in state["candidates"] if c.get("ticker") not in covered]
    if missing:
        return _record_invalid(
            state, stage,
            f"- [allocation_coverage] Candidati senza peso né motivo di esclusione: {', '.join(missing)}.",
        )

    # Gate 3 — deterministic aggregate limits.
    lookback = int((limits.get("correlation") or {}).get("lookback_days", 180))
    returns = await _fetch_returns([e["ticker"] for e in allocation], lookback)
    gate3_violations = check_portfolio_limits(allocation, state["fundamentals"], returns, limits)
    gate3_errors = [v for v in gate3_violations if v.severity == "error"]
    for v in gate3_violations:
        if v.severity == "warning":
            print(f"      ℹ Gate 3: {v.message}")
    if gate3_errors:
        print(f"      ⚠ Gate 3: {len(gate3_errors)} limite/i di portafoglio violato/i — re-allocazione richiesta")
        emit(state.get("run_id"), "gate3_violation",
             violations=[v.as_dict() for v in gate3_errors])
        return _record_invalid(state, stage, _feedback_text(gate3_errors))

    print(f"      → Allocation approved by Gate 3: {len(allocation)} position(s), "
          f"{100 - sum(float(e['peso_pct']) for e in allocation):.1f}% cash")
    return {
        "allocation": allocation,
        "allocation_esclusi": allocation_esclusi,
        "nota_strategia": nota_strategia,
        "retries": {**state["retries"], stage: 0},
    }


async def node_report_writer(state: PipelineState) -> dict:
    stage = "report_writer"
    print("\n[+3] ReportWriter ← generating final report")
    feedback = state["validation_feedback"].get(stage, "")
    result = await send_task_with_retry(
        AGENTS["report_writer"],
        stage,
        "Produce the final equity research report in Italian.",
        data={
            "candidates": state["candidates"],
            "risk_assessment": state["risk_assessment"],
            "news": state["news"],
            "themes": state["themes"],
            "allocation": state.get("allocation", []),
            "nota_strategia": state.get("nota_strategia", ""),
            "validation_feedback": feedback,
        },
        timeout=300.0,
    )
    if result.status == "failed":
        raise RuntimeError(f"ReportWriter failed: {result.message.text()}")
    if result.status == "invalid":
        return _record_invalid(state, stage, result.message.text())

    report = _extract_data(result, "report") or {}
    summary = _extract_data(result, "executive_summary") or result.message.text()
    qa = _extract_data(result, "qa_verdict") or ""

    # Merge Gate 1/Gate 2 exclusions into candidati_esclusi deterministically —
    # in Python, not via the LLM prompt — so compliance-critical exclusion data
    # can never be dropped or reworded by report generation.
    gate_excluded = [
        *state.get("gate1_excluded", []),
        *state.get("compliance_flagged", []),
        *state.get("allocation_esclusi", []),
    ]
    if gate_excluded:
        report["candidati_esclusi"] = [*report.get("candidati_esclusi", []), *gate_excluded]

    # Same deterministic-merge rule for the allocation: the numbers Gate 3
    # approved are copied into the report verbatim in Python — the LLM writes
    # commentary around them but can never alter or drop a weight.
    if state.get("allocation"):
        report["allocazione"] = state["allocation"]
        report["nota_allocazione"] = state.get("nota_strategia", "")

    try:
        report_model = Report(**report)
    except Exception:
        report_model = None
    _, violations = validate_stage("report", report_model)
    errors = [v for v in violations if v.severity == "error"]
    if errors:
        return _record_invalid(state, stage, _feedback_text(errors))

    print("      → Report generated")
    return {
        "report": report,
        "executive_summary": summary,
        "qa_verdict": qa,
        "retries": {**state["retries"], stage: 0},
    }


# ------------------------------------------------------------------ #
# Graph definition                                                     #
# ------------------------------------------------------------------ #

def _make_router(stage: str, next_node: str, loop_node: str | None = None):
    """Loop back to `loop_node` (defaults to `stage` — true whenever the
    graph node's name matches its retry-tracking stage name, as for
    fundamental_analyst/risk_assessor/report_writer) while validation retries
    are pending for `stage`; proceed to `next_node` once a clean attempt
    resets the counter to 0; hard-fail once MAX_VALIDATION_RETRIES is
    exceeded. `loop_node` must be passed explicitly whenever the graph node
    name differs from the stage name (e.g. "news_sentiment_discovery" node
    tracking retries under the "news_sentiment" stage key, shared with the
    "specific"-mode topology)."""
    loop_node = loop_node or stage

    def router(state: PipelineState) -> str:
        retries = state["retries"].get(stage, 0)
        if retries == 0:
            return next_node
        if retries > MAX_VALIDATION_RETRIES:
            raise RuntimeError(
                f"{stage} failed validation after {MAX_VALIDATION_RETRIES} retries: "
                f"{state['validation_feedback'].get(stage, '')}"
            )
        return loop_node
    return router


def _make_dual_router(stage_a: str, stage_b: str, next_node: str, loop_node: str):
    """Same semantics as `_make_router`, but for `node_data_news_parallel`
    where two independent sub-stages share one graph node: only advances
    once *both* stages have a clean (retries==0) attempt; hard-fails if
    either exceeds MAX_VALIDATION_RETRIES."""
    def router(state: PipelineState) -> str:
        for stage in (stage_a, stage_b):
            retries = state["retries"].get(stage, 0)
            if retries > MAX_VALIDATION_RETRIES:
                raise RuntimeError(
                    f"{stage} failed validation after {MAX_VALIDATION_RETRIES} retries: "
                    f"{state['validation_feedback'].get(stage, '')}"
                )
        if state["retries"].get(stage_a, 0) == 0 and state["retries"].get(stage_b, 0) == 0:
            return next_node
        return loop_node
    return router


def _with_persistence(node_fn, stage: str):
    """Wraps a node so its resulting state snapshot is saved to
    pipeline_runs after every stage (not just at pipeline completion) —
    a crash between stages can then be resumed instead of restarting the
    whole pipeline. Best-effort (see shared/db.py::save_run_state): a save
    failure never breaks the pipeline itself."""
    async def wrapped(state: PipelineState) -> dict:
        emit(state.get("run_id"), "stage_start", stage=stage)
        t0 = time.perf_counter()
        result = await node_fn(state)
        merged = {**state, **result}
        await save_run_state(state["run_id"], state["tickers"], "running", stage, merged)
        # A retry pass through the same node still emits stage_end — the UI
        # distinguishes it because a stage_retry event precedes it.
        emit(state.get("run_id"), "stage_end", stage=stage,
             duration_s=round(time.perf_counter() - t0, 1),
             clean=all(v == 0 for v in merged.get("retries", {}).values()))
        return result
    return wrapped


def _entry_router(state: PipelineState) -> str | list[str]:
    """Picks the graph's starting node(s) from which stage data is already
    present in `state` — a fresh run starts at the node(s) matching
    `state["mode"]`, a resumed run (loaded from pipeline_runs) jumps straight
    to the first stage that hasn't completed yet."""
    if state.get("allocation"):
        return "report_writer"
    if state.get("compliance_checked"):
        return "portfolio_manager"
    if state.get("risk_assessment"):
        return "compliance_agent"
    if state.get("candidates"):
        return "risk_assessor"
    if state.get("fundamentals") and (state.get("news") or state.get("themes")):
        return "fundamental_analyst"
    if state["mode"] == "specific":
        return ["data_news_parallel"]
    # "discovery": news_sentiment done but fundamentals still pending, or nothing done yet
    if state.get("news") or state.get("themes"):
        return "data_collector_from_candidates"
    return "news_sentiment_discovery"


def _build_graph() -> StateGraph:
    builder = StateGraph(PipelineState)

    builder.add_node("data_news_parallel", _with_persistence(node_data_news_parallel, "data_news_parallel"))
    builder.add_node("news_sentiment_discovery", _with_persistence(node_news_sentiment_discovery, "news_sentiment_discovery"))
    builder.add_node("data_collector_from_candidates", _with_persistence(node_data_collector_from_candidates, "data_collector_from_candidates"))
    builder.add_node("fundamental_analyst", _with_persistence(node_fundamental_analyst, "fundamental_analyst"))
    builder.add_node("risk_assessor", _with_persistence(node_risk_assessor, "risk_assessor"))
    builder.add_node("compliance_agent", _with_persistence(node_compliance_agent, "compliance_agent"))
    builder.add_node("portfolio_manager", _with_persistence(node_portfolio_manager, "portfolio_manager"))
    builder.add_node("report_writer", _with_persistence(node_report_writer, "report_writer"))

    builder.set_conditional_entry_point(_entry_router)
    builder.add_conditional_edges(
        "data_news_parallel",
        _make_dual_router("data_collector", "news_sentiment", "fundamental_analyst", "data_news_parallel"),
    )
    builder.add_conditional_edges(
        "news_sentiment_discovery",
        _make_router("news_sentiment", "data_collector_from_candidates", loop_node="news_sentiment_discovery"),
    )
    builder.add_conditional_edges(
        "data_collector_from_candidates",
        _make_router("data_collector", "fundamental_analyst", loop_node="data_collector_from_candidates"),
    )
    builder.add_conditional_edges("fundamental_analyst", _make_router("fundamental_analyst", "risk_assessor"))
    builder.add_conditional_edges("risk_assessor", _make_router("risk_assessor", "compliance_agent"))
    builder.add_conditional_edges("compliance_agent", _make_router("compliance_agent", "portfolio_manager"))
    builder.add_conditional_edges("portfolio_manager", _make_router("portfolio_manager", "report_writer"))
    builder.add_conditional_edges("report_writer", _make_router("report_writer", END))

    return builder.compile()


_graph = _build_graph()


# ------------------------------------------------------------------ #
# Pipeline entry point                                                 #
# ------------------------------------------------------------------ #

async def run_pipeline(
    mode: str,
    tickers: list[str],
    priority_sectors: list[str],
    excluded_sectors: list[str],
    focus: str = "",
    resume_run_id: str | None = None,
    run_id: str | None = None,
    open_browser: bool = True,
) -> dict:
    print("\n" + "=" * 60)
    print("  EQUITY RESEARCHER A2A — LangGraph Pipeline v2")
    print("=" * 60)

    initial_state: PipelineState | None = None
    if resume_run_id:
        loaded = await load_run_state(resume_run_id)
        if loaded is None:
            raise RuntimeError(
                f"Cannot resume run {resume_run_id}: no persisted state found "
                "(DATABASE_URL unset, unknown run_id, or the DB is unreachable)."
            )
        initial_state = loaded
        print(f"      → Resuming run {resume_run_id}")

    if initial_state is None:
        gate1_excluded: list[dict] = []
        if mode == "specific":
            # Gate 1 (restricted list) — pure ticker-symbol match, no fundamentals
            # needed yet, so it runs once here instead of inside a graph node.
            tickers, gate1_excluded = check_restricted_list(tickers)
            if gate1_excluded:
                print(f"      ⚠ Gate 1 (restricted list): {len(gate1_excluded)} ticker(s) blocked")
            if not tickers:
                raise RuntimeError("All requested tickers were blocked by Gate 1 (restricted list).")

        initial_state = {
            "run_id": run_id or str(uuid.uuid4()),
            "mode": mode,
            "tickers": tickers,
            "candidate_tickers": [],
            "focus": focus,
            "priority_sectors": priority_sectors,
            "excluded_sectors": excluded_sectors,
            "fundamentals": [],
            "news": [],
            "themes": [],
            "candidates": [],
            "risk_assessment": [],
            "gate1_excluded": gate1_excluded,
            "compliance_flagged": [],
            "compliance_checked": False,
            "allocation": [],
            "allocation_esclusi": [],
            "nota_strategia": "",
            "report": {},
            "executive_summary": "",
            "qa_verdict": "",
            "retries": {},
            "validation_feedback": {},
        }
    run_id = initial_state["run_id"]
    print(f"      → run_id: {run_id} (mode={initial_state['mode']})")
    _current_run_id.set(run_id)
    if initial_state.get("gate1_excluded"):
        # The "specific"-mode restricted-list check ran before run_id existed.
        emit(run_id, "gate1_block", check="restricted_list", blocked=initial_state["gate1_excluded"])

    t0 = time.time()
    try:
        final_state = await _graph.ainvoke(initial_state)
    except Exception as e:
        await save_run_state(run_id, initial_state["tickers"], "failed", "unknown", initial_state)
        emit(run_id, "run_failed", error=str(e))
        end_stream(run_id)
        raise
    execution_seconds = int(time.time() - t0)
    await save_run_state(run_id, final_state["tickers"], "completed", "report_writer", final_state)

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)

    # Tickers actually analyzed — for "discovery" mode this is the candidates
    # NewsSentiment proposed, not the (empty) user-supplied `tickers`.
    analyzed_tickers = sorted({f.get("ticker") for f in final_state.get("fundamentals", []) if f.get("ticker")}) or tickers

    report_path, violations = generate_html(
        executive_summary=final_state["executive_summary"],
        report_dict=final_state["report"],
        qa_verdict=final_state["qa_verdict"],
        tickers=analyzed_tickers,
        execution_seconds=execution_seconds,
    )
    print(f"\nReport HTML salvato in: {report_path}")
    if violations:
        errors = [v for v in violations if v.severity == "error"]
        warnings = [v for v in violations if v.severity == "warning"]
        if errors:
            print(f"  ⚠  {len(errors)} errore/i critico/i nel report")
        if warnings:
            print(f"  ℹ  {len(warnings)} avvertimento/i di qualità")
    if open_browser:
        webbrowser.open(report_path.as_uri())

    emit(run_id, "run_complete",
         report_path=str(report_path),
         executive_summary=final_state["executive_summary"],
         qa_verdict=final_state["qa_verdict"],
         execution_seconds=execution_seconds,
         analyzed_tickers=analyzed_tickers)
    end_stream(run_id)

    return {
        "status": "completed",
        "run_id": run_id,
        "executive_summary": final_state["executive_summary"],
        "qa_verdict": final_state["qa_verdict"],
        "report": final_state["report"],
        "report_path": str(report_path),
    }


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

def _print_result(result: dict) -> None:
    print("\n=== SINTESI ESECUTIVA ===")
    print(result.get("executive_summary", ""))
    print("\n=== QA VERDICT ===")
    print(result.get("qa_verdict", ""))


async def _interactive_loop(
    session_arg: str | None, new_session: bool,
    default_priority_sectors: list[str], default_excluded_sectors: list[str],
) -> None:
    """Multi-turn REPL: reads free-text requests, has the coordinator
    interpret each one (with the session's conversation history as context),
    and runs the pipeline. Conversation turns persist to
    conversation_turns/conversation_sessions (shared/db.py) — best-effort,
    degrades to a single-session-only experience if DATABASE_URL isn't set."""
    session_id = None if new_session else session_arg
    session_id = await get_or_create_session(session_id)
    if session_id:
        print(f"Sessione: {session_id} (memoria cross-sessione attiva)")
    else:
        print("DATABASE_URL non configurato — nessuna memoria tra sessioni per questa esecuzione.")

    print('Scrivi una richiesta in linguaggio naturale (es. "confrontami NVDA e AMD", '
          '"opportunità nel settore bancario europeo ora"). "exit" o Ctrl-D per uscire.\n')

    while True:
        try:
            user_input = input("> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break

        history = await load_recent_turns(session_id) if session_id else []
        if session_id:
            await append_turn(session_id, "user", user_input)

        try:
            intent: CoordinatorIntent = await interpret_prompt(_coordinator_client, user_input, history)
        except Exception as e:
            print(f"⚠ Non sono riuscito a interpretare la richiesta: {e}")
            continue

        priority_sectors = intent.priority_sectors or default_priority_sectors
        excluded_sectors = intent.excluded_sectors or default_excluded_sectors

        try:
            result = await run_pipeline(
                mode=intent.mode,
                tickers=intent.tickers,
                priority_sectors=priority_sectors,
                excluded_sectors=excluded_sectors,
                focus=intent.focus,
            )
        except Exception as e:
            print(f"⚠ Pipeline fallita: {e}")
            if session_id:
                await append_turn(session_id, "assistant", f"[errore] {e}")
            continue

        _print_result(result)
        if session_id:
            await append_turn(
                session_id, "assistant", result.get("executive_summary", ""),
                run_id=result.get("run_id"),
            )
        print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Equity Researcher A2A Orchestrator")
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Non-interactive shortcut: analyze exactly these tickers and exit "
             "(bypasses the natural-language coordinator).",
    )
    parser.add_argument("--output", default=None, help="Save JSON report to file (only with --tickers/--resume)")
    parser.add_argument(
        "--resume", default=None, metavar="RUN_ID",
        help="Resume a previously interrupted run from its last completed stage "
             "(requires DATABASE_URL — see shared/db.py::pipeline_runs).",
    )
    parser.add_argument("--session", default=None, metavar="SESSION_ID", help="Resume a specific conversation session")
    parser.add_argument("--new-session", action="store_true", help="Start a new conversation session instead of continuing the last one")
    args = parser.parse_args()

    config_path = Path("config.yaml")
    config_data = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}

    default_priority_sectors = config_data.get("priority_sectors", ["Technology", "Banking"])
    default_excluded_sectors = config_data.get("excluded_sectors", ["crypto", "DeFi", "Web3"])

    if args.tickers or args.resume:
        # Non-interactive, single-shot: scripted use, or resuming a specific run.
        tickers = args.tickers or config_data.get("tickers", ["AAPL", "MSFT", "UCG.MI"])
        result = asyncio.run(run_pipeline(
            mode="specific", tickers=tickers,
            priority_sectors=default_priority_sectors, excluded_sectors=default_excluded_sectors,
            resume_run_id=args.resume,
        ))
        _print_result(result)
        if args.output:
            Path(args.output).write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"\nReport salvato in: {args.output}")
    else:
        asyncio.run(_interactive_loop(
            args.session, args.new_session, default_priority_sectors, default_excluded_sectors,
        ))
