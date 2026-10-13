"""Indicadores comparativos entre entes (última avaliação de cada um)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api import servicos
from api.deps import exige_leitura, get_repo
from api.schemas import ItemRanking

router = APIRouter(prefix="/indicadores", tags=["indicadores"], dependencies=[Depends(exige_leitura)])


@router.get("/ranking", response_model=list[ItemRanking], summary="Ranking pelo score geral ou de uma dimensão",
            description="Avaliações com cobertura abaixo do mínimo (`Avaliação incompleta`) aparecem no fim, "
                        "sem posição.")
def ranking(dimensao: str | None = Query(None, description="ex.: receita_despesa"), repo=Depends(get_repo)):
    dims = servicos.cfg_scoring()["dimensoes"]
    if dimensao and dimensao not in dims:
        raise HTTPException(422, f"dimensão inválida; use uma de {sorted(dims)}")
    return servicos.ranking(repo.entes(), dimensao)


@router.get("/dimensoes", summary="Matriz ente × dimensão (para heatmap)")
def dimensoes(repo=Depends(get_repo)) -> dict[str, Any]:
    dims = servicos.cfg_scoring()["dimensoes"]
    linhas = []
    for e in repo.entes():
        av = servicos.resumo_avaliacao(e)
        if av:
            linhas.append({"slug": e["slug"], "nome": e["nome"], "score": av["score"],
                           **{d: av["dimensoes"].get(d) for d in dims}})
    return {"dimensoes": dims, "linhas": linhas}


@router.get("/status", summary="Quantidade de quesitos por status em cada ente")
def status_por_ente(repo=Depends(get_repo)) -> list[dict[str, Any]]:
    out = []
    for e in repo.entes():
        av = servicos.resumo_avaliacao(e)
        if av:
            out.append({"slug": e["slug"], "nome": e["nome"], "run_id": av["run_id"], **av["contagem"]})
    return out


@router.get("/dimensoes/rotulos", summary="Rótulos das dimensões do score")
def rotulos() -> dict[str, str]:
    return servicos.cfg_scoring()["dimensoes"]
