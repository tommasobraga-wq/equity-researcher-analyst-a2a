# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run a single agent (example: data-collector on port 8001)
uv run python agents/data-collector/agent.py

# Run all 5 agents (each in a separate terminal)
uv run python agents/data-collector/agent.py      # :8001
uv run python agents/news-sentiment/agent.py      # :8002
uv run python agents/fundamental-analyst/agent.py # :8003
uv run python agents/risk-assessor/agent.py       # :8004
uv run python agents/report-writer/agent.py       # :8005

# Run the full pipeline (requires all agents running)
uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI

# Save output to file
uv run python orchestrator/main.py --tickers AAPL MSFT --output report.json

# Health check a running agent
curl http://localhost:8001/health

# Agent card discovery
curl http://localhost:8001/.well-known/agent.json
```

## Architecture

This is an **A2A (Agent-to-Agent)** multi-agent equity research system. The CrewAI pipeline was decomposed into 5 independent FastAPI services that communicate via **JSON-RPC 2.0 over HTTP**. Each agent uses a different AI framework — intentionally, for pedagogical/architectural comparison purposes.

### Pipeline (sequential, orchestrated)

```
Orchestrator
  → [1] DataCollector     :8001  OpenAI Agents SDK   fetch fundamentals from yfinance
  → [2] NewsSentiment     :8002  Smolagents          RSS feeds → JSON news + themes
  → [3] FundamentalAnalyst:8003  BeeAI ReActAgent    news+themes+fundamentals → candidates
  → [4] RiskAssessor      :8004  BeeAI ReActAgent    candidates → scoring + scenarios
  → [5] ReportWriter      :8005  Anthropic API direct final Italian report + QA pass
```

The orchestrator (`orchestrator/main.py`) uses **LangGraph** (`StateGraph`). Each pipeline step is a node; `PipelineState` (TypedDict) carries accumulated data across nodes. The graph is compiled once at module load (`_build_graph()`) and invoked with `_graph.ainvoke(initial_state)`. Adding conditional edges, parallel branches, or retry loops only requires modifying `_build_graph()` — node logic stays untouched.

### A2A Protocol

`shared/a2a_models.py` defines the full wire format:
- **`JsonRpcRequest`** — wraps every call: `method="tasks/send"`, params contain an `A2ATask`
- **`A2ATask`** — `id` + `message` (list of `TextPart` and/or `DataPart`)
- **`A2ATaskResult`** — `id` + `status` (`completed|failed|working`) + `message`
- Structured data travels as `DataPart(data={key: value})` inside the message parts
- Use `A2ATaskResult.ok()` / `A2ATaskResult.fail()` factory methods in agents

### Agent anatomy

Every agent follows the same pattern:
1. `run_agent(task: A2ATask) -> A2ATaskResult` — core logic, called by the FastAPI handler
2. `POST /tasks` — receives `JsonRpcRequest`, validates method, delegates to `run_agent`
3. `GET /.well-known/agent.json` — serves the Agent Card for discovery
4. `GET /health` — liveness check

### Models in use

| Agent | Framework | Model |
|---|---|---|
| DataCollector | OpenAI Agents SDK (LitellmModel) | `claude-haiku-4-5-20251001` |
| NewsSentiment | Smolagents (LiteLLMModel) | `claude-haiku-4-5-20251001` |
| FundamentalAnalyst | BeeAI (AnthropicChatModel) | `claude-haiku-4-5-20251001` |
| RiskAssessor | BeeAI (AnthropicChatModel) | `claude-haiku-4-5-20251001` |
| ReportWriter | Anthropic SDK direct | `claude-sonnet-4-6` (report) + `claude-sonnet-4-6` (QA) |

### Shared tools

- `shared/tools/yfinance_tool.py` — `get_stock_fundamentals(ticker)` / `get_stock_fundamentals_text(ticker)`. Wraps yfinance with a 15s per-ticker timeout via `ThreadPoolExecutor`.
- `shared/tools/rss_feed.py` — `fetch_rss_news()` reads 6 RSS feeds (Reuters, Yahoo Finance, MarketWatch, Investing.com × 2) with retry logic.

### BeeAI model constraint

BeeAI's `ReActAgent` uses **assistant message prefill** internally. Sonnet 4.6 does not support prefill (`invalid_request_error`), so FundamentalAnalyst and RiskAssessor must stay on `claude-haiku-4-5-20251001` until BeeAI adds a non-prefill runner for Claude 4.x.

### Domain constraints (hardcoded in agent prompts)

- Universe: US and EU equities only (UK/LSE excluded)
- Excluded sectors: energy, utilities, real estate, REITs, consumer staples, industrials, airlines, crypto/DeFi/Web3
- Priority sectors: Technology, AI, Software, Semiconductors, Banking, Financial Services
- Final report language: **Italian**

### Report Writer internals

Two-step process in `run_agent`:
1. Generate full report with `=== SINTESI ESECUTIVA ===` and `=== JSON ===` sections
2. Run QA pass on the same output; QA model responds with `QA: [APPROVATO|CORRETTO]`

The JSON schema embedded in `_REPORT_SCHEMA` defines the canonical output structure (candidates with 5-dimension scoring summing to max 50, analyst consensus, scenarios, risks, falsification trigger).

## Environment

Requires `ANTHROPIC_API_KEY` in `.env` at project root. No other API keys needed for the default configuration (yfinance and RSS feeds require no keys).

## Roadmap

- **v2 orchestrator**: migrate to LangGraph when the agent graph justifies it
- **Model phase 2**: Gemini Flash for NewsSentiment (add `GOOGLE_API_KEY` to `.env`, change model_id only)
- **Model phase 3**: Ollama local models via OpenAI-compatible endpoint (`http://localhost:11434/v1`)
