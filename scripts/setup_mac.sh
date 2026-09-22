#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  printf 'OpenUltra currently requires an Apple Silicon Mac.\n' >&2
  exit 1
fi
MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if (( MACOS_MAJOR < 14 )); then
  printf 'OpenUltra requires macOS 14 or newer for MLX.\n' >&2
  exit 1
fi

BREW="$(command -v brew || true)"
if ! open -Ra "Google Chrome" >/dev/null 2>&1; then
  if [[ -z "$BREW" ]]; then
    printf 'Google Chrome is missing. Install Homebrew from https://brew.sh, then rerun setup.\n' >&2
    exit 1
  fi
  "$BREW" install --cask google-chrome
fi

PYTHON=""
for candidate in python3.12 python3.13 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert (3, 11) <= sys.version_info[:2] < (3, 14)' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  if [[ -z "$BREW" ]]; then
    printf 'Python 3.11-3.13 is missing. Install Homebrew from https://brew.sh, then rerun setup.\n' >&2
    exit 1
  fi
  "$BREW" install python@3.12
  PYTHON="$("$BREW" --prefix python@3.12)/bin/python3.12"
fi

"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

printf '\nOpenUltra is installed. Start it with:\n  ./scripts/start.sh\n'
printf 'The first run may download the Laya checkpoint; allow several GB of free space.\n'
