import io

from ingestion.discover.fingerprint import detect, looks_like_spa
from ingestion.discover.resolve_urls import find_candidates, resolve
from ingestion.models import Entity, FetchResult
from ingestion.transform.parse_files import parse_file
from ingestion.transform.parse_html import parse_html
from tests.helpers import page


def _res(url, html, headers=None):
    return FetchResult(url=url, url_final=url, status=200, content=html.encode(),
                       content_type="text/html; charset=utf-8", headers=headers or {})

# ------------------------------------------------------------------ fingerprint

def test_fingerprint_wordpress():
    html = page("Prefeitura", "", extra='<link rel="stylesheet" href="/wp-content/themes/x/style.css">'
                                        '<meta name="generator" content="WordPress 6.6">')
    f = detect(_res("https://www.marica.rj.gov.br/", html))
    assert f.plataforma == "wordpress" and not f.requires_js


def test_fingerprint_geosiap_pelo_host():
    f = detect(_res("https://japeri.geosiap.net.br/portal-transparencia/home", page("Portal", "")))
    assert f.plataforma == "geosiap"


def test_fingerprint_betha_exige_js():
    html = '<html><head><script src="a.js"></script></head><body><div id="app"></div></body></html>'
    f = detect(_res("https://transparencia.betha.cloud/#/abc/", html))
    assert f.plataforma == "betha" and f.requires_js and f.spa_detectada


def test_spa_generica_sem_plataforma_conhecida():
    html = ('<html><body><div id="root"></div><script src="1.js"></script><script src="2.js"></script>'
            '<script src="3.js"></script></body></html>')
    assert looks_like_spa(html, "https://transparencia.duquedecaxias.rj.gov.br/")
    f = detect(_res("https://transparencia.duquedecaxias.rj.gov.br/", html))
    assert f.requires_js


def test_pagina_estatica_nao_e_spa():
    assert not looks_like_spa(page("Despesas", "<p>Tabela de despesas</p>"), "https://x.gov.br/despesas")


# ------------------------------------------------------------------ descoberta

CAPA = page("Prefeitura de Exemplo", "", links=[
    ("https://transparencia.exemplo.rj.gov.br/", "Portal da Transparência"),
    ("https://esic.exemplo.rj.gov.br/", "e-SIC"),
    ("/ouvidoria", "Ouvidoria"),
    ("javascript:void(0)", "Transparência (menu)"),
    ("/arquivos/transparencia.pdf", "Transparência 2019 (PDF)"),
    ("/noticias", "Notícias"),
])


def test_candidatos_da_capa():
    links = parse_html(CAPA, "https://www.exemplo.rj.gov.br/").links
    c = find_candidates(links)
    assert c["portal_transparencia"][0].url == "https://transparencia.exemplo.rj.gov.br/"
    assert all(not x.url.endswith(".pdf") for x in c["portal_transparencia"])   # PDF não é o portal
    assert c["esic"][0].url == "https://esic.exemplo.rj.gov.br/"
    assert c["ouvidoria"][0].url == "https://www.exemplo.rj.gov.br/ouvidoria"


def _ente(**urls):
    return Entity(id="rj-exemplo", nome="Exemplo", municipio="Exemplo", uf="RJ", tipo_entidade="prefeitura",
                  poder="executivo", esfera="municipal", codigo_ibge=None, urls=urls)


def test_resolve_confirma_config_e_registra_divergencia():
    links = parse_html(CAPA, "https://www.exemplo.rj.gov.br/").links
    e = _ente(site_oficial="https://www.exemplo.rj.gov.br/", portal_transparencia="https://antigo.exemplo.com/",
              esic="https://esic.exemplo.rj.gov.br/")
    urls = resolve(e, links, e.urls["site_oficial"])
    por = {(u.tipo, u.fonte): u for u in urls}
    assert por[("esic", "config")].evidencia["confirmado_na_capa"] is True
    desc = por[("portal_transparencia", "descoberta")]
    assert desc.url == "https://transparencia.exemplo.rj.gov.br/"
    assert desc.evidencia["divergente_de_config"] == "https://antigo.exemplo.com/"
    assert desc.evidencia["texto_link"] == "Portal da Transparência" and desc.dominio_gov
    assert ("ouvidoria", "descoberta") in por


# ------------------------------------------------------------------ parse_html

