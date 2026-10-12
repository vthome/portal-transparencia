"""Identificação da plataforma do portal (fingerprint).

Por que importa: em escala, avaliamos *fornecedores + exceções*. Saber que um portal é
Betha ou GeoSiap diz ao crawler se precisa de Playwright, que seletor esperar e quais
rotas visitar primeiro — e permite medir acerto por plataforma.
"""
from __future__ import annotations

import re
from typing import Any

from ingestion.models import FetchResult, Fingerprint
from ingestion.transform.normalize import host_of
from ingestion.transform.validate import load_platforms

_GENERATOR = re.compile(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', re.I)
_GENERATOR2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']generator["\']', re.I)
_TAGS = re.compile(r"<[^>]+>")


def looks_like_spa(html: str, url: str) -> bool:
    """HTML que depende de JS: pouco texto visível, muitos scripts, ou rota com hash."""
    if re.search(r"#!?/", url):
        return True
    corpo = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    texto = " ".join(_TAGS.sub(" ", corpo).split())
    n_scripts = len(re.findall(r"(?i)<script", html))
    raiz_vazia = re.search(r'<div id="(root|app|__next)">\s*</div>|<app-root[^>]*>\s*</app-root>', html, re.I)
    return bool(raiz_vazia) or (len(texto) < 300 and n_scripts >= 3)


def detect(res: FetchResult, cfg: dict[str, Any] | None = None) -> Fingerprint:
    cfg = cfg or load_platforms()
    url = res.url_final or res.url
    host = host_of(url)
    html = res.texto[:400_000] if res.content else ""
    headers = "\n".join(f"{k}: {v}" for k, v in res.headers.items()).lower()
    gen = (_GENERATOR.search(html) or _GENERATOR2.search(html))
    generator = gen.group(1).lower() if gen else ""

    melhor: tuple[str | None, int, list[str]] = (None, 0, [])
    melhor_generica: tuple[str | None, int, list[str]] = (None, 0, [])
    for nome, p in cfg["plataformas"].items():
        pontos, sinais = 0, []
        for s in p.get("sinais", []):
            alvo = {"host": host, "url": url, "html": html, "meta_generator": generator}.get(s["tipo"])
            if s["tipo"] == "header":
                hit = re.search(s["valor"], headers, re.I | re.M)
            else:
                hit = re.search(s["valor"], alvo or "", re.I)
            if hit:
                pontos += s["peso"]
                sinais.append(f"{s['tipo']}:{s['valor']}")
        if p.get("generica"):
            if pontos > melhor_generica[1]:
                melhor_generica = (nome, pontos, sinais)
        elif pontos > melhor[1]:
            melhor = (nome, pontos, sinais)

    # fornecedor específico vence assinatura genérica (ex.: Betha também é SPA com hash)
    limiar = cfg.get("limiar", 3)
    nome, pontos, sinais = melhor if melhor[1] >= limiar else melhor_generica
    if pontos < limiar:
        nome = None
    plat = cfg["plataformas"].get(nome, {}) if nome else {}
    spa = looks_like_spa(html, url) if html else False
    return Fingerprint(
        plataforma=nome, pontuacao=pontos, sinais=sinais,
        requires_js=bool(plat.get("requires_js")) or spa, spa_detectada=spa,
        wait_selector=plat.get("wait_selector"),
    )
