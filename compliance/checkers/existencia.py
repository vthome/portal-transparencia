"""Verificadores de existência: portal, link na capa, página/conteúdo, canal eletrônico."""
from __future__ import annotations

import re

from compliance.checkers.base import Evidencia, ausencia, r, verificador
from compliance.coleta import ColetaEnte, Pagina, trecho
from ingestion.transform.normalize import find_terms, host_of, is_gov_domain


@verificador("portal")
def portal(c, coleta: ColetaEnte, ctx):
    capa, portal_ini = coleta.capa(), coleta.pagina_inicial_portal()
    ev = []
    for p, tipo in ((capa, "site_oficial"), (portal_ini, "portal_transparencia")):
        if p is not None and p.ok:
            ev.append(Evidencia("link", url=p.url, page_snapshot_id=p.id, trecho=p.titulo,
                                metadados={"tipo": tipo, "dominio_gov": is_gov_domain(p.url)}))
    if ev:
        gov = all(e.metadados["dominio_gov"] for e in ev)
        obs = "" if gov else " Atenção: domínio fora de .gov.br."
        return r("atende", f"{'Site oficial e portal' if len(ev) == 2 else 'Sítio'} acessível(is).{obs}", ev,
                 forca=0.95, fontes=len(ev), dominio_gov=gov)
    return r("nao_verificavel", "nem a capa do site nem o portal puderam ser coletados nesta rodada.",
             forca=0.0, qualidade=0.0)


@verificador("homepage_link")
def homepage_link(c, coleta: ColetaEnte, ctx):
    capa = coleta.capa()
    if capa is None or not capa.ok:
        return r("nao_verificavel", "a capa do site não foi coletada.", forca=0.0, qualidade=0.0)
    hosts = coleta.hosts_portal
    achados = [lk for lk in capa.links or []
               if find_terms(lk.get("texto", ""), ["transparencia", "portal da transparencia"])
               or host_of(lk.get("url", "")) in hosts]
    shot = [Evidencia("screenshot", url=capa.url, page_snapshot_id=capa.id, screenshot_path=capa.screenshot_path)] \
        if capa.screenshot_path else []
    if not achados:
        return r("nao_atende", "nenhum link para o portal da transparência na capa do site.",
                 [Evidencia("ausencia", url=capa.url, page_snapshot_id=capa.id)] + shot, forca=0.8)
    topo = [lk for lk in achados if lk.get("na_capa_topo")]
    lk = (topo or achados)[0]
    ev = [Evidencia("link", url=lk["url"], page_snapshot_id=capa.id, trecho=lk.get("texto"),
                    metadados={"pagina": capa.url, "destaque": bool(topo)})] + shot
    if topo:
        return r("atende", f"link \"{lk.get('texto')}\" no cabeçalho/menu da capa.", ev, forca=0.9,
                 fontes=1 + bool(shot))
    return r("parcial", f"link \"{lk.get('texto')}\" existe na capa, mas fora do cabeçalho/menu.", ev, forca=0.7)


def _paginas_rotulo(coleta: ColetaEnte, termos: list[str]) -> list[Pagina]:
    return [p for p in coleta.paginas_ok() if find_terms(p.rotulo, termos)]


