"""Serviço de embeddings da busca semântica: transforma a consulta do usuário em vetor.

Por que existe: a API roda no plano gratuito do Render (512 MB de RAM) e o SERAFIM precisa de
~1,5 GB. Os vetores dos trechos já estão no banco (gerados pela Conclusão, no GitHub Actions);
falta só vetorizar a consulta, com o MESMO modelo, para comparar no pgvector. Este serviço faz
só isso e roda num host com memória suficiente (ver docs/06-busca-semantica-remota.md).

Contrato (o mesmo que api/embedder_remoto.py espera):
    GET  /health  -> {"status": "ok", "modelo": "...", "dim": 768}
    POST /embed   -> corpo {"textos": ["..."]}, cabeçalho "Authorization: Bearer <EMBEDDINGS_TOKEN>"
                     resposta {"modelo": "...", "dim": 768, "vetores": [[...], ...]}

Variáveis de ambiente:
    EMBEDDINGS_MODELO   padrão PORTULAN/serafim-100m-portuguese-pt-sentence-encoder-ir
    EMBEDDINGS_TOKEN    obrigatório; o mesmo valor vai para EMBEDDINGS_REMOTO_TOKEN na API
    EMBEDDINGS_MAX_TEXTOS (16) e EMBEDDINGS_MAX_CARACTERES (2000): limites por chamada
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Callable, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

log = logging.getLogger("embeddings")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MODELO_PADRAO = "PORTULAN/serafim-100m-portuguese-pt-sentence-encoder-ir"


class Modelo(Protocol):
    nome: str
    dim: int

    def encode(self, textos: list[str]) -> list[list[float]]: ...


class SerafimLocal:
    """Mesma chamada da Conclusão (ingestion/embeddings/generate_embeddings.py,
    SentenceTransformerEmbedder.encode): vetores normalizados (normalize_embeddings=True).
    Se mudar lá, mude aqui — senão a consulta e os trechos deixam de ser comparáveis."""

    def __init__(self, modelo: str) -> None:
        from sentence_transformers import SentenceTransformer  # import pesado só aqui

        self.nome = modelo
        self._m = SentenceTransformer(modelo, device="cpu")
        f = getattr(self._m, "get_embedding_dimension", None) or self._m.get_sentence_embedding_dimension
        self.dim = int(f())

    def encode(self, textos: list[str]) -> list[list[float]]:
        vs = self._m.encode(textos, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
        return [list(map(float, v)) for v in vs]


class Pedido(BaseModel):
    textos: list[str] = Field(..., min_length=1, description="textos a vetorizar (ex.: a consulta da busca)")


class Resposta(BaseModel):
    modelo: str
    dim: int
    vetores: list[list[float]]


def criar_app(carregar: Callable[[str], Modelo] = SerafimLocal, token: str | None = None,
              modelo: str | None = None, max_textos: int | None = None,
              max_caracteres: int | None = None) -> FastAPI:
    token = token if token is not None else os.getenv("EMBEDDINGS_TOKEN", "")
    modelo = modelo or os.getenv("EMBEDDINGS_MODELO", MODELO_PADRAO)
    max_textos = max_textos or int(os.getenv("EMBEDDINGS_MAX_TEXTOS", "16"))
    max_caracteres = max_caracteres or int(os.getenv("EMBEDDINGS_MAX_CARACTERES", "2000"))
    if not token:
        # serviço público sem token = qualquer um gasta a sua cota de processamento
        raise SystemExit("defina EMBEDDINGS_TOKEN (o mesmo valor de EMBEDDINGS_REMOTO_TOKEN na API)")
    estado: dict[str, Modelo] = {}

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        t0 = time.monotonic()
        estado["m"] = carregar(modelo)          # carrega na partida: a 1ª consulta já sai rápida
        log.info("modelo %s carregado (%d dimensões) em %.1f s", modelo, estado["m"].dim, time.monotonic() - t0)
        yield

    app = FastAPI(title="Embeddings — Monitor de Transparência", version="1.0", lifespan=ciclo,
                  description="Vetoriza a consulta da busca semântica com o mesmo modelo dos trechos.")

    def autoriza(authorization: str | None = Header(None)) -> None:
        esperado = f"Bearer {token}"
        if not (authorization and secrets.compare_digest(authorization.encode(), esperado.encode())):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token ausente ou inválido",
                                headers={"WWW-Authenticate": "Bearer"})

    @app.get("/health")
    def health() -> dict:
        m = estado.get("m")
        return {"status": "ok" if m else "carregando", "modelo": modelo, "dim": m.dim if m else None}

    @app.post("/embed", response_model=Resposta, dependencies=[Depends(autoriza)])
    def embed(p: Pedido) -> dict:
        if len(p.textos) > max_textos:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"no máximo {max_textos} textos por chamada")
        if any(len(t) > max_caracteres for t in p.textos):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"textos de até {max_caracteres} caracteres")
        m = estado["m"]
        return {"modelo": m.nome, "dim": m.dim, "vetores": m.encode(p.textos)}

    return app


if os.getenv("EMBEDDINGS_TOKEN"):      # uvicorn app:app (no container); nos testes, use criar_app()
    app = criar_app()
