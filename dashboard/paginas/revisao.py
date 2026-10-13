import hmac

import streamlit as st
from painel import dados, ui
from painel import formatos as fmt
from painel.cliente import ApiErro

st.title("Revisão humana")
st.caption("Quesitos em que o motor tem pouca confiança (abaixo de 0,6) ou que o robô não conseguiu verificar. "
           "O veredito do revisor não apaga o do motor: ele passa a valer no **score revisado** e alimenta a "
           "recalibração da confiança.")

cfg = dados.config()

# ------------------------------------------------------------------ quem pode revisar
pode_revisar = cfg.revisao_habilitada
if not cfg.revisao_habilitada:
    st.warning("Modo somente leitura: este painel não tem `API_TOKEN_REVISAO` configurado nos secrets.",
               icon=":material/lock:")
elif cfg.revisao_senha and not st.session_state.get("revisor_autorizado"):
    pode_revisar = False
    with st.form("login_revisor"):
        st.markdown("**Entrar como revisor**")
        senha = st.text_input("Senha de revisão", type="password")
        if st.form_submit_button("Entrar", icon=":material/login:"):
            if hmac.compare_digest(senha.encode(), cfg.revisao_senha.encode()):
                st.session_state["revisor_autorizado"] = True
                st.rerun()
            st.error("Senha incorreta.")

if msg := st.session_state.pop("msg_revisao", None):
    st.success(msg, icon=":material/check_circle:")

# ------------------------------------------------------------------ fila
entes = dados.carregar(dados.entes, msg="Conectando à API… (a primeira chamada pode levar até 1 minuto)")
rotulos = dados.carregar(dados.rotulos)
nomes = {e["slug"]: fmt.nome_curto(e["nome"]) for e in entes}
c1, c2, c3 = st.columns([2, 1, 1])
ente = c1.selectbox("Município", [""] + list(nomes), format_func=lambda s: nomes.get(s, "Todos"), key="rev_ente")
incluir_nv = c2.toggle("Incluir não verificáveis", value=True, key="rev_nv")
limite = c3.number_input("Máximo de itens", 10, 1000, 200, step=10, key="rev_lim")
fila = dados.carregar(dados.fila, ente or None, incluir_nv, int(limite))

st.subheader(f"Fila: {len(fila)} quesito(s)")
st.caption("Ordem: essenciais primeiro, depois maior peso e menor confiança.")
if fila:
    st.dataframe(fmt.df_fila(fila, rotulos).drop(columns=["id"]), hide_index=True,
                 column_config={"Quesito": st.column_config.TextColumn(width="large"),
                                "Confiança": st.column_config.ProgressColumn(min_value=0, max_value=1,
                                                                             format="%.2f")})
else:
    st.success("Nada pendente de revisão com esses filtros.", icon=":material/task_alt:")

# ------------------------------------------------------------------ revisar um item
pedido = st.session_state.pop("revisar_id", None)          # vindo do botão "Revisar este quesito"
opcoes = {x["id"]: f"{nomes.get(x['slug'], x['slug'])} · {x['codigo']} — {x['texto'][:80]}" for x in fila}
if pedido and pedido not in opcoes:
    extra = dados.carregar(dados.resultado, pedido)
    opcoes = {pedido: f"{nomes.get(extra['slug'], extra['slug'])} · {extra['codigo']} — {extra['texto'][:80]}",
              **opcoes}
if pedido:
    st.session_state["rev_item"] = pedido
elif st.session_state.get("rev_item") not in opcoes:      # item revisado saiu da fila
    st.session_state.pop("rev_item", None)

