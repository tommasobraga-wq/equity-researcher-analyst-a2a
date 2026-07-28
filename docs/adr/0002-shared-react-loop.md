# 0002 — Loop ReAct condiviso invece di framework per-agente

## Stato
Accepted

## Contesto
Nella fase di apprendimento del progetto sono stati sperimentati framework diversi per il tool-use loop di agenti diversi (OpenAI Agents SDK, Smolagents, BeeAI ReActAgent). Questo produceva tre superfici di configurazione diverse, tre modi diversi di intercettare audit/logging, e tre comportamenti leggermente diversi nel gestire prompt caching, retry e output strutturato — nessun beneficio reale, dato che tutti e tre gli agenti chiamavano comunque l'API Anthropic sotto il cofano.

## Decisione
Ritirare la scelta multi-framework in favore di un **unico loop ReAct scritto a mano** (`shared/react_agent.py::run_react`), costruito direttamente sull'SDK Anthropic: gestisce tool-use, prompt caching (cache breakpoint su `system`/`tools`), e output strutturato forzato tramite un tool fittizio `submit_final_answer` con `tool_choice={"type": "any"}` — più economico e affidabile del chiedere "rispondi solo con JSON" nel prompt. Usato da 6 dei 7 agenti + dal coordinator; ReportWriter chiama l'SDK direttamente (`_call_claude`) perché non ha un vero passo di tool-use, solo generate+QA.

## Conseguenze
- **Positive**: un solo punto da istrumentare per audit/logging/costo (vedi [0012](0012-observability.md)); un solo comportamento di prompt caching e gestione errori da capire e mantenere; nessuna dipendenza da framework esterni il cui roadmap non è sotto controllo del progetto.
- **Negative**: perde le funzionalità "batteries-included" di quei framework (memory management, planning multi-step nativo) — non necessarie qui perché ogni agente ha un compito singolo e stateless per task.
- Non riconsiderato in questa fase: se in futuro servisse planning multi-step reale (un agente che decide dinamicamente la propria prossima azione oltre il tool-use semplice), varrebbe la pena rivalutare need vs costo di un framework dedicato.
