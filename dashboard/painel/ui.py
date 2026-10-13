"""Peças de interface reaproveitadas pelas páginas."""
from __future__ import annotations

import streamlit as st

from painel import dados
from painel import formatos as fmt


def escolher_ente(entes: list[dict], chave: str, rotulo: str = "Município") -> str:
    """Seletor de município que lembra a escolha entre páginas e aceita ?ente=<slug> na URL."""
    slugs = [e["slug"] for e in entes]
    nomes = {e["slug"]: fmt.nome_curto(e["nome"]) for e in entes}
    pedido = st.query_params.get("ente") or st.session_state.get("ente_atual")
    padrao = slugs.index(pedido) if pedido in slugs else 0
    slug = st.selectbox(rotulo, slugs, index=padrao, format_func=lambda s: nomes.get(s, s), key=chave)
    if slug != st.session_state.get("ente_atual"):
        st.session_state["ente_atual"] = slug
    if st.query_params.get("ente") != slug:
        st.query_params["ente"] = slug
    return slug


def evidencias(evs: list[dict]) -> None:
    """Lista de evidências com link original, trecho e botão para a cópia guardada (link assinado)."""
    if not evs:
        st.caption("Nenhuma evidência registrada para este quesito.")
        return
    for ev in evs:
        with st.container(border=True):
            tipo = ev.get("tipo") or "evidência"
            titulo = ev.get("pagina_titulo") or ev.get("url") or "—"
            st.markdown(f"**{tipo.capitalize()}** · {titulo}")
            if ev.get("url"):
                st.markdown(f"[{ev['url']}]({ev['url']})")
            if ev.get("trecho"):
                st.caption(f"“{ev['trecho'][:500]}”")
            meta = []
            if ev.get("pagina_coletada_em"):
                meta.append(f"coletada em {fmt.data_br(ev['pagina_coletada_em'], hora=True)}")
            if ev.get("arquivo_extensao"):
                meta.append(f"arquivo .{ev['arquivo_extensao']}")
            if meta:
                st.caption(" · ".join(meta))
            if ev.get("tem_copia") and st.button("Abrir cópia guardada", key=f"copia_{ev['id']}",
                                                 icon=":material/inventory_2:"):
                link = dados.link_evidencia(ev["id"])
                if link is None:
                    st.warning("Não foi possível gerar o link da cópia (Storage não configurado na API?).")
                elif link["tipo"] == "screenshot":
                    st.image(link["url"], caption="Captura de tela no momento da coleta")
                else:
                    st.link_button(f"Abrir {link['tipo']} (link válido por {link['expira_em_s'] // 60} min)",
                                   link["url"], icon=":material/open_in_new:")


def explicacao_score() -> None:
    with st.expander("Como ler o score", icon=":material/help:"):
        st.markdown(
            "- **Score (0–100):** média ponderada dos quesitos verificados, primeiro dentro de cada uma das 5 "
            "dimensões e depois entre as dimensões (todas valem o mesmo).\n"
            "- **Faixa (mín–máx):** quesitos que o robô **não conseguiu verificar** (página bloqueada, fora do ar, "
            "dado só após consulta) não contam como descumprimento. O mínimo supõe que todos falhariam; o máximo, "
            "que todos atenderiam.\n"
            "- **Cobertura:** % do peso dos quesitos que pôde ser verificado. Abaixo de 80%, a avaliação é "
            "*incompleta* e fica fora do ranking.\n"
            "- **Essenciais:** quesitos da LRF e do núcleo do checklist; classificações Diamante/Ouro/Prata exigem "
            "todos atendidos.\n"
            "- **Score revisado:** o mesmo cálculo, usando o veredito do revisor humano onde houver revisão.")
