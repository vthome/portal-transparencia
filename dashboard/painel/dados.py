"""Acesso aos dados com cache (5 min) e tratamento de erro padronizado para as páginas.

Funções com `_api` no 1º argumento: o Streamlit não tenta "hashear" o cliente; a chave do
cache é `base` (a URL da API) + os demais argumentos.
"""
from __future__ import annotations

from typing import Any, Callable

import streamlit as st

from painel.cliente import ApiCliente, ApiErro, ApiIndisponivel
from painel.config import Config, carregar_config

TTL = 300


def config() -> Config:
    cfg = st.session_state.get("_config_painel")
    return cfg if isinstance(cfg, Config) else carregar_config()


def api() -> ApiCliente:
    """Cliente da sessão. Os testes injetam um cliente em st.session_state['_cliente_api']."""
    injetado = st.session_state.get("_cliente_api")
    if injetado is not None:
        return injetado
    cfg = config()
    return _cliente(cfg.api_url, cfg.api_token_revisao, cfg.timeout_s)


@st.cache_resource(show_spinner=False)
def _cliente(url: str, token: str | None, timeout: float) -> ApiCliente:
    return ApiCliente(url, token=token, timeout=timeout)


def carregar(fn: Callable[..., Any], *args, msg: str = "Consultando a API…", **kw) -> Any:
    """Chama a API; em falha mostra a mensagem certa e interrompe a página (st.stop)."""
    a = api()
    try:
        with st.spinner(msg):
            return fn(a, a.base_url, *args, **kw)
    except ApiIndisponivel as e:
        st.error(
            f"**A API não respondeu** ({e.detalhe}). No plano gratuito do Render ela dorme após ~15 min sem uso "
            "e leva até 1 minuto para acordar: aguarde e clique em **Tentar de novo**.", icon=":material/cloud_off:")
        if st.button("Tentar de novo", icon=":material/refresh:"):
            st.cache_data.clear()
            st.rerun()
        st.stop()
    except ApiErro as e:
        st.error(f"A API recusou a consulta ({e.status}): {e.detalhe}", icon=":material/error:")
        st.stop()


def limpar_cache() -> None:
    st.cache_data.clear()


# ------------------------------------------------------------------ consultas com cache
@st.cache_data(ttl=TTL, show_spinner=False)
def health(_api: ApiCliente, base: str) -> dict:
    return _api.health()


@st.cache_data(ttl=TTL, show_spinner=False)
def entes(_api: ApiCliente, base: str) -> list[dict]:
    return _api.entes()


@st.cache_data(ttl=TTL, show_spinner=False)
def ente(_api: ApiCliente, base: str, slug: str) -> dict:
    return _api.ente(slug)


@st.cache_data(ttl=TTL, show_spinner=False)
def ranking(_api: ApiCliente, base: str, dimensao: str | None = None) -> list[dict]:
    return _api.ranking(dimensao)


@st.cache_data(ttl=TTL, show_spinner=False)
def matriz_dimensoes(_api: ApiCliente, base: str) -> dict:
    return _api.matriz_dimensoes()


@st.cache_data(ttl=TTL, show_spinner=False)
def rotulos(_api: ApiCliente, base: str) -> dict[str, str]:
    return _api.rotulos_dimensoes()


@st.cache_data(ttl=TTL, show_spinner=False)
def status_por_ente(_api: ApiCliente, base: str) -> list[dict]:
    return _api.status_por_ente()


@st.cache_data(ttl=TTL, show_spinner=False)
def historico(_api: ApiCliente, base: str, slug: str) -> list[dict]:
    return _api.historico(slug)


@st.cache_data(ttl=TTL, show_spinner=False)
def resultados(_api: ApiCliente, base: str, slug: str, run_id: int | None = None) -> list[dict]:
    return _api.resultados(slug, run_id)


@st.cache_data(ttl=TTL, show_spinner=False)
def diff(_api: ApiCliente, base: str, slug: str, de: int | None, para: int | None) -> dict:
    return _api.diff(slug, de, para)


@st.cache_data(ttl=TTL, show_spinner=False)
def timeline(_api: ApiCliente, base: str, slug: str, codigo: str) -> list[dict]:
    return _api.timeline(slug, codigo)


@st.cache_data(ttl=TTL, show_spinner=False)
def resultado(_api: ApiCliente, base: str, resultado_id: int) -> dict:
    return _api.resultado(resultado_id)


@st.cache_data(ttl=60, show_spinner=False)
def fila(_api: ApiCliente, base: str, ente: str | None, incluir_nv: bool, limite: int) -> list[dict]:
    return _api.fila(ente, incluir_nv, limite)


@st.cache_data(ttl=60, show_spinner=False)
def revisoes(_api: ApiCliente, base: str, ente: str | None = None, limite: int = 200) -> list[dict]:
    return _api.revisoes(ente, limite)


@st.cache_data(ttl=60, show_spinner=False)
def concordancia(_api: ApiCliente, base: str) -> dict:
    return _api.concordancia()


@st.cache_data(ttl=TTL, show_spinner=False)
def busca(_api: ApiCliente, base: str, q: str, ente: str | None, k: int, modo: str) -> dict:
    return _api.busca(q, ente, k, modo)


@st.cache_data(ttl=TTL, show_spinner=False)
def rodadas(_api: ApiCliente, base: str, limite: int = 20) -> list[dict]:
    return _api.rodadas(limite)


@st.cache_data(ttl=TTL, show_spinner=False)
def rodada(_api: ApiCliente, base: str, run_id: int) -> list[dict]:
    return _api.rodada(run_id)


@st.cache_data(ttl=3600, show_spinner=False)
def checklist(_api: ApiCliente, base: str) -> list[dict]:
    return _api.checklist()


def link_evidencia(evidencia_id: int, tipo: str | None = None) -> dict | None:
    """Sem cache: o link assinado expira. Erros viram None (a página mostra só o link original)."""
    try:
        return api().link_evidencia(evidencia_id, tipo)
    except ApiErro:
        return None
