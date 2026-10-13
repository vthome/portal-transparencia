import streamlit as st
from painel import dados, ui
from painel import formatos as fmt
from painel import graficos as gr

st.title("Município")

entes = dados.carregar(dados.entes, msg="Conectando à API… (a primeira chamada pode levar até 1 minuto)")
if not entes:
    st.info("Nenhum município cadastrado na API.")
    st.stop()
slug = ui.escolher_ente(entes, "mun_ente")
d = dados.carregar(dados.ente, slug)
av, rev = d.get("avaliacao"), d.get("score_revisado")
rotulos = dados.carregar(dados.rotulos)

st.caption(f"{d['nome']} · {d['tipo_entidade']} · poder {d['poder']}"
           + (f" · avaliação da rodada {av['run_id']} em {fmt.data_br(av['avaliado_em'], hora=True)}" if av else ""))
if not av:
    st.info("Este município ainda não foi avaliado. Rode a conclusão (Camada 2) para a rodada de coleta mais recente.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Score", fmt.num(av["score"]), help=f"Faixa de incerteza: {fmt.faixa(av)}")
c1.caption(f"Faixa: {fmt.faixa(av)}")
if rev and d.get("revisoes"):
    delta = None if rev["score"] is None or av["score"] is None else round(rev["score"] - av["score"], 2)
    c2.metric("Score revisado", fmt.num(rev["score"]), delta=fmt.num(delta) if delta else None,
              help=f"Com {d['revisoes']} veredito(s) corrigido(s) por revisão humana")
else:
    c2.metric("Score revisado", "—", help="Ainda sem revisão humana")
c3.metric("Cobertura", fmt.num(av["cobertura"], sufixo="%"), help="% do peso dos quesitos que pôde ser verificado")
c4.metric("Classificação", av.get("classificacao") or "—")
st.markdown(f"**Essenciais:** {fmt.essenciais(av.get('essenciais_ok'))}"
            + (f" — reprovados: {', '.join(av['essenciais_reprovados'])}" if av.get("essenciais_reprovados") else ""))

with st.expander("Endereços monitorados", icon=":material/link:"):
    for u in d.get("urls") or []:
        st.markdown(f"- **{u['tipo']}**: [{u['url']}]({u['url']})"
                    + ("" if u.get("dominio_gov") in (None, True) else " ⚠️ fora de .gov.br"))
ui.explicacao_score()

aba_dim, aba_q, aba_det = st.tabs(["Dimensões", "Quesitos", "Detalhe do quesito"])

with aba_dim:
    col1, col2 = st.columns([3, 2])
    col1.plotly_chart(gr.dimensoes_ente(av["dimensoes"], rotulos))
    cont = av.get("contagem") or {}
    col2.markdown("**Quesitos por situação**")
    col2.dataframe(
        [{"Situação": fmt.rotulo_status(k), "Quesitos": cont.get(k, 0)} for k in fmt.ORDEM_STATUS if cont.get(k)],
        hide_index=True)

rs = dados.carregar(dados.resultados, slug)

with aba_q:
    f1, f2, f3, f4 = st.columns([2, 2, 1, 1])
    status_sel = f1.multiselect("Situação", fmt.ORDEM_STATUS, format_func=fmt.rotulo_status, key="mun_status")
    dims = [""] + list(rotulos)
    dim_sel = f2.selectbox("Dimensão", dims, format_func=lambda k: rotulos.get(k, "Todas"), key="mun_dim")
    so_ess = f3.toggle("Só essenciais", key="mun_ess")
    so_rev = f4.toggle("Só para revisar", key="mun_rev")
    filtrados = [r for r in rs
                 if (not status_sel or ("pendente" if r["metodo"] == "pendente" and not r["revisado"]
                                        else r["status_final"]) in status_sel)
                 and (not dim_sel or r["dimensao"] == dim_sel)
                 and (not so_ess or r["essencial"]) and (not so_rev or r["precisa_revisao"])]
    st.caption(f"{len(filtrados)} de {len(rs)} quesitos · ⚠️ = confiança baixa, aguardando revisão humana")
    st.dataframe(
        fmt.df_resultados(filtrados, rotulos).drop(columns=["id"]), hide_index=True,
        column_config={
            "Quesito": st.column_config.TextColumn("Quesito", width="large"),
            "Confiança": st.column_config.ProgressColumn("Confiança", min_value=0, max_value=1, format="%.2f"),
            "Motor": st.column_config.TextColumn("Motor", help="veredito original do motor, quando revisado"),
        })

with aba_det:
    opcoes = {r["id"]: f"{r['codigo']} — {r['texto'][:90]}" for r in rs}
    rid = st.selectbox("Quesito", list(opcoes), format_func=opcoes.get, key="mun_quesito")
    r = dados.carregar(dados.resultado, rid)
    a, b, c = st.columns(3)
    a.markdown(f"**Situação:** {fmt.rotulo_status(r['status_final'], r['metodo'])}")
    if r.get("status_revisado"):
        a.caption(f"Motor: {fmt.rotulo_status(r['status'], r['metodo'])} · corrigido por revisão humana")
    b.markdown(f"**Confiança:** {fmt.num(r.get('confianca'), 2)} · **Método:** {fmt.METODO.get(r['metodo'], r['metodo'])}")
    c.markdown(f"**Peso:** {r['peso']}{' · essencial' if r['essencial'] else ''} · "
               f"**Dimensão:** {rotulos.get(r['dimensao'], r['dimensao'])}")
    st.markdown(f"**{r['codigo']}** — {r['texto']}")
    if r.get("fundamentacao"):
        st.caption(f"Fundamentação: {r['fundamentacao']}")
    st.info(r.get("justificativa") or "Sem justificativa registrada.", icon=":material/gavel:")
    st.markdown("**Evidências**")
    ui.evidencias(r.get("evidencias") or [])
    if r.get("revisoes"):
        st.markdown("**Revisões humanas**")
        st.dataframe([{"Quando": fmt.data_br(x["revisado_em"], hora=True), "Revisor": x["revisor"],
                       "Veredito": fmt.rotulo_status(x["status_corrigido"]), "Comentário": x.get("comentario") or ""}
                      for x in r["revisoes"]], hide_index=True)
    with st.expander("Sinais usados no cálculo da confiança", icon=":material/tune:"):
        st.json(r.get("sinais") or {})
    if st.button("Revisar este quesito", icon=":material/rate_review:"):
        st.session_state["revisar_id"] = rid
        st.switch_page("paginas/revisao.py")
