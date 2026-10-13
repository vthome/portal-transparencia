import streamlit as st
from painel import dados, ui
from painel import formatos as fmt
from painel import graficos as gr

st.title("Conformidade dos portais da transparência")
st.caption("Sete municípios do RJ avaliados contra o checklist de transparência (142 quesitos), "
           "com evidência auditável por quesito. Dados da coleta automática mais recente de cada município.")

rk = dados.carregar(dados.ranking, None, msg="Conectando à API… (a primeira chamada pode levar até 1 minuto)")
if not rk:
    st.info("Ainda não há avaliações. Rode a coleta e a conclusão (Camadas 1 e 2) para popular o painel.",
            icon=":material/hourglass_empty:")
    st.stop()
rotulos = dados.carregar(dados.rotulos)
fila = dados.carregar(dados.fila, None, True, 1000)

completas = [x for x in rk if x.get("no_ranking")]
scores = [x["score"] for x in rk if x.get("score") is not None]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Municípios avaliados", len(rk), help=f"{len(completas)} com cobertura suficiente para o ranking")
c2.metric("Score médio", fmt.num(sum(scores) / len(scores)) if scores else "—", help="média simples dos scores")
if completas:
    lider = completas[0]
    c3.metric("Maior score", fmt.num(lider["score"]), help=fmt.nome_curto(lider["nome"]))
    c3.caption(fmt.nome_curto(lider["nome"]))
else:
    c3.metric("Maior score", "—")
c4.metric("Quesitos aguardando revisão", len(fila), help="confiança abaixo de 0,6 ou não verificáveis")

st.subheader("Ranking")
opcoes = {"": "Score geral", **rotulos}
dim = st.selectbox("Classificar por", list(opcoes), format_func=lambda k: opcoes[k], key="vg_dim")
itens = dados.carregar(dados.ranking, dim or None) if dim else rk
st.plotly_chart(gr.ranking(itens, campo="valor", faixa=not dim,
                           titulo_x=f"{opcoes[dim]} (0–100)"))
st.caption("Barras mais claras e “(incompleta)”: cobertura abaixo de 80%, fora do ranking. "
           + ("" if dim else "A linha fina em cada barra é a faixa de incerteza (mín–máx)."))

tabela = fmt.df_ranking(itens)
tabela["Detalhe"] = "municipio?ente=" + tabela["slug"]
st.dataframe(
    tabela.drop(columns=["slug"]), hide_index=True,
    column_config={
        "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
        "Cobertura (%)": st.column_config.NumberColumn("Cobertura (%)", format="%.1f"),
        "Detalhe": st.column_config.LinkColumn("Detalhe", display_text="Abrir"),
    })
ui.explicacao_score()

st.subheader("Score por dimensão")
st.caption("Cada dimensão vale o mesmo no score geral. Células mais escuras, maior conformidade.")
matriz = dados.carregar(dados.matriz_dimensoes)
if matriz["linhas"]:
    st.plotly_chart(gr.heatmap_dimensoes(matriz, rotulos))

st.subheader("Quesitos por situação")
st.caption("Não verificável = o robô não conseguiu ler a informação (não conta como descumprimento). "
           "Pendente = quesito ainda sem verificação automática.")
st.plotly_chart(gr.status_empilhado(dados.carregar(dados.status_por_ente)))
