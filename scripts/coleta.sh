#!/usr/bin/env bash
# Rodada completa da Camada 1 (coleta + extração) gravando no Supabase. Linux/macOS/Git Bash.
#   scripts/coleta.sh                          # preflight + todos os entes
#   scripts/coleta.sh --entes rj-marica --max-paginas 20
#   scripts/coleta.sh --sem-render             # sem Chromium
#   scripts/coleta.sh --so-preflight
#   scripts/coleta.sh --local                  # sem Supabase
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python; [ -x .venv/bin/python ] && PY=.venv/bin/python
export PYTHONIOENCODING=utf-8

PIPE=(); PRE=(); LOCAL=0; PULAR=0; SO=0
while [ $# -gt 0 ]; do
  case "$1" in
    --entes) PIPE+=(--entes "$2"); PRE+=(--entes "$2"); shift ;;
    --max-paginas|--paralelismo) PIPE+=("$1" "$2"); shift ;;
    --sem-render) PIPE+=(--sem-render); PRE+=(--sem-render) ;;
    --sem-rede) PRE+=(--sem-rede) ;;
    --local) PIPE+=(--local); LOCAL=1 ;;
    --pular-preflight) PULAR=1 ;;
    --so-preflight) SO=1 ;;
    *) echo "opção desconhecida: $1"; exit 2 ;;
  esac; shift
done

if [ "$LOCAL" = 0 ] && [ ! -f .env ] && [ -z "${DATABASE_URL:-}" ]; then
  echo "Sem .env nem variáveis de ambiente. Copie .env.example para .env e preencha."; exit 1
fi
if [ "$LOCAL" = 0 ] && [ "$PULAR" = 0 ]; then
  echo "== Verificação prévia =="
  "$PY" -m ingestion.preflight "${PRE[@]}" || { echo "Preflight falhou: corrija os itens [ERRO]."; exit 1; }
  [ "$SO" = 1 ] && exit 0
fi
echo "== Coleta + extração =="
set +e; "$PY" -m ingestion.pipeline "${PIPE[@]}"; COD=$?; set -e
REL=$(ls -t data/runs/*-relatorio.md 2>/dev/null | head -1 || true)
[ -n "$REL" ] && { echo; cat "$REL"; echo; echo "(salvo em $REL)"; }
exit $COD
