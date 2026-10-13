"""Score de conformidade: média ponderada em dois níveis + faixa de incerteza + classificação.

Por que não a média simples dos 142 itens:
- o tamanho do bloco no checklist decidiria o peso (Despesa 13 itens × Obras 4)
- os 42 meta-critérios repetidos (pesquisa, exportação, atualidade, histórico) valeriam
  mais que publicar a despesa
- publicar o RGF (LRF) valeria o mesmo que divulgar pesquisa de satisfação
- não aplicável contado como zero puniria o ente; falha do robô contada como zero
  misturaria problema técnico com não conformidade
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from compliance.checkers.base import NOTA, Resultado
from compliance.checklist import Criterio

VERIFICADOS = set(NOTA)


@dataclass
class Score:
    score: float | None
    score_min: float | None
    score_max: float | None
    cobertura: float | None
    essenciais_ok: bool | None
    classificacao: str
    scores_bloco: dict[str, Any]
    pesos_usados: dict[str, Any]


def _media(itens: list[tuple[int, float]]) -> float | None:
    soma_w = sum(w for w, _ in itens)
    return sum(w * n for w, n in itens) / soma_w if soma_w else None


def _agrega(pares: list[tuple[Criterio, Resultado]], dims: list[str], valor_nv: float | None) -> tuple[
        float | None, dict[str, float | None], dict[str, float | None]]:
    por_dim: dict[str, list[tuple[int, float]]] = {d: [] for d in dims}
    por_bloco: dict[str, list[tuple[int, float]]] = {}
    for c, r in pares:
        if r.status in VERIFICADOS:
            n = r.nota
        elif r.status == "nao_verificavel" and r.metodo != "pendente" and valor_nv is not None:
            n = valor_nv
        else:
            continue
        por_dim.setdefault(c.dimensao, []).append((c.peso, n))
        por_bloco.setdefault(c.bloco, []).append((c.peso, n))
    s_dim = {d: _media(v) for d, v in por_dim.items()}
    s_bloco = {b: _media(v) for b, v in sorted(por_bloco.items(), key=lambda x: int(x[0]))}
    validas = [v for v in s_dim.values() if v is not None]
    total = sum(validas) / len(validas) if validas else None
    return total, s_dim, s_bloco


def _pct(x: float | None) -> float | None:
    return None if x is None else round(100 * x, 2)


def classificar(score: float | None, essenciais_ok: bool | None, cobertura: float | None, cfg: dict) -> str:
    if score is None:
        return "Sem dados"
    if cobertura is not None and cobertura < float(cfg["cobertura_minima"]):
        return "Avaliação incompleta"
    for faixa in cfg["classificacao"]:
        if score >= faixa["min"] and (not faixa.get("exige_essenciais") or essenciais_ok is True):
            return faixa["nome"]
    return cfg["classificacao"][-1]["nome"]


def calcular(pares: list[tuple[Criterio, Resultado]], cfg: dict[str, Any]) -> Score:
    dims = list(cfg["dimensoes"])
    aplicaveis = [(c, r) for c, r in pares if r.status != "nao_aplicavel"]
    automatizados = [(c, r) for c, r in aplicaveis if r.metodo != "pendente"]
    s, s_dim, s_bloco = _agrega(automatizados, dims, None)
    s_min, _, _ = _agrega(automatizados, dims, 0.0)
    s_max, _, _ = _agrega(automatizados, dims, 1.0)

    peso_aut = sum(c.peso for c, _ in automatizados)
    peso_ver = sum(c.peso for c, r in automatizados if r.status in VERIFICADOS)
    cobertura = round(peso_ver / peso_aut, 3) if peso_aut else None

    essenciais = [r for c, r in automatizados if c.essencial]
    if any(r.status in {"nao_atende", "parcial"} for r in essenciais):
        ess_ok = False
    elif essenciais and all(r.status == "atende" for r in essenciais):
        ess_ok = True
    else:
        ess_ok = None                       # nenhum reprovado, mas há essencial não verificado

    contagem: dict[str, int] = {}
    for _, r in pares:
        k = "pendente" if r.metodo == "pendente" else r.status
        contagem[k] = contagem.get(k, 0) + 1
    score = _pct(s)
    return Score(
        score=score, score_min=_pct(s_min), score_max=_pct(s_max), cobertura=cobertura, essenciais_ok=ess_ok,
        classificacao=classificar(score, ess_ok, cobertura, cfg),
        scores_bloco={"blocos": {b: _pct(v) for b, v in s_bloco.items()},
                      "dimensoes": {d: _pct(v) for d, v in s_dim.items()},
                      "contagem": contagem, "aplicaveis": len(aplicaveis), "automatizados": len(automatizados),
                      "essenciais_reprovados": [c.codigo for c, r in automatizados
                                                if c.essencial and r.status in {"nao_atende", "parcial"}]},
        pesos_usados={"criterios": {c.codigo: c.peso for c, _ in aplicaveis},
                      "dimensoes": {d: round(1 / len(dims), 4) for d in dims}, "notas": NOTA},
    )
