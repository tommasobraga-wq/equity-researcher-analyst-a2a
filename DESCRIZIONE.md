# Equity Researcher A2A — System Description

## The Solution

**Equity Researcher A2A** is an AI-native equity research platform that automates the entire analytical cycle — from data collection to final report generation — by combining five specialized intelligent agents that collaborate through the **A2A (Agent-to-Agent)** protocol.

The system takes a list of stock tickers as input (e.g. `AAPL MSFT UCG.MI`) and, fully automatically, collects market fundamentals, processes real-time financial news, identifies the candidates with the highest potential, assesses risk across five quantitative dimensions, and generates a professional report with executive summary, base/bull/bear scenarios, and comparative scoring.

The architecture decomposes a traditional monolithic CrewAI pipeline into **5 independent FastAPI microservices** communicating via JSON-RPC 2.0 over HTTP. Each agent is built on a different AI framework — OpenAI Agents SDK, Smolagents, BeeAI, and the Anthropic SDK directly — a deliberate choice that makes the system a live architectural benchmark across the main agentic orchestration paradigms.

---

## Benefits

**Speed and scalability**
The full research pipeline — which would require hours of analytical work if done manually — completes in minutes. Each agent is an autonomous microservice: it can be scaled, replaced, or updated independently without affecting the others.

**Complete information coverage**
The system simultaneously aggregates data from heterogeneous sources — RSS feeds from Reuters, Yahoo Finance, MarketWatch, and Investing.com for news, yfinance for fundamentals — ensuring that no relevant information is lost due to time constraints or limited human attention.

**Quality and reproducibility**
Every claim in the report is traceable: news items are identified by unique codes (N1, N2…) and explicitly cited for each candidate. An automated QA pass verifies calculation consistency (scoring, analyst consensus) and formal correctness before the report is delivered.

**Guardrails and risk control**
The Risk Assessor includes hardcoded conditional constraints: it refuses to produce a score if volatility data (52-week range, P/E ratio) is missing, preventing risk assessments based on incomplete data.

**Separation of concerns**
Each agent has a clearly defined area of responsibility. Changing the model, framework, or data source on a single agent requires no modifications to the others — the A2A interface is the stable contract.

**Extensibility**
The LangGraph orchestrator allows new agents (Portfolio Manager, Macro Agent, Earnings Calendar) to be added by modifying only the graph definition, without touching the logic of existing nodes.

---

## Key Uses

| Use case | Description |
|----------|-------------|
| **Automated morning briefing** | Run the pipeline at market open to receive an up-to-date report on a predefined ticker universe, with no manual intervention |
| **Pre-earnings screening** | Analyse candidates in Technology, AI, and Banking sectors ahead of earnings seasons, with company-specific investment theses and catalysts |
| **Portfolio review** | Feed an existing portfolio's tickers to get an updated risk profile assessment and analyst consensus changes |
| **Thematic research** | Identify the best stocks exposed to a specific market theme (e.g. AI, semiconductors) by cross-referencing recent news and fundamentals |
| **Architectural prototyping** | Use the project as a template to benchmark agentic AI frameworks (OpenAI Agents SDK vs Smolagents vs BeeAI vs Anthropic SDK) on a real pipeline |
| **Training and education** | Study in practice the ReAct, Chain of Thought, Feedback Loop, Conditional Constraints, and Structured Output patterns on a concrete use case |

---

## Overview

## How to Start the System

```bash
# 1. Install dependencies
uv sync

# 2. Start the 5 agents (each in a separate terminal)
uv run python agents/data-collector/agent.py      # port 8001
uv run python agents/news-sentiment/agent.py      # port 8002
uv run python agents/fundamental-analyst/agent.py # port 8003
uv run python agents/risk-assessor/agent.py       # port 8004
uv run python agents/report-writer/agent.py       # port 8005

# 3. Run the pipeline
uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI

# Save report to file
uv run python orchestrator/main.py --tickers AAPL MSFT --output report.json
```

Only `ANTHROPIC_API_KEY` in the `.env` file at the project root is required. No other keys are needed (yfinance and RSS feeds are free).

