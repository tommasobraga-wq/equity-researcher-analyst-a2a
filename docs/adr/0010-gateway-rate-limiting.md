# 0010 — Rate limiting del gateway a contatore globale, non per-IP

## Stato
Accepted (rivista una volta nella stessa sessione — vedi sotto)

## Contesto
`POST /api/chat` sul gateway web innesca chiamate LLM a pagamento e l'esecuzione di una pipeline completa — merita un limite di frequenza per evitare click ripetuti accidentali o bug lato client che moltiplicano le run. La prima implementazione (`shared/rate_limit.py::RateLimiter`) era una struttura generica **multi-chiave** (dizionario `chiave → hit`), usata con `request.client.host` (IP del chiamante) come chiave.

## Decisione
In sede di audit overengineering, semplificata a un **contatore globale singolo** (nessuna chiave): il gateway è uno strumento ad uso personale con un solo operatore reale (il browser locale) — una chiave IP vedrà sempre lo stesso valore, quindi la generalità multi-chiave non serve mai. Il comportamento (finestra scorrevole 60s, stesso limite configurabile via `RATE_LIMIT_PER_MINUTE`) resta identico; cambia solo che non esiste più il concetto di "chiamante" da distinguere.

## Conseguenze
- **Positive**: meno codice, meno test dedicati alla generalità multi-chiave (rimosso `test_keys_are_independent`), nessuna astrazione che non verrà mai esercitata nella pratica.
- **Negative**: se in futuro il gateway venisse esposto a più utenti reali (non più solo l'operatore locale), il rate limiting tornerebbe a dover essere per-chiamante — a quel punto la generalità andrebbe reintrodotta, ma con un requisito reale a giustificarla, non per precauzione.
- Il cap di concorrenza (`_MAX_CONCURRENT_RUNS`, run pipeline simultanee) è rimasto invariato: quello protegge da un problema reale anche per un solo utente (aprire due tab per errore), a differenza del keying per IP.
