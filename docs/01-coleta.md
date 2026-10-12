# Camada 1 — Coleta: spec, passo a passo e roteiro de testes

Versão do motor: `coleta-0.1.0` · 12/10/2026

A Camada 1 transforma `municipios.yaml` em **snapshots imutáveis** no Supabase: páginas HTML (estáticas ou renderizadas), arquivos (PDF, CSV, XLS/XLSX, ODS, JSON) e o que foi extraído deles. Ela **não julga conformidade** — só registra o que existe, onde estava, como se chegou lá e se a coleta foi confiável. A Camada 2 lê esses snapshots e decide.

## 1. Spec

### 1.1 Fluxo de uma rodada

```
pipeline.run
 └─ por ente (ThreadPool, paralelismo entre domínios)
     └─ extract_data.collect_entity            → crawl_job
         └─ Crawler.run
             1. capa do site oficial            → fingerprint + descoberta (resolve_urls)
             2. portal, e-SIC, ouvidoria         → prioridade máxima na fila
             3. best-first por relevância        → seções de secoes.yaml ainda não cobertas
                para cada URL:
                  robots.txt → GET (retry/backoff) → fingerprint do host
                  → Playwright se SPA/HTML vazio → parse_html → classify_page
                  → snapshot (Storage + banco) → enfileira links → baixa arquivos relevantes
```

### 1.2 Módulos

| Módulo | Responsabilidade | Entrada → saída |
| --- | --- | --- |
| `config/municipios.yaml` | Entes, URLs conhecidas, override de robots, TLS | — |
| `config/platforms.yaml` | Fingerprints e parâmetros por plataforma (JS, seletor, rotas) | — |
| `config/secoes.yaml` | Seções-alvo, termos, quesitos ligados, orçamento | — |
| `transform/validate.py` | Valida YAMLs (pydantic); classifica página `ok/vazio/soft404` | YAML → `Entity` |
| `extract/http_client.py` | GET educado: UA, robots, taxa por domínio, retry, circuito, teto de bytes, GET condicional | URL → `FetchResult` (nunca lança) |
| `discover/fingerprint.py` | Detecta plataforma e SPA | `FetchResult` → `Fingerprint` |
| `discover/resolve_urls.py` | Acha portal, e-SIC, ouvidoria, dados abertos na capa | links → `EntityUrl[]` |
| `extract/renderer.py` | Playwright: DOM pós-JS + screenshot da 1ª dobra | URL → `FetchResult` |
| `extract/crawler.py` | Fila priorizada, orçamento, trilha, seções, arquivos | `Entity` → snapshots via `Sink` |
| `transform/parse_html.py` | Título, h1, texto, links, tabelas, formulários, breadcrumbs, datas, acessibilidade, exportação | HTML → `ParsedPage` |
| `transform/parse_files.py` | PDF (3 primeiras páginas, período/bimestre), CSV, planilhas, JSON, ZIP | bytes → `ParsedFile` |
| `transform/normalize.py` | Texto sem acento, URL canônica, datas e moeda BR | funções puras |
| `load/storage.py` | Objetos por sha256 (local ou Supabase Storage) | bytes → chave |
| `load/load_postgres.py` | `Repository`: Postgres (psycopg 3) ou JSONL local | dataclasses → linhas |
| `extract/extract_data.py` | Liga crawler + storage + repositório para 1 ente | `Entity` → `CrawlStats` |
| `pipeline.py` | CLI, rodada, status agregado, resumo JSON | `--entes` → `run` |

### 1.3 Status de coleta (`page_snapshot.coleta_status`)

| Status | Quando | Tradução na Camada 2 |
| --- | --- | --- |
| `ok` | 2xx com texto útil (≥ 200 caracteres) | avalia normalmente |
| `vazio` | 2xx sem texto útil (SPA não renderizada, "Carregando...") | `nao_verificavel` |
| `soft404` | 2xx dizendo "página não encontrada", "em construção" | `nao_atende` só com confirmação; por padrão `nao_verificavel` |
| `erro` | 4xx/5xx, timeout, DNS, TLS, teto de bytes | 404 em link da própria seção é evidência de falha; demais → `nao_verificavel` |
| `bloqueado_robots` | robots.txt proíbe e não há override | `nao_verificavel` + nota de política |
| `nao_modificado` | 304 em GET condicional (arquivos) | reaproveita o veredito anterior |

Regra de ouro: **falha do robô nunca vira "Não atende"**.

### 1.4 Decisões de projeto

