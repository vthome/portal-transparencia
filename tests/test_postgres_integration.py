"""Integração com Postgres real: sql/schema.sql (schema `monitor`) + PostgresRepository.

Roda só quando TEST_DATABASE_URL está definida — no CI, um container
pgvector/pgvector:pg16 efêmero (ver .github/workflows/ci.yml).
NÃO aponte para o banco do Supabase: o teste apaga e recria o schema `monitor`.

    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest -m integration
"""
import os
from pathlib import Path

import pytest

from tests.helpers import make_http, test_settings
from tests.test_crawler_pipeline import ente, montar_site

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL não definida")]

RAIZ = Path(__file__).parent.parent
V02 = (RAIZ / "sql" / "schema.sql").read_text(encoding="utf-8")


def _v01_portavel() -> str:
    """schema v0.1 (o que foi aplicado no Supabase) sem os trechos exclusivos do Supabase."""
    sql = (Path(__file__).parent / "fixtures" / "schema_v01.sql").read_text(encoding="utf-8")
    sql = sql.replace("revoke all on schema monitor from anon, authenticated;", "")
    sql = sql.split("-- ---------- 8. Storage")[0]
    return "create schema if not exists extensions;\n" + sql


def _colunas(c, tabela):
    return {r[0] for r in c.execute(
        "select column_name from information_schema.columns where table_schema='monitor' and table_name=%s",
        (tabela,))}


@pytest.fixture(scope="module")
def repo():
    import psycopg

    from ingestion.load.load_postgres import PostgresRepository

    with psycopg.connect(DSN, autocommit=True) as c:
        # 1) instalação limpa da v0.2, duas vezes (idempotência)
        c.execute("drop schema if exists monitor cascade")
        c.execute(V02)
        c.execute(V02)
        assert {"coleta_status", "trilha", "secoes", "extraido"} <= _colunas(c, "page_snapshot")
        # 2) caminho real do Supabase: v0.1 já aplicada, depois v0.2 por cima
        c.execute("drop schema monitor cascade")
        c.execute(_v01_portavel())
        c.execute(V02)
        assert {"coleta_status", "trilha", "secoes", "extraido", "inalterado"} <= _colunas(c, "page_snapshot")
        assert {"entity_id", "secoes", "texto_link"} <= _colunas(c, "file_artifact")
        assert {"bloqueios_robots", "secoes_ausentes"} <= _colunas(c, "crawl_job")
    return PostgresRepository(DSN)


def test_pipeline_grava_no_schema_monitor(repo, tmp_path):
    from ingestion.load.storage import LocalStorage
    from ingestion.pipeline import run

    settings = test_settings(data_dir=tmp_path)
    e = ente(id="rj-teste-integracao")
    resumo = run([e], repo, LocalStorage(tmp_path / "o"), settings,
                 http_factory=lambda: make_http(montar_site('"v1"'), settings))
    rid = int(resumo["run_id"])
    c = repo.conn
    eid = c.execute("select id from monitor.entity where slug=%s", (e.id,)).fetchone()[0]

    assert c.execute("select status::text from monitor.run where id=%s", (rid,)).fetchone()[0] == "parcial"
    job = c.execute("""select status::text, paginas, arquivos, secoes_encontradas, tentativas, segundos
                       from monitor.crawl_job where run_id=%s""", (rid,)).fetchone()
    assert job[0] == "parcial" and job[1] > 5 and job[2] >= 2 and "despesa" in job[3]
    assert job[4] == 1 and job[5] is not None

    n = c.execute("""select count(*) from monitor.page_snapshot p join monitor.crawl_job j on j.id=p.crawl_job_id
                     where j.run_id=%s and p.coleta_status='bloqueado_robots'""", (rid,)).fetchone()[0]
    assert n == 1
    trilha, texto, links, extraido = c.execute(
        """select trilha, texto, links, extraido from monitor.page_snapshot p
           join monitor.crawl_job j on j.id=p.crawl_job_id
           where j.run_id=%s and p.url like '%%/despesas'""", (rid,)).fetchone()
    assert len(trilha) == 3
    assert "Despesas" in texto and links and "texto" not in extraido and "links" not in extraido
    assert extraido["tabelas"][0]["cabecalhos_norm"] == ["empenho", "valor pago"]

    tipos = dict(c.execute("""select tipo, count(*) from monitor.entity_url
                              where entity_id=%s and valido_ate is null group by tipo""", (eid,)))
    assert tipos.get("site") == 1 and tipos.get("portal") == 1 and "esic" in tipos   # mapeamento YAML -> banco
    vig = sum(tipos.values())

    # 2ª rodada: PDF via 304 reaproveita sha256; URLs vigentes não duplicam
    resumo2 = run([e], repo, LocalStorage(tmp_path / "o"), settings,
                  http_factory=lambda: make_http(montar_site('"v1"'), settings))
    assert c.execute("select count(*) from monitor.entity_url where entity_id=%s and valido_ate is null",
                     (eid,)).fetchone()[0] == vig
    meta = c.execute("""select f.metadados from monitor.file_artifact f
                        join monitor.page_snapshot p on p.id=f.page_snapshot_id
                        join monitor.crawl_job j on j.id=p.crawl_job_id
                        where j.run_id=%s and f.url like '%%.pdf'""", (int(resumo2["run_id"]),)).fetchone()[0]
    assert meta.get("nao_modificado") is True
    ult = c.execute("select entity_slug, run_id from monitor.v_ultima_coleta where entity_id=%s", (eid,)).fetchone()
    assert ult == (e.id, int(resumo2["run_id"]))
    saude = c.execute("select entes, parciais from monitor.v_saude_rodadas where run_id=%s", (rid,)).fetchone()
    assert saude == (1, 1)
