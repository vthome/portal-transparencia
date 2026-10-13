"""Entes monitorados: lista com a última avaliação e detalhe com URLs e score revisado."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api import servicos
from api.deps import exige_leitura, get_repo
from api.schemas import EnteDetalhe, EnteResumo

router = APIRouter(prefix="/entes", tags=["entes"], dependencies=[Depends(exige_leitura)])


def montar_ente(e: dict[str, Any]) -> dict[str, Any]:
    coleta = None
    if e.get("coleta_run_id") is not None:
        coleta = {"run_id": e["coleta_run_id"], "status": e.get("coleta_status"), "paginas": e.get("coleta_paginas"),
                  "arquivos": e.get("coleta_arquivos"), "finalizado_em": e.get("coleta_em")}
    return {"slug": e["slug"], "nome": e["nome"], "municipio": e["municipio"], "uf": e["uf"],
            "cod_ibge": (e.get("cod_ibge") or "").strip() or None, "tipo_entidade": e["tipo_entidade"],
            "poder": e["poder"], "esfera": e["esfera"], "avaliacao": servicos.resumo_avaliacao(e), "coleta": coleta}


def ente_ou_404(repo, slug: str) -> dict[str, Any]:
    e = next((x for x in repo.entes() if x["slug"] == slug), None)
    if e is None:
        raise HTTPException(404, f"ente '{slug}' não encontrado")
    return e


@router.get("", response_model=list[EnteResumo], summary="Entes com a última avaliação e a última coleta")
def listar(repo=Depends(get_repo)):
    return [montar_ente(e) for e in repo.entes()]


@router.get("/{slug}", response_model=EnteDetalhe, summary="Detalhe do ente",
            description="Inclui as URLs vigentes e o **score revisado**: o mesmo cálculo da Camada 2, "
                        "com o veredito do revisor no lugar do motor onde houver revisão humana.")
def detalhe(slug: str, repo=Depends(get_repo)):
    e = ente_ou_404(repo, slug)
    out = montar_ente(e) | {"urls": repo.urls(slug), "score_revisado": None, "revisoes": 0}
    if e.get("avaliacao_id") is not None:
        res = repo.resultados(e["avaliacao_id"])
        out["revisoes"] = sum(1 for r in res if r.get("status_revisado"))
        out["score_revisado"] = servicos.score_revisado(res)
    return out
