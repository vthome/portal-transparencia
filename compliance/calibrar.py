"""Calibra os pesos da confiança com as revisões humanas (monitor.human_review).

    python -m compliance.calibrar            # exige pelo menos 30 revisões
    python -m compliance.calibrar --minimo 10

Alvo: o revisor concordou com o veredito do motor (status_corrigido == status).
Features: forca, fontes (até 3) e qualidade — os mesmos sinais gravados em
criterion_result.sinais. Grava compliance/config/confianca.json, usado automaticamente
pelas próximas avaliações. Reavaliar rodadas antigas recalcula a confiança com os pesos novos.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from compliance.confidence import ARQUIVO_CALIBRADO, PESOS_PADRAO, features


def ajustar(revisoes: list[dict], minimo: int = 30) -> dict:
    from sklearn.linear_model import LogisticRegression

    linhas = [r for r in revisoes if r.get("status") in {"atende", "parcial", "nao_atende"}]
    if len(linhas) < minimo:
        raise SystemExit(f"apenas {len(linhas)} revisões utilizáveis; mínimo {minimo}.")
    X = [features(r.get("sinais") or {}) for r in linhas]
    y = [int(r["status_corrigido"] == r["status"]) for r in linhas]
    if len(set(y)) < 2:
        raise SystemExit("todas as revisões concordam (ou discordam) do motor; não há o que calibrar ainda.")
    m = LogisticRegression().fit(X, y)
    pesos = {"intercepto": float(m.intercept_[0]), "forca": float(m.coef_[0][0]),
             "fontes": float(m.coef_[0][1]), "qualidade": float(m.coef_[0][2])}
    return {"pesos": pesos, "n": len(linhas), "concordancia": round(sum(y) / len(y), 3),
            "calibrado_em": datetime.now(timezone.utc).isoformat(), "padrao": PESOS_PADRAO}


def main(argv: list[str] | None = None) -> int:
    from compliance.repo import build_repo
    from ingestion.settings import get_settings

    ap = argparse.ArgumentParser()
    ap.add_argument("--minimo", type=int, default=30)
    ap.add_argument("--local", action="store_true")
    a = ap.parse_args(argv)
    res = ajustar(build_repo(get_settings(), a.local).revisoes_para_calibracao(), a.minimo)
    ARQUIVO_CALIBRADO.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
