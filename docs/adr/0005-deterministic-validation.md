# 0005 — Validazione deterministica pre-QA invece di solo giudizio LLM

## Stato
Accepted

## Contesto
Prima di questa decisione, alcune verifiche puramente meccaniche (l'aritmetica di uno scoring che deve sommare a un totale, il formato di un ID news, la presenza di parole chiave di prompt-injection o di direttive di acquisto/vendita esplicite) venivano delegate al passo di QA basato su LLM (`shared/qa.py::run_llm_qa`). Un controllo aritmetico o di formato non ha bisogno di un giudizio — ha bisogno di essere corretto sempre, cosa che un LLM non garantisce (può sbagliare a sommare, o non notare un pattern).

## Decisione
Spostare le verifiche di formato/aritmetica in codice deterministico (`shared/validators.py`: `check_risk_scoring_deterministic`, `check_candidates_deterministic`, `check_compliance_format_deterministic`, e le funzioni di `validate_stage` per prompt-injection/direttive buy-sell/keyword crypto), eseguite **prima** del passo di QA LLM. Il prompt di QA di ogni agente è stato aggiornato per non ri-verificare l'aritmetica ("già verificata deterministicamente a monte — non serve ricontrollarla"), lasciando all'LLM solo i giudizi che richiedono davvero comprensione del linguaggio (coerenza narrativa, citazioni valide, coerenza temporale).

## Conseguenze
- **Positive**: zero falsi negativi sulle verifiche meccaniche; meno token spesi nel prompt di QA (non deve più istruire il modello a fare aritmetica); il fallimento di un controllo deterministico produce un feedback preciso e immediato (`_record_invalid`) invece di dipendere dalla capacità dell'LLM di individuare l'errore.
- **Negative**: ogni nuova regola meccanica va scritta due volte concettualmente — una volta in codice (per la garanzia) e implicitamente nel prompt dell'agente generatore (per provare a non violarla la prima volta) — ma questo è un costo accettato, non un problema: il codice è la rete di sicurezza, non la prima linea di correttezza.
