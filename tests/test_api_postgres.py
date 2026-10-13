"""API (Camada 3) contra Postgres + pgvector reais, com dados das Camadas 1 e 2.

Roda só com TEST_DATABASE_URL (no CI, o container pgvector/pgvector:pg16). NÃO aponte para o
Supabase: o teste apaga e recria o schema `monitor`.

Cenário: duas rodadas de coleta do site falso; a 1ª avaliada como se fosse em 2030 (prazos de
atualização vencidos), a 2ª em 12/10/2026 — assim o diff entre rodadas tem mudanças reais.
"""
import os
from datetime import date
from pathlib import Path

import pytest

from tests.helpers import make_http, test_settings
from tests.test_crawler_pipeline import ente, montar_site

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL não definida")]

SCHEMA = (Path(__file__).parent.parent / "sql" / "schema.sql").read_text(encoding="utf-8")
SLUG = "rj-api-teste"
TOKEN = "segredo-de-teste"


class AssinadorFalso:
    expira = 60

    def assinar(self, chave):
        return f"https://storage.exemplo/assinado/{chave}?token=x"


@pytest.fixture(scope="module")
def dados(tmp_path_factory):
    import psycopg

    from compliance.pipeline import executar
    from compliance.repo import PostgresConclusao
    from ingestion.embeddings.generate_embeddings import HashEmbedder
    from ingestion.load.load_postgres import PostgresRepository
    from ingestion.load.storage import LocalStorage
    from ingestion.pipeline import run

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("drop schema if exists monitor cascade")
        c.execute(SCHEMA)
    tmp = tmp_path_factory.mktemp("api")
    s = test_settings(data_dir=tmp)
    repo = PostgresRepository(DSN)
    runs = []
    for _ in range(2):
        r = run([ente(id=SLUG)], repo, LocalStorage(tmp / "o"), s,
                http_factory=lambda: make_http(montar_site('"v1"'), s))
        runs.append(r["run_id"])
    conc = PostgresConclusao(DSN)
    emb = HashEmbedder(768)
    executar(conc, runs[0], s, emb, data_ref=date(2030, 6, 30))
    executar(conc, runs[1], s, emb, data_ref=date(2026, 10, 12))
    return [int(x) for x in runs]


def _app(**kw):
    from api.config import ApiSettings
    from api.main import create_app

    return create_app(ApiSettings(database_url=DSN, token_revisao=TOKEN, busca="hash", **kw))


@pytest.fixture(scope="module")
def cli(dados):
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as c:
        app.state.assinador = AssinadorFalso()
        yield c


def test_health_e_swagger(cli, dados):
    h = cli.get("/health").json()
    assert h["status"] == "ok" and h["schema_v03"] is True and h["ultima_rodada"] == dados[1]
    paths = cli.get("/openapi.json").json()["paths"]
    assert {"/entes", "/revisoes", "/busca", "/entes/{slug}/diff"} <= set(paths)
    assert cli.get("/docs").status_code == 200


def test_entes_e_detalhe(cli, dados):
    es = cli.get("/entes").json()
    e = next(x for x in es if x["slug"] == SLUG)
    av = e["avaliacao"]
    assert av["run_id"] == dados[1] and 0 <= av["score_min"] <= av["score"] <= av["score_max"] <= 100
    assert sum(av["contagem"].values()) == 142 and set(av["dimensoes"]) == {
        "institucional_acesso", "receita_despesa", "pessoal", "licitacoes_contratos_obras", "planejamento_contas"}
    assert av["modelo_embedding"] == "hash-bow-768" and e["coleta"]["run_id"] == dados[1]
    d = cli.get(f"/entes/{SLUG}").json()
    assert d["urls"] and d["revisoes"] == 0
    assert d["score_revisado"]["score"] == av["score"]            # sem revisão: mesmo cálculo da Camada 2
    assert cli.get("/entes/nao-existe").status_code == 404


def test_indicadores(cli):
    rk = cli.get("/indicadores/ranking").json()
    assert rk[0]["slug"] == SLUG and (rk[0]["posicao"] == 1) == rk[0]["no_ranking"]
    assert cli.get("/indicadores/ranking", params={"dimensao": "pessoal"}).json()[0]["valor"] is not None
    assert cli.get("/indicadores/ranking", params={"dimensao": "xpto"}).status_code == 422
    m = cli.get("/indicadores/dimensoes").json()
    assert len(m["dimensoes"]) == 5 and m["linhas"][0]["slug"] == SLUG
    st = cli.get("/indicadores/status").json()[0]
    assert st["slug"] == SLUG and st.get("atende", 0) > 0


