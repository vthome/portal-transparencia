"""Camada 2: checklist, verificadores, confiança, score, embeddings e pipeline (offline)."""
from datetime import date

import pytest

from compliance.checkers import REGISTRO, Contexto
from compliance.checkers.base import Resultado, ausencia
from compliance.checkers.estruturados import _casa
from compliance.checkers.semantico import IndiceSemantico
from compliance.checklist import Criterio, carregar_checklist, carregar_scoring
from compliance.coleta import Arquivo, ColetaEnte, Pagina
from compliance.confidence import calcular as conf
from compliance.repo import AvaliacaoRevisada, LocalConclusao
from compliance.scoring import calcular as score
from ingestion.embeddings.generate_embeddings import (
    DimensaoIncompativel,
    HashEmbedder,
    gerar,
)
from ingestion.transform.prepare_documents import janelas

CFG = carregar_scoring()
REF = date(2026, 10, 12)


def pagina(id_, url, titulo, secoes=(), status="ok", texto="", links=(), prof=1, **extraido):
    return Pagina(id=id_, url=url, titulo=titulo, status=status, secoes=list(secoes), trilha=[url],
                  profundidade=prof, renderizado=False, texto=texto or (titulo + " ") * 20, links=list(links),
                  extraido=extraido)


def coleta(paginas=(), arquivos=(), poder="executivo", job_status="concluido", urls=None):
    return ColetaEnte(slug="rj-x", nome="X", tipo_entidade="prefeitura", poder=poder, esfera="municipal",
                      job={"status": job_status}, urls=urls or [
                          {"tipo": "site_oficial", "url": "https://x.rj.gov.br/"},
                          {"tipo": "portal_transparencia", "url": "https://transparencia.x.rj.gov.br/"}],
                      paginas=list(paginas), arquivos=list(arquivos))


def crit(verificador, params, codigo="9.9", peso=2, dimensao="pessoal", essencial=False, texto="quesito"):
    return Criterio(codigo=codigo, bloco=codigo.split(".")[0], dimensao=dimensao, matriz="comum", texto=texto,
                    fundamentacao="", peso=peso, essencial=essencial, aplica_a=["comum"], onda=1,
                    verificador=verificador, params=params)


def ctx(indice=None, embedder=None):
    return Contexto(data_ref=REF, cfg=CFG, indice=indice, embedder=embedder)


CAPA = pagina("p0", "https://x.rj.gov.br/", "Prefeitura", prof=0)
PORTAL = pagina("p1", "https://transparencia.x.rj.gov.br/", "Portal da Transparência", prof=1)


# ------------------------------------------------------------------ checklist

def test_checklist_completo_e_aplicabilidade():
    ck = carregar_checklist()
    assert len(ck.criterios) == 142
    assert sum(c.matriz == "comum" for c in ck.criterios) == 122
    assert {c.verificador for c in ck.criterios} <= set(REGISTRO)
    camara = coleta(poder="legislativo")
    prefeitura = coleta()
    assert sum(c.aplica(camara.tags) for c in ck.criterios) == 122          # matriz do Executivo não penaliza a Câmara
    assert sum(c.aplica(prefeitura.tags) for c in ck.criterios) == 142
    pesos = {c.codigo: c.peso for c in ck.criterios}
    assert pesos["12.1"] == 3 and pesos["4.5"] == 2 and pesos["21.4"] == 1  # LRF essencial; meta 2; boa prática 1


# ------------------------------------------------------------------ score

def _r(status, metodo="regra"):
    return Resultado(status, "", metodo=metodo)


def test_score_dois_niveis_incerteza_e_essenciais():
    pares = [
        (crit("x", {}, "4.1", peso=3, dimensao="receita_despesa", essencial=True), _r("atende")),
        (crit("x", {}, "4.5", peso=1, dimensao="receita_despesa"), _r("nao_atende")),
        (crit("x", {}, "5.1", peso=2, dimensao="pessoal"), _r("parcial")),
        (crit("x", {}, "5.2", peso=2, dimensao="pessoal"), _r("nao_verificavel")),
        (crit("x", {}, "16.5", peso=2, dimensao="pessoal"), _r("nao_verificavel", "pendente")),
        (crit("x", {}, "19.1", peso=3, dimensao="planejamento_contas"), _r("nao_aplicavel")),
    ]
    s = score(pares, CFG)
    # receita_despesa = (3*1 + 1*0)/4 = 0.75 ; pessoal = (2*0.5)/2 = 0.5 ; média das dimensões = 0.625
    assert s.score == 62.5
    assert s.scores_bloco["dimensoes"]["receita_despesa"] == 75.0
    # mínimo: 5.2 vale 0 -> pessoal 0.25 -> 0.5 ; máximo: 5.2 vale 1 -> pessoal 0.75 -> 0.75
    assert (s.score_min, s.score_max) == (50.0, 75.0)
    assert s.cobertura == round(6 / 8, 3)                                   # pendente e N/A fora; 5.2 não verificado
    assert s.essenciais_ok is True
    assert s.classificacao == "Avaliação incompleta"                        # cobertura 75% < 80%
    assert s.scores_bloco["contagem"]["pendente"] == 1 and s.scores_bloco["contagem"]["nao_aplicavel"] == 1


