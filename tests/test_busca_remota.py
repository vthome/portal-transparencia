"""Busca semântica com a consulta vetorizada fora da API (API_BUSCA=remoto).

Cobre as três peças: o cliente (api/embedder_remoto.py), a rota /busca com queda para a busca
por palavras, e o serviço (servicos/embeddings/app.py) — este com um modelo falso, sem baixar o
SERAFIM. O último teste liga cliente e serviço de verdade (contrato ponta a ponta).
"""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.deps import get_embedder  # noqa: E402
from api.embedder_remoto import EmbedderIndisponivel, RemotoEmbedder  # noqa: E402
from api.main import create_app  # noqa: E402
from tests.test_api import FakeRepo, cfg  # noqa: E402

MODELO = "hash-bow-768"          # o FakeRepo.busca_vetor confere este nome
DIM = 768


def _servico():
    caminho = Path(__file__).parent.parent / "servicos" / "embeddings" / "app.py"
    spec = importlib.util.spec_from_file_location("servico_embeddings", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ModeloFalso:
    nome = MODELO
    dim = DIM

    def encode(self, textos):
        return [[1.0] + [0.0] * (DIM - 1) for _ in textos]


# ------------------------------------------------------------------ cliente
class Resp:
    def __init__(self, status=200, corpo=None, texto=None):
        self.status_code, self._corpo, self._texto = status, corpo, texto

    def json(self):
        if self._texto is not None:
            raise ValueError("não é JSON")
        return self._corpo


class Sessao:
    def __init__(self, resposta=None, erro=None):
        self.headers, self.resposta, self.erro, self.chamadas = {}, resposta, erro, []

    def post(self, url, json=None, timeout=None):
        self.chamadas.append((url, json, timeout, dict(self.headers)))
        if self.erro:
            raise self.erro
        return self.resposta


def _cliente(sessao):
    return RemotoEmbedder("https://emb.exemplo/", "tok", MODELO, DIM, timeout_s=7, session=sessao)


def test_cliente_devolve_vetores_e_envia_token():
    s = Sessao(Resp(corpo={"modelo": MODELO, "dim": DIM, "vetores": [[0.5] * DIM]}))
    assert _cliente(s).encode(["orçamento"]) == [[0.5] * DIM]
    url, corpo, timeout, headers = s.chamadas[0]
    assert url == "https://emb.exemplo/embed" and corpo == {"textos": ["orçamento"]} and timeout == 7
    assert headers["Authorization"] == "Bearer tok"


@pytest.mark.parametrize("sessao, trecho", [
    (Sessao(erro=requests.Timeout()), "não respondeu a tempo"),
    (Sessao(erro=requests.ConnectionError()), "ConnectionError"),
    (Sessao(Resp(401, {})), "recusou o token"),
    (Sessao(Resp(500, {})), "respondeu 500"),
    (Sessao(Resp(texto="<html>")), "não é JSON"),
    (Sessao(Resp(corpo={"modelo": "outro-modelo", "vetores": [[0.1] * DIM]})), "outro-modelo"),
    (Sessao(Resp(corpo={"modelo": MODELO, "vetores": [[0.1] * 384]})), "768 dimensões"),
    (Sessao(Resp(corpo={"modelo": MODELO, "vetores": []})), "1 vetor"),
])
def test_cliente_transforma_falhas_em_indisponivel(sessao, trecho):
    with pytest.raises(EmbedderIndisponivel, match=trecho):
        _cliente(sessao).encode(["x"])


# ------------------------------------------------------------------ rota /busca
class EmbedderOk:
    nome = MODELO

    def encode(self, textos):
        return [[0.1] * DIM for _ in textos]


class EmbedderFora:
    nome = MODELO

    def encode(self, textos):
        raise EmbedderIndisponivel("o serviço de embeddings não respondeu a tempo")


def _api(embedder):
    app = create_app(cfg(database_url=None, busca="remoto"), repo=FakeRepo())
    app.dependency_overrides[get_embedder] = lambda: embedder
    return TestClient(app)


def test_busca_remota_usa_pgvector():
    with _api(EmbedderOk()) as c:
        b = c.get("/busca", params={"q": "folha de pagamento"}).json()
    assert b["modo"] == "semantica" and b["modelo"] == MODELO and b.get("aviso") is None
    assert b["resultados"][0]["texto"] == "semântico"


def test_busca_remota_fora_do_ar_cai_para_palavras_com_aviso():
    with _api(EmbedderFora()) as c:
        b = c.get("/busca", params={"q": "creche"}).json()
        assert b["modo"] == "texto" and b["resultados"] and "indisponível" in b["aviso"]
        # erro de digitação + serviço fora: aproximada, ainda com o aviso
        b = c.get("/busca", params={"q": "orcameto"}).json()
        assert b["modo"] == "aproximada" and b["aviso"]
        # quem pediu explicitamente a semântica recebe o erro, não outra busca
        r = c.get("/busca", params={"q": "creche", "modo": "semantica"})
        assert r.status_code == 503 and "indisponível" in r.json()["detail"]


def test_health_informa_busca_remota():
    app = create_app(cfg(database_url=None, busca="remoto", embeddings_remoto_url="https://emb.exemplo",
                         embeddings_remoto_token="tok"), repo=FakeRepo())
    with TestClient(app) as c:
        assert c.get("/health").json()["busca"] == "semantica_remota"


def test_remoto_sem_url_fica_na_busca_por_texto():
    from api.config import ApiSettings

    assert get_embedder(ApiSettings(busca="remoto", embeddings_remoto_url=None,
                                    embeddings_remoto_token=None)) is None


# ------------------------------------------------------------------ serviço
def test_servico_exige_token_e_limita_tamanho():
    mod = _servico()
    with pytest.raises(SystemExit):
        mod.criar_app(carregar=lambda m: ModeloFalso(), token="")
    app = mod.criar_app(carregar=lambda m: ModeloFalso(), token="segredo", modelo=MODELO,
                        max_textos=2, max_caracteres=50)
    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok", "modelo": MODELO, "dim": DIM}
        assert c.post("/embed", json={"textos": ["a"]}).status_code == 401
        auth = {"Authorization": "Bearer segredo"}
        assert c.post("/embed", json={"textos": ["a"]}, headers={"Authorization": "Bearer x"}).status_code == 401
        assert c.post("/embed", json={"textos": ["a", "b", "c"]}, headers=auth).status_code == 422
        assert c.post("/embed", json={"textos": ["a" * 51]}, headers=auth).status_code == 422
        assert c.post("/embed", json={"textos": []}, headers=auth).status_code == 422
        d = c.post("/embed", json={"textos": ["orçamento"]}, headers=auth).json()
        assert d["modelo"] == MODELO and d["dim"] == DIM and len(d["vetores"][0]) == DIM


class SessaoTestClient:
    """Adapta o TestClient do serviço à interface de requests.Session usada pelo cliente."""

    def __init__(self, tc):
        self.tc, self.headers = tc, {}

    def post(self, url, json=None, timeout=None):
        return self.tc.post(url.replace("http://servico", ""), json=json, headers=self.headers)


def test_contrato_cliente_servico_ponta_a_ponta():
    mod = _servico()
    app = mod.criar_app(carregar=lambda m: ModeloFalso(), token="segredo", modelo=MODELO)
    with TestClient(app) as tc:
        emb = RemotoEmbedder("http://servico", "segredo", MODELO, DIM, session=SessaoTestClient(tc))
        assert emb.encode(["remuneração dos servidores"])[0][0] == 1.0
        errado = RemotoEmbedder("http://servico", "outro", MODELO, DIM, session=SessaoTestClient(tc))
        with pytest.raises(EmbedderIndisponivel, match="token"):
            errado.encode(["x"])
