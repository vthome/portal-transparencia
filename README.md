# Monitor de Conformidade de Portais da Transparência (RJ)

Prova de conceito que avalia continuamente os portais de 7 prefeituras do RJ contra o checklist de transparência, com evidência auditável por critério, score ponderado e histórico.

```
1. Coleta      municipios.yaml → descoberta + fingerprint → crawler (requests/Playwright) → extração
2. Conclusão   snapshots → embeddings (768) → 13 verificadores → confiança → score
3. Exposição   FastAPI (Render) → Streamlit Cloud → revisão humana
```

Coleta e conclusão são separadas: regras novas reavaliam snapshots antigos sem recoletar.

## Status

| Camada | Estado |
| --- | --- |
| 1. Coleta | implementada — [docs/01-coleta.md](docs/01-coleta.md) |
| 2. Conclusão | implementada — [docs/02-conclusao.md](docs/02-conclusao.md) |
| 3. API (FastAPI · Render) | implementada — [docs/03-api.md](docs/03-api.md) |
| 3. Dashboard (Streamlit Cloud) | implementado — [docs/04-dashboard.md](docs/04-dashboard.md) |

## Rodando

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium
pytest -q

# coleta sem banco (grava em data/)
python -m ingestion.pipeline --local --entes rj-marica --max-paginas 20

# coleta no Supabase (preencha .env a partir de .env.example e aplique sql/schema.sql — schema `monitor`)
python -m ingestion.preflight          # verifica banco, schema, bucket, Chromium e portais
.\scripts\coleta.ps1                  # Windows: preflight + coleta + relatório (Linux: scripts/coleta.sh)
python -m ingestion.reextract          # refaz só a extração sobre os snapshots guardados

# conclusão (Camada 2) sobre a última rodada — reaplique sql/schema.sql (v0.3) antes da 1ª vez
pip install -r requirements-ml.txt     # sentence-transformers (PyTorch); ver docs/02-conclusao.md
.\scripts\conclusao.ps1               # embeddings + conformidade + score (Linux: scripts/conclusao.sh)
python -m compliance.pipeline --sem-embeddings   # só regras, sem modelo
python -m compliance.calibrar          # recalibra a confiança com as revisões humanas (≥ 30)

# API (Camada 3) — reaplique sql/schema.sql (v0.4); deploy no Render pelo render.yaml
.\scripts\api.ps1                     # http://127.0.0.1:8000/docs
python scripts/testar_api.py --url https://<seu-servico>.onrender.com

# dashboard (Camada 3) — só conversa com a API
.\scripts\dashboard.ps1 -Api https://<seu-servico>.onrender.com   # http://localhost:8501
```

## Estrutura

```
ingestion/
  config/        municipios.yaml, platforms.yaml, secoes.yaml
  discover/      fingerprint.py, resolve_urls.py
  extract/       http_client.py, crawler.py, renderer.py, extract_data.py
  transform/     normalize.py, validate.py, parse_html.py, parse_files.py, extracao.py
  load/          storage.py, load_postgres.py
  transform/prepare_documents.py, embeddings/generate_embeddings.py   (trechos e vetores 768)
  models.py, settings.py, pipeline.py, preflight.py, report.py, reextract.py
compliance/
  config/        criteria.yaml (142 quesitos), scoring.yaml, checklistPortalTransp.xlsx
  checkers/      existencia, estruturados, documentos, acessibilidade, semantico (+ base)
  coleta.py, checklist.py, confidence.py, scoring.py, engine.py, repo.py, pipeline.py, report.py, calibrar.py
dashboard/       app.py (Streamlit), requirements.txt (Streamlit Cloud)
  painel/        config, cliente da API, cache, formatos, gráficos (Plotly), ui
  paginas/       visao_geral, municipio, evolucao, revisao, busca, monitoramento
api/
  main.py, config.py, database.py, deps.py, schemas.py, servicos.py
  routers/       entes, indicadores, avaliacoes, revisoes, documentos, monitoramento
scripts/         coleta, conclusao, api, dashboard (.ps1/.sh), testar_api.py, importar_checklist.py
.streamlit/      config.toml, secrets.toml.example
sql/schema.sql   schema `monitor` v0.4 (v0.1 + coleta + Camada 2 + índices da API)
sql/api_role.sql usuário restrito para a API (lê tudo, grava só revisões)
render.yaml      Blueprint do Render (API) · requirements-api.txt · .python-version
tests/           offline (site falso) + integração Postgres no CI
.github/workflows/ ci.yml, coleta.yml (dias 1 e 15), conclusao.yml (após cada coleta)
docs/01-coleta.md, docs/02-conclusao.md, docs/03-api.md, docs/04-dashboard.md
```

## Política de coleta

O robô se identifica (`MonitorTransparenciaBot`), respeita robots.txt por padrão, faz no máximo 1 requisição/s por domínio e não contorna CAPTCHA. Páginas que não puderam ser coletadas são registradas como tal e nunca contam como não conformidade.
