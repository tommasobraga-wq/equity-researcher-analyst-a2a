# Equity Researcher A2A — Contesto Progetto
> Documento generato da conversazione con Claude (claude.ai) — 25 aprile 2026
> Da condividere con Claude Code CLI per continuare lo sviluppo

---

## Progetto Esistente

**Posizione:** `~/Developer/equity-research-analyst`
**Stack attuale:** CrewAI, uv, Python 3.11+, Anthropic API
**Tool esistenti da riusare:**
- `RssFeedTool` — RSS feeds (Reuters/Yahoo/MarketWatch/Investing.com) via feedparser
- `YFinanceTool` — dati fondamentali via yfinance (no API key necessaria)

### Pipeline CrewAI attuale (sequenziale)
1. `news_researcher` (Haiku) — RSS feeds → JSON array notizie con ID (N1, N2…)
2. `theme_analyst` (Sonnet) — clustering in 3-4 macro/sector themes
3. `stock_screener` (Sonnet) — fino a 5 candidati equity (US/EU) con fondamentali yfinance
4. `risk_assessor` (Sonnet) — scenari base/bull/bear, scoring 1-10 per dimensione (max 50)
5. `report_writer` (Sonnet) — executive summary italiano + JSON completo (max_tokens=16000)
6. `qa_reviewer` (Sonnet) — QA verdict breve (feedback loop)

**Scope constraints hardcoded:**
- Universe: US e EU equities only (UK/LSE esclusa)
- Esclusi: energy, utilities, real estate, REITs, consumer staples, industrials, airlines, crypto, DeFi, Web3
- Output language: Italiano

---

## Nuova Architettura — Equity Researcher v2

### Obiettivo
Scomporre la crew CrewAI in agenti indipendenti che comunicano via protocollo **A2A (Agent-to-Agent)**.
Ogni agente usa un framework diverso per scopo didattico/architetturale.

### Protocolli
- **A2A** — comunicazione inter-agente (JSON-RPC 2.0 su HTTP, Agent Card in `/.well-known/agent.json`)
- **MCP** — connessione ai tool esterni (dati finanziari, web search)

### Agenti & Framework

| # | Agente | Framework | Pattern | Mappa da CrewAI |
|---|--------|-----------|---------|-----------------|
| 0 | **Orchestrator** | Python async puro (v1) → LangGraph (v2+) | Pipeline sequenziale + feedback loop | — |
| 1 | **Data Collector** | OpenAI Agents SDK | Tool use + MCP | `stock_screener` + `YFinanceTool` |
| 2 | **News & Sentiment** | Smolagents (HuggingFace) | CodeAgent + ReAct + web search | `news_researcher` + `RssFeedTool` |
| 3 | **Fundamental Analyst** | BeeAI | CoT + stato persistente | `theme_analyst` |
| 4 | **Risk Assessor** | BeeAI | ReAct + Conditional Constraints | `risk_assessor` |
| 5 | **Report Writer** | Anthropic API diretta | Structured output | `report_writer` + `qa_reviewer` |

### Pattern implementati
- **ReAct** → Orchestrator, Data Collector, Risk Assessor
- **Chain of Thought** → Fundamental Analyst, News & Sentiment
- **Feedback Loop** → Risk Assessor → Fundamental Analyst (rimando se dati incoerenti)
- **Conditional Constraints** → BeeAI Risk Assessor (guardrail interni: non produrre output se dati volatilità incompleti)
- **Structured Output** → Report Writer (JSON + markdown italiano)

### Struttura cartelle target

