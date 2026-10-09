#!/usr/bin/env bash
# Stop the agent app and the claims system that scripts/dev-up.sh started.
# PostgreSQL stays running: it is the shared Homebrew service.
set -uo pipefail
cd "$(dirname "$0")/.."
STATE=.state

stop() {
  local name=$1 pidfile="$STATE/$1.pid"
  if [[ ! -f "$pidfile" ]]; then echo "  $name: not started here"; return; fi
  local pid; pid=$(cat "$pidfile")
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid"
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid"; echo "  $name: killed (pid $pid)";
    else echo "  $name: stopped (pid $pid)"; fi
  else
    echo "  $name: was not running"
  fi
  rm -f "$pidfile"
}

stop agent
stop claims-system
echo "  PostgreSQL left running (shared service)."
