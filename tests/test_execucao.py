"""Ferramentas de execução da Camada 1: preflight, relatório, reextração, backends."""
from dataclasses import replace

import pytest
import requests

from ingestion.load.load_postgres import LocalRepository
from ingestion.load.storage import LocalStorage
from ingestion.pipeline import ConfigAusente, build_backends, run
from ingestion.preflight import checar_ambiente, checar_portais, checar_storage
from ingestion.reextract import reextrair
from ingestion.transform.extracao import PARSER_VERSION
from tests.helpers import FakeSite, make_http, page, test_settings
from tests.test_crawler_pipeline import PORTAL, ente, montar_site


def _rodada(tmp_path):
    settings = test_settings(data_dir=tmp_path)
    repo, storage = LocalRepository(tmp_path / "local"), LocalStorage(tmp_path / "objetos")
    resumo = run([ente()], repo, storage, settings, http_factory=lambda: make_http(montar_site(), settings))
    return settings, repo, storage, resumo


# ------------------------------------------------------------------ backends

def test_sem_supabase_falha_em_vez_de_cair_no_local(tmp_path):
    s = test_settings(data_dir=tmp_path, database_url=None, supabase_url=None, supabase_secret_key=None)
    with pytest.raises(ConfigAusente, match="DATABASE_URL"):
        build_backends(s, local=False)
    repo, storage = build_backends(s, local=True)
    assert isinstance(repo, LocalRepository) and isinstance(storage, LocalStorage)


# ------------------------------------------------------------------ relatório

def test_pipeline_gera_relatorio_com_criterios(tmp_path):
    _, _, _, resumo = _rodada(tmp_path)
    rel = (tmp_path / "runs" / f"{resumo['run_id']}-relatorio.md").read_text(encoding="utf-8")
    assert resumo["relatorio"].endswith("-relatorio.md")
    assert "| rj-exemplo | parcial |" in rel and "10 / 1 / 0 / 1 / 1" in rel   # ok/vazio/soft404/erro/robots
    assert "## Critérios de aceite" in rel and "[x] 1. todo ente tem job na rodada" in rel
    assert "http_404" in rel                       # link quebrado aparece nos erros


# ------------------------------------------------------------------ reextração

def test_reextracao_so_refaz_versao_antiga_e_recalcula_secoes(tmp_path):
    settings, repo, storage, resumo = _rodada(tmp_path)
    rid = resumo["run_id"]
    assert reextrair(repo, storage, rid)["avaliados"] == 0          # tudo já na versão atual

    # simula um snapshot extraído por um parser antigo, com seções erradas
    snap = next(p for p in repo.read("page_snapshot") if p["url"] == f"{PORTAL}/despesas")
    velho = {**snap["extraido"], "parser_version": PARSER_VERSION - 1}
    repo.atualizar_extracao(snap["id"], "ok", "x", ["obras"], velho)
    job = repo.run_jobs(rid)[0]
    repo.atualizar_secoes_job(job["job_id"], ["obras"], [])

    r = reextrair(repo, storage, rid)
    assert r["avaliados"] == 1 and r["atualizados"] == 1 and r["falhas"] == 0
    novo = next(p for p in repo.read("page_snapshot") if p["id"] == snap["id"])
    assert novo["titulo"] == "Despesas" and "despesa" in novo["secoes"]
    assert novo["extraido"]["parser_version"] == PARSER_VERSION
    assert novo["extraido"]["texto_link"] == "Despesas"             # texto do link preservado
    assert novo["content_hash"] == snap["content_hash"]             # evidência original intacta
    job = repo.run_jobs(rid)[0]
    assert "despesa" in job["secoes_encontradas"] and "obras" not in job["secoes_encontradas"]
    assert set(job["secoes_encontradas"]) | set(job["secoes_ausentes"]) >= {"despesa", "obras"}


def test_reextracao_sem_objeto_no_storage_mantem_extracao_antiga(tmp_path):
    settings, repo, _, resumo = _rodada(tmp_path)
    r = reextrair(repo, LocalStorage(tmp_path / "vazio"), resumo["run_id"], todos=True)
    assert r["atualizados"] == 0 and r["falhas"] > 0
    job = repo.run_jobs(resumo["run_id"])[0]
    assert "despesa" in job["secoes_encontradas"]                   # não apagou seções por falta do HTML


# ------------------------------------------------------------------ preflight

def test_preflight_ambiente_detecta_chave_publicavel():
    s = test_settings(database_url="postgresql://u:p@aws-0-sa-east-1.pooler.supabase.com:6543/postgres",
                      supabase_url="https://x.supabase.co", supabase_secret_key="sb_publishable_abc")
    checks = {c.nome: c for c in checar_ambiente(s)}
    assert not checks["SUPABASE_SECRET_KEY"].ok and "PUBLICÁVEL" in checks["SUPABASE_SECRET_KEY"].detalhe
    assert "transaction pooler" in checks["DATABASE_URL"].detalhe
    ok = {c.nome: c for c in checar_ambiente(replace(s, supabase_secret_key="sb_secret_abcdefghijklmnop"))}
    assert ok["SUPABASE_SECRET_KEY"].ok and "nova" in ok["SUPABASE_SECRET_KEY"].detalhe
    assert "abcdefghijklmnop" not in ok["SUPABASE_SECRET_KEY"].detalhe   # chave mascarada


def test_preflight_portais_diagnostica_robots_tls_e_redirecionamento():
    site = FakeSite()
    site.robots("https://bloq.rj.gov.br", "User-agent: *\nDisallow: /")
    site.html("https://bloq.rj.gov.br/", page("x", ""))
    site.handler("https://tls.rj.gov.br/robots.txt", lambda r: requests.exceptions.SSLError("cert"))
    site.handler("https://tls.rj.gov.br/", lambda r: requests.exceptions.SSLError("certificate verify failed"))
    site.html("https://ok.rj.gov.br/", page("ok", ""))
    entes = [ente(id="rj-bloq", urls={"site_oficial": "https://bloq.rj.gov.br/"}),
             ente(id="rj-tls", urls={"site_oficial": "https://tls.rj.gov.br/"}),
             ente(id="rj-ok", urls={"site_oficial": "https://ok.rj.gov.br/"})]
    linhas = {ln["ente"]: ln for ln in checar_portais(entes, make_http(site, test_settings(tentativas=1)))}
    assert linhas["rj-bloq"]["robots"] == "bloqueia" and "robots" in linhas["rj-bloq"]["sugestao"]
    assert linhas["rj-tls"]["erro"] == "tls" and "verificar_tls" in linhas["rj-tls"]["sugestao"]
    assert linhas["rj-ok"]["http"] == 200 and linhas["rj-ok"]["sugestao"] == ""


def test_preflight_storage_grava_le_e_apaga():
    chamadas = []

    class Bucket(FakeSite):
        def send(self, request, **kw):
            chamadas.append(request.method)
            r = requests.Response()
            r.status_code, r._content, r.url = 200, (b"preflight" if request.method == "GET" else b"{}"), request.url
            return r

    sess = requests.Session()
    sess.mount("https://", Bucket())
    s = test_settings(supabase_url="https://x.supabase.co", supabase_secret_key="sb_secret_x")
    [c] = checar_storage(s, session=sess)
    assert c.ok and chamadas == ["POST", "GET", "DELETE"]
