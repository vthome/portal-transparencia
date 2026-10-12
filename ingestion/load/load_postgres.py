"""Persistência da Camada 1.

`Repository` é a interface que o pipeline usa. Duas implementações:
- PostgresRepository: Supabase/Postgres via psycopg 3, schema `monitor` (produção, CI agendado)
- LocalRepository:    arquivos JSONL em data/local/ (desenvolvimento offline e testes)

Ambas são *append-only* para snapshots e arquivos: nada coletado é sobrescrito, o que
garante a trilha de auditoria e permite reavaliar coletas antigas com regras novas.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ingestion.models import (
    CrawlStats,
    Entity,
    EntityUrl,
    FileArtifact,
    PageSnapshot,
    agora,
)


class Repository(Protocol):
    def start_run(self, gatilho: str, engine_version: str, parametros: dict[str, Any]) -> str: ...
    def finish_run(self, run_id: str, status: str) -> None: ...
    def upsert_entity(self, e: Entity) -> None: ...
    def sync_entity_urls(self, entity_id: str, urls: list[EntityUrl]) -> None: ...
    def start_job(self, run_id: str, entity_id: str) -> str: ...
    def finish_job(self, job_id: str, stats: CrawlStats) -> None: ...
    def save_page(self, job_id: str, entity_id: str, snap: PageSnapshot) -> str: ...
    def save_file(self, page_id: str, entity_id: str, art: FileArtifact) -> str: ...
    def previous_page(self, entity_id: str, url: str) -> dict[str, Any] | None: ...
    def previous_file(self, entity_id: str, url: str) -> dict[str, Any] | None: ...


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


def _dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, default=_json_default)


# ============================================================== Local (JSONL)

class LocalRepository:
    """Uma pasta por tabela, um arquivo JSONL por tabela. Simples de inspecionar com pandas."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ultimo_page: dict[tuple[str, str], dict] = {}
        self._ultimo_file: dict[tuple[str, str], dict] = {}
        self._carregar_indices()

    def _append(self, tabela: str, row: dict[str, Any]) -> None:
        with self._lock, open(self.root / f"{tabela}.jsonl", "a", encoding="utf-8") as f:
            f.write(_dumps(row) + "\n")

    def read(self, tabela: str) -> list[dict[str, Any]]:
        p = self.root / f"{tabela}.jsonl"
        if not p.exists():
            return []
        rows: dict[str, dict] = {}
        ordem: list[dict] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("_update"):          # atualização de linha existente (run/job)
                rows[r["id"]].update({k: v for k, v in r.items() if k != "_update"})
            elif "id" in r:
                rows[r["id"]] = r
                ordem.append(r)
            else:
                ordem.append(r)
        return ordem

    def _carregar_indices(self) -> None:
        for r in self.read("page_snapshot"):
            self._ultimo_page[(r["entity_id"], r["url"])] = r
        for r in self.read("file_artifact"):
            if r.get("sha256"):
                self._ultimo_file[(r["entity_id"], r["url"])] = r

    def start_run(self, gatilho, engine_version, parametros) -> str:
        rid = str(uuid.uuid4())
        self._append("run", {"id": rid, "gatilho": gatilho, "engine_version": engine_version,
                             "parametros": parametros, "status": "em_execucao", "iniciado_em": agora()})
        return rid

    def finish_run(self, run_id, status) -> None:
        self._append("run", {"_update": True, "id": run_id, "status": status, "finalizado_em": agora()})

    def upsert_entity(self, e: Entity) -> None:
        self._append("entity", {k: v for k, v in asdict(e).items() if k not in {"urls"}})

    def sync_entity_urls(self, entity_id, urls) -> None:
        for u in urls:
            self._append("entity_url", {"entity_id": entity_id, **asdict(u), "valido_de": agora()})

    def start_job(self, run_id, entity_id) -> str:
        jid = str(uuid.uuid4())
        self._append("crawl_job", {"id": jid, "run_id": run_id, "entity_id": entity_id,
                                   "status": "em_execucao", "iniciado_em": agora()})
        return jid

    def finish_job(self, job_id, stats) -> None:
        self._append("crawl_job", {"_update": True, "id": job_id, **asdict(stats), "finalizado_em": agora()})

    def save_page(self, job_id, entity_id, snap) -> str:
        pid = str(uuid.uuid4())
        row = {"id": pid, "crawl_job_id": job_id, "entity_id": entity_id, **snap.to_dict()}
        row["id"] = pid
        self._append("page_snapshot", row)
        with self._lock:
            self._ultimo_page[(entity_id, snap.url)] = row
        return pid

    def save_file(self, page_id, entity_id, art) -> str:
        fid = str(uuid.uuid4())
        row = {"id": fid, "page_snapshot_id": page_id, "entity_id": entity_id, **art.to_dict()}
        row["id"] = fid
        self._append("file_artifact", row)
        if art.sha256:
            with self._lock:
                self._ultimo_file[(entity_id, art.url)] = row
        return fid

    def previous_page(self, entity_id, url):
        r = self._ultimo_page.get((entity_id, url))
        return {"texto_hash": (r.get("extraido") or {}).get("texto_hash"), "content_hash": r.get("content_hash")} if r else None

    def previous_file(self, entity_id, url):
        r = self._ultimo_file.get((entity_id, url))
        if not r:
            return None
        meta = r.get("metadados") or {}
        return {**r, "etag": meta.get("etag"), "last_modified": meta.get("last_modified")}


