"""Gera embeddings dos snapshots de uma rodada e grava em monitor.chunk (pgvector).

    python -m ingestion.embeddings.generate_embeddings            # última rodada
    python -m ingestion.embeddings.generate_embeddings --run 4

Custo controlado por três mecanismos:
1. idempotência: snapshot que já tem trechos daquele modelo não é reprocessado
2. reuso entre rodadas: o vetor é identificado por sha256(modelo + texto); página que
   não mudou reaproveita o vetor da rodada anterior sem passar pelo modelo
3. tetos de trechos por página/arquivo (prepare_documents)

Embedders:
- SentenceTransformerEmbedder: o modelo real (padrão SERAFIM 100M, 768 dimensões)
- HashEmbedder: determinístico, sem rede nem GPU; usado nos testes e com --embedder hash
  (similaridade por sobreposição de palavras: serve para validar o encanamento, não a qualidade)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sys
import time
from typing import Any, Protocol

from ingestion.settings import Settings, get_settings
from ingestion.transform.normalize import normalize_text
from ingestion.transform.prepare_documents import Trecho, preparar

log = logging.getLogger("embeddings")


class Embedder(Protocol):
    nome: str
    dim: int

    def encode(self, textos: list[str]) -> list[list[float]]: ...


class DimensaoIncompativel(SystemExit):
    pass


class SentenceTransformerEmbedder:
    def __init__(self, modelo: str, dim_esperada: int, lote: int = 32, device: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover - depende do ambiente
            raise SystemExit("sentence-transformers não instalado: pip install -r requirements-ml.txt") from e
        self.nome = modelo
        self._m = SentenceTransformer(modelo, device=device)
        # sentence-transformers >= 5 renomeou o método; mantém compatibilidade com as versões 3/4
        f = getattr(self._m, "get_embedding_dimension", None) or self._m.get_sentence_embedding_dimension
        self.dim = int(f())
        if self.dim != dim_esperada:
            raise DimensaoIncompativel(
                f"o modelo {modelo} gera vetores de {self.dim} dimensões, mas EMBEDDINGS_DIM={dim_esperada}. "
                "Ajuste EMBEDDINGS_DIM e a coluna monitor.chunk.embedding (ver docs/02-conclusao.md).")
        self.lote = lote

    def encode(self, textos: list[str]) -> list[list[float]]:
        vs = self._m.encode(textos, batch_size=self.lote, normalize_embeddings=True, show_progress_bar=False)
        return [list(map(float, v)) for v in vs]


class HashEmbedder:
    """Bag-of-words com hashing + normalização L2. Determinístico e sem dependências."""

    def __init__(self, dim: int = 768) -> None:
        self.nome = f"hash-bow-{dim}"
        self.dim = dim

    def encode(self, textos: list[str]) -> list[list[float]]:
        out = []
        for t in textos:
            v = [0.0] * self.dim
            for tok in normalize_text(t).split():
                if len(tok) < 3:
                    continue
                h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "little")
                v[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def criar_embedder(s: Settings, tipo: str = "modelo") -> Embedder:
    if tipo == "hash":
        return HashEmbedder(s.embeddings_dim)
    return SentenceTransformerEmbedder(s.embeddings_modelo, s.embeddings_dim, s.embeddings_lote,
                                       s.embeddings_device)


def _por_origem(trechos: list[Trecho]) -> list[list[Trecho]]:
    """Agrupa os trechos por página/arquivo, na ordem: cada grupo é gravado inteiro ou não é gravado."""
    grupos: dict[tuple, list[Trecho]] = {}
    for t in trechos:
        grupos.setdefault((t.page_snapshot_id, t.file_artifact_id), []).append(t)
    return list(grupos.values())


def gerar(repo, run_id: str, embedder: Embedder, lote_gravacao: int = 256) -> dict[str, Any]:
    """Cria os trechos que faltam para a rodada e grava os vetores. Devolve contadores.

    Grava em lotes de ~`lote_gravacao` trechos, sempre com páginas/arquivos completos: se o processo
    for interrompido, o que já foi gravado fica e a próxima execução continua de onde parou
    (snapshots que já têm trechos do modelo não são reprocessados).
    """
    dim_banco = repo.dimensao_embedding()
    if dim_banco is not None and dim_banco != embedder.dim:
        raise DimensaoIncompativel(
            f"monitor.chunk.embedding tem {dim_banco} dimensões e o embedder '{embedder.nome}' gera {embedder.dim}. "
            "Rode o sql/schema.sql v0.3 (converte para 768 com a tabela vazia) ou ajuste EMBEDDINGS_DIM.")
    log.info("lendo snapshots da rodada %s sem trechos de %s...", run_id, embedder.nome)
    paginas, arquivos = repo.snapshots_para_embedding(run_id, embedder.nome)
    trechos: list[Trecho] = preparar(paginas, arquivos)
    resumo = {"run_id": run_id, "modelo": embedder.nome, "paginas": len(paginas), "arquivos": len(arquivos),
              "trechos": len(trechos), "reaproveitados": 0, "calculados": 0}
    if not trechos:
        log.info("nada a fazer: todos os snapshots da rodada já têm trechos de %s", embedder.nome)
        return resumo
    hashes_todos = sorted({t.hash(embedder.nome) for t in trechos})
    existentes = repo.vetores_existentes(embedder.nome, hashes_todos)
    a_calcular = len([h for h in hashes_todos if h not in existentes])
    log.info("%d páginas e %d arquivos → %d trechos; %d vetores reaproveitados de rodadas anteriores, "
             "%d a calcular", len(paginas), len(arquivos), len(trechos), len(hashes_todos) - a_calcular, a_calcular)

    calculados: dict[str, list[float]] = {}
    gravados, t0 = 0, time.monotonic()
    grupos, pendentes = _por_origem(trechos), []

    def descarregar(bloco: list[Trecho]) -> None:
        nonlocal gravados
        hs = [t.hash(embedder.nome) for t in bloco]
        novos = list(dict.fromkeys(h for h in hs if h not in existentes and h not in calculados))
        if novos:
            texto_de = {h: t.texto for t, h in zip(bloco, hs)}
            for h, v in zip(novos, embedder.encode([texto_de[h] for h in novos])):
                calculados[h] = v
        repo.salvar_chunks([{"page_snapshot_id": t.page_snapshot_id, "file_artifact_id": t.file_artifact_id,
                             "ordem": t.ordem, "texto": t.texto, "modelo": embedder.nome, "texto_hash": h,
                             "embedding": existentes.get(h) or calculados[h]} for t, h in zip(bloco, hs)])
        gravados += len(bloco)
        dt = time.monotonic() - t0
        ritmo = len(calculados) / dt if dt > 0 else 0
        falta = a_calcular - len(calculados)
        eta = f", faltam ~{falta / ritmo / 60:.0f} min" if ritmo > 0 and falta > 0 else ""
        log.info("embeddings: %d/%d trechos gravados · %d calculados (%.1f/s)%s",
                 gravados, len(trechos), len(calculados), ritmo, eta)

    for g in grupos:
        pendentes += g
        if len(pendentes) >= lote_gravacao:
            descarregar(pendentes)
            pendentes = []
    if pendentes:
        descarregar(pendentes)
    resumo["reaproveitados"] = sum(1 for t in trechos if t.hash(embedder.nome) in existentes)
    resumo["calculados"] = len(calculados)
    return resumo


def vetor_para_texto(v: list[float]) -> str:
    """Formato aceito pelo pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def texto_para_vetor(s: str | list) -> list[float]:
    if isinstance(s, list):
        return [float(x) for x in s]
    return [float(x) for x in json.loads(re.sub(r"\s+", "", s))]


def main(argv: list[str] | None = None) -> int:
    from compliance.repo import build_repo

    ap = argparse.ArgumentParser(description="Embeddings dos snapshots de uma rodada")
    ap.add_argument("--run")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--embedder", choices=["modelo", "hash"], default="modelo")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    repo = build_repo(s, a.local)
    run_id = a.run or repo.latest_run_id()
    if not run_id:
        print("nenhuma rodada encontrada", file=sys.stderr)
        return 1
    print(json.dumps(gerar(repo, str(run_id), criar_embedder(s, a.embedder)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
