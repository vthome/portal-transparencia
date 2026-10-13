#!/usr/bin/env bash
# Camada 2 (embeddings + conformidade + score) sobre a última rodada de coleta. Linux/macOS/Git Bash.
#   scripts/conclusao.sh                         # última rodada, todos os entes, modelo 768
#   scripts/conclusao.sh --run 7 --entes rj-marica
#   scripts/conclusao.sh --sem-embeddings        # só regras
#   scripts/conclusao.sh --embedder hash         # sem baixar modelo
#   scripts/conclusao.sh --forcar | --local | --manter-rodadas N
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python
if [ -z "${VIRTUAL_ENV:-}" ]; then
  for v in .venv mprj-venv; do [ -x "$v/bin/python" ] && { PY="$v/bin/python"; break; }; done
fi
export PYTHONIOENCODING=utf-8
if [[ " $* " != *" --local "* ]] && [ ! -f .env ] && [ -z "${DATABASE_URL:-}" ]; then
  echo "Sem .env nem DATABASE_URL. Copie .env.example para .env e preencha."; exit 1
fi
if [[ " $* " != *" --sem-embeddings "* && " $* " != *" hash "* ]] && ! "$PY" -c "import sentence_transformers" 2>/dev/null; then
  echo "sentence-transformers não instalado: pip install -r requirements-ml.txt (ou use --sem-embeddings / --embedder hash)"; exit 1
fi
"$PY" -m compliance.pipeline "$@"
