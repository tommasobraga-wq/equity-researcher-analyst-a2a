# 0012 — Osservabilità: Grafana + costo/token LLM via contextvar centralizzato

## Stato
Accepted (l'instrumentazione dei call site è stata rivista una volta nella stessa sessione — vedi sotto)

## Contesto
`shared/audit.py::log_event` registra già ogni chiamata A2A e fetch esterno su Postgres (`audit_log`), ma senza vista aggregata/storica — capire quale agente è lento o quanto costa un'analisi richiedeva leggere i log a mano. Non esisteva inoltre alcun tracking di costo/token per chiamata LLM (il campo `usage` della risposta Anthropic non veniva mai catturato).

## Decisione
- **Cattura costo/token**: instrumentati i 3 punti condivisi da cui ogni agente chiama Claude (`shared/react_agent.py::run_react`, `shared/qa.py::run_llm_qa`, `agents/report-writer/agent.py::_call_claude`), con `shared/pricing.py::estimate_cost_usd` (tariffe $/M-token approssimate, **non** billing-grade).
- **Grafana** (`grafana/grafana-oss`, non `grafana/grafana` — licenza open source) come nuovo servizio Docker dietro il profilo `observability`, con datasource e dashboard "Pipeline Overview" auto-provisionati (5 pannelli: latenza/errori/costo/token/volume per agente, tutti su query SQL dirette contro `audit_log`).
- **Propagazione di `correlation_id`/`agent`**: la prima versione richiedeva che ognuno dei ~13 call site (7 agenti + coordinator + report-writer) passasse questi due valori come keyword-only obbligatori — **rivista** in sede di audit overengineering: l'invasività (toccare ogni file agente) non era giustificata dal valore del dato raccolto. Sostituita con due `contextvars.ContextVar` impostati una sola volta in `shared/a2a_server.py::handle_task` (che già riceve `task.id`/`agent_name` prima di chiamare `run_agent`), letti come default da `run_react`/`run_llm_qa`/`_call_claude`. L'unica eccezione è `orchestrator/coordinator.py::interpret_prompt`, che non passa da `handle_task` e continua a passare i valori esplicitamente.

## Conseguenze
- **Positive**: il breakdown di costo/token per agente non è ottenibile da nessun'altra fonte (la dashboard costi Anthropic non lo scompone per stadio della pipeline) — è il dato che giustifica l'instrumentazione; dopo la revisione, aggiungere un nuovo agente non richiede più ricordarsi di propagare due kwarg ad ogni chiamata LLM, solo che passi da `handle_task` come tutti gli altri.
- **Negative**: le tariffe di costo sono hardcoded e vanno verificate manualmente contro https://www.anthropic.com/pricing quando cambiano; Grafana è un servizio in più da tenere aggiornato (immagine pinnata a `11.4.0`).
- Verificato end-to-end due volte: alla prima implementazione (dashboard con dati reali da una run AAPL) e dopo la revisione della propagazione (riga `audit_log` con `agent`/`correlation_id` corretti popolati interamente dal contextvar, senza kwarg espliciti al call site).
