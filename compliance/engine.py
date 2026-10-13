"""Aplica o checklist à coleta de um ente: verificador → confiança → score."""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from datetime import date
from typing import Any

from compliance import confidence
from compliance.checkers import REGISTRO, VERSOES, Contexto, Resultado
from compliance.checkers.base import r
from compliance.checklist import Checklist, Criterio
from compliance.coleta import ColetaEnte
from compliance.scoring import Score, calcular

log = logging.getLogger(__name__)
ENGINE_VERSION = "conformidade-0.1.0"


@dataclass
class AvaliacaoEnte:
    slug: str
    resultados: list[tuple[Criterio, Resultado]]
    score: Score

    def checker_version(self, c: Criterio) -> str:
        return f"{c.verificador}@{VERSOES.get(c.verificador, '1')}"


def avaliar_criterio(c: Criterio, coleta: ColetaEnte, ctx: Contexto, pesos_conf: dict[str, float]) -> Resultado:
    if not c.aplica(coleta.tags):
        return Resultado("nao_aplicavel", f"quesito da matriz {c.matriz}/{', '.join(c.aplica_a)} não se aplica a "
                         f"{coleta.tipo_entidade} ({coleta.poder}).", metodo="regra")
    try:
        res = REGISTRO[c.verificador](c, coleta, ctx)
    except Exception as e:  # um verificador com defeito não derruba a avaliação do ente
        log.exception("verificador %s falhou em %s/%s", c.verificador, coleta.slug, c.codigo)
        res = r("nao_verificavel", f"erro interno do verificador: {type(e).__name__}: {e}", forca=0.0,
                erro=traceback.format_exc(limit=2)[-500:])
    if res.status in {"atende", "parcial", "nao_atende"}:
        res.confianca = confidence.calcular(res.sinais, pesos_conf)
    return res


def avaliar_ente(coleta: ColetaEnte, checklist: Checklist, cfg: dict[str, Any], data_ref: date | None = None,
                 indice=None, embedder=None) -> AvaliacaoEnte:
    ctx = Contexto(data_ref=data_ref or date.today(), cfg=cfg, indice=indice, embedder=embedder)
    pesos = confidence.carregar_pesos()
    pares = [(c, avaliar_criterio(c, coleta, ctx, pesos)) for c in checklist.criterios]
    return AvaliacaoEnte(coleta.slug, pares, calcular(pares, cfg))
