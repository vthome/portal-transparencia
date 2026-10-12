"""Extração estruturada de uma página HTML.

O resultado (`ParsedPage.to_dict()`) vai para `page_snapshot.extraido` e é o insumo dos
verificadores da Camada 2: links (navegação), tabelas (dados estruturados), formulários
(busca, e-SIC), breadcrumbs e sinais de acessibilidade, datas (atualidade).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from bs4 import BeautifulSoup, Tag

from ingestion.transform.normalize import (
    extension_of,
    normalize_header,
    normalize_text,
    normalize_url,
    parse_dates_br,
)

ARQUIVO_EXT = {"pdf", "csv", "xls", "xlsx", "ods", "json", "xml", "zip", "txt", "doc", "docx", "odt"}
MAX_TEXTO = 50_000
MAX_LINKS = 800
MAX_TABELAS = 20
LINHAS_AMOSTRA = 3


@dataclass
class Link:
    url: str
    texto: str
    arquivo: str | None = None          # extensão quando aponta para documento
    na_capa_topo: bool = False          # no <header>/<nav> ou nos primeiros links da página
    title: str | None = None


@dataclass
class Tabela:
    cabecalhos: list[str]
    cabecalhos_norm: list[str]
    n_linhas: int
    amostra: list[list[str]]
    legenda: str | None = None


@dataclass
class Campo:
    nome: str | None
    tipo: str
    rotulo: str | None
    obrigatorio: bool
    opcoes: int = 0


@dataclass
class Formulario:
    action: str | None
    metodo: str
    campos: list[Campo]
    parece_busca: bool


@dataclass
class ParsedPage:
    titulo: str | None
    h1: list[str]
    headings: list[str]
    texto: str
    idioma: str | None
    meta: dict[str, str]
    links: list[Link]
    tabelas: list[Tabela]
    formularios: list[Formulario]
    breadcrumbs: list[str]
    datas: list[str]                 # ISO, mais recentes primeiro (máx. 20)
    datas_atualizacao: list[str]     # datas próximas de "atualizado em"
    acessibilidade: dict[str, Any] = field(default_factory=dict)
    botoes_exportacao: list[str] = field(default_factory=list)
    iframes: list[str] = field(default_factory=list)
    n_scripts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_EXPORT = re.compile(r"\b(csv|xlsx?|ods|json|xml|pdf|exportar|download|baixar|planilha|dados abertos)\b", re.I)
_ATUALIZ = re.compile(r"(atualizad[oa]s?\s+em|ultima atualizacao|data de atualizacao|atualizacao:)\s*:?\s*(.{0,40})")


def _txt(el: Tag | None) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse_html(html: str, base_url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "lxml")
    n_scripts = len(soup.find_all("script"))

    titulo = _txt(soup.title) or None
    meta = {}
    for m in soup.find_all("meta"):
        k = (m.get("name") or m.get("property") or "").lower()
        if k in {"generator", "description", "keywords", "og:site_name", "author"} and m.get("content"):
            meta[k] = m["content"][:300]
    html_tag = soup.find("html")
    idioma = html_tag.get("lang") if isinstance(html_tag, Tag) else None

    acess = _acessibilidade(soup)
    links = _links(soup, base_url)
    forms = _formularios(soup, base_url)
    breadcrumbs = _breadcrumbs(soup)
    iframes = [u for u in (normalize_url(f.get("src", ""), base_url) for f in soup.find_all("iframe")) if u]

    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()

    tabelas = [_tabela(t) for t in soup.find_all("table")[:MAX_TABELAS]]
    tabelas = [t for t in tabelas if t.n_linhas or t.cabecalhos]
    h1 = [_txt(h) for h in soup.find_all("h1")][:5]
    headings = [_txt(h) for h in soup.find_all(["h1", "h2", "h3"])][:60]
    texto = " ".join(soup.get_text(" ", strip=True).split())[:MAX_TEXTO]

    datas = sorted({d.isoformat() for d in parse_dates_br(texto)}, reverse=True)[:20]
    atualiz = []
    for m in _ATUALIZ.finditer(normalize_text(texto)):
        # recupera o trecho original aproximado para extrair a data
        atualiz += [d.isoformat() for d in parse_dates_br(m.group(2).replace(" ", "/"))]
    atualiz += [d.isoformat() for d in parse_dates_br(" ".join(
        re.findall(r"[Aa]tualizad[oa]s?\s+em:?\s*([0-9/.\-]{8,10})", texto)))]
   # exportacao = sorted({m.lower() for l in links for m in _EXPORT.findall(l.texto + " " + (l.arquivo or ""))})
    exportacao = sorted({m.lower() for link in links for m in _EXPORT.findall(link.texto + " " + (link.arquivo or ""))})

    return ParsedPage(
        titulo=titulo, h1=h1, headings=headings, texto=texto, idioma=idioma, meta=meta,
        links=links, tabelas=tabelas, formularios=forms, breadcrumbs=breadcrumbs,
        datas=datas, datas_atualizacao=sorted(set(atualiz), reverse=True),
        acessibilidade=acess, botoes_exportacao=exportacao, iframes=iframes, n_scripts=n_scripts,
    )


def _links(soup: BeautifulSoup, base_url: str) -> list[Link]:
    out: dict[str, Link] = {}
    topo = set()
    for zona in soup.select("header, nav, [role=navigation], #menu, .menu, .navbar"):
        topo.update(id(a) for a in zona.find_all("a"))
    for i, a in enumerate(soup.find_all("a")):
        href = a.get("href") or a.get("data-href") or ""
        url = normalize_url(href, base_url)
        if not url:
            continue
        texto = _txt(a) or a.get("title") or a.get("aria-label") or ""
        if not texto:
            img = a.find("img")
            texto = (img.get("alt") or "") if isinstance(img, Tag) else ""
        ext = extension_of(url)
        link = Link(url=url, texto=texto[:200], arquivo=ext if ext in ARQUIVO_EXT else None,
                    na_capa_topo=id(a) in topo or i < 40, title=a.get("title"))
        if url not in out or (not out[url].texto and link.texto):
            out[url] = link
        if len(out) >= MAX_LINKS:
            break
    return list(out.values())


def _tabela(t: Tag) -> Tabela:
    linhas = t.find_all("tr")
    cab: list[str] = []
    thead = t.find("thead")
    if thead:
        cab = [_txt(c) for c in thead.find_all(["th", "td"])]
    elif linhas and linhas[0].find("th"):
        cab = [_txt(c) for c in linhas[0].find_all(["th", "td"])]
    corpo = [r for r in linhas if r.find("td") and (not thead or r.find_parent("thead") is None)]
    if cab and corpo and [_txt(c) for c in corpo[0].find_all(["th", "td"])] == cab:
        corpo = corpo[1:]
    amostra = [[_txt(c)[:120] for c in r.find_all(["td", "th"])] for r in corpo[:LINHAS_AMOSTRA]]
    cap = t.find("caption")
    return Tabela(cabecalhos=cab, cabecalhos_norm=[normalize_header(c) for c in cab],
                  n_linhas=len(corpo), amostra=amostra, legenda=_txt(cap) or None)


def _formularios(soup: BeautifulSoup, base_url: str) -> list[Formulario]:
    out = []
    for f in soup.find_all("form")[:15]:
        campos = []
        for el in f.find_all(["input", "select", "textarea"]):
            tipo = el.get("type", el.name).lower() if el.name == "input" else el.name
            if tipo in {"hidden", "submit", "button", "image", "reset"}:
                continue
            rotulo = None
            if el.get("id"):
                lab = soup.find("label", attrs={"for": el["id"]})
                rotulo = _txt(lab) or None
            rotulo = rotulo or el.get("placeholder") or el.get("aria-label") or el.get("title")
            campos.append(Campo(
                nome=el.get("name"), tipo=tipo, rotulo=(rotulo or None),
                obrigatorio=el.has_attr("required") or el.get("aria-required") == "true",
                opcoes=len(el.find_all("option")) if el.name == "select" else 0,
            ))
        sinais = normalize_text(" ".join([f.get("action", ""), f.get("role", ""), f.get("id", ""),
                                          f.get("class", "") if isinstance(f.get("class"), str) else " ".join(f.get("class", []))]
                                         + [c.nome or "" for c in campos] + [c.rotulo or "" for c in campos]))
        busca = any(t in sinais.split() for t in ("search", "busca", "pesquisa", "pesquisar", "buscar", "q", "s"))
        out.append(Formulario(action=normalize_url(f.get("action") or base_url, base_url),
                              metodo=(f.get("method") or "get").lower(), campos=campos, parece_busca=busca))
    return out


def _breadcrumbs(soup: BeautifulSoup) -> list[str]:
    alvo = soup.select_one('[aria-label*="breadcrumb" i], [class*="breadcrumb" i], '
                           '[id*="breadcrumb" i], [itemtype*="BreadcrumbList"]')
    if not alvo:
        return []
    itens = [_txt(x) for x in alvo.find_all(["li", "a", "span"]) if _txt(x)]
    vistos, out = set(), []
    for i in itens:
        if i not in vistos and len(i) < 80:
            vistos.add(i)
            out.append(i)
    return out[:10]


def _acessibilidade(soup: BeautifulSoup) -> dict[str, Any]:
    bruto = soup.get_text(" ", strip=True)[:20000]
    texto = normalize_text(bruto)
    attrs = " ".join(str(v) for t in soup.find_all(True, limit=4000)
                     for k, v in t.attrs.items() if k in {"class", "id", "aria-label", "title", "alt", "src"})
    a = normalize_text(attrs)
    imgs = soup.find_all("img")
    return {
        "alto_contraste": "contraste" in texto or "contrast" in a,
        "ajuste_fonte": bool(re.search(r"(?<![\w])A\s?[+\-−](?![\w])", bruto)
                             or re.search(r"\b(a mais|a menos|aumentar fonte|diminuir fonte|font size|fonte maior)\b",
                                          texto + " " + a)),
        "simbolo_acessibilidade": "acessibilidade" in a or "accessibility" in a,
        "vlibras": "vlibras" in a or "vlibras" in texto,
        "accesskeys": len(soup.select("[accesskey]")),
        "skip_link": bool(soup.select_one('a[href="#conteudo"], a[href="#main"], a[href="#content"], .skip-link')),
        "imagens": len(imgs),
        "imagens_sem_alt": sum(1 for i in imgs if not (i.get("alt") or "").strip()),
        "lang": bool((soup.find("html") or {}).get("lang")) if soup.find("html") else False,
    }
