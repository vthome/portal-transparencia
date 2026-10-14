import streamlit as st
from painel import dados
from painel import formatos as fmt

st.title("Monitoramento")
st.caption("Saúde da API e das rodadas de coleta. Falha de coleta não vira “não atende”: o quesito fica "
           "não verificável e alarga a faixa de incerteza do score.")

h = dados.carregar(dados.health, msg="Conectando à API… (a primeira chamada pode levar até 1 minuto)")
c1, c2, c3, c4 = st.columns(4)
c1.metric("API", "✅ No ar" if h.get("status") == "ok" else "⚠️ Degradada", help=f"versão {h.get('versao')}")
c2.metric("Banco", "✅ OK" if h.get("banco") == "ok" else f"⚠️ {h.get('banco')}")
c3.metric("Última rodada", h.get("ultima_rodada") or "—")
c4.metric("Busca", {"semantica": "Semântica", "semantica_remota": "Semântica (serviço externo)"}
          .get(h.get("busca"), "Por palavras"))
api = dados.api()
st.link_button("Documentação da API (Swagger)", f"{api.base_url}/docs", icon=":material/api:")

st.subheader("Rodadas de coleta")
rodadas = dados.carregar(dados.rodadas, 20)
if not rodadas:
    st.info("Nenhuma rodada registrada.")
    st.stop()
st.dataframe([{"Rodada": r["run_id"], "Início": fmt.data_br(r.get("iniciado_em"), hora=True),
               "Fim": fmt.data_br(r.get("finalizado_em"), hora=True), "Origem": r.get("gatilho"),
               "Status": r.get("status"), "Municípios": r.get("entes"), "Concluídos": r.get("concluidos"),
               "Parciais": r.get("parciais"), "Falhas": r.get("falhas"), "Páginas": r.get("paginas"),
               "Arquivos": r.get("arquivos"), "Avaliados": r.get("avaliacoes")} for r in rodadas], hide_index=True)

ids = [r["run_id"] for r in rodadas]
run = st.selectbox("Detalhar rodada", ids, key="mon_run", format_func=lambda i: f"Rodada {i}")
jobs = dados.carregar(dados.rodada, run)
st.dataframe(fmt.df_jobs(jobs), hide_index=True,
             column_config={"Seções ausentes": st.column_config.TextColumn(width="large"),
                            "Duração (s)": st.column_config.NumberColumn(format="%.0f")})
st.caption("Seções ausentes: áreas do portal que o robô procurou e não encontrou nessa coleta "
           "(ou que estavam bloqueadas). Vale conferir manualmente.")

with st.expander("Checklist em vigor", icon=":material/checklist:"):
    ck = dados.carregar(dados.checklist)
    rotulos = dados.carregar(dados.rotulos)
    st.dataframe([{"Código": c["codigo"], "Quesito": c["texto"], "Dimensão": rotulos.get(c["dimensao"], c["dimensao"]),
                   "Peso": c["peso"], "Essencial": "Sim" if c["essencial"] else "", "Matriz": c["matriz"],
                   "Verificador": c["verificador"], "Fundamentação": c.get("fundamentacao") or ""} for c in ck],
                 hide_index=True, column_config={"Quesito": st.column_config.TextColumn(width="large")})
    st.caption(f"{len(ck)} quesitos · versão {ck[0]['versao'] if ck else '—'}")
