#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${LAYA_BROWSER_DEMO_PORT:-8765}"
MODEL="${LAYA_MODEL_PATH:-$ROOT/../laya-mlx/models/hub/laya-mlx}"
KEEP_OPEN="${LAYA_BROWSER_KEEP_OPEN:-8}"

"$ROOT/scripts/launch_chrome.sh"
export BU_CDP_URL="${BU_CDP_URL:-http://127.0.0.1:${LAYA_BROWSER_CDP_PORT:-9333}}"

mkdir -p "$ROOT/artifacts"
python -m http.server "$PORT" --directory "$ROOT/examples/demo_site" >"$ROOT/artifacts/demo-server.log" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
sleep 0.3

"$ROOT/.venv/bin/laya-browser" run "http://127.0.0.1:$PORT/" \
  --goal "Open Documentation, then open Local inference" \
  --success-text "No network model calls" \
  --model "$MODEL" \
  --max-steps 8 \
  --max-seconds 30 \
  --keep-open "$KEEP_OPEN" \
  --trace "$ROOT/artifacts/demo.json"
