import streamlit as st
from painel import dados, ui
from painel import formatos as fmt
from painel import graficos as gr

st.title("Evolução")
st.caption("Como o score de cada município mudou entre as rodadas de coleta e quais quesitos mudaram de situação.")

entes = dados.carregar(dados.entes, msg="Conectando à API… (a primeira chamada pode levar até 1 minuto)")
if not entes:
    st.info("Nenhum município cadastrado na API.")
    st.stop()
slug = ui.escolher_ente(entes, "evo_ente")
hist = dados.carregar(dados.historico, slug)
if not hist:
    st.info("Este município ainda não foi avaliado.")
    st.stop()
rotulos = dados.carregar(dados.rotulos)

st.plotly_chart(gr.evolucao(hist))
st.dataframe(fmt.df_historico(hist).drop(columns=["run_id"]), hide_index=True,
             column_config={"Score": st.column_config.NumberColumn(format="%.1f"),
                            "Mínimo": st.column_config.NumberColumn(format="%.1f"),
                            "Máximo": st.column_config.NumberColumn(format="%.1f"),
                            "Cobertura (%)": st.column_config.NumberColumn(format="%.1f")})

st.subheader("O que mudou entre duas rodadas")
if len(hist) < 2:
    st.info("Só há uma avaliação deste município. A comparação aparece a partir da segunda rodada.",
            icon=":material/info:")
else:
    runs = [h["run_id"] for h in hist]
    nomes = {h["run_id"]: fmt.rotulo_rodada(h) for h in hist}
    c1, c2 = st.columns(2)
    de = c1.selectbox("De", runs, index=len(runs) - 2, format_func=nomes.get, key="evo_de")
    para = c2.selectbox("Para", runs, index=len(runs) - 1, format_func=nomes.get, key="evo_para")
    if de == para:
        st.warning("Escolha duas rodadas diferentes.")
    else:
        d = dados.carregar(dados.diff, slug, de, para)
        res = d["resumo"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Variação do score", fmt.num(d["para"]["score"]),
                  delta=fmt.num(d["delta_score"]) if d.get("delta_score") is not None else None)
        m2.metric("Melhoraram", res.get("melhorou", 0))
        m3.metric("Pioraram", res.get("piorou", 0))
        m4.metric("Mudaram de verificabilidade", res.get("mudou_verificabilidade", 0),
                  help="Passaram a ser (ou deixaram de ser) verificáveis pelo robô")
        if d["mudancas"]:
            st.dataframe(fmt.df_mudancas(d["mudancas"], rotulos), hide_index=True,
                         column_config={"Quesito": st.column_config.TextColumn(width="large")})
        else:
            st.success("Nenhum quesito mudou de situação entre essas rodadas.")

st.subheader("Histórico de um quesito")
rs = dados.carregar(dados.resultados, slug)
opcoes = {r["codigo"]: f"{r['codigo']} — {r['texto'][:90]}" for r in rs}
cod = st.selectbox("Quesito", list(opcoes), format_func=opcoes.get, key="evo_quesito")
tl = dados.carregar(dados.timeline, slug, cod)
st.dataframe([{"Rodada": f"Rodada {x['run_id']} · {fmt.data_br(x['avaliado_em'])}",
               "Situação": fmt.rotulo_status(x["status_final"], x["metodo"]),
               "Confiança": x.get("confianca"), "Mudança": fmt.EVENTO.get(x["evento"], x["evento"]),
               "Justificativa": x.get("justificativa") or ""} for x in tl], hide_index=True,
             column_config={"Confiança": st.column_config.NumberColumn(format="%.2f")})
