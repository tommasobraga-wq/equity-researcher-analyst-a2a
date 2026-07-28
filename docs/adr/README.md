# Architecture Decision Records

Registro delle decisioni architetturali di `equity-researcher-analyst-a2a`, nello stile [MADR](https://adr.github.io/madr/) semplificato: una decisione per file, numerata in ordine, mai modificata retroattivamente — una decisione superata si documenta con un nuovo ADR che la sostituisce (campo "Superseded by" nel vecchio, "Supersedes" nel nuovo), non con un edit del file esistente.

Questo registro fotografa lo stato testato e committato al 28 luglio 2026 (commit `7638e62`). Non copre ipotesi di evoluzione futura ancora in discussione (vedi conversazione corrente per il ragionamento in corso su scope/early-exit della pipeline).

| # | Titolo | Stato |
|---|--------|-------|
| [0001](0001-a2a-decomposition.md) | Decomposizione da crew monolitica a servizi A2A indipendenti | Accepted |
| [0002](0002-shared-react-loop.md) | Loop ReAct condiviso invece di framework per-agente | Accepted |
| [0003](0003-langgraph-dynamic-orchestrator.md) | Orchestratore a grafo (LangGraph) con topologia dinamica | Accepted |
| [0004](0004-three-gate-compliance.md) | Modello a tre gate: deterministico, RAG, deterministico | Accepted |
| [0005](0005-deterministic-validation.md) | Validazione deterministica pre-QA invece di solo giudizio LLM | Accepted |
| [0006](0006-hmac-auth-audit-trail.md) | Firma HMAC opt-in + audit trail su Postgres | Accepted |
| [0007](0007-persistence-resilience.md) | Resumable runs e memoria conversazionale; circuit breaker scartato | Accepted |
| [0008](0008-containerization.md) | Containerizzazione via Docker Compose con profili | Accepted |
| [0009](0009-alembic-migrations.md) | Migrazioni Alembic sopra il bootstrap runtime esistente | Accepted |
| [0010](0010-gateway-rate-limiting.md) | Rate limiting del gateway a contatore globale, non per-IP | Accepted |
| [0011](0011-backup-restore.md) | Backup/restore Postgres manuale on-demand | Accepted |
| [0012](0012-observability.md) | Osservabilità: Grafana + costo/token LLM via contextvar centralizzato | Accepted |
