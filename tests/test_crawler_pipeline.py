"""Testes de integração offline: crawler + pipeline sobre um município falso."""
import io
from dataclasses import replace

from ingestion.extract.crawler import Crawler
from ingestion.load.load_postgres import LocalRepository
from ingestion.load.storage import LocalStorage, SupabaseStorage
from ingestion.models import Entity
from ingestion.pipeline import run
from tests.helpers import FakeRenderer, FakeSite, make_http, page, test_settings

SITE = "https://www.exemplo.rj.gov.br"
PORTAL = "https://transparencia.exemplo.rj.gov.br"
SPA = "https://spa.exemplo.rj.gov.br"


def _pdf_rreo() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(50, 800, "RELATORIO RESUMIDO DA EXECUCAO ORCAMENTARIA - 4o Bimestre de 2026")
    c.drawString(50, 780, "Julho a Agosto de 2026")
    c.save()
    return buf.getvalue()


PDF = _pdf_rreo()


def montar_site(etag_pdf: str | None = None) -> FakeSite:
    s = FakeSite()
    s.html(f"{SITE}/", page("Prefeitura de Exemplo", "", links=[
        (f"{PORTAL}/", "Portal da Transparência"),
        ("https://esic.exemplo.rj.gov.br/", "e-SIC"),
        ("/ouvidoria", "Ouvidoria"),
        ("/noticias", "Notícias"),
        ("https://www.facebook.com/prefeitura", "Facebook"),
        (f"{SPA}/#/inicio", "Dados Abertos"),
    ], extra='<link href="/wp-content/themes/a.css">'))
    s.html(f"{SITE}/ouvidoria", page("Ouvidoria", "<p>Endereço: Rua A, 1. Horário: 9h às 17h. Telefone (21) 0000-0000</p>"))
    s.html(f"{SITE}/noticias", page("Notícias", "", links=[(f"/noticia/{i}", f"Festa {i}") for i in range(30)]))
    for i in range(30):
        s.html(f"{SITE}/noticia/{i}", page(f"Festa {i}", ""))
    s.html("https://esic.exemplo.rj.gov.br/", page("e-SIC — Acesso à Informação", "<form><input name='nome' required></form>"))

    s.robots(PORTAL, "User-agent: *\nDisallow: /privado/\n")
    s.html(f"{PORTAL}/", page("Portal da Transparência", "", links=[
        ("/despesas", "Despesas"), ("/receitas", "Receitas"), ("/servidores", "Servidores e Folha de Pagamento"),
        ("/licitacoes", "Licitações"), ("/contas", "RREO e RGF"), ("/privado/painel", "Painel interno de despesas"),
        ("/quebrado", "Obras públicas"), ("/agenda", "Agenda cultural"),
    ]))
    s.html(f"{PORTAL}/despesas", page("Despesas", "<table><tr><th>Empenho</th><th>Valor Pago</th></tr>"
                                                  "<tr><td>1</td><td>R$ 1,00</td></tr></table>",
                                      links=[("/despesas.csv", "Exportar CSV")]))
    s.add(f"{PORTAL}/despesas.csv", body="Empenho;Valor Pago\n1;1,00\n", headers={"content-type": "text/csv"})
    s.html(f"{PORTAL}/receitas", page("Receitas", "<p>Receita arrecadada</p>"))
    s.html(f"{PORTAL}/servidores", page("Servidores", "<p>Remuneração</p>"))
    s.html(f"{PORTAL}/licitacoes", page("Licitações", "<p>Pregão 1/2026</p>"))
    s.html(f"{PORTAL}/agenda", page("Agenda cultural", "", links=[("/agenda/show", "Show")]))
    s.html(f"{PORTAL}/contas", page("Relatórios fiscais", "", links=[("/rreo-4bim-2026.pdf", "RREO 4º bimestre 2026")]))
    if etag_pdf:
        def pdf_handler(req):
            if req.headers.get("If-None-Match") == etag_pdf:
                return 304, {}, b""
            return 200, {"content-type": "application/pdf", "etag": etag_pdf}, PDF
        s.handler(f"{PORTAL}/rreo-4bim-2026.pdf", pdf_handler)
    else:
        s.add(f"{PORTAL}/rreo-4bim-2026.pdf", body=PDF, headers={"content-type": "application/pdf"})
    # SPA: HTML estático vazio; conteúdo só aparece após JS (FakeRenderer)
    s.html(f"{SPA}/", '<html><body><div id="root"></div><script src="a.js"></script></body></html>')
    return s


RENDER = {f"{SPA}/#/inicio": page("Dados Abertos", "<p>Conjuntos de dados abertos do município</p>")}


def ente(**kw) -> Entity:
    base = Entity(id="rj-exemplo", nome="Prefeitura de Exemplo", municipio="Exemplo", uf="RJ",
                  tipo_entidade="prefeitura", poder="executivo", esfera="municipal", codigo_ibge="3300000",
                  urls={"site_oficial": f"{SITE}/", "portal_transparencia": f"{PORTAL}/"},
                  dominios_extra=[])
    return replace(base, **kw)


