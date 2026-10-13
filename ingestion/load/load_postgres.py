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
    # leitura para relatório e reextração
    def latest_run_id(self) -> str | None: ...
    def run_jobs(self, run_id: str) -> list[dict[str, Any]]: ...
    def run_snapshot_stats(self, run_id: str) -> list[dict[str, Any]]: ...
    def run_top_erros(self, run_id: str, limite: int = 5) -> list[dict[str, Any]]: ...
    def run_paginas_por_host(self, run_id: str) -> list[dict[str, Any]]: ...
    def snapshots_para_reextrair(self, run_id: str, parser_version: int | None) -> list[dict[str, Any]]: ...
    def atualizar_extracao(self, snapshot_id: str, coleta_status: str, titulo: str | None,
                           secoes: list[str], extraido: dict[str, Any]) -> None: ...
    def atualizar_secoes_job(self, job_id: str, encontradas: list[str], ausentes: list[str]) -> None: ...


# Status que vêm de uma extração (podem mudar no reextract); os demais vêm da coleta
STATUS_EXTRAIVEIS = ("ok", "vazio", "soft404")


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

    # ---- leitura

    def latest_run_id(self):
        runs = self.read("run")
        return runs[-1]["id"] if runs else None

    def _jobs(self, run_id):
        return [j for j in self.read("crawl_job") if j["run_id"] == run_id]

    def run_jobs(self, run_id):
        nomes = {e["id"]: e.get("nome") for e in self.read("entity")}
        out = []
        for j in self._jobs(run_id):
            out.append({"job_id": j["id"], "slug": j["entity_id"], "nome": nomes.get(j["entity_id"]),
                        **{k: j.get(k) for k in ("status", "plataforma_detectada", "paginas", "arquivos",
                                                 "requisicoes", "renderizacoes", "bloqueios_robots",
                                                 "paginas_inalteradas", "secoes_encontradas",
                                                 "secoes_ausentes", "duracao_s", "erro", "fingerprint")}})
        return out

    def _snaps(self, run_id):
        jobs = {j["id"]: j["entity_id"] for j in self._jobs(run_id)}
        return [(jobs[p["crawl_job_id"]], p) for p in self.read("page_snapshot") if p["crawl_job_id"] in jobs]

    def run_snapshot_stats(self, run_id):
        cont: dict[tuple, dict] = {}
        for slug, p in self._snaps(run_id):
            k = (slug, p["coleta_status"])
            c = cont.setdefault(k, {"slug": slug, "coleta_status": p["coleta_status"], "n": 0, "renderizados": 0})
            c["n"] += 1
            c["renderizados"] += 1 if p.get("renderizado_js") else 0
        return sorted(cont.values(), key=lambda r: (r["slug"], r["coleta_status"]))

    def run_top_erros(self, run_id, limite=5):
        cont: dict[tuple, int] = {}
        for slug, p in self._snaps(run_id):
            if p.get("erro"):
                k = (slug, p["erro"].split(":")[0])
                cont[k] = cont.get(k, 0) + 1
        rows = [{"slug": k[0], "erro": k[1], "n": n} for k, n in cont.items()]
        return sorted(rows, key=lambda r: (r["slug"], -r["n"]))

    def run_paginas_por_host(self, run_id):
        from ingestion.transform.normalize import host_of

        cont: dict[tuple, dict] = {}
        for slug, p in self._snaps(run_id):
            k = (slug, host_of(p["url"]))
            c = cont.setdefault(k, {"slug": slug, "host": k[1], "paginas": 0, "ok": 0})
            c["paginas"] += 1
            c["ok"] += 1 if p["coleta_status"] == "ok" else 0
        return sorted(cont.values(), key=lambda r: (r["slug"], -r["paginas"]))

    def snapshots_para_reextrair(self, run_id, parser_version):
        out = []
        for slug, p in self._snaps(run_id):
            ext = p.get("extraido") or {}
            if p["coleta_status"] in STATUS_EXTRAIVEIS and p.get("storage_path") and \
                    (parser_version is None or ext.get("parser_version") != parser_version):
                out.append({"id": p["id"], "job_id": p["crawl_job_id"], "slug": slug, "url": p["url"],
                            "url_final": p.get("url_final"), "http_status": p.get("http_status"),
                            "storage_path": p["storage_path"], "coleta_status": p["coleta_status"],
                            "secoes": p.get("secoes") or [], "extraido": ext})
        return out

    def atualizar_extracao(self, snapshot_id, coleta_status, titulo, secoes, extraido):
        self._append("page_snapshot", {"_update": True, "id": snapshot_id, "coleta_status": coleta_status,
                                       "titulo": titulo, "secoes": secoes, "extraido": extraido})

    def atualizar_secoes_job(self, job_id, encontradas, ausentes):
        self._append("crawl_job", {"_update": True, "id": job_id, "secoes_encontradas": encontradas,
                                   "secoes_ausentes": ausentes})

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

    # ---------------------------------------------------------- leitura (relatório / reextração)

    def _dicts(self, sql: str, params: tuple) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def latest_run_id(self):
        rid = self._one("select id from monitor.run order by id desc limit 1", ())
        return str(rid) if rid is not None else None

    def run_jobs(self, run_id):
        rows = self._dicts(
            """select j.id::text as job_id, e.slug, e.nome, j.status::text as status, j.plataforma_detectada,
                      j.paginas, j.arquivos, j.requisicoes, j.renderizacoes, j.bloqueios_robots,
                      j.paginas_inalteradas, j.secoes_encontradas, j.secoes_ausentes,
                      j.segundos::float as duracao_s, j.erro, j.fingerprint
               from monitor.crawl_job j join monitor.entity e on e.id = j.entity_id
               where j.run_id = %s order by e.slug""", (int(run_id),))
        for r in rows:  # mesmo vocabulário do LocalRepository
            r["status"] = {"executando": "em_execucao"}.get(r["status"], r["status"])
        return rows

    def run_snapshot_stats(self, run_id):
        return self._dicts(
            """select e.slug, p.coleta_status::text as coleta_status, count(*)::int as n,
                      count(*) filter (where p.renderizado_js)::int as renderizados
               from monitor.page_snapshot p join monitor.crawl_job j on j.id = p.crawl_job_id
               join monitor.entity e on e.id = j.entity_id
               where j.run_id = %s group by 1, 2 order by 1, 2""", (int(run_id),))

    def run_top_erros(self, run_id, limite=5):
        return self._dicts(
            """select slug, erro, n from (
                 select e.slug, split_part(p.erro, ':', 1) as erro, count(*)::int as n,
                        row_number() over (partition by e.slug order by count(*) desc) as rk
                 from monitor.page_snapshot p join monitor.crawl_job j on j.id = p.crawl_job_id
                 join monitor.entity e on e.id = j.entity_id
                 where j.run_id = %s and p.erro is not null group by 1, 2) t
               where rk <= %s order by slug, n desc""", (int(run_id), limite))

    def run_paginas_por_host(self, run_id):
        return self._dicts(
            """select e.slug, lower(split_part(split_part(split_part(split_part(p.url, '://', 2), '/', 1), '#', 1), ':', 1)) as host,
                      count(*)::int as paginas,
                      count(*) filter (where p.coleta_status = 'ok')::int as ok
               from monitor.page_snapshot p join monitor.crawl_job j on j.id = p.crawl_job_id
               join monitor.entity e on e.id = j.entity_id
               where j.run_id = %s group by 1, 2 order by 1, 3 desc""", (int(run_id),))

    def snapshots_para_reextrair(self, run_id, parser_version):
        return self._dicts(
            """select p.id::text as id, p.crawl_job_id::text as job_id, e.slug, p.url, p.url_final,
                      p.http_status, p.storage_path, p.coleta_status::text as coleta_status, p.secoes, p.extraido
               from monitor.page_snapshot p join monitor.crawl_job j on j.id = p.crawl_job_id
               join monitor.entity e on e.id = j.entity_id
               where j.run_id = %s and p.storage_path is not null
                 and p.coleta_status in ('ok', 'vazio', 'soft404')
                 and (%s::int is null or coalesce((p.extraido->>'parser_version')::int, 0) <> %s::int)
               order by p.id""", (int(run_id), parser_version, parser_version))

    def atualizar_extracao(self, snapshot_id, coleta_status, titulo, secoes, extraido):
        extraido = dict(extraido)
        texto = extraido.pop("texto", None)
        links = extraido.pop("links", None)
        self.conn.execute(
            """update monitor.page_snapshot set coleta_status = %s::monitor.status_coleta, titulo = %s,
                 secoes = %s, texto = %s, links = %s, extraido = %s where id = %s""",
            (coleta_status, _sem_nul(titulo), secoes, _sem_nul(texto),
             self._j(links) if links is not None else None, self._j(extraido), int(snapshot_id)))

    def atualizar_secoes_job(self, job_id, encontradas, ausentes):
        self.conn.execute(
            "update monitor.crawl_job set secoes_encontradas = %s, secoes_ausentes = %s where id = %s",
            (encontradas, ausentes, int(job_id)))


def _sem_nul(o: Any) -> Any:
    """Postgres rejeita \\x00 em text/jsonb — comum em texto extraído de PDF."""
    if isinstance(o, str):
        return o.replace("\x00", "")
    if isinstance(o, dict):
        return {k: _sem_nul(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sem_nul(v) for v in o]
    return o
