"""Verificadores de documentos, atualidade e histórico."""
from __future__ import annotations

import calendar
import re
from datetime import date

from compliance.checkers.base import Evidencia, ausencia, r, verificador
from compliance.coleta import ColetaEnte
from ingestion.transform.normalize import find_terms

DOC_EXT = {"pdf", "doc", "docx", "odt", "xls", "xlsx", "ods", "csv", "zip"}
_MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
          "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
_ANO = re.compile(r"(?<!\d)(20[0-4]\d)(?!\d)")


def _grupos_ok(texto: str, grupos: list[list[str]]) -> bool:
    return all(find_terms(texto, g) for g in grupos)


@verificador("document", versao="2")
def document(c, coleta: ColetaEnte, ctx):
    p = c.params
    grupos, secoes = p["grupos"], p.get("secoes") or None
    q = coleta.qualidade(secoes)
    # 1) arquivo baixado e lido cujo rótulo/conteúdo corresponde ao documento
    arquivos = sorted(coleta.arquivos_ok(), key=lambda a: not (set(a.secoes) & set(secoes or [])))
    for a in arquivos:
        alvo = f"{a.rotulo.replace('/', ' ').replace('-', ' ').replace('_', ' ')} {a.texto[:3000]}"
        if _grupos_ok(alvo, grupos):
            per = a.metadados.get("periodo") or {}
            return r("atende", f"documento publicado: \"{a.texto_link or a.url}\".",
                     [Evidencia("arquivo", url=a.url, file_artifact_id=a.id, page_snapshot_id=a.page_id,
                                trecho=(a.texto or "")[:300],
                                metadados={"extensao": a.extensao, "paginas": a.metadados.get("paginas"),
                                           "exercicios": a.metadados.get("exercicios"), "periodo": per})],
                     forca=0.9, fontes=2, qualidade=q)
    # 2) link para documento (não baixado: teto de arquivos, erro ou fora do escopo)
    for pg, lk in coleta.links():
        alvo = f"{lk.get('texto', '')} {lk.get('url', '').replace('/', ' ').replace('-', ' ').replace('_', ' ')}"
        if _grupos_ok(alvo, grupos) and (lk.get("arquivo") in DOC_EXT or find_terms(lk.get("texto", ""),
                                                                                     ["baixar", "download", "pdf"])):
            return r("atende", f"link para o documento: \"{lk.get('texto')}\" (arquivo não baixado nesta rodada).",
                     [Evidencia("link", url=lk.get("url"), page_snapshot_id=pg.id, trecho=lk.get("texto"),
                                metadados={"pagina_origem": pg.url})], forca=0.6, qualidade=q)
    # 3) página sobre o documento, sem o arquivo
    for pg in coleta.paginas_ok():
        if _grupos_ok(pg.rotulo, grupos):
            return r("parcial", f"página \"{pg.titulo}\" trata do documento, mas o arquivo não foi localizado.",
                     [Evidencia("link", url=pg.url, page_snapshot_id=pg.id, trecho=pg.titulo)], forca=0.5, qualidade=q)
    return ausencia(coleta, secoes, "documento")


# ------------------------------------------------------------------ atualidade

def _fim_periodo(per: dict, exercicios: list[int] | None) -> date | None:
    ano = per.get("ano") or (exercicios[0] if exercicios else None)
    if not ano:
        return None
    mes = None
    if per.get("bimestre"):
        mes = 2 * int(per["bimestre"])
    elif per.get("quadrimestre"):
        mes = 4 * int(per["quadrimestre"])
    elif per.get("meses"):
        mes = _MESES.get(per["meses"][-1])
    if not mes or not 1 <= mes <= 12:
        return None
    return date(int(ano), mes, calendar.monthrange(int(ano), mes)[1])


def _datas_secao(coleta: ColetaEnte, secoes, filtro, ref: date) -> list[tuple[date, str, Evidencia]]:
    out = []
    for pg in coleta.paginas_ok(secoes, filtro):
        for iso in pg.campo("datas_atualizacao"):
            d = date.fromisoformat(iso)
            out.append((d, "atualizado em", Evidencia("data", url=pg.url, page_snapshot_id=pg.id,
                                                       trecho=f"atualizado em {d:%d/%m/%Y}")))
        for iso in pg.campo("datas")[:10]:
            d = date.fromisoformat(iso)
            out.append((d, "data na página", Evidencia("data", url=pg.url, page_snapshot_id=pg.id,
                                                        trecho=f"data {d:%d/%m/%Y} em \"{pg.titulo}\"")))
    for a in coleta.arquivos_ok(secoes, filtro):
        fim = _fim_periodo(a.metadados.get("periodo") or {}, a.metadados.get("exercicios"))
        if fim:
            out.append((fim, "período de referência", Evidencia("arquivo", url=a.url, file_artifact_id=a.id,
                                                                 trecho=f"período até {fim:%d/%m/%Y}",
                                                                 metadados={"periodo": a.metadados.get("periodo")})))
        for iso in (a.metadados.get("datas") or [])[:5]:
            d = date.fromisoformat(iso)
            out.append((d, "data no documento", Evidencia("arquivo", url=a.url, file_artifact_id=a.id,
                                                          trecho=f"data {d:%d/%m/%Y}")))
    return [x for x in out if x[0] <= ref]


