"""Reexecuta só a fase de EXTRAÇÃO sobre snapshots já coletados (sem acessar os portais).

Quando usar: mudou o parser (`parse_html.py`), o `secoes.yaml` ou a classificação de
página. O HTML bruto está no Storage (chave = sha256), então basta reler e reextrair.

    python -m ingestion.reextract                  # última rodada, só snapshots com parser antigo
    python -m ingestion.reextract --run 12 --todos # força todos os snapshots da rodada 12
    python -m ingestion.reextract --local          # sobre data/local + data/objetos

O que muda: `titulo`, `coleta_status` (só entre ok/vazio/soft404), `secoes`, `texto`,
`links`, `extraido` e as seções encontradas/ausentes do job. O que NÃO muda: bytes
coletados, hashes, trilha, HTTP status, horário da coleta — a evidência original fica intacta.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from typing import Any

from ingestion.load.load_postgres import Repository
from ingestion.load.storage import Storage
from ingestion.pipeline import build_backends
from ingestion.settings import get_settings
from ingestion.transform.extracao import PARSER_VERSION, extrair_pagina
from ingestion.transform.normalize import decode_bytes
from ingestion.transform.validate import load_secoes

log = logging.getLogger("reextract")


def reextrair(repo: Repository, storage: Storage, run_id: str, todos: bool = False) -> dict[str, Any]:
    secoes_cfg = load_secoes()["secoes"]
    extraiveis = repo.snapshots_para_reextrair(run_id, None)
    alvos = [s for s in extraiveis
             if todos or (s.get("extraido") or {}).get("parser_version") != PARSER_VERSION]
    resumo = {"run_id": run_id, "parser_version": PARSER_VERSION, "avaliados": len(alvos),
              "atualizados": 0, "status_mudou": 0, "falhas": 0}
    # seções do job = união das seções das páginas extraídas; começa com as que não serão refeitas
    ids_alvo = {s["id"] for s in alvos}
    secoes_job: dict[str, set[str]] = defaultdict(set)
    for s in extraiveis:
        if s["id"] not in ids_alvo:
            secoes_job[s["job_id"]].update(s.get("secoes") or [])

    for snap in alvos:
        try:
            html = decode_bytes(storage.get(snap["storage_path"]), "text/html")
        except Exception as e:  # objeto ausente no Storage: mantém a extração antiga
            log.warning("snapshot %s: não foi possível ler %s: %s", snap["id"], snap["storage_path"], e)
            resumo["falhas"] += 1
            secoes_job[snap["job_id"]].update(snap.get("secoes") or [])
            continue
        antigo = snap.get("extraido") or {}
        pag = extrair_pagina(html, snap["url"], snap.get("url_final") or snap["url"],
                             antigo.get("texto_link", ""), secoes_cfg, snap.get("http_status"))
        if "fingerprint_host" in antigo:
            pag.extraido["fingerprint_host"] = antigo["fingerprint_host"]
        repo.atualizar_extracao(snap["id"], pag.coleta_status, pag.parsed.titulo, pag.secoes, pag.extraido)
        resumo["atualizados"] += 1
        if pag.coleta_status != snap["coleta_status"]:
            resumo["status_mudou"] += 1
        secoes_job[snap["job_id"]].update(pag.secoes)

    # seções do job = união das seções das páginas (inclui as não reextraídas)
    todas = sorted(secoes_cfg)
    jobs_afetados = {s["job_id"] for s in alvos}
    for job in repo.run_jobs(run_id):
        if job["job_id"] not in jobs_afetados:
            continue
        encontradas = sorted(secoes_job[job["job_id"]])
        repo.atualizar_secoes_job(job["job_id"], encontradas, [s for s in todas if s not in encontradas])
    return resumo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reextração sobre snapshots armazenados")
    ap.add_argument("--run", help="id da rodada (padrão: a mais recente)")
    ap.add_argument("--todos", action="store_true", help="reextrai mesmo snapshots já na versão atual")
    ap.add_argument("--local", action="store_true", help="usa data/local e data/objetos")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    repo, storage = build_backends(get_settings(), a.local)
    run_id = a.run or repo.latest_run_id()
    if not run_id:
        print("nenhuma rodada encontrada", file=sys.stderr)
        return 1
    resumo = reextrair(repo, storage, str(run_id), a.todos)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 1 if resumo["falhas"] and not resumo["atualizados"] else 0


if __name__ == "__main__":
    sys.exit(main())
