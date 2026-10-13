"""Persistência da Camada 2.

- PostgresConclusao: schema `monitor` no Supabase (checklist_version, criterion, chunk,
  evaluation, criterion_result, evidence). Lê a coleta gravada pela Camada 1.
- LocalConclusao: arquivos JSONL em data/local (mesma pasta do modo local da coleta),
  para desenvolvimento offline e testes.

Reavaliar a mesma rodada substitui a avaliação anterior daquele ente (delete + insert).
Se já houver revisão humana sobre ela, a substituição é recusada sem --forcar.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from compliance.checklist import Checklist
from compliance.coleta import Arquivo, ColetaEnte, Pagina
from compliance.engine import ENGINE_VERSION, AvaliacaoEnte
from ingestion.embeddings.generate_embeddings import texto_para_vetor, vetor_para_texto
from ingestion.load.load_postgres import LocalRepository, _dumps, _sem_nul
from ingestion.settings import Settings

URL_TIPO_YAML = {"site": "site_oficial", "portal": "portal_transparencia"}


class AvaliacaoRevisada(SystemExit):
    pass


class ConclusaoRepo(Protocol):
    def latest_run_id(self) -> str | None: ...
    def entes_da_rodada(self, run_id: str) -> list[dict[str, Any]]: ...
    def carregar_coleta(self, run_id: str, slug: str) -> ColetaEnte: ...
    def upsert_checklist(self, checklist: Checklist) -> str: ...
    def dimensao_embedding(self) -> int | None: ...
    def snapshots_para_embedding(self, run_id: str, modelo: str) -> tuple[list[dict], list[dict]]: ...
    def vetores_existentes(self, modelo: str, hashes: list[str]) -> dict[str, list[float]]: ...
    def salvar_chunks(self, linhas: list[dict[str, Any]]) -> None: ...
    def chunks_do_ente(self, run_id: str, slug: str, modelo: str) -> list[dict[str, Any]]: ...
    def podar_chunks(self, manter_rodadas: int) -> int: ...
    def tem_revisoes(self, run_id: str, slug: str) -> bool: ...
    def salvar_avaliacao(self, run_id: str, slug: str, versao_id: str, aval: AvaliacaoEnte,
                         modelo: str | None) -> str: ...
    def revisoes_para_calibracao(self) -> list[dict[str, Any]]: ...


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================== Local (JSONL)

class LocalConclusao:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.coleta = LocalRepository(self.root)
        self._lock = threading.Lock()

    def _append(self, tabela: str, row: dict[str, Any]) -> None:
        with self._lock, open(self.root / f"{tabela}.jsonl", "a", encoding="utf-8") as f:
            f.write(_dumps(row) + "\n")

    def _ler(self, tabela: str) -> list[dict[str, Any]]:
        p = self.root / f"{tabela}.jsonl"
        if not p.exists():
            return []
        out, apagados = [], set()
        for line in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("_delete"):
                apagados.add(r["id"])
            else:
                out.append(r)
        return [r for r in out if r.get("id") not in apagados]

    def read(self, tabela: str) -> list[dict[str, Any]]:
        """Como _ler, mas emulando o `on delete cascade` do Postgres (sem órfãos)."""
        linhas = self._ler(tabela)
        if tabela in {"criterion_result", "evidence", "human_review"}:
            evs = {e["id"] for e in self._ler("evaluation")}
            crs = {c["id"] for c in self._ler("criterion_result") if c["evaluation_id"] in evs}
            if tabela == "criterion_result":
                return [c for c in linhas if c["id"] in crs]
            return [x for x in linhas if x["criterion_result_id"] in crs]
        return linhas

    # ---- coleta
    def latest_run_id(self):
        return self.coleta.latest_run_id()

    def _jobs(self, run_id):
        return {j["entity_id"]: j for j in self.coleta.read("crawl_job") if j["run_id"] == run_id}

    def entes_da_rodada(self, run_id):
        ents = {e["id"]: e for e in self.coleta.read("entity")}
        out = []
        for slug, j in sorted(self._jobs(run_id).items()):
            e = ents.get(slug, {})
            out.append({"slug": slug, "nome": e.get("nome", slug), "tipo_entidade": e.get("tipo_entidade", "prefeitura"),
                        "poder": e.get("poder", "executivo"), "esfera": e.get("esfera", "municipal"), "job": j})
        return out

    def carregar_coleta(self, run_id, slug):
        info = next(x for x in self.entes_da_rodada(run_id) if x["slug"] == slug)
        job = info["job"]
        paginas = []
        for p in self.coleta.read("page_snapshot"):
            if p["crawl_job_id"] != job["id"]:
                continue
            ext = dict(p.get("extraido") or {})
            paginas.append(Pagina(id=p["id"], url=p["url"], titulo=p.get("titulo"), status=p["coleta_status"],
                                  secoes=p.get("secoes") or [], trilha=p.get("trilha") or [],
                                  profundidade=p.get("profundidade", 0), renderizado=bool(p.get("renderizado_js")),
                                  texto=ext.pop("texto", "") or "", links=ext.pop("links", []) or [], extraido=ext,
                                  screenshot_path=p.get("screenshot_path")))
        ids = {p.id for p in paginas}
        arquivos = [Arquivo(id=a["id"], page_id=a.get("page_snapshot_id"), url=a["url"], texto_link=a.get("texto_link"),
                            extensao=a.get("extensao"), metadados=a.get("metadados") or {}, secoes=a.get("secoes") or [],
                            texto=a.get("texto_extraido") or "", erro=a.get("erro"), sha256=a.get("sha256"))
                    for a in self.coleta.read("file_artifact") if a.get("page_snapshot_id") in ids]
        urls = {}
        for u in self.coleta.read("entity_url"):
            if u["entity_id"] == slug:
                urls[(u["tipo"], u["url"])] = {"tipo": u["tipo"], "url": u["url"], "fonte": u.get("fonte"),
                                               "dominio_gov": u.get("dominio_gov")}
        return ColetaEnte(slug=slug, nome=info["nome"], tipo_entidade=info["tipo_entidade"], poder=info["poder"],
                          esfera=info["esfera"], job=job, urls=list(urls.values()), paginas=paginas, arquivos=arquivos)

    # ---- checklist
    def upsert_checklist(self, checklist):
        vers = [v for v in self._ler("checklist_version") if v["sha256"] == checklist.sha256]
        if vers:
            vid = vers[0]["id"]
            ids = {c["codigo"]: c["id"] for c in self._ler("criterion") if c["checklist_version_id"] == vid}
        else:
            vid = str(uuid.uuid4())
            self._append("checklist_version", {"id": vid, "nome": checklist.nome, "sha256": checklist.sha256,
                                               "publicado_em": _agora()})
            ids = {}
            for c in checklist.criterios:
                ids[c.codigo] = str(uuid.uuid4())
                self._append("criterion", {"id": ids[c.codigo], "checklist_version_id": vid,
                                           **{k: v for k, v in asdict(c).items() if k != "id"},
                                           "params_hash": c.params_hash})
        for c in checklist.criterios:
            c.id = ids[c.codigo]
        return vid

    # ---- embeddings
    def dimensao_embedding(self):
        return None

    def _snaps_rodada(self, run_id):
        jobs = {j["id"] for j in self._jobs(run_id).values()}
        paginas = [p for p in self.coleta.read("page_snapshot") if p["crawl_job_id"] in jobs]
        ids = {p["id"] for p in paginas}
        arqs = [a for a in self.coleta.read("file_artifact") if a.get("page_snapshot_id") in ids]
        return paginas, arqs

    def snapshots_para_embedding(self, run_id, modelo):
        feitos_p = {c["page_snapshot_id"] for c in self._ler("chunk") if c["modelo"] == modelo}
        feitos_a = {c["file_artifact_id"] for c in self._ler("chunk") if c["modelo"] == modelo}
        paginas, arqs = self._snaps_rodada(run_id)
        ps = [{"id": p["id"], "coleta_status": p["coleta_status"], "titulo": p.get("titulo"),
               "secoes": p.get("secoes"), "texto": (p.get("extraido") or {}).get("texto", "")}
              for p in paginas if p["coleta_status"] == "ok" and p["id"] not in feitos_p]
        As = [{k: a.get(k) for k in ("id", "erro", "sha256", "texto_extraido", "metadados", "texto_link", "secoes",
                                     "extensao")} for a in arqs if a["id"] not in feitos_a]
        return ps, As

    def vetores_existentes(self, modelo, hashes):
        alvo = set(hashes)
        return {c["texto_hash"]: c["embedding"] for c in self._ler("chunk")
                if c["modelo"] == modelo and c["texto_hash"] in alvo}

    def salvar_chunks(self, linhas):
        for ln in linhas:
            self._append("chunk", {"id": str(uuid.uuid4()), **ln})

    def chunks_do_ente(self, run_id, slug, modelo):
        job = self._jobs(run_id)[slug]["id"]
        paginas = {p["id"]: p["url"] for p in self.coleta.read("page_snapshot") if p["crawl_job_id"] == job}
        arqs = {a["id"]: a["url"] for a in self.coleta.read("file_artifact") if a.get("page_snapshot_id") in paginas}
        out = []
        for c in self._ler("chunk"):
            if c["modelo"] != modelo:
                continue
            url = paginas.get(c.get("page_snapshot_id")) or arqs.get(c.get("file_artifact_id"))
            if url:
                out.append({"texto": c["texto"], "page_snapshot_id": c.get("page_snapshot_id"),
                            "file_artifact_id": c.get("file_artifact_id"), "url": url, "embedding": c["embedding"]})
        return out

    def podar_chunks(self, manter_rodadas):
        return 0

    # ---- avaliação
    def tem_revisoes(self, run_id, slug):
        evs = {e["id"] for e in self._ler("evaluation") if e["run_id"] == run_id and e["entity_slug"] == slug}
        crs = {c["id"] for c in self._ler("criterion_result") if c["evaluation_id"] in evs}
        return any(h["criterion_result_id"] in crs for h in self._ler("human_review"))

    def salvar_avaliacao(self, run_id, slug, versao_id, aval, modelo):
        for e in self._ler("evaluation"):
            if e["run_id"] == run_id and e["entity_slug"] == slug:
                self._append("evaluation", {"_delete": True, "id": e["id"]})
        eid = str(uuid.uuid4())
        s = aval.score
        self._append("evaluation", {"id": eid, "run_id": run_id, "entity_slug": slug, "checklist_version_id": versao_id,
                                    "score": s.score, "score_min": s.score_min, "score_max": s.score_max,
                                    "cobertura": s.cobertura, "essenciais_ok": s.essenciais_ok,
                                    "classificacao": s.classificacao, "scores_bloco": s.scores_bloco,
                                    "pesos_usados": s.pesos_usados, "engine_version": ENGINE_VERSION,
                                    "modelo_embedding": modelo, "avaliado_em": _agora()})
        for c, r in aval.resultados:
            rid = str(uuid.uuid4())
            self._append("criterion_result", {"id": rid, "evaluation_id": eid, "criterion_id": c.id, "codigo": c.codigo,
                                              "status": r.status, "nota": r.nota, "confianca": r.confianca,
                                              "metodo": r.metodo, "checker_version": aval.checker_version(c),
                                              "justificativa": r.justificativa, "sinais": r.sinais})
            for ev in r.evidencias:
                self._append("evidence", {"id": str(uuid.uuid4()), "criterion_result_id": rid, **asdict(ev)})
        return eid

    def revisoes_para_calibracao(self):
        crs = {c["id"]: c for c in self._ler("criterion_result")}
        return [{"sinais": crs[h["criterion_result_id"]]["sinais"], "status": crs[h["criterion_result_id"]]["status"],
                 "status_corrigido": h["status_corrigido"]}
                for h in self._ler("human_review") if h["criterion_result_id"] in crs]


# ============================================================== Postgres / Supabase

class PostgresConclusao:
    def __init__(self, dsn: str) -> None:
        import psycopg

        self._psycopg = psycopg
        self._dsn = dsn
        self._local = threading.local()

    @property
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None or c.closed:
            c = self._psycopg.connect(self._dsn, autocommit=True, prepare_threshold=None,
                                      application_name="conformidade")
            self._local.conn = c
        return c

    def _dicts(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    @staticmethod
    def _j(o: Any):
        from psycopg.types.json import Jsonb

        return Jsonb(_sem_nul(o), dumps=_dumps)

    # ---- coleta
    def latest_run_id(self):
        r = self.conn.execute("select max(id) from monitor.run").fetchone()
        return str(r[0]) if r and r[0] is not None else None

    def entes_da_rodada(self, run_id):
        rows = self._dicts(
            """select e.slug, e.nome, e.tipo_entidade, e.poder, e.esfera, j.id::text as job_id, j.status::text as status,
                      j.paginas, j.bloqueios_robots, j.secoes_encontradas
               from monitor.crawl_job j join monitor.entity e on e.id = j.entity_id
               where j.run_id = %s order by e.slug""", (int(run_id),))
        return [{"slug": r["slug"], "nome": r["nome"], "tipo_entidade": r["tipo_entidade"], "poder": r["poder"],
                 "esfera": r["esfera"], "job": {"id": r["job_id"], "status": r["status"], "paginas": r["paginas"],
                                                "bloqueios_robots": r["bloqueios_robots"],
                                                "secoes_encontradas": r["secoes_encontradas"]}} for r in rows]

    def carregar_coleta(self, run_id, slug):
        info = next(x for x in self.entes_da_rodada(run_id) if x["slug"] == slug)
        jid = int(info["job"]["id"])
        paginas = [Pagina(id=str(p["id"]), url=p["url"], titulo=p["titulo"], status=p["coleta_status"],
                          secoes=p["secoes"] or [], trilha=p["trilha"] or [], profundidade=p["profundidade"] or 0,
                          renderizado=bool(p["renderizado_js"]), texto=p["texto"] or "", links=p["links"] or [],
                          extraido=p["extraido"] or {}, screenshot_path=p["screenshot_path"])
                   for p in self._dicts(
                       """select id, url, titulo, coleta_status::text as coleta_status, secoes, trilha, profundidade,
                                 renderizado_js, texto, links, extraido, screenshot_path
                          from monitor.page_snapshot where crawl_job_id = %s order by id""", (jid,))]
        arquivos = [Arquivo(id=str(a["id"]), page_id=str(a["page_snapshot_id"]), url=a["url"], texto_link=a["texto_link"],
                            extensao=a["extensao"], metadados=a["metadados"] or {}, secoes=a["secoes"] or [],
                            texto=a["texto"] or "", erro=a["erro"], sha256=a["sha256"])
                    for a in self._dicts(
                        """select f.id, f.page_snapshot_id, f.url, f.texto_link, f.extensao, f.metadados, f.secoes,
                                  left(f.texto_extraido, 20000) as texto, f.erro, f.sha256
                           from monitor.file_artifact f join monitor.page_snapshot p on p.id = f.page_snapshot_id
                           where p.crawl_job_id = %s order by f.id""", (jid,))]
        urls = [{"tipo": URL_TIPO_YAML.get(u["tipo"], u["tipo"]), "url": u["url"], "fonte": u["fonte"],
                 "dominio_gov": u["dominio_gov"]}
                for u in self._dicts(
                    """select u.tipo, u.url, u.fonte, u.dominio_gov from monitor.entity_url u
                       join monitor.entity e on e.id = u.entity_id where e.slug = %s and u.valido_ate is null""", (slug,))]
        return ColetaEnte(slug=slug, nome=info["nome"], tipo_entidade=info["tipo_entidade"], poder=info["poder"],
                          esfera=info["esfera"], job=info["job"], urls=urls, paginas=paginas, arquivos=arquivos)

    # ---- checklist
    def upsert_checklist(self, checklist):
        vid = self.conn.execute(
            """insert into monitor.checklist_version (nome, sha256) values (%s, %s)
               on conflict (nome) do update set sha256 = excluded.sha256 returning id""",
            (checklist.nome, checklist.sha256)).fetchone()[0]
        with self.conn.transaction():
            for c in checklist.criterios:
                c.id = str(self.conn.execute(
                    """insert into monitor.criterion (checklist_version_id, codigo, bloco, dimensao, matriz, texto,
                         fundamentacao, peso, essencial, aplica_a, verificador, params, onda, params_hash)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       on conflict (checklist_version_id, codigo) do update set bloco = excluded.bloco,
                         dimensao = excluded.dimensao, texto = excluded.texto, peso = excluded.peso,
                         essencial = excluded.essencial, aplica_a = excluded.aplica_a,
                         verificador = excluded.verificador, params = excluded.params, onda = excluded.onda,
                         params_hash = excluded.params_hash
                       returning id""",
                    (vid, c.codigo, c.bloco, c.dimensao, c.matriz, c.texto, c.fundamentacao, c.peso, c.essencial,
                     c.aplica_a, c.verificador, self._j(c.params), c.onda, c.params_hash)).fetchone()[0])
        return str(vid)

    # ---- embeddings
    def dimensao_embedding(self):
        r = self.conn.execute("""select atttypmod from pg_attribute
                                 where attrelid = 'monitor.chunk'::regclass and attname = 'embedding'""").fetchone()
        return int(r[0]) if r and r[0] and r[0] > 0 else None

    def snapshots_para_embedding(self, run_id, modelo):
        ps = self._dicts(
            """select p.id::text as id, p.coleta_status::text as coleta_status, p.titulo, p.secoes, p.texto
               from monitor.page_snapshot p join monitor.crawl_job j on j.id = p.crawl_job_id
               where j.run_id = %s and p.coleta_status = 'ok'
                 and not exists (select 1 from monitor.chunk c where c.page_snapshot_id = p.id and c.modelo = %s)""",
            (int(run_id), modelo))
        As = self._dicts(
            """select f.id::text as id, f.erro, f.sha256, left(f.texto_extraido, 20000) as texto_extraido, f.metadados,
                      f.texto_link, f.secoes, f.extensao
               from monitor.file_artifact f join monitor.page_snapshot p on p.id = f.page_snapshot_id
               join monitor.crawl_job j on j.id = p.crawl_job_id
               where j.run_id = %s and f.erro is null
                 and not exists (select 1 from monitor.chunk c where c.file_artifact_id = f.id and c.modelo = %s)""",
            (int(run_id), modelo))
        return ps, As

    def vetores_existentes(self, modelo, hashes):
        out: dict[str, list[float]] = {}
        for i in range(0, len(hashes), 500):
            for h, v in self.conn.execute(
                    """select distinct on (texto_hash) texto_hash::text, embedding::text from monitor.chunk
                       where modelo = %s and texto_hash::text = any(%s::text[])""", (modelo, hashes[i:i + 500])):
                out[h.strip()] = texto_para_vetor(v)
        return out

    def salvar_chunks(self, linhas):
        sql = """insert into monitor.chunk (page_snapshot_id, file_artifact_id, ordem, texto, modelo, texto_hash, embedding)
                 values (%s, %s, %s, %s, %s, %s, %s)"""
        # sem "::vector": o psycopg envia str como tipo desconhecido e o Postgres converte para a coluna,
        # funcione o pgvector em `public` (Supabase deste projeto) ou em `extensions` (CI).
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(sql, [(int(x["page_snapshot_id"]) if x["page_snapshot_id"] else None,
                                   int(x["file_artifact_id"]) if x["file_artifact_id"] else None,
                                   x["ordem"], _sem_nul(x["texto"]), x["modelo"], x["texto_hash"],
                                   vetor_para_texto(x["embedding"])) for x in linhas])

    def chunks_do_ente(self, run_id, slug, modelo):
        rows = self._dicts(
            """select c.texto, c.page_snapshot_id::text as page_snapshot_id, c.file_artifact_id::text as file_artifact_id,
                      coalesce(p.url, f.url) as url, c.embedding::text as embedding
               from monitor.chunk c
               left join monitor.page_snapshot p on p.id = c.page_snapshot_id
               left join monitor.file_artifact f on f.id = c.file_artifact_id
               left join monitor.page_snapshot pf on pf.id = f.page_snapshot_id
               join monitor.crawl_job j on j.id = coalesce(p.crawl_job_id, pf.crawl_job_id)
               join monitor.entity e on e.id = j.entity_id
               where j.run_id = %s and e.slug = %s and c.modelo = %s""", (int(run_id), slug, modelo))
        for r in rows:
            r["embedding"] = texto_para_vetor(r["embedding"])
        return rows

    def podar_chunks(self, manter_rodadas):
        """Apaga trechos de rodadas antigas (o vetor fica nas rodadas recentes; o reuso por hash continua)."""
        cur = self.conn.execute(
            """delete from monitor.chunk c using monitor.page_snapshot p, monitor.crawl_job j
               where c.page_snapshot_id = p.id and p.crawl_job_id = j.id
                 and j.run_id not in (select id from monitor.run order by id desc limit %s)""", (manter_rodadas,))
        n = cur.rowcount
        cur = self.conn.execute(
            """delete from monitor.chunk c using monitor.file_artifact f, monitor.page_snapshot p, monitor.crawl_job j
               where c.file_artifact_id = f.id and f.page_snapshot_id = p.id and p.crawl_job_id = j.id
                 and j.run_id not in (select id from monitor.run order by id desc limit %s)""", (manter_rodadas,))
        return n + cur.rowcount

    # ---- avaliação
    def tem_revisoes(self, run_id, slug):
        r = self.conn.execute(
            """select exists (select 1 from monitor.human_review h
                 join monitor.criterion_result cr on cr.id = h.criterion_result_id
                 join monitor.evaluation ev on ev.id = cr.evaluation_id
                 join monitor.entity e on e.id = ev.entity_id
                 where ev.run_id = %s and e.slug = %s)""", (int(run_id), slug)).fetchone()
        return bool(r[0])

    def salvar_avaliacao(self, run_id, slug, versao_id, aval, modelo):
        s = aval.score
        with self.conn.transaction():
            eid_ent = self.conn.execute("select id from monitor.entity where slug = %s", (slug,)).fetchone()[0]
            self.conn.execute("delete from monitor.evaluation where run_id = %s and entity_id = %s",
                              (int(run_id), eid_ent))
            ev_id = self.conn.execute(
                """insert into monitor.evaluation (run_id, entity_id, checklist_version_id, score, score_min, score_max,
                     cobertura, essenciais_ok, classificacao, scores_bloco, pesos_usados, engine_version, modelo_embedding)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (int(run_id), eid_ent, int(versao_id), s.score, s.score_min, s.score_max,
                 None if s.cobertura is None else round(100 * s.cobertura, 2), s.essenciais_ok, s.classificacao,
                 self._j(s.scores_bloco), self._j(s.pesos_usados), ENGINE_VERSION, modelo)).fetchone()[0]
            evid = []
            for c, r in aval.resultados:
                rid = self.conn.execute(
                    """insert into monitor.criterion_result (evaluation_id, criterion_id, status, nota, confianca, metodo,
                         checker_version, justificativa, sinais)
                       values (%s, %s, %s::monitor.status_criterio, %s, %s, %s::monitor.metodo_avaliacao, %s, %s, %s)
                       returning id""",
                    (ev_id, int(c.id), r.status, r.nota, r.confianca, r.metodo, aval.checker_version(c),
                     _sem_nul(r.justificativa), self._j(r.sinais))).fetchone()[0]
                for e in r.evidencias:
                    evid.append((rid, e.tipo, e.url, int(e.page_snapshot_id) if e.page_snapshot_id else None,
                                 int(e.file_artifact_id) if e.file_artifact_id else None, _sem_nul(e.trecho),
                                 e.screenshot_path, self._j(e.metadados)))
            if evid:
                with self.conn.cursor() as cur:
                    cur.executemany(
                        """insert into monitor.evidence (criterion_result_id, tipo, url, page_snapshot_id,
                             file_artifact_id, trecho, screenshot_path, metadados)
                           values (%s,%s,%s,%s,%s,%s,%s,%s)""", evid)
        return str(ev_id)

    def revisoes_para_calibracao(self):
        return self._dicts(
            """select cr.sinais, cr.status::text as status, h.status_corrigido::text as status_corrigido
               from monitor.human_review h join monitor.criterion_result cr on cr.id = h.criterion_result_id""")


def build_repo(s: Settings, local: bool) -> ConclusaoRepo:
    if local:
        return LocalConclusao(s.data_dir / "local")
    if not s.database_url:
        raise SystemExit("DATABASE_URL ausente no .env (ou use --local).")
    return PostgresConclusao(s.database_url)
