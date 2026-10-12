"""Validação de configuração (YAML) e de qualidade da coleta.

Dois papéis distintos:
1. `load_entities` / `load_platforms` / `load_secoes`: falham cedo e com mensagem clara se o
   YAML estiver errado — antes de qualquer requisição.
2. `classify_page`: decide se o que veio do servidor serve como evidência
   (ok / vazio / soft404). Uma página "Em construção" com HTTP 200 não pode virar "Atende".
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
)

from ingestion.models import Entity
from ingestion.settings import CONFIG_DIR
from ingestion.transform.normalize import normalize_text

URL_TIPOS = {"site_oficial", "portal_transparencia", "esic", "ouvidoria", "dados_abertos", "outro"}


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- municipios.yaml

class _Override(BaseModel):
    ativo: bool = False
    justificativa: str | None = None


class _EnteCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    nome: str
    municipio: str
    codigo_ibge: str | None = Field(default=None, pattern=r"^\d{7}$")
    uf: str | None = None
    tipo_entidade: str | None = None
    poder: str | None = None
    esfera: str | None = None
    urls: dict[str, HttpUrl]
    dominios_extra: list[str] = []
    robots_override: _Override = _Override()
    verificar_tls: bool = True
    ativo: bool = True

    @field_validator("urls")
    @classmethod
    def _tipos(cls, v: dict[str, Any]) -> dict[str, Any]:
        invalid = set(v) - URL_TIPOS
        if invalid:
            raise ValueError(f"tipos de URL inválidos: {sorted(invalid)}; use {sorted(URL_TIPOS)}")
        if "site_oficial" not in v and "portal_transparencia" not in v:
            raise ValueError("informe ao menos site_oficial ou portal_transparencia")
        return v


class _MunicipiosCfg(BaseModel):
    padroes: dict[str, str] = {}
    entes: list[_EnteCfg]


def load_entities(path: Path | None = None, only: list[str] | None = None) -> list[Entity]:
    path = path or CONFIG_DIR / "municipios.yaml"
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    try:
        cfg = _MunicipiosCfg.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path}: configuração inválida\n{e}") from e

    ids = [e.id for e in cfg.entes]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ConfigError(f"{path}: ids duplicados: {sorted(dup)}")
    if only:
        unknown = set(only) - set(ids)
        if unknown:
            raise ConfigError(f"entes desconhecidos: {sorted(unknown)}; disponíveis: {ids}")

    p = cfg.padroes
    out = []
    for e in cfg.entes:
        if not e.ativo or (only and e.id not in only):
            continue
        if e.robots_override.ativo and not (e.robots_override.justificativa or "").strip():
            raise ConfigError(f"{e.id}: robots_override ativo exige justificativa")
        out.append(Entity(
            id=e.id, nome=e.nome, municipio=e.municipio,
            uf=e.uf or p.get("uf", "RJ"),
            tipo_entidade=e.tipo_entidade or p.get("tipo_entidade", "prefeitura"),
            poder=e.poder or p.get("poder", "executivo"),
            esfera=e.esfera or p.get("esfera", "municipal"),
            codigo_ibge=e.codigo_ibge,
            urls={k: str(v) for k, v in e.urls.items()},
            dominios_extra=e.dominios_extra,
            robots_override=e.robots_override.ativo,
            robots_justificativa=e.robots_override.justificativa,
            verificar_tls=e.verificar_tls,
        ))
    return out


def load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


def load_platforms() -> dict[str, Any]:
    cfg = load_yaml("platforms.yaml")
    for nome, p in cfg["plataformas"].items():
        for s in p.get("sinais", []):
            if s["tipo"] not in {"host", "url", "html", "header", "meta_generator"}:
                raise ConfigError(f"platforms.yaml: {nome}: tipo de sinal inválido {s['tipo']}")
            pattern = s["valor"].split(":", 1)[1].strip() if s["tipo"] == "header" else s["valor"]
            try:
                re.compile(pattern)
            except re.error as err:
                raise ConfigError(f"platforms.yaml: {nome}: regex inválida {s['valor']!r}: {err}") from err
    return cfg


def load_secoes() -> dict[str, Any]:
    cfg = load_yaml("secoes.yaml")
    for nome, s in cfg["secoes"].items():
        if not s.get("termos"):
            raise ConfigError(f"secoes.yaml: seção {nome} sem termos")
    return cfg


# ---------------------------------------------------------------- qualidade da página

_SOFT404 = [
    "pagina nao encontrada", "pagina nao existe", "404 not found", "not found", "erro 404",
    "em construcao", "em manutencao", "conteudo indisponivel", "nenhum registro encontrado",
    "page not found", "acesso negado", "access denied",
]
MIN_TEXTO_UTIL = 200  # caracteres de texto visível abaixo dos quais a página é considerada vazia


def classify_page(texto_visivel: str, titulo: str | None, http_status: int | None) -> str:
    """ok | vazio | soft404 — aplicado só a respostas 2xx."""
    t = normalize_text(texto_visivel)
    head = normalize_text(titulo) + " " + t[:400]
    if any(p in head for p in _SOFT404) and len(t) < 2000:
        return "soft404"
    if len(t) < MIN_TEXTO_UTIL:
        return "vazio"
    return "ok"
