#!/usr/bin/env sh
# KPI Bridge - Linux/macOS install (idempotent). Options: --no-ai --no-ocr
set -eu
cd "$(dirname "$0")"
PY=""
for cand in python3.14 python3.13 python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.12 or newer is required (python3.12 / python3-venv package)." >&2; exit 1; }
exec "$PY" scripts/kpictl.py setup "$@"