class MemSink:
    def __init__(self):
        self.pages, self.files = [], []

    def save_page(self, snap, html, shot):
        self.pages.append((snap, html, shot))
        return f"p{len(self.pages)}"

    def save_file(self, art, content, page_id):
        self.files.append((art, content, page_id))
        return f"f{len(self.files)}"

    def previous_page(self, url):
        return None

    def previous_file(self, url):
        return None


def crawl(settings=None, renderer=None, site=None, **ente_kw):
    site = site or montar_site()
    sink = MemSink()
    settings = settings or test_settings(renderizar=renderer is not None)
    c = Crawler(ente(**ente_kw), make_http(site, settings), sink, settings,
                renderer_factory=(lambda: renderer) if renderer else None)
    out = c.run()
    return out, sink, site


def _por_url(sink):
    return {s.url: s for s, _, _ in sink.pages}


# ------------------------------------------------------------------ crawler

def test_crawler_encontra_secoes_e_registra_trilha():
    out, sink, site = crawl()
    snaps = _por_url(sink)
    assert {"despesa", "receita", "pessoal", "licitacoes", "rreo", "ouvidoria", "sic"} <= set(out.stats.secoes_encontradas)
    d = snaps[f"{PORTAL}/despesas"]
    assert d.coleta_status == "ok" and "despesa" in d.secoes
    assert d.trilha == [f"{SITE}/", f"{PORTAL}/", f"{PORTAL}/despesas"]    # caminho de navegação (quesito 4.1)
    assert d.extraido["tabelas"][0]["cabecalhos_norm"] == ["empenho", "valor pago"]


def test_crawler_nao_sai_do_dominio_e_nao_segue_irrelevante():
    out, sink, site = crawl()
    assert not any("facebook.com" in c for c in site.calls)
    noticias = [u for u in _por_url(sink) if "/noticia/" in u]
    assert noticias == []          # 'Festa N' não tem relação com nenhuma seção
    assert f"{PORTAL}/agenda/show" not in _por_url(sink)


def test_crawler_registra_robots_e_link_quebrado_como_evidencia():
    out, sink, _ = crawl()
    snaps = _por_url(sink)
    assert snaps[f"{PORTAL}/privado/painel"].coleta_status == "bloqueado_robots"
    q = snaps[f"{PORTAL}/quebrado"]
    assert q.coleta_status == "erro" and q.http_status == 404 and "obras" in q.secoes
    assert out.stats.bloqueios_robots == 1
    assert out.stats.status == "parcial"          # houve bloqueio por robots


def test_crawler_baixa_e_extrai_arquivos_relevantes():
    out, sink, _ = crawl()
    arts = {a.url: a for a, _, _ in sink.files}
    rreo = arts[f"{PORTAL}/rreo-4bim-2026.pdf"]
    assert rreo.metadados["tipo"] == "pdf" and rreo.metadados["periodo"]["bimestre"] == 4
    assert "rreo" in rreo.secoes and rreo.sha256 and rreo.page_url == f"{PORTAL}/contas"
    csv = arts[f"{PORTAL}/despesas.csv"]
    assert csv.metadados["colunas_norm"] == ["empenho", "valor pago"]


def test_crawler_respeita_orcamento_de_paginas():
    out, sink, _ = crawl(settings=test_settings(max_paginas=5))
    assert len(sink.pages) == 5 == out.stats.paginas
    # o portal entra antes de qualquer outra página por ter prioridade máxima
    assert sink.pages[1][0].url == f"{PORTAL}/"


def test_crawler_renderiza_spa_e_detecta_plataforma():
    r = FakeRenderer(RENDER)
    out, sink, _ = crawl(renderer=r, dominios_extra=[])
    s = _por_url(sink)[f"{SPA}/#/inicio"]
    assert s.renderizado_js and s.coleta_status == "ok" and "lgpd_digital" in s.secoes
    assert f"{SPA}/#/inicio" in r.calls and r.closed
    assert out.fingerprints["www.exemplo.rj.gov.br"].plataforma == "wordpress"
    assert out.fingerprints["spa.exemplo.rj.gov.br"].requires_js


def test_spa_sem_renderizador_vira_vazio_nao_erro():
    out, sink, _ = crawl()
    s = _por_url(sink)[f"{SPA}/#/inicio"]
    assert s.coleta_status == "vazio" and not s.renderizado_js     # Camada 2 => nao_verificavel


def test_robots_override_coleta_e_marca_snapshot():
    out, sink, _ = crawl(robots_override=True, robots_justificativa="LAI art. 8º")
    s = _por_url(sink)[f"{PORTAL}/privado/painel"]
    assert s.robots_override and s.coleta_status in {"erro", "ok"}   # coletou (404 no site falso)
    assert out.stats.bloqueios_robots == 0


