# Monitor de Conformidade de Portais da Transparência (RJ)

Prova de conceito que avalia continuamente os portais de 7 prefeituras do RJ contra o checklist de transparência, com evidência auditável por critério, score ponderado e histórico.

```
1. Coleta      municipios.yaml → descoberta + fingerprint → crawler (requests/Playwright) → extração
2. Conclusão   snapshots → embeddings → verificadores + LLM juiz → score            (próxima etapa)
3. Exposição   FastAPI (Render) → Streamlit Cloud → revisão humana                 (próxima etapa)
```

Coleta e conclusão são separadas: regras novas reavaliam snapshots antigos sem recoletar.

## Status

| Camada | Estado |
| --- | --- |
| 1. Coleta | implementada — [docs/01-coleta.md](docs/01-coleta.md) |
| 2. Conclusão | a fazer |
| 3. API e dashboard | a fazer |

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
```

## Estrutura

```
ingestion/
  config/        municipios.yaml, platforms.yaml, secoes.yaml
  discover/      fingerprint.py, resolve_urls.py
  extract/       http_client.py, crawler.py, renderer.py, extract_data.py
  transform/     normalize.py, validate.py, parse_html.py, parse_files.py, extracao.py
  load/          storage.py, load_postgres.py
  models.py, settings.py, pipeline.py, preflight.py, report.py, reextract.py
scripts/         coleta.ps1, coleta.sh
sql/schema.sql   schema `monitor` (v0.1 + colunas da coleta)
tests/           offline (site falso) + integração Postgres no CI
.github/workflows/ ci.yml, coleta.yml (dias 1 e 15)
docs/01-coleta.md
```

## Política de coleta

O robô se identifica (`MonitorTransparenciaBot`), respeita robots.txt por padrão, faz no máximo 1 requisição/s por domínio e não contorna CAPTCHA. Páginas que não puderam ser coletadas são registradas como tal e nunca contam como não conformidade.
