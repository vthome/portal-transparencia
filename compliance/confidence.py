"""Confiança calculada a partir dos sinais do verificador — nunca declarada por ele.

    confiança = sigmoid(b + w_forca·forca + w_fontes·min(fontes, 3) + w_qualidade·qualidade)

Os pesos padrão foram escolhidos para que:
- indício direto e coleta limpa (forca 0.9) fique em ~0.8
- só um link sem a página (forca 0.55) fique abaixo de 0.6 → revisão humana
- ausência sem a seção ter sido localizada (forca 0.35) fique em ~0.4 → revisão humana

Quando houver revisões humanas suficientes, `python -m compliance.calibrar` ajusta os pesos
por regressão logística (alvo: o revisor concordou com o veredito) e grava
compliance/config/confianca.json, que passa a ser usado automaticamente.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ARQUIVO_CALIBRADO = Path(__file__).resolve().parent / "config" / "confianca.json"
PESOS_PADRAO = {"intercepto": -3.0, "forca": 3.2, "fontes": 0.35, "qualidade": 1.2}


def carregar_pesos() -> dict[str, float]:
    if ARQUIVO_CALIBRADO.exists():
        try:
            dados = json.loads(ARQUIVO_CALIBRADO.read_text(encoding="utf-8"))
            return {k: float(dados["pesos"][k]) for k in PESOS_PADRAO}
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    return dict(PESOS_PADRAO)


def features(sinais: dict[str, Any]) -> list[float]:
    return [float(sinais.get("forca", 0.5)), float(min(int(sinais.get("fontes", 1)), 3)),
            float(sinais.get("qualidade", 1.0))]


def calcular(sinais: dict[str, Any], pesos: dict[str, float] | None = None) -> float:
    w = pesos or carregar_pesos()
    f, n, q = features(sinais)
    z = w["intercepto"] + w["forca"] * f + w["fontes"] * n + w["qualidade"] * q
    return round(1 / (1 + math.exp(-z)), 3)
