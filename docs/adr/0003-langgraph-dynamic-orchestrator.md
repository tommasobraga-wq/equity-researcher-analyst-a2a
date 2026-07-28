# 0003 — Orchestratore a grafo (LangGraph) con topologia dinamica

## Stato
Accepted

## Contesto
L'applicazione da cui questo progetto deriva concettualmente era anch'essa agentica, ma a **sequenza rigida**: una crew CrewAI con un `Process` sequenziale fisso, deciso staticamente in fase di definizione della crew. Il confronto rilevante non è "agentico vs script", ma "sequenza fissa vs grafo con controllo di flusso dinamico" — la domanda di fondo è se il salto a un orchestratore a grafo sia giustificato da un bisogno reale o sia stato fatto per esercizio.

Tre requisiti concreti, emersi durante lo sviluppo, non sono esprimibili in modo pulito con una sequenza fissa:
1. **Topologia scelta dal contenuto della richiesta**: se l'utente nomina i ticker ("confrontami NVDA e AMD") DataCollector e NewsSentiment possono girare in parallelo; se la richiesta è aperta ("opportunità nel settore bancario europeo") NewsSentiment deve proporre prima i candidati, quindi l'ordine si inverte ed è necessariamente sequenziale. Una crew a sequenza fissa richiederebbe due crew diverse selezionate da un `if` esterno al framework, non un'unica definizione che si adatta.
2. **Retry mirato con feedback, senza far fallire l'intero run**: con 7 servizi indipendenti su HTTP, un output malformato da un singolo agente non deve buttare via il lavoro (e il costo LLM) degli stadi già completati. Serve poter rimandare **allo stesso stadio** con un feedback testuale specifico, non ripartire da capo.
3. **Persistenza e ripresa a metà pipeline**: un crash o un'interruzione a metà run deve poter riprendere dall'ultimo stadio completato, non ripetere l'intera catena (costosa in token).

## Decisione
Adottare **LangGraph** (`StateGraph`) come motore dell'orchestratore (`orchestrator/main.py`): ogni stadio è un nodo, `PipelineState` (TypedDict) accumula lo stato attraversando i nodi, e:
- `set_conditional_entry_point(_entry_router)` sceglie il/i nodo/i di partenza in base a `state["mode"]` (`specific`/`discovery`, deciso dal coordinator via NL) **e** a quali dati di stadio sono già presenti nello stato — lo stesso meccanismo serve sia per la topologia dinamica sia per `--resume`.
- `_make_router`/`_make_dual_router` implementano il retry-con-feedback per singolo stadio (fino a `MAX_VALIDATION_RETRIES`), rimandando al nodo stesso invece che facendo fallire il grafo.
- `_with_persistence` salva uno snapshot di `PipelineState` su Postgres dopo ogni stadio (vedi [0007](0007-persistence-resilience.md)).

## Conseguenze
- **Positive**: i tre requisiti sopra sono coperti dallo stesso meccanismo (il grafo), non da tre soluzioni ad-hoc separate; la topologia dinamica e il retry sono stati verificati dal vivo (run reale con violazione Gate 3 → feedback → ri-allocazione riuscita, log del 28/07/2026).
- **Negative**: un grafo con router condizionali è più difficile da leggere staticamente di una sequenza fissa — richiede test dedicati sulla logica di routing (`tests/test_orchestrator_resilience.py`) per restare comprensibile.
- **Limite noto, dichiarato esplicitamente**: il grafo oggi è dinamico solo nel **punto d'ingresso** (`_entry_router`), non nel punto d'uscita — l'unico nodo che porta a `END` è `report_writer`. Una richiesta come "estraimi solo i fondamentali di AAPL, senza generare il report" non viene tradotta in un comportamento diverso: `CoordinatorIntent` non ha un campo di scope/stadio-finale, quindi la pipeline percorre sempre l'intera catena fino al report, salvo le esclusioni decise dai gate. Estendere il grafo con un'uscita anticipata condizionale è tecnicamente possibile (LangGraph lo supporta) ma non è stato implementato — è una valutazione di evolutiva futura, non un limite del framework.
