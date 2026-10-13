#!/usr/bin/env bash
# Sobe a API local (http://127.0.0.1:8000/docs) ou testa uma API publicada.
#   scripts/api.sh
#   scripts/api.sh --url https://monitor-transparencia-api.onrender.com [--token X]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python
[ -z "${VIRTUAL_ENV:-}" ] && for v in .venv mprj-venv; do [ -x "$v/bin/python" ] && { PY="$v/bin/python"; break; }; done
if [ "${1:-}" = "--url" ]; then exec "$PY" scripts/testar_api.py "$@"; fi
exec "$PY" -m uvicorn api.main:app --reload --port "${PORT:-8000}"
