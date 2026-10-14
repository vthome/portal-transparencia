"""API (Camada 3) offline: regras de apresentação, autenticação, validação e rotas com repositório falso.

O SQL de verdade é testado em tests/test_api_postgres.py (Postgres + pgvector, no CI).
"""
from datetime import date, datetime, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api import servicos  # noqa: E402
from api.config import ApiSettings  # noqa: E402
from api.main import create_app  # noqa: E402

AGORA = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


def cfg(**kw) -> ApiSettings:
    """Configuração isolada: ignora o .env de quem roda os testes (tokens, Supabase, busca)."""
    base = dict(database_url=None, token_revisao=None, token_leitura=None, cors_origins=[], busca="texto",
                supabase_url=None, supabase_secret_key=None, supabase_bucket="snapshots", link_expira_s=3600)
    return ApiSettings(**{**base, **kw})


TOKEN = "tok-revisao"


def res(id_, codigo, status, conf=0.9, metodo="regra", peso=2, essencial=False, dimensao="pessoal",
        revisado=None, texto="quesito"):
    return {"id": id_, "codigo": codigo, "bloco": codigo.split(".")[0], "dimensao": dimensao, "matriz": "comum",
            "texto": texto, "peso": peso, "essencial": essencial, "verificador": "existence", "status": status,
            "nota": servicos.NOTA.get(status), "confianca": conf if status in servicos.NOTA else None,
            "metodo": metodo, "justificativa": "j", "n_evidencias": 1, "revisao_id": None,
            "status_revisado": revisado, "revisor": "r" if revisado else None,
            "revisado_em": AGORA if revisado else None}


