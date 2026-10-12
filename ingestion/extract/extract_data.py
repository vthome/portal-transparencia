"""Coleta de UM ente: liga crawler + storage + repositório.

`collect_entity` é a unidade de trabalho que uma fila executaria em produção
(um job = um ente numa rodada). Erros inesperados são capturados aqui e o job
termina como `falhou` com a mensagem — um ente nunca derruba a rodada.
"""
from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Callable

from ingestion.extract.crawler import Crawler
from ingestion.extract.http_client import HttpClient
from ingestion.extract.renderer import PlaywrightRenderer, RendererProtocol
from ingestion.load.load_postgres import Repository
from ingestion.load.storage import Storage
from ingestion.models import CrawlStats, Entity, FileArtifact, PageSnapshot
from ingestion.settings import Settings
from ingestion.transform.normalize import host_of

log = logging.getLogger(__name__)


class RepoSink:
    """Implementa o `Sink` do crawler: grava bytes no Storage e metadados no repositório."""

    def __init__(self, repo: Repository, storage: Storage, job_id: str, entity_id: str) -> None:
        self.repo, self.storage, self.job_id, self.entity_id = repo, storage, job_id, entity_id

    def save_page(self, snap: PageSnapshot, html: bytes | None, screenshot: bytes | None) -> str:
        if html:
            snap.storage_path = self.storage.put(html, "html", "text/html; charset=utf-8")
        if screenshot:
            snap.screenshot_path = self.storage.put(screenshot, "png", "image/png")
        return self.repo.save_page(self.job_id, self.entity_id, snap)

    def save_file(self, art: FileArtifact, content: bytes | None, page_id: str) -> str:
        if content:
            art.storage_path = self.storage.put(content, art.extensao, art.mime)
        return self.repo.save_file(page_id, self.entity_id, art)

    def previous_page(self, url: str) -> dict[str, Any] | None:
        return self.repo.previous_page(self.entity_id, url)

    def previous_file(self, url: str) -> dict[str, Any] | None:
        return self.repo.previous_file(self.entity_id, url)


def default_renderer_factory(entity: Entity, settings: Settings) -> Callable[[], RendererProtocol]:
    return lambda: PlaywrightRenderer(settings, verify_tls=entity.verificar_tls)


def collect_entity(
    entity: Entity,
    run_id: str,
    repo: Repository,
    storage: Storage,
    settings: Settings,
    http_factory: Callable[[], HttpClient] | None = None,
    renderer_factory: Callable[[], RendererProtocol] | None = None,
) -> CrawlStats:
    t0 = time.monotonic()
    repo.upsert_entity(entity)
    job_id = repo.start_job(run_id, entity.id)
    # um cliente por ente: contadores, robots e circuito ficam isolados por job
    http = http_factory() if http_factory else HttpClient(settings)
    if renderer_factory is None and settings.renderizar:
        renderer_factory = default_renderer_factory(entity, settings)
    try:
        crawler = Crawler(entity, http, RepoSink(repo, storage, job_id, entity.id), settings,
                          renderer_factory=renderer_factory)
        out = crawler.run()
        stats = out.stats
        for u in out.urls:  # plataforma detectada por host vai para entity_url
            f = out.fingerprints.get(host_of(u.url))
            if f and f.plataforma:
                u.plataforma = f.plataforma
        repo.sync_entity_urls(entity.id, out.urls)
    except Exception as e:  # pragma: no cover - rede de segurança
        log.exception("falha na coleta de %s", entity.id)
        stats = CrawlStats(status="falhou", erro=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"[:2000])
    stats.duracao_s = round(time.monotonic() - t0, 2)
    repo.finish_job(job_id, stats)
    log.info("%s: %s — %d páginas, %d arquivos, %d req, %d render, robots=%d, %.1fs",
             entity.id, stats.status, stats.paginas, stats.arquivos, stats.requisicoes,
             stats.renderizacoes, stats.bloqueios_robots, stats.duracao_s)
    return stats