- **Coleta orientada a objetivo, não espelho do site.** Orçamento padrão: 60 páginas, profundidade 3, 30 arquivos por ente; cada seção puxa a fila até 4 páginas e 3 arquivos. Links sem relação com nenhuma seção só são seguidos a partir da capa.
- **Domínio próprio.** Segue links no domínio registrável do site oficial (`*.niteroi.rj.gov.br`), no host exato do portal (`japeri.geosiap.net.br`, e não `*.geosiap.net.br`) e em `dominios_extra`. e-SIC externo (Fala.BR) é visitado, mas não expandido.
- **Rota de SPA é página.** `#/despesas` é mantido na URL canônica; `#topo` é descartado.
- **Fornecedor vence assinatura genérica.** Betha também é "SPA com hash", mas o fingerprint específico tem precedência (`generica: true` no YAML).
- **Mudança detectada pelo texto, não pelos bytes.** `extraido.texto_hash` (texto visível normalizado) define `inalterado`; o HTML bruto vai ao Storage por sha256, então repetir conteúdo não ocupa espaço.
- **GET condicional só para arquivos.** HTML sempre é baixado (precisamos dos links para seguir a navegação); PDFs/planilhas usam ETag/Last-Modified e, em 304, reaproveitam sha256 e extração.
- **Robots.txt é política.** Padrão: respeitar. Override por ente exige `justificativa` no YAML e fica marcado em cada snapshot (`robots_override=true`).
- **Trilha de navegação.** Cada snapshot guarda o caminho de cliques desde a capa — insumo direto do verificador `navigation` (ex.: quesito 4.1).
- **Screenshot** só da capa e da 1ª página do portal (evidência do 1.2), para controlar custo de renderização.

### 1.5 Modelo de dados (sql/schema.sql)

`entity` → `entity_url` (com vigência) · `run` → `crawl_job` (1 por ente por rodada, `unique(run_id, entity_id)`) → `page_snapshot` → `file_artifact`. Views `v_ultima_coleta` e `v_saude_rodadas` alimentam a página de monitoramento do dashboard. RLS ligada sem políticas públicas.

## 2. Passo a passo

### Passo 1 — Ambiente local

```bash
git clone https://github.com/SEU_USUARIO/portal-transparencia.git && cd portal-transparencia
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium
cp .env.example .env      # preencher na etapa 3
pytest -q                 # 55 testes passam, 1 pulado (integração Postgres)
```

### Passo 2 — Primeira coleta sem banco

```bash
python -m ingestion.pipeline --local --entes rj-marica --max-paginas 20 -v
```

Grava em `data/local/*.jsonl` e `data/objetos/`. Maricá é o melhor caso (HTML servido); use para calibrar antes dos SPAs.

```python
import pandas as pd
p = pd.read_json("data/local/page_snapshot.jsonl", lines=True)
p[["url", "coleta_status", "secoes", "profundidade"]]
```

### Passo 3 — Supabase

1. SQL Editor → colar e executar `sql/schema.sql` (idempotente).
2. Storage: o pipeline cria o bucket privado `coleta` na primeira execução (ou crie manualmente).
3. Project Settings → Database → *Connection string* → **Session pooler** (porta 5432) em `DATABASE_URL`. O Transaction pooler (6543) também funciona — prepared statements já estão desligados.
4. Project Settings → API → `SUPABASE_URL` e a chave `service_role` em `SUPABASE_SERVICE_KEY`.
5. `python -m ingestion.pipeline --entes rj-marica --max-paginas 20` e conferir:

```sql
select * from v_saude_rodadas order by iniciado_em desc limit 1;
select entity_id, status, paginas, arquivos, bloqueios_robots, secoes_ausentes from v_ultima_coleta;
```

### Passo 4 — GitHub Actions

Settings → Secrets and variables → Actions:
- Secrets: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
- Variable: `COLETA_USER_AGENT` com URL do repositório e um e-mail de contato

`ci.yml` roda lint + testes a cada push (com Postgres efêmero para o teste de integração). `coleta.yml` roda nos dias 1 e 15 e por *Run workflow* manual. Dispare duas vezes nesta semana para ter histórico real.

### Passo 5 — Decisão de robots.txt (Itaboraí, Japeri, Cachoeiras)

Com `ativo: false` esses portais saem como `bloqueado_robots` e não penalizam o score. Se decidir pelo override, troque para `ativo: true`, mantenha a justificativa e registre a decisão na documentação técnica.

### Passo 6 — Calibrar por município

Rodar um ente por vez e ajustar só YAML:

| Sintoma | Ajuste |
| --- | --- |
| Seção em `secoes_ausentes` mas existe no portal | adicionar o termo usado pelo portal em `secoes.yaml` |
| Portal SPA coletado como `vazio` | conferir Playwright; adicionar `wait_selector` em `platforms.yaml` |
| Plataforma `null` | adicionar sinal (host ou trecho de HTML) em `platforms.yaml` |
| Informação em subdomínio não seguido | `dominios_extra` no ente |
| Orçamento estoura antes de cobrir as seções | `--max-paginas` ou `max_paginas_por_secao` |