class FakeRepo:
    """Mesma interface de api.database.Repo, com um ente e duas avaliações."""

    def __init__(self):
        self.res = {
            10: [res(1, "1.1", "atende", essencial=True, peso=3, dimensao="institucional_acesso"),
                 res(2, "4.1", "nao_atende", conf=0.4, dimensao="receita_despesa"),
                 res(3, "5.1", "nao_verificavel", dimensao="pessoal")],
            20: [res(11, "1.1", "atende", essencial=True, peso=3, dimensao="institucional_acesso"),
                 res(12, "4.1", "parcial", conf=0.7, dimensao="receita_despesa"),
                 res(13, "5.1", "nao_verificavel", dimensao="pessoal"),
                 res(14, "16.5", "nao_verificavel", metodo="pendente", dimensao="institucional_acesso")]}
        self.avs = [self._av(10, 1, 40.0), self._av(20, 2, 60.0)]
        self.revs = []

    def _av(self, av_id, run, score):
        return {"slug": "rj-teste", "nome": "Teste", "avaliacao_id": av_id, "run_id": run, "score": score,
                "score_min": score - 5, "score_max": score + 5, "cobertura": 90.0, "essenciais_ok": True,
                "classificacao": "Intermediário", "avaliado_em": AGORA, "rodada_em": AGORA,
                "scores_bloco": {"dimensoes": {"pessoal": score}, "contagem": {"atende": 1}},
                "engine_version": "conformidade-0.1.0", "modelo_embedding": None}

    def ping(self):
        return {"ultima_rodada": 2, "avaliacoes": 2, "schema_v03": True}

    def entes(self):
        return [{"municipio": "Teste", "uf": "RJ", "cod_ibge": "3300000", "tipo_entidade": "prefeitura",
                 "poder": "executivo", "esfera": "municipal", **self.avs[-1], "coleta_run_id": 2,
                 "coleta_status": "concluido", "coleta_paginas": 10, "coleta_arquivos": 2, "coleta_em": AGORA}]

    def urls(self, slug):
        return [{"tipo": "site", "url": "https://teste.rj.gov.br/"}]

    def avaliacao(self, slug, run_id=None):
        avs = [a for a in self.avs if run_id is None or a["run_id"] == run_id]
        return avs[-1] if avs else None

    def historico(self, slug):
        return self.avs

    def resultados(self, avaliacao_id):
        rv = {r["resultado_id"]: r["status_corrigido"] for r in self.revs}
        return [{**r, "status_revisado": rv.get(r["id"], r["status_revisado"])} for r in self.res[avaliacao_id]]

    def timeline(self, slug, codigo):
        out = []
        for av in self.avs:
            for r in self.resultados(av["avaliacao_id"]):
                if r["codigo"] == codigo:
                    out.append({"run_id": av["run_id"], "avaliado_em": AGORA, "resultado_id": r["id"],
                                "status": r["status"], "nota": r["nota"], "confianca": r["confianca"],
                                "metodo": r["metodo"], "justificativa": r["justificativa"],
                                "status_revisado": r["status_revisado"]})
        return out

    def resultado(self, rid):
        for av_id, rs in self.res.items():
            for r in self.resultados(av_id):
                if r["id"] == rid:
                    return {**r, "avaliacao_id": av_id, "run_id": 1 if av_id == 10 else 2, "slug": "rj-teste",
                            "nome": "Teste", "fundamentacao": "LAI", "checker_version": "existence@2",
                            "sinais": {"forca": 0.7}}
        return None

    def evidencias(self, rid):
        return [{"id": 100 + rid, "tipo": "link", "url": "https://teste.rj.gov.br/x", "tem_copia": True}]

    def copias_da_evidencia(self, eid):
        return {"id": eid, "screenshot": "png/aa/x.png", "html": "html/aa/x.html", "arquivo": None} \
            if eid >= 100 else None

    def revisoes_do_resultado(self, rid):
        return [r for r in self.revs if r["resultado_id"] == rid]

    def fila(self, limiar, slug, incluir_nv, limite):
        self.ultimo_limiar = limiar
        return [{**r, "slug": "rj-teste", "run_id": 2} for r in self.resultados(20)
                if not r["status_revisado"] and r["metodo"] != "pendente"
                and ((r["confianca"] is not None and r["confianca"] < limiar)
                     or (incluir_nv and r["status"] == "nao_verificavel"))][:limite]

    def inserir_revisao(self, rid, status, revisor, comentario):
        rv = {"id": len(self.revs) + 1, "resultado_id": rid, "revisor": revisor, "status_corrigido": status,
              "comentario": comentario, "revisado_em": AGORA}
        self.revs.append(rv)
        return rv

    def revisoes(self, slug, limite):
        out = []
        for r in self.revs:
            res = self.resultado(r["resultado_id"])
            out.append({**r, "slug": "rj-teste", "run_id": res["run_id"], "codigo": res["codigo"],
                        "status_motor": res["status"], "concordou": r["status_corrigido"] == res["status"],
                        "confianca": res["confianca"], "metodo": res["metodo"], "verificador": "existence"})
        return out

    def rodadas(self, limite):
        return [{"run_id": 2, "gatilho": "manual", "status": "concluido", "iniciado_em": AGORA,
                 "finalizado_em": AGORA, "entes": 1, "concluidos": 1, "parciais": 0, "falhas": 0, "paginas": 40,
                 "arquivos": 5, "bytes": 1000, "avaliacoes": 1}]

    def rodada(self, run_id):
        return [{"slug": "rj-teste", "status": "concluido", "paginas": 40, "arquivos": 5, "bloqueios_robots": 0,
                 "secoes_ausentes": ["obras"], "segundos": 12.5}] if run_id == 2 else []

    def checklist(self):
        base = {"versao": "checklistPortalTransp teste", "bloco": "2", "matriz": "comum", "texto": "quesito",
                "fundamentacao": "LAI", "peso": 2, "essencial": False, "aplica_a": ["comum"], "onda": 1}
        return [{**base, "codigo": "10.2", "dimensao": "pessoal", "verificador": "x"},
                {**base, "codigo": "2.1", "dimensao": "pessoal", "verificador": "x"}]

    def busca_texto(self, q, slug, k):
        if q == "orcameto":                     # erro de digitação: a busca exata não acha nada
            return []
        return [{"trecho_id": 1, "slug": "rj-teste", "run_id": 2, "texto": f"... {q} ...", "relevancia": 0.5}]

    def busca_aproximada(self, q, slug, k):
        return [{"trecho_id": 3, "slug": "rj-teste", "run_id": 2, "texto": "... orçamento ...", "relevancia": 0.7}]

    def busca_vetor(self, v, modelo, slug, k):
        assert v.startswith("[") and modelo == "hash-bow-768"
        return [{"trecho_id": 2, "slug": "rj-teste", "run_id": 2, "texto": "semântico", "relevancia": 0.8}]


