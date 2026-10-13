#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
elif [[ -f "$ROOT_DIR/.venv/Scripts/python.exe" ]]; then
  # Supports Git Bash on Windows as well as Linux/macOS.
  PYTHON="$ROOT_DIR/.venv/Scripts/python.exe"
else
  BOOTSTRAP_PYTHON="$(find_python || true)"
  if [[ -z "$BOOTSTRAP_PYTHON" ]]; then
    echo "Python 3.11+ is required but was not found." >&2
    exit 1
  fi
  echo "Creating virtual environment..."
  "$BOOTSTRAP_PYTHON" -m venv .venv
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON="$ROOT_DIR/.venv/Scripts/python.exe"
  fi
fi

if ! "$PYTHON" -c 'import openpyxl, mysql.connector, apscheduler, waitress, argon2; from zoneinfo import ZoneInfo; ZoneInfo("America/New_York")' >/dev/null 2>&1; then
  echo "Installing dependencies..."
  "$PYTHON" -m pip install -r requirements.txt
fi

echo "Starting Price Monitor at http://127.0.0.1:8080"
exec "$PYTHON" -m price_monitor.app
