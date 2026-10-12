"""Escopo de navegação, reserva de orçamento do portal, links em JavaScript e downloads sem extensão."""
import io

from ingestion.extract.crawler import Crawler, ext_da_resposta
from ingestion.load.load_postgres import LocalRepository
from ingestion.load.storage import LocalStorage
from ingestion.models import FetchResult
from ingestion.pipeline import run
from ingestion.transform.parse_html import parse_html
from tests.helpers import FakeRenderer, FakeSite, make_http, page, test_settings
from tests.test_crawler_pipeline import MemSink, ente

SITE = "https://www.cidade.com"                 # site em domínio não-gov (como o Wix de Cachoeiras)
BETHA = "https://transparencia.betha.cloud"
FALA = "https://falabr.cgu.gov.br"


def _pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(50, 800, "RELATORIO RESUMIDO DA EXECUCAO ORCAMENTARIA 3o Bimestre de 2026")
    c.save()
    return buf.getvalue()


def _crawl(site, settings=None, renderer=None, **ente_kw):
    sink = MemSink()
    settings = settings or test_settings(renderizar=renderer is not None)
    out = Crawler(ente(**ente_kw), make_http(site, settings), sink, settings,
                  renderer_factory=(lambda: renderer) if renderer else None).run()
    return out, sink, {s.url: s for s, _, _ in sink.pages}


# ------------------------------------------------------------------ item 1: portal descoberto

def test_portal_descoberto_em_outro_host_e_expandido():
    s = FakeSite()
    s.html(f"{SITE}/", page("Prefeitura", "", links=[(f"{BETHA}/p/ente1", "Portal da Transparência")]))
    s.html(f"{BETHA}/p/ente1", page("Portal da Transparência", "", links=[
        ("/p/ente1/despesas", "Despesas"), ("/p/ente1/receitas", "Receitas")]))
    s.html(f"{BETHA}/p/ente1/despesas", page("Despesas", "<p>Empenhos</p>"))
    s.html(f"{BETHA}/p/ente1/receitas", page("Receitas", "<p>Arrecadação</p>"))
    out, _, snaps = _crawl(s, urls={"site_oficial": f"{SITE}/"})          # sem portal no YAML
    assert f"{BETHA}/p/ente1/despesas" in snaps and f"{BETHA}/p/ente1/receitas" in snaps
    assert {"despesa", "receita"} <= set(out.stats.secoes_encontradas)
    assert ("portal_transparencia", "descoberta") in {(u.tipo, u.fonte) for u in out.urls}
    assert out.stats.fingerprint["transparencia.betha.cloud"]["portal"] is True


# ------------------------------------------------------------------ item 2: canal externo

def test_canal_externo_e_visitado_mas_nao_expandido():
    s = FakeSite()
    s.html(f"{SITE}/", page("Prefeitura", ""))
    s.html(f"{FALA}/web/rj/x/", page("Fala.BR — Acesso à Informação", "", links=[
        ("/web/rj/x/manifestacao", "Ouvidoria: registrar manifestação"),
        ("/web/rj/x/lai", "Lei de Acesso à Informação")]))
    out, _, snaps = _crawl(s, urls={"site_oficial": f"{SITE}/", "esic": f"{FALA}/web/rj/x/"})
    assert snaps[f"{FALA}/web/rj/x/"].coleta_status == "ok"               # semente coletada (evidência)
    assert not any(u.startswith(f"{FALA}/web/rj/x/") and u != f"{FALA}/web/rj/x/" for u in snaps)


# ------------------------------------------------------------------ item 3: reserva de orçamento

def test_site_institucional_nao_consome_orcamento_do_portal():
    s = FakeSite()
    noticias = [(f"/licitacoes-noticia-{i}", f"Licitações: notícia {i}") for i in range(20)]
    s.html(f"{SITE}/", page("Prefeitura", "", links=noticias + [(f"{BETHA}/p/e", "Portal da Transparência")]))
    for href, txt in noticias:
        s.html(f"{SITE}{href}", page(txt, ""))
    secoes = ["Despesas", "Receitas", "Servidores", "Contratos", "Obras", "Diárias", "Convênios", "Emendas"]
    s.html(f"{BETHA}/p/e", page("Portal da Transparência", "",
                                links=[(f"/p/e/{i}", t) for i, t in enumerate(secoes)]))
    for i, t in enumerate(secoes):
        s.html(f"{BETHA}/p/e/{i}", page(t, ""))
    cfg = test_settings(max_paginas=12, fracao_portal=0.5)
    out, _, snaps = _crawl(s, settings=cfg, urls={"site_oficial": f"{SITE}/"})
    portal = [u for u in snaps if u.startswith(BETHA)]
    assert len(snaps) == 12 and len(portal) >= 6        # metade reservada ao portal


# ------------------------------------------------------------------ item 4: links em JavaScript