DESPESAS = """
<html lang="pt-BR"><head><title>Despesas — Portal</title><meta name="generator" content="WordPress 6"></head>
<body>
 <nav aria-label="breadcrumb"><ol><li><a href="/">Início</a></li><li><a href="/despesas">Despesas</a></li></ol></nav>
 <a href="#conteudo" class="skip-link">Pular</a><a accesskey="1" href="/">Início</a>
 <button class="alto-contraste">Alto contraste</button><a href="#" class="a-mais">A+</a>
 <h1>Despesas por empenho</h1>
 <p>Atualizado em 05/10/2026</p>
 <form action="/despesas" method="get"><label for="ano">Exercício</label>
   <select id="ano" name="ano"><option>2026</option><option>2025</option></select>
   <input type="text" name="q" placeholder="Pesquisar"><input type="submit"></form>
 <table><thead><tr><th>Nº do Empenho</th><th>Credor</th><th>Valor Liquidado</th><th>Valor Pago</th></tr></thead>
  <tbody><tr><td>2026NE0001</td><td>ACME LTDA</td><td>R$ 1.000,00</td><td>R$ 1.000,00</td></tr>
         <tr><td>2026NE0002</td><td>BETA SA</td><td>R$ 2.500,00</td><td>R$ 0,00</td></tr></tbody></table>
 <a href="/despesas.csv">Exportar CSV</a> <a href="/despesas.xlsx">Planilha XLSX</a>
 <img src="logo.png">
 <script>var x = "não é texto";</script>
</body></html>"""


def test_parse_html_estrutura_completa():
    p = parse_html(DESPESAS, "https://transparencia.exemplo.rj.gov.br/despesas")
    assert p.titulo == "Despesas — Portal" and p.h1 == ["Despesas por empenho"]
    assert p.breadcrumbs[:2] == ["Início", "Despesas"]
    t = p.tabelas[0]
    assert t.cabecalhos_norm == ["n do empenho", "credor", "valor liquidado", "valor pago"]
    assert t.n_linhas == 2 and t.amostra[0][0] == "2026NE0001"
    f = p.formularios[0]
    assert f.parece_busca and f.metodo == "get"
    assert {c.nome for c in f.campos} == {"ano", "q"} and f.campos[0].rotulo == "Exercício" and f.campos[0].opcoes == 2
    assert "2026-10-05" in p.datas_atualizacao
    assert {"csv", "xlsx"} <= set(p.botoes_exportacao)
    #arquivos = {l.url: l.arquivo for l in p.links if l.arquivo}
    arquivos = {link.url: link.arquivo for link in p.links if link.arquivo}
    assert arquivos["https://transparencia.exemplo.rj.gov.br/despesas.csv"] == "csv"
    a = p.acessibilidade
    assert a["alto_contraste"] and a["ajuste_fonte"] and a["skip_link"] and a["accesskeys"] == 1
    assert a["imagens_sem_alt"] == 1 and a["lang"]
    assert "não é texto" not in p.texto
    assert p.meta["generator"].startswith("WordPress")


# ------------------------------------------------------------------ parse_files

def _pdf_bytes(linhas):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("RREO 4o bimestre 2026")
    y = 800
    # for l in linhas:
    #     c.drawString(50, y, l)
    #     y -= 20
    for linha in linhas:
        c.drawString(50, y, linha)
        y -= 20
    c.showPage()
    c.drawString(50, 800, "Página 2")
    c.save()
    return buf.getvalue()


def test_parse_pdf_rreo():
    pdf = _pdf_bytes(["RELATORIO RESUMIDO DA EXECUCAO ORCAMENTARIA", "4o Bimestre",
                      "Julho a Agosto de 2026", "Exercicio de 2026", "Publicado em 30/09/2026"])
    r = parse_file(pdf, "application/pdf", "pdf")
    assert r.tipo == "pdf" and r.paginas == 2 and r.erro is None
    m = r.metadados
    assert m["periodo"]["bimestre"] == 4 and m["periodo"]["meses"] == ["julho", "agosto"] and m["periodo"]["ano"] == 2026
    assert m["exercicios"] == [2026] and "2026-09-30" in m["datas"]
    assert m["pdf_info"]["title"] == "RREO 4o bimestre 2026" and not m["sem_texto"]


def test_parse_csv_latin1_ponto_e_virgula():
    csv = "Nome;Cargo;Remuneração Bruta\nJOSÉ;Analista;5.000,00\nMARIA;Técnica;3.000,00\n".encode("cp1252")
    r = parse_file(csv, "text/csv", "csv")
    assert r.tipo == "csv" and r.metadados["separador"] == ";"
    assert r.metadados["colunas_norm"] == ["nome", "cargo", "remuneracao bruta"]
    assert r.metadados["n_linhas_lidas"] == 2 and r.metadados["amostra"][0][0] == "JOSÉ"


def test_parse_xlsx_com_titulo_antes_do_cabecalho():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["PREFEITURA MUNICIPAL - FOLHA DE PAGAMENTO"])
    ws.append([])
    ws.append(["Matrícula", "Nome", "Cargo", "Salário"])
    ws.append(["001", "Ana", "Professora", "4000"])
    buf = io.BytesIO()
    wb.save(buf)
    r = parse_file(buf.getvalue(), None, "xlsx")
    assert r.tipo == "planilha" and r.metadados["colunas_norm"] == ["matricula", "nome", "cargo", "salario"]


def test_arquivo_corrompido_nao_lanca():
    r = parse_file(b"%PDF-1.4 lixo", "application/pdf", "pdf")
    assert r.tipo == "pdf" and r.erro
