from datetime import date
from pathlib import Path

import pytest

from ingestion.transform.normalize import (
    find_terms,
    is_gov_domain,
    normalize_text,
    normalize_url,
    parse_dates_br,
    parse_money_br,
    registrable_domain,
)
from ingestion.transform.validate import (
    ConfigError,
    classify_page,
    load_entities,
    load_platforms,
    load_secoes,
)

# ------------------------------------------------------------------ normalize

def test_normalize_text_remove_acentos_e_pontuacao():
    assert normalize_text("Portal da Transparência — e-SIC!") == "portal da transparencia e sic"


def test_find_terms_casa_palavra_inteira():
    assert find_terms("Acesse o e-SIC aqui", ["sic", "e-sic"]) == ["sic", "e-sic"]
    assert find_terms("Música clássica", ["sic"]) == []          # 'sic' dentro de 'musica' não conta


@pytest.mark.parametrize("url,esperado", [
    ("HTTPS://Www.Niteroi.RJ.gov.br:443/a//b?utm_source=x&b=2&a=1", "https://www.niteroi.rj.gov.br/a/b?a=1&b=2"),
    ("https://transparencia.niteroi.rj.gov.br/#/despesas", "https://transparencia.niteroi.rj.gov.br/#/despesas"),
    ("https://site.rj.gov.br/pagina#topo", "https://site.rj.gov.br/pagina"),
    ("javascript:void(0)", None),
    ("mailto:ouvidoria@x.gov.br", None),
])
def test_normalize_url(url, esperado):
    assert normalize_url(url) == esperado


def test_normalize_url_relativa():
    assert normalize_url("../despesas", "https://x.rj.gov.br/portal/receitas/") == "https://x.rj.gov.br/portal/despesas"


def test_registrable_domain():
    assert registrable_domain("transparencia.niteroi.rj.gov.br") == "niteroi.rj.gov.br"
    assert registrable_domain("japeri.geosiap.net.br") == "geosiap.net.br"
    assert registrable_domain("transparencia.prefeitura.rio") == "prefeitura.rio"
    assert is_gov_domain("https://www.marica.rj.gov.br/") and not is_gov_domain("https://www.prefeituracachoeiras.com/")


def test_datas_e_moeda():
    assert parse_dates_br("Atualizado em 05/03/2026 e 2026-01-31; 7 de março de 2026") == [
        date(2026, 3, 5), date(2026, 1, 31), date(2026, 3, 7)]
    assert parse_dates_br("31/02/2026") == []
    assert parse_money_br("R$ 1.234.567,89") == 1234567.89
    assert parse_money_br("(1.000,00)") == -1000.0
    assert parse_money_br("sem valor") is None


# ------------------------------------------------------------------ validate

def test_municipios_yaml_real_valida():
    entes = load_entities()
    assert len(entes) == 7
    ids = {e.id for e in entes}
    assert {"rj-rio-de-janeiro", "rj-niteroi", "rj-marica", "rj-duque-de-caxias", "rj-itaborai",
            "rj-japeri", "rj-cachoeiras-de-macacu"} == ids
    assert all(e.poder == "executivo" and e.tipo_entidade == "prefeitura" for e in entes)
    assert all(not e.robots_override for e in entes)   # política padrão: respeitar robots.txt


def test_configs_auxiliares_validas():
    assert "betha" in load_platforms()["plataformas"]
    secoes = load_secoes()["secoes"]
    quesitos = [q for s in secoes.values() for q in s["quesitos"]]
    assert len(quesitos) == len(set(quesitos)), "um quesito não pode pertencer a duas seções"


def test_filtro_por_ente_desconhecido(tmp_path: Path):
    with pytest.raises(ConfigError, match="desconhecidos"):
        load_entities(only=["rj-atlantida"])


def _yaml(tmp_path: Path, corpo: str) -> Path:
    p = tmp_path / "m.yaml"
    p.write_text(corpo, encoding="utf-8")
    return p


def test_id_duplicado_falha(tmp_path: Path):
    p = _yaml(tmp_path, """
entes:
  - {id: rj-a, nome: A, municipio: A, urls: {site_oficial: "https://a.rj.gov.br/"}}
  - {id: rj-a, nome: B, municipio: B, urls: {site_oficial: "https://b.rj.gov.br/"}}
""")
    with pytest.raises(ConfigError, match="duplicados"):
        load_entities(p)


def test_url_invalida_e_tipo_invalido_falham(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_entities(_yaml(tmp_path, "entes:\n  - {id: rj-a, nome: A, municipio: A, urls: {site_oficial: 'nao-e-url'}}\n"))
    with pytest.raises(ConfigError):
        load_entities(_yaml(tmp_path, "entes:\n  - {id: rj-a, nome: A, municipio: A, urls: {blog: 'https://a.gov.br'}}\n"))


def test_override_sem_justificativa_falha(tmp_path: Path):
    p = _yaml(tmp_path, """
entes:
  - id: rj-a
    nome: A
    municipio: A
    urls: {site_oficial: "https://a.rj.gov.br/"}
    robots_override: {ativo: true}
""")
    with pytest.raises(ConfigError, match="justificativa"):
        load_entities(p)


def test_classify_page():
    texto = "Despesas do município " * 30
    assert classify_page(texto, "Despesas", 200) == "ok"
    assert classify_page("Carregando...", "Portal", 200) == "vazio"
    assert classify_page("Página em construção. Volte em breve.", "Despesas", 200) == "soft404"
    assert classify_page("Oops " * 20, "404 Not Found", 200) == "soft404"
