"""Divide páginas e arquivos coletados em trechos (chunks) para embeddings.

Regras:
- só páginas com coleta `ok` e arquivos lidos sem erro entram
- cada trecho leva um prefixo com título e seção da página: melhora a busca semântica
  ("Despesas | ...") sem custo extra
- janela de ~800 caracteres com sobreposição de 150, cortando em fim de frase
- teto de trechos por página/arquivo: o objetivo é localizar evidência, não indexar o portal
- planilhas/CSV viram um trecho descritivo (colunas + amostra), que é o que a busca precisa
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

# O SERAFIM (e a maioria dos encoders BERT) lê no máximo 128 tokens; o resto é truncado em silêncio.
# 128 tokens ≈ 500 caracteres de português — janelas de 480 com 100 de sobreposição cabem inteiras.
TAMANHO = 480
SOBREPOSICAO = 100
MAX_TRECHOS_PAGINA = 60
MAX_TRECHOS_ARQUIVO = 20
MIN_TRECHO = 40

_FIM_FRASE = re.compile(r"(?<=[.!?;:])\s+")


@dataclass
class Trecho:
    texto: str
    ordem: int
    page_snapshot_id: str | None = None
    file_artifact_id: str | None = None

    def hash(self, modelo: str) -> str:
        """Identidade do vetor: o mesmo texto no mesmo modelo gera o mesmo embedding."""
        return hashlib.sha256(f"{modelo}\n{self.texto}".encode()).hexdigest()


def janelas(texto: str, tamanho: int = TAMANHO, sobreposicao: int = SOBREPOSICAO) -> list[str]:
    texto = " ".join((texto or "").split())
    if len(texto) <= tamanho:
        return [texto] if len(texto) >= MIN_TRECHO else []
    frases = _FIM_FRASE.split(texto)
    out, atual = [], ""
    for f in frases:
        while len(f) > tamanho:                       # frase gigante (tabela achatada): corte duro
            if atual:
                out.append(atual)
                atual = ""
            out.append(f[:tamanho])
            f = f[tamanho - sobreposicao:]
        if len(atual) + len(f) + 1 > tamanho and atual:
            out.append(atual)
            atual = atual[-sobreposicao:] + " " + f
        else:
            atual = f"{atual} {f}".strip()
    if len(atual) >= MIN_TRECHO:
        out.append(atual)
    return out


def trechos_da_pagina(pagina: dict[str, Any]) -> list[Trecho]:
    if pagina.get("coleta_status") != "ok":
        return []
    titulo = (pagina.get("titulo") or "").strip()
    secoes = ", ".join(pagina.get("secoes") or [])
    prefixo = " | ".join(p for p in (titulo, secoes) if p)
    corpo = pagina.get("texto") or ""
    out = []
    for i, j in enumerate(janelas(corpo)[:MAX_TRECHOS_PAGINA]):
        out.append(Trecho(texto=f"{prefixo} | {j}" if prefixo else j, ordem=i,
                          page_snapshot_id=str(pagina["id"])))
    return out


def trechos_do_arquivo(arq: dict[str, Any]) -> list[Trecho]:
    if arq.get("erro") or not arq.get("sha256") and not arq.get("texto_extraido"):
        return []
    meta = arq.get("metadados") or {}
    rotulo = " | ".join(p for p in (arq.get("texto_link") or "", (meta.get("pdf_info") or {}).get("title", ""),
                                    ", ".join(arq.get("secoes") or [])) if p)
    tipo = meta.get("tipo")
    if tipo in {"csv", "planilha", "json"}:
        cols = ", ".join(meta.get("colunas") or [])
        amostra = "; ".join(" / ".join(map(str, linha)) for linha in (meta.get("amostra") or [])[:3])
        texto = f"{rotulo} | arquivo {arq.get('extensao') or tipo} | colunas: {cols} | amostra: {amostra}"
        return [Trecho(texto=texto[:TAMANHO * 2], ordem=0, file_artifact_id=str(arq["id"]))]
    corpo = arq.get("texto_extraido") or ""
    return [Trecho(texto=f"{rotulo} | {j}" if rotulo else j, ordem=i, file_artifact_id=str(arq["id"]))
            for i, j in enumerate(janelas(corpo)[:MAX_TRECHOS_ARQUIVO])]


def preparar(paginas: Iterable[dict], arquivos: Iterable[dict]) -> list[Trecho]:
    out: list[Trecho] = []
    for p in paginas:
        out += trechos_da_pagina(p)
    for a in arquivos:
        out += trechos_do_arquivo(a)
    return out
