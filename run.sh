#!/usr/bin/env bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$ROOT_DIR/run.py" "$@"
fi
if command -v python >/dev/null 2>&1; then
  exec python "$ROOT_DIR/run.py" "$@"
fi

echo "Python 3.11+ is required but was not found." >&2
exit 1
