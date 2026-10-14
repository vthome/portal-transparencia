"""Dependências das rotas: repositório, autenticação por chave, embedder da busca e Storage."""
from __future__ import annotations

import logging
import secrets
import threading
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from api.config import ApiSettings, get_api_settings

log = logging.getLogger("api.deps")

chave_header = APIKeyHeader(name="X-API-Key", auto_error=False,
                            description="API_TOKEN_REVISAO para gravar revisões; API_TOKEN_LEITURA se configurado")


def get_settings() -> ApiSettings:
    return get_api_settings()


def get_repo(request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "banco não configurado (DATABASE_URL)")
    return repo


def _confere(chave: str | None, esperado: str) -> bool:
    return chave is not None and secrets.compare_digest(chave.encode(), esperado.encode())


def exige_leitura(chave: Annotated[str | None, Security(chave_header)],
                  s: Annotated[ApiSettings, Depends(get_settings)]) -> None:
    """Leitura é aberta por padrão (dados públicos de transparência). Com API_TOKEN_LEITURA, exige a chave."""
    if s.token_leitura and not (_confere(chave, s.token_leitura)
                                or (s.token_revisao and _confere(chave, s.token_revisao))):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key ausente ou inválida",
                            headers={"WWW-Authenticate": "API-Key"})


def exige_revisor(chave: Annotated[str | None, Security(chave_header)],
                  s: Annotated[ApiSettings, Depends(get_settings)]) -> None:
    if not s.token_revisao:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "revisão desabilitada: defina API_TOKEN_REVISAO no servidor")
    if not _confere(chave, s.token_revisao):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key ausente ou inválida",
                            headers={"WWW-Authenticate": "API-Key"})


# ---------------------------------------------------------------- embedder (busca semântica)
_emb_lock = threading.Lock()
_embedder = None


def get_embedder(s: Annotated[ApiSettings, Depends(get_settings)]):
    """Carregado uma vez, na primeira busca. None => busca por texto (API_BUSCA=texto).

    `remoto`: a consulta é vetorizada no serviço externo (servicos/embeddings); sem URL ou token
    configurados, a busca fica por texto e o motivo vai para o log."""
    global _embedder
    if s.busca not in {"modelo", "hash", "remoto"}:
        return None
    if s.busca == "remoto" and not (s.embeddings_remoto_url and s.embeddings_remoto_token):
        log.warning("API_BUSCA=remoto sem EMBEDDINGS_REMOTO_URL/EMBEDDINGS_REMOTO_TOKEN: busca por texto")
        return None
    with _emb_lock:
        if _embedder is None:
            if s.busca == "remoto":
                from api.embedder_remoto import RemotoEmbedder

                _embedder = RemotoEmbedder(s.embeddings_remoto_url, s.embeddings_remoto_token,
                                           s.embeddings_modelo, s.embeddings_dim, s.embeddings_remoto_timeout_s)
            else:
                from ingestion.embeddings.generate_embeddings import (
                    HashEmbedder,
                    SentenceTransformerEmbedder,
                )

                _embedder = HashEmbedder(s.embeddings_dim) if s.busca == "hash" else \
                    SentenceTransformerEmbedder(s.embeddings_modelo, s.embeddings_dim)
    return _embedder


# ---------------------------------------------------------------- Storage (links assinados)
class Assinador:
    """Gera URL temporária para um objeto do bucket privado (screenshots, HTML, PDFs)."""

    def __init__(self, s: ApiSettings, session=None) -> None:
        import requests

        self.base = s.supabase_url.rstrip("/") + "/storage/v1"
        self.bucket = s.supabase_bucket
        self.expira = s.link_expira_s
        self.http = session or requests.Session()
        self.http.headers["apikey"] = s.supabase_secret_key
        if not s.supabase_secret_key.startswith("sb_"):        # chave legada service_role (JWT)
            self.http.headers["Authorization"] = f"Bearer {s.supabase_secret_key}"

    def assinar(self, chave: str) -> str:
        r = self.http.post(f"{self.base}/object/sign/{self.bucket}/{chave}", json={"expiresIn": self.expira},
                           timeout=15)
        if r.status_code == 404 or (r.status_code == 400 and "not found" in r.text.lower()):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "objeto não encontrado no Storage")
        if r.status_code >= 400:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Storage respondeu {r.status_code}")
        caminho = r.json().get("signedURL") or r.json().get("signedUrl")
        return caminho if caminho.startswith("http") else self.base + caminho


def get_assinador(request: Request, s: Annotated[ApiSettings, Depends(get_settings)]) -> Assinador:
    a = getattr(request.app.state, "assinador", None)
    if a is not None:
        return a
    if not (s.supabase_url and s.supabase_secret_key):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Storage não configurado: defina SUPABASE_URL e SUPABASE_SECRET_KEY no servidor")
    request.app.state.assinador = Assinador(s)
    return request.app.state.assinador
