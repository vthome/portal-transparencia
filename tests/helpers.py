"""Infra de teste offline: um 'site falso' montado como adapter do requests.

Nenhum teste acessa a internet. Para simular um portal, registre respostas por URL:

    site = FakeSite()
    site.html("https://www.exemplo.rj.gov.br/", "<html>...</html>")
    site.robots("https://www.exemplo.rj.gov.br", "User-agent: *\\nDisallow: /privado")
    http = make_http(site)
"""
from __future__ import annotations

import io
from dataclasses import replace
from typing import Callable

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from ingestion.extract.http_client import HttpClient
from ingestion.models import FetchResult
from ingestion.settings import Settings
from ingestion.transform.normalize import normalize_url

Handler = Callable[[requests.PreparedRequest], tuple[int, dict, bytes]]


class FakeSite(BaseAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.routes: dict[str, list[Handler]] = {}
        self.calls: list[str] = []

    def _key(self, url: str) -> str:
        return normalize_url(url.split("#")[0]) or url

    def add(self, url: str, status: int = 200, body: bytes | str = b"", headers: dict | None = None,
            times: int | None = None) -> "FakeSite":
        data = body.encode("utf-8") if isinstance(body, str) else body
        h = {"content-type": "text/html; charset=utf-8", **(headers or {})}
        def handler(req):
            return status, h, data
        # `times=n`: resposta consumida n vezes; sem `times`: resposta permanente (fica por último)
        self.routes.setdefault(self._key(url), []).extend([handler] * (times or 1))
        return self

    def html(self, url: str, body: str, **kw) -> "FakeSite":
        return self.add(url, body=body, **kw)

    def robots(self, origin: str, body: str) -> "FakeSite":
        return self.add(origin.rstrip("/") + "/robots.txt", body=body, headers={"content-type": "text/plain"})

    def handler(self, url: str, fn: Handler) -> "FakeSite":
        self.routes.setdefault(self._key(url), []).append(fn)
        return self

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.calls.append(request.url)
        key = self._key(request.url)
        lst = self.routes.get(key)
        if not lst:
            status, headers, body = 404, {"content-type": "text/html"}, b"<html><title>404 Not Found</title></html>"
        else:
            fn = lst.pop(0) if len(lst) > 1 else lst[0]
            out = fn(request)
            if isinstance(out, Exception):
                raise out
            status, headers, body = out
        r = requests.Response()
        r.status_code = status
        r.headers = CaseInsensitiveDict(headers)
        r.raw = io.BytesIO(body)
        r.url = request.url
        r.request = request
        r.reason = {200: "OK", 304: "Not Modified", 404: "Not Found", 503: "Service Unavailable"}.get(status, "")
        r.encoding = None
        return r

    def close(self) -> None:
        pass


def test_settings(**kw) -> Settings:
    base = Settings(user_agent="MonitorTransparenciaBot/test", req_por_segundo=1000, timeout_s=5,
                    tentativas=3, max_paginas=40, max_profundidade=3, max_arquivos=10,
                    paralelismo_entes=2, renderizar=False, screenshots=False)
    return replace(base, **kw)


test_settings.__test__ = False  # não é um teste do pytest


def make_http(site: FakeSite, settings: Settings | None = None, sleeps: list | None = None) -> HttpClient:
    s = requests.Session()
    s.mount("http://", site)
    s.mount("https://", site)
    sleeps = sleeps if sleeps is not None else []
    return HttpClient(settings or test_settings(), session=s, sleep=sleeps.append)


class FakeRenderer:
    """Renderer falso: devolve o HTML 'pós-JS' registrado para a URL."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = {normalize_url(k): v for k, v in pages.items()}
        self.calls: list[str] = []
        self.closed = False

    def render(self, url, wait_selector=None, screenshot=False) -> FetchResult:
        self.calls.append(url)
        html = self.pages.get(normalize_url(url))
        if html is None:
            return FetchResult(url=url, erro="http_404", status=404, renderizado_js=True)
        return FetchResult(url=url, url_final=url, status=200, content=html.encode(),
                           content_type="text/html; charset=utf-8", renderizado_js=True,
                           screenshot=b"\x89PNG-fake" if screenshot else None)

    def close(self) -> None:
        self.closed = True


def page(title: str, body: str, links: list[tuple[str, str]] = (), extra: str = "") -> str:
    """HTML com texto suficiente para não ser classificado como vazio."""
    nav = "".join(f'<li><a href="{h}">{t}</a></li>' for h, t in links)
    filler = "<p>" + ("Conteúdo institucional do município com informações ao cidadão. " * 6) + "</p>"
    return (f"<html lang='pt-BR'><head><title>{title}</title></head><body>"
            f"<header><nav><ul>{nav}</ul></nav></header><main><h1>{title}</h1>{body}{filler}{extra}</main>"
            f"</body></html>")
