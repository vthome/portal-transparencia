"""Contrato dos verificadores.

Um verificador recebe (critério, coleta do ente, contexto) e devolve um `Resultado` com
status, evidências e SINAIS. A confiança não é declarada pelo verificador: é calculada
depois, a partir dos sinais (compliance/confidence.py), de forma uniforme e calibrável.

Sinais:
  forca     0..1  quão direto é o indício (cabeçalho exato 0.9, link sem página 0.55,
                  ausência numa seção coletada 0.7, ausência sem seção 0.35)
  fontes    int   quantas evidências independentes concordam
  qualidade 0..1  fração das páginas da seção coletadas com sucesso
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from compliance.coleta import ColetaEnte

NOTA = {"atende": 1.0, "parcial": 0.5, "nao_atende": 0.0}


@dataclass
class Evidencia:
    tipo: str                                   # link, trecho, tabela, arquivo, formulario, screenshot, trilha, data, ausencia
    url: str | None = None
    page_snapshot_id: str | None = None
    file_artifact_id: str | None = None
    trecho: str | None = None
    screenshot_path: str | None = None
    metadados: dict[str, Any] = field(default_factory=dict)


@dataclass
class Resultado:
    status: str                                 # atende | parcial | nao_atende | nao_aplicavel | nao_verificavel
    justificativa: str
    evidencias: list[Evidencia] = field(default_factory=list)
    sinais: dict[str, Any] = field(default_factory=dict)
    metodo: str = "regra"                       # regra | embedding | llm | humano | pendente
    confianca: float | None = None

    @property
    def nota(self) -> float | None:
        return NOTA.get(self.status)


@dataclass
class Contexto:
    data_ref: date
    cfg: dict[str, Any]                         # scoring.yaml
    indice: Any = None                          # IndiceSemantico do ente (ou None sem embeddings)
    embedder: Any = None


Verificador = Callable[[Any, ColetaEnte, Contexto], Resultado]
REGISTRO: dict[str, Verificador] = {}
VERSOES: dict[str, str] = {}


def verificador(nome: str, versao: str = "1"):
    def deco(fn: Verificador) -> Verificador:
        REGISTRO[nome] = fn
        VERSOES[nome] = versao
        return fn
    return deco


def r(status: str, justificativa: str, evid: list[Evidencia] | None = None, *, forca: float = 0.5,
      fontes: int = 1, qualidade: float = 1.0, metodo: str = "regra", **extra) -> Resultado:
    return Resultado(status, justificativa, evid or [], {"forca": round(forca, 3), "fontes": fontes,
                                                          "qualidade": round(qualidade, 3), **extra}, metodo)


def ausencia(coleta: ColetaEnte, secoes: list[str] | None, oque: str, evid: list[Evidencia] | None = None
             ) -> Resultado:
    """O item não foi encontrado. Decide entre não atende e não verificável conforme a coleta.

    Falha do robô nunca vira "não atende": seção com páginas só em erro/vazio/robots, job que
    falhou ou portal não coletado resultam em `nao_verificavel`.
    """
    q = coleta.qualidade(secoes)
    sit = coleta.situacao(secoes)
    rotulo = ", ".join(secoes or []) or "site"
    if sit == "coberta":
        return r("nao_atende", f"{oque} não identificado(a) nas páginas coletadas da seção ({rotulo}).",
                 evid, forca=0.7, qualidade=q, situacao=sit)
    if sit == "falha":
        return r("nao_verificavel", f"páginas da seção ({rotulo}) não puderam ser lidas (erro, vazio ou robots.txt).",
                 evid, forca=0.0, qualidade=q, situacao=sit)
    if coleta.job.get("status") == "falhou" or not any(p.ok for p in coleta.paginas):
        return r("nao_verificavel", "a coleta do ente falhou; não há base para avaliar.", evid, forca=0.0,
                 qualidade=q, situacao="coleta_falhou")
    if coleta.hosts_portal and not coleta.paginas_portal():
        return r("nao_verificavel", "o portal da transparência não foi coletado nesta rodada.", evid, forca=0.0,
                 qualidade=q, situacao="portal_nao_coletado")
    return r("nao_atende", f"seção ({rotulo}) não localizada no orçamento de páginas da rodada; {oque} "
             "não encontrado(a). Recomenda-se revisão humana.", evid, forca=0.35, qualidade=q, situacao=sit)
