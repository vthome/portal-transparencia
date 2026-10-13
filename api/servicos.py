"""Regras de apresentação sem banco: score com revisões, ranking, diff entre rodadas.

Tudo aqui é função pura (testável offline). O score revisado usa exatamente o mesmo cálculo
da Camada 2 (compliance.scoring), trocando o veredito do motor pelo do revisor quando houver.
"""
from __future__ import annotations

from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Iterable

from compliance.checkers.base import NOTA, Resultado
from compliance.checklist import carregar_scoring
from compliance.scoring import calcular

STATUS = ("atende", "parcial", "nao_atende", "nao_aplicavel", "nao_verificavel")


@lru_cache
def cfg_scoring() -> dict[str, Any]:
    return carregar_scoring()


def ordem_codigo(codigo: str) -> tuple:
    partes = []
    for p in str(codigo).split("."):
        partes.append((0, int(p), "") if p.isdigit() else (1, 0, p))
    return tuple(partes)


def status_final(r: dict[str, Any]) -> str:
    return r.get("status_revisado") or r["status"]


def enriquecer(resultados: Iterable[dict[str, Any]], limiar: float | None = None) -> list[dict[str, Any]]:
    """Ordena por código e acrescenta status_final, nota_final e precisa_revisao."""
    lim = cfg_scoring()["revisao_abaixo_de"] if limiar is None else limiar
    out = []
    for r in sorted(resultados, key=lambda x: ordem_codigo(x["codigo"])):
        r = dict(r)
        r["status_final"] = status_final(r)
        r["nota_final"] = NOTA.get(r["status_final"])
        r["revisado"] = r.get("status_revisado") is not None
        r["precisa_revisao"] = (not r["revisado"] and r["metodo"] != "pendente"
                                and ((r.get("confianca") is not None and r["confianca"] < lim)
                                     or r["status"] == "nao_verificavel"))
        out.append(r)
    return out


def score_revisado(resultados: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Recalcula o score com os vereditos finais (revisão humana prevalece sobre o motor)."""
    pares = []
    for r in resultados:
        c = SimpleNamespace(codigo=r["codigo"], bloco=str(r["bloco"]), dimensao=r["dimensao"],
                            peso=int(r["peso"]), essencial=bool(r["essencial"]))
        revisado = r.get("status_revisado") is not None
        pares.append((c, Resultado(status_final(r), "", metodo="humano" if revisado else r["metodo"])))
    s = calcular(pares, cfg_scoring())
    return {"score": s.score, "score_min": s.score_min, "score_max": s.score_max,
            "cobertura": None if s.cobertura is None else round(100 * s.cobertura, 2),
            "essenciais_ok": s.essenciais_ok, "classificacao": s.classificacao,
            "dimensoes": s.scores_bloco["dimensoes"], "contagem": s.scores_bloco["contagem"],
            "essenciais_reprovados": s.scores_bloco["essenciais_reprovados"]}


def resumo_avaliacao(av: dict[str, Any] | None) -> dict[str, Any] | None:
    if not av or av.get("avaliacao_id") is None:
        return None
    sb = av.get("scores_bloco") or {}
    return {"avaliacao_id": av["avaliacao_id"], "run_id": av["run_id"], "avaliado_em": av.get("avaliado_em"),
            "score": av.get("score"), "score_min": av.get("score_min"), "score_max": av.get("score_max"),
            "cobertura": av.get("cobertura"), "essenciais_ok": av.get("essenciais_ok"),
            "classificacao": av.get("classificacao"), "dimensoes": sb.get("dimensoes") or {},
            "contagem": sb.get("contagem") or {}, "essenciais_reprovados": sb.get("essenciais_reprovados") or [],
            "engine_version": av.get("engine_version"), "modelo_embedding": av.get("modelo_embedding")}


def ranking(entes: list[dict[str, Any]], dimensao: str | None = None) -> list[dict[str, Any]]:
    """Posição só para avaliações completas; incompletas (cobertura baixa) vêm depois, sem posição."""
    linhas = []
    for e in entes:
        av = resumo_avaliacao(e)
        if av is None:
            continue
        valor = av["dimensoes"].get(dimensao) if dimensao else av["score"]
        linhas.append({"slug": e["slug"], "nome": e["nome"], "valor": valor, **av,
                       "no_ranking": av["classificacao"] not in {"Avaliação incompleta", "Sem dados"}
                       and valor is not None})
    linhas.sort(key=lambda x: (not x["no_ranking"], -(x["valor"] if x["valor"] is not None else -1), x["slug"]))
    pos = 0
    for x in linhas:
        x["posicao"] = (pos := pos + 1) if x["no_ranking"] else None
    return linhas


def evento(nota_de: float | None, nota_para: float | None, status_de: str | None, status_para: str | None) -> str:
    if status_de is None:
        return "novo"
    if status_para is None:
        return "removido"
    if status_de == status_para:
        return "estavel"
    if nota_de is None or nota_para is None:
        return "mudou_verificabilidade"            # entrou/saiu de não verificável ou não aplicável
    return "melhorou" if nota_para > nota_de else "piorou"


def diff(de: list[dict[str, Any]], para: list[dict[str, Any]], incluir_estaveis: bool = False) -> dict[str, Any]:
    """Compara os vereditos finais de duas avaliações do mesmo ente, quesito a quesito."""
    a = {r["codigo"]: r for r in de}
    b = {r["codigo"]: r for r in para}
    mudancas, resumo = [], {"melhorou": 0, "piorou": 0, "estavel": 0, "mudou_verificabilidade": 0,
                            "novo": 0, "removido": 0}
    for cod in sorted(set(a) | set(b), key=ordem_codigo):
        ra, rb = a.get(cod), b.get(cod)
        sa, sb = (status_final(ra) if ra else None), (status_final(rb) if rb else None)
        ev = evento(NOTA.get(sa) if sa else None, NOTA.get(sb) if sb else None, sa, sb)
        resumo[ev] += 1
        if ev == "estavel" and not incluir_estaveis:
            continue
        base = rb or ra
        mudancas.append({"codigo": cod, "texto": base["texto"], "dimensao": base["dimensao"],
                         "peso": base["peso"], "essencial": base["essencial"], "status_de": sa, "status_para": sb,
                         "evento": ev, "resultado_id_de": ra["id"] if ra else None,
                         "resultado_id_para": rb["id"] if rb else None,
                         "justificativa_para": rb.get("justificativa") if rb else None})
    return {"resumo": resumo, "mudancas": mudancas}
