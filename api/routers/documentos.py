"""Documentos e evidências: links temporários para cópias guardadas e busca nos trechos coletados."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.config import ApiSettings
from api.deps import exige_leitura, get_assinador, get_embedder, get_repo, get_settings
from api.schemas import Busca, LinkArquivo

router = APIRouter(tags=["documentos"], dependencies=[Depends(exige_leitura)])
log = logging.getLogger("api.busca")


@router.get("/evidencias/{evidencia_id}/arquivo", response_model=LinkArquivo,
            summary="Link temporário para a cópia guardada da evidência",
            description="O bucket é privado; a API assina um link que expira (API_LINK_EXPIRA_S, padrão 1 h). "
                        "`tipo`: screenshot da página, HTML coletado ou arquivo baixado (PDF, CSV...).")
def arquivo_da_evidencia(evidencia_id: int,
                         tipo: Literal["screenshot", "html", "arquivo"] | None = Query(
                             None, description="padrão: arquivo > screenshot > html"),
                         repo=Depends(get_repo), assinador=Depends(get_assinador)):
    x = repo.copias_da_evidencia(evidencia_id)
    if x is None:
        raise HTTPException(404, "evidência não encontrada")
    opcoes = {"arquivo": x.get("arquivo"), "screenshot": x.get("screenshot"), "html": x.get("html")}
    ordem = [tipo] if tipo else ["arquivo", "screenshot", "html"]
    escolhido = next((t for t in ordem if opcoes.get(t)), None)
    if escolhido is None:
        raise HTTPException(404, f"a evidência não tem cópia guardada{' do tipo ' + tipo if tipo else ''}")
    chave = opcoes[escolhido]
    return {"url": assinador.assinar(chave), "tipo": escolhido, "chave": chave, "expira_em_s": assinador.expira}


@router.get("/busca", response_model=Busca, summary="Busca nos trechos da coleta mais recente de cada ente",
            description="Modo **semântico** (pgvector, mesmo modelo dos embeddings) quando a API tem como vetorizar "
                        "a consulta: com o modelo carregado (`API_BUSCA=modelo`) ou pelo serviço externo de "
                        "embeddings (`API_BUSCA=remoto`). Senão, **texto** (full-text em português do Postgres, sem "
                        "diferenciar acentos nem maiúsculas: `orcamento` encontra `Orçamento`). Se a busca por texto "
                        "não achar nada, a API tenta a busca **aproximada** (trigramas), que tolera erros de "
                        "digitação. O campo `modo` informa qual foi usada; `aviso` explica quando a busca semântica "
                        "estava indisponível e a resposta veio da busca por palavras.")
def busca(q: str = Query(..., min_length=3, max_length=300, description="ex.: lista de espera das creches"),
          slug: str | None = Query(None, alias="ente"), k: int = Query(10, ge=1, le=50),
          modo: Literal["auto", "semantica", "texto"] = "auto",
          repo=Depends(get_repo), embedder=Depends(get_embedder),
          s: ApiSettings = Depends(get_settings)):
    if modo == "semantica" and embedder is None:
        raise HTTPException(409, "busca semântica desligada neste servidor (API_BUSCA=texto)")
    aviso = None
    if embedder is not None and modo != "texto":
        from api.embedder_remoto import EmbedderIndisponivel
        from ingestion.embeddings.generate_embeddings import vetor_para_texto

        try:
            vetor = vetor_para_texto(embedder.encode([q])[0])
        except EmbedderIndisponivel as e:
            if modo == "semantica":
                raise HTTPException(503, f"busca semântica indisponível agora: {e}") from e
            log.warning("busca semântica indisponível, usando a busca por palavras: %s", e)
            aviso = f"A busca por significado está indisponível agora ({e}). Mostrando a busca por palavras."
        else:
            return {"consulta": q, "modo": "semantica", "modelo": embedder.nome,
                    "resultados": repo.busca_vetor(vetor, embedder.nome, slug, k)}
    resultados = repo.busca_texto(q, slug, k)
    if resultados:
        return {"consulta": q, "modo": "texto", "modelo": None, "aviso": aviso, "resultados": resultados}
    # nada exato: tenta tolerar erro de digitação antes de responder "nada encontrado"
    return {"consulta": q, "modo": "aproximada", "modelo": None, "aviso": aviso,
            "resultados": repo.busca_aproximada(q, slug, k)}
