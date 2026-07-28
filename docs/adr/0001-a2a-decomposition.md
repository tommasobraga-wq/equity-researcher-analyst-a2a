# 0001 — Decomposizione da crew monolitica a servizi A2A indipendenti

## Stato
Accepted

## Contesto
Il progetto nasce da una crew CrewAI: un unico processo Python in cui gli agenti sono oggetti in-memory orchestrati da un `Process` sequenziale interno al framework. Questo rende ogni agente indissolubile dagli altri — non è possibile scalarli, versionarli, riavviarli o osservarli indipendentemente, e ogni cambiamento a un agente richiede di ridistribuire l'intera crew.

## Decisione
Decomporre la crew in **7 servizi FastAPI indipendenti** (DataCollector, NewsSentiment, FundamentalAnalyst, RiskAssessor, ComplianceAgent, PortfolioManager, ReportWriter), ciascuno con la propria porta, che comunicano tramite **JSON-RPC 2.0 su HTTP** seguendo il protocollo A2A (Agent-to-Agent): `shared/a2a_models.py` definisce il wire format (`JsonRpcRequest`/`A2ATask`/`A2ATaskResult`), `shared/a2a_server.py::handle_task` è l'handler condiviso da tutti gli agenti.

## Conseguenze
- **Positive**: ogni agente è un processo indipendente, riavviabile e osservabile (`/health`, agent card via `/.well-known/agent.json`) senza toccare gli altri; il protocollo A2A rende esplicito il contratto tra agenti invece di lasciarlo implicito nelle chiamate di funzione di CrewAI.
- **Negative**: la latenza di rete (HTTP) sostituisce la chiamata in-process; serve un meccanismo esplicito di autenticazione/firma tra servizi che prima non serviva (vedi [0006](0006-hmac-auth-audit-trail.md)); il deployment locale richiede orchestrare 7+ processi (mitigato da [0008](0008-containerization.md)).
- La granularità a 7 servizi (uno per responsabilità di dominio) è stata preferita a un singolo servizio multi-endpoint: ogni agente ha un modello e un budget di costo LLM diverso (vedi tabella modelli in `CLAUDE.md`), e la separazione rende il costo per stadio osservabile (vedi [0012](0012-observability.md)).
