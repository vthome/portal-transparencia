"""Verificadores de dados estruturados: campos obrigatórios, ferramenta de pesquisa e exportação."""
from __future__ import annotations

import re

from compliance.checkers.base import Evidencia, ausencia, r, verificador
from compliance.coleta import ColetaEnte
from ingestion.transform.normalize import normalize_text

FORMATOS_MAQUINA = {"csv", "xlsx", "xls", "ods", "json", "xml"}
FORMATOS = FORMATOS_MAQUINA | {"pdf", "txt", "doc", "docx", "odt"}
_SINONIMO_FORMATO = {"planilha": "xlsx", "dados abertos": "csv"}


_NUM = re.compile(r"\b[nN]\s*[º°]")


def _casa(campos: dict[str, list[str]], cabecalhos: list[str]) -> dict[str, str]:
    """campo -> cabeçalho que o satisfaz (sinônimo normalizado contido no cabeçalho normalizado)."""
    # "Nº"/"N°" viraria "no" na normalização; troca por "numero" antes
    cab = [f" {normalize_text(_NUM.sub('numero', h))} " for h in cabecalhos if h]
    achados = {}
    for campo, sins in campos.items():
        for s in sins:
            alvo = normalize_text(s)
            if not alvo:
                continue
            hit = next((h.strip() for h in cab if f" {alvo}" in h), None)
            if hit:
                achados[campo] = hit
                break
    return achados


