"""Cliente do serviço externo de embeddings (API_BUSCA=remoto).

A API do Render gratuito não comporta o SERAFIM na memória. Os vetores dos trechos já estão no
pgvector; aqui só a CONSULTA é vetorizada, por HTTP, no serviço de servicos/embeddings/ — que
usa o mesmo modelo e a mesma normalização da Conclusão. Depois a comparação continua no banco
(Repo.busca_vetor), exatamente como no modo `modelo`.

Qualquer falha (serviço dormindo, fora do ar, token errado, modelo diferente) vira
EmbedderIndisponivel; a rota /busca então responde com a busca por palavras e um aviso.
"""
from __future__ import annotations

import logging

log = logging.getLogger("api.embeddings")


class EmbedderIndisponivel(RuntimeError):
    """O serviço de embeddings não devolveu um vetor utilizável."""


class RemotoEmbedder:
    def __init__(self, url: str, token: str, modelo: str, dim: int, timeout_s: float = 20.0,
                 session=None) -> None:
        import requests

        self.url = url.rstrip("/")
        self.nome = modelo            # tem de ser o mesmo modelo gravado em monitor.chunk.modelo
        self.dim = dim
        self.timeout_s = timeout_s
        self.http = session or requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"

    def encode(self, textos: list[str]) -> list[list[float]]:
        import requests

        try:
            r = self.http.post(f"{self.url}/embed", json={"textos": textos}, timeout=self.timeout_s)
        except requests.Timeout as e:
            raise EmbedderIndisponivel("o serviço de embeddings não respondeu a tempo "
                                       "(pode estar iniciando; tente de novo em instantes)") from e
        except requests.RequestException as e:
            raise EmbedderIndisponivel(f"falha ao contatar o serviço de embeddings ({type(e).__name__})") from e
        if r.status_code in (401, 403):
            raise EmbedderIndisponivel("o serviço de embeddings recusou o token (EMBEDDINGS_REMOTO_TOKEN)")
        if r.status_code >= 400:
            raise EmbedderIndisponivel(f"o serviço de embeddings respondeu {r.status_code}")
        try:
            d = r.json()
        except ValueError as e:
            raise EmbedderIndisponivel("resposta do serviço de embeddings não é JSON") from e
        if d.get("modelo") != self.nome:
            # vetores de modelos diferentes não são comparáveis: melhor não buscar do que buscar errado
            raise EmbedderIndisponivel(f"o serviço usa o modelo {d.get('modelo')!r}, mas os trechos foram "
                                       f"gerados com {self.nome!r} (confira EMBEDDINGS_MODELO)")
        vetores = d.get("vetores")
        if not isinstance(vetores, list) or len(vetores) != len(textos) \
                or any(not isinstance(v, list) or len(v) != self.dim for v in vetores):
            raise EmbedderIndisponivel(f"o serviço de embeddings não devolveu {len(textos)} vetor(es) "
                                       f"de {self.dim} dimensões")
        return [[float(x) for x in v] for v in vetores]
