"""Extração de PDF, CSV, XLS(X)/ODS e JSON.

A Camada 2 precisa saber *o que é* o arquivo (RREO? edital? folha?), *de quando* é e
*quais colunas* tem — não do dataset inteiro. Por isso lemos amostras: as primeiras
páginas de PDFs e as primeiras linhas de planilhas. Isso mantém custo e memória
previsíveis mesmo com arquivos de dezenas de MB.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from ingestion.transform.normalize import decode_bytes, normalize_header, parse_dates_br

PDF_PAGINAS_LIDAS = 3
MAX_TEXTO = 20_000
LINHAS_AMOSTRA = 5
LINHAS_LIDAS = 5_000


@dataclass
class ParsedFile:
    tipo: str                          # pdf, csv, planilha, json, zip, desconhecido
    paginas: int | None = None
    texto: str | None = None
    metadados: dict[str, Any] = field(default_factory=dict)
    erro: str | None = None


def sniff_kind(content: bytes, content_type: str | None, ext: str | None) -> str:
    ct = (content_type or "").lower()
    if content[:5] == b"%PDF-" or "pdf" in ct or ext == "pdf":
        return "pdf"
    if content[:2] == b"PK":  # zip: xlsx/ods/docx também são zip
        if ext in {"xlsx", "ods"} or "spreadsheet" in ct or "excel" in ct:
            return "planilha"
        return "zip"
    if content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or ext == "xls" or "ms-excel" in ct:
        return "planilha"
    if ext == "json" or "json" in ct:
        return "json"
    if ext in {"csv", "txt"} or "csv" in ct or "text/plain" in ct:
        return "csv"
    return "desconhecido"


def parse_file(content: bytes, content_type: str | None = None, ext: str | None = None) -> ParsedFile:
    kind = sniff_kind(content, content_type, ext)
    try:
        if kind == "pdf":
            return _pdf(content)
        if kind == "planilha":
            return _planilha(content, ext)
        if kind == "csv":
            return _csv(content, content_type)
        if kind == "json":
            return _json(content, content_type)
        if kind == "zip":
            return _zip(content)
        return ParsedFile(tipo="desconhecido")
    except Exception as e:  # arquivo corrompido não pode derrubar a coleta
        return ParsedFile(tipo=kind, erro=f"{type(e).__name__}: {e}"[:300])


def _pdf(content: bytes) -> ParsedFile:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        n = len(pdf.pages)
        partes, n_tabelas = [], 0
        for page in pdf.pages[:PDF_PAGINAS_LIDAS]:
            partes.append(page.extract_text() or "")
            n_tabelas += len(page.find_tables())
        info = {k.lower(): str(v)[:200] for k, v in (pdf.metadata or {}).items()
                if k in ("Title", "Author", "Subject", "Creator", "Producer", "CreationDate", "ModDate")}
    texto = "\n".join(partes)[:MAX_TEXTO]
    meta: dict[str, Any] = {
        "pdf_info": info,
        "tabelas_nas_primeiras_paginas": n_tabelas,
        "sem_texto": len(texto.strip()) < 50,   # provável PDF escaneado -> OCR em evolução futura
        "datas": sorted({d.isoformat() for d in parse_dates_br(texto)}, reverse=True)[:10],
        "exercicios": _exercicios(texto),
        "periodo": _periodo(texto),
    }
    return ParsedFile(tipo="pdf", paginas=n, texto=texto, metadados=meta)


def _csv(content: bytes, content_type: str | None) -> ParsedFile:
    import pandas as pd

    texto = decode_bytes(content, content_type)
    primeira = texto.splitlines()[0] if texto else ""
    sep = max([";", ",", "\t", "|"], key=primeira.count)
    df = pd.read_csv(io.StringIO(texto), sep=sep, nrows=LINHAS_LIDAS, dtype=str,
                     on_bad_lines="skip", engine="python")
    return ParsedFile(tipo="csv", texto=texto[:MAX_TEXTO], metadados={
        "separador": sep, **_resumo_df(df), "linhas_estimadas": texto.count("\n")})


def _planilha(content: bytes, ext: str | None) -> ParsedFile:
    import pandas as pd

    engine = {"xls": "xlrd", "ods": "odf"}.get(ext or "", "openpyxl")
    xls = pd.ExcelFile(io.BytesIO(content), engine=engine)
    abas = {}
    for nome in xls.sheet_names[:10]:
        df = xls.parse(nome, nrows=LINHAS_LIDAS, dtype=str, header=None)
        df = _promove_cabecalho(df)
        abas[nome] = _resumo_df(df)
    principal = next(iter(abas.values()), {})
    return ParsedFile(tipo="planilha", metadados={"abas": abas, **principal})


def _promove_cabecalho(df):
    """Planilhas públicas costumam ter título nas primeiras linhas; o cabeçalho é a
    primeira linha com a maioria das células preenchidas."""
    for i in range(min(len(df), 15)):
        linha = df.iloc[i]
        if linha.notna().mean() >= 0.6:
            novo = df.iloc[i + 1:].copy()
            novo.columns = [str(c) for c in linha.fillna("")]
            return novo.reset_index(drop=True)
    return df


def _json(content: bytes, content_type: str | None) -> ParsedFile:
    data = json.loads(decode_bytes(content, content_type))
    registros = data if isinstance(data, list) else next(
        (v for v in data.values() if isinstance(v, list)), []) if isinstance(data, dict) else []
    chaves = sorted({k for r in registros[:50] if isinstance(r, dict) for k in r})
    return ParsedFile(tipo="json", metadados={
        "n_registros": len(registros), "colunas": chaves,
        "colunas_norm": [normalize_header(c) for c in chaves]})


def _zip(content: bytes) -> ParsedFile:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        nomes = z.namelist()
    return ParsedFile(tipo="zip", metadados={"arquivos": nomes[:100], "n_arquivos": len(nomes)})


def _resumo_df(df) -> dict[str, Any]:
    df = df.dropna(how="all")
    cols = [str(c) for c in df.columns]
    return {
        "colunas": cols,
        "colunas_norm": [normalize_header(c) for c in cols],
        "n_linhas_lidas": int(len(df)),
        "amostra": df.head(LINHAS_AMOSTRA).fillna("").astype(str).values.tolist(),
    }


_EXERC = re.compile(r"(?:exerc[ií]cio|ano)\s*(?:de|:)?\s*(20\d{2})", re.I)
_BIMESTRE = re.compile(r"(\d)\s*[ºo°]?\s*bimestre", re.I)
_QUADRI = re.compile(r"(\d)\s*[ºo°]?\s*quadrimestre", re.I)
_MES_A_MES = re.compile(
    r"(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
    r"\s*(?:a|-|/)\s*(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
    r"(?:\s*(?:de)?\s*(20\d{2}))?", re.I)


def _exercicios(texto: str) -> list[int]:
    return sorted({int(y) for y in _EXERC.findall(texto)}, reverse=True)[:5]


def _periodo(texto: str) -> dict[str, Any]:
    """Período de referência de relatórios fiscais (insumo de 20.2 / 12.2 — atualidade)."""
    out: dict[str, Any] = {}
    if m := _BIMESTRE.search(texto):
        out["bimestre"] = int(m.group(1))
    if m := _QUADRI.search(texto):
        out["quadrimestre"] = int(m.group(1))
    if m := _MES_A_MES.search(texto):
        out["meses"] = [m.group(1).lower(), m.group(2).lower()]
        if m.group(3):
            out["ano"] = int(m.group(3))
    return out