```
~/Developer/
├── equity-research-analyst/     ← progetto CrewAI esistente (NON toccare)
└── beeai-a2a/                   ← nuovo progetto (già creato, .venv presente)
    ├── .env                     ← ANTHROPIC_API_KEY
    ├── shared/
    │   ├── a2a_models.py        ← dataclass A2A (AgentCard, Task, TaskResult)
    │   ├── tools/
    │   │   ├── rss_feed.py      ← copiato da equity-research-analyst
    │   │   └── yfinance_tool.py ← copiato da equity-research-analyst
    ├── orchestrator/
    │   ├── main.py              ← entry point, async pipeline
    │   └── .well-known/
    │       └── agent.json       ← Agent Card A2A
    ├── agents/
    │   ├── data-collector/      ← FastAPI su porta 8001, OpenAI Agents SDK
    │   │   ├── agent.py
    │   │   └── .well-known/agent.json
    │   ├── news-sentiment/      ← FastAPI su porta 8002, Smolagents
    │   │   ├── agent.py
    │   │   └── .well-known/agent.json
    │   ├── fundamental-analyst/ ← FastAPI su porta 8003, BeeAI
    │   │   ├── agent.py
    │   │   └── .well-known/agent.json
    │   ├── risk-assessor/       ← FastAPI su porta 8004, BeeAI
    │   │   ├── agent.py
    │   │   └── .well-known/agent.json
    │   └── report-writer/       ← FastAPI su porta 8005, Anthropic API diretta
    │       ├── agent.py
    │       └── .well-known/agent.json
    └── pyproject.toml
```

### Roadmap modelli (3 fasi)

**Fase 1 — Opzione A:** tutti su Claude API
- Agenti core (Orchestrator, Fundamental Analyst, Report Writer): `claude-sonnet-4-6`
- Agenti meccanici (Data Collector, News & Sentiment, Risk Assessor): `claude-haiku-4-5-20251001`
- Costo stimato sviluppo attivo: ~$5–12/mese

**Fase 2 — Opzione B:** + Gemini Flash per News & Sentiment
- `GOOGLE_API_KEY` aggiunta al `.env`
- Solo News & Sentiment cambia modello, zero impatto sugli altri agenti

**Fase 3 — Opzione C:** + Ollama locale (MacBook Apple Silicon)
- Candidati: `llama3.1:8b` per Data Collector, `mistral:7b` per News & Sentiment, `qwen2.5:7b` per Risk Assessor
- Endpoint OpenAI-compatible (`http://localhost:11434/v1`) — nessuna modifica ai framework

### Evoluzione orchestratore (roadmap agenti futuri)
- **v1** — pipeline lineare: replica e supera CrewAI
- **v2** — agenti aggiuntivi: Portfolio Manager, Earnings Calendar, Macro Agent
- **v3** — migrazione orchestratore a LangGraph quando il grafo lo giustifica

---

## IDE & Ambiente

- **IDE:** Google Antigravity (fork VS Code, supporta Claude nativo)
- **Hardware:** MacBook Pro Apple Silicon (ottimo per Ollama Fase 3)
- **Python:** 3.11+ con uv come package manager
- **Cartella progetto nuovo:** `~/Developer/beeai-a2a/` (.venv già presente)

---

## Prossimi step da completare

1. Leggere `src/equity_research_analyst/crew.py` e i YAML config per mappatura completa
2. Creare `shared/a2a_models.py` con le dataclass del protocollo A2A
3. Copiare e adattare `RssFeedTool` e `YFinanceTool` in `shared/tools/`
4. Implementare `agents/data-collector/agent.py` (OpenAI Agents SDK + FastAPI)
5. Implementare `orchestrator/main.py` con chiamata A2A al Data Collector
6. Verificare handshake A2A tra Orchestrator e Data Collector
7. Aggiungere gli agenti rimanenti uno alla volta

---

## Note tecniche A2A

Ogni agente espone:
- `POST /tasks` — ricezione task A2A
- `GET /.well-known/agent.json` — discovery Agent Card

Agent Card esempio:
```json
{
  "name": "DataCollector",
  "version": "1.0.0",
  "description": "Fetches financial data for equity tickers",
  "url": "http://localhost:8001",
  "capabilities": ["equity_data", "fundamentals", "price_history"],
  "input_schema": {
    "ticker": "string",
    "period": "string"
  }
}
```

Comunicazione via JSON-RPC 2.0:
```json
{
  "jsonrpc": "2.0",
  "method": "tasks/send",
  "params": {
    "id": "task-001",
    "message": {
      "role": "user",
      "parts": [{"type": "text", "text": "Fetch data for UCG.MI"}]
    }
  },
  "id": 1
}
```