@verificador("freshness", versao="2")
def freshness(c, coleta: ColetaEnte, ctx):
    p = c.params
    secoes, filtro = p.get("secoes") or None, p.get("filtro") or None
    ref_tipo = p.get("referencia", "mensal")
    limite = int(ctx.cfg["atualidade_dias"][ref_tipo])
    datas = _datas_secao(coleta, secoes, filtro, ctx.data_ref)
    q = coleta.qualidade(secoes)
    if not datas:
        if coleta.situacao(secoes) == "coberta":
            return r("nao_verificavel", "a seção foi coletada, mas nenhuma data de atualização foi identificada.",
                     forca=0.0, qualidade=q)
        return ausencia(coleta, secoes, "informação datada")
    # "atualizado em" e período de documento valem mais que datas soltas no texto
    pref = [x for x in datas if x[1] in {"atualizado em", "período de referência"}] or datas
    d, origem, ev = max(pref, key=lambda x: x[0])
    atraso = (ctx.data_ref - d).days
    det = {"data_mais_recente": d.isoformat(), "origem": origem, "atraso_dias": atraso, "limite_dias": limite,
           "referencia": ref_tipo}
    ev.metadados.update(det)
    forca = 0.85 if origem != "data na página" else 0.6
    if atraso <= limite:
        return r("atende", f"informação de {d:%d/%m/%Y} ({origem}); dentro do prazo {ref_tipo} ({limite} dias).",
                 [ev], forca=forca, qualidade=q, **det)
    if atraso <= 2 * limite:
        return r("parcial", f"informação mais recente de {d:%d/%m/%Y}: {atraso} dias, acima do prazo {ref_tipo}.",
                 [ev], forca=forca - 0.1, qualidade=q, **det)
    return r("nao_atende", f"informação mais recente de {d:%d/%m/%Y}: {atraso} dias de atraso.",
             [ev], forca=forca, qualidade=q, **det)


# ------------------------------------------------------------------ histórico

@verificador("history", versao="2")
def history(c, coleta: ColetaEnte, ctx):
    p = c.params
    secoes, filtro = p.get("secoes") or None, p.get("filtro") or None
    ref = ctx.data_ref
    janela = range(ref.year - 8, ref.year)
    anos: dict[int, Evidencia] = {}
    for pg in coleta.paginas_ok(secoes, filtro):
        textos = [pg.titulo or "", *pg.campo("h1"), *pg.campo("headings")] + \
                 [lk.get("texto", "") for lk in pg.links or []]
        for t in textos:
            for y in map(int, _ANO.findall(t)):
                if y in janela:
                    anos.setdefault(y, Evidencia("trecho", url=pg.url, page_snapshot_id=pg.id, trecho=t[:160]))
        for f in pg.campo("formularios"):          # seletor de exercício com vários anos
            for campo in f.get("campos", []):
                if campo.get("tipo") == "select" and campo.get("opcoes", 0) >= 3 and \
                        find_terms(f"{campo.get('rotulo') or ''} {campo.get('nome') or ''}", ["ano", "exercicio", "competencia"]):
                    anos.setdefault(-1, Evidencia("formulario", url=pg.url, page_snapshot_id=pg.id,
                                                  trecho=f"seletor \"{campo.get('rotulo') or campo.get('nome')}\" "
                                                         f"com {campo.get('opcoes')} opções"))
        for d in pg.campo("datas"):
            y = int(d[:4])
            if y in janela:
                anos.setdefault(y, Evidencia("data", url=pg.url, page_snapshot_id=pg.id, trecho=d))
    for a in coleta.arquivos_ok(secoes, filtro):
        for y in (a.metadados.get("exercicios") or []):
            if int(y) in janela:
                anos.setdefault(int(y), Evidencia("arquivo", url=a.url, file_artifact_id=a.id,
                                                  trecho=f"exercício {y}: {a.texto_link or ''}"[:160]))
        for y in map(int, _ANO.findall(a.rotulo)):
            if y in janela:
                anos.setdefault(y, Evidencia("arquivo", url=a.url, file_artifact_id=a.id, trecho=(a.texto_link or a.url)[:160]))
    seletor = anos.pop(-1, None)
    q = coleta.qualidade(secoes)
    n = len(anos) + (2 if seletor else 0)
    det = {"anos_anteriores": sorted(anos), "seletor_de_exercicio": bool(seletor)}
    evs = ([seletor] if seletor else []) + [anos[y] for y in sorted(anos, reverse=True)[:3]]
    if n >= 2:
        return r("atende", f"informações de exercícios anteriores: {', '.join(map(str, sorted(anos))) or 'seletor de exercício'}.",
                 evs, forca=0.75 if seletor or len(anos) >= 3 else 0.65, fontes=min(len(evs), 3), qualidade=q, **det)
    if n == 1:
        return r("parcial", f"apenas um exercício anterior identificado ({', '.join(map(str, sorted(anos)))}).",
                 evs, forca=0.55, qualidade=q, **det)
    return ausencia(coleta, secoes, "histórico de exercícios anteriores")

