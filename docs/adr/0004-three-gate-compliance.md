# 0004 — Modello a tre gate: deterministico, RAG, deterministico

## Stato
Accepted

## Contesto
Un sistema di ricerca azionaria che alimenta decisioni reali ha bisogno di controlli di conformità che non possono dipendere solo dal giudizio (probabilistico) di un LLM: una lista di titoli sotto restrizione, un'esclusione ESG, un limite di concentrazione di portafoglio sono regole note a priori, verificabili con certezza — farle giudicare da un LLM significherebbe accettare un margine di errore non necessario dove non serve creatività, solo applicazione di regole.

Al tempo stesso, alcune verifiche di conformità (SFDR, MiFID II, Consob) richiedono di interpretare testo normativo e applicarlo al caso specifico — un compito che non si presta a una regola deterministica ma a un giudizio informato da un corpus documentale.

## Decisione
Tre gate, ciascuno con il meccanismo giusto per il tipo di verifica che deve fare:
- **Gate 1** (`shared/eligibility.py`, deterministico, zero costo LLM): restricted list, qualità dati, perimetro geografico US/EU, esclusioni ESG — tutte regole espresse in YAML/codice, mai delegate a un LLM.
- **Gate 2** (`agents/compliance-agent`, RAG su Voyage AI + pgvector): l'unico gate con giudizio LLM, scope limitato esplicitamente a ciò che il corpus indicizzato contiene (SFDR/MiFID II/Consob) — non re-deriva le regole di Gate 1/3 dalla RAG, che sarebbe sia ridondante sia non ancorato (quelle regole vivono in YAML, non nei PDF).
- **Gate 3** (`shared/portfolio.py`, deterministico): limiti aggregati di portafoglio (concentrazione, correlazione, drawdown) sull'allocazione proposta dal Portfolio Manager.

## Conseguenze
- **Positive**: ogni gate ha zero falsi negativi sulle regole che gli competono (Gate 1/3 non possono "dimenticare" una regola come farebbe un LLM); il costo LLM è speso solo dove il giudizio serve davvero (Gate 2).
- **Negative**: aggiungere una nuova regola di conformità richiede decidere esplicitamente a quale gate appartiene — un errore di categorizzazione (es. mettere un limite quantitativo dentro il prompt di Gate 2 invece che in YAML) reintrodurrebbe il problema che questo design evita.
- Un verdetto "non conforme" di Gate 2 è un esito legittimo, non un errore dell'agente: viene partizionato (`compliance_flagged`), mai fatto ripartire come retry di validazione.
