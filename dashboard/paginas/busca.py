import streamlit as st
from painel import dados
from painel import formatos as fmt

st.title("Busca nos portais")
st.caption("Procura nos textos coletados na rodada mais recente de cada município (páginas e documentos). "
           "Útil para conferir onde uma informação aparece — ou confirmar que não aparece.")

entes = dados.carregar(dados.entes, msg="Conectando à API… (a primeira chamada pode levar até 1 minuto)")
nomes = {e["slug"]: fmt.nome_curto(e["nome"]) for e in entes}

with st.form("form_busca"):
    q = st.text_input("O que procurar", placeholder="ex.: lista de espera das creches, diárias de viagem, "
                      "contrato de coleta de lixo", max_chars=300, key="busca_q")
    c1, c2 = st.columns([2, 1])
    ente = c1.selectbox("Município", [""] + list(nomes), format_func=lambda s: nomes.get(s, "Todos"),
                        key="busca_ente")
    k = c2.slider("Resultados", 5, 30, 10, key="busca_k")
    st.form_submit_button("Buscar", type="primary", icon=":material/search:")

q = (q or "").strip()
if not q:
    st.stop()
if len(q) < 3:
    st.warning("Digite pelo menos 3 caracteres.")
    st.stop()

res = dados.carregar(dados.busca, q, ente or None, int(k), "auto", msg="Buscando…")
modo = "semântica (por significado)" if res["modo"] == "semantica" else "por palavras"
st.caption(f"{len(res['resultados'])} resultado(s) · busca {modo}"
           + (f" · modelo {res['modelo']}" if res.get("modelo") else ""))
if not res["resultados"]:
    st.info("Nada encontrado. Tente outras palavras ou um sinônimo.", icon=":material/search_off:")
for item in res["resultados"]:
    with st.container(border=True):
        titulo = item.get("titulo") or item.get("url") or "Trecho"
        st.markdown(f"**{titulo}** · {nomes.get(item['slug'], item['slug'])}")
        if item.get("url"):
            st.markdown(f"[{item['url']}]({item['url']})")
        texto = item["texto"]
        st.caption(texto[:600] + ("…" if len(texto) > 600 else ""))
        st.caption(f"Relevância {fmt.num(item['relevancia'], 3)} · "
                   f"{'documento' if item.get('file_artifact_id') else 'página'} da rodada {item['run_id']}")
