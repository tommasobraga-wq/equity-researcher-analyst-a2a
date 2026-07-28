# 0006 — Firma HMAC opt-in + audit trail su Postgres

## Stato
Accepted

## Contesto
Decomponendo la crew in 7 servizi HTTP indipendenti ([0001](0001-a2a-decomposition.md)), è comparsa una superficie che prima non esisteva: chiamate di rete tra componenti che devono potersi fidare l'uno dell'altro (integrità/autenticità del payload) e un bisogno di tracciabilità (chi ha chiamato chi, quando, con quale esito) che nella crew monolitica era implicito nel flusso di controllo di un singolo processo.

## Decisione
- **Firma HMAC-SHA256** (`shared/auth.py`) su ogni richiesta/risposta A2A, con un singolo segreto condiviso (`A2A_SHARED_SECRET`) e protezione da replay (skew 300s). Se il segreto non è configurato, l'autenticazione è **saltata silenziosamente** — default dev-friendly. `enforce_secret_policy()` fallisce rapidamente all'avvio se `A2A_AUTH_REQUIRED=true` ma nessun segreto è configurato, così "girare senza auth" richiede un opt-in esplicito, non è un gap silenzioso.
- **Audit trail** (`shared/audit.py::log_event`) su Postgres (`audit_log`): ogni chiamata A2A e ogni fetch esterno (yfinance/RSS) viene registrato con `correlation_id`/`agent`/`status`/`duration_ms`/`payload`. Best-effort: mai un'eccezione, degrada a un warning se `DATABASE_URL` non è configurato.

## Conseguenze
- **Positive**: sicurezza e osservabilità sono opt-in coerenti con l'uso attuale (locale, singolo utente) senza bloccare lo sviluppo quotidiano; l'audit trail diventa poi la base dati per l'osservabilità aggregata (vedi [0012](0012-observability.md)) senza bisogno di un sistema di tracing separato (Prometheus/OpenTelemetry).
- **Negative**: il default "auth skippata se il segreto non c'è" è un rischio se l'applicazione venisse esposta oltre `localhost` senza che qualcuno imposti esplicitamente `A2A_AUTH_REQUIRED=true` — accettabile per l'uso attuale, da rivalutare se cambiasse il perimetro di rete del deployment.
