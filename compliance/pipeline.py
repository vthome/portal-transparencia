"""Camada 2 — conclusão: embeddings → conformidade → score, gravados no Supabase.

    python -m compliance.pipeline                     # última rodada de coleta, todos os entes
    python -m compliance.pipeline --run 4 --entes rj-marica
    python -m compliance.pipeline --sem-embeddings    # só regras (semântico cai para termos)
    python -m compliance.pipeline --embedder hash     # testa o encanamento sem baixar modelo
    python -m compliance.pipeline --local             # sobre data/local (coleta feita com --local)

Lê os snapshots da rodada (não acessa portais). Pode ser reexecutado sobre rodadas antigas
quando o checklist, os verificadores ou os pesos mudarem.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from compliance.checkers.semantico import IndiceSemantico
from compliance.checklist import carregar_checklist, carregar_scoring
from compliance.engine import ENGINE_VERSION, avaliar_ente
from compliance.repo import AvaliacaoRevisada, ConclusaoRepo, build_repo
from compliance.report import gerar as gerar_relatorio
from ingestion.embeddings.generate_embeddings import criar_embedder
from ingestion.embeddings.generate_embeddings import gerar as gerar_embeddings
from ingestion.settings import Settings, get_settings

log = logging.getLogger("conclusao")


def executar(repo: ConclusaoRepo, run_id: str, s: Settings, embedder=None, entes: list[str] | None = None,
             forcar: bool = False, data_ref: date | None = None, manter_rodadas: int | None = 2) -> dict:
    cfg = carregar_scoring()
    checklist = carregar_checklist(scoring=cfg)
    versao_id = repo.upsert_checklist(checklist)
    log.info("checklist %s (%d quesitos) · versão %s", checklist.nome, len(checklist.criterios), versao_id)

    resumo_emb = None
    if embedder is not None:
        resumo_emb = gerar_embeddings(repo, run_id, embedder)
        log.info("embeddings: %s", resumo_emb)
        if manter_rodadas:
            podados = repo.podar_chunks(manter_rodadas)
            if podados:
                log.info("trechos de rodadas antigas removidos: %d", podados)

    alvos = [e for e in repo.entes_da_rodada(run_id) if not entes or e["slug"] in entes]
    avals, saida = [], {}
    for e in alvos:
        slug = e["slug"]
        if repo.tem_revisoes(run_id, slug) and not forcar:
            raise AvaliacaoRevisada(f"{slug}: a avaliação desta rodada já tem revisão humana; use --forcar para "
                                    "substituí-la (as revisões serão apagadas).")
        log.info("%s: avaliando %d quesitos...", slug, len(checklist.criterios))
        coleta = repo.carregar_coleta(run_id, slug)
        indice = IndiceSemantico.de_chunks(repo.chunks_do_ente(run_id, slug, embedder.nome)) if embedder else None
        aval = avaliar_ente(coleta, checklist, cfg, data_ref=data_ref, indice=indice, embedder=embedder)
        repo.salvar_avaliacao(run_id, slug, versao_id, aval, embedder.nome if embedder else None)
        avals.append(aval)
        sc = aval.score
        log.info("%s: score %s (%s–%s) · %s · cobertura %s", slug, sc.score, sc.score_min, sc.score_max,
                 sc.classificacao, sc.cobertura)
        saida[slug] = {"score": sc.score, "score_min": sc.score_min, "score_max": sc.score_max,
                       "cobertura": sc.cobertura, "essenciais_ok": sc.essenciais_ok,
                       "classificacao": sc.classificacao, "contagem": sc.scores_bloco["contagem"]}
    rel = gerar_relatorio(run_id, avals, cfg, s.data_dir, resumo_emb) if avals else None
    return {"run_id": run_id, "engine_version": ENGINE_VERSION, "checklist": checklist.nome,
            "embeddings": resumo_emb, "entes": saida, "relatorio": str(rel) if rel else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Camada 2: embeddings, conformidade e score")
    ap.add_argument("--run", help="rodada de coleta (padrão: a mais recente)")
    ap.add_argument("--entes", help="ids separados por vírgula")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--sem-embeddings", action="store_true", help="não gera/usa embeddings")
    ap.add_argument("--embedder", choices=["modelo", "hash"], default="modelo")
    ap.add_argument("--forcar", action="store_true", help="substitui avaliação que já tem revisão humana")
    ap.add_argument("--manter-rodadas", type=int, default=2, help="rodadas cujos trechos ficam no banco (0 = todas)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    repo = build_repo(s, a.local)
    run_id = a.run or repo.latest_run_id()
    if not run_id:
        print("nenhuma rodada de coleta encontrada", file=sys.stderr)
        return 1
    embedder = None if a.sem_embeddings else criar_embedder(s, a.embedder)
    res = executar(repo, str(run_id), s, embedder, a.entes.split(",") if a.entes else None, a.forcar,
                   manter_rodadas=a.manter_rodadas or None)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["relatorio"]:
        print(open(res["relatorio"], encoding="utf-8").read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
