"""Núcleo do dashboard: configuração, cliente da API, cache, formatos e gráficos.

Fica separado das páginas para ser testável sem a interface (tests/test_dashboard.py).
O dashboard só conversa com a API (FastAPI no Render) — nunca com o banco.
"""
