"""API do Monitor de Conformidade de Portais da Transparência (RJ).

    uvicorn api.main:app --reload                 # local (lê DATABASE_URL do .env)
    Swagger: http://127.0.0.1:8000/docs  ·  OpenAPI: /openapi.json

No Render: `uvicorn api.main:app --host 0.0.0.0 --port $PORT` (ver render.yaml).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from api import __version__
from api.config import ApiSettings, get_api_settings
from api.database import Repo, criar_pool
from api.deps import get_settings
from api.routers import (
    avaliacoes,
    documentos,
    entes,
    indicadores,
    monitoramento,
    revisoes,
)
from api.schemas import Health

log = logging.getLogger("api")

DESCRICAO = """
Consulta às avaliações de conformidade dos portais da transparência de 7 municípios do RJ
(Rio de Janeiro, Niterói, Maricá, Duque de Caxias, Itaboraí, Japeri e Cachoeiras de Macacu).

* **Leitura** aberta por padrão (dados públicos); se o servidor definir `API_TOKEN_LEITURA`, envie `X-API-Key`.
* **Revisão humana** (`POST /revisoes`) exige `X-API-Key` = `API_TOKEN_REVISAO`.
* Scores de 0 a 100; `score_min`/`score_max` são a faixa de incerteza dos quesitos não verificáveis;
  `cobertura` é o % do peso que pôde ser verificado.
"""

TAGS = [
    {"name": "entes", "description": "Municípios monitorados e a avaliação mais recente."},
    {"name": "indicadores", "description": "Comparações entre entes: ranking, dimensões, status."},
    {"name": "avaliações", "description": "Histórico, resultados por quesito, diff entre rodadas e timeline."},
    {"name": "revisão humana", "description": "Fila de baixa confiança e correção de vereditos."},
    {"name": "documentos", "description": "Cópias guardadas das evidências e busca nos trechos coletados."},
    {"name": "monitoramento", "description": "Saúde das rodadas de coleta e checklist em vigor."},
    {"name": "sistema", "description": "Saúde da API."},
]


def create_app(settings: ApiSettings | None = None, repo=None) -> FastAPI:
    """`repo` permite injetar um repositório (testes); sem ele, abre o pool do DATABASE_URL."""
    s = settings or get_api_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = None
        if repo is not None:
            app.state.repo = repo
        elif s.database_url:
            pool = criar_pool(s)
            pool.open(wait=False)
            app.state.repo = Repo(pool)
        else:
            log.warning("DATABASE_URL não definida: as rotas de dados responderão 503")
            app.state.repo = None
        yield
        if pool is not None:
            pool.close()

    app = FastAPI(title="Monitor de Conformidade — Portais da Transparência (RJ)", version=__version__,
                  description=DESCRICAO, openapi_tags=TAGS, lifespan=lifespan,
                  contact={"name": "MPRJ — projeto Monitor de Transparência"})
    app.dependency_overrides[get_settings] = lambda: s
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST"],
                           allow_headers=["X-API-Key", "Content-Type"])

    for r in (entes, indicadores, avaliacoes, revisoes, documentos, monitoramento):
        app.include_router(r.router)

    @app.exception_handler(Exception)
    async def erro_banco(request: Request, exc: Exception):
        # falhas de conexão/timeout do Postgres viram 503 (o dashboard mostra "tente de novo")
        nome = type(exc).__module__ + "." + type(exc).__name__
        if nome.startswith(("psycopg", "psycopg_pool")):
            log.exception("erro de banco em %s", request.url.path)
            return JSONResponse({"detail": "banco de dados indisponível no momento"}, status_code=503)
        log.exception("erro inesperado em %s", request.url.path)
        return JSONResponse({"detail": "erro interno"}, status_code=500)

    @app.get("/", include_in_schema=False)
    def raiz():
        return RedirectResponse("/docs")

    @app.get("/health", response_model=Health, tags=["sistema"], summary="Saúde da API e do banco",
             responses={503: {"description": "banco indisponível"}})
    def health(request: Request, cfg: ApiSettings = Depends(get_settings)):
        r = getattr(request.app.state, "repo", None)
        remota = cfg.busca == "remoto" and bool(cfg.embeddings_remoto_url and cfg.embeddings_remoto_token)
        base = {"versao": __version__,
                "busca": "semantica" if cfg.busca in {"modelo", "hash"} else "semantica_remota" if remota else "texto"}
        if r is None:
            return JSONResponse({**base, "status": "degradado", "banco": "não configurado"}, status_code=503)
        try:
            p = r.ping()
        except Exception as e:  # noqa: BLE001
            log.warning("health: banco indisponível: %s", e)
            return JSONResponse({**base, "status": "degradado", "banco": "indisponível"}, status_code=503)
        return {**base, "status": "ok", "banco": "ok", "ultima_rodada": p["ultima_rodada"],
                "avaliacoes": p["avaliacoes"], "schema_v03": p["schema_v03"]}

    return app


app = create_app()
