"""Fase de extração de uma página: HTML bruto → campos estruturados.

Função pura e versionada, usada em dois lugares:
- pelo crawler, logo após coletar (caminho normal da rodada)
- pelo `ingestion.reextract`, sobre o HTML guardado no Storage, quando o parser ou o
  `secoes.yaml` mudam — sem recoletar nada

Mudou a lógica de extração? Incremente PARSER_VERSION: o reextract usa esse número para
saber quais snapshots estão desatualizados.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ingestion.transform.normalize import find_terms, normalize_text
from ingestion.transform.parse_html import ParsedPage, parse_html
from ingestion.transform.validate import classify_page

PARSER_VERSION = 2


def relevancia(texto: str, url: str, secoes_cfg: dict[str, dict], secoes: list[str] | None = None
               ) -> tuple[float, list[str]]:
    """Pontua um texto + URL contra as seções do secoes.yaml.

    Texto (link, título, h1, breadcrumb) pesa 3; termos na URL pesam 1. Até 2 acertos
    por seção contam, para uma página cheia de "despesa" não dominar a fila.
    """
    pts, hits = 0.0, []
    url_txt = url.replace("/", " ").replace("-", " ").replace("_", " ")
    for s in (secoes if secoes is not None else list(secoes_cfg)):
        termos = secoes_cfg[s]["termos"]
        t = len(find_terms(texto, termos))
        u = len(find_terms(url_txt, termos))
        if t or u:
            hits.append(s)
            pts += 3 * min(t, 2) + min(u, 2)
    return pts, hits


@dataclass
class PaginaExtraida:
    parsed: ParsedPage
    coleta_status: str          # ok | vazio | soft404
    secoes: list[str]
    texto_hash: str
    extraido: dict[str, Any]


def extrair_pagina(html: str, url: str, url_base: str | None, texto_link: str,
                   secoes_cfg: dict[str, dict], http_status: int | None = 200) -> PaginaExtraida:
    parsed = parse_html(html, url_base or url)
    status = classify_page(parsed.texto, parsed.titulo, http_status)
    alvo = " ".join([parsed.titulo or "", *parsed.h1, *parsed.breadcrumbs, texto_link or ""])
    _, secoes = relevancia(alvo, url, secoes_cfg)
    texto_hash = hashlib.sha256(normalize_text(parsed.texto).encode()).hexdigest()
    extraido = parsed.to_dict()
    extraido.update({
        "texto_hash": texto_hash,
        "texto_link": texto_link or "",     # guardado para a reextração recalcular as seções
        "parser_version": PARSER_VERSION,
    })
    return PaginaExtraida(parsed, status, secoes, texto_hash, extraido)
