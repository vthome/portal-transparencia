"""Gráficos Plotly — funções puras (recebem dados, devolvem go.Figure).

Paleta: série única em azul (slot 1 da paleta de referência), magnitude em rampa sequencial
azul (claro → escuro), status com as cores reservadas de status SEMPRE acompanhadas de rótulo.
Um único eixo por gráfico; tooltips em português; marcas finas.
"""
from __future__ import annotations

import plotly.graph_objects as go

from painel.formatos import ORDEM_STATUS, STATUS, nome_curto, num, rotulo_rodada

AZUL = "#2a78d6"            # série única
AZUL_CLARO = "#86b6ef"      # mesma série, avaliação incompleta (rotulada no texto)
FAIXA = "rgba(42,120,214,0.16)"
CINZA_GRADE = "rgba(128,128,128,0.18)"
RAMPA = [[0.0, "#cde2fb"], [0.17, "#9ec5f4"], [0.33, "#6da7ec"], [0.5, "#3987e5"],
         [0.67, "#256abf"], [0.83, "#184f95"], [1.0, "#0d366b"]]


def _base(fig: go.Figure, altura: int, titulo_x: str | None = None) -> go.Figure:
    fig.update_layout(height=altura, margin=dict(l=8, r=16, t=8, b=8), showlegend=False,
                      hoverlabel=dict(font_size=13), separators=",.", bargap=0.35)
    fig.update_xaxes(gridcolor=CINZA_GRADE, zeroline=False, title=titulo_x)
    fig.update_yaxes(gridcolor=CINZA_GRADE, zeroline=False)
    return fig


def ranking(itens: list[dict], campo: str = "score", titulo_x: str = "Score (0–100)",
            faixa: bool = True) -> go.Figure:
    """Barras horizontais; com `faixa`, a incerteza (mín–máx) aparece como barra de erro."""
    itens = [x for x in itens if x.get(campo) is not None]
    itens = sorted(itens, key=lambda x: x[campo])           # maior no topo
    # o valor vai no rótulo do eixo: dentro ou fora da barra ele colidiria com a barra de erro da faixa
    nomes = [f"{nome_curto(x['nome'])}{'' if x.get('no_ranking', True) else ' (incompleta)'}  <b>{num(x[campo])}</b>"
             for x in itens]
    valores = [x[campo] for x in itens]
    cores = [AZUL if x.get("no_ranking", True) else AZUL_CLARO for x in itens]
    fig = go.Figure(go.Bar(
        x=valores, y=nomes, orientation="h", marker=dict(color=cores, cornerradius=4, line_width=0),
        text=[num(v) for v in valores], textposition="none",
        customdata=[[num(x.get("score_min")), num(x.get("score_max")), num(x.get("cobertura")),
                     x.get("classificacao") or "—", nome_curto(x["nome"])] for x in itens],
        hovertemplate="<b>%{customdata[4]}</b><br>Score: %{text}<br>Faixa: %{customdata[0]} – %{customdata[1]}"
                      "<br>Cobertura: %{customdata[2]}%<br>%{customdata[3]}<extra></extra>"))
    if faixa:
        fig.data[0].error_x = dict(type="data", symmetric=False, thickness=1.5, width=4, color="rgba(128,128,128,0.9)",
                                   array=[(x.get("score_max") or x[campo]) - x[campo] for x in itens],
                                   arrayminus=[x[campo] - (x.get("score_min") or x[campo]) for x in itens])
    _base(fig, 70 + 44 * len(itens), titulo_x)
    fig.update_xaxes(range=[0, 105])
    fig.update_yaxes(ticksuffix="  ")
    return fig


