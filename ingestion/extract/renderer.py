"""Renderização com Playwright (Chromium headless) para SPAs e screenshots de evidência.

É o recurso mais caro do pipeline, então:
- só é usado quando o fingerprint indica JS ou o HTML estático veio vazio;
- um navegador por thread (sync API do Playwright não é thread-safe), reaproveitado
  entre páginas do mesmo ente;
- import tardio: rodar testes e coleta estática não exige o Chromium instalado.

Interface mínima (`render`) para que o crawler possa receber um renderer falso nos testes.
"""
from __future__ import annotations

import time
from typing import Protocol

from ingestion.models import FetchResult
from ingestion.settings import Settings, get_settings


class RendererProtocol(Protocol):
    def render(self, url: str, wait_selector: str | None = None, screenshot: bool = False) -> FetchResult: ...
    def close(self) -> None: ...


class PlaywrightRenderer:
    def __init__(self, settings: Settings | None = None, verify_tls: bool = True) -> None:
        self.s = settings or get_settings()
        self._verify_tls = verify_tls
        self._pw = self._browser = self._context = None

    def _ensure(self) -> None:
        if self._browser:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        self._context = self._browser.new_context(
            user_agent=self.s.user_agent, locale="pt-BR", viewport={"width": 1366, "height": 900},
            ignore_https_errors=not self._verify_tls,
        )
        # não baixa mídia pesada: só precisamos do DOM e de um screenshot legível
        self._context.route("**/*.{mp4,webm,mp3,woff,woff2,ttf}", lambda r: r.abort())

    def render(self, url: str, wait_selector: str | None = None, screenshot: bool = False) -> FetchResult:
        res = FetchResult(url=url, renderizado_js=True, tentativas=1)
        t0 = time.monotonic()
        try:
            self._ensure()
            page = self._context.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=self.s.timeout_s * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass  # portais com polling nunca ficam "idle"; seguimos com o DOM atual
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=8_000)
                    except Exception:
                        pass
                html = page.content()
                res.status = resp.status if resp else 200
                res.url_final = page.url
                res.headers = {k.lower(): v for k, v in (resp.headers.items() if resp else [])}
                res.content_type = "text/html; charset=utf-8"
                res.content = html.encode("utf-8")
                if screenshot:
                    # 1ª dobra: evidência do quesito 1.2 (acesso ao portal visível na capa)
                    res.screenshot = page.screenshot(full_page=False, type="png")
                if res.status >= 400:
                    res.erro = f"http_{res.status}"
            finally:
                page.close()
        except Exception as e:
            nome = type(e).__name__
            res.erro = "timeout" if "Timeout" in nome else "render"
            res.detalhe_erro = f"{nome}: {e}"[:300]
        res.duracao_s = round(time.monotonic() - t0, 3)
        return res

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                obj and obj.close()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._context = None
