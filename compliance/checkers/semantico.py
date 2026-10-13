"""Verificador semântico: busca por embeddings + confirmação por termos.

Fluxo (sem enviar páginas inteiras a um modelo):
1. a consulta do quesito (texto + `consulta` do YAML) vira um vetor com o mesmo modelo dos trechos
2. os k trechos mais próximos do ente são recuperados (similaridade de cosseno)
3. o veredito combina a similaridade com a presença literal de termos do quesito nesses trechos;
   as duas evidências concordando aumentam `fontes` e, portanto, a confiança
4. sem embeddings (modelo indisponível), cai para a busca por termos e marca `metodo=regra`

Limiares em scoring.yaml (`semantico.alto` / `semantico.medio`) devem ser calibrados por modelo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from compliance.checkers.base import Evidencia, ausencia, r, verificador
from compliance.checkers.existencia import existence
from compliance.coleta import ColetaEnte
from ingestion.transform.normalize import find_terms


@dataclass
class IndiceSemantico:
    vetores: np.ndarray                           # (n, d), linhas normalizadas
    metas: list[dict[str, Any]] = field(default_factory=list)  # texto, page_snapshot_id, file_artifact_id, url

    @classmethod
    def de_chunks(cls, chunks: list[dict[str, Any]]) -> "IndiceSemantico | None":
        if not chunks:
            return None
        m = np.asarray([c["embedding"] for c in chunks], dtype=np.float32)
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
        return cls(m, [{k: v for k, v in c.items() if k != "embedding"} for c in chunks])

    def buscar(self, vetor: list[float], k: int = 5) -> list[tuple[float, dict[str, Any]]]:
        q = np.asarray(vetor, dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-12
        sims = self.vetores @ q
        idx = np.argsort(-sims)[:k]
        return [(float(sims[i]), self.metas[i]) for i in idx]


@verificador("semantic", versao="2")
def semantic(c, coleta: ColetaEnte, ctx):
    p = c.params
    termos, secoes = p.get("termos", []), p.get("secoes") or None
    base = existence(type("C", (), {"params": {"termos": termos, "secoes": secoes, "escopo": "site"}})(),
                     coleta, ctx)
    if ctx.indice is None or ctx.embedder is None:
        base.sinais["forca"] = round(base.sinais.get("forca", 0.5) * 0.9, 3)
        base.justificativa += " (sem embeddings: decisão só por termos)"
        return base
    cfg = ctx.cfg["semantico"]
    consulta = f"{c.texto}. {p.get('consulta', '')}"
    hits = ctx.indice.buscar(ctx.embedder.encode([consulta])[0], k=int(cfg.get("k", 5)))
    sim_max = hits[0][0] if hits else 0.0
    com_termo = [(s, m) for s, m in hits if find_terms(m.get("texto", ""), termos)]
    evs = [Evidencia("trecho", url=m.get("url"), page_snapshot_id=m.get("page_snapshot_id"),
                     file_artifact_id=m.get("file_artifact_id"), trecho=(m.get("texto") or "")[:300],
                     metadados={"similaridade": round(s, 3), "termo": bool(find_terms(m.get("texto", ""), termos))})
           for s, m in (com_termo or hits)[:3]]
    det = {"similaridade_max": round(sim_max, 3), "trechos_com_termo": len(com_termo)}
    alto, medio = float(cfg["alto"]), float(cfg["medio"])
    q = coleta.qualidade(secoes)
    if com_termo and com_termo[0][0] >= medio:
        fontes = 2 if base.status == "atende" else 1
        return r("atende", f"trecho relevante (similaridade {com_termo[0][0]:.2f}) contém termos do quesito.", evs,
                 forca=min(0.95, 0.55 + com_termo[0][0] / 2), fontes=fontes, qualidade=q, metodo="embedding", **det)
    if sim_max >= alto:
        return r("parcial", f"trecho semanticamente próximo (similaridade {sim_max:.2f}), sem os termos esperados.",
                 evs, forca=0.5, qualidade=q, metodo="embedding", **det)
    if base.status in {"atende", "parcial"}:
        base.sinais.update({**det, "forca": round(base.sinais.get("forca", 0.5) * 0.85, 3)})
        base.justificativa += f" (busca semântica fraca: {sim_max:.2f})"
        return base
    res = ausencia(coleta, secoes, "conteúdo do quesito", evs)
    res.sinais.update(det)
    res.metodo = "embedding"
    return res