# ============================================================== Postgres / Supabase

class PostgresRepository:
    """psycopg 3 sobre o schema `monitor` (sql/schema.sql v0.2).

    Diferenças de modelo tratadas aqui, para o resto do pipeline não precisar saber:
    - o ente é identificado pelo `slug` (= id do municipios.yaml); o `bigint` do banco
      fica num cache interno
    - ids de run/job/snapshot são `bigint`; a interface os trafega como `str`
    - status de execução usa o enum `monitor.status_execucao` ('executando', não 'em_execucao')
    - tipos de URL do YAML são mapeados para os do banco (site_oficial -> site, ...)

    Uma conexão por thread. Compatível com o pooler do Supabase em modo transação
    (porta 6543): prepared statements desligados. Todas as tabelas são qualificadas
    com `monitor.` — não dependemos de search_path (o pooler não garante).
    """

    URL_TIPO_DB = {"site_oficial": "site", "portal_transparencia": "portal"}
    STATUS_DB = {"em_execucao": "executando"}

    def __init__(self, dsn: str) -> None:
        import psycopg  # import tardio: testes e modo local não exigem o driver

        self._psycopg = psycopg
        self._dsn = dsn
        self._local = threading.local()
        self._ids: dict[str, int] = {}          # slug -> monitor.entity.id
        self._respeita: dict[str, bool] = {}    # slug -> respeita robots.txt
        self._lock = threading.Lock()

    @property
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None or c.closed:
            c = self._psycopg.connect(self._dsn, autocommit=True, prepare_threshold=None,
                                      application_name="coleta-portais")
            self._local.conn = c
        return c

    def _one(self, sql: str, params: tuple) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    @staticmethod
    def _j(o: Any):
        from psycopg.types.json import Jsonb

        return Jsonb(_sem_nul(o), dumps=_dumps)

    def _eid(self, slug: str) -> int:
        with self._lock:
            if slug in self._ids:
                return self._ids[slug]
        eid = self._one("select id from monitor.entity where slug = %s", (slug,))
        if eid is None:
            raise KeyError(f"ente {slug!r} não cadastrado (upsert_entity antes)")
        with self._lock:
            self._ids[slug] = eid
        return eid

    # ---------------------------------------------------------- execução

    def start_run(self, gatilho, engine_version, parametros) -> str:
        return str(self._one(
            """insert into monitor.run (gatilho, engine_version, git_sha, parametros)
               values (%s, %s, %s, %s) returning id""",
            (gatilho, engine_version, os.getenv("GITHUB_SHA"), self._j(parametros))))

    def finish_run(self, run_id, status) -> None:
        self.conn.execute(
            "update monitor.run set status = %s::monitor.status_execucao, finalizado_em = now() where id = %s",
            (self.STATUS_DB.get(status, status), int(run_id)))

    def upsert_entity(self, e: Entity) -> None:
        eid = self._one(
            """insert into monitor.entity (slug, nome, municipio, uf, cod_ibge, tipo_entidade, poder, esfera)
               values (%s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (slug) do update set nome = excluded.nome, municipio = excluded.municipio,
                 uf = excluded.uf, cod_ibge = excluded.cod_ibge, tipo_entidade = excluded.tipo_entidade,
                 poder = excluded.poder, esfera = excluded.esfera, atualizado_em = now()
               returning id""",
            (e.id, e.nome, e.municipio, e.uf, e.codigo_ibge, e.tipo_entidade, e.poder, e.esfera))
        with self._lock:
            self._ids[e.id] = eid
            self._respeita[e.id] = not e.robots_override

    def sync_entity_urls(self, entity_id, urls) -> None:
        """Mantém vigência: URL que sumiu da lista ganha `valido_ate`; nova URL vira nova linha."""
        eid = self._eid(entity_id)
        respeita = self._respeita.get(entity_id, True)
        novas = {}
        for u in urls:
            novas[(self.URL_TIPO_DB.get(u.tipo, u.tipo), u.url)] = u
        with self.conn.transaction():
            vigentes = {(t, url): i for i, t, url in self.conn.execute(
                "select id, tipo, url from monitor.entity_url where entity_id = %s and valido_ate is null",
                (eid,))}
            for chave, id_ in vigentes.items():
                if chave not in novas:
                    self.conn.execute("update monitor.entity_url set valido_ate = now() where id = %s", (id_,))
            for (tipo, url), u in novas.items():
                if (tipo, url) in vigentes:
                    self.conn.execute(
                        """update monitor.entity_url set plataforma = coalesce(%s, plataforma), evidencia = %s,
                             respeita_robots = %s, dominio_gov = %s where id = %s""",
                        (u.plataforma, self._j(u.evidencia), respeita, u.dominio_gov, vigentes[(tipo, url)]))
                else:
                    self.conn.execute(
                        """insert into monitor.entity_url
                             (entity_id, tipo, url, plataforma, respeita_robots, fonte, dominio_gov, evidencia)
                           values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (eid, tipo, url, u.plataforma, respeita, u.fonte, u.dominio_gov, self._j(u.evidencia)))

    def start_job(self, run_id, entity_id) -> str:
        return str(self._one(
            """insert into monitor.crawl_job (run_id, entity_id, status, tentativas, iniciado_em)
               values (%s, %s, 'executando', 1, now())
               on conflict (run_id, entity_id) do update
                 set tentativas = monitor.crawl_job.tentativas + 1, status = 'executando',
                     erro = null, iniciado_em = now()
               returning id""", (int(run_id), self._eid(entity_id))))

    def finish_job(self, job_id, s: CrawlStats) -> None:
        self.conn.execute(
            """update monitor.crawl_job set status = %s::monitor.status_execucao, erro = %s,
                 plataforma_detectada = %s, fingerprint = %s, paginas = %s, arquivos = %s, requisicoes = %s,
                 renderizacoes = %s, bytes = %s, paginas_inalteradas = %s, bloqueios_robots = %s,
                 secoes_encontradas = %s, secoes_ausentes = %s, segundos = %s, finalizado_em = now()
               where id = %s""",
            (self.STATUS_DB.get(s.status, s.status), _sem_nul(s.erro), s.plataforma_detectada,
             self._j(s.fingerprint), s.paginas, s.arquivos, s.requisicoes, s.renderizacoes, s.bytes,
             s.paginas_inalteradas, s.bloqueios_robots, s.secoes_encontradas, s.secoes_ausentes,
             s.duracao_s, int(job_id)))

    # ---------------------------------------------------------- snapshots

    def save_page(self, job_id, entity_id, p: PageSnapshot) -> str:
        # texto e links têm colunas próprias (índice trigram em `texto`); o resto vai em `extraido`
        extraido = dict(p.extraido)
        texto = extraido.pop("texto", None)
        links = extraido.pop("links", None)
        return str(self._one(
            """insert into monitor.page_snapshot (crawl_job_id, entity_id, url, url_final, http_status,
                 content_type, content_hash, tamanho, titulo, coleta_status, erro, renderizado_js,
                 robots_override, profundidade, trilha, secoes, texto, links, extraido, storage_path,
                 screenshot_path, etag, last_modified, inalterado, coletado_em)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::monitor.status_coleta, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               returning id""",
            (int(job_id), self._eid(entity_id), p.url, p.url_final, p.http_status, p.content_type,
             p.content_hash, p.tamanho, _sem_nul(p.titulo), p.coleta_status, _sem_nul(p.erro),
             p.renderizado_js, p.robots_override, p.profundidade, self._j(p.trilha), p.secoes,
             _sem_nul(texto), self._j(links) if links is not None else None, self._j(extraido),
             p.storage_path, p.screenshot_path, p.etag, p.last_modified, p.inalterado, p.coletado_em)))

    def save_file(self, page_id, entity_id, a: FileArtifact) -> str:
        return str(self._one(
            """insert into monitor.file_artifact (page_snapshot_id, entity_id, url, texto_link, mime, extensao,
                 sha256, tamanho, storage_path, paginas, texto_extraido, metadados, secoes, erro, coletado_em)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (int(page_id), self._eid(entity_id), a.url, _sem_nul(a.texto_link), a.mime, a.extensao, a.sha256,
             a.tamanho, a.storage_path, a.paginas, _sem_nul(a.texto_extraido), self._j(a.metadados), a.secoes,
             _sem_nul(a.erro), a.coletado_em)))

    def previous_page(self, entity_id, url):
        row = self.conn.execute(
            """select extraido->>'texto_hash', content_hash from monitor.page_snapshot
               where entity_id = %s and url = %s and coleta_status = 'ok'
               order by coletado_em desc limit 1""",
            (self._eid(entity_id), url)).fetchone()
        return {"texto_hash": row[0], "content_hash": row[1]} if row else None

    def previous_file(self, entity_id, url):
        row = self.conn.execute(
            """select mime, sha256, tamanho, storage_path, paginas, texto_extraido, metadados
               from monitor.file_artifact
               where entity_id = %s and url = %s and sha256 is not null
               order by coletado_em desc limit 1""",
            (self._eid(entity_id), url)).fetchone()
        if not row:
            return None
        meta = row[6] or {}
        return {"mime": row[0], "sha256": row[1], "tamanho": row[2], "storage_path": row[3], "paginas": row[4],
                "texto_extraido": row[5], "metadados": meta, "etag": meta.get("etag"),
                "last_modified": meta.get("last_modified")}


def _sem_nul(o: Any) -> Any:
    """Postgres rejeita \\x00 em text/jsonb — comum em texto extraído de PDF."""
    if isinstance(o, str):
        return o.replace("\x00", "")
    if isinstance(o, dict):
        return {k: _sem_nul(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sem_nul(v) for v in o]
    return o