---

## General Architecture

```
┌─────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                      │
│              LangGraph StateGraph v2                 │
│                                                      │
│  PipelineState (TypedDict) — accumulated data        │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌──────┐ ┌──────┐│
│  │  [1]   │→│  [2]   │→│  [3]   │→│ [4]  │→│ [5]  ││
│  │Data    │ │News    │ │Fundmt. │ │Risk  │ │Report││
│  │Collect.│ │Sentim. │ │Analyst │ │Asses.│ │Writer││
│  │:8001   │ │:8002   │ │:8003   │ │:8004 │ │:8005 ││
│  └────────┘ └────────┘ └────────┘ └──────┘ └──────┘│
└─────────────────────────────────────────────────────┘
         ↑  Communication via JSON-RPC 2.0 over HTTP  ↑
```

The pipeline is **sequential**: each node receives the state accumulated by previous steps and appends its own results. The orchestrator uses **LangGraph** (`StateGraph`) — adding parallel branches or retry loops only requires modifying `_build_graph()` in `orchestrator/main.py`, without touching node logic.

---

## The A2A Protocol

All messages between the orchestrator and agents follow **JSON-RPC 2.0**:

```json
// Request (Orchestrator → Agent)
{
  "jsonrpc": "2.0",
  "method": "tasks/send",
  "params": {
    "id": "task-uuid",
    "message": {
      "role": "user",
      "parts": [
        {"type": "text",  "text": "Fetch data for AAPL, MSFT"},
        {"type": "data",  "data": {"candidates": [...]}}
      ]
    }
  },
  "id": 1
}

// Response (Agent → Orchestrator)
{
  "jsonrpc": "2.0",
  "result": {
    "id": "task-uuid",
    "status": "completed",
    "message": {
      "role": "agent",
      "parts": [
        {"type": "text", "text": "Fundamentals fetched."},
        {"type": "data", "data": {"fundamentals": [...]}}
      ]
    }
  },
  "id": 1
}
```

Messages can contain **text** parts (free-form text) and **data** parts (structured dictionaries). Structured data always travels as `DataPart`. Pydantic models are defined in `shared/a2a_models.py`.

Each agent also exposes:
- `GET /.well-known/agent.json` — Agent Card for discovery
- `GET /health` — liveness check

---

## The 5 Agents in Detail

### [1] Data Collector — port 8001
**Framework:** OpenAI Agents SDK (via LiteLLM) | **Model:** `claude-haiku-4-5-20251001`

Receives the ticker list from the orchestrator, calls `fetch_fundamentals` individually for each one via **yfinance**, and returns a JSON array of fundamentals. The tool is decorated with `@function_tool` (OpenAI Agents SDK pattern).

Data returned per ticker: current price, P/E TTM, forward P/E, EPS TTM, 52-week range, market cap, average analyst target, analyst count, recommendation, buy/hold/sell breakdown, sector.

### [2] News & Sentiment — port 8002
**Framework:** Smolagents (HuggingFace) `CodeAgent` | **Model:** `claude-haiku-4-5-20251001`

Reads 6 financial RSS feeds (Reuters, Yahoo Finance, MarketWatch, Investing.com) via `read_financial_rss`. Selects the 10–12 most relevant articles for priority sectors, assigns each a unique ID (N1, N2, …), and clusters them into 3–4 macro market themes.

**Included sectors:** Technology, AI, Software, Semiconductors, Banking, Financial Services.  
**Excluded sectors:** energy, utilities, real estate, REITs, consumer staples, industrials, airlines, crypto/DeFi/Web3.

Output: JSON object `{"news": [...], "themes": [...]}`.

### [3] Fundamental Analyst — port 8003
**Framework:** BeeAI `ReActAgent` | **Model:** `claude-haiku-4-5-20251001`

Receives news, themes, and pre-fetched fundamentals from the previous step. Identifies up to 3 equity candidates that best fit the market themes, calls `fetch_fundamentals` to verify and enrich data, and builds a company-specific investment thesis (not just macro commentary).

Output: JSON array of candidates with ticker, thesis, catalyst, supporting news IDs, fundamentals, and analyst consensus.

