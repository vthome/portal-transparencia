"""Acesso ao Postgres (Supabase, schema `monitor`) para a API.

Pool de conexões psycopg: `autocommit` e sem prepared statements (compatível com o pooler do
Supabase nas portas 5432 e 6543). Todas as consultas ficam aqui; as rotas só montam a resposta.
"""
from __future__ import annotations

import logging
from typing import Any

from api.config import ApiSettings

log = logging.getLogger("api.db")

# colunas de evaluation expostas (numeric -> float8 para virar número no JSON)
_EV = """ev.id as avaliacao_id, ev.run_id, ev.score::float8 as score, ev.score_min::float8 as score_min,
         ev.score_max::float8 as score_max, ev.cobertura::float8 as cobertura, ev.essenciais_ok,
         ev.classificacao, ev.avaliado_em, ev.scores_bloco, ev.engine_version, ev.modelo_embedding"""

_ULTIMA_REVISAO = """left join lateral (
           select h.id as revisao_id, h.status_corrigido::text as status_revisado, h.revisor, h.comentario,
                  h.revisado_em
           from monitor.human_review h where h.criterion_result_id = cr.id
           order by h.revisado_em desc, h.id desc limit 1) hr on true"""


class Repo:
    """Consultas da API. Recebe um pool psycopg (ou qualquer objeto com `.connection()`)."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def _all(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self.pool.connection() as c:
            return c.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        with self.pool.connection() as c:
            return c.execute(sql, params).fetchone()

    # ------------------------------------------------------------ sistema
    def ping(self) -> dict[str, Any]:
        return self._one(
            """select now() as agora,
                      (select max(id) from monitor.run) as ultima_rodada,
                      (select count(*) from monitor.evaluation) as avaliacoes,
                      exists (select 1 from information_schema.columns where table_schema = 'monitor'
                              and table_name = 'criterion_result' and column_name = 'sinais') as schema_v03""")

    # ------------------------------------------------------------ entes
    def entes(self) -> list[dict[str, Any]]:
        return self._all(
            f"""select e.slug, e.nome, e.municipio, e.uf, e.cod_ibge, e.tipo_entidade, e.poder, e.esfera,
                       {_EV},
                       uc.run_id as coleta_run_id, uc.status::text as coleta_status, uc.paginas as coleta_paginas,
                       uc.arquivos as coleta_arquivos, uc.finalizado_em as coleta_em
                from monitor.entity e
                left join monitor.v_latest_evaluation ev on ev.entity_id = e.id
                left join monitor.v_ultima_coleta uc on uc.entity_id = e.id
                where e.ativo
                order by e.slug""")

    def urls(self, slug: str) -> list[dict[str, Any]]:
        return self._all(
            """select u.tipo, u.url, u.plataforma, u.fonte, u.dominio_gov, u.valido_de
               from monitor.entity_url u join monitor.entity e on e.id = u.entity_id
               where e.slug = %s and u.valido_ate is null order by u.tipo, u.url""", (slug,))

    # ------------------------------------------------------------ avaliações
    def avaliacao(self, slug: str, run_id: int | None = None) -> dict[str, Any] | None:
        """Avaliação do ente numa rodada (a mais recente se run_id for None)."""
        return self._one(
            f"""select e.slug, e.nome, {_EV}, r.iniciado_em as rodada_em
                from monitor.evaluation ev
                join monitor.entity e on e.id = ev.entity_id
                join monitor.run r on r.id = ev.run_id
                where e.slug = %(slug)s and (%(run)s::bigint is null or ev.run_id = %(run)s::bigint)
                order by ev.run_id desc, ev.avaliado_em desc limit 1""", {"slug": slug, "run": run_id})

    def historico(self, slug: str) -> list[dict[str, Any]]:
        return self._all(
            f"""select {_EV}, r.iniciado_em as rodada_em, r.gatilho
                from monitor.evaluation ev
                join monitor.entity e on e.id = ev.entity_id
                join monitor.run r on r.id = ev.run_id
                where e.slug = %s order by ev.run_id""", (slug,))

    def resultados(self, avaliacao_id: int) -> list[dict[str, Any]]:
        return self._all(
            f"""select cr.id, c.codigo, c.bloco, c.dimensao, c.matriz, c.texto, c.peso, c.essencial, c.verificador,
                       cr.status::text as status, cr.nota::float8 as nota, cr.confianca::float8 as confianca,
                       cr.metodo::text as metodo, cr.justificativa,
                       (select count(*) from monitor.evidence x where x.criterion_result_id = cr.id)::int
                         as n_evidencias,
                       hr.revisao_id, hr.status_revisado, hr.revisor, hr.revisado_em
                from monitor.criterion_result cr
                join monitor.criterion c on c.id = cr.criterion_id
                {_ULTIMA_REVISAO}
                where cr.evaluation_id = %s""", (avaliacao_id,))

    def timeline(self, slug: str, codigo: str) -> list[dict[str, Any]]:
        return self._all(
            f"""select ev.run_id, ev.avaliado_em, cr.id as resultado_id, cr.status::text as status,
                       cr.nota::float8 as nota, cr.confianca::float8 as confianca, cr.metodo::text as metodo,
                       cr.justificativa, hr.status_revisado
                from monitor.criterion_result cr
                join monitor.criterion c on c.id = cr.criterion_id
                join monitor.evaluation ev on ev.id = cr.evaluation_id
                join monitor.entity e on e.id = ev.entity_id
                {_ULTIMA_REVISAO}
                where e.slug = %s and c.codigo = %s
                order by ev.run_id""", (slug, codigo))

    def resultado(self, resultado_id: int) -> dict[str, Any] | None:
        return self._one(
            f"""select cr.id, cr.evaluation_id as avaliacao_id, ev.run_id, e.slug, e.nome,
                       c.codigo, c.bloco, c.dimensao, c.matriz, c.texto, c.fundamentacao, c.peso, c.essencial,
                       c.verificador, cr.status::text as status, cr.nota::float8 as nota,
                       cr.confianca::float8 as confianca, cr.metodo::text as metodo, cr.checker_version,
                       cr.justificativa, cr.sinais,
                       hr.revisao_id, hr.status_revisado, hr.revisor, hr.revisado_em
                from monitor.criterion_result cr
                join monitor.criterion c on c.id = cr.criterion_id
                join monitor.evaluation ev on ev.id = cr.evaluation_id
                join monitor.entity e on e.id = ev.entity_id
                {_ULTIMA_REVISAO}
                where cr.id = %s""", (resultado_id,))

    def evidencias(self, resultado_id: int) -> list[dict[str, Any]]:
        return self._all(
            """select x.id, x.tipo, x.url, x.page_snapshot_id, x.file_artifact_id, x.trecho, x.screenshot_path,
                      x.metadados, p.coletado_em as pagina_coletada_em, p.titulo as pagina_titulo,
                      f.extensao as arquivo_extensao,
                      (x.screenshot_path is not null or p.storage_path is not null or f.storage_path is not null)
                        as tem_copia
               from monitor.evidence x
               left join monitor.page_snapshot p on p.id = x.page_snapshot_id
               left join monitor.file_artifact f on f.id = x.file_artifact_id
               where x.criterion_result_id = %s order by x.id""", (resultado_id,))

    def copias_da_evidencia(self, evidencia_id: int) -> dict[str, Any] | None:
        return self._one(
            """select x.id, x.screenshot_path, coalesce(x.screenshot_path, p.screenshot_path) as screenshot,
                      p.storage_path as html, f.storage_path as arquivo, f.extensao
               from monitor.evidence x
               left join monitor.page_snapshot p on p.id = x.page_snapshot_id
               left join monitor.file_artifact f on f.id = x.file_artifact_id
               where x.id = %s""", (evidencia_id,))

    # ------------------------------------------------------------ revisão humana
    def fila(self, limiar: float, slug: str | None, incluir_nao_verificaveis: bool, limite: int) -> list[dict]:
        return self._all(
            """select cr.id, e.slug, ev.run_id, c.codigo, c.texto, c.dimensao, c.peso, c.essencial,
                      cr.status::text as status, cr.confianca::float8 as confianca, cr.metodo::text as metodo,
                      cr.justificativa
               from monitor.v_latest_evaluation ev
               join monitor.entity e on e.id = ev.entity_id
               join monitor.criterion_result cr on cr.evaluation_id = ev.id
               join monitor.criterion c on c.id = cr.criterion_id
               where (%(slug)s::text is null or e.slug = %(slug)s::text)
                 and not exists (select 1 from monitor.human_review h where h.criterion_result_id = cr.id)
                 and cr.metodo <> 'pendente'
                 and (cr.confianca < %(limiar)s
                      or (%(nv)s and cr.status = 'nao_verificavel'))
               order by c.essencial desc, c.peso desc, cr.confianca asc nulls first, e.slug, cr.id
               limit %(limite)s""",
            {"slug": slug, "limiar": limiar, "nv": incluir_nao_verificaveis, "limite": limite})

    def inserir_revisao(self, resultado_id: int, status: str, revisor: str, comentario: str | None) -> dict:
        return self._one(
            """insert into monitor.human_review (criterion_result_id, revisor, status_corrigido, comentario)
               values (%s, %s, %s::monitor.status_criterio, %s)
               returning id, criterion_result_id as resultado_id, revisor, status_corrigido::text as status_corrigido,
                         comentario, revisado_em""", (resultado_id, revisor, status, comentario))

    def revisoes_do_resultado(self, resultado_id: int) -> list[dict[str, Any]]:
        return self._all(
            """select id, criterion_result_id as resultado_id, revisor, status_corrigido::text as status_corrigido,
                      comentario, revisado_em
               from monitor.human_review where criterion_result_id = %s order by revisado_em, id""",
            (resultado_id,))

    def revisoes(self, slug: str | None, limite: int) -> list[dict[str, Any]]:
        return self._all(
            """select h.id, h.criterion_result_id as resultado_id, e.slug, ev.run_id, c.codigo,
                      cr.status::text as status_motor, h.status_corrigido::text as status_corrigido,
                      (h.status_corrigido = cr.status) as concordou, cr.confianca::float8 as confianca,
                      cr.metodo::text as metodo, c.verificador, h.revisor, h.comentario, h.revisado_em
               from monitor.human_review h
               join monitor.criterion_result cr on cr.id = h.criterion_result_id
               join monitor.criterion c on c.id = cr.criterion_id
               join monitor.evaluation ev on ev.id = cr.evaluation_id
               join monitor.entity e on e.id = ev.entity_id
               where (%(slug)s::text is null or e.slug = %(slug)s::text)
               order by h.revisado_em desc, h.id desc limit %(limite)s""", {"slug": slug, "limite": limite})

    # ------------------------------------------------------------ monitoramento
    def rodadas(self, limite: int) -> list[dict[str, Any]]:
        return self._all(
            """select s.run_id, s.gatilho, s.status::text as status, s.iniciado_em, s.finalizado_em, s.entes,
                      s.concluidos, s.parciais, s.falhas, s.paginas::bigint as paginas,
                      s.arquivos::bigint as arquivos, s.bytes::bigint as bytes,
                      (select count(*) from monitor.evaluation ev where ev.run_id = s.run_id) as avaliacoes
               from monitor.v_saude_rodadas s order by s.run_id desc limit %s""", (limite,))

    def rodada(self, run_id: int) -> list[dict[str, Any]]:
        return self._all(
            """select e.slug, j.status::text as status, j.tentativas, j.erro, j.paginas, j.arquivos, j.bytes,
                      j.segundos::float8 as segundos, j.plataforma_detectada, j.requisicoes, j.renderizacoes,
                      j.paginas_inalteradas, j.bloqueios_robots, j.secoes_encontradas, j.secoes_ausentes,
                      j.iniciado_em, j.finalizado_em
               from monitor.crawl_job j join monitor.entity e on e.id = j.entity_id
               where j.run_id = %s order by e.slug""", (run_id,))

    def checklist(self) -> list[dict[str, Any]]:
        return self._all(
            """select v.nome as versao, c.codigo, c.bloco, c.dimensao, c.matriz, c.texto, c.fundamentacao, c.peso,
                      c.essencial, c.aplica_a, c.verificador, c.onda
               from monitor.criterion c join monitor.checklist_version v on v.id = c.checklist_version_id
               where v.id = (select max(id) from monitor.checklist_version)""")

    # ------------------------------------------------------------ busca
    _BUSCA_BASE = """
        from monitor.chunk c
        left join monitor.page_snapshot p on p.id = c.page_snapshot_id
        left join monitor.file_artifact f on f.id = c.file_artifact_id
        left join monitor.page_snapshot pf on pf.id = f.page_snapshot_id
        join monitor.crawl_job j on j.id = coalesce(p.crawl_job_id, pf.crawl_job_id)
        join monitor.v_ultima_coleta u on u.entity_id = j.entity_id and u.run_id = j.run_id
        join monitor.entity e on e.id = j.entity_id"""
    _BUSCA_COLS = """c.id as trecho_id, e.slug, j.run_id, c.page_snapshot_id, c.file_artifact_id,
                     coalesce(p.url, f.url) as url, coalesce(p.titulo, f.texto_link) as titulo, c.texto"""

    # Configuração de texto sem acento (schema v0.5). Se o banco ainda estiver na v0.4, a busca
    # volta para 'portuguese' (com acento) em vez de falhar — o deploy da API pode chegar antes do SQL.
    TS_SEM_ACENTO = "monitor.pt_sem_acento"
    TS_LEGADO = "portuguese"
    # Similaridade mínima da busca aproximada (0–1): "orcameto" × "orçamento" ≈ 0,7.
    LIMIAR_APROXIMADA = 0.5

    def busca_texto(self, q: str, slug: str | None, k: int) -> list[dict[str, Any]]:
        """Full-text em português, sem acento e sem diferenciar maiúsculas, sobre os trechos da
        coleta mais recente de cada ente: "orcamento" encontra "Orçamento"."""
        from psycopg import errors

        try:
            return self._busca_fts(self.TS_SEM_ACENTO, q, slug, k)
        except errors.UndefinedObject:      # schema sem a v0.5 (configuração pt_sem_acento ausente)
            log.warning("busca: monitor.pt_sem_acento não existe; rode o sql/schema.sql (v0.5)")
            return self._busca_fts(self.TS_LEGADO, q, slug, k)

    def _busca_fts(self, cfg: str, q: str, slug: str | None, k: int) -> list[dict[str, Any]]:
        # `cfg` é uma das constantes acima (nunca vem do usuário) e entra como literal no SQL:
        # assim a expressão casa exatamente com a do índice ix_chunk_fts_sem_acento.
        assert cfg in (self.TS_SEM_ACENTO, self.TS_LEGADO)
        tsv = f"to_tsvector('{cfg}'::regconfig, c.texto)"
        tsq = f"websearch_to_tsquery('{cfg}'::regconfig, %(q)s)"
        return self._all(
            f"""select {self._BUSCA_COLS}, ts_rank({tsv}, {tsq})::float8 as relevancia
                {self._BUSCA_BASE}
                where {tsv} @@ {tsq}
                  and (%(slug)s::text is null or e.slug = %(slug)s::text)
                order by relevancia desc, c.id limit %(k)s""", {"q": q, "slug": slug, "k": k})

    def busca_aproximada(self, q: str, slug: str | None, k: int) -> list[dict[str, Any]]:
        """Tolerante a erros de digitação ("orcameto", "licitacao" com letra trocada): similaridade de
        trigramas (pg_trgm) entre a consulta e cada trecho, ambos sem acento e em minúsculas.
        Chamada só quando a busca exata não acha nada. Sem a v0.5 do schema, devolve lista vazia."""
        from psycopg import errors

        try:
            with self.pool.connection() as conn, conn.transaction():
                # pg_trgm fica no schema extensions (Supabase) — mesmo cuidado de busca_vetor
                conn.execute("set local search_path = monitor, public, extensions")
                conn.execute(f"set local pg_trgm.word_similarity_threshold = {float(self.LIMIAR_APROXIMADA)}")
                return conn.execute(
                    f"""select {self._BUSCA_COLS},
                               word_similarity(monitor.sem_acento(%(q)s), monitor.sem_acento(c.texto))::float8
                                 as relevancia
                        {self._BUSCA_BASE}
                        where (%(slug)s::text is null or e.slug = %(slug)s::text)
                          and monitor.sem_acento(%(q)s) <%% monitor.sem_acento(c.texto)
                        order by relevancia desc, c.id limit %(k)s""",
                    {"q": q, "slug": slug, "k": k}).fetchall()
        except errors.UndefinedFunction:    # schema sem a v0.5 (função sem_acento ausente)
            log.warning("busca aproximada indisponível; rode o sql/schema.sql (v0.5)")
            return []

    def busca_vetor(self, vetor: str, modelo: str, slug: str | None, k: int) -> list[dict[str, Any]]:
        """Similaridade de cosseno (pgvector). `search_path` local: o pgvector pode estar em public ou extensions."""
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("set local search_path = monitor, public, extensions")
            return conn.execute(
                f"""select {self._BUSCA_COLS}, (1 - (c.embedding <=> %(v)s::vector))::float8 as relevancia
                    {self._BUSCA_BASE}
                    where c.modelo = %(modelo)s and (%(slug)s::text is null or e.slug = %(slug)s::text)
                    order by c.embedding <=> %(v)s::vector limit %(k)s""",
                {"v": vetor, "modelo": modelo, "slug": slug, "k": k}).fetchall()


def criar_pool(s: ApiSettings):
    """Pool psycopg aberto no startup da aplicação (lifespan)."""
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    timeout = int(s.statement_timeout_ms)

    def configurar(conn) -> None:
        conn.execute(f"set statement_timeout = {timeout}")
        conn.execute("set application_name = 'api'")

    return ConnectionPool(
        s.database_url, min_size=1, max_size=s.pool_max, open=False, configure=configurar,
        check=ConnectionPool.check_connection,      # descarta conexões mortas (ex.: após o Render hibernar)
        kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row})