def test_parse_html_recupera_destinos_de_onclick_data_e_select():
    html = """<html><body>
      <a href="javascript:void(0)" onclick="window.open('/transparencia/despesas','_blank')">Despesas</a>
      <a href="#" onclick="location.href = 'https://x.gov.br/receitas'">Receitas</a>
      <a href="javascript:abrirMenu()">Menu</a>
      <li onclick="navigateTo('/folha')" data-href="/servidores">Servidores</li>
      <button onclick="window.location.href='/licitacoes'">Licitações</button>
      <select onchange="location=this.value"><option value="">Selecione</option>
        <option value="/rreo">RREO</option><option value="2026">2026</option></select>
    </body></html>"""
    p = parse_html(html, "https://x.gov.br/")
    urls = {link.url: link.texto for link in p.links}
    assert urls["https://x.gov.br/transparencia/despesas"] == "Despesas"
    assert "https://x.gov.br/receitas" in urls and "https://x.gov.br/servidores" in urls
    assert "https://x.gov.br/licitacoes" in urls and urls["https://x.gov.br/rreo"] == "RREO"
    assert not any(u.endswith("/2026") for u in urls)
    assert p.links_javascript == 1                                       # só o "abrirMenu()" ficou sem destino


def test_pagina_com_menu_javascript_e_renderizada_e_host_passa_a_renderizar():
    menu_morto = "".join(f'<a href="javascript:void(0)">Item {i}</a>' for i in range(6))
    s = FakeSite()
    s.html(f"{SITE}/", page("Prefeitura", menu_morto))
    s.html(f"{SITE}/despesas", page("Despesas", ""))
    r = FakeRenderer({f"{SITE}/": page("Prefeitura", "", links=[("/despesas", "Despesas")])})
    out, _, snaps = _crawl(s, renderer=r, urls={"site_oficial": f"{SITE}/"})
    assert snaps[f"{SITE}/"].renderizado_js and f"{SITE}/despesas" in snaps


def test_sem_ganho_ao_renderizar_nao_tenta_de_novo_no_host():
    menu_morto = "".join(f'<a href="javascript:void(0)">Item {i}</a>' for i in range(6))
    s = FakeSite()
    s.html(f"{SITE}/", page("Prefeitura", menu_morto, links=[("/despesas", "Despesas")]))
    s.html(f"{SITE}/despesas", page("Despesas", menu_morto))
    r = FakeRenderer({f"{SITE}/": page("Prefeitura", menu_morto, links=[("/despesas", "Despesas")])})
    _crawl(s, renderer=r, urls={"site_oficial": f"{SITE}/"})
    assert r.calls == [f"{SITE}/"]                                        # despesas não foi renderizada


# ------------------------------------------------------------------ item 5: downloads sem extensão

def test_ext_da_resposta():
    def res(ct="", cd="", body=b"x", url="https://x/d?id=1"):
        return FetchResult(url=url, status=200, content=body, content_type=ct,
                           headers={"content-disposition": cd} if cd else {})
    assert ext_da_resposta(res(cd='attachment; filename="rreo_3bim.pdf"'), "https://x/d?id=1") == "pdf"
    assert ext_da_resposta(res(ct="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), "u") == "xlsx"
    assert ext_da_resposta(res(ct="application/octet-stream", body=b"%PDF-1.7"), "u") == "pdf"
    assert ext_da_resposta(res(ct="text/html; charset=utf-8"), "u") is None


def test_download_sem_extensao_herda_secao_e_html_vira_pagina():
    s = FakeSite()
    s.html(f"{SITE}/", page("Prefeitura", "", links=[("/contas", "Relatórios fiscais RREO")]))
    s.html(f"{SITE}/contas", page("Relatórios fiscais RREO", "", links=[
        ("/download?id=7", "Baixar"), ("/arquivos/lista", "Arquivo de relatórios do RREO")]))
    s.add(f"{SITE}/download?id=7", body=_pdf(),
          headers={"content-type": "application/octet-stream", "content-disposition": 'attachment; filename="r.pdf"'})
    s.html(f"{SITE}/arquivos/lista", page("Arquivo de relatórios RREO", "<p>lista</p>"))
    out, sink, snaps = _crawl(s, urls={"site_oficial": f"{SITE}/"})
    arts = {a.url: a for a, _, _ in sink.files}
    a = arts[f"{SITE}/download?id=7"]
    assert a.extensao == "pdf" and a.metadados["tipo"] == "pdf" and "rreo" in a.secoes   # seção herdada da página
    assert f"{SITE}/arquivos/lista" not in arts and f"{SITE}/arquivos/lista" in snaps    # era página, não arquivo


# ------------------------------------------------------------------ item 6: relatório por host

def test_relatorio_mostra_paginas_por_host(tmp_path):
    s = FakeSite()
    s.html(f"{SITE}/", page("Prefeitura", "", links=[(f"{BETHA}/p/e", "Portal da Transparência")]))
    s.html(f"{BETHA}/p/e", page("Portal da Transparência", "", links=[("/p/e/d", "Despesas")]))
    s.html(f"{BETHA}/p/e/d", page("Despesas", ""))
    cfg = test_settings(data_dir=tmp_path)
    resumo = run([ente(urls={"site_oficial": f"{SITE}/"})], LocalRepository(tmp_path / "l"),
                 LocalStorage(tmp_path / "o"), cfg, http_factory=lambda: make_http(s, cfg))
    rel = open(resumo["relatorio"], encoding="utf-8").read()
    assert "## Páginas por host" in rel
    assert "| rj-exemplo | transparencia.betha.cloud | 2 | 2 | sim |" in rel
    assert "| rj-exemplo | www.cidade.com | 1 | 1 |  |" in rel
