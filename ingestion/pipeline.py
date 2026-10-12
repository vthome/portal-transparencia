"""Orquestra uma rodada de coleta (Camada 1).

Uso:
    python -m ingestion.pipeline                       # todos os entes, Supabase se configurado
    python -m ingestion.pipeline --entes rj-marica     # um ente
    python -m ingestion.pipeline --local --sem-render  # sem banco/navegador: grava em data/local
    python -m ingestion.pipeline --gatilho agendado    # usado pelo GitHub Actions

Saída: resumo JSON no stdout e em data/runs/<run_id>.json. Código de saída 1 só se
TODOS os entes falharem (falha parcial é resultado legítimo, não erro do pipeline).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from typing import Any

from ingestion.extract.extract_data import collect_entity
from ingestion.load.load_postgres import LocalRepository, PostgresRepository, Repository
from ingestion.load.storage import LocalStorage, Storage, SupabaseStorage
from ingestion.models import Entity
from ingestion.settings import ENGINE_VERSION, Settings, get_settings
from ingestion.transform.validate import load_entities, load_platforms, load_secoes

log = logging.getLogger("pipeline")


class ConfigAusente(SystemExit):
    pass


def build_backends(settings: Settings, local: bool, storage: bool = True) -> tuple[Repository, Storage | None]:
    """--local grava em data/; sem --local, exige Supabase completo (nada de cair no local
    em silêncio e o usuário achar que gravou no banco)."""
    if local:
        return LocalRepository(settings.data_dir / "local"), \
            (LocalStorage(settings.data_dir / "objetos") if storage else None)
    faltam = [n for n, v in (("DATABASE_URL", settings.database_url), ("SUPABASE_URL", settings.supabase_url),
                             ("SUPABASE_SECRET_KEY", settings.supabase_secret_key)) if not v]
    if (storage and faltam) or not settings.database_url:
        raise ConfigAusente(f"faltam variáveis {faltam} no .env (ou use --local). "
                            "Rode `python -m ingestion.preflight` para diagnosticar.")
    repo: Repository = PostgresRepository(settings.database_url)
    if not storage:
        return repo, None
    sb = SupabaseStorage(settings.supabase_url, settings.supabase_secret_key, settings.supabase_bucket)
    sb.ensure_bucket()
    return repo, sb


def run(entities: list[Entity], repo: Repository, storage: Storage, settings: Settings,
        gatilho: str = "manual", **collect_kwargs: Any) -> dict[str, Any]:
    # valida configs antes de abrir a rodada
    load_platforms(), load_secoes()
    params = {"entes": [e.id for e in entities], "max_paginas": settings.max_paginas,
              "max_profundidade": settings.max_profundidade, "renderizar": settings.renderizar,
              "req_por_segundo": settings.req_por_segundo}
    run_id = repo.start_run(gatilho, ENGINE_VERSION, params)
    log.info("rodada %s: %d entes", run_id, len(entities))

    resultados: dict[str, Any] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, settings.paralelismo_entes)) as pool:
            futs = {pool.submit(collect_entity, e, run_id, repo, storage, settings, **collect_kwargs): e
                    for e in entities}
            for fut in as_completed(futs):
                e = futs[fut]
                stats = fut.result()
                resultados[e.id] = asdict(stats)
                log.info("[%d/%d] %s concluído (%s)", len(resultados), len(entities), e.id, stats.status)
    except BaseException:
        # Ctrl+C ou erro inesperado: a rodada não fica eternamente 'executando'
        repo.finish_run(run_id, "falhou")
        raise

    status_jobs = [r["status"] for r in resultados.values()]
    if all(s == "concluido" for s in status_jobs):
        status = "concluido"
    elif all(s == "falhou" for s in status_jobs):
        status = "falhou"
    else:
        status = "parcial"
    repo.finish_run(run_id, status)

    resumo = {"run_id": run_id, "status": status, "engine_version": ENGINE_VERSION,
              "entes": {k: {c: v[c] for c in ("status", "plataforma_detectada", "paginas", "arquivos",
                                               "requisicoes", "renderizacoes", "bloqueios_robots",
                                               "secoes_encontradas", "secoes_ausentes", "duracao_s", "erro")}
                        for k, v in sorted(resultados.items())}}
    out = settings.data_dir / "runs"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run_id}.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from ingestion.report import gerar_relatorio

        resumo["relatorio"] = str(gerar_relatorio(repo, str(run_id), settings.data_dir,
                                                  [e.id for e in entities]))
    except Exception as e:  # relatório é conveniência; nunca derruba a rodada
        log.warning("não foi possível gerar o relatório: %s", e)
    return resumo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Coleta de portais da transparência (Camada 1)")
    ap.add_argument("--entes", help="ids separados por vírgula (padrão: todos os ativos)")
    ap.add_argument("--gatilho", default="manual", choices=["manual", "agendado", "reprocessamento"])
    ap.add_argument("--local", action="store_true", help="grava em data/local e data/objetos (sem Supabase)")
    ap.add_argument("--sem-render", action="store_true", help="não usa Playwright")
    ap.add_argument("--max-paginas", type=int)
    ap.add_argument("--paralelismo", type=int)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    s = replace(s, renderizar=s.renderizar and not a.sem_render,
                max_paginas=a.max_paginas or s.max_paginas,
                paralelismo_entes=a.paralelismo or s.paralelismo_entes)
    entities = load_entities(only=a.entes.split(",") if a.entes else None)
    repo, storage = build_backends(s, a.local)
    resumo = run(entities, repo, storage, s, gatilho=a.gatilho)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 1 if resumo["status"] == "falhou" else 0


if __name__ == "__main__":
    sys.exit(main())
