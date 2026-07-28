# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run a single agent (example: data-collector on port 8001)
uv run python agents/data-collector/agent.py

# Run all 7 agents (each in a separate terminal)
uv run python agents/data-collector/agent.py      # :8001
uv run python agents/news-sentiment/agent.py      # :8002
uv run python agents/fundamental-analyst/agent.py # :8003
uv run python agents/risk-assessor/agent.py       # :8004
uv run python agents/report-writer/agent.py       # :8005
uv run python agents/compliance-agent/agent.py    # :8006 — requires VOYAGE_API_KEY + DATABASE_URL (pgvector)
uv run python agents/portfolio-manager/agent.py   # :8007

# One-time (and after editing policies/docs/*.md): index policy documents for the Compliance Agent
uv run python scripts/ingest_policies.py

# One-time (after first configuring DATABASE_URL, and after pulling new migrations):
# apply versioned schema migrations
uv run alembic upgrade head

# Interactive natural-language coordinator (requires all agents running) — multi-turn REPL
uv run python orchestrator/main.py

# Web UI — chat + live step-by-step pipeline trace on http://localhost:8000
uv run uvicorn gateway.app:app --port 8000

# Resume a specific/new conversation session
uv run python orchestrator/main.py --session <session_id>
uv run python orchestrator/main.py --new-session

# Non-interactive shortcut: analyze exactly these tickers and exit (bypasses the coordinator)
uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI

# Save output to file (only with --tickers/--resume)
uv run python orchestrator/main.py --tickers AAPL MSFT --output report.json

# Resume a run interrupted mid-pipeline (requires DATABASE_URL, see Persistence below)
uv run python orchestrator/main.py --resume <run_id>

# Health check a running agent
curl http://localhost:8001/health

# Agent card discovery
curl http://localhost:8001/.well-known/agent.json
```

## Architecture

This is an **A2A (Agent-to-Agent)** multi-agent equity research system. The CrewAI pipeline was decomposed into 6 independent FastAPI services that communicate via **JSON-RPC 2.0 over HTTP**. All agents but ReportWriter run on a single hand-rolled ReAct loop (`shared/react_agent.py`) built directly on the Anthropic SDK — the original per-agent framework split (OpenAI Agents SDK / Smolagents / BeeAI) was retired in favor of one shared, auditable tool-use loop; ReportWriter alone has no tool-use step and calls the Anthropic SDK directly for its two-step generate+QA flow.

A **Portfolio Manager** agent (port 8007) sits after Gate 2: it aggregates the compliant candidates into a portfolio allocation (percent weights + rationale, implicit cash), which **Gate 3** (`shared/portfolio.py`, deterministic) then checks against the aggregate limits in `policies/portfolio_limits.yaml` — per-position and per-sector concentration, max positions, weighted drawdown-from-52w-high proxy, and pairwise correlation of daily returns (yfinance history, best-effort: a missing series degrades to a warning, never blocks). A breach feeds the violation text back to the PM through the standard validation-retry loop (re-allocation), not a pipeline failure. The approved weights are merged into the final report (`allocazione`/`nota_allocazione`) deterministically in Python, same rule as the gate exclusions. The PM has no LLM-QA step: allocation correctness is fully checkable deterministically, so a second LLM pass adds cost without coverage.

### Coordinator (natural-language front end)

`orchestrator/coordinator.py::interpret_prompt` turns a free-text request ("confrontami NVDA e AMD" / "opportunità nel settore bancario europeo ora") into a `CoordinatorIntent` (`mode`, `tickers`, `priority_sectors`, `excluded_sectors`, `focus`) — reuses `shared/react_agent.py::run_react` with `tools=[]` (pure reasoning/extraction, forced structured output via the same `submit_final_answer` mechanism every agent uses). Recent conversation history (see Conversation memory below) is included in the prompt so it can resolve implicit references ("approfondisci NVDA", "rispetto a ieri").

### Pipeline — two topologies chosen dynamically by `state["mode"]`

```
mode="specific" (tickers named by the user):
  Gate 1 (restricted list, run_pipeline) → data_news_parallel :8001+:8002 (concurrent, Gate 1 ESG check inline)
    → fundamental_analyst :8003 → risk_assessor :8004 → compliance_agent :8006 (Gate 2, RAG) → portfolio_manager :8007 (+ Gate 3) → report_writer :8005