class AssinadorFalso:
    expira = 60

    def assinar(self, chave):
        return f"https://s/{chave}"


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def cli(repo):
    app = create_app(cfg(database_url=None, token_revisao=TOKEN, busca="texto"), repo=repo)
    with TestClient(app) as c:
        app.state.assinador = AssinadorFalso()
        yield c


# ------------------------------------------------------------------ serviços (puros)

def test_score_revisado_e_igual_ao_da_camada2(tmp_path):
    """Sem revisões, recalcular a partir das linhas do banco dá exatamente o score gravado pelo motor."""
    from compliance.checklist import carregar_checklist
    from compliance.pipeline import executar
    from compliance.repo import LocalConclusao
    from ingestion.embeddings.generate_embeddings import HashEmbedder
    from tests.test_conformidade import _rodada

    s, rid = _rodada(tmp_path)
    repo = LocalConclusao(tmp_path / "local")
    out = executar(repo, rid, s, HashEmbedder(768), data_ref=date(2026, 10, 12))["entes"]["rj-exemplo"]
    crit = {c.codigo: c for c in carregar_checklist().criterios}
    linhas = [{"codigo": r["codigo"], "bloco": crit[r["codigo"]].bloco, "dimensao": crit[r["codigo"]].dimensao,
               "peso": crit[r["codigo"]].peso, "essencial": crit[r["codigo"]].essencial, "status": r["status"],
               "metodo": r["metodo"]} for r in repo.read("criterion_result")]
    sr = servicos.score_revisado(linhas)
    assert (sr["score"], sr["score_min"], sr["score_max"]) == (out["score"], out["score_min"], out["score_max"])
    assert sr["classificacao"] == out["classificacao"]

    # revisar um "não atende" essencial para "atende" só pode subir o score
    alvo = next(x for x in linhas if x["status"] == "nao_atende" and x["essencial"])
    alvo["status_revisado"] = "atende"
    assert servicos.score_revisado(linhas)["score"] > out["score"]


def test_revisao_de_pendente_passa_a_contar():
    linhas = [res(1, "1.1", "atende", peso=3, dimensao="institucional_acesso"),
              res(2, "16.5", "nao_verificavel", metodo="pendente", dimensao="institucional_acesso")]
    assert servicos.score_revisado(linhas)["contagem"] == {"atende": 1, "pendente": 1}
    linhas[1]["status_revisado"] = "nao_atende"
    sr = servicos.score_revisado(linhas)
    assert sr["contagem"] == {"atende": 1, "nao_atende": 1} and sr["score"] < 100


def test_enriquecer_ordena_e_marca_revisao():
    rs = servicos.enriquecer([res(1, "10.1", "atende"), res(2, "2.1", "nao_atende", conf=0.3),
                              res(3, "2.10", "nao_verificavel"), res(4, "16.5", "nao_verificavel", metodo="pendente"),
                              res(5, "3.1", "nao_atende", conf=0.3, revisado="atende")], limiar=0.6)
    assert [r["codigo"] for r in rs] == ["2.1", "2.10", "3.1", "10.1", "16.5"]
    flags = {r["codigo"]: r["precisa_revisao"] for r in rs}
    assert flags == {"2.1": True, "2.10": True, "3.1": False, "10.1": False, "16.5": False}
    assert next(r for r in rs if r["codigo"] == "3.1")["status_final"] == "atende"


def test_ranking_deixa_incompletas_sem_posicao():
    def e(slug, score, cls):
        return {"slug": slug, "nome": slug, "avaliacao_id": 1, "run_id": 1, "score": score,
                "classificacao": cls, "scores_bloco": {"dimensoes": {"pessoal": score / 2}}}
    rk = servicos.ranking([e("a", 50, "Intermediário"), e("b", 80, "Avaliação incompleta"),
                           e("c", 70, "Intermediário"), {"slug": "d", "nome": "d", "avaliacao_id": None}])
    assert [(x["slug"], x["posicao"]) for x in rk] == [("c", 1), ("a", 2), ("b", None)]
    assert servicos.ranking([e("a", 50, "Básico")], "pessoal")[0]["valor"] == 25