@verificador("existence", versao="2")
def existence(c, coleta: ColetaEnte, ctx):
    p = c.params
    termos, secoes, regexes = p.get("termos", []), p.get("secoes") or None, p.get("regex") or []
    escopo_site = p.get("escopo") == "site"
    por_rotulo = _paginas_rotulo(coleta, termos)
    da_secao = coleta.paginas_ok(secoes) if secoes else []
    # escopo site + padrão textual (endereço, telefone...): costuma estar no rodapé de qualquer página
    extras = coleta.paginas_ok() if escopo_site and regexes else \
        [x for x in (coleta.capa(), coleta.pagina_inicial_portal()) if x is not None and x.ok] if escopo_site else []
    candidatas = list({x.id: x for x in por_rotulo + da_secao + extras}.values())
    q = coleta.qualidade(secoes)

    if regexes:
        melhor, acertos = None, []
        for pg in candidatas:
            hits = [rx for rx in regexes if re.search(rx, pg.texto or "", re.I)]
            if len(hits) > len(acertos) or (len(hits) == len(acertos) and pg in por_rotulo and melhor not in por_rotulo):
                melhor, acertos = pg, hits
        if melhor is not None and acertos:
            ev = [Evidencia("trecho", url=melhor.url, page_snapshot_id=melhor.id,
                            trecho=trecho(melhor.texto, acertos[0]), metadados={"titulo": melhor.titulo})]
            forca = 0.85 if melhor in por_rotulo else 0.7
            if len(acertos) == len(regexes):
                return r("atende", f"informação encontrada em \"{melhor.titulo}\".", ev, forca=forca, qualidade=q)
            return r("parcial", f"{len(acertos)} de {len(regexes)} elementos encontrados em \"{melhor.titulo}\".",
                     ev, forca=forca - 0.1, qualidade=q)
        if candidatas:
            return r("nao_atende", "páginas relacionadas coletadas, mas sem a informação exigida.",
                     [Evidencia("ausencia", url=x.url, page_snapshot_id=x.id) for x in candidatas[:3]],
                     forca=0.6, qualidade=q)
        return ausencia(coleta, secoes, "informação exigida")

    if por_rotulo:
        pg = por_rotulo[0]
        return r("atende", f"página \"{pg.titulo}\" corresponde ao quesito.",
                 [Evidencia("link", url=pg.url, page_snapshot_id=pg.id, trecho=pg.titulo,
                            metadados={"trilha": pg.trilha})], forca=0.9, fontes=min(len(por_rotulo), 3), qualidade=q)
    for pg in (coleta.paginas_ok() if escopo_site else da_secao):
        if find_terms(pg.texto[:20000], termos):
            return r("atende", f"conteúdo correspondente no texto de \"{pg.titulo}\".",
                     [Evidencia("trecho", url=pg.url, page_snapshot_id=pg.id, trecho=trecho(pg.texto, termos=termos))],
                     forca=0.7, qualidade=q)
    for pg, lk in coleta.links():
        if find_terms(lk.get("texto", ""), termos):
            return r("atende", f"link \"{lk.get('texto')}\" encontrado; a página de destino não foi coletada.",
                     [Evidencia("link", url=lk.get("url"), page_snapshot_id=pg.id, trecho=lk.get("texto"),
                                metadados={"pagina_origem": pg.url})], forca=0.55, qualidade=q)
    return ausencia(coleta, secoes, "conteúdo do quesito")


@verificador("canal_eletronico")
def canal_eletronico(c, coleta: ColetaEnte, ctx):
    p = c.params
    urls = coleta.urls_do_tipo(*p.get("tipo_url", []))
    ev = []
    for u in urls:
        snap = next((pg for pg in coleta.paginas if pg.url.rstrip("/") == u.rstrip("/")), None)
        if snap is not None and snap.ok:
            return r("atende", f"canal eletrônico acessível em {u}.",
                     [Evidencia("link", url=u, page_snapshot_id=snap.id, trecho=snap.titulo)], forca=0.85, fontes=2)
        ev.append(Evidencia("link", url=u, metadados={"coletado": snap is not None, "status": snap.status if snap else None}))
    res = existence(type("C", (), {"params": {"termos": p.get("termos", []), "secoes": p.get("secoes")}})(),
                    coleta, ctx)
    if res.status in {"atende", "parcial"}:
        res.evidencias = ev + res.evidencias
        res.sinais["fontes"] = res.sinais.get("fontes", 1) + bool(ev)
        return res
    if ev:
        return r("atende", "canal eletrônico cadastrado/descoberto, mas a página não pôde ser lida pelo robô.",
                 ev, forca=0.55)
    return res
