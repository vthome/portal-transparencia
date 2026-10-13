"""Relatório de conformidade de uma rodada (Markdown)."""
from __future__ import annotations

from pathlib import Path

from compliance.engine import AvaliacaoEnte

ROTULO = {"atende": "atende", "parcial": "parcial", "nao_atende": "não atende",
          "nao_verificavel": "não verificável", "nao_aplicavel": "não aplicável", "pendente": "pendente"}


def _fmt(x, suf=""):
    return "—" if x is None else f"{x:.1f}{suf}"


def renderizar(run_id: str, avals: list[AvaliacaoEnte], cfg: dict, resumo_emb: dict | None = None) -> str:
    dims = cfg["dimensoes"]
    limiar = float(cfg.get("revisao_abaixo_de", 0.6))
    ls = [f"# Relatório de conformidade — rodada {run_id}", ""]
    if resumo_emb:
        ls.append(f"Embeddings: modelo `{resumo_emb.get('modelo')}` · {resumo_emb.get('trechos', 0)} trechos novos "
                  f"({resumo_emb.get('calculados', 0)} calculados, {resumo_emb.get('reaproveitados', 0)} reaproveitados)")
        ls.append("")
    ls += ["## Ranking", "",
           "| Ente | Score | Faixa (mín–máx) | Cobertura | Essenciais | Classificação |",
           "| --- | ---: | --- | ---: | :---: | --- |"]
    for a in sorted(avals, key=lambda a: -(a.score.score or -1)):
        s = a.score
        ess = {True: "ok", False: "reprovado", None: "incompleto"}[s.essenciais_ok]
        ls.append(f"| {a.slug} | {_fmt(s.score)} | {_fmt(s.score_min)}–{_fmt(s.score_max)} | "
                  f"{_fmt(None if s.cobertura is None else 100 * s.cobertura, '%')} | {ess} | {s.classificacao} |")
    ls += ["", "## Score por dimensão", "", "| Ente | " + " | ".join(dims.values()) + " |",
           "| --- |" + " ---: |" * len(dims)]
    for a in avals:
        d = a.score.scores_bloco["dimensoes"]
        ls.append(f"| {a.slug} | " + " | ".join(_fmt(d.get(k)) for k in dims) + " |")
    ls += ["", "## Resultados por status", "",
           "| Ente | atende | parcial | não atende | não verificável | pendente | revisão humana |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for a in avals:
        c = a.score.scores_bloco["contagem"]
        rev = sum(1 for _, r in a.resultados if r.confianca is not None and r.confianca < limiar)
        ls.append(f"| {a.slug} | {c.get('atende', 0)} | {c.get('parcial', 0)} | {c.get('nao_atende', 0)} | "
                  f"{c.get('nao_verificavel', 0)} | {c.get('pendente', 0)} | {rev} |")
    ls += ["", "## Essenciais reprovados", ""]
    for a in avals:
        rep = a.score.scores_bloco["essenciais_reprovados"]
        ls.append(f"- **{a.slug}**: {', '.join(rep) if rep else 'nenhum'}")
    ls += ["", "## Amostra de evidências (primeiros 5 'atende' de maior confiança por ente)", ""]
    for a in avals:
        top = sorted([(c, r) for c, r in a.resultados if r.status == "atende" and r.confianca],
                     key=lambda x: -x[1].confianca)[:5]
        ls.append(f"**{a.slug}**")
        for c, r in top:
            url = r.evidencias[0].url if r.evidencias else ""
            ls.append(f"- {c.codigo} ({r.confianca:.2f}) {r.justificativa} {url}")
        ls.append("")
    ls.append("Notas: score 0–100; não verificável (falha de coleta) fica fora do score e define a faixa mín–máx; "
              "pendente = quesito ainda não automatizado. Classificação inspirada no PNTP — conferir faixas na cartilha.")
    return "\n".join(ls) + "\n"


def gerar(run_id: str, avals: list[AvaliacaoEnte], cfg: dict, data_dir: Path, resumo_emb: dict | None = None) -> Path:
    destino = Path(data_dir) / "runs" / f"{run_id}-conformidade.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(renderizar(run_id, avals, cfg, resumo_emb), encoding="utf-8")
    return destino
