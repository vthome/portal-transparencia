"""Revisão humana: fila (baixa confiança), gravação do veredito e concordância com o motor.

Fluxo do diagrama: Streamlit → POST /revisoes → monitor.human_review. As revisões alimentam
`python -m compliance.calibrar`, que reajusta os pesos da confiança.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api import servicos
from api.deps import exige_leitura, exige_revisor, get_repo
from api.schemas import RevisaoIn, RevisaoOut

router = APIRouter(prefix="/revisoes", tags=["revisão humana"])


@router.get("/fila", dependencies=[Depends(exige_leitura)],
            summary="Quesitos da última avaliação que precisam de revisão",
            description="Confiança abaixo do limiar (`scoring.yaml → revisao_abaixo_de`, padrão 0,6) ou não "
                        "verificáveis, ainda sem revisão. Ordem: essenciais, peso, menor confiança.")
def fila(slug: str | None = Query(None, alias="ente"),
         limiar: float | None = Query(None, ge=0, le=1),
         incluir_nao_verificaveis: bool = True,
         limite: int = Query(100, ge=1, le=1000),
         repo=Depends(get_repo)) -> list[dict[str, Any]]:
    lim = servicos.cfg_scoring()["revisao_abaixo_de"] if limiar is None else limiar
    return repo.fila(lim, slug, incluir_nao_verificaveis, limite)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RevisaoOut,
             dependencies=[Depends(exige_revisor)],
             summary="Grava o veredito do revisor (exige X-API-Key = API_TOKEN_REVISAO)",
             description="Não altera o resultado do motor: registra a correção em `monitor.human_review`. "
                         "O status final passa a ser o do revisor e o score revisado é recalculado.")
def revisar(body: RevisaoIn, repo=Depends(get_repo)):
    r = repo.resultado(body.resultado_id)
    if r is None:
        raise HTTPException(404, "resultado não encontrado")
    rev = repo.inserir_revisao(body.resultado_id, body.status_corrigido, body.revisor.strip(), body.comentario)
    score = servicos.score_revisado(repo.resultados(r["avaliacao_id"]))
    return {"revisao": rev, "concordou_com_motor": body.status_corrigido == r["status"], "score_revisado": score}


@router.get("", response_model=None, dependencies=[Depends(exige_leitura)], summary="Revisões gravadas")
def listar(slug: str | None = Query(None, alias="ente"), limite: int = Query(200, ge=1, le=5000),
           repo=Depends(get_repo)) -> list[dict[str, Any]]:
    return repo.revisoes(slug, limite)


@router.get("/concordancia", dependencies=[Depends(exige_leitura)],
            summary="Quanto os revisores concordam com o motor, por verificador e por faixa de confiança",
            description="Base para decidir quando recalibrar (`python -m compliance.calibrar`, a partir de 30 "
                        "revisões).")
def concordancia(repo=Depends(get_repo)) -> dict[str, Any]:
    revs = repo.revisoes(None, 100000)

    def agrega(chave) -> dict[str, dict[str, Any]]:
        g: dict[str, list[bool]] = defaultdict(list)
        for x in revs:
            g[chave(x)].append(bool(x["concordou"]))
        return {k: {"n": len(v), "concordancia": round(sum(v) / len(v), 3)} for k, v in sorted(g.items())}

    def faixa(x) -> str:
        c = x.get("confianca")
        return "sem confiança" if c is None else f"{min(int(c * 5), 4) * 0.2:.1f}–{min(int(c * 5), 4) * 0.2 + 0.2:.1f}"

    total = len(revs)
    return {"revisoes": total,
            "concordancia": round(sum(bool(x["concordou"]) for x in revs) / total, 3) if total else None,
            "pronto_para_calibrar": total >= 30,
            "por_verificador": agrega(lambda x: x["verificador"]),
            "por_faixa_de_confianca": agrega(faixa)}

