#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${OPENULTRA_BROWSER_CDP_PORT:-9333}"
PROFILE="${OPENULTRA_BROWSER_CHROME_PROFILE:-$ROOT/.runtime/chrome-profile}"
EXPLICIT_PORT="${OPENULTRA_BROWSER_CDP_PORT:-}"
FOUND_PORT=false

if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
  printf 'OPENULTRA_BROWSER_CDP_PORT must be a port between 1024 and 65535.\n' >&2
  exit 1
fi
mkdir -p "$PROFILE"
PROFILE="$(cd "$PROFILE" && pwd -P)"

for _ in {1..20}; do
  ENDPOINT="http://127.0.0.1:$PORT"
  PID="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  if [[ -z "$PID" ]]; then
    FOUND_PORT=true
    break
  fi
  COMMAND="$(ps -p "$PID" -o command= 2>/dev/null || true)"
  if [[ "$COMMAND" == *"--user-data-dir=$PROFILE"* ]] &&
     curl -fsS "$ENDPOINT/json/version" >/dev/null 2>&1; then
    printf 'Chrome automation endpoint ready at %s\n' "$ENDPOINT" >&2
    printf '%s\n' "$ENDPOINT"
    exit 0
  fi
  if [[ -n "$EXPLICIT_PORT" ]]; then
    printf 'Port %s is occupied by a different process or Chrome profile. Choose another OPENULTRA_BROWSER_CDP_PORT.\n' "$PORT" >&2
    exit 1
  fi
  PORT=$((PORT + 1))
done

if [[ "$FOUND_PORT" != true ]]; then
  printf 'No free Chrome DevTools port was found between 9333 and 9352.\n' >&2
  exit 1
fi

open -na "Google Chrome" --args \
  --remote-debugging-port="$PORT" \
  --remote-debugging-address=127.0.0.1 \
  --remote-allow-origins=http://localhost \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check

for _ in {1..100}; do
  if curl -fsS "$ENDPOINT/json/version" >/dev/null 2>&1; then
    printf 'Chrome automation endpoint ready at %s\n' "$ENDPOINT" >&2
    printf '%s\n' "$ENDPOINT"
    exit 0
  fi
  sleep 0.1
done

printf 'Chrome automation endpoint did not start at %s\n' "$ENDPOINT" >&2
exit 1