def test_classificacao_respeita_essenciais():
    from compliance.scoring import classificar

    assert classificar(96, True, 1.0, CFG) == "Diamante"
    assert classificar(96, False, 1.0, CFG) == "Elevado"                   # sem essenciais, teto Elevado
    assert classificar(40, None, 0.9, CFG) == "Básico"


def test_confianca_monotonica_e_limiares():
    assert conf({"forca": 0.9, "fontes": 2, "qualidade": 1}) > conf({"forca": 0.9, "fontes": 1, "qualidade": 1})
    assert conf({"forca": 0.9, "qualidade": 1}) > 0.75
    assert conf({"forca": 0.55, "qualidade": 1}) < 0.6                      # link sem página -> revisão
    assert conf({"forca": 0.35, "qualidade": 1}) < 0.5                      # ausência sem seção -> revisão


# ------------------------------------------------------------------ verificadores

def test_casamento_de_cabecalhos_por_inicio_de_palavra():
    campos = {"numero": ["numero", "n do"], "liquidacao": ["liquidac"]}
    assert _casa(campos, ["Nº do Processo", "Valor Liquidado"]) == {"numero": "numero do processo"}
    assert _casa(campos, ["Nome", "Liquidação"]) == {"liquidacao": "liquidacao"}   # "n do" não casa com "nome"


def test_structured_fields_tabela_e_csv():
    desp = pagina("p2", "https://transparencia.x.rj.gov.br/despesas", "Despesas", ["despesa"],
                  tabelas=[{"cabecalhos": ["Empenho", "Valor Pago"], "cabecalhos_norm": ["empenho", "valor pago"],
                            "n_linhas": 10}])
    c = crit("structured_fields", {"secoes": ["despesa"], "minimo_parcial": 2,
                                   "campos": {"empenho": ["empenh"], "liquidacao": ["liquid"], "pagamento": ["pago"]}})
    r = REGISTRO["structured_fields"](c, coleta([CAPA, PORTAL, desp]), ctx())
    assert r.status == "parcial" and r.sinais["faltando"] == ["liquidacao"] and r.evidencias[0].tipo == "tabela"
    csv = Arquivo("a1", "p2", "https://t/d.csv", "Despesas CSV", "csv",
                  {"colunas_norm": ["empenho", "valor liquidado", "valor pago"]}, ["despesa"], "", None, "sha")
    r2 = REGISTRO["structured_fields"](c, coleta([CAPA, PORTAL, desp], [csv]), ctx())
    assert r2.status == "atende"


def test_dados_atras_de_formulario_sao_nao_verificaveis():
    pg = pagina("p2", "https://transparencia.x.rj.gov.br/folha", "Servidores", ["pessoal"],
                formularios=[{"campos": [{"nome": "ano", "tipo": "select"}], "parece_busca": True}])
    c = crit("structured_fields", {"secoes": ["pessoal"], "campos": {"nome": ["nome"]}})
    assert REGISTRO["structured_fields"](c, coleta([CAPA, PORTAL, pg]), ctx()).status == "nao_verificavel"


def test_ausencia_distingue_falha_de_coleta():
    bloqueada = pagina("p3", "https://transparencia.x.rj.gov.br/obras", "", ["obras"], status="bloqueado_robots")
    assert ausencia(coleta([CAPA, PORTAL, bloqueada]), ["obras"], "x").status == "nao_verificavel"
    assert ausencia(coleta([CAPA, PORTAL], job_status="falhou"), ["obras"], "x").status == "nao_verificavel"
    assert ausencia(coleta([CAPA]), ["obras"], "x").status == "nao_verificavel"          # portal não coletado
    r = ausencia(coleta([CAPA, PORTAL]), ["obras"], "x")
    assert r.status == "nao_atende" and r.sinais["forca"] == 0.35                        # vai para revisão


