#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/x-post-queue.pid"
LOG="$ROOT/server.log"
cd "$ROOT"

is_up() {
  if [[ -f "$PID_FILE" ]]; then
    local p
    p="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${p:-}" ]] && kill -0 "$p" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

case "${1:-start}" in
  stop)
    if is_up; then
      kill "$(cat "$PID_FILE")" 2>/dev/null || true
      rm -f "$PID_FILE"
      echo "stopped"
    else
      echo "not running"
    fi
    ;;
  status)
    if is_up; then
      echo "running pid=$(cat "$PID_FILE")"
      curl -sS "http://127.0.0.1:${PORT:-4340}/api/health" || true
      echo
    else
      echo "not running"
      exit 1
    fi
    ;;
  start|*)
    if is_up; then
      echo "already running pid=$(cat "$PID_FILE")"
      curl -sS "http://127.0.0.1:${PORT:-4340}/api/health"; echo
      exit 0
    fi
    nohup python3 "$ROOT/server.py" >>"$LOG" 2>&1 &
    sleep 0.4
    if is_up; then
      echo "LIVE http://127.0.0.1:${PORT:-4340}/ pid=$(cat "$PID_FILE")"
    else
      echo "failed to start; see $LOG" >&2
      tail -n 40 "$LOG" >&2 || true
      exit 1
    fi
    ;;
esac