def test_diff_classifica_eventos():
    de = [res(1, "1.1", "atende"), res(2, "4.1", "nao_atende"), res(3, "5.1", "atende"),
          res(4, "6.1", "nao_verificavel"), res(5, "7.1", "parcial")]
    para = [res(11, "1.1", "atende"), res(12, "4.1", "parcial"), res(13, "5.1", "nao_atende"),
            res(14, "6.1", "atende"), res(16, "8.1", "atende"), res(15, "7.1", "nao_atende", revisado="atende")]
    d = servicos.diff(de, para)
    ev = {m["codigo"]: m["evento"] for m in d["mudancas"]}
    assert ev == {"4.1": "melhorou", "5.1": "piorou", "6.1": "mudou_verificabilidade", "7.1": "melhorou",
                  "8.1": "novo"}
    assert d["resumo"]["estavel"] == 1 and len(servicos.diff(de, para, incluir_estaveis=True)["mudancas"]) == 6


# ------------------------------------------------------------------ rotas

def test_health_e_raiz(cli):
    h = cli.get("/health").json()
    assert h == {"status": "ok", "banco": "ok", "versao": "0.1.0", "ultima_rodada": 2, "avaliacoes": 2,
                 "schema_v03": True, "busca": "texto"}
    assert cli.get("/", follow_redirects=False).headers["location"] == "/docs"


def test_sem_banco_responde_503():
    with TestClient(create_app(cfg(database_url=None))) as c:
        assert c.get("/health").status_code == 503
        assert c.get("/entes").status_code == 503


def test_erro_de_banco_vira_503(repo):
    class Quebrado(FakeRepo):
        def entes(self):
            import types
            erro = types.new_class("OperationalError", (Exception,))
            erro.__module__ = "psycopg"
            raise erro("conexão perdida")

        def ping(self):
            raise RuntimeError("down")

    with TestClient(create_app(cfg(database_url=None), repo=Quebrado()), raise_server_exceptions=False) as c:
        assert c.get("/entes").status_code == 503
        assert c.get("/health").json()["banco"] == "indisponível"


def test_entes_e_detalhe(cli):
    e = cli.get("/entes").json()[0]
    assert e["slug"] == "rj-teste" and e["avaliacao"]["run_id"] == 2 and e["coleta"]["paginas"] == 10
    d = cli.get("/entes/rj-teste").json()
    assert d["urls"][0]["tipo"] == "site" and d["score_revisado"]["contagem"]["pendente"] == 1
    assert cli.get("/entes/outro").status_code == 404


def test_resultados_filtros_diff_timeline(cli):
    rs = cli.get("/entes/rj-teste/resultados").json()
    assert [r["codigo"] for r in rs] == ["1.1", "4.1", "5.1", "16.5"]
    assert [r["codigo"] for r in cli.get("/entes/rj-teste/resultados", params={"precisa_revisao": True}).json()] \
        == ["5.1"]
    assert len(cli.get("/entes/rj-teste/resultados", params={"run_id": 1}).json()) == 3
    assert cli.get("/entes/rj-teste/resultados", params={"status": "talvez"}).status_code == 422
    d = cli.get("/entes/rj-teste/diff").json()
    assert d["delta_score"] == 20.0 and {m["codigo"]: m["evento"] for m in d["mudancas"]} == {
        "4.1": "melhorou", "16.5": "novo"}
    assert cli.get("/entes/rj-teste/diff", params={"de": 1, "para": 7}).status_code == 404


def test_post_revisao(cli, repo):
    corpo = {"resultado_id": 12, "status_corrigido": "atende", "revisor": "Ana"}
    assert cli.post("/revisoes", json=corpo).status_code == 401
    r = cli.post("/revisoes", json=corpo, headers={"X-API-Key": TOKEN})
    assert r.status_code == 201 and r.json()["concordou_com_motor"] is False
    assert r.json()["score_revisado"]["dimensoes"]["receita_despesa"] == 100.0
    assert cli.get("/resultados/12").json()["status_final"] == "atende"
    assert cli.post("/revisoes", json={**corpo, "revisor": "A"}, headers={"X-API-Key": TOKEN}).status_code == 422
    assert cli.post("/revisoes", json={**corpo, "resultado_id": 999}, headers={"X-API-Key": TOKEN}).status_code == 404


