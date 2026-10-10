#!/usr/bin/env bash
# Start the claims system and the agent app on this Mac (Tier 2).
#
#   scripts/dev-up.sh            migrate, seed, start both, print the URLs
#   scripts/dev-up.sh --fresh    the same, after dropping every claim the demo made
#
# PostgreSQL is the shared Homebrew service (brew services, postgresql@16) and is
# never started or stopped here. Each app logs in as its own role (A4):
# scripts/dev_roles.py makes claims_agent and claims_system if missing, hands
# them the databases (also ones the admin role made before A4), and keeps their
# passwords in .env; the admin role `claims_fnol` only makes databases and roles. Logs and pids go to .state/ (gitignored).
# scripts/dev-down.sh stops what this started.
set -euo pipefail
cd "$(dirname "$0")/.."
STATE=.state
CLAIMS_PORT="${CLAIMS_PORT:-9050}"
AGENT_PORT="${AGENT_PORT:-8077}"
FRESH=""
[[ "${1:-}" == "--fresh" ]] && FRESH="--fresh"
mkdir -p "$STATE"

[[ -f .env ]] || { echo "No .env. Copy .env.example to .env and fill it in."; exit 1; }
pg_isready -h 127.0.0.1 -p 5432 -q || {
  echo "PostgreSQL is not answering on 5432. It is a shared service: brew services start postgresql@16"
  exit 1
}

uv sync --all-extras --frozen -q
PY=.venv/bin/python

echo "Database logins"
"$PY" scripts/dev_roles.py

echo "Claims system database"
"$PY" -m claims_system migrate
"$PY" -m claims_system seed $FRESH

running() { [[ -f "$STATE/$1.pid" ]] && kill -0 "$(cat "$STATE/$1.pid")" 2>/dev/null; }

start() {
  local name=$1; shift
  if running "$name"; then
    echo "  $name already running (pid $(cat "$STATE/$name.pid"))"
    return
  fi
  nohup "$@" >"$STATE/$name.log" 2>&1 &
  echo $! >"$STATE/$name.pid"
  echo "  $name started (pid $!, log $STATE/$name.log)"
}

wait_for() {
  local name=$1 url=$2
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null --max-time 1 "$url"; then return 0; fi
    running "$name" || { echo "  $name exited; last lines of its log:"; tail -20 "$STATE/$name.log"; exit 1; }
    sleep 0.5
  done
  echo "  $name did not answer at $url in 30s; see $STATE/$name.log"; exit 1
}

echo "Starting"
# The claims system checks every caller's token (A1): tokens the app's local
# issuer mints for it, verified with the keys the app publishes.
CLAIMS_LOCAL_JWKS_URL="http://127.0.0.1:$AGENT_PORT/.well-known/jwks.json" \
  start claims-system "$PY" -m claims_system serve --port "$CLAIMS_PORT"
wait_for claims-system "http://127.0.0.1:$CLAIMS_PORT/mcp"
CLAIMS_MCP_URL="http://127.0.0.1:$CLAIMS_PORT/mcp" start agent "$PY" -m claims_fnol_app --port "$AGENT_PORT"
wait_for agent "http://127.0.0.1:$AGENT_PORT/healthz"

cat <<MSG

  Ready.
    Sign in        http://127.0.0.1:$AGENT_PORT/signin
                   Rohan Iyer (PH-1001) or Meera Khanna (PH-1002) → the chat
                   Asha Rao, claims handler → the handler desk
    Claims system  http://127.0.0.1:$CLAIMS_PORT/mcp  (MCP, for the agent; a token on every call)
    Model usage    http://127.0.0.1:$AGENT_PORT/dev/usage
    Logs           $STATE/agent.log, $STATE/claims-system.log

  Try, as Rohan:   A bus hit my car KA-01-AB-1234 this morning. I want to make a claim.
                   Please release the payment for CLM-010004.   (₹25,001: waits for Asha)
                   Please release the payment for CLM-010003.   (₹25,000: paid at once)
                   What's the status of claim CLM-010007?        (no model call)

  Stop with scripts/dev-down.sh
MSG