def test_freshness_por_periodo_do_rreo():
    rreo = Arquivo("a1", "p2", "https://t/rreo.pdf", "RREO 4º bimestre", "pdf",
                   {"periodo": {"bimestre": 4, "ano": 2026}, "exercicios": [2026]}, ["rreo"], "texto", None, "s")
    c = crit("freshness", {"secoes": ["rreo"], "referencia": "bimestral"})
    r = REGISTRO["freshness"](c, coleta([CAPA, PORTAL], [rreo]), ctx())
    assert r.status == "atende" and r.sinais["data_mais_recente"] == "2026-08-31" and r.sinais["atraso_dias"] == 42
    velho = Arquivo("a2", "p2", "https://t/r.pdf", "RREO", "pdf", {"periodo": {"bimestre": 3, "ano": 2026}},
                    ["rreo"], "t", None, "s")
    assert REGISTRO["freshness"](c, coleta([CAPA, PORTAL], [velho]), ctx()).status == "parcial"   # 104 dias > 90


def test_history_com_seletor_de_exercicio():
    pg = pagina("p2", "https://transparencia.x.rj.gov.br/receitas", "Receitas", ["receita"],
                formularios=[{"campos": [{"nome": "exercicio", "rotulo": "Exercício", "tipo": "select", "opcoes": 6}]}])
    r = REGISTRO["history"](crit("history", {"secoes": ["receita"]}), coleta([CAPA, PORTAL, pg]), ctx())
    assert r.status == "atende" and r.sinais["seletor_de_exercicio"]


def test_formulario_esic_regras_da_lai():
    def com(campos):
        pg = pagina("p2", "https://esic.x.rj.gov.br/", "e-SIC", ["sic"],
                    formularios=[{"campos": campos}])
        return REGISTRO["formulario_esic"](crit("formulario_esic", {"secoes": ["sic"]}), coleta([CAPA, PORTAL, pg]), ctx())
    base = [{"nome": "nome", "tipo": "text", "obrigatorio": True}, {"nome": "email", "tipo": "email", "obrigatorio": True},
            {"nome": "pedido", "tipo": "textarea", "obrigatorio": True}]
    assert com(base).status == "atende"
    assert com(base + [{"nome": "motivo", "tipo": "textarea", "obrigatorio": True}]).status == "nao_atende"
    assert com(base + [{"nome": "rg", "rotulo": "RG", "tipo": "text", "obrigatorio": True}]).status == "parcial"


def test_homepage_link_destaque_e_screenshot():
    capa = pagina("p0", "https://x.rj.gov.br/", "Prefeitura", prof=0,
                  links=[{"url": "https://transparencia.x.rj.gov.br/", "texto": "Transparência", "na_capa_topo": True}])
    capa.screenshot_path = "png/ab/x.png"
    r = REGISTRO["homepage_link"](crit("homepage_link", {}), coleta([capa, PORTAL]), ctx())
    assert r.status == "atende" and any(e.tipo == "screenshot" for e in r.evidencias)


def test_semantico_com_e_sem_embeddings():
    emb = HashEmbedder(256)
    pg = pagina("p2", "https://x.rj.gov.br/creches", "Educação", ["boas_praticas"],
                texto="A lista de espera das creches municipais e os critérios de prioridade estão publicados. " * 3)
    textos = ["Lista de espera das creches municipais com critérios de prioridade de vagas",
              "Agenda cultural do fim de semana com shows na praça"]
    idx = IndiceSemantico.de_chunks([{"texto": t, "page_snapshot_id": "p2", "url": pg.url, "embedding": v}
                                     for t, v in zip(textos, emb.encode(textos))])
    c = crit("semantic", {"consulta": "lista de espera em creches", "termos": ["lista de espera", "creche"],
                          "secoes": ["boas_praticas"]}, texto="Divulga a lista de espera em creches públicas")
    r = REGISTRO["semantic"](c, coleta([CAPA, PORTAL, pg]), ctx(idx, emb))
    assert r.status == "atende" and r.metodo == "embedding" and r.sinais["trechos_com_termo"] >= 1
    r2 = REGISTRO["semantic"](c, coleta([CAPA, PORTAL, pg]), ctx())
    assert r2.status == "atende" and r2.metodo == "regra"                  # fallback por termos


# ------------------------------------------------------------------ embeddings

def test_janelas_respeitam_tamanho_e_sobreposicao():
    texto = ("Frase de teste sobre despesas públicas do município. " * 60).strip()
    js = janelas(texto, 300, 50)
    assert len(js) > 5 and all(len(j) <= 360 for j in js)


class _RepoDim:
    def dimensao_embedding(self):
        return 512


def test_dimensao_incompativel_falha_cedo():
    with pytest.raises(DimensaoIncompativel, match="512"):
        gerar(_RepoDim(), "1", HashEmbedder(768))


# ------------------------------------------------------------------ pipeline ponta a ponta

def _rodada(tmp_path):
    from ingestion.load.load_postgres import LocalRepository
    from ingestion.load.storage import LocalStorage
    from ingestion.pipeline import run
    from tests.helpers import make_http, test_settings
    from tests.test_crawler_pipeline import ente, montar_site

    s = test_settings(data_dir=tmp_path)
    r = run([ente()], LocalRepository(tmp_path / "local"), LocalStorage(tmp_path / "o"), s,
            http_factory=lambda: make_http(montar_site(), s))
    return s, r["run_id"]