def test_historico_resultados_diff_timeline(cli, dados):
    h = cli.get(f"/entes/{SLUG}/historico").json()
    assert [x["run_id"] for x in h] == dados
    rs = cli.get(f"/entes/{SLUG}/resultados").json()
    assert len(rs) == 142 and rs[0]["codigo"] == "1.1"
    assert all(r["status_final"] == "atende" for r in cli.get(f"/entes/{SLUG}/resultados",
                                                               params={"status": "atende"}).json())
    antigos = cli.get(f"/entes/{SLUG}/resultados", params={"run_id": dados[0]}).json()
    assert len(antigos) == 142 and {r["id"] for r in antigos}.isdisjoint({r["id"] for r in rs})
    assert cli.get(f"/entes/{SLUG}/resultados", params={"run_id": 999999}).status_code == 404

    d = cli.get(f"/entes/{SLUG}/diff").json()
    assert d["de"]["run_id"] == dados[0] and d["para"]["run_id"] == dados[1]
    assert sum(d["resumo"].values()) == 142
    assert d["mudancas"] and all(m["evento"] != "estavel" for m in d["mudancas"])
    assert d["resumo"]["melhorou"] > 0                       # prazos vencidos em 2030 voltam a valer em 2026
    assert d["delta_score"] == round(d["para"]["score"] - d["de"]["score"], 2)

    cod = next(m["codigo"] for m in d["mudancas"] if m["evento"] == "melhorou")
    tl = cli.get(f"/entes/{SLUG}/quesitos/{cod}/timeline").json()
    assert [x["run_id"] for x in tl] == dados and tl[0]["evento"] == "primeira" and tl[1]["evento"] == "melhorou"
    assert cli.get(f"/entes/{SLUG}/quesitos/99.99/timeline").status_code == 404


def test_detalhe_do_resultado_e_link_da_evidencia(cli):
    rs = cli.get(f"/entes/{SLUG}/resultados").json()
    com_ev = next(r for r in rs if r["n_evidencias"] > 0)
    det = cli.get(f"/resultados/{com_ev['id']}").json()
    assert det["codigo"] == com_ev["codigo"] and det["evidencias"] and isinstance(det["sinais"], dict)
    assert det["checker_version"] and det["fundamentacao"] is not None
    assert cli.get("/resultados/99999999").status_code == 404

    arquivo = None
    for r in rs:
        if not r["n_evidencias"]:
            continue
        for ev in cli.get(f"/resultados/{r['id']}").json()["evidencias"]:
            if ev["file_artifact_id"] and ev["tem_copia"]:
                arquivo = ev
                break
        if arquivo:
            break
    assert arquivo is not None, "o site falso tem PDF/CSV baixados; alguma evidência deve apontar para eles"
    link = cli.get(f"/evidencias/{arquivo['id']}/arquivo").json()
    assert link["tipo"] == "arquivo" and link["url"].startswith("https://storage.exemplo/assinado/")
    assert cli.get(f"/evidencias/{arquivo['id']}/arquivo", params={"tipo": "screenshot"}).status_code == 404
    assert cli.get("/evidencias/99999999/arquivo").status_code == 404


def test_fila_e_revisao_humana(cli):
    fila = cli.get("/revisoes/fila", params={"ente": SLUG}).json()
    assert fila and all((x["confianca"] is not None and x["confianca"] < 0.6) or x["status"] == "nao_verificavel"
                        for x in fila)
    assert all(x["metodo"] != "pendente" for x in fila)
    alvo = next(x for x in fila if x["status"] in {"nao_atende", "nao_verificavel"})
    corpo = {"resultado_id": alvo["id"], "status_corrigido": "atende", "revisor": "Revisor Teste",
             "comentario": "conferido no portal"}

    assert cli.post("/revisoes", json=corpo).status_code == 401
    assert cli.post("/revisoes", json=corpo, headers={"X-API-Key": "errada"}).status_code == 401
    h = {"X-API-Key": TOKEN}
    assert cli.post("/revisoes", json={**corpo, "status_corrigido": "talvez"}, headers=h).status_code == 422
    assert cli.post("/revisoes", json={**corpo, "resultado_id": 99999999}, headers=h).status_code == 404

    antes = cli.get(f"/entes/{SLUG}").json()
    r = cli.post("/revisoes", json=corpo, headers=h)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["revisao"]["status_corrigido"] == "atende" and out["concordou_com_motor"] is False
    assert out["score_revisado"]["score"] >= antes["avaliacao"]["score"]
    if alvo["status"] == "nao_verificavel":                  # passou a contar: cobertura sobe
        assert out["score_revisado"]["cobertura"] > antes["score_revisado"]["cobertura"]

    det = cli.get(f"/resultados/{alvo['id']}").json()
    assert det["status_final"] == "atende" and det["status"] == alvo["status"] and len(det["revisoes"]) == 1
    assert alvo["id"] not in {x["id"] for x in cli.get("/revisoes/fila", params={"ente": SLUG}).json()}
    depois = cli.get(f"/entes/{SLUG}").json()
    assert depois["revisoes"] == 1 and depois["avaliacao"]["score"] == antes["avaliacao"]["score"]   # motor intacto
    revs = cli.get("/revisoes").json()
    assert revs[0]["resultado_id"] == alvo["id"] and revs[0]["concordou"] is False
    conc = cli.get("/revisoes/concordancia").json()
    assert conc["revisoes"] == 1 and conc["concordancia"] == 0 and not conc["pronto_para_calibrar"]
    rs = cli.get(f"/entes/{SLUG}/resultados", params={"precisa_revisao": True}).json()
    assert alvo["id"] not in {x["id"] for x in rs}


