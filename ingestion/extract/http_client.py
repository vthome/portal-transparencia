"""Cliente HTTP "educado" e resiliente para portais públicos.

Garantias:
- nunca lança exceção para o chamador: todo problema vira `FetchResult.erro`
- robots.txt respeitado por padrão; override só por ente e sempre registrado
- taxa por domínio (token bucket simples) — paralelismo é ENTRE domínios, não dentro
- retry com backoff exponencial só para falhas transitórias (timeout, conexão, 429, 5xx)
- circuit breaker por domínio: após N falhas seguidas o domínio é pulado na rodada
- teto de bytes por resposta (download em streaming)
- GET condicional (ETag / Last-Modified) para coleta incremental

Thread-safe: um mesmo cliente pode ser usado por várias threads (um ente por thread).
"""
from __future__ import annotations

import threading
import time
import urllib.robotparser
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

import requests

from ingestion.models import FetchResult
from ingestion.settings import Settings, get_settings

TRANSITORIOS = {429, 500, 502, 503, 504}

@dataclass
class _DomainState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    proximo_slot: float = 0.0
    falhas_seguidas: int = 0
    aberto: bool = False

class HttpClient:
    def __init__(
        self,
        settings: Settings | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        limite_circuito: int = 5,
    ) -> None:
        self.s = settings or get_settings()
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": self.s.user_agent,
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/csv,*/*;q=0.8",
        })
        self._sleep, self._clock = sleep, clock
        self._limite_circuito = limite_circuito
        self._dominios: dict[str, _DomainState] = defaultdict(_DomainState)
        self._dominios_lock = threading.Lock()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()
        self.contador = 0                 # requisições efetivamente enviadas
        self._contador_lock = threading.Lock()

    # ------------------------------------------------------------ API pública

    def fetch(
        self,
        url: str,
        *,
        robots_override: bool = False,
        verify_tls: bool = True,
        etag: str | None = None,
        last_modified: str | None = None,
        max_bytes: int | None = None,
    ) -> FetchResult:
        t0 = self._clock()
        res = FetchResult(url=url)
        host = (urlsplit(url).hostname or "").lower()
        dom = self._state(host)

        if dom.aberto:
            res.erro, res.detalhe_erro = "circuito_aberto", f"{host}: muitas falhas seguidas nesta rodada"
            return res

        permitido = self.robots_allowed(url, verify_tls=verify_tls)
        if not permitido:
            if not robots_override:
                res.erro, res.detalhe_erro = "bloqueado_robots", "robots.txt não permite este caminho"
                return res
            res.robots_override = True

        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        limite = max_bytes or self.s.max_bytes
        for tentativa in range(1, self.s.tentativas + 1):
            res.tentativas = tentativa
            self._throttle(dom)
            erro, detalhe, status = self._get_once(url, res, headers, verify_tls, limite)
            transitorio = erro in {"timeout", "conexao"} or status in TRANSITORIOS
            if not transitorio:
                break
            if tentativa < self.s.tentativas:
                espera = self._retry_after(res) or (2 ** (tentativa - 1))
                self._sleep(min(espera, 60))

        res.erro, res.detalhe_erro = erro, detalhe
        self._registrar_resultado(dom, res)
        res.duracao_s = round(self._clock() - t0, 3)
        return res

    def robots_allowed(self, url: str, verify_tls: bool = True) -> bool:
        parts = urlsplit(url)
        origem = f"{parts.scheme}://{parts.netloc}"
        with self._robots_lock:
            if origem not in self._robots:
                self._robots[origem] = self._load_robots(origem, verify_tls)
            rp = self._robots[origem]
        if rp is None:  # sem robots.txt (404) ou ilegível => permitido
            return True
        return rp.can_fetch(self.s.user_agent, url)

    def robots_txt_status(self, url: str) -> str:
        parts = urlsplit(url)
        rp = self._robots.get(f"{parts.scheme}://{parts.netloc}")
        return "ausente" if rp is None else "presente"

    # ------------------------------------------------------------ internos

    def _get_once(self, url, res: FetchResult, headers, verify_tls, limite):
        self._count()
        try:
            with self.session.get(url, headers=headers, timeout=self.s.timeout_s, stream=True,
                                  verify=verify_tls, allow_redirects=True) as r:
                res.status = r.status_code
                res.url_final = r.url
                res.headers = {k.lower(): v for k, v in r.headers.items()}
                res.content_type = res.headers.get("content-type")
                declarado = int(res.headers.get("content-length") or 0)
                if declarado and declarado > limite:
                    res.content, res.truncado = b"", True
                    return "muito_grande", f"content-length {declarado} > {limite}", r.status_code
                buf = bytearray()
                for chunk in r.iter_content(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > limite:
                        res.truncado = True
                        break
                res.content = bytes(buf[:limite])
                if res.truncado:
                    return "muito_grande", f"corpo excedeu {limite} bytes", r.status_code
                if r.status_code == 304:
                    return None, None, 304
                if 400 <= r.status_code < 500:
                    return f"http_{r.status_code}", r.reason, r.status_code
                if r.status_code >= 500:
                    return f"http_{r.status_code}", r.reason, r.status_code
                return None, None, r.status_code
        except requests.exceptions.SSLError as e:
            return "tls", str(e)[:300], None
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as e:
            return "timeout", str(e)[:300], None
        except requests.exceptions.ConnectionError as e:
            msg = str(e)
            kind = "dns" if ("Name or service not known" in msg or "getaddrinfo" in msg
                             or "NameResolution" in msg) else "conexao"
            return kind, msg[:300], None
        except requests.exceptions.RequestException as e:
            return "conexao", str(e)[:300], None

    def _load_robots(self, origem: str, verify_tls: bool):
        res = FetchResult(url=f"{origem}/robots.txt")
        dom = self._state((urlsplit(origem).hostname or "").lower())
        self._throttle(dom)
        erro, _, status = self._get_once(res.url, res, {}, verify_tls, 512 * 1024)
        if erro or status != 200:
            # RFC 9309: 4xx => sem restrição; 5xx/indisponível => também tratamos como sem
            # restrição na PoC, mas o job registra que o robots.txt não pôde ser lido.
            return None
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(res.texto.splitlines())
        return rp

    def _state(self, host: str) -> _DomainState:
        with self._dominios_lock:
            return self._dominios[host]

    def _throttle(self, dom: _DomainState) -> None:
        intervalo = 1.0 / max(self.s.req_por_segundo, 0.01)
        with dom.lock:
            agora = self._clock()
            espera = dom.proximo_slot - agora
            dom.proximo_slot = max(agora, dom.proximo_slot) + intervalo
        if espera > 0:
            self._sleep(espera)

    def _registrar_resultado(self, dom: _DomainState, res: FetchResult) -> None:
        falha_servidor = res.erro in {"timeout", "conexao", "dns", "tls"} or (
            res.status is not None and res.status >= 500)
        with dom.lock:
            dom.falhas_seguidas = dom.falhas_seguidas + 1 if falha_servidor else 0
            if dom.falhas_seguidas >= self._limite_circuito:
                dom.aberto = True

    @staticmethod
    def _retry_after(res: FetchResult) -> float | None:
        v = res.headers.get("retry-after")
        try:
            return float(v) if v else None
        except ValueError:
            return None

    def _count(self) -> None:
        with self._contador_lock:
            self.contador += 1