def test_pipeline_conclusao_local(tmp_path):
    from compliance.pipeline import executar

    s, rid = _rodada(tmp_path)
    repo = LocalConclusao(tmp_path / "local")
    res = executar(repo, rid, s, HashEmbedder(768), data_ref=REF)
    sc = res["entes"]["rj-exemplo"]
    assert 0 <= sc["score"] <= 100 and sc["score_min"] <= sc["score"] <= sc["score_max"]
    assert sum(sc["contagem"].values()) == 142
    crs = {c["codigo"]: c for c in repo.read("criterion_result")}
    assert crs["1.2"]["status"] == "atende" and crs["20.1"]["status"] == "atende"
    assert crs["20.2"]["status"] == "atende" and crs["4.1"]["status"] == "parcial"
    assert crs["16.5"]["metodo"] == "pendente" and crs["16.5"]["confianca"] is None
    assert all(0 <= c["confianca"] <= 1 for c in crs.values() if c["confianca"] is not None)
    assert repo.read("evidence") and repo.read("chunk") and len(repo.read("evaluation")) == 1
    assert res["embeddings"]["calculados"] > 0
    rel = open(res["relatorio"], encoding="utf-8").read()
    assert "## Ranking" in rel and "rj-exemplo" in rel

    # reavaliar a mesma rodada substitui (não duplica) e não recalcula embeddings
    res2 = executar(repo, rid, s, HashEmbedder(768), data_ref=REF)
    assert len(repo.read("evaluation")) == 1 and res2["embeddings"]["trechos"] == 0

    # com revisão humana, só substitui com --forcar
    atual = repo.read("evaluation")[0]["id"]
    alvo = next(c for c in repo.read("criterion_result") if c["codigo"] == "1.3" and c["evaluation_id"] == atual)
    repo._append("human_review", {"id": "h1", "criterion_result_id": alvo["id"], "revisor": "v",
                                  "status_corrigido": "atende"})
    with pytest.raises(AvaliacaoRevisada):
        executar(repo, rid, s, HashEmbedder(768), data_ref=REF)
    executar(repo, rid, s, HashEmbedder(768), data_ref=REF, forcar=True)


def test_segunda_rodada_reaproveita_vetores(tmp_path):
    from compliance.pipeline import executar

    s, rid1 = _rodada(tmp_path)
    repo = LocalConclusao(tmp_path / "local")
    executar(repo, rid1, s, HashEmbedder(768), data_ref=REF)
    _, rid2 = _rodada(tmp_path)
    repo2 = LocalConclusao(tmp_path / "local")
    res = executar(repo2, rid2, s, HashEmbedder(768), data_ref=REF)
    assert res["embeddings"]["trechos"] > 0 and res["embeddings"]["calculados"] == 0   # mesmo texto, mesmo vetor


def test_calibracao_com_revisoes_sinteticas():
    from compliance.calibrar import ajustar

    revs = [{"sinais": {"forca": f, "fontes": 1, "qualidade": 1.0}, "status": "atende",
             "status_corrigido": "atende" if f > 0.6 else "nao_atende"} for f in [0.3, 0.4, 0.5, 0.55] * 5 + [0.8, 0.9, 0.95] * 5]
    res = ajustar(revs, minimo=10)
    assert res["n"] == 35 and res["pesos"]["forca"] > 0


def test_embeddings_interrompidos_continuam_de_onde_pararam(tmp_path):
    """Grava em lotes de páginas completas: uma queda no meio não perde o que já foi gravado."""
    s, rid = _rodada(tmp_path)
    repo = LocalConclusao(tmp_path / "local")

    class Falha(HashEmbedder):
        chamadas = 0

        def encode(self, textos):
            Falha.chamadas += 1
            if Falha.chamadas > 1:
                raise KeyboardInterrupt
            return super().encode(textos)

    with pytest.raises(KeyboardInterrupt):
        gerar(repo, rid, Falha(768), lote_gravacao=3)
    parcial = repo.read("chunk")
    assert parcial
    origens = {(c["page_snapshot_id"], c["file_artifact_id"]) for c in parcial}
    res = gerar(repo, rid, HashEmbedder(768), lote_gravacao=3)          # mesmo nome de modelo
    assert res["trechos"] > 0
    total = repo.read("chunk")
    assert {(c["page_snapshot_id"], c["file_artifact_id"]) for c in total[len(parcial):]}.isdisjoint(origens)
    assert gerar(repo, rid, HashEmbedder(768))["trechos"] == 0                 # completo
