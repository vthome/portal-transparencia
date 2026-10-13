"""Verificadores de acessibilidade, formulário do e-SIC e quesitos pendentes."""
from __future__ import annotations

import re

from compliance.checkers.base import Evidencia, ausencia, r, verificador
from compliance.coleta import ColetaEnte
from ingestion.transform.normalize import normalize_text

_SINAL = {
    "alto_contraste": lambda a: a.get("alto_contraste"),
    "ajuste_fonte": lambda a: a.get("ajuste_fonte"),
    "simbolo": lambda a: a.get("simbolo_acessibilidade") or a.get("vlibras"),
}


@verificador("accessibility")
def accessibility(c, coleta: ColetaEnte, ctx):
    sinal = c.params["sinal"]
    alvos = {"site oficial": coleta.capa(), "portal": coleta.pagina_inicial_portal()}
    alvos = {k: v for k, v in alvos.items() if v is not None and v.ok}
    if not alvos:
        return r("nao_verificavel", "capa do site e portal não coletados.", forca=0.0, qualidade=0.0)
    presentes, ev = [], []
    for nome, pg in alvos.items():
        if sinal == "breadcrumb":
            # o caminho aparece nas páginas internas; vale qualquer página do mesmo host
            internas = [x for x in coleta.paginas_ok() if x.host == pg.host and x.campo("breadcrumbs")]
            ok = bool(internas)
            if ok:
                ev.append(Evidencia("trecho", url=internas[0].url, page_snapshot_id=internas[0].id,
                                    trecho=" > ".join(internas[0].campo("breadcrumbs")), metadados={"onde": nome}))
        else:
            ok = bool(_SINAL[sinal](pg.extraido.get("acessibilidade") or {}))
            if ok:
                ev.append(Evidencia("trecho", url=pg.url, page_snapshot_id=pg.id,
                                    trecho=f"{sinal.replace('_', ' ')} detectado", metadados={"onde": nome}))
        if ok:
            presentes.append(nome)
    if len(presentes) == len(alvos):
        return r("atende", f"{sinal.replace('_', ' ')} presente em: {', '.join(presentes)}.", ev,
                 forca=0.8, fontes=len(presentes))
    if presentes:
        faltando = [k for k in alvos if k not in presentes]
        return r("parcial", f"presente em {', '.join(presentes)}; ausente em {', '.join(faltando)}.", ev, forca=0.7)
    return r("nao_atende", f"{sinal.replace('_', ' ')} não detectado em {', '.join(alvos)}.",
             [Evidencia("ausencia", url=pg.url, page_snapshot_id=pg.id) for pg in alvos.values()], forca=0.7)


_PROIBIDOS = re.compile(r"\b(motivo|justificativa|finalidade|razao do pedido|motivacao)\b")
_PESADOS = re.compile(r"\b(rg|identidade|comprovante|anexo|anexar|upload|documento de identificacao|certidao)\b")
_LOGIN = re.compile(r"\b(login|senha|cadastre se|faca seu cadastro|entrar com gov br|acesso gov br)\b")


@verificador("formulario_esic")
def formulario_esic(c, coleta: ColetaEnte, ctx):
    """LAI art. 10, §1º: identificação pode ser pedida; motivação, não."""
    secoes = c.params.get("secoes") or ["sic"]
    urls_esic = {u.rstrip("/") for u in coleta.urls_do_tipo("esic")}
    paginas = {p.id: p for p in coleta.paginas_ok(secoes)}
    paginas.update({p.id: p for p in coleta.paginas_ok() if p.url.rstrip("/") in urls_esic})
    forms = [(pg, f) for pg in paginas.values() for f in pg.campo("formularios") if len(f.get("campos", [])) >= 3]
    if forms:
        pg, f = max(forms, key=lambda x: len(x[1].get("campos", [])))
        obrig = [normalize_text(f"{x.get('rotulo') or ''} {x.get('nome') or ''}") for x in f["campos"] if x.get("obrigatorio")]
        proib = [o for o in obrig if _PROIBIDOS.search(o)]
        pesados = [o for o in obrig if _PESADOS.search(o)]
        ev = [Evidencia("formulario", url=pg.url, page_snapshot_id=pg.id,
                        metadados={"obrigatorios": obrig, "proibidos": proib, "pesados": pesados})]
        if proib:
            return r("nao_atende", f"o formulário exige motivação do pedido ({', '.join(proib)}).", ev, forca=0.75)
        if pesados:
            return r("parcial", f"o formulário exige documentos/anexos ({', '.join(pesados)}).", ev, forca=0.6)
        return r("atende", f"formulário com {len(obrig)} campo(s) obrigatório(s), sem exigência de motivação.",
                 ev, forca=0.65)
    login = [pg for pg in paginas.values() if _LOGIN.search(normalize_text(pg.texto[:5000]))]
    if login:
        return r("nao_verificavel", "o e-SIC exige login/cadastro antes do formulário; o robô não cria contas.",
                 [Evidencia("trecho", url=login[0].url, page_snapshot_id=login[0].id, trecho="exige login/cadastro")],
                 forca=0.0)
    return ausencia(coleta, secoes, "formulário de pedido do e-SIC")


@verificador("pendente")
def pendente(c, coleta: ColetaEnte, ctx):
    res = r("nao_verificavel", f"quesito ainda não automatizado: {c.params.get('motivo', 'sem verificador')}.",
            forca=0.0, metodo="pendente")
    return res
