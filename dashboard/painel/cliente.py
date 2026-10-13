"""Cliente HTTP da API do monitor. Uma função por rota; erros viram exceções com mensagem em português."""
from __future__ import annotations

from typing import Any

import requests


class ApiErro(Exception):
    def __init__(self, status: int, detalhe: str) -> None:
        super().__init__(f"{status}: {detalhe}")
        self.status = status
        self.detalhe = detalhe


class ApiIndisponivel(ApiErro):
    """Sem resposta (rede, timeout) ou 502/503/504: API dormindo, fora do ar ou banco indisponível."""


class ApiCliente:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 90.0, session=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.http = session or requests.Session()

    # ------------------------------------------------------------ transporte
    def _req(self, metodo: str, caminho: str, params: dict | None = None, json: Any = None,
             auth: bool = False) -> Any:
        headers = {"X-API-Key": self.token} if (auth and self.token) else {}
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            r = self.http.request(metodo, self.base_url + caminho, params=params, json=json, headers=headers,
                                  timeout=self.timeout)
        except requests.RequestException as e:
            raise ApiIndisponivel(0, f"sem resposta da API ({type(e).__name__})") from e
        if r.status_code >= 400:
            try:
                det = r.json().get("detail")
                det = det if isinstance(det, str) else "; ".join(
                    f"{'.'.join(map(str, x.get('loc', [])[1:]))}: {x.get('msg')}" for x in det or [])
            except Exception:
                det = r.text[:200]
            cls = ApiIndisponivel if r.status_code in (502, 503, 504) else ApiErro
            raise cls(r.status_code, det or "erro")
        return r.json()

    def _get(self, caminho: str, **params) -> Any:
        return self._req("GET", caminho, params=params)

    # ------------------------------------------------------------ rotas
    def health(self) -> dict:
        return self._get("/health")

    def entes(self) -> list[dict]:
        return self._get("/entes")

    def ente(self, slug: str) -> dict:
        return self._get(f"/entes/{slug}")

    def ranking(self, dimensao: str | None = None) -> list[dict]:
        return self._get("/indicadores/ranking", dimensao=dimensao)

    def matriz_dimensoes(self) -> dict:
        return self._get("/indicadores/dimensoes")

    def rotulos_dimensoes(self) -> dict[str, str]:
        return self._get("/indicadores/dimensoes/rotulos")

    def status_por_ente(self) -> list[dict]:
        return self._get("/indicadores/status")

    def historico(self, slug: str) -> list[dict]:
        return self._get(f"/entes/{slug}/historico")

    def resultados(self, slug: str, run_id: int | None = None) -> list[dict]:
        return self._get(f"/entes/{slug}/resultados", run_id=run_id)

    def diff(self, slug: str, de: int | None = None, para: int | None = None) -> dict:
        return self._get(f"/entes/{slug}/diff", de=de, para=para)

    def timeline(self, slug: str, codigo: str) -> list[dict]:
        return self._get(f"/entes/{slug}/quesitos/{codigo}/timeline")

    def resultado(self, resultado_id: int) -> dict:
        return self._get(f"/resultados/{resultado_id}")

    def link_evidencia(self, evidencia_id: int, tipo: str | None = None) -> dict:
        return self._get(f"/evidencias/{evidencia_id}/arquivo", tipo=tipo)

    def fila(self, ente: str | None = None, incluir_nao_verificaveis: bool = True, limite: int = 200) -> list[dict]:
        return self._get("/revisoes/fila", ente=ente, incluir_nao_verificaveis=str(incluir_nao_verificaveis).lower(),
                         limite=limite)

    def revisoes(self, ente: str | None = None, limite: int = 200) -> list[dict]:
        return self._get("/revisoes", ente=ente, limite=limite)

    def concordancia(self) -> dict:
        return self._get("/revisoes/concordancia")

    def revisar(self, resultado_id: int, status_corrigido: str, revisor: str, comentario: str | None) -> dict:
        if not self.token:
            raise ApiErro(401, "este dashboard não tem API_TOKEN_REVISAO configurado")
        return self._req("POST", "/revisoes", json={"resultado_id": resultado_id, "status_corrigido": status_corrigido,
                                                    "revisor": revisor, "comentario": comentario or None}, auth=True)

    def busca(self, q: str, ente: str | None = None, k: int = 10, modo: str = "auto") -> dict:
        return self._get("/busca", q=q, ente=ente, k=k, modo=modo)

    def rodadas(self, limite: int = 20) -> list[dict]:
        return self._get("/rodadas", limite=limite)

    def rodada(self, run_id: int) -> list[dict]:
        return self._get(f"/rodadas/{run_id}")

    def checklist(self) -> list[dict]:
        return self._get("/checklist")