def test_revisao_desligada_sem_token(repo):
    with TestClient(create_app(cfg(database_url=None), repo=repo)) as c:
        r = c.post("/revisoes", json={"resultado_id": 12, "status_corrigido": "atende", "revisor": "Ana"},
                   headers={"X-API-Key": "qualquer"})
        assert r.status_code == 503 and "API_TOKEN_REVISAO" in r.json()["detail"]


def test_fila_usa_limiar_do_scoring(cli, repo):
    cli.get("/revisoes/fila")
    assert repo.ultimo_limiar == servicos.cfg_scoring()["revisao_abaixo_de"]
    assert cli.get("/revisoes/fila", params={"limiar": 2}).status_code == 422


def test_busca_texto_e_semantica(cli, repo):
    b = cli.get("/busca", params={"q": "creche"}).json()
    assert b["modo"] == "texto" and b["resultados"][0]["texto"] == "... creche ..."
    assert cli.get("/busca", params={"q": "creche", "modo": "semantica"}).status_code == 409
    with TestClient(create_app(cfg(database_url=None, busca="hash"), repo=repo)) as c:
        b = c.get("/busca", params={"q": "creche"}).json()
        assert b["modo"] == "semantica" and b["modelo"] == "hash-bow-768"
        assert c.get("/busca", params={"q": "creche", "modo": "texto"}).json()["modo"] == "texto"


def test_busca_aproximada_quando_texto_nao_acha(cli):
    b = cli.get("/busca", params={"q": "orcameto"}).json()
    assert b["modo"] == "aproximada" and b["resultados"][0]["texto"] == "... orçamento ..."
    assert cli.get("/busca", params={"q": "orcamento"}).json()["modo"] == "texto"   # achou: sem fallback


def test_link_assinado_e_storage_nao_configurado(cli, repo):
    assert cli.get("/evidencias/101/arquivo").json() == {"url": "https://s/png/aa/x.png", "tipo": "screenshot",
                                                         "chave": "png/aa/x.png", "expira_em_s": 60}
    assert cli.get("/evidencias/101/arquivo", params={"tipo": "html"}).json()["chave"] == "html/aa/x.html"
    assert cli.get("/evidencias/101/arquivo", params={"tipo": "arquivo"}).status_code == 404
    assert cli.get("/evidencias/5/arquivo").status_code == 404
    with TestClient(create_app(cfg(database_url=None), repo=repo)) as c:
        assert c.get("/evidencias/101/arquivo").status_code == 503


def test_assinador_monta_url_do_supabase():
    from api.deps import Assinador

    class Sessao:
        headers = {}

        def post(self, url, json, timeout):
            self.chamada = (url, json)
            return type("R", (), {"status_code": 200, "text": "",
                                  "json": lambda s: {"signedURL": "/object/sign/snapshots/png/a.png?token=t"}})()

    s = cfg(supabase_url="https://abc.supabase.co/", supabase_secret_key="sb_secret_x", link_expira_s=90)
    a = Assinador(s, session=Sessao())
    assert a.assinar("png/a.png") == "https://abc.supabase.co/storage/v1/object/sign/snapshots/png/a.png?token=t"
    assert a.http.chamada == ("https://abc.supabase.co/storage/v1/object/sign/snapshots/png/a.png",
                              {"expiresIn": 90})
    assert a.http.headers == {"apikey": "sb_secret_x"}          # chave nova não vai em Authorization


def test_monitoramento_checklist_e_token_de_leitura(cli, repo):
    assert cli.get("/rodadas/2").json()[0]["slug"] == "rj-teste" and cli.get("/rodadas/3").status_code == 404
    assert [c["codigo"] for c in cli.get("/checklist").json()] == ["2.1", "10.2"]
    with TestClient(create_app(cfg(database_url=None, token_leitura="L"), repo=repo)) as c:
        assert c.get("/entes").status_code == 401 and c.get("/health").status_code == 200
        assert c.get("/entes", headers={"X-API-Key": "L"}).status_code == 200


def test_openapi_documenta_seguranca_e_exemplos(cli):
    spec = cli.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
    exemplo = spec["components"]["schemas"]["RevisaoIn"]["examples"][0]
    assert exemplo["status_corrigido"] == "atende"
    assert len(spec["paths"]) == 20
