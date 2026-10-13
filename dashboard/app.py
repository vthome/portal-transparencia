"""Dashboard do Monitor de Conformidade de Portais da Transparência (RJ).

    streamlit run dashboard/app.py

Lê tudo da API (FastAPI no Render). Configuração em .streamlit/secrets.toml (local) ou nos
Secrets do Streamlit Cloud: API_URL, API_TOKEN_REVISAO (opcional), REVISAO_SENHA (opcional).
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))     # `painel` importável no Streamlit Cloud e nos testes

st.set_page_config(page_title="Monitor de Transparência · RJ", page_icon=":material/fact_check:", layout="wide")

paginas = st.navigation([
    st.Page("paginas/visao_geral.py", title="Visão geral", icon=":material/leaderboard:", default=True),
    st.Page("paginas/municipio.py", title="Município", icon=":material/location_city:"),
    st.Page("paginas/evolucao.py", title="Evolução", icon=":material/timeline:"),
    st.Page("paginas/revisao.py", title="Revisão humana", icon=":material/rate_review:"),
    st.Page("paginas/busca.py", title="Busca", icon=":material/search:"),
    st.Page("paginas/monitoramento.py", title="Monitoramento", icon=":material/monitor_heart:"),
], position="top")
paginas.run()