mode="discovery" (no tickers named — open-ended, e.g. "opportunità nel settore bancario europeo ora"):
  news_sentiment_discovery :8002 → Gate 1 (restricted list) → data_collector_from_candidates :8001 (Gate 1 ESG check inline)
    → fundamental_analyst :8003 → risk_assessor :8004 → compliance_agent :8006 (Gate 2, RAG) → portfolio_manager :8007 (+ Gate 3) → report_writer :8005
```

`fundamental_analyst`/`risk_assessor`/`report_writer` are unchanged in both modes. The fork exists because DataCollector needs to know *which* tickers to fetch: in "specific" mode the user already named them, so DataCollector and NewsSentiment run concurrently (`node_data_news_parallel`, `asyncio.gather`); in "discovery" mode nothing is known yet, so NewsSentiment must run first and propose `candidate_tickers` (see NewsSentiment below) before DataCollector can run — necessarily sequential, and in reverse order from "specific" mode.

**Why parallel execution is one graph node, not two with a fan-in edge**: LangGraph's `set_conditional_entry_point`/multi-edge fan-in does run concurrent branches, but when each branch has its own independent validation-retry self-loop (as `_make_router` implements), the fan-in node fires prematurely as soon as either branch advances — verified empirically with a minimal test graph. `node_data_news_parallel` sidesteps this by being a single graph node that internally `asyncio.gather`s both A2A calls, each with its own retry logic; `_make_dual_router` only advances past it once *both* sub-stages (tracked under the "data_collector"/"news_sentiment" keys in `state["retries"]`) are clean. `_merge_partial_results` merges the two sub-results without letting one clobber the other's `retries`/`validation_feedback` update.

The orchestrator (`orchestrator/main.py`) uses **LangGraph** (`StateGraph`). Each pipeline step is a node; `PipelineState` (TypedDict) carries accumulated data across nodes, including `mode`, `tickers`, `candidate_tickers`, `focus`, `gate1_excluded`, `compliance_flagged`, `compliance_checked`. The graph is compiled once at module load (`_build_graph()`) and invoked with `_graph.ainvoke(initial_state)`. `_entry_router` picks the starting node(s) from `state["mode"]` plus which stage data is already present (also drives `--resume`).

### Compliance gates

Three gates:

- **Gate 1 — eligibility filter** (`shared/eligibility.py`, deterministic, zero LLM cost, no new agent/port): four checks, split by when the data they need becomes available. `check_restricted_list()` matches ticker symbols against `policies/restricted_list.yaml` — runs once in `run_pipeline` for "specific" mode (before the graph starts) and inside `node_data_collector_from_candidates` for "discovery" mode (on `candidate_tickers`), since neither needs fundamentals data. The other three run inside `_collect_fundamentals` (shared by both topologies) right after DataCollector returns fundamentals, in order: `check_data_quality()` drops tickers with no usable price (null/≤0 — bogus/misspelled/delisted symbols); `check_market_perimeter()` enforces the US/EU-27-only universe by issuer **domicile** (yfinance `country`, `market` as fallback) — so a US-listed ADR of a UK issuer (e.g. AZN on NYSE, `country="United Kingdom"`) is excluded while an EU issuer trading as a US ADR (e.g. SAP/STM, `country="Germany"/"Netherlands"`) stays in; `check_esg_exclusions()` matches each candidate's `sector`/`industry` against `esg_excluded_sectors` in the policy file. All blocked entries accumulate in `state["gate1_excluded"]` (same shape as `shared/models.py::CandidatoEscluso`) and are never re-fetched/re-analyzed downstream.
- **Gate 2 — Compliance Agent** (`agents/compliance-agent`, port 8006): runs after `risk_assessor`, before `report_writer` (not after the full pipeline including the report, to avoid generating a report on candidates that get blocked). Validates every candidate against internal policy documents retrieved via RAG (`shared/policy_store.py` — Voyage AI embeddings + pgvector). **Scope**: Gate 2 is the *qualitative* regulatory-judgment gate — it covers only what the indexed corpus actually contains (SFDR / MiFID II product governance / MiFID II suitability / Consob 20307 intermediary-conduct) and what the deterministic gates *cannot* express. It deliberately does **not** re-check restricted-list, ESG-sector, or concentration limits — those are owned deterministically by Gate 1 (`shared/eligibility.py`) and Gate 3 (`shared/portfolio.py`), and having the LLM re-derive them from RAG was both redundant and ungrounded (those rules live in YAML, not in the embedded PDFs). The prompt gives an explicit search budget (1–2 targeted `search_policy` queries per candidate, then submit) so the ReAct loop converges instead of hunting indefinitely for policy text the corpus doesn't hold. A "non-compliant" verdict is a legitimate outcome, not an agent error: `node_compliance_agent` partitions `candidates`/`risk_assessment` into compliant (proceed to `report_writer`) and flagged (accumulated in `state["compliance_flagged"]`), it never triggers the `invalid`/retry path for the verdict itself (only for malformed output, same as every other agent's QA).

- **Gate 3 — portfolio limits** (`shared/portfolio.py`, deterministic, zero LLM cost): runs in `node_portfolio_manager` on the allocation the PM proposes. Limits live in `policies/portfolio_limits.yaml`. See the Portfolio Manager paragraph above for the full mechanics (violation → re-allocation retry, correlation best-effort).

Both `gate1_excluded` and `compliance_flagged` (plus the PM's `allocation_esclusi`) are merged into the final report's `candidati_esclusi` deterministically in Python (`node_report_writer`, after receiving the report from ReportWriter) — not via the LLM prompt, so compliance-critical exclusion data can never be dropped or reworded by report generation.

### RAG (Compliance Agent policy retrieval)

`shared/policy_store.py`: `ingest_documents()` chunks (paragraph-level) and embeds (Voyage AI, `voyage-3`, `input_type="document"`) every file in `policies/docs/*.md` into the `policy_chunks` pgvector table (`shared/db.py::get_vector_pool`/`_VECTOR_SCHEMA`); `search_policy()` embeds a query (`input_type="query"`) and returns the nearest chunks by cosine distance, each citable via its `source` document. Re-run `scripts/ingest_policies.py` after editing `policies/docs/*.md` — ingestion replaces any existing chunks for a given source file. The Compliance Agent exposes this as a `search_policy` tool in its ReAct loop.

`get_vector_pool()` is deliberately **not** best-effort like the rest of `shared/db.py`: a missing `DATABASE_URL`/`VOYAGE_API_KEY` or a Postgres without the `vector` extension raises immediately (both at `agents/compliance-agent/agent.py` import time via `_enforce_rag_config()`, and on first retrieval) rather than letting the Compliance Agent silently run with retrieval disabled — skipping a compliance check unnoticed would be dangerous, unlike audit logging or conversation memory degrading to a no-op.

### Schema migrations (`alembic/`)

`shared/db.py`'s runtime bootstrap (`_SCHEMA`/`_VECTOR_SCHEMA`, `CREATE TABLE IF NOT EXISTS`) is unchanged and still the zero-config fallback — nothing breaks if you never touch Alembic. Alembic is the sanctioned path for every schema change from here on: new migrations go in `alembic/versions/`, not into `_SCHEMA`/`_VECTOR_SCHEMA`. The baseline migration (`54d88a740b4b`) doesn't hand-copy that DDL a second time — it imports `_SCHEMA`/`_VECTOR_SCHEMA` from `shared/db.py` and executes them directly, so the table shapes have one source of truth instead of two texts that could silently drift apart. `alembic/env.py` reads `DATABASE_URL` from `.env` — same variable `shared/db.py` uses, nothing to keep in sync separately. If your DB was created by the runtime bootstrap before adopting Alembic, run `alembic stamp head` (tables already exist) instead of `alembic upgrade head`. In `docker-compose.yml`, the `migrate` service applies migrations automatically on every `up`, before `compliance-agent`/`gateway` start (fast/idempotent, unlike the rate-limited `ingest-policies` job, so unlike that one it isn't gated behind a manual profile).

### Backup/restore (Postgres, Docker)

Manual, on-demand logical backup/restore via `pg_dump`/`pg_restore` — no app code, just two one-shot services in `docker-compose.yml` (`backup`/`restore`, `profiles: ["tools"]`, same non-default pattern as `ingest-policies`) using the `pgvector/pgvector:pg16` image directly (it already ships the Postgres client tools). `docker compose --profile tools run --rm backup` dumps the whole DB (schema + `pgcrypto`/`vector` extensions + every table — no selective per-table exclusion, simpler and more robust than keeping a `-t`/`-T` list in sync with the schema) to `./backups/<timestamp>.dump`, then prunes dumps older than 30 days. `./backups/` is a **host bind mount**, not the `pgdata` named volume — deliberately, so dumps survive independently of the container/volume and can be copied off-host; doing that copy (USB, cloud storage, etc.) is a manual step left to the operator, not automated. Restore: `BACKUP_FILE=<filename> docker compose --profile tools run --rm restore` (`--clean --if-exists`, safe to re-run). No scheduling (cron, etc.) is wired up — on-demand only, matching this project's single-instance/personal-use scope; no incremental/WAL-based point-in-time recovery either, a full logical dump is proportionate here.

### Observability (Grafana over `audit_log`)

`shared/audit.py::log_event()` already records every A2A call and external tool fetch to Postgres (`audit_log`: `agent`/`event_type`/`status`/`duration_ms`/`payload`), but nothing aggregates it — the gateway UI only shows live per-run stage/duration, not a historical/cross-run view. Every LLM call now also emits an `event_type="llm_usage"` row (`agent`, `input_tokens`/`output_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`, `estimated_cost_usd` — via `shared/pricing.py::estimate_cost_usd()`, hardcoded approximate $/M-token rates, **not** billing-grade, verify against https://www.anthropic.com/pricing before trusting absolute numbers). Instrumentation lives in exactly 3 places, not scattered per-agent: `shared/react_agent.py::run_react()` (async, calls `log_event` directly after each `messages.create()`), `shared/qa.py::run_llm_qa()` and `agents/report-writer/agent.py::_call_claude()` (both sync, use the new `shared/audit.py::log_event_fire_and_forget()` — schedules the write as a background asyncio task since a sync function can't `await`; silently no-ops outside a running event loop, same best-effort posture as `log_event` itself). `correlation_id`/`agent` are **not** threaded through every one of the ~13 call sites across the 7 agents — that was tried and reverted as unnecessarily invasive (touching every agent file for a value the framework already has). Instead, `shared/a2a_server.py::handle_task()` (which already receives both `task.id` and `agent_name` before invoking `run_agent()`) scopes them for the task's duration via `shared/audit.py::audit_context()` (a `contextvars`-based context manager); `run_react()`/`run_llm_qa()`/`_call_claude()` read them back via `current_correlation_id()`/`current_agent()` when not passed explicitly. The one exception is `orchestrator/coordinator.py::interpret_prompt()`, which never goes through `handle_task` (called directly by the gateway/REPL, not as an A2A task) — it still passes `correlation_id`/`agent` explicitly to `run_react()`.

`docker compose --profile observability up -d grafana` starts Grafana (`grafana/grafana-oss`, fully open source — not `grafana/grafana`) with the Postgres datasource and the "Pipeline Overview" dashboard **auto-provisioned** (`observability/grafana/provisioning/`, `observability/grafana/dashboards/pipeline_overview.json`) — no manual setup after first login (`admin`/`admin` by default, override via `GRAFANA_ADMIN_PASSWORD`). Purely observational — omitting the profile breaks nothing, unlike `compliance-agent`/`postgres`, so unlike those it's correctly gated behind a separate profile. Two indices (`idx_audit_agent_created`, `idx_audit_event_type_created`, added in the `ac8ba0fd0a2f` migration) back the dashboard's aggregation queries.

### Web gateway (`gateway/`)

`gateway/app.py` (port 8000) is the graphical front end: a FastAPI service that runs the orchestrator **in-process** (imports `run_pipeline`/`interpret_prompt` directly — the browser never talks to the agents). `POST /api/chat` interprets the prompt, starts the pipeline as a background task and returns `{run_id, session_id, intent}`; `GET /api/stream/{run_id}` streams live pipeline events over SSE; `GET /api/report/{run_id}` serves the final HTML report for the inline iframe; `GET /api/health` fans out to the 7 agents' `/health`. The single-page frontend (`gateway/static/index.html`, vanilla JS) shows the chat on the left and a step-by-step run timeline on the right (stages with durations, gate 1/2/3 events with excluded tickers/violations, retries with feedback).

Events come from `shared/events.py` — an in-memory per-run asyncio.Queue bus. `emit()` is a no-op when nobody subscribed, so the REPL/`--tickers` paths are unaffected; the orchestrator emits from `_with_persistence` (stage_start/stage_end for every node), `_record_invalid` (stage_retry), and the gate blocks. `run_pipeline` accepts `run_id=` (so the gateway can subscribe before the graph starts) and `open_browser=` (gateway passes False).

### Conversation memory (cross-session + multi-turn)

`orchestrator/main.py`'s default mode (no `--tickers`/`--resume`) is an interactive REPL: each free-text line goes through the coordinator, then the pipeline, then the turn is persisted. `shared/db.py::get_or_create_session()`/`append_turn()`/`load_recent_turns()` back this with two Postgres tables (`conversation_sessions`, `conversation_turns`) — same best-effort, degrade-to-noop-without-`DATABASE_URL` posture as the rest of `shared/db.py`. Default behavior with no `--session`/`--new-session` flag is to resume the most recently active session — "continue where you left off," the natural default for a single local user.

### Persistence & resilience (pipeline-level)

- **Resumable runs** — every node is wrapped (`_with_persistence`) to snapshot `PipelineState` into the `pipeline_runs` Postgres table (`shared/db.py`) after each stage. `set_conditional_entry_point(_entry_router)` picks the graph's starting node from which stage data is already present in the loaded state, so `--resume <run_id>` (loads state via `shared.db.load_run_state`) jumps straight to the first incomplete stage instead of restarting from `data_collector`. Requires `DATABASE_URL`; without it, resume isn't available (same optional-dependency posture as the audit trail).

A per-agent circuit breaker was tried and removed as overengineering: the orchestrator calls each agent sequentially within a single run (one user, one run at a time) — there's never a second concurrent caller to protect an agent from, which is the whole premise of a circuit breaker. `send_task_with_retry`'s rate-limit retry already handles the transient case; resumable runs already handle a hard failure better (resume from the last clean stage instead of failing fast for 60s).

### A2A Protocol

`shared/a2a_models.py` defines the full wire format:
- **`JsonRpcRequest`** — wraps every call: `method="tasks/send"`, params contain an `A2ATask`
- **`A2ATask`** — `id` + `message` (list of `TextPart` and/or `DataPart`)
- **`A2ATaskResult`** — `id` + `status` (`completed|failed|working|invalid`) + `message`. `invalid` (vs `failed`) marks a content/QA rejection — the orchestrator's retry loop injects `result.message.text()` back into the agent's next prompt as `validation_feedback`, up to `MAX_VALIDATION_RETRIES`.
- Structured data travels as `DataPart(data={key: value})` inside the message parts
- Use `A2ATaskResult.ok()` / `A2ATaskResult.fail()` / `A2ATaskResult.invalid()` factory methods in agents

`shared/a2a_server.py::handle_task` is the shared FastAPI route handler used by all 5 agents (parses the JSON-RPC envelope, verifies/signs HMAC, audit-logs request+response, calls `run_agent`) — agent-specific code only implements `run_agent`.

### Agent anatomy

Every agent follows the same pattern:
1. `run_agent(task: A2ATask) -> A2ATaskResult` — core logic, called by the FastAPI handler
2. `POST /tasks` — receives `JsonRpcRequest`, delegates to `shared.a2a_server.handle_task`
3. `GET /.well-known/agent.json` — serves the Agent Card for discovery
4. `GET /health` — liveness check

### Models in use

| Agent | Model |
|---|---|
| DataCollector | `claude-haiku-4-5-20251001` |
| NewsSentiment | `claude-haiku-4-5-20251001` |
| FundamentalAnalyst | `claude-haiku-4-5-20251001` |
| RiskAssessor | `claude-haiku-4-5-20251001` |
| ComplianceAgent | `claude-sonnet-5` (report) + `claude-sonnet-5` (QA) |
| PortfolioManager | `claude-sonnet-5` (no separate QA — Gate 3 is deterministic) |
| ReportWriter | `claude-sonnet-5` (report) + `claude-sonnet-5` (QA) |

### Shared tools

- `shared/tools/yfinance_tool.py` — `get_stock_fundamentals(ticker)` / `get_stock_fundamentals_text(ticker)`. Wraps yfinance with a 15s per-ticker timeout via `ThreadPoolExecutor`.
- `shared/tools/rss_feed.py` — `fetch_rss_news()` reads 6 RSS feeds (Reuters, Yahoo Finance, MarketWatch, Investing.com × 2) with retry logic.
- `shared/policy_store.py` — RAG retrieval over internal compliance policies (Voyage AI + pgvector), used by ComplianceAgent's `search_policy` tool. See RAG section above.
- `shared/eligibility.py` — Gate 1 deterministic checks (restricted list, ESG exclusions). See Compliance gates section above.

### Enterprise / cross-cutting modules (`shared/`)

- **`auth.py`** — HMAC-SHA256 signing/verification of every A2A request and response, single shared secret (`A2A_SHARED_SECRET`) known to the orchestrator and all 5 agents, with replay protection (300s skew). If the secret is unset, auth is skipped entirely — a dev-friendly default. `enforce_secret_policy()` fails fast at process startup (called right after `load_dotenv()` in every agent and the orchestrator) when `A2A_AUTH_REQUIRED=true` but no secret is configured, so "running without auth" must be an explicit opt-in rather than a silent gap.
- **`audit.py`** — `log_event()` best-effort async insert into the Postgres `audit_log` table (correlation_id = task id, direction, status, duration_ms). Never raises; degrades to a printed warning if `DATABASE_URL`/DB is unavailable. Wired into `a2a_server.py` (every A2A request/response) and into individual agents for tool calls (e.g. yfinance/RSS fetches).
- **`db.py`** — shared `asyncpg` connection pool (one per process), lazily created and schema-bootstrapped on first use (`audit_log`, `pipeline_runs`, `conversation_sessions`, `conversation_turns` tables). Returns `None` if `DATABASE_URL` is unset — callers must treat that as "audit/resume/memory unavailable," never as a hard failure. Also provides `check_connection()` (used by `/health`), `save_run_state()`/`load_run_state()` (resumable runs, see Persistence & resilience below), and `get_or_create_session()`/`append_turn()`/`load_recent_turns()` (conversation memory, see Coordinator above).
- **`log.py`** — structured JSON logging to stdout (`get_logger()`), level via `LOG_LEVEL`. Complements `audit.py`'s durable Postgres trail with grep/tail-able operational logs; both carry `correlation_id` as a standard field.
- **`sanitize.py`** — `sanitize_external_text()` strips HTML/control chars and truncates length; applied to RSS content and free-text yfinance fields before they reach a prompt.
- **`validators.py`** — deterministic (no LLM cost) per-stage validation: prompt-injection phrase detection, explicit buy/sell directive detection, crypto-keyword detection (word-boundary regex), news-ID format checks, scoring arithmetic/range checks. `validate_stage()` runs after each pipeline stage in the orchestrator; violations feed `_record_invalid()` → retried with feedback, same mechanism as agent-side `invalid` results.
- **`schemas.py`** — lenient Pydantic models (`extra="allow"`) for intermediate per-stage LLM output (fundamentals, news/themes, candidates, risk assessment) — catch missing/malformed structure without enforcing a rigid contract. Distinct from `shared/models.py`, which is the strict schema of ReportWriter's final output.
- **`qa.py`** — shared LLM-QA mechanism (`run_llm_qa`, `parse_qa_verdict`): calls Claude with a caller-supplied system prompt, parses a `QA: APPROVATO|DA_CORREGGERE` verdict line plus an optional `=== CORREZIONI ===` JSON block. Used by all 5 agents, including ReportWriter (its QA step calls `run_llm_qa` directly and returns `A2ATaskResult.invalid()` on `DA_CORREGGERE`, feeding the same orchestrator retry loop as every other stage).
- **`react_agent.py`** — the shared ReAct tool-use loop described above.

### Domain constraints (hardcoded in agent prompts)

- Universe: US and EU equities only (UK/LSE excluded)
- Perimeter guardrail (not a sector preference): crypto/DeFi/Web3 excluded as outside the equity market (`shared/validators.py::no_crypto` + prompt-level). No "soft" sector exclusions — every US/EU listed-equity sector is allowed. ESG hard-block (thermal coal/tobacco/controversial weapons) lives separately in `policies/restricted_list.yaml` (Gate 1).
- Priority sectors: Technology, AI, Software, Semiconductors, Banking, Financial Services
- Final report language: **Italian**

### Report Writer internals

Two-step process in `run_agent`:
1. Generate full report with `=== SINTESI ESECUTIVA ===` and `=== JSON ===` sections
2. Run QA pass via `shared/qa.py::run_llm_qa` on the same output; `DA_CORREGGERE` returns `A2ATaskResult.invalid()`, which the orchestrator retries with feedback (same as every other stage) instead of just recording the verdict for later.

The JSON schema embedded in `_REPORT_SCHEMA` defines the canonical output structure (candidates with 5-dimension scoring summing to max 50, analyst consensus, scenarios, risks, falsification trigger).

## Environment

See `.env.example` for the full list. Requires `ANTHROPIC_API_KEY` in `.env` at project root. `A2A_SHARED_SECRET`/`A2A_AUTH_REQUIRED` and `DATABASE_URL` are optional for the other 5 agents/orchestrator — without them, A2A signing, audit persistence, resumable runs, and conversation memory degrade to no-ops (see `shared/auth.py`, `shared/db.py` above). `VOYAGE_API_KEY` **and** `DATABASE_URL` (with the `vector` extension available) are **required** for the Compliance Agent specifically — it fails to start without them (see RAG section above). No other API keys needed for the default configuration (yfinance and RSS feeds require no keys).

## Architecture Decision Records

`docs/adr/` records the reasoning behind the major architectural choices (A2A decomposition, the LangGraph dynamic orchestrator vs. a fixed-sequence crew, the three-gate compliance model, containerization, observability, and the overengineering reversals — circuit breaker, per-IP rate limiting, duplicated schema DDL). Consult it before revisiting a decision that looks questionable in isolation; several entries document something that was tried and deliberately reverted, with the reasoning kept so it isn't re-tried for the same reason.

## Roadmap

- **Model phase 2**: Gemini Flash for NewsSentiment (add `GOOGLE_API_KEY` to `.env`, change model_id only)
- **Model phase 3**: Ollama local models via OpenAI-compatible endpoint (`http://localhost:11434/v1`)
