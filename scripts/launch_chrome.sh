#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${LAYA_BROWSER_CDP_PORT:-9333}"
ENDPOINT="http://127.0.0.1:$PORT"
PROFILE="${LAYA_BROWSER_CHROME_PROFILE:-$ROOT/.runtime/chrome-profile}"

if curl -fsS "$ENDPOINT/json/version" >/dev/null 2>&1; then
  printf 'Chrome automation endpoint ready at %s\n' "$ENDPOINT"
  exit 0
fi

mkdir -p "$PROFILE"
open -na "Google Chrome" --args \
  --remote-debugging-port="$PORT" \
  --remote-debugging-address=127.0.0.1 \
  --remote-allow-origins=http://localhost \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check

for _ in {1..50}; do
  if curl -fsS "$ENDPOINT/json/version" >/dev/null 2>&1; then
    printf 'Chrome automation endpoint ready at %s\n' "$ENDPOINT"
    exit 0
  fi
  sleep 0.1
done

printf 'Chrome automation endpoint did not start at %s\n' "$ENDPOINT" >&2
exit 1