> **BeeAI note:** The `ReActAgent` uses assistant message prefill internally — incompatible with Sonnet 4.6. BeeAI agents must stay on `claude-haiku-4-5-20251001` until BeeAI adds a non-prefill runner for Claude 4.x.

### [4] Risk Assessor — port 8004
**Framework:** BeeAI `ReActAgent` with Conditional Constraints | **Model:** `claude-haiku-4-5-20251001`

Receives the candidates identified by the Fundamental Analyst. For each candidate:

1. **Guardrail** — calls `check_volatility_data` to verify that the 52-week range and P/E are available. If missing: the candidate receives `"quality": "insufficient_data"` and all scores are set to 0.
2. **Scenarios** — produces company-specific base/bull/bear analysis.
3. **Scoring** — evaluates 5 dimensions from 1 to 10 (maximum 50 total):
   - `catalyst_strength` — strength of the specific catalyst
   - `horizon_fit` — consistency with the investment time horizon
   - `narrative_asymmetry` — upside vs downside narrative potential
   - `evidence_quality` — quality and specificity of supporting evidence
   - `crowding_risk` — risk of crowded positioning

### [5] Report Writer — port 8005
**Framework:** Anthropic SDK direct | **Model:** `claude-sonnet-4-6` (report + QA)

Produces the final report in two steps:

**Step 1 — Report generation** (`max_tokens=16000`):
- Receives candidates, risk assessment, news, and themes
- Produces a document with two marked sections:
  - `=== EXECUTIVE SUMMARY ===` — maximum 10 lines, neutral tone, no buy/sell directives
  - `=== JSON ===` — full structure according to `_REPORT_SCHEMA`

**Step 2 — QA review** (same model, `max_tokens=2048`):
- Checks: JSON schema compliance, news citations, no explicit buy/sell, scoring correctness, Italian language, consistent dates
- Responds with `QA: [APPROVED|CORRECTED]` and optionally `=== CORRECTIONS ===`

---

## Final Report Schema (JSON)

```json
{
  "analysis_date": "YYYY-MM-DD",
  "universe": "US and EU equities",
  "themes": [
    {
      "theme_id": "T1",
      "title": "...",
      "why_now": "...",
      "evidence": ["N1"],
      "indicators_to_monitor": ["item"]
    }
  ],
  "candidates": [
    {
      "rank": 1,
      "ticker": "AAPL",
      "company": "Apple Inc.",
      "market": "US",
      "theme": "T1",
      "thesis": "...",
      "catalyst": "...",
      "horizon_weeks": "...",
      "scenarios": {"base": "", "bull": "", "bear": ""},
      "risks": {"macro": "", "sector": "", "company": "", "regulatory": "", "valuation": ""},
      "falsification_trigger": "what would invalidate the thesis",
      "next_checks": ["item"],
      "cited_evidence": ["N1", "N2"],
      "quality_rating": "high|medium|low",
      "scoring": {
        "catalyst_strength": 8,
        "horizon_fit": 7,
        "narrative_asymmetry": 6,
        "evidence_quality": 7,
        "crowding_risk": 5,
        "total": 33
      },
      "analyst_consensus": {
        "total_analysts": 42,
        "strong_buy": 20, "buy": 15, "hold": 5, "sell": 1, "strong_sell": 1,
        "summary": "Buy",
        "average_target": "$220"
      }
    }
  ],
  "excluded_candidates": [{"ticker": "...", "exclusion_reason": "..."}],
  "methodological_note": "..."
}
```

---

## Shared Tools

### `shared/tools/yfinance_tool.py`
Wrapper around **yfinance** with a 15-second timeout per ticker (via `ThreadPoolExecutor`). Exposes two functions:
- `get_stock_fundamentals(ticker)` → dictionary
- `get_stock_fundamentals_text(ticker)` → formatted string

### `shared/tools/rss_feed.py`
Reads 6 financial RSS feeds with retry logic:
- Reuters Markets
- Yahoo Finance
- MarketWatch
- Investing.com (×2 feeds)

---

## Rate Limit Handling

