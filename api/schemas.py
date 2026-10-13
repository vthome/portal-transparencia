"""Modelos de entrada/saída (aparecem no Swagger em /docs)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

StatusCriterio = Literal["atende", "parcial", "nao_atende", "nao_aplicavel", "nao_verificavel"]


class Health(BaseModel):
    status: Literal["ok", "degradado"]
    banco: str
    versao: str
    ultima_rodada: int | None = None
    avaliacoes: int | None = None
    schema_v03: bool | None = None
    busca: str


class Avaliacao(BaseModel):
    avaliacao_id: int
    run_id: int
    avaliado_em: datetime | None = None
    score: float | None = Field(None, description="0–100, só com quesitos verificados")
    score_min: float | None = Field(None, description="não verificáveis valendo 0")
    score_max: float | None = Field(None, description="não verificáveis valendo 1")
    cobertura: float | None = Field(None, description="% do peso automatizado que pôde ser verificado")
    essenciais_ok: bool | None = None
    classificacao: str | None = None
    dimensoes: dict[str, float | None] = {}
    contagem: dict[str, int] = {}
    essenciais_reprovados: list[str] = []
    engine_version: str | None = None
    modelo_embedding: str | None = None


class ScoreRevisado(BaseModel):
    score: float | None
    score_min: float | None
    score_max: float | None
    cobertura: float | None
    essenciais_ok: bool | None
    classificacao: str
    dimensoes: dict[str, float | None]
    contagem: dict[str, int]
    essenciais_reprovados: list[str]


class Coleta(BaseModel):
    run_id: int | None = None
    status: str | None = None
    paginas: int | None = None
    arquivos: int | None = None
    finalizado_em: datetime | None = None


class EnteResumo(BaseModel):
    slug: str
    nome: str
    municipio: str
    uf: str
    cod_ibge: str | None = None
    tipo_entidade: str
    poder: str
    esfera: str
    avaliacao: Avaliacao | None = None
    coleta: Coleta | None = None


class Url(BaseModel):
    tipo: str
    url: str
    plataforma: str | None = None
    fonte: str | None = None
    dominio_gov: bool | None = None
    valido_de: datetime | None = None


class EnteDetalhe(EnteResumo):
    urls: list[Url] = []
    score_revisado: ScoreRevisado | None = None
    revisoes: int = 0


class ItemRanking(Avaliacao):
    slug: str
    nome: str
    valor: float | None = Field(None, description="score geral ou da dimensão pedida")
    posicao: int | None = Field(None, description="nula para avaliação incompleta")
    no_ranking: bool


class ResultadoItem(BaseModel):
    id: int
    codigo: str
    bloco: str
    dimensao: str
    matriz: str
    texto: str
    peso: int
    essencial: bool
    verificador: str
    status: StatusCriterio
    nota: float | None = None
    confianca: float | None = None
    metodo: str
    justificativa: str | None = None
    n_evidencias: int = 0
    status_revisado: StatusCriterio | None = None
    revisor: str | None = None
    revisado_em: datetime | None = None
    status_final: StatusCriterio
    nota_final: float | None = None
    revisado: bool
    precisa_revisao: bool


class Evidencia(BaseModel):
    id: int
    tipo: str
    url: str | None = None
    page_snapshot_id: int | None = None
    file_artifact_id: int | None = None
    trecho: str | None = None
    screenshot_path: str | None = None
    metadados: dict[str, Any] | None = None
    pagina_coletada_em: datetime | None = None
    pagina_titulo: str | None = None
    arquivo_extensao: str | None = None
    tem_copia: bool = False


class Revisao(BaseModel):
    id: int
    resultado_id: int
    revisor: str
    status_corrigido: StatusCriterio
    comentario: str | None = None
    revisado_em: datetime


class ResultadoDetalhe(BaseModel):
    id: int
    avaliacao_id: int
    run_id: int
    slug: str
    nome: str
    codigo: str
    bloco: str
    dimensao: str
    matriz: str
    texto: str
    fundamentacao: str | None = None
    peso: int
    essencial: bool
    verificador: str
    status: StatusCriterio
    nota: float | None = None
    confianca: float | None = None
    metodo: str
    checker_version: str
    justificativa: str | None = None
    sinais: dict[str, Any] = {}
    status_revisado: StatusCriterio | None = None
    status_final: StatusCriterio
    evidencias: list[Evidencia] = []
    revisoes: list[dict[str, Any]] = []


class RevisaoIn(BaseModel):
    resultado_id: int = Field(..., description="criterion_result.id (veja /entes/{slug}/resultados ou /revisoes/fila)")
    status_corrigido: StatusCriterio
    revisor: str = Field(..., min_length=2, max_length=120)
    comentario: str | None = Field(None, max_length=2000)

    model_config = {"json_schema_extra": {"examples": [
        {"resultado_id": 123, "status_corrigido": "atende", "revisor": "Ana (MPRJ)",
         "comentario": "Despesas publicadas em /despesas com empenho, liquidação e pagamento."}]}}


class RevisaoOut(BaseModel):
    revisao: Revisao
    concordou_com_motor: bool
    score_revisado: ScoreRevisado


class LinkArquivo(BaseModel):
    url: str
    tipo: Literal["screenshot", "html", "arquivo"]
    chave: str
    expira_em_s: int


class ItemBusca(BaseModel):
    trecho_id: int
    slug: str
    run_id: int
    page_snapshot_id: int | None = None
    file_artifact_id: int | None = None
    url: str | None = None
    titulo: str | None = None
    texto: str
    relevancia: float


class Busca(BaseModel):
    consulta: str
    modo: Literal["semantica", "texto"]
    modelo: str | None = None
    resultados: list[ItemBusca]
