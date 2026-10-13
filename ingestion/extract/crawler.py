"""Crawler orientado a objetivo (best-first) para um ente.

Não baixamos o portal inteiro: procuramos, dentro de um orçamento de páginas, as
seções que o checklist exige (secoes.yaml). A fila é priorizada pela relevância do
link para as seções *ainda não cobertas*; quando uma seção atinge seu teto, ela deixa
de puxar a fila e o orçamento vai para as que faltam.

Cada página coletada vira um `PageSnapshot` imediatamente persistido pelo `Sink`
(progresso parcial sobrevive a uma queda no meio da rodada). Páginas bloqueadas por
robots.txt ou com erro também são registradas — são a evidência de "não verificável".
"""
from __future__ import annotations

import hashlib
import heapq
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ingestion.discover import fingerprint as fp
from ingestion.discover.resolve_urls import resolve
from ingestion.extract.http_client import HttpClient
from ingestion.extract.renderer import RendererProtocol
from ingestion.models import (
    CrawlStats,
    Entity,
    EntityUrl,
    FetchResult,
    FileArtifact,
    Fingerprint,
    PageSnapshot,
)
from ingestion.settings import Settings, get_settings
from ingestion.transform.extracao import extrair_pagina, relevancia
from ingestion.transform.normalize import (
    extension_of,
    host_of,
    normalize_url,
    registrable_domain,
)
from ingestion.transform.parse_files import parse_file
from ingestion.transform.parse_html import Link
from ingestion.transform.validate import load_platforms, load_secoes

log = logging.getLogger(__name__)

ARQUIVOS_BAIXAVEIS = {"pdf", "csv", "xls", "xlsx", "ods", "json", "zip"}
MAX_ARQUIVO_BYTES = 15 * 1024 * 1024


class Sink(Protocol):
    def save_page(self, snap: PageSnapshot, html: bytes | None, screenshot: bytes | None) -> str: ...
    def save_file(self, art: FileArtifact, content: bytes | None, page_id: str) -> str: ...
    def previous_page(self, url: str) -> dict[str, Any] | None: ...
    def previous_file(self, url: str) -> dict[str, Any] | None: ...


@dataclass(order=True)
class _Item:
    prioridade: float
    seq: int
    url: str = field(compare=False)
    profundidade: int = field(compare=False)
    trilha: list[str] = field(compare=False)
    texto_link: str = field(compare=False, default="")


@dataclass
class CrawlOutput:
    stats: CrawlStats
    urls: list[EntityUrl]
    fingerprints: dict[str, Fingerprint]


