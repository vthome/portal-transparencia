"""Relatório de uma rodada de coleta + checagem dos critérios de aceite da Camada 1.

    python -m ingestion.report                # última rodada (Supabase, ou local se não houver DATABASE_URL)
    python -m ingestion.report --run 12
    python -m ingestion.report --local

Gera data/runs/<run_id>-relatorio.md e imprime o mesmo conteúdo. O pipeline chama
`gerar_relatorio` automaticamente ao final de cada rodada.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from ingestion.load.load_postgres import Repository
from ingestion.settings import get_settings
from ingestion.transform.validate import load_secoes

MIN_SECOES = 8          # critério 3 do roteiro de testes (docs/01-coleta.md, 3.2)


def coletar(repo: Repository, run_id: str) -> dict[str, Any]:
    jobs = repo.run_jobs(run_id)
    stats: dict[str, dict[str, int]] = defaultdict(dict)
    render: dict[str, int] = defaultdict(int)
    for r in repo.run_snapshot_stats(run_id):
        stats[r["slug"]][r["coleta_status"]] = r["n"]
        render[r["slug"]] += r["renderizados"]
    erros: dict[str, list[str]] = defaultdict(list)
    for r in repo.run_top_erros(run_id):
        erros[r["slug"]].append(f"{r['erro']} ({r['n']})")
    return {"jobs": jobs, "stats": stats, "render": render, "erros": erros}


def criterios(dados: dict[str, Any], esperados: list[str]) -> list[tuple[str, bool, str]]:
    jobs = {j["slug"]: j for j in dados["jobs"]}
    out = []
    faltando = sorted(set(esperados) - set(jobs))
    out.append(("1. todo ente tem job na rodada", not faltando, f"sem job: {faltando}" if faltando else
                f"{len(jobs)}/{len(esperados)}"))
    excecoes = [s for s, j in jobs.items() if j.get("erro") and "Traceback" in (j.get("erro") or "")]
    out.append(("2. nenhum job terminou por exceção", not excecoes, ", ".join(excecoes) or "ok"))
    fracos = [f"{s} ({len(j.get('secoes_encontradas') or [])})" for s, j in jobs.items()
              if not j.get("bloqueios_robots") and j.get("status") != "falhou"
              and len(j.get("secoes_encontradas") or []) < MIN_SECOES]
    out.append((f"3. entes sem bloqueio cobrem >= {MIN_SECOES} seções", not fracos, ", ".join(fracos) or "ok"))
    inalt = [(s, (j.get("paginas_inalteradas") or 0) / j["paginas"]) for s, j in jobs.items() if j.get("paginas")]
    media = sum(v for _, v in inalt) / len(inalt) if inalt else 0
    out.append(("4. reaproveitamento (só a partir da 2ª rodada, meta >= 0,7)", True, f"média {media:.2f}"))
    return out


def renderizar(run_id: str, dados: dict[str, Any], esperados: list[str]) -> str:
    total_secoes = len(load_secoes()["secoes"])
    jobs = dados["jobs"]
    linhas = [f"# Relatório da coleta — rodada {run_id}", ""]
    tot = {k: sum((j.get(k) or 0) for j in jobs) for k in ("paginas", "arquivos", "requisicoes", "renderizacoes")}
    linhas.append(f"{len(jobs)} entes · {tot['paginas']} páginas · {tot['arquivos']} arquivos · "
                  f"{tot['requisicoes']} requisições · {tot['renderizacoes']} renderizações")
    linhas += ["", "## Por ente", "",
               "| Ente | Status | Plataforma | Páginas | ok / vazio / soft404 / erro / robots | Arquivos | "
               "Render | Seções | Tempo (s) |",
               "| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: |"]
    for j in jobs:
        st = dados["stats"].get(j["slug"], {})
        dist = " / ".join(str(st.get(k, 0)) for k in ("ok", "vazio", "soft404", "erro", "bloqueado_robots"))
        linhas.append(
            f"| {j['slug']} | {j.get('status')} | {j.get('plataforma_detectada') or '—'} | {j.get('paginas') or 0} | "
            f"{dist} | {j.get('arquivos') or 0} | {dados['render'].get(j['slug'], 0)} | "
            f"{len(j.get('secoes_encontradas') or [])}/{total_secoes} | {j.get('duracao_s') or 0:.0f} |")

    linhas += ["", "## Seções não encontradas", ""]
    for j in jobs:
        aus = j.get("secoes_ausentes") or []
        linhas.append(f"- **{j['slug']}**: {', '.join(aus) if aus else 'nenhuma'}")

    linhas += ["", "## Erros de coleta mais frequentes", ""]
    algum = False
    for j in jobs:
        e = dados["erros"].get(j["slug"])
        if e or j.get("erro"):
            algum = True
            detalhe = "; ".join(e or [])
            if j.get("erro"):
                detalhe = (detalhe + "; " if detalhe else "") + "job: " + j["erro"].splitlines()[0][:160]
            linhas.append(f"- **{j['slug']}**: {detalhe}")
    if not algum:
        linhas.append("- nenhum")

    linhas += ["", "## Critérios de aceite", ""]
    for nome, ok, det in criterios(dados, esperados):
        linhas.append(f"- [{'x' if ok else ' '}] {nome} — {det}")
    linhas += ["", "Lembrete: `vazio`, `erro` e `bloqueado_robots` viram `nao_verificavel` na Camada 2; "
               "não são não conformidade do ente.", ""]
    return "\n".join(linhas)


def gerar_relatorio(repo: Repository, run_id: str, data_dir: Path, esperados: list[str] | None = None) -> Path:
    esperados = esperados if esperados is not None else [j["slug"] for j in repo.run_jobs(run_id)]
    texto = renderizar(run_id, coletar(repo, run_id), esperados)
    destino = Path(data_dir) / "runs" / f"{run_id}-relatorio.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    return destino


def main(argv: list[str] | None = None) -> int:
    from ingestion.pipeline import build_backends

    ap = argparse.ArgumentParser(description="Relatório de uma rodada de coleta")
    ap.add_argument("--run", help="id da rodada (padrão: a mais recente)")
    ap.add_argument("--local", action="store_true")
    a = ap.parse_args(argv)
    s = get_settings()
    repo, _ = build_backends(s, a.local, storage=False)
    run_id = a.run or repo.latest_run_id()
    if not run_id:
        print("nenhuma rodada encontrada", file=sys.stderr)
        return 1
    caminho = gerar_relatorio(repo, str(run_id), s.data_dir)
    print(caminho.read_text(encoding="utf-8"))
    print(f"\n(salvo em {caminho})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
