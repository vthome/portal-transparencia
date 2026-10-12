"""Contratos de dados da Camada 1. Tudo que sai daqui é serializável em JSON."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

UrlTipo = Literal["site_oficial", "portal_transparencia", "esic", "ouvidoria", "diario_oficial",
                  "dados_abertos", "lgpd", "outro"]

# Status de coleta de uma página. A Camada 2 converte tudo que não é "ok"/"nao_modificado"
# em `nao_verificavel` — nunca em `nao_atende`. Falha do robô não é falha do ente.
ColetaStatus = Literal["ok", "vazio", "soft404", "erro", "bloqueado_robots", "nao_modificado"]

JobStatus = Literal["em_execucao", "concluido", "parcial", "falhou"]


def agora() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class EntityUrl:
    tipo: UrlTipo
    url: str
    fonte: Literal["config", "descoberta"]
    plataforma: str | None = None
    dominio_gov: bool | None = None
    evidencia: dict[str, Any] = field(default_factory=dict)


@dataclass
class Entity:
    id: str
    nome: str
    municipio: str
    uf: str
    tipo_entidade: str
    poder: str
    esfera: str
    codigo_ibge: str | None
    urls: dict[str, str]
    dominios_extra: list[str] = field(default_factory=list)
    robots_override: bool = False
    robots_justificativa: str | None = None
    verificar_tls: bool = True


@dataclass
class FetchResult:
    """Resultado de uma requisição. Nunca lança exceção: erros viram `erro`."""
    url: str
    url_final: str | None = None
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    content_type: str | None = None
    erro: str | None = None            # timeout, dns, conexao, tls, http_4xx, http_5xx, bloqueado_robots, muito_grande, circuito_aberto
    detalhe_erro: str | None = None
    truncado: bool = False
    robots_override: bool = False
    tentativas: int = 0
    duracao_s: float = 0.0
    renderizado_js: bool = False
    screenshot: bytes | None = None

    @property
    def ok(self) -> bool:
        return self.erro is None and self.status is not None and 200 <= self.status < 300

    @property
    def nao_modificado(self) -> bool:
        return self.status == 304

    @property
    def texto(self) -> str:
        from ingestion.transform.normalize import decode_bytes

        return decode_bytes(self.content, self.content_type)


@dataclass
class Fingerprint:
    plataforma: str | None
    pontuacao: int
    sinais: list[str]
    requires_js: bool
    spa_detectada: bool
    wait_selector: str | None = None


@dataclass
class PageSnapshot:
    url: str
    url_final: str | None
    http_status: int | None
    content_type: str | None
    content_hash: str | None
    tamanho: int
    titulo: str | None
    coleta_status: ColetaStatus
    erro: str | None
    renderizado_js: bool
    robots_override: bool
    profundidade: int
    trilha: list[str]
    secoes: list[str]
    extraido: dict[str, Any]
    storage_path: str | None = None
    screenshot_path: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    inalterado: bool = False
    coletado_em: datetime = field(default_factory=agora)
    id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["coletado_em"] = self.coletado_em.isoformat()
        return d


@dataclass
class FileArtifact:
    url: str
    texto_link: str | None
    mime: str | None
    extensao: str | None
    sha256: str | None
    tamanho: int
    storage_path: str | None
    paginas: int | None
    texto_extraido: str | None
    metadados: dict[str, Any]
    secoes: list[str]
    erro: str | None = None
    coletado_em: datetime = field(default_factory=agora)
    page_url: str | None = None   # página onde o link foi encontrado (para amarrar ao snapshot)
    id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["coletado_em"] = self.coletado_em.isoformat()
        return d


@dataclass
class CrawlStats:
    status: JobStatus = "em_execucao"
    erro: str | None = None
    plataforma_detectada: str | None = None
    fingerprint: dict[str, Any] = field(default_factory=dict)
    paginas: int = 0
    arquivos: int = 0
    requisicoes: int = 0
    renderizacoes: int = 0
    bytes: int = 0
    paginas_inalteradas: int = 0
    bloqueios_robots: int = 0
    secoes_encontradas: list[str] = field(default_factory=list)
    secoes_ausentes: list[str] = field(default_factory=list)
    duracao_s: float = 0.0
