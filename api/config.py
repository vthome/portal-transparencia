"""Configuração da API (variáveis de ambiente; no Render, em Environment)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

try:  # .env só em desenvolvimento; no Render as variáveis vêm do painel
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _lista(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@dataclass(frozen=True)
class ApiSettings:
    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL"))
    # escrita (POST /revisoes) exige X-API-Key = API_TOKEN_REVISAO; sem ele a revisão fica desligada
    token_revisao: str | None = field(default_factory=lambda: os.getenv("API_TOKEN_REVISAO") or None)
    # opcional: se definido, TODAS as rotas (menos /health) exigem X-API-Key = API_TOKEN_LEITURA
    token_leitura: str | None = field(default_factory=lambda: os.getenv("API_TOKEN_LEITURA") or None)
    cors_origins: list[str] = field(default_factory=lambda: _lista(os.getenv("API_CORS_ORIGINS")))
    # busca: "texto" (full-text do Postgres, leve — padrão no Render Free/Starter),
    # "modelo" (SERAFIM carregado na API: precisa de ~1,5 GB de RAM) ou "hash" (testes)
    busca: str = field(default_factory=lambda: os.getenv("API_BUSCA", "texto").lower())
    embeddings_modelo: str = field(default_factory=lambda: os.getenv(
        "EMBEDDINGS_MODELO", "PORTULAN/serafim-100m-portuguese-pt-sentence-encoder-ir"))
    embeddings_dim: int = field(default_factory=lambda: int(os.getenv("EMBEDDINGS_DIM", "768")))
    # Storage (links assinados para screenshots, HTML e arquivos guardados)
    supabase_url: str | None = field(default_factory=lambda: os.getenv("SUPABASE_URL"))
    supabase_secret_key: str | None = field(default_factory=lambda: os.getenv("SUPABASE_SECRET_KEY"))
    supabase_bucket: str = field(default_factory=lambda: os.getenv("SUPABASE_BUCKET", "snapshots"))
    link_expira_s: int = field(default_factory=lambda: int(os.getenv("API_LINK_EXPIRA_S", "3600")))
    pool_max: int = field(default_factory=lambda: int(os.getenv("API_POOL_MAX", "5")))
    statement_timeout_ms: int = field(default_factory=lambda: int(os.getenv("API_STATEMENT_TIMEOUT_MS", "15000")))


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