def heatmap_dimensoes(matriz: dict, rotulos: dict[str, str]) -> go.Figure:
    """Município × dimensão, rampa sequencial azul 0–100, valor escrito em cada célula."""
    dims = list(matriz["dimensoes"])
    linhas = sorted(matriz["linhas"], key=lambda x: -(x.get("score") or 0))
    z = [[ln.get(d) for d in dims] for ln in linhas]
    xs = [rotulos.get(d, d) for d in dims]
    ys = [nome_curto(ln["nome"]) for ln in linhas]
    fig = go.Figure(go.Heatmap(
        z=z, x=xs, y=ys, zmin=0, zmax=100, colorscale=RAMPA, xgap=2, ygap=2,
        colorbar=dict(title="Score", thickness=10),
        hovertemplate="<b>%{y}</b><br>%{x}: %{z:.1f}<extra></extra>", hoverongaps=False))
    for i, linha in enumerate(z):
        for j, v in enumerate(linha):
            fig.add_annotation(x=xs[j], y=ys[i], text=num(v, 0) if v is not None else "—", showarrow=False,
                               font=dict(size=12, color="#ffffff" if (v or 0) >= 55 else "#0b0b0b"))
    _base(fig, 90 + 46 * len(linhas))
    fig.update_xaxes(side="top", tickangle=0, showgrid=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig


def status_empilhado(linhas: list[dict]) -> go.Figure:
    """Quesitos por status em cada município (barras empilhadas; legenda com rótulos)."""
    nomes = [nome_curto(x["nome"]) for x in linhas]
    fig = go.Figure()
    for k in ORDEM_STATUS:
        vals = [x.get(k, 0) or 0 for x in linhas]
        if not any(vals):
            continue
        s = STATUS[k]
        fig.add_bar(name=f"{s['simbolo']} {s['rotulo']}", y=nomes, x=vals, orientation="h",
                    marker=dict(color=s["cor"], line_width=0),
                    hovertemplate=f"<b>%{{y}}</b><br>{s['rotulo']}: %{{x}} quesitos<extra></extra>")
    _base(fig, 110 + 40 * len(linhas), "Quesitos")
    fig.update_layout(barmode="stack", showlegend=True,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None, traceorder="normal"))
    fig.update_yaxes(ticksuffix="  ")
    fig.update_yaxes(autorange="reversed")
    return fig


def dimensoes_ente(dimensoes: dict[str, float | None], rotulos: dict[str, str]) -> go.Figure:
    itens = [(rotulos.get(d, d), v) for d, v in dimensoes.items()]
    fig = go.Figure(go.Bar(
        x=[v if v is not None else 0 for _, v in itens], y=[n for n, _ in itens], orientation="h",
        marker=dict(color=AZUL, cornerradius=4, line_width=0),
        text=[num(v) if v is not None else "sem dados" for _, v in itens], textposition="outside",
        cliponaxis=False, hovertemplate="<b>%{y}</b><br>Score: %{text}<extra></extra>"))
    _base(fig, 60 + 46 * len(itens), "Score (0–100)")
    fig.update_xaxes(range=[0, 105])
    fig.update_yaxes(autorange="reversed", ticksuffix="  ")
    return fig


def evolucao(hist: list[dict]) -> go.Figure:
    """Score por rodada (linha) com a faixa de incerteza (área)."""
    xs = [rotulo_rodada(h) for h in hist]
    fig = go.Figure()
    fig.add_scatter(x=xs + xs[::-1], y=[h.get("score_max") for h in hist] + [h.get("score_min") for h in hist][::-1],
                    mode="lines", fill="toself", fillcolor=FAIXA, line=dict(width=0), hoverinfo="skip",
                    name="Faixa de incerteza", showlegend=True)
    fig.add_scatter(x=xs, y=[h.get("score") for h in hist], mode="lines+markers", name="Score",
                    line=dict(color=AZUL, width=2), marker=dict(size=9, color=AZUL, line=dict(width=2, color="white")),
                    customdata=[[num(h.get("score_min")), num(h.get("score_max")), num(h.get("cobertura")),
                                 h.get("classificacao") or "—"] for h in hist],
                    hovertemplate="<b>%{x}</b><br>Score: %{y:.1f}<br>Faixa: %{customdata[0]} – %{customdata[1]}"
                                  "<br>Cobertura: %{customdata[2]}%<br>%{customdata[3]}<extra></extra>")
    _base(fig, 320)
    fig.update_layout(showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_yaxes(range=[0, 100], title="Score (0–100)")
    return fig
