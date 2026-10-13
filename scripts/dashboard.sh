#!/usr/bin/env bash
# Abre o dashboard. Uso: scripts/dashboard.sh [URL_DA_API]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python
[ -z "${VIRTUAL_ENV:-}" ] && for v in .venv mprj-venv; do [ -x "$v/bin/python" ] && { PY="$v/bin/python"; break; }; done
[ -n "${1:-}" ] && export API_URL="$1"
exec "$PY" -m streamlit run dashboard/app.py
