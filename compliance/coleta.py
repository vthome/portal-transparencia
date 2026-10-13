"""Modelo de entrada do motor: tudo que a Camada 1 coletou para um ente numa rodada.

Os verificadores trabalham só com estes objetos em memória (nada de SQL), o que os
torna testáveis e permite reavaliar uma rodada antiga com regras novas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from ingestion.transform.normalize import find_terms, host_of, normalize_text

STATUS_FALHA = {"erro", "vazio", "soft404", "bloqueado_robots"}


@dataclass
class Pagina:
    id: str
    url: str
    titulo: str | None
    status: str
    secoes: list[str]
    trilha: list[str]
    profundidade: int
    renderizado: bool
    texto: str
    links: list[dict[str, Any]]
    extraido: dict[str, Any]
    screenshot_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def host(self) -> str:
        return host_of(self.url)

    def campo(self, nome: str, padrao=None):
        return self.extraido.get(nome, padrao if padrao is not None else [])

    @property
    def rotulo(self) -> str:
        """Texto que identifica a página: título, h1, breadcrumb e texto do link que levou a ela."""
        partes = [self.titulo or "", *self.campo("h1"), *self.campo("breadcrumbs"),
                  self.extraido.get("texto_link") or ""]
        return " ".join(p for p in partes if p)


@dataclass
class Arquivo:
    id: str
    page_id: str | None
    url: str
    texto_link: str | None
    extensao: str | None
    metadados: dict[str, Any]
    secoes: list[str]
    texto: str
    erro: str | None
    sha256: str | None = None

    @property
    def ok(self) -> bool:
        return not self.erro and bool(self.sha256 or self.texto)

    @property
    def rotulo(self) -> str:
        titulo_pdf = (self.metadados.get("pdf_info") or {}).get("title", "")
        return " ".join(p for p in (self.texto_link or "", titulo_pdf, self.url) if p)


@dataclass
class ColetaEnte:
    slug: str
    nome: str
    tipo_entidade: str
    poder: str
    esfera: str
    job: dict[str, Any]
    urls: list[dict[str, Any]]                    # tipo (nomes do YAML), url, fonte, dominio_gov
    paginas: list[Pagina] = field(default_factory=list)
    arquivos: list[Arquivo] = field(default_factory=list)

    # ------------------------------------------------------------ aplicabilidade

    @property
    def tags(self) -> set[str]:
        tags = {"comum"}
        if self.poder == "executivo":
            tags.add("executivo")
            if self.esfera == "municipal":
                tags.add("executivo_municipal")
        if self.poder == "legislativo":
            tags.add("legislativo")
        return tags

    # ------------------------------------------------------------ páginas-chave

    def urls_do_tipo(self, *tipos: str) -> list[str]:
        return [u["url"] for u in self.urls if u.get("tipo") in tipos]

    @property
    def hosts_portal(self) -> set[str]:
        return {host_of(u) for u in self.urls_do_tipo("portal_transparencia")}

    def capa(self) -> Pagina | None:
        site = self.urls_do_tipo("site_oficial")
        cands = [p for p in self.paginas if p.profundidade == 0]
        if site:
            h = host_of(site[0]).removeprefix("www.")
            cands = [p for p in cands if p.host.removeprefix("www.") == h] or cands
        return cands[0] if cands else None

    def paginas_portal(self, so_ok: bool = True) -> list[Pagina]:
        hs = self.hosts_portal
        return [p for p in self.paginas if p.host in hs and (p.ok or not so_ok)]

    def pagina_inicial_portal(self) -> Pagina | None:
        ps = sorted(self.paginas_portal(), key=lambda p: (len(p.trilha), p.profundidade))
        return ps[0] if ps else None

    # ------------------------------------------------------------ seleção

    def paginas_ok(self, secoes: list[str] | None = None, filtro: list[str] | None = None) -> list[Pagina]:
        out = [p for p in self.paginas if p.ok]
        if secoes:
            out = [p for p in out if set(p.secoes) & set(secoes)]
        if filtro:
            out = [p for p in out if find_terms(p.rotulo + " " + p.url.replace("/", " "), filtro)]
        return out

    def arquivos_ok(self, secoes: list[str] | None = None, filtro: list[str] | None = None) -> list[Arquivo]:
        out = [a for a in self.arquivos if a.ok]
        if secoes:
            out = [a for a in out if set(a.secoes) & set(secoes)]
        if filtro:
            out = [a for a in out if find_terms(a.rotulo.replace("/", " "), filtro)]
        return out

    def links(self, paginas: list[Pagina] | None = None) -> Iterator[tuple[Pagina, dict[str, Any]]]:
        for p in (paginas if paginas is not None else self.paginas_ok()):
            for link in p.links or []:
                yield p, link

    def situacao(self, secoes: list[str] | None) -> str:
        """coberta: há página ok da seção · falha: só páginas com erro/vazio/robots · ausente: nada."""
        if not secoes:
            return "coberta" if any(p.ok for p in self.paginas) else "falha"
        ps = [p for p in self.paginas if set(p.secoes) & set(secoes)]
        if any(p.ok for p in ps):
            return "coberta"
        if any(p.status in STATUS_FALHA for p in ps):
            return "falha"
        return "ausente"

    def qualidade(self, secoes: list[str] | None) -> float:
        """Fração de páginas da seção coletadas com sucesso (1.0 quando não há páginas)."""
        ps = [p for p in self.paginas if not secoes or set(p.secoes) & set(secoes)]
        return round(sum(p.ok for p in ps) / len(ps), 3) if ps else 1.0


def trecho(texto: str, padrao: str | re.Pattern | None = None, termos: list[str] | None = None,
           raio: int = 140) -> str | None:
    """Recorte de texto em volta da primeira ocorrência (evidência legível para o revisor)."""
    if not texto:
        return None
    pos = -1
    if padrao is not None:
        m = re.search(padrao, texto, re.I)
        pos = m.start() if m else -1
    if pos < 0 and termos:
        norm = normalize_text(texto)
        for t in termos:
            i = norm.find(normalize_text(t))
            if i >= 0:
                # posição aproximada no texto original (normalização preserva o tamanho na maioria dos casos)
                pos = min(i, len(texto) - 1)
                break
    if pos < 0:
        return None
    ini, fim = max(0, pos - raio), min(len(texto), pos + raio)
    return ("…" if ini else "") + " ".join(texto[ini:fim].split()) + ("…" if fim < len(texto) else "")