if opcoes:
    st.subheader("Revisar")
    rid = st.selectbox("Quesito", list(opcoes), format_func=opcoes.get, key="rev_item")
    r = dados.carregar(dados.resultado, rid)
    st.markdown(f"**{nomes.get(r['slug'], r['slug'])} · {r['codigo']}** — {r['texto']}")
    a, b = st.columns(2)
    a.markdown(f"**Veredito do motor:** {fmt.rotulo_status(r['status'], r['metodo'])} · "
               f"confiança {fmt.num(r.get('confianca'), 2)}")
    if r.get("status_revisado"):
        b.markdown(f"**Já revisado:** {fmt.rotulo_status(r['status_revisado'])}")
    st.info(r.get("justificativa") or "Sem justificativa registrada.", icon=":material/gavel:")
    if r.get("fundamentacao"):
        st.caption(f"Fundamentação: {r['fundamentacao']}")
    with st.expander(f"Evidências ({len(r.get('evidencias') or [])})", expanded=True, icon=":material/attach_file:"):
        ui.evidencias(r.get("evidencias") or [])

    if pode_revisar:
        with st.form("form_revisao", clear_on_submit=False):
            padrao = r["status"] if r["status"] in fmt.STATUS_REVISAVEIS else "atende"
            novo = st.radio("Veredito correto", fmt.STATUS_REVISAVEIS, index=fmt.STATUS_REVISAVEIS.index(padrao),
                            format_func=fmt.rotulo_status, horizontal=True)
            revisor = st.text_input("Revisor", value=st.session_state.get("revisor_nome", ""),
                                    placeholder="Nome (e órgão)", max_chars=120)
            comentario = st.text_area("Comentário", placeholder="Onde a informação foi encontrada (ou por que não "
                                      "atende). Ajuda a auditar e a melhorar o robô.", max_chars=2000)
            if st.form_submit_button("Gravar revisão", type="primary", icon=":material/save:"):
                if len(revisor.strip()) < 2:
                    st.error("Informe o nome do revisor.")
                else:
                    try:
                        out = dados.api().revisar(rid, novo, revisor.strip(), comentario.strip() or None)
                    except ApiErro as e:
                        st.error(f"A API recusou a revisão ({e.status}): {e.detalhe}")
                    else:
                        st.session_state["revisor_nome"] = revisor.strip()
                        sr = out["score_revisado"]
                        concordou = "concordando com" if out["concordou_com_motor"] else "corrigindo"
                        st.session_state["msg_revisao"] = (
                            f"Revisão gravada ({concordou} o motor). Score revisado de "
                            f"{nomes.get(r['slug'], r['slug'])}: {fmt.num(sr['score'])} · {sr['classificacao']}.")
                        dados.limpar_cache()
                        st.rerun()

# ------------------------------------------------------------------ concordância
st.subheader("Concordância entre revisores e motor")
conc = dados.carregar(dados.concordancia)
m1, m2, m3 = st.columns(3)
m1.metric("Revisões", conc["revisoes"])
m2.metric("Concordância", fmt.num(100 * conc["concordancia"], sufixo="%") if conc["concordancia"] is not None else "—")
m3.metric("Pronto para recalibrar", "Sim" if conc["pronto_para_calibrar"] else "Ainda não",
          help="A partir de 30 revisões: rode `python -m compliance.calibrar` para reajustar a confiança")
if conc["revisoes"]:
    t1, t2 = st.columns(2)
    t1.markdown("**Por verificador**")
    t1.dataframe([{"Verificador": k, "Revisões": v["n"], "Concordância (%)": round(100 * v["concordancia"], 1)}
                  for k, v in conc["por_verificador"].items()], hide_index=True)
    t2.markdown("**Por faixa de confiança do motor**")
    t2.dataframe([{"Confiança": k, "Revisões": v["n"], "Concordância (%)": round(100 * v["concordancia"], 1)}
                  for k, v in conc["por_faixa_de_confianca"].items()], hide_index=True)
    with st.expander("Revisões recentes", icon=":material/history:"):
        st.dataframe([{"Quando": fmt.data_br(x["revisado_em"], hora=True), "Município": x["slug"],
                       "Código": x["codigo"], "Motor": fmt.rotulo_status(x["status_motor"]),
                       "Revisor": fmt.rotulo_status(x["status_corrigido"]), "Por": x["revisor"],
                       "Comentário": x.get("comentario") or ""} for x in dados.carregar(dados.revisoes)],
                     hide_index=True)
