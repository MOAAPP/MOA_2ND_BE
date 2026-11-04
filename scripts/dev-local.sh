#!/usr/bin/env bash
# Start FastAPI in the local repository's own venv. Never use the macOS system Python 3.9.
set -euo pipefail
cd "$(dirname "$0")/.."

candidates=(python3.12 python3.13 python3.11 python3.10 python3)
selected=""
for candidate in "${candidates[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
    selected="$candidate"
    break
  fi
done
if [[ -z "$selected" ]]; then
  echo "Python 3.10+ is required. Current macOS system Python 3.9 is too old." >&2
  echo "Install Python 3.12: brew install python@3.12" >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  "$selected" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
exec .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