def test_busca_semantica_e_texto(cli):
    sem = cli.get("/busca", params={"q": "despesas empenho pagamento", "k": 5}).json()
    assert sem["modo"] == "semantica" and sem["modelo"] == "hash-bow-768" and 0 < len(sem["resultados"]) <= 5
    rel = [x["relevancia"] for x in sem["resultados"]]
    assert rel == sorted(rel, reverse=True) and all(x["slug"] == SLUG for x in sem["resultados"])
    txt = cli.get("/busca", params={"q": "despesas", "modo": "texto", "ente": SLUG}).json()
    assert txt["modo"] == "texto" and txt["resultados"] and "espesa" in txt["resultados"][0]["texto"]
    assert cli.get("/busca", params={"q": "ab"}).status_code == 422
    assert cli.get("/busca", params={"q": "zzzqqqxxx", "modo": "texto"}).json()["resultados"] == []


def test_monitoramento_e_checklist(cli, dados):
    rod = cli.get("/rodadas").json()
    assert [x["run_id"] for x in rod] == dados[::-1] and all(x["avaliacoes"] == 1 for x in rod)
    jobs = cli.get(f"/rodadas/{dados[1]}").json()
    assert jobs[0]["slug"] == SLUG and jobs[0]["paginas"] > 0
    assert cli.get("/rodadas/999999").status_code == 404
    ck = cli.get("/checklist").json()
    assert len(ck) == 142 and ck[0]["codigo"] == "1.1" and ck[-1]["codigo"].startswith("21.")
    assert all(c["verificador"] == "freshness" for c in cli.get("/checklist", params={"verificador": "freshness"}).json())


def test_token_de_leitura(dados):
    from fastapi.testclient import TestClient

    with TestClient(_app(token_leitura="leitor")) as c:
        assert c.get("/health").status_code == 200                     # health fica aberto (Render)
        assert c.get("/entes").status_code == 401
        assert c.get("/entes", headers={"X-API-Key": "leitor"}).status_code == 200
        assert c.get("/entes", headers={"X-API-Key": TOKEN}).status_code == 200   # revisor também lê


def test_usuario_restrito_da_api(dados):
    """sql/api_role.sql: a API funciona com o usuário monitor_api, que só grava revisões."""
    import psycopg
    from fastapi.testclient import TestClient
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    sql = (Path(__file__).parent.parent / "sql" / "api_role.sql").read_text(encoding="utf-8")
    senha = "senha-de-teste-bem-longa-123"
    with psycopg.connect(DSN, autocommit=True) as c:
        existe = c.execute("select 1 from pg_roles where rolname = 'monitor_api'").fetchone()
        if existe:                                   # papel é do cluster: pode sobrar de outro banco de teste
            c.execute(f"alter role monitor_api password '{senha}'")
        else:
            with pytest.raises(psycopg.errors.RaiseException, match="Defina uma senha"):
                c.execute(sql)                       # placeholder é recusado na criação
            c.execute(sql.replace("senha text := 'TROQUE_ESTA_SENHA'", f"senha text := '{senha}'"))
        c.execute(sql)                               # rodar de novo como está no repositório: mantém a senha
    params = conninfo_to_dict(DSN)
    params.update(user="monitor_api", password=senha)
    dsn_api = make_conninfo(**params)

    from api.config import ApiSettings
    from api.main import create_app

    with TestClient(create_app(ApiSettings(database_url=dsn_api, token_revisao=TOKEN, busca="hash"))) as cli:
        assert cli.get("/health").json()["status"] == "ok"
        rs = cli.get(f"/entes/{SLUG}/resultados").json()
        assert len(rs) == 142
        assert cli.get("/busca", params={"q": "despesas"}).json()["resultados"]            # vector + <=>
        assert cli.get("/busca", params={"q": "despesas", "modo": "texto"}).status_code == 200
        r = cli.post("/revisoes", json={"resultado_id": rs[2]["id"], "status_corrigido": "parcial",
                                        "revisor": "Usuário API"}, headers={"X-API-Key": TOKEN})
        assert r.status_code == 201, r.text
    with psycopg.connect(dsn_api, autocommit=True) as k:
        for proibido in ("delete from monitor.evaluation", "update monitor.criterion_result set status = 'atende'",
                         "delete from monitor.human_review", "insert into monitor.run (gatilho, engine_version) "
                                                             "values ('x', 'y')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                k.execute(proibido)
