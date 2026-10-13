"""Configuração por variáveis de ambiente (12-factor). Nada de segredo em YAML."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # .env local é conveniência de desenvolvimento; em CI/Render usam-se secrets
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(__file__).resolve().parent / "config"
ENGINE_VERSION = "coleta-0.1.0"


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "sim", "yes"}


@dataclass(frozen=True)
class Settings:
    # Identificação do robô (obrigatória por boa prática; contato permite ao gestor reclamar)
    user_agent: str = field(default_factory=lambda: os.getenv(
        "CRAWLER_USER_AGENT",
        "MonitorTransparenciaBot/0.1 (+https://github.com/SEU_USUARIO/portal-transparencia)"))
    # Limites de coleta
    req_por_segundo: float = field(default_factory=lambda: float(os.getenv("COLETA_REQ_POR_SEGUNDO", "1")))
    timeout_s: float = field(default_factory=lambda: float(os.getenv("COLETA_TIMEOUT_S", "25")))
    max_bytes: int = field(default_factory=lambda: int(os.getenv("COLETA_MAX_BYTES", str(20 * 1024 * 1024))))
    tentativas: int = field(default_factory=lambda: int(os.getenv("COLETA_TENTATIVAS", "3")))
    max_paginas: int = field(default_factory=lambda: int(os.getenv("COLETA_MAX_PAGINAS", "60")))
    max_profundidade: int = field(default_factory=lambda: int(os.getenv("COLETA_MAX_PROFUNDIDADE", "3")))
    max_arquivos: int = field(default_factory=lambda: int(os.getenv("COLETA_MAX_ARQUIVOS", "30")))
    # fração do orçamento de páginas reservada ao(s) host(s) do portal da transparência;
    # o site institucional (notícias, agenda...) só usa o restante enquanto houver fila no portal
    fracao_portal: float = field(default_factory=lambda: float(os.getenv("COLETA_FRACAO_PORTAL", "0.5")))
    paralelismo_entes: int = field(default_factory=lambda: int(os.getenv("COLETA_PARALELISMO", "3")))
    renderizar: bool = field(default_factory=lambda: _bool("COLETA_RENDERIZAR", True))
    screenshots: bool = field(default_factory=lambda: _bool("COLETA_SCREENSHOTS", True))
    # Persistência
    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL"))
    supabase_url: str | None = field(default_factory=lambda: os.getenv("SUPABASE_URL"))
    # chave secreta do Supabase (sb_secret_...); a legada service_role também funciona aqui
    supabase_secret_key: str | None = field(default_factory=lambda: os.getenv("SUPABASE_SECRET_KEY"))
    supabase_bucket: str = field(default_factory=lambda: os.getenv("SUPABASE_BUCKET", "snapshots"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("COLETA_DATA_DIR", str(ROOT / "data"))))
    # Camada 2 — embeddings (a dimensão precisa bater com monitor.chunk.embedding)
    embeddings_modelo: str = field(default_factory=lambda: os.getenv(
        "EMBEDDINGS_MODELO", "PORTULAN/serafim-100m-portuguese-pt-sentence-encoder-ir"))
    embeddings_dim: int = field(default_factory=lambda: int(os.getenv("EMBEDDINGS_DIM", "768")))
    embeddings_lote: int = field(default_factory=lambda: int(os.getenv("EMBEDDINGS_LOTE", "32")))
    embeddings_device: str | None = field(default_factory=lambda: os.getenv("EMBEDDINGS_DEVICE") or None)


def get_settings() -> Settings:
    return Settings()