## 3. Roteiro de testes

### 3.1 Automatizados (offline, `pytest -q`)

| Arquivo | O que garante |
| --- | --- |
| `test_normalize_validate.py` | URL canônica (SPA, utm, relativa), termos por palavra inteira, datas/moeda BR, YAML real válido, erros de config (id duplicado, URL inválida, override sem justificativa), `classify_page` |
| `test_http_client.py` | UA identificado, robots bloqueia/override marca, retry com backoff em 503, `Retry-After`, 404 sem retry, timeout/DNS classificados, teto de bytes, 304, circuit breaker, taxa por domínio |
| `test_discover_parse.py` | Fingerprint WordPress/GeoSiap/Betha/SPA, descoberta de portal/e-SIC/ouvidoria com divergência de config, extração completa de HTML (tabela, form, breadcrumb, acessibilidade, exportação), PDF de RREO (bimestre e período), CSV cp1252 com `;`, XLSX com título antes do cabeçalho, PDF corrompido não derruba |
| `test_crawler_pipeline.py` | Município falso ponta a ponta: seções e trilha, não sai do domínio, ignora notícias, registra robots e link quebrado, baixa RREO e CSV, respeita orçamento, renderiza SPA, SPA sem Chromium vira `vazio`, override, duas rodadas incrementais (sem novo upload, PDF via 304), um ente fora do ar não derruba a rodada, Supabase Storage com duplicado |
| `test_postgres_integration.py` | Schema aplicado 2×, pipeline grava run/job/snapshots/arquivos, vigência de URLs sem duplicar na 2ª rodada, view `v_ultima_coleta` (roda no CI) |

### 3.2 Smoke real por município (manual, após o Passo 3)

Rodar `python -m ingestion.pipeline --entes <id> --max-paginas 40` e registrar numa planilha de validação:

| Ente | Esperado na 1ª rodada | Verificar |
| --- | --- | --- |
| rj-marica | `concluido`; plataforma do portal detectada; despesa, receita, pessoal, licitações cobertas | trilha da página de despesas tem capa → portal → despesas |
| rj-niteroi | portal com `renderizado_js=true` | snapshot do portal com texto útil (não `vazio`) |
| rj-duque-de-caxias | capa e portal renderizados | `renderizacoes` > 0 no job |
| rj-rio-de-janeiro | páginas em subdomínios `*.rio` e `data.rio` | `secoes_encontradas` inclui `lgpd_digital` (dados abertos) |
| rj-itaborai | `parcial`, `bloqueado_robots` no portal | site WordPress coletado normalmente |
| rj-japeri | plataforma `geosiap`; portal `bloqueado_robots` | e-SIC Fala.BR visitado, sem expandir |
| rj-cachoeiras-de-macacu | plataforma do site `wix`; portal descoberto na capa (ou ausente) | `entity_url` com `fonte=descoberta` e `dominio_gov=false` |

Critérios de aceite da Camada 1:

1. Os 7 entes têm `crawl_job` em toda rodada, mesmo com falha (`v_saude_rodadas.entes = 7`).
2. Nenhum job falha por exceção não tratada (`erro` começando com nome de exceção Python).
3. Para cada ente sem bloqueio, ≥ 8 das 18 seções em `secoes_encontradas`.
4. 2ª rodada sem mudança no portal: `paginas_inalteradas / paginas ≥ 0,7` e quase nenhum upload novo.
5. Amostra de 10 snapshots por ente conferida à mão: URL abre, título bate, seções fazem sentido.

### 3.3 Medições para o relatório (item 17 do desafio)

```sql
select entity_id, paginas, requisicoes, renderizacoes, bytes/1e6 as mb, duracao_s
from crawl_job where run_id = '<run>';
```

Esses números substituem a hipótese do plano (~100 requisições por portal) e alimentam a estimativa de custo para 5.000 portais/mês.

## 4. Limitações conhecidas

- Não preenche formulários nem pagina tabelas; consultas que só existem após filtro ficam para adaptadores específicos.
- PDF escaneado é marcado (`sem_texto=true`) mas não passa por OCR.
- CAPTCHA/anti-bot não é contornado: vira `erro` ou `vazio`.
- robots.txt ilegível (5xx) é tratado como permissivo; o job não distingue ainda "ausente" de "indisponível".
- Rotas-semente por plataforma estão vazias até serem confirmadas nos portais reais.

## 5. Próximo passo (Camada 2)

`sql/002_conclusao.sql` (checklist_version, criterion, evaluation, criterion_result, evidence, chunk com pgvector, human_review), `criteria.yaml` gerado a partir da planilha, e os verificadores lendo `page_snapshot.extraido` e `file_artifact.metadados`.
