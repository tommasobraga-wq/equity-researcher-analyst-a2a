#!/usr/bin/env bash
# start.sh — avvia in cascata tutti e 5 gli agenti, poi mostra il comando per l'orchestratore.
# Uso:   ./start.sh
# Stop:  ./start.sh stop

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$REPO_DIR/logs"
PID_FILE="$REPO_DIR/.agents.pid"

AGENTS=(
  "data-collector:8001"
  "news-sentiment:8002"
  "fundamental-analyst:8003"
  "risk-assessor:8004"
  "report-writer:8005"
)

HEALTH_RETRIES=20   # tentativi ogni 1s → max 20s per agente
HEALTH_INTERVAL=1

# ── colori ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
die()  { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

# ── stop ──────────────────────────────────────────────────────────────────────
stop_agents() {
  if [[ ! -f "$PID_FILE" ]]; then
    warn "Nessun file PID trovato ($PID_FILE). Niente da fermare."
    return
  fi
  echo "Fermo gli agenti..."
  while IFS= read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && ok "PID $pid terminato"
    else
      warn "PID $pid non in esecuzione"
    fi
  done < "$PID_FILE"
  rm -f "$PID_FILE"
  ok "Tutti gli agenti fermati."
}

if [[ "${1:-}" == "stop" ]]; then
  stop_agents
  exit 0
fi

# ── pre-flight ────────────────────────────────────────────────────────────────
[[ -f "$REPO_DIR/.env" ]] || die "File .env mancante in $REPO_DIR"
mkdir -p "$LOG_DIR"
> "$PID_FILE"

# ── avvio a cascata ───────────────────────────────────────────────────────────
for entry in "${AGENTS[@]}"; do
  name="${entry%%:*}"
  port="${entry##*:}"
  log="$LOG_DIR/${name}.log"
  agent_path="$REPO_DIR/agents/${name}/agent.py"

  [[ -f "$agent_path" ]] || die "Agent non trovato: $agent_path"

  echo ""
  # se la porta è già occupata, termina il processo che la detiene
  existing_pid="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]]; then
    warn "${name} già attivo su :${port} (PID ${existing_pid}) — riavvio..."
    kill "$existing_pid" 2>/dev/null || true
    # attendi che la porta si liberi (max 5s)
    for ((w=1; w<=5; w++)); do
      lsof -ti tcp:"$port" > /dev/null 2>&1 || break
      sleep 1
    done
  fi

  echo "Avvio ${name} su :${port}..."
  uv run python "$agent_path" > "$log" 2>&1 &
  pid=$!
  echo "$pid" >> "$PID_FILE"

  # attesa health-check
  ok=false
  for ((i=1; i<=HEALTH_RETRIES; i++)); do
    if curl -sf "http://localhost:${port}/health" > /dev/null 2>&1; then
      ok=true
      break
    fi
    sleep "$HEALTH_INTERVAL"
  done

  if $ok; then
    ok "${name} pronto (PID ${pid}, log: logs/${name}.log)"
  else
    die "${name} non ha risposto su :${port} entro $((HEALTH_RETRIES * HEALTH_INTERVAL))s. Controlla logs/${name}.log"
  fi
done

# ── riepilogo ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN} Tutti gli agenti sono attivi.${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo ""
echo "Log:        $LOG_DIR/"
echo "PID file:   $PID_FILE"
echo ""
echo "Per avviare una pipeline di esempio:"
echo "  uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI"
echo ""
echo "Per fermare tutto:"
echo "  ./start.sh stop"
