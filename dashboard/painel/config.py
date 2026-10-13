"""Configuração: variáveis de ambiente > secrets (Streamlit Cloud ou .streamlit/secrets.toml) > padrão local.

No Streamlit Cloud os secrets de nível raiz também viram variáveis de ambiente, então a ordem
não muda nada lá; localmente, `scripts/dashboard.ps1 -Api <url>` consegue trocar a API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

PADRAO_API = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class Config:
    api_url: str = PADRAO_API
    api_token_revisao: str | None = None      # libera POST /revisoes (fica no servidor do Streamlit)
    revisao_senha: str | None = None          # senha que o revisor digita no app para liberar o formulário
    timeout_s: float = 90.0                   # o plano free do Render pode levar ~1 min para acordar

    @property
    def revisao_habilitada(self) -> bool:
        return bool(self.api_token_revisao)


def _segredo(nome: str) -> str | None:
    valor = os.getenv(nome)
    if valor in (None, ""):
        try:
            import streamlit as st

            valor = st.secrets.get(nome)
        except Exception:  # sem secrets.toml o Streamlit levanta erro ao acessar st.secrets
            valor = None
    return str(valor).strip() if valor not in (None, "") else None


def carregar_config() -> Config:
    return Config(api_url=(_segredo("API_URL") or PADRAO_API).rstrip("/"),
                  api_token_revisao=_segredo("API_TOKEN_REVISAO"),
                  revisao_senha=_segredo("REVISAO_SENHA"),
                  timeout_s=float(_segredo("API_TIMEOUT_S") or 90))
