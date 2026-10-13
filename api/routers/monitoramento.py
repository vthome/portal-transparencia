"""Saúde das rodadas de coleta e o checklist em vigor."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import exige_leitura, get_repo

router = APIRouter(tags=["monitoramento"], dependencies=[Depends(exige_leitura)])


@router.get("/rodadas", summary="Rodadas de coleta: entes, falhas, volume e quantas foram avaliadas")
def rodadas(limite: int = Query(20, ge=1, le=200), repo=Depends(get_repo)) -> list[dict[str, Any]]:
    return repo.rodadas(limite)


@router.get("/rodadas/{run_id}", summary="Coleta de cada ente numa rodada (status, páginas, seções ausentes)")
def rodada(run_id: int, repo=Depends(get_repo)) -> list[dict[str, Any]]:
    jobs = repo.rodada(run_id)
    if not jobs:
        raise HTTPException(404, f"rodada {run_id} não encontrada")
    return jobs


@router.get("/checklist", summary="Quesitos do checklist em vigor")
def checklist(dimensao: str | None = None, verificador: str | None = None,
              repo=Depends(get_repo)) -> list[dict[str, Any]]:
    from api.servicos import ordem_codigo

    linhas = [c for c in repo.checklist()
              if (dimensao is None or c["dimensao"] == dimensao)
              and (verificador is None or c["verificador"] == verificador)]
    return sorted(linhas, key=lambda c: ordem_codigo(c["codigo"]))