def test_descoberta_entra_em_entity_urls():
    out, _, _ = crawl()
    tipos = {(u.tipo, u.fonte) for u in out.urls}
    assert ("portal_transparencia", "config") in tipos
    assert ("esic", "descoberta") in tipos and ("ouvidoria", "descoberta") in tipos


# ------------------------------------------------------------------ pipeline ponta a ponta

def test_pipeline_local_duas_rodadas_incremental(tmp_path):
    settings = test_settings(data_dir=tmp_path)
    repo = LocalRepository(tmp_path / "local")
    storage = LocalStorage(tmp_path / "objetos")
    etag = '"rreo-v1"'

    r1 = run([ente()], repo, storage, settings, http_factory=lambda: make_http(montar_site(etag), settings))
    assert r1["status"] == "parcial" and r1["entes"]["rj-exemplo"]["arquivos"] >= 2
    uploads_1 = storage.uploads

    # 2ª rodada, site idêntico: nada novo no storage, páginas marcadas como inalteradas, PDF via 304
    repo2 = LocalRepository(tmp_path / "local")      # reabre do disco, como numa nova execução
    r2 = run([ente()], repo2, storage, settings, http_factory=lambda: make_http(montar_site(etag), settings))
    assert storage.uploads == uploads_1
    jobs = [j for j in repo2.read("crawl_job") if j["run_id"] == r2["run_id"]]
    assert jobs[0]["paginas_inalteradas"] >= 5
    pdfs = [f for f in repo2.read("file_artifact") if f["url"].endswith(".pdf")]
    assert len(pdfs) == 2 and pdfs[1]["metadados"].get("nao_modificado") is True
    assert pdfs[0]["sha256"] == pdfs[1]["sha256"] and pdfs[1]["storage_path"] == pdfs[0]["storage_path"]

    runs = repo2.read("run")
    assert [r["status"] for r in runs] == ["parcial", "parcial"] and all(r.get("finalizado_em") for r in runs)
    assert (tmp_path / "runs" / f"{r2['run_id']}.json").exists()
    snap = next(p for p in repo2.read("page_snapshot") if p["url"] == f"{PORTAL}/despesas")
    assert storage.get(snap["storage_path"]).startswith(b"<html")


def test_pipeline_um_ente_falhando_nao_derruba_os_outros(tmp_path):
    settings = test_settings(data_dir=tmp_path)
    repo, storage = LocalRepository(tmp_path / "l"), LocalStorage(tmp_path / "o")
    fora = ente(id="rj-fora-do-ar", urls={"site_oficial": "https://fora.rj.gov.br/"})
    site = montar_site()
    import requests
    site.handler("https://fora.rj.gov.br/robots.txt", lambda r: requests.exceptions.ConnectionError("conexao recusada"))
    site.handler("https://fora.rj.gov.br/", lambda r: requests.exceptions.ConnectionError("conexao recusada"))
    resumo = run([ente(), fora], repo, storage, settings, http_factory=lambda: make_http(site, settings))
    assert resumo["entes"]["rj-fora-do-ar"]["status"] == "falhou"
    assert resumo["entes"]["rj-exemplo"]["status"] == "parcial"
    assert resumo["status"] == "parcial"


# ------------------------------------------------------------------ storage

def test_supabase_storage_trata_duplicado_como_sucesso():
    import requests

    calls = []
    site = FakeSite()
    site.handler("https://abc.supabase.co/storage/v1/object/coleta/x", lambda r: (200, {}, b""))

    class Adapter(FakeSite):
        def send(self, request, **kw):
            calls.append((request.method, request.url, request.headers.get("x-upsert")))
            r = requests.Response()
            r.status_code = 400 if len(calls) > 1 else 200
            r._content = b'{"error":"Duplicate","message":"The resource already exists"}' if len(calls) > 1 else b"{}"
            r.url = request.url
            return r

    sess = requests.Session()
    sess.mount("https://", Adapter())
    st = SupabaseStorage("https://abc.supabase.co", "service-key", "coleta", session=sess)
    k1 = st.put(b"conteudo A", "html", "text/html")
    k2 = st.put(b"conteudo B", "html", "text/html")      # simula objeto já existente no bucket
    k3 = st.put(b"conteudo A", "html", "text/html")      # cache local: nem chama a API
    assert k1 == k3 and k1 != k2 and len(calls) == 2
    assert calls[0][1].startswith("https://abc.supabase.co/storage/v1/object/coleta/") and calls[0][2] == "false"
    assert st.uploads == 1
    assert "/object/coleta/html/" in calls[0][1]          # layout html/ · png/ · files/


def test_supabase_chave_nova_vai_so_no_apikey():
    novo = SupabaseStorage("https://abc.supabase.co", "sb_secret_xyz", "snapshots")
    assert novo.http.headers["apikey"] == "sb_secret_xyz" and "Authorization" not in novo.http.headers
    legado = SupabaseStorage("https://abc.supabase.co", "eyJhbGciOi.legado", "snapshots")
    assert legado.http.headers["Authorization"] == "Bearer eyJhbGciOi.legado"
