"""Dashboard Streamlit (Camada 3): cliente da API, formatos, gráficos e as 6 páginas (AppTest).

As páginas rodam contra a API de verdade (FastAPI em memória) com o repositório falso de
tests/test_api.py — o mesmo caminho HTTP que o Streamlit Cloud usa com o Render.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
pytest.importorskip("fastapi")

RAIZ = Path(__file__).parent.parent
sys.path.insert(0, str(RAIZ / "dashboard"))

from tests import streamlit_shim  # noqa: E402

streamlit_shim.instalar()

import requests  # noqa: E402
import streamlit as st  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from painel import formatos as fmt  # noqa: E402
from painel import graficos as gr  # noqa: E402
from painel.cliente import ApiCliente, ApiErro, ApiIndisponivel  # noqa: E402
from painel.config import Config  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from api.main import create_app  # noqa: E402
from tests.test_api import TOKEN, AssinadorFalso, FakeRepo, cfg  # noqa: E402

APP = str(RAIZ / "dashboard" / "app.py")
PAGINAS = ["paginas/visao_geral.py", "paginas/municipio.py", "paginas/evolucao.py", "paginas/revisao.py",
           "paginas/busca.py", "paginas/monitoramento.py"]


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def http(repo):
    app = create_app(cfg(token_revisao=TOKEN), repo=repo)
    with TestClient(app) as c:
        app.state.assinador = AssinadorFalso()
        yield c


@pytest.fixture
def cliente(http):
    return ApiCliente("http://testserver", token=TOKEN, session=http)


def abrir(cliente, pagina=None, config=None, **estado):
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_cliente_api"] = cliente
    at.session_state["_config_painel"] = config or Config(api_url="http://testserver", api_token_revisao=TOKEN)
    for k, v in estado.items():
        at.session_state[k] = v
    at.run()
    if pagina:
        at.switch_page(pagina).run()
    return at


def sem_erros(at):
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


# ------------------------------------------------------------------ cliente
def test_cliente_rotas_e_erros(cliente):
    assert cliente.entes()[0]["slug"] == "rj-teste"
    assert cliente.diff("rj-teste")["delta_score"] == 20.0
    with pytest.raises(ApiErro) as e:
        cliente.ente("nao-existe")
    assert e.value.status == 404 and "não encontrado" in e.value.detalhe
    with pytest.raises(ApiErro) as e:
        cliente.busca("ab")
    assert e.value.status == 422 and "q" in e.value.detalhe
    out = cliente.revisar(12, "atende", "Ana", None)
    assert out["revisao"]["status_corrigido"] == "atende" and out["concordou_com_motor"] is False
    with pytest.raises(ApiErro) as e:
        ApiCliente("http://testserver", session=cliente.http).revisar(12, "atende", "Ana", None)
    assert e.value.status == 401


def test_cliente_api_fora_do_ar():
    class Caiu:
        def request(self, *a, **kw):
            raise requests.ConnectionError("recusada")

    with pytest.raises(ApiIndisponivel):
        ApiCliente("http://x", session=Caiu()).health()

    class Dormindo:
        def request(self, *a, **kw):
            return type("R", (), {"status_code": 503, "text": "", "json": lambda s: {"detail": "banco indisponível"}})()

    with pytest.raises(ApiIndisponivel, match="banco indisponível"):
        ApiCliente("http://x", session=Dormindo()).entes()


def test_config_le_secrets_e_ambiente(monkeypatch):
    from painel.config import carregar_config

    monkeypatch.setenv("API_URL", "https://minha-api.onrender.com/")
    monkeypatch.delenv("API_TOKEN_REVISAO", raising=False)
    c = carregar_config()
    assert c.api_url == "https://minha-api.onrender.com" and not c.revisao_habilitada


# ------------------------------------------------------------------ formatos e gráficos
def test_formatos():
    assert fmt.num(1234.5) == "1.234,5" and fmt.num(None) == "—" and fmt.num(93.456, 2, "%") == "93,46%"
    assert fmt.rotulo_status("nao_verificavel", "pendente") == "⏳ Pendente"
    assert fmt.rotulo_status("nao_atende") == "❌ Não atende"
    assert fmt.nome_curto("Prefeitura de Maricá") == "Maricá"
    rs = [{"id": 1, "codigo": "1.1", "texto": "t", "dimensao": "pessoal", "peso": 3, "essencial": True,
           "status": "nao_atende", "status_final": "atende", "metodo": "regra", "revisado": True,
           "precisa_revisao": False, "confianca": 0.4, "n_evidencias": 2},
          {"id": 2, "codigo": "16.5", "texto": "t", "dimensao": "pessoal", "peso": 2, "essencial": False,
           "status": "nao_verificavel", "status_final": "nao_verificavel", "metodo": "pendente", "revisado": False,
           "precisa_revisao": False, "confianca": None}]
    df = fmt.df_resultados(rs, {"pessoal": "Pessoal"})
    assert list(df["Status"]) == ["✅ Atende", "⏳ Pendente"] and df["Motor"][0] == "❌ Não atende"
    assert fmt.contagem_status(rs)["atende"] == 1 and fmt.contagem_status(rs)["pendente"] == 1


def _item(slug, score, completa=True):
    return {"slug": slug, "nome": f"Prefeitura de {slug}", "valor": score, "score": score, "score_min": score - 5,
            "score_max": score + 7, "cobertura": 90.0, "classificacao": "Intermediário", "no_ranking": completa}


def test_grafico_ranking_com_e_sem_faixa():
    fig = gr.ranking([_item("A", 40), _item("B", 70), _item("C", 55, completa=False)], campo="valor")
    barra = fig.data[0]
    assert list(barra.x) == [40, 55, 70]                      # ordem crescente = maior no topo
    assert list(barra.y) == ["A  <b>40,0</b>", "C (incompleta)  <b>55,0</b>", "B  <b>70,0</b>"]
    assert list(barra.marker.color) == [gr.AZUL, gr.AZUL_CLARO, gr.AZUL]
    assert list(barra.error_x.array) == [7, 7, 7] and list(barra.error_x.arrayminus) == [5, 5, 5]
    assert gr.ranking([_item("A", 40)], faixa=False).data[0].error_x.array is None


def test_grafico_heatmap_texto_legivel():
    matriz = {"dimensoes": {"pessoal": "Pessoal", "receita_despesa": "Receita"},
              "linhas": [{"slug": "a", "nome": "Prefeitura de A", "score": 50, "pessoal": 80.0, "receita_despesa": 20.0}]}
    fig = gr.heatmap_dimensoes(matriz, matriz["dimensoes"])
    cores = {a.text: a.font.color for a in fig.layout.annotations}
    assert cores == {"80": "#ffffff", "20": "#0b0b0b"}          # texto claro só em célula escura
    assert fig.data[0].zmin == 0 and fig.data[0].zmax == 100


def test_grafico_status_e_evolucao():
    fig = gr.status_empilhado([{"slug": "a", "nome": "Prefeitura de A", "atende": 3, "nao_atende": 1}])
    assert [t.name for t in fig.data] == ["✅ Atende", "❌ Não atende"] and fig.layout.barmode == "stack"
    hist = [{"run_id": 1, "avaliado_em": "2026-10-01T10:00:00Z", "score": 40, "score_min": 35, "score_max": 47},
            {"run_id": 2, "avaliado_em": "2026-10-12T10:00:00Z", "score": 60, "score_min": 55, "score_max": 66}]
    fig = gr.evolucao(hist)
    assert [t.name for t in fig.data] == ["Faixa de incerteza", "Score"] and list(fig.data[1].y) == [40, 60]


# ------------------------------------------------------------------ páginas (AppTest)
def test_visao_geral(cliente):
    at = abrir(cliente)
    sem_erros(at)
    assert at.title[0].value == "Conformidade dos portais da transparência"
    valores = {m.label: m.value for m in at.metric}
    assert valores["Municípios avaliados"] == "1" and valores["Score médio"] == "60,0"
    assert len(at.get("plotly_chart")) == 3
    tabela = at.dataframe[0].value
    assert tabela["Município"][0] == "Teste" and tabela["Detalhe"][0] == "municipio?ente=rj-teste"
    at.selectbox(key="vg_dim").set_value("pessoal").run()
    sem_erros(at)


def test_municipio_quesitos_e_detalhe(cliente):
    at = abrir(cliente, "paginas/municipio.py")
    sem_erros(at)
    assert {m.label for m in at.metric} >= {"Score", "Score revisado", "Cobertura", "Classificação"}
    quesitos = at.dataframe[1].value
    assert list(quesitos["Código"]) == ["1.1", "4.1", "5.1", "16.5"]
    at.toggle(key="mun_rev").set_value(True).run()
    assert list(at.dataframe[1].value["Código"]) == ["5.1"]
    at.selectbox(key="mun_quesito").set_value(12).run()
    sem_erros(at)
    assert any("4.1" in m.value for m in at.markdown)
    assert any(b.label == "Abrir cópia guardada" for b in at.button)
    at.button(key="copia_112").click().run()                 # link assinado da cópia (screenshot)
    sem_erros(at)
    assert at.get("imgs") or at.get("image")


def test_municipio_por_query_param(cliente):
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_cliente_api"] = cliente
    at.query_params["ente"] = "rj-teste"
    at.run().switch_page("paginas/municipio.py").run()
    sem_erros(at)
    assert at.selectbox(key="mun_ente").value == "rj-teste"


def test_evolucao_diff_e_timeline(cliente):
    at = abrir(cliente, "paginas/evolucao.py")
    sem_erros(at)
    valores = {m.label: m for m in at.metric}
    assert valores["Melhoraram"].value == "1" and valores["Variação do score"].value == "60,0"
    mudancas = at.dataframe[1].value
    assert set(mudancas["Código"]) == {"4.1", "16.5"}
    at.selectbox(key="evo_quesito").set_value("4.1").run()
    sem_erros(at)
    assert list(at.dataframe[2].value["Mudança"]) == ["• Primeira avaliação", "⬆️ Melhorou"]
    at.selectbox(key="evo_de").set_value(2).run()
    assert any("rodadas diferentes" in w.value for w in at.warning)


def test_revisao_com_senha_e_gravacao(cliente, repo):
    config = Config(api_url="http://testserver", api_token_revisao=TOKEN, revisao_senha="segredo")
    at = abrir(cliente, "paginas/revisao.py", config=config)
    sem_erros(at)
    assert not [f for f in at.get("form") if getattr(f, "form_id", "") == "form_revisao"]
    at.text_input[0].set_value("errada").run()
    at.button[0].click().run()
    assert any("Senha incorreta" in e.value for e in at.error)
    at.text_input[0].set_value("segredo")
    at.button[0].click().run()
    sem_erros(at)
    assert at.session_state["revisor_autorizado"] is True

    fila = at.dataframe[0].value
    assert list(fila["Código"]) == ["5.1"]                   # não verificável, ainda sem revisão
    at.selectbox(key="rev_item").set_value(13).run()
    at.radio[0].set_value("atende")
    at.text_input[0].set_value("Ana (MPRJ)")
    at.text_area[0].set_value("conferido no portal")
    gravar = next(b for b in at.button if b.label == "Gravar revisão")
    gravar.click().run()
    sem_erros(at)
    assert repo.revs and repo.revs[-1]["resultado_id"] == 13 and repo.revs[-1]["revisor"] == "Ana (MPRJ)"
    assert repo.revs[-1]["status_corrigido"] == "atende" and repo.revs[-1]["comentario"] == "conferido no portal"
    assert any("Revisão gravada" in s.value for s in at.success)
    assert at.session_state["revisor_nome"] == "Ana (MPRJ)"


def test_revisao_somente_leitura_sem_token(cliente):
    at = abrir(cliente, "paginas/revisao.py", config=Config(api_url="http://testserver"))
    sem_erros(at)
    assert any("somente leitura" in w.value for w in at.warning)
    assert not any(b.label == "Gravar revisão" for b in at.button)


def test_revisao_a_partir_do_municipio(cliente):
    at = abrir(cliente, "paginas/municipio.py")
    at.selectbox(key="mun_quesito").set_value(11).run()        # 1.1 atende: não está na fila
    next(b for b in at.button if b.label == "Revisar este quesito").click().run()
    sem_erros(at)
    assert at.selectbox(key="rev_item").value == 11


def test_busca(cliente):
    at = abrir(cliente, "paginas/busca.py")
    sem_erros(at)
    at.text_input(key="busca_q").set_value("creche")
    next(b for b in at.button if b.label == "Buscar").click().run()
    sem_erros(at)
    assert any("busca por palavras" in c.value for c in at.caption)
    assert any("... creche ..." in c.value for c in at.caption)
    at.text_input(key="busca_q").set_value("orcameto")
    next(b for b in at.button if b.label == "Buscar").click().run()
    sem_erros(at)
    assert any("busca aproximada" in c.value for c in at.caption)
    assert any("mais parecidos" in i.value for i in at.info)


def test_monitoramento(cliente):
    at = abrir(cliente, "paginas/monitoramento.py")
    sem_erros(at)
    valores = {m.label: m.value for m in at.metric}
    assert valores["API"] == "✅ No ar" and valores["Última rodada"] == "2"
    assert list(at.dataframe[1].value["Município"]) == ["rj-teste"]


def test_api_fora_do_ar_mostra_aviso():
    class Caiu:
        def request(self, *a, **kw):
            raise requests.ConnectionError("recusada")

    at = abrir(ApiCliente("http://x", session=Caiu()))
    assert not at.exception
    assert any("A API não respondeu" in e.value for e in at.error)
    assert any(b.label == "Tentar de novo" for b in at.button)


@pytest.mark.parametrize("pagina", PAGINAS)
def test_paginas_sem_avaliacao(pagina):
    """Banco recém-criado (sem avaliações): nenhuma página quebra."""
    class Vazio(FakeRepo):
        def __init__(self):
            super().__init__()
            self.avs, self.res = [], {}

        def entes(self):
            return [{"slug": "rj-teste", "nome": "Teste", "municipio": "Teste", "uf": "RJ", "cod_ibge": None,
                     "tipo_entidade": "prefeitura", "poder": "executivo", "esfera": "municipal",
                     "avaliacao_id": None, "run_id": None, "scores_bloco": None, "coleta_run_id": None}]

        def avaliacao(self, slug, run_id=None):
            return None

        def fila(self, *a):
            return []

        def rodadas(self, limite):
            return []

        def busca_texto(self, *a):
            return []

        def busca_aproximada(self, *a):
            return []

    app = create_app(cfg(token_revisao=TOKEN), repo=Vazio())
    with TestClient(app) as c:
        at = abrir(ApiCliente("http://testserver", token=TOKEN, session=c), pagina)
        assert not at.exception, [e.value for e in at.exception]
