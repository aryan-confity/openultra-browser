#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/openultra-browser ]]; then
  printf 'Run ./scripts/setup_mac.sh first.\n' >&2
  exit 1
fi

export BU_CDP_URL="$(./scripts/launch_chrome.sh)"
exec .venv/bin/openultra-browser inspect "$@"
