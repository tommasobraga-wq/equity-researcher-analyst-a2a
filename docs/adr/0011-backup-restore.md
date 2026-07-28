# 0011 — Backup/restore Postgres manuale on-demand

## Stato
Accepted

## Contesto
Postgres ospita audit trail, stato delle run, memoria conversazionale e i chunk RAG delle policy — dati che vale la pena poter recuperare in caso di errore, ma per un deployment single-instance/uso personale non serve una strategia di disaster recovery completa (backup incrementali, point-in-time recovery, scheduling automatico).

## Decisione
Due servizi one-shot in `docker-compose.yml` (`backup`/`restore`, profilo `tools`), che usano `pg_dump`/`pg_restore` dell'immagine `pgvector/pgvector:pg16` stessa (nessun codice applicativo dedicato): `docker compose --profile tools run --rm backup` dumpa l'intero DB (schema + estensioni + tutte le tabelle, nessuna esclusione selettiva da tenere sincronizzata) su `./backups/<timestamp>.dump` — un **bind mount all'host**, non il volume Docker nominato, così i dump sopravvivono indipendentemente dal ciclo di vita del container/volume. Nessuno scheduling automatico (cron); nessun incrementale/WAL.

## Conseguenze
- **Positive**: due comandi manuali, nessuna infrastruttura di scheduling da mantenere, proporzionato all'uso attuale; i dump su bind mount possono essere copiati altrove (USB, cloud) come passo manuale dell'operatore.
- **Negative**: nessuna protezione automatica se l'operatore dimentica di lanciare un backup prima di un'operazione rischiosa; nessun recovery a un punto nel tempo preciso (solo l'ultimo dump manuale) — accettato consapevolmente per lo scope attuale, da rivalutare se i dati diventassero critici o il deployment multi-utente.