The orchestrator implements two mechanisms:

1. **`send_task_with_retry`** — detects `rate_limit` errors in the response and retries up to 3 times with a 65-second wait between attempts.
2. **Explicit sleep** — the `node_risk_assessor` node waits 65 seconds before starting, to let the Haiku rate limit window reset after the Fundamental Analyst's intensive tool calls.

---

## Domain Constraints (hardcoded)

| Parameter | Value |
|-----------|-------|
| Universe | US and EU equities (UK/LSE excluded) |
| Excluded sectors | energy, utilities, real estate, REITs, consumer staples, industrials, airlines, crypto/DeFi/Web3 |
| Priority sectors | Technology, AI, Software, Semiconductors, Banking, Financial Services |
| Output language | Italian |
| Maximum candidates | 5 (typically 3 from Fundamental Analyst) |
| Scoring | 5 dimensions × max 10 = max 50 |

---

## File Structure

```
equity-researcher-analyst-a2a/
├── .env                          ← ANTHROPIC_API_KEY
├── pyproject.toml                ← uv dependencies
├── orchestrator/
│   └── main.py                   ← LangGraph pipeline + A2A client
├── agents/
│   ├── data-collector/
│   │   ├── agent.py              ← OpenAI Agents SDK, port 8001
│   │   └── .well-known/agent.json
│   ├── news-sentiment/
│   │   ├── agent.py              ← Smolagents CodeAgent, port 8002
│   │   └── .well-known/agent.json
│   ├── fundamental-analyst/
│   │   ├── agent.py              ← BeeAI ReActAgent, port 8003
│   │   └── .well-known/agent.json
│   ├── risk-assessor/
│   │   ├── agent.py              ← BeeAI ReActAgent + guardrail, port 8004
│   │   └── .well-known/agent.json
│   └── report-writer/
│       ├── agent.py              ← Anthropic SDK direct, port 8005
│       └── .well-known/agent.json
└── shared/
    ├── a2a_models.py             ← Pydantic models (A2ATask, JsonRpc*, etc.)
    └── tools/
        ├── yfinance_tool.py      ← yfinance wrapper with timeout
        └── rss_feed.py           ← financial RSS feed reader
```

---

## Roadmap

| Version | Description |
|---------|-------------|
| v2 orchestrator | LangGraph is already active; adding parallel branches or retry loops only requires modifying `_build_graph()` |
| Model phase 2 | Gemini Flash for NewsSentiment — add `GOOGLE_API_KEY` and change `model_id` only |
| Model phase 3 | Local Ollama on Apple Silicon (`llama3.1:8b`, `mistral:7b`) via OpenAI-compatible endpoint |
| Future agents | Portfolio Manager, Earnings Calendar, Macro Agent |

---

## Problems This Application Solves

Traditional equity research suffers from three structural problems that Equity Researcher A2A directly addresses.

**The cost of analytical time.** A complete analysis of three or four stocks — gathering news, reading fundamentals, building a thesis, running scenario analysis, writing the report — requires a human analyst between two and four hours of focused work. The system reduces this to minutes, freeing the professional for higher-judgment activities: validating theses, comparing against their own market view, and making the final decision.

**The fragmentation of information sources.** The data relevant to an investment decision is scattered: prices and fundamentals on financial platforms, news on RSS feeds and specialist sites, analyst consensus on proprietary terminals. Tracking all these sources in parallel is impossible without dedicated tooling, and the risk is making decisions on incomplete information. The system automatically aggregates all these sources into a single coherent cycle, ensuring the picture is complete.

**The lack of structure and traceability in informal analysis.** Many stock assessments remain as disorganised notes, messaging chats, or improvised spreadsheets — hard to review, impossible to compare over time, and lacking any format that makes it possible to understand why a thesis proved right or wrong. Equity Researcher A2A produces a structured and repeatable output: every claim cites its source (news code N1, N2…), every candidate has a score across five dimensions, and every thesis explicitly includes a falsification trigger — the condition that, if it occurred, would invalidate the thesis itself. This makes analysis reviewable, comparable, and useful for building a more rigorous decision-making process over time.
