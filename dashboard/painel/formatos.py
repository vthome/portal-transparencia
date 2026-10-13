"""Rótulos, cores e tabelas (pandas) — funções puras, sem Streamlit."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

# Status do quesito: rótulo + símbolo sempre juntos (a cor nunca carrega o significado sozinha)
STATUS: dict[str, dict[str, str]] = {
    "atende":          {"rotulo": "Atende",           "simbolo": "✅", "cor": "#0ca30c"},
    "parcial":         {"rotulo": "Parcial",          "simbolo": "🟡", "cor": "#fab219"},
    "nao_atende":      {"rotulo": "Não atende",       "simbolo": "❌", "cor": "#d03b3b"},
    "nao_verificavel": {"rotulo": "Não verificável",  "simbolo": "❔", "cor": "#8a8984"},
    "pendente":        {"rotulo": "Pendente",         "simbolo": "⏳", "cor": "#c3c2b7"},
    "nao_aplicavel":   {"rotulo": "Não se aplica",    "simbolo": "➖", "cor": "#e0dfd9"},
}
ORDEM_STATUS = ["atende", "parcial", "nao_atende", "nao_verificavel", "pendente", "nao_aplicavel"]
STATUS_REVISAVEIS = ["atende", "parcial", "nao_atende", "nao_verificavel", "nao_aplicavel"]

METODO = {"regra": "Regra", "embedding": "Semântico", "llm": "LLM", "humano": "Humano", "pendente": "Não automatizado"}

EVENTO = {"melhorou": "⬆️ Melhorou", "piorou": "⬇️ Piorou", "mudou_verificabilidade": "↔️ Mudou verificabilidade",
          "novo": "🆕 Novo", "removido": "🗑️ Removido", "estavel": "= Estável", "primeira": "• Primeira avaliação"}


def rotulo_status(status: str | None, metodo: str | None = None) -> str:
    chave = "pendente" if metodo == "pendente" else status
    s = STATUS.get(chave or "")
    return f"{s['simbolo']} {s['rotulo']}" if s else (status or "—")


def num(x: Any, casas: int = 1, sufixo: str = "") -> str:
    """Número no formato brasileiro (vírgula decimal); '—' para vazio."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    s = f"{float(x):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s + sufixo


def data_br(iso: str | None, hora: bool = False) -> str:
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    return d.strftime("%d/%m/%Y %H:%M" if hora else "%d/%m/%Y")


def rotulo_rodada(h: dict) -> str:
    quando = h.get("avaliado_em") or h.get("rodada_em") or h.get("iniciado_em")
    return f"Rodada {h['run_id']} · {data_br(quando)}"


def faixa(av: dict | None) -> str:
    if not av or av.get("score_min") is None:
        return "—"
    return f"{num(av['score_min'])} – {num(av['score_max'])}"


def essenciais(ok: bool | None) -> str:
    return {True: "✅ Todos atendidos", False: "❌ Há reprovados", None: "❔ Não verificados"}[ok]


def nome_curto(nome: str) -> str:
    for p in ("Prefeitura Municipal de ", "Prefeitura Municipal do ", "Prefeitura de ", "Prefeitura do "):
        if nome.startswith(p):
            return nome[len(p):]
    return nome


# ------------------------------------------------------------------ tabelas
def df_ranking(itens: list[dict]) -> pd.DataFrame:
    linhas = [{"Posição": x.get("posicao"), "Município": nome_curto(x["nome"]), "Score": x.get("score"),
               "Faixa (mín–máx)": faixa(x), "Cobertura (%)": x.get("cobertura"),
               "Classificação": x.get("classificacao") or "—", "Essenciais": essenciais(x.get("essenciais_ok")),
               "slug": x["slug"]} for x in itens]
    return pd.DataFrame(linhas, columns=["Posição", "Município", "Score", "Faixa (mín–máx)", "Cobertura (%)",
                                         "Classificação", "Essenciais", "slug"])


def df_resultados(rs: list[dict], rotulos: dict[str, str]) -> pd.DataFrame:
    linhas = []
    for r in rs:
        linhas.append({
            "Código": r["codigo"], "Quesito": r["texto"], "Dimensão": rotulos.get(r["dimensao"], r["dimensao"]),
            "Peso": r["peso"], "Essencial": "Sim" if r["essencial"] else "",
            "Status": rotulo_status(r.get("status_final") or r["status"], r.get("metodo")),
            "Motor": rotulo_status(r["status"], r.get("metodo")) if r.get("revisado") else "",
            "Confiança": r.get("confianca"), "Método": METODO.get(r.get("metodo"), r.get("metodo")),
            "Evidências": r.get("n_evidencias", 0), "Revisar": "⚠️" if r.get("precisa_revisao") else "",
            "id": r["id"]})
    return pd.DataFrame(linhas, columns=["Código", "Quesito", "Dimensão", "Peso", "Essencial", "Status", "Motor",
                                         "Confiança", "Método", "Evidências", "Revisar", "id"])


def contagem_status(rs: list[dict]) -> dict[str, int]:
    """Quantos quesitos em cada status final (pendente separado)."""
    out = {k: 0 for k in ORDEM_STATUS}
    for r in rs:
        k = "pendente" if r.get("metodo") == "pendente" and not r.get("revisado") else (r.get("status_final") or r["status"])
        out[k] = out.get(k, 0) + 1
    return out


def df_historico(hist: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"Rodada": rotulo_rodada(h), "run_id": h["run_id"], "Score": h.get("score"),
                          "Mínimo": h.get("score_min"), "Máximo": h.get("score_max"),
                          "Cobertura (%)": h.get("cobertura"), "Classificação": h.get("classificacao")}
                         for h in hist])


def df_mudancas(mudancas: list[dict], rotulos: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame([{"Código": m["codigo"], "Quesito": m["texto"],
                          "Dimensão": rotulos.get(m["dimensao"], m["dimensao"]),
                          "Essencial": "Sim" if m.get("essencial") else "",
                          "Antes": rotulo_status(m.get("status_de")) if m.get("status_de") else "—",
                          "Depois": rotulo_status(m.get("status_para")) if m.get("status_para") else "—",
                          "Mudança": EVENTO.get(m["evento"], m["evento"]),
                          "Justificativa atual": m.get("justificativa_para") or ""} for m in mudancas],
                        columns=["Código", "Quesito", "Dimensão", "Essencial", "Antes", "Depois", "Mudança",
                                 "Justificativa atual"])


def df_fila(itens: list[dict], rotulos: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame([{"Município": x["slug"], "Código": x["codigo"], "Quesito": x["texto"],
                          "Dimensão": rotulos.get(x["dimensao"], x["dimensao"]),
                          "Essencial": "Sim" if x.get("essencial") else "", "Peso": x.get("peso"),
                          "Veredito do motor": rotulo_status(x["status"]), "Confiança": x.get("confianca"),
                          "id": x["id"]} for x in itens],
                        columns=["Município", "Código", "Quesito", "Dimensão", "Essencial", "Peso",
                                 "Veredito do motor", "Confiança", "id"])


def df_jobs(jobs: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"Município": j["slug"], "Status": j.get("status"), "Páginas": j.get("paginas"),
                          "Arquivos": j.get("arquivos"), "Bloqueadas (robots)": j.get("bloqueios_robots"),
                          "Plataforma": j.get("plataforma_detectada") or "—",
                          "Seções ausentes": ", ".join(j.get("secoes_ausentes") or []) or "—",
                          "Duração (s)": j.get("segundos"), "Erro": j.get("erro") or ""} for j in jobs])