@verificador("structured_fields", versao="2")
def structured_fields(c, coleta: ColetaEnte, ctx):
    p = c.params
    campos: dict[str, list[str]] = p["campos"]
    secoes = p.get("secoes") or None
    total = len(campos)
    minimo = p.get("minimo_parcial") or (1 if total == 1 else max(1, total // 2))
    fontes = []                                    # (achados, Evidencia, forca)
    for pg in coleta.paginas_ok(secoes):
        for i, t in enumerate(pg.campo("tabelas")):
            ach = _casa(campos, t.get("cabecalhos_norm") or t.get("cabecalhos") or [])
            if ach:
                fontes.append((ach, Evidencia("tabela", url=pg.url, page_snapshot_id=pg.id,
                                              trecho=" | ".join(t.get("cabecalhos") or [])[:400],
                                              metadados={"tabela": i, "linhas": t.get("n_linhas"),
                                                         "amostra": (t.get("amostra") or [])[:2],
                                                         "trilha": pg.trilha}), 0.9))
    for a in coleta.arquivos_ok(secoes):
        cols = a.metadados.get("colunas_norm") or a.metadados.get("colunas") or []
        ach = _casa(campos, cols)
        if ach:
            fontes.append((ach, Evidencia("arquivo", url=a.url, file_artifact_id=a.id, page_snapshot_id=a.page_id,
                                          trecho=", ".join(cols)[:400],
                                          metadados={"extensao": a.extensao, "linhas": a.metadados.get("n_linhas_lidas")}),
                           0.85))
    q = coleta.qualidade(secoes)
    if fontes:
        fontes.sort(key=lambda f: -len(f[0]))
        ach, ev, forca = fontes[0]
        uniao = set().union(*(f[0] for f in fontes))
        n = len(uniao)
        detalhe = {"encontrados": sorted(uniao), "faltando": sorted(set(campos) - uniao),
                   "colunas": ach}
        ev.metadados.update(detalhe)
        evs = [ev] + [f[1] for f in fontes[1:3]]
        if n == total:
            return r("atende", f"todos os {total} campo(s) exigidos aparecem nos dados publicados.", evs,
                     forca=forca, fontes=min(len(fontes), 3), qualidade=q, **detalhe)
        if n >= minimo:
            return r("parcial", f"{n} de {total} campos encontrados; faltam {', '.join(detalhe['faltando'])}.",
                     evs, forca=forca - 0.1, fontes=min(len(fontes), 3), qualidade=q, **detalhe)
        return r("nao_atende", f"apenas {n} de {total} campos encontrados.", evs, forca=0.65, qualidade=q, **detalhe)

    paginas = coleta.paginas_ok(secoes)
    tem_tabela = any(pg.campo("tabelas") for pg in paginas)
    tem_form = any(pg.campo("formularios") for pg in paginas)
    if paginas and tem_tabela:
        return r("nao_atende", "a seção publica tabelas, mas sem os campos exigidos.",
                 [Evidencia("tabela", url=pg.url, page_snapshot_id=pg.id,
                            trecho=" | ".join((pg.campo("tabelas")[0].get("cabecalhos") or []))[:300])
                  for pg in paginas if pg.campo("tabelas")][:2], forca=0.7, qualidade=q)
    if paginas and tem_form:
        return r("nao_verificavel", "os dados da seção só aparecem após uma consulta (formulário); o robô não "
                 "submete pesquisas nesta versão.", [Evidencia("formulario", url=paginas[0].url,
                                                               page_snapshot_id=paginas[0].id)],
                 forca=0.0, qualidade=q)
    return ausencia(coleta, secoes, "tabela com os campos exigidos")


def _forms_relevantes(paginas):
    for pg in paginas:
        for f in pg.campo("formularios"):
            campos = [x for x in f.get("campos", []) if x.get("tipo") not in {"checkbox", "radio"}]
            yield pg, f, campos


@verificador("search_filter")
def search_filter(c, coleta: ColetaEnte, ctx):
    p = c.params
    if p.get("escopo") == "site":
        paginas = [x for x in (coleta.capa(), coleta.pagina_inicial_portal()) if x is not None and x.ok] \
            or coleta.paginas_ok()[:5]
        for pg, f, campos in _forms_relevantes(paginas):
            if f.get("parece_busca"):
                return r("atende", f"campo de busca em \"{pg.titulo}\".",
                         [Evidencia("formulario", url=pg.url, page_snapshot_id=pg.id,
                                    metadados={"action": f.get("action"), "campos": [x.get("nome") for x in campos]})],
                         forca=0.85)
        if paginas:
            return r("nao_atende", "nenhuma ferramenta de busca encontrada na capa do site ou do portal.",
                     [Evidencia("ausencia", url=x.url, page_snapshot_id=x.id) for x in paginas[:2]], forca=0.7)
        return r("nao_verificavel", "capa e portal não coletados.", forca=0.0)
    secoes = p.get("secoes") or None
    paginas = coleta.paginas_ok(secoes)
    melhor = None
    for pg, f, campos in _forms_relevantes(paginas):
        filtros = [x for x in campos if x.get("tipo") in {"select", "date", "text", "search", "number", "month"}]
        if melhor is None or len(filtros) > len(melhor[2]):
            melhor = (pg, f, filtros)
    q = coleta.qualidade(secoes)
    if melhor and melhor[2]:
        pg, f, filtros = melhor
        ev = [Evidencia("formulario", url=pg.url, page_snapshot_id=pg.id,
                        metadados={"campos": [x.get("rotulo") or x.get("nome") for x in filtros][:12],
                                   "action": f.get("action")})]
        if len(filtros) >= 2:
            return r("atende", f"formulário com {len(filtros)} filtros em \"{pg.titulo}\".", ev, forca=0.85, qualidade=q)
        return r("parcial", f"apenas um filtro disponível em \"{pg.titulo}\".", ev, forca=0.7, qualidade=q)
    return ausencia(coleta, secoes, "ferramenta de pesquisa com filtros")


@verificador("export")
def export(c, coleta: ColetaEnte, ctx):
    secoes = c.params.get("secoes") or None
    paginas = coleta.paginas_ok(secoes)
    formatos: dict[str, Evidencia] = {}
    generico = None
    for pg in paginas:
        for b in pg.campo("botoes_exportacao"):
            fmt = _SINONIMO_FORMATO.get(b, b)
            if fmt in FORMATOS:
                formatos.setdefault(fmt, Evidencia("link", url=pg.url, page_snapshot_id=pg.id, trecho=f"botão {b}"))
            elif generico is None:
                generico = Evidencia("link", url=pg.url, page_snapshot_id=pg.id, trecho=f"opção \"{b}\"")
        for lk in pg.links or []:
            if lk.get("arquivo") in FORMATOS:
                formatos.setdefault(lk["arquivo"], Evidencia("link", url=lk["url"], page_snapshot_id=pg.id,
                                                             trecho=lk.get("texto")))
    for a in coleta.arquivos_ok(secoes):
        if a.extensao in FORMATOS:
            formatos.setdefault(a.extensao, Evidencia("arquivo", url=a.url, file_artifact_id=a.id,
                                                      page_snapshot_id=a.page_id, trecho=a.texto_link))
    q = coleta.qualidade(secoes)
    maquina = sorted(set(formatos) & FORMATOS_MAQUINA)
    detalhe = {"formatos": sorted(formatos), "legiveis_por_maquina": maquina}
    if len(formatos) >= 2 and maquina:
        return r("atende", f"relatórios em {', '.join(sorted(formatos))}.", list(formatos.values())[:4],
                 forca=0.85, fontes=min(len(formatos), 3), qualidade=q, **detalhe)
    if formatos or generico:
        evs = list(formatos.values())[:3] + ([generico] if generico else [])
        return r("parcial", f"exportação limitada ({', '.join(sorted(formatos)) or 'formato não identificado'}).",
                 evs, forca=0.65, qualidade=q, **detalhe)
    return ausencia(coleta, secoes, "opção de gravação/exportação")