class Crawler:
    def __init__(
        self,
        entity: Entity,
        http: HttpClient,
        sink: Sink,
        settings: Settings | None = None,
        renderer_factory: Callable[[], RendererProtocol] | None = None,
        secoes_cfg: dict[str, Any] | None = None,
        platforms_cfg: dict[str, Any] | None = None,
    ) -> None:
        self.e, self.http, self.sink = entity, http, sink
        self.s = settings or get_settings()
        self._renderer_factory = renderer_factory
        self._renderer: RendererProtocol | None = None
        self._render_indisponivel = renderer_factory is None or not self.s.renderizar
        cfg = secoes_cfg or load_secoes()
        self.secoes: dict[str, dict] = cfg["secoes"]
        self.orc = cfg.get("orcamento", {})
        self.plats = platforms_cfg or load_platforms()

        self.stats = CrawlStats()
        self.cobertura: dict[str, int] = {s: 0 for s in self.secoes}
        self.arquivos_secao: dict[str, int] = {s: 0 for s in self.secoes}
        self.vistos: set[str] = set()
        self.arquivos_vistos: set[str] = set()
        self.fila: list[_Item] = []
        self._seq = itertools.count()
        self.js_hosts: set[str] = set()
        self.wait_selector: dict[str, str] = {}
        self.fingerprints: dict[str, Fingerprint] = {}
        self.urls: list[EntityUrl] = []
        self.status_paginas: dict[str, int] = {}
        self.paginas_chave_ok: set[str] = set()   # capa / portal / canais coletados com sucesso
        self.proprios = self._dominios_proprios()

    # ------------------------------------------------------------ orquestração

    def run(self) -> CrawlOutput:
        try:
            self._seed()
            while self.fila and self.stats.paginas < self.s.max_paginas:
                item = heapq.heappop(self.fila)
                if item.url in self.vistos:
                    continue
                self._visit(item)
        finally:
            if self._renderer:
                self._renderer.close()
        self._finalizar()
        return CrawlOutput(self.stats, self.urls, self.fingerprints)

    def _seed(self) -> None:
        site = self.e.urls.get("site_oficial")
        portal = self.e.urls.get("portal_transparencia")
        if site:
            capa = self._visit(_Item(0, next(self._seq), normalize_url(site), 0, [normalize_url(site)]),
                               capa=True)
            links = capa.get("links", []) if capa else []
            self.urls = resolve(self.e, links, site)
        else:
            self.urls = resolve(self.e, [], "")
        # portal e canais entram com prioridade máxima; a trilha registra que vieram da capa
        for u in self.urls:
            if u.tipo == "site_oficial":
                continue
            url = normalize_url(u.url)
            if url and url not in self.vistos:
                trilha = [normalize_url(site), url] if site else [url]
                prio = -100 if u.tipo == "portal_transparencia" else -50
                self._push(url, 1 if site else 0, trilha, prio, u.tipo)
        if portal:
            self.proprios.add(host_of(portal))

    def _visit(self, item: _Item, capa: bool = False) -> dict[str, Any] | None:
        url = item.url
        self.vistos.add(url)
        host = host_of(url)
        snap_base = dict(url=url, profundidade=item.profundidade, trilha=item.trilha)

        # 1) robots — vale também para a renderização
        permitido = self.http.robots_allowed(url, verify_tls=self.e.verificar_tls)
        if not permitido and not self.e.robots_override:
            self.stats.bloqueios_robots += 1
            self._salvar(PageSnapshot(**snap_base, url_final=None, http_status=None, content_type=None,
                                      content_hash=None, tamanho=0, titulo=None,
                                      coleta_status="bloqueado_robots",
                                      erro="robots.txt não permite este caminho", renderizado_js=False,
                                      robots_override=False, secoes=self._secoes_de(item.texto_link, url),
                                      extraido={}), None, None)
            return None

        # 2) coleta estática
        res = self.http.fetch(url, robots_override=self.e.robots_override, verify_tls=self.e.verificar_tls)
        self.stats.requisicoes = self.http.contador

        # 3) fingerprint do host (primeira página de cada host)
        if host not in self.fingerprints and res.content:
            f = fp.detect(res, self.plats)
            self.fingerprints[host] = f
            if f.requires_js:
                self.js_hosts.add(host)
                if f.wait_selector:
                    self.wait_selector[host] = f.wait_selector
            if f.plataforma:
                self._seed_rotas(f.plataforma, res.url_final or url, item)

        # 4) renderização quando necessário (SPA ou HTML vazio)
        quer_shot = self.s.screenshots and (capa or item.texto_link == "portal_transparencia")
        precisa_js = host in self.js_hosts or (res.ok and fp.looks_like_spa(res.texto, url))
        if (precisa_js or quer_shot) and res.erro not in {"dns", "tls", "circuito_aberto"}:
            r2 = self._render(url, host, screenshot=quer_shot)
            if r2 is not None and r2.ok:
                if precisa_js:
                    r2.robots_override = res.robots_override
                    res = r2
                else:
                    res.screenshot = r2.screenshot

        return self._processar(item, res, capa)

    def _processar(self, item: _Item, res: FetchResult, capa: bool) -> dict[str, Any] | None:
        url = item.url
        self.stats.bytes += len(res.content)
        if not res.ok:
            self._salvar(PageSnapshot(
                url=url, url_final=res.url_final, http_status=res.status, content_type=res.content_type,
                content_hash=None, tamanho=len(res.content), titulo=None, coleta_status="erro",
                erro=f"{res.erro}: {res.detalhe_erro}" if res.detalhe_erro else res.erro,
                renderizado_js=res.renderizado_js, robots_override=res.robots_override,
                profundidade=item.profundidade, trilha=item.trilha,
                secoes=self._secoes_de(item.texto_link, url), extraido={}), None, None)
            return None

        ext = extension_of(res.url_final or url)
        ct = (res.content_type or "").lower()
        if ext in ARQUIVOS_BAIXAVEIS or ("html" not in ct and "xml" not in ct and ct):
            # a "página" é um documento: registra como arquivo de uma página-sentinela
            return self._arquivo_direto(item, res)

        # fase de extração (mesma função usada pelo ingestion.reextract)
        ext_pag = extrair_pagina(res.texto, url, res.url_final or url, item.texto_link, self.secoes, res.status)
        parsed, status, secoes = ext_pag.parsed, ext_pag.coleta_status, ext_pag.secoes
        for s in secoes:
            self.cobertura[s] += 1
        prev = self.sink.previous_page(url)
        inalterado = bool(prev and prev.get("texto_hash") == ext_pag.texto_hash)
        if inalterado:
            self.stats.paginas_inalteradas += 1
        extraido = ext_pag.extraido
        if host_of(url) in self.fingerprints:
            extraido["fingerprint_host"] = self.fingerprints[host_of(url)].__dict__

        snap = PageSnapshot(
            url=url, url_final=res.url_final, http_status=res.status, content_type=res.content_type,
            content_hash=hashlib.sha256(res.content).hexdigest(), tamanho=len(res.content),
            titulo=parsed.titulo, coleta_status=status, erro=None, renderizado_js=res.renderizado_js,
            robots_override=res.robots_override, profundidade=item.profundidade, trilha=item.trilha,
            secoes=secoes, extraido=extraido, etag=res.headers.get("etag"),
            last_modified=res.headers.get("last-modified"), inalterado=inalterado,
        )
        page_id = self._salvar(snap, res.content, res.screenshot)

        if item.profundidade < self.s.max_profundidade and self._segue_links(url):
            self._enfileirar(parsed.links, item)
        self._baixar_arquivos(parsed.links, item, page_id, url)
        return {"links": parsed.links, "parsed": parsed}

    # ------------------------------------------------------------ fila e relevância

    def _secoes_ativas(self) -> list[str]:
        teto = self.orc.get("max_paginas_por_secao", 4)
        return [s for s, n in self.cobertura.items() if n < teto]

    def _relevancia(self, texto: str, url: str, secoes: list[str]) -> tuple[float, list[str]]:
        return relevancia(texto, url, self.secoes, secoes)

    def _enfileirar(self, links: list[Link], origem: _Item) -> None:
        ativas = self._secoes_ativas()
        for link in links:
            if link.arquivo or link.url in self.vistos or not self._permitido(link.url):
                continue
            pts, _ = self._relevancia(link.texto, link.url, ativas)
            if pts == 0 and origem.profundidade >= 1:
                continue  # exploração estrutural só a partir da capa / 1º nível
            prio = -(pts + (1 if link.na_capa_topo else 0)) + 0.5 * (origem.profundidade + 1)
            self._push(link.url, origem.profundidade + 1, origem.trilha + [link.url], prio, link.texto)

    def _push(self, url: str, prof: int, trilha: list[str], prio: float, texto: str) -> None:
        heapq.heappush(self.fila, _Item(prio, next(self._seq), url, prof, trilha[-6:], texto))

    def _seed_rotas(self, plataforma: str, base: str, item: _Item) -> None:
        for rota in self.plats["plataformas"].get(plataforma, {}).get("rotas_semente", []):
            u = normalize_url(rota, base)
            if u and u not in self.vistos:
                self._push(u, item.profundidade + 1, item.trilha + [u], -40, f"rota:{plataforma}")

    def _secoes_de(self, texto: str, url: str) -> list[str]:
        return self._relevancia(texto, url, list(self.secoes))[1]

    # ------------------------------------------------------------ arquivos

    def _baixar_arquivos(self, links: list[Link], origem: _Item, page_id: str, page_url: str) -> None:
        teto_secao = self.orc.get("max_arquivos_por_secao", 3)
        candidatos = []
        for link in links:
            if link.arquivo not in ARQUIVOS_BAIXAVEIS or link.url in self.arquivos_vistos:
                continue
            pts, secs = self._relevancia(link.texto, link.url, list(self.secoes))
            secs = [s for s in secs if self.arquivos_secao[s] < teto_secao]
            if not secs:
                continue
            candidatos.append((pts, link, secs))
        candidatos.sort(key=lambda c: -c[0])
        for _, link, secs in candidatos:
            if self.stats.arquivos >= self.s.max_arquivos:
                return
            self.arquivos_vistos.add(link.url)
            for s in secs:
                self.arquivos_secao[s] += 1
            self._baixar(link.url, link.texto, secs, page_id, page_url)

    def _baixar(self, url: str, texto: str, secs: list[str], page_id: str, page_url: str) -> None:
        prev = self.sink.previous_file(url)
        res = self.http.fetch(url, robots_override=self.e.robots_override, verify_tls=self.e.verificar_tls,
                              etag=(prev or {}).get("etag"), last_modified=(prev or {}).get("last_modified"),
                              max_bytes=MAX_ARQUIVO_BYTES)
        self.stats.requisicoes = self.http.contador
        self._registrar_arquivo(url, texto, secs, res, prev, page_id, page_url)

    def _registrar_arquivo(self, url, texto, secs, res: FetchResult, prev, page_id, page_url):
        ext = extension_of(res.url_final or url)
        if res.nao_modificado and prev:
            art = FileArtifact(url=url, texto_link=texto, mime=prev.get("mime"), extensao=ext,
                               sha256=prev.get("sha256"), tamanho=prev.get("tamanho") or 0,
                               storage_path=prev.get("storage_path"), paginas=prev.get("paginas"),
                               texto_extraido=prev.get("texto_extraido"),
                               metadados={**(prev.get("metadados") or {}), "nao_modificado": True},
                               secoes=secs, page_url=page_url)
            self.sink.save_file(art, None, page_id)
            self.stats.arquivos += 1
            return
        if not res.ok:
            art = FileArtifact(url=url, texto_link=texto, mime=res.content_type, extensao=ext, sha256=None,
                               tamanho=len(res.content), storage_path=None, paginas=None, texto_extraido=None,
                               metadados={"http_status": res.status}, secoes=secs,
                               erro=f"{res.erro}: {res.detalhe_erro}" if res.detalhe_erro else res.erro,
                               page_url=page_url)
            self.sink.save_file(art, None, page_id)
            return
        self.stats.bytes += len(res.content)
        pf = parse_file(res.content, res.content_type, ext)
        meta = {**pf.metadados, "tipo": pf.tipo, "etag": res.headers.get("etag"),
                "last_modified": res.headers.get("last-modified"), "http_status": res.status}
        art = FileArtifact(url=url, texto_link=texto, mime=res.content_type, extensao=ext,
                           sha256=hashlib.sha256(res.content).hexdigest(), tamanho=len(res.content),
                           storage_path=None, paginas=pf.paginas, texto_extraido=pf.texto,
                           metadados=meta, secoes=secs, erro=pf.erro, page_url=page_url)
        self.sink.save_file(art, res.content, page_id)
        self.stats.arquivos += 1

    def _arquivo_direto(self, item: _Item, res: FetchResult) -> None:
        """URL de seção que é, ela própria, um PDF/CSV: snapshot mínimo + artefato."""
        secs = self._secoes_de(item.texto_link, item.url)
        snap = PageSnapshot(url=item.url, url_final=res.url_final, http_status=res.status,
                            content_type=res.content_type, content_hash=hashlib.sha256(res.content).hexdigest(),
                            tamanho=len(res.content), titulo=None, coleta_status="ok", erro=None,
                            renderizado_js=False, robots_override=res.robots_override,
                            profundidade=item.profundidade, trilha=item.trilha, secoes=secs,
                            extraido={"documento_direto": True})
        page_id = self._salvar(snap, None, None)
        self._registrar_arquivo(item.url, item.texto_link, secs, res, None, page_id, item.url)
        return None

    # ------------------------------------------------------------ apoio

    def _render(self, url: str, host: str, screenshot: bool) -> FetchResult | None:
        if self._render_indisponivel:
            return None
        if self._renderer is None:
            try:
                self._renderer = self._renderer_factory()
            except Exception as e:  # Playwright/Chromium ausente: segue só com HTML estático
                log.warning("renderizador indisponível: %s", e)
                self._render_indisponivel = True
                return None
        r = self._renderer.render(url, wait_selector=self.wait_selector.get(host), screenshot=screenshot)
        self.stats.renderizacoes += 1
        if r.erro == "render" and r.detalhe_erro and ("Executable" in r.detalhe_erro or "ModuleNotFound" in r.detalhe_erro):
            self._render_indisponivel = True
        return r

    def _salvar(self, snap: PageSnapshot, html: bytes | None, shot: bytes | None) -> str:
        self.stats.paginas += 1
        self.status_paginas[snap.coleta_status] = self.status_paginas.get(snap.coleta_status, 0) + 1
        if snap.trilha and len(snap.trilha) <= 2 and snap.coleta_status == "ok":
            self.paginas_chave_ok.add(snap.url)
        return self.sink.save_page(snap, html, shot)

    def _dominios_proprios(self) -> set[str]:
        out = set()
        site = self.e.urls.get("site_oficial")
        if site:
            out.add("*." + registrable_domain(host_of(site)))
        for u in self.e.urls.values():
            out.add(host_of(u))
        for d in self.e.dominios_extra:
            out.add("*." + d.lower())
        return out

    def _segue_links(self, url: str) -> bool:
        return self._permitido(url)

    def _permitido(self, url: str) -> bool:
        h = host_of(url)
        for d in self.proprios:
            if d.startswith("*."):
                base = d[2:]
                if h == base or h.endswith("." + base):
                    return True
            elif h == d:
                return True
        return False

    def _finalizar(self) -> None:
        st = self.stats
        st.secoes_encontradas = sorted(s for s, n in self.cobertura.items() if n > 0)
        st.secoes_ausentes = sorted(s for s, n in self.cobertura.items() if n == 0)
        portal_host = host_of(self.e.urls.get("portal_transparencia", "")) or None
        fps = self.fingerprints.get(portal_host) if portal_host else None
        if fps is None and self.fingerprints:
            fps = next(iter(self.fingerprints.values()))
        st.plataforma_detectada = fps.plataforma if fps else None
        st.fingerprint = {h: f.__dict__ for h, f in self.fingerprints.items()}
        st.requisicoes = self.http.contador
        ok = self.status_paginas.get("ok", 0)
        if ok == 0:
            st.status = "falhou"
            st.erro = st.erro or f"nenhuma página útil coletada: {self.status_paginas}"
        elif st.bloqueios_robots or not self.paginas_chave_ok:
            # links quebrados em níveis profundos são achado (evidência), não falha do job
            st.status = "parcial"
        else:
            st.status = "concluido"
