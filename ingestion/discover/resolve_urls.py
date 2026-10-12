"""Descoberta: a partir da capa do site oficial, encontra portal da transparência,
e-SIC, ouvidoria e dados abertos.

Regras:
- URL do YAML (fonte=config) é a referência; a descoberta adiciona o que falta e
  registra divergência quando a capa aponta para outro endereço (sinal de troca de
  portal/fornecedor — vira nova linha em `entity_url`).
- Cada candidato guarda a evidência: texto do link, URL de origem e pontuação. Isso já é
  insumo do quesito 1.2 (acesso ao portal visível na capa).
"""
from __future__ import annotations

from dataclasses import dataclass

from ingestion.models import Entity, EntityUrl
from ingestion.transform.normalize import find_terms, is_gov_domain, normalize_text
from ingestion.transform.parse_html import Link

# termo -> peso. Texto do link vale mais que a URL.
ALVOS: dict[str, dict[str, int]] = {
    "portal_transparencia": {"portal da transparencia": 6, "transparencia": 4, "contas publicas": 2,
                             "portal transparencia": 6},
    "esic": {"e sic": 6, "esic": 6, "acesso a informacao": 4, "servico de informacao ao cidadao": 5,
             "lai": 2, "sic": 3, "pedido de informacao": 4},
    "ouvidoria": {"ouvidoria": 6, "fala br": 4, "falabr": 4, "fale conosco": 2},
    "dados_abertos": {"dados abertos": 6, "open data": 3},
}
LIMIAR = 4


@dataclass
class Candidato:
    tipo: str
    url: str
    texto: str
    pontos: int
    na_capa_topo: bool


def score_link(link: Link, tipo: str) -> int:
    termos = ALVOS[tipo]
    pts = sum(termos[t] for t in find_terms(link.texto or "", list(termos)))
    pts += sum(max(1, termos[t] // 2) for t in find_terms(link.url, list(termos)))
    if link.arquivo:          # um PDF chamado "transparência" não é o portal
        pts -= 5
    if link.na_capa_topo:
        pts += 1
    return pts


def find_candidates(links: list[Link]) -> dict[str, list[Candidato]]:
    out: dict[str, list[Candidato]] = {t: [] for t in ALVOS}
    for link in links:
        for tipo in ALVOS:
            pts = score_link(link, tipo)
            if pts >= LIMIAR:
                out[tipo].append(Candidato(tipo, link.url, link.texto, pts, link.na_capa_topo))
    for tipo in out:
        out[tipo].sort(key=lambda c: (-c.pontos, len(c.url)))
    return out


def resolve(entity: Entity, links_capa: list[Link], url_capa: str) -> list[EntityUrl]:
    """Junta URLs configuradas e descobertas. Devolve a lista vigente para `entity_url`."""
    urls: list[EntityUrl] = []
    for tipo, url in entity.urls.items():
        urls.append(EntityUrl(tipo=tipo, url=url, fonte="config", dominio_gov=is_gov_domain(url)))

    cands = find_candidates(links_capa)
    for tipo, lista in cands.items():
        if not lista:
            continue
        melhor = lista[0]
        conf = entity.urls.get(tipo)
        evid = {"pagina_origem": url_capa, "texto_link": melhor.texto, "pontos": melhor.pontos,
                "na_capa_topo": melhor.na_capa_topo,
                "alternativas": [c.url for c in lista[1:4]]}
        if conf and _mesmo_destino(conf, melhor.url):
            for u in urls:
                if u.tipo == tipo and u.fonte == "config":
                    u.evidencia = {**evid, "confirmado_na_capa": True}
            continue
        if conf:
            evid["divergente_de_config"] = conf
        urls.append(EntityUrl(tipo=tipo, url=melhor.url, fonte="descoberta",
                              dominio_gov=is_gov_domain(melhor.url), evidencia=evid))
    return urls


def _mesmo_destino(a: str, b: str) -> bool:
    na, nb = normalize_text(a.removeprefix("https://").removeprefix("http://").removeprefix("www.")), \
        normalize_text(b.removeprefix("https://").removeprefix("http://").removeprefix("www."))
    return na == nb or na.startswith(nb) or nb.startswith(na)
