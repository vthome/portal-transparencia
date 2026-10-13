"""Histórico, resultados por quesito, diff entre rodadas, timeline e detalhe com evidências."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api import servicos
from api.deps import exige_leitura, get_repo
from api.routers.entes import ente_ou_404
from api.schemas import Avaliacao, ResultadoDetalhe, ResultadoItem, StatusCriterio

router = APIRouter(tags=["avaliações"], dependencies=[Depends(exige_leitura)])


def _avaliacao_ou_404(repo, slug: str, run_id: int | None) -> dict[str, Any]:
    ente_ou_404(repo, slug)
    av = repo.avaliacao(slug, run_id)
    if av is None:
        raise HTTPException(404, f"'{slug}' não tem avaliação" + (f" na rodada {run_id}" if run_id else ""))
    return av


@router.get("/entes/{slug}/historico", response_model=list[Avaliacao], summary="Evolução do score por rodada")
def historico(slug: str, repo=Depends(get_repo)):
    ente_ou_404(repo, slug)
    return [servicos.resumo_avaliacao(a) for a in repo.historico(slug)]


@router.get("/entes/{slug}/resultados", response_model=list[ResultadoItem],
            summary="Resultado de cada quesito (última avaliação ou de uma rodada)")
def resultados(slug: str,
               run_id: int | None = Query(None, description="rodada; padrão: a mais recente avaliada"),
               status: StatusCriterio | None = Query(None, description="filtra pelo status final"),
               dimensao: str | None = None,
               bloco: str | None = None,
               essencial: bool | None = None,
               precisa_revisao: bool | None = None,
               repo=Depends(get_repo)):
    av = _avaliacao_ou_404(repo, slug, run_id)
    linhas = servicos.enriquecer(repo.resultados(av["avaliacao_id"]))
    return [r for r in linhas
            if (status is None or r["status_final"] == status)
            and (dimensao is None or r["dimensao"] == dimensao)
            and (bloco is None or str(r["bloco"]) == bloco)
            and (essencial is None or r["essencial"] == essencial)
            and (precisa_revisao is None or r["precisa_revisao"] == precisa_revisao)]


@router.get("/entes/{slug}/diff", summary="O que mudou entre duas avaliações do ente",
            description="Padrão: penúltima × última avaliação. Compara o status final (revisão humana prevalece).")
def diff(slug: str, de: int | None = Query(None, description="run_id de origem"),
         para: int | None = Query(None, description="run_id de destino"),
         incluir_estaveis: bool = False, repo=Depends(get_repo)) -> dict[str, Any]:
    ente_ou_404(repo, slug)
    hist = repo.historico(slug)
    if not hist:
        raise HTTPException(404, f"'{slug}' não tem avaliação")
    runs = [h["run_id"] for h in hist]
    para = para or runs[-1]
    if de is None:
        anteriores = [r for r in runs if r < para]
        if not anteriores:
            raise HTTPException(404, f"'{slug}' só tem uma avaliação até a rodada {para}; não há o que comparar")
        de = anteriores[-1]
    av_de, av_para = (next((h for h in hist if h["run_id"] == r), None) for r in (de, para))
    if av_de is None or av_para is None:
        raise HTTPException(404, f"rodada sem avaliação para '{slug}'; disponíveis: {runs}")
    d = servicos.diff(repo.resultados(av_de["avaliacao_id"]), repo.resultados(av_para["avaliacao_id"]),
                      incluir_estaveis)
    a, b = servicos.resumo_avaliacao(av_de), servicos.resumo_avaliacao(av_para)
    delta = None if a["score"] is None or b["score"] is None else round(b["score"] - a["score"], 2)
    return {"slug": slug, "de": a, "para": b, "delta_score": delta, **d}


@router.get("/entes/{slug}/quesitos/{codigo}/timeline", summary="Histórico de um quesito do ente")
def timeline(slug: str, codigo: str, repo=Depends(get_repo)) -> list[dict[str, Any]]:
    ente_ou_404(repo, slug)
    linhas = repo.timeline(slug, codigo)
    if not linhas:
        raise HTTPException(404, f"quesito '{codigo}' sem resultados para '{slug}'")
    out, anterior = [], None
    for r in linhas:
        final = servicos.status_final(r)
        nota = servicos.NOTA.get(final)
        ev = "primeira" if anterior is None else servicos.evento(anterior[1], nota, anterior[0], final)
        out.append({**r, "status_final": final, "evento": ev})
        anterior = (final, nota)
    return out


@router.get("/resultados/{resultado_id}", response_model=ResultadoDetalhe,
            summary="Quesito avaliado com justificativa, sinais de confiança, evidências e revisões")
def resultado(resultado_id: int, repo=Depends(get_repo)):
    r = repo.resultado(resultado_id)
    if r is None:
        raise HTTPException(404, "resultado não encontrado")
    revs = repo.revisoes_do_resultado(resultado_id)
    return {**r, "sinais": r.get("sinais") or {}, "status_final": servicos.status_final(r),
            "evidencias": repo.evidencias(resultado_id), "revisoes": revs}
