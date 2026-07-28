# 0007 — Resumable runs e memoria conversazionale; circuit breaker scartato

## Stato
Accepted (il circuit breaker descritto sotto è stato implementato e poi rimosso nella stessa sessione di audit — vedi sezione dedicata)

## Contesto
Una pipeline multi-stadio con costo LLM non trascurabile per run ha bisogno di non ripetere lavoro già fatto quando qualcosa va storto a metà, e un REPL multi-turno ha bisogno di ricordare il contesto tra un turno e l'altro ("approfondisci NVDA", "rispetto a ieri").

Durante l'implementazione era stato aggiunto anche un **circuit breaker** per agente (`_circuit_state`, soglia 3 fallimenti consecutivi, cooldown 60s con half-open) — pattern mutuato da sistemi distribuiti con più chiamanti concorrenti verso lo stesso servizio.

## Decisione
- **Resumable runs**: ogni nodo del grafo è avvolto (`_with_persistence`) per salvare uno snapshot di `PipelineState` su Postgres (`pipeline_runs`) dopo ogni stadio. `--resume <run_id>` (via `_entry_router`) riparte dal primo stadio non completato invece che da capo.
- **Memoria conversazionale**: `conversation_sessions`/`conversation_turns` persistono i turni del REPL; default "riprendi l'ultima sessione attiva" se non specificato altrimenti.
- **Circuit breaker: implementato, poi rimosso** in sede di audit overengineering (28/07/2026). Motivazione: l'orchestratore chiama ogni agente **sequenzialmente all'interno di un singolo run** (un utente, un run alla volta) — non esiste mai un secondo chiamante concorrente da cui il circuito dovrebbe proteggere l'agente, che è la premessa stessa del pattern. Il retry-su-rate-limit già esistente copre il caso transitorio; le resumable runs coprono meglio il caso di fallimento hard (si riprende dall'ultimo stadio pulito, invece di bloccare le chiamate per 60s senza permettere di procedere).

## Conseguenze
- **Positive**: nessun lavoro perso su crash/interruzione; continuità conversazionale naturale per un utente singolo; il circuit breaker rimosso ha eliminato stato in-memory dedicato, una ContextVar solo per quello, ed eventi UI dedicati, senza perdere resilienza reale (verificato con un run reale end-to-end dopo la rimozione).
- **Negative**: entrambi i meccanismi di persistenza richiedono `DATABASE_URL` — senza, degradano a no-op (stesso principio best-effort del resto di `shared/db.py`), quindi in un ambiente senza Postgres configurato non c'è resume né memoria cross-sessione.
- Questa ADR documenta esplicitamente una decisione presa e poi corretta nella stessa sessione — non riscritta a posteriori, per lasciare traccia del perché il circuit breaker sia stato scartato, utile se in futuro qualcuno fosse tentato di riaggiungerlo per lo stesso motivo (sembra "buona pratica enterprise" senza il contesto di uso mono-utente).
