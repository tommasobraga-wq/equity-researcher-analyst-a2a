# 0008 — Containerizzazione via Docker Compose con profili

## Stato
Accepted

## Contesto
Avviare l'applicazione a mano richiede 7 processi agente + Postgres/pgvector + (opzionalmente) il gateway, ciascuno con le proprie variabili d'ambiente — fragile e poco riproducibile, soprattutto per una demo o per un secondo sviluppatore.

## Decisione
Un `Dockerfile` condiviso (`x-agent-defaults` YAML anchor per non ripetere `build`/`image`/`env_file` in ogni servizio) e un `docker-compose.yml` con:
- **Profilo di default**: i 7 agenti + Postgres/pgvector + `migrate` (Alembic, vedi [0009](0009-alembic-migrations.md)) + gateway. ComplianceAgent e Postgres sono nel default, non in un profilo separato — sono uno stadio obbligatorio della pipeline (non c'è fallback se manca), a differenza di Grafana.
- **Profilo `tools`**: `ingest-policies` (rate-limited, non da rieseguire ad ogni `up`), `backup`/`restore` (manuali, on-demand).
- **Profilo `observability`**: Grafana (vedi [0012](0012-observability.md)) — puramente osservazionale, ometterlo non rompe nulla.

## Conseguenze
- **Positive**: `docker compose up` porta l'intero stack in uno stato noto e riproducibile; i profili separano ciò che è sempre necessario da ciò che è opzionale o manuale, invece di un unico `up` monolitico.
- **Negative**: un errore di categorizzazione tra "default" e "profilo separato" può rompere silenziosamente la pipeline — è già successo una volta durante lo sviluppo (ComplianceAgent inizialmente messo nel profilo sbagliato, ha rotto ogni run reale finché non è stato spostato nel default) — lezione appresa: la domanda guida è "un run reale fallisce senza questo servizio?", non "è comodo separarlo?".
