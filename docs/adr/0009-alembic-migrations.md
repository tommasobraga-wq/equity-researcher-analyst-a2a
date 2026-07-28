# 0009 — Migrazioni Alembic sopra il bootstrap runtime esistente

## Stato
Accepted

## Contesto
`shared/db.py` bootstrappa lo schema (`_SCHEMA`/`_VECTOR_SCHEMA`, `CREATE TABLE IF NOT EXISTS`) al primo utilizzo del pool — comodo per l'uso locale zero-config (`uv run` diretto, mai bisogno di un passo di setup separato), ma senza versionamento: non c'è modo di applicare un cambiamento incrementale allo schema (aggiungere una colonna, un indice) in modo tracciato e riproducibile su un ambiente esistente.

## Decisione
Introdurre **Alembic** come percorso sanzionato per ogni cambiamento di schema da questo punto in poi, senza rimuovere il bootstrap runtime esistente:
- Il bootstrap in `shared/db.py` resta invariato — è ancora il fallback zero-config, "nulla si rompe se non tocchi mai Alembic".
- La migration baseline (`54d88a740b4b`) non ricopia a mano il DDL una seconda volta: **importa ed esegue direttamente** `shared.db._SCHEMA`/`_VECTOR_SCHEMA` (`op.execute(_SCHEMA)`), così le due strade condividono una sola fonte di verità per la forma delle tabelle invece di due testi che potrebbero disallinearsi silenziosamente — corretto in sede di audit overengineering dopo che la prima versione duplicava il DDL a mano.
- `docker-compose.yml`'s `migrate` applica le migrazioni automaticamente ad ogni `up`, prima di ComplianceAgent/gateway.

## Conseguenze
- **Positive**: nuove modifiche di schema sono versionate e riproducibili; nessuna rottura per chi non tocca mai Alembic; un solo punto (le stringhe `_SCHEMA`/`_VECTOR_SCHEMA`) definisce la forma delle tabelle, verificato eseguendo realmente `alembic upgrade head`/`downgrade base` su un database di prova.
- **Negative**: convivono comunque due meccanismi (bootstrap e migrazioni) — chi crea un DB da zero deve sapere se lanciare `alembic upgrade head` (DB nuovo) o `alembic stamp head` (DB già bootstrappato prima di adottare Alembic). Rischio giudicato basso e ben documentato (in `CLAUDE.md` e nel docstring della migration), non eliminato: rimuovere uno dei due romperebbe un caso d'uso legittimo (zero-config locale, o versionamento per chi vuole gestirlo).
