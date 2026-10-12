# Portal Transparência RJ

Prova de conceito para avaliação automatizada da conformidade de Portais da Transparência de municípios do Estado do Rio de Janeiro: Cachoeiras Macacu, Duque de Caxias, Itaboraí, Japeri, Maricá, Niterói e Rio de Janeiro. Apresenta score ponderado e histórico.


## Objetivo

Desenvolver um pipeline de coleta, extração e análise de evidências públicas, permitindo avaliar critérios de transparência, calcular indicadores de conformidade e disponibilizar resultados auditáveis.

## Tecnologias

- Python
- FastAPI
- Streamlit
- PostgreSQL / Supabase
- pgvector
- Sentence Transformers
- GitHub Actions

```
1. Coleta      municipios.yaml → descoberta + fingerprint → crawler (requests/Playwright) → extração
2. Conclusão   snapshots → embeddings → verificadores + LLM juiz → score            (próxima etapa)
3. Exposição   FastAPI (Render) → Streamlit Cloud → revisão humana                 (próxima etapa)
```
## Status

| Camada | Estado |
| --- | --- |
| 1. Coleta | implementada — [docs/01-coleta.md](docs/01-coleta.md) |
| 2. Conclusão | a fazer |
| 3. API e dashboard | a fazer |

## Componentes

- `config/`: configuração dos municípios, plataformas e critérios.
- `ingestion/`: descoberta, coleta, extração e transformação.
- `compliance/`: classificação de conformidade, confiança e pontuação.
- `api/`: disponibilização dos resultados por API REST.
- `dashboard/`: visualização e monitoramento.
- `sql/`: estrutura do banco de dados.
- `tests/`: testes automatizados.

## Status

Projeto em desenvolvimento — estrutura inicial criada.

## Dados

As análises utilizarão informações públicas dos portais oficiais dos municípios selecionados. As avaliações deverão preservar evidências, fontes e datas de coleta.


## Rodando

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium
pytest -q

# coleta sem banco (grava em data/)
python -m ingestion.pipeline --local --entes rj-marica --max-paginas 20

# coleta no Supabase (preencha .env a partir de .env.example e aplique sql/schema.sql)
python -m ingestion.pipeline
```

## Estrutura

```
ingestion/
  config/        municipios.yaml, platforms.yaml, secoes.yaml
  discover/      fingerprint.py, resolve_urls.py
  extract/       http_client.py, crawler.py, renderer.py, extract_data.py
  transform/     normalize.py, validate.py, parse_html.py, parse_files.py
  load/          storage.py, load_postgres.py
  models.py, settings.py, pipeline.py
sql/schema.sql
tests/           offline (site falso) + integração Postgres no CI
.github/workflows/ ci.yml, coleta.yml (dias 1 e 15)
docs/01-coleta.md
```

## Política de coleta

O robô se identifica (`MonitorTransparenciaBot`), respeita robots.txt por padrão, faz no máximo 1 requisição/s por domínio e não contorna CAPTCHA. Páginas que não puderam ser coletadas são registradas como tal e nunca contam como não conformidade.
