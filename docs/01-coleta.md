# Camada 1 — Coleta: spec, passo a passo e roteiro de testes

Versão do motor: `coleta-0.1.0` · schema `monitor` v0.2 · 12/10/2026

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
| `transform/extracao.py` | Fase de extração de uma página (parse + status + seções), versionada por `PARSER_VERSION` | HTML → `PaginaExtraida` |
| `extract/extract_data.py` | Liga crawler + storage + repositório para 1 ente | `Entity` → `CrawlStats` |
| `pipeline.py` | CLI, rodada, status agregado, resumo JSON e relatório | `--entes` → `run` |
| `preflight.py` | Verifica .env, banco, schema v0.2, bucket, Chromium e portais antes de rodar | — → diagnóstico |
| `report.py` | Relatório da rodada + critérios de aceite (`data/runs/<run>-relatorio.md`) | `run_id` → Markdown |
| `reextract.py` | Reexecuta só a extração sobre o HTML guardado no Storage | `run_id` → snapshots atualizados |
| `scripts/coleta.ps1`, `scripts/coleta.sh` | Preflight → coleta → relatório, num comando | — |

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
- **Escopo de navegação.** Segue links no domínio registrável do site oficial (`*.niteroi.rj.gov.br`), no host exato do portal — configurado **ou descoberto na capa** (`japeri.geosiap.net.br`, `transparencia.betha.cloud`, nunca `*.geosiap.net.br`) — e em `dominios_extra`. Canais em terceiros (Fala.BR, sistemas de ouvidoria de fornecedor) são visitados como semente, mas não expandidos.
- **Orçamento reservado ao portal.** Duas filas: portal e demais hosts. Enquanto houver fila no portal, o site institucional usa no máximo `1 - COLETA_FRACAO_PORTAL` das páginas (padrão: metade). O relatório mostra a distribuição de páginas por host.
- **Menus em JavaScript.** Links são recuperados de `href`, `onclick` (`window.open`, `location.href`), atributos `data-href`/`data-url` e `<select>` de navegação. Se uma página estática tiver 5 ou mais `<a>` sem destino, ela é renderizada; havendo mais links, o host passa a ser renderizado direto; sem ganho, não tenta de novo naquele host.
- **Downloads sem extensão.** Links cujo texto ou URL sugere arquivo ("Baixar", "PDF", `/download?id=`) são baixados; a extensão vem do `Content-Disposition`, do `Content-Type` ou da assinatura do arquivo. Se a resposta for HTML, o link volta para a fila como página. Sem termo de seção no link, o arquivo herda as seções da página onde apareceu. Teto: 5 tentativas por página e 2 × `COLETA_MAX_ARQUIVOS` por ente.
- **Rota de SPA é página.** `#/despesas` é mantido na URL canônica; `#topo` é descartado.
- **Fornecedor vence assinatura genérica.** Betha também é "SPA com hash", mas o fingerprint específico tem precedência (`generica: true` no YAML).
- **Mudança detectada pelo texto, não pelos bytes.** `extraido.texto_hash` (texto visível normalizado) define `inalterado`; o HTML bruto vai ao Storage por sha256, então repetir conteúdo não ocupa espaço.
- **GET condicional só para arquivos.** HTML sempre é baixado (precisamos dos links para seguir a navegação); PDFs/planilhas usam ETag/Last-Modified e, em 304, reaproveitam sha256 e extração.
- **Robots.txt é política.** Padrão: respeitar. Override por ente exige `justificativa` no YAML e fica marcado em cada snapshot (`robots_override=true`).
- **Trilha de navegação.** Cada snapshot guarda o caminho de cliques desde a capa — insumo direto do verificador `navigation` (ex.: quesito 4.1).
- **Screenshot** só da capa e da 1ª página do portal (evidência do 1.2), para controlar custo de renderização.

### 1.5 Modelo de dados (sql/schema.sql)

Tudo fica no schema `monitor` (fora da Data API do Supabase, sem acesso para `anon`/`authenticated`). O arquivo é a v0.1 aplicada na configuração inicial mais a seção 6.1 (v0.2), que acrescenta com `add column if not exists` o que a coleta grava.

`entity` → `entity_url` (com vigência) · `run` → `crawl_job` (1 por ente por rodada, `unique(run_id, entity_id)`) → `page_snapshot` → `file_artifact`. Views `v_ultima_coleta` e `v_saude_rodadas` alimentam a página de monitoramento do dashboard. RLS ligada sem políticas.

O `PostgresRepository` faz a ponte entre o modelo do pipeline e o do banco:

| No pipeline / YAML | No banco |
| --- | --- |
| `id` do ente (`rj-marica`) | `entity.slug`; o `entity.id` (bigint) fica em cache |
| `site_oficial`, `portal_transparencia` | `entity_url.tipo` = `site`, `portal` (demais tipos iguais) |
| job `em_execucao` | enum `status_execucao` = `executando` |
| `PageSnapshot.extraido["texto"]`, `["links"]` | colunas `texto` (índice trigram) e `links`; o resto em `extraido` |
| `CrawlStats.duracao_s` | `crawl_job.segundos` |
| robots override do ente | `entity_url.respeita_robots` |

Objetos no bucket `snapshots`: `html/<aa>/<sha256>.html`, `png/<aa>/<sha256>.png`, `files/<aa>/<sha256>.<ext>`.

## 2. Executar a coleta completa no Supabase

Pré-requisitos: repositório atualizado, `sql/schema.sql` aplicado no Supabase e `.env` preenchido (Passos 1 e 3 abaixo).

### 2.1 Verificar o ambiente

```powershell
python -m ingestion.preflight
```

Saída esperada (resumo):

```
1. Configuração      [OK] YAMLs · DATABASE_URL · SUPABASE_URL · SUPABASE_SECRET_KEY
2. Banco             [OK] conexão · schema monitor v0.2 · enum status_coleta · [AVISO] pgvector
3. Storage           [OK] bucket 'snapshots': grava, lê e apaga
4. Navegador         [OK] Chromium
5. Portais           uma linha por URL: HTTP, robots, tempo e sugestão
PRONTO para rodar a coleta.
```

Os itens `[ERRO]` bloqueiam; `[AVISO]` e a seção 5 só informam. A seção 5 é onde se decide, antes de coletar, se algum ente precisa de `verificar_tls: false` ou de `robots_override` no `municipios.yaml`.

### 2.2 Rodar

Windows (PowerShell, na raiz do repositório):

```powershell
.\scripts\coleta.ps1 -Entes rj-marica -MaxPaginas 20   # 1º teste: um ente, orçamento pequeno
.\scripts\coleta.ps1                                   # rodada completa dos 7 entes
```

Se o PowerShell bloquear scripts: `powershell -ExecutionPolicy Bypass -File .\scripts\coleta.ps1`.

Linux/macOS/Git Bash: `scripts/coleta.sh --entes rj-marica --max-paginas 20` e depois `scripts/coleta.sh`.

O script faz preflight → coleta + extração → relatório. Sem os scripts: `python -m ingestion.pipeline`. Sem `--local`, o pipeline **exige** as três variáveis do Supabase e para com erro se faltar alguma — não grava no disco em silêncio.

Tempo de referência: com 1 req/s por domínio e 60 páginas, cada ente leva alguns minutos; 3 entes rodam em paralelo (`COLETA_PARALELISMO`).

### 2.3 Conferir

O relatório sai no terminal e em `data/runs/<run_id>-relatorio.md`: status, distribuição ok/vazio/soft404/erro/robots, arquivos, renderizações e seções por ente, páginas por host (com o portal marcado), erros mais frequentes e os critérios de aceite. Para gerar de novo: `python -m ingestion.report` (ou `--run <id>`).

No Supabase:

```sql
select * from monitor.v_saude_rodadas order by run_id desc limit 3;
select entity_slug, status, paginas, arquivos, bloqueios_robots, secoes_ausentes from monitor.v_ultima_coleta;
select e.slug, p.url, p.coleta_status, p.secoes, p.trilha
from monitor.page_snapshot p join monitor.entity e on e.id = p.entity_id
where p.crawl_job_id in (select id from monitor.crawl_job where run_id = <run>) order by e.slug, p.profundidade;
```

No Storage, bucket `snapshots`: pastas `html/`, `png/` (screenshots) e `files/` (PDF, CSV, planilhas).

### 2.4 Reexecutar só a extração

Mudou `parse_html.py` ou `secoes.yaml`? Não precisa recoletar:

```powershell
python -m ingestion.reextract              # última rodada, só snapshots com PARSER_VERSION antigo
python -m ingestion.reextract --run 12 --todos
```

Atualiza título, status (entre ok/vazio/soft404), seções, texto, links e `extraido`, e recalcula as seções do job. Bytes, hashes, trilha e horário da coleta não mudam. Ao alterar a lógica de extração, incremente `PARSER_VERSION` em `transform/extracao.py`.

### 2.5 Problemas comuns

| Sintoma | Causa provável | Ação |
| --- | --- | --- |
| Preflight: `schema monitor v0.2` com colunas faltando | `sql/schema.sql` atual não foi aplicado | rodar o arquivo inteiro de novo no SQL Editor |
| Preflight: upload 400/404 no Storage | bucket `snapshots` não existe ou chave errada | rodar `sql/schema.sql`; conferir `SUPABASE_SECRET_KEY` (secreta, não a publicável) |
| Preflight: conexão Postgres recusada | DSN errado ou senha com caractere especial não codificado | copiar a URI do Supabase de novo; codificar `@`, `#`, `/` na senha |
| Portal com `tls` | cadeia de certificado incompleta (comum em `.gov.br`) | `verificar_tls: false` no ente, registrado na documentação |
| SPA com `vazio` em todas as páginas | Chromium não instalado | `python -m playwright install chromium` |
| Rodada fica `executando` | processo morto à força (não Ctrl+C) | `update monitor.run set status='falhou' where id=<id>` |

## 3. Passo a passo da configuração

### Passo 1 — Ambiente local

```bash
git clone https://github.com/SEU_USUARIO/portal-transparencia.git && cd portal-transparencia
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium
cp .env.example .env      # preencher na etapa 3
pytest -q                 # 72 testes passam, 1 pulado (integração Postgres)
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

1. SQL Editor → colar e executar `sql/schema.sql` inteiro. Se a v0.1 já foi aplicada, nada é apagado: só entram as colunas e views da seção 6.1. O bucket privado `snapshots` também é criado ali.
2. Project Settings → Database → *Connection string* → **Session pooler** (porta 5432) em `DATABASE_URL`. O Transaction pooler (6543) também funciona — prepared statements já estão desligados.
3. Project Settings → API Keys → `SUPABASE_URL` e a chave secreta (`sb_secret_...`) em `SUPABASE_SECRET_KEY`. A legada `service_role` também funciona na mesma variável.
4. `python -m ingestion.pipeline --entes rj-marica --max-paginas 20` e conferir:

```sql
select * from monitor.v_saude_rodadas order by iniciado_em desc limit 1;
select entity_slug, status, paginas, arquivos, bloqueios_robots, secoes_ausentes from monitor.v_ultima_coleta;
```

### Passo 4 — GitHub Actions

Settings → Secrets and variables → Actions:
- Secrets: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`
- Variable: `CRAWLER_USER_AGENT` com URL do repositório e um e-mail de contato

`ci.yml` roda lint + testes a cada push (com Postgres + pgvector efêmero para o teste de integração). `coleta.yml` roda nos dias 1 e 15 e por *Run workflow* manual. Dispare duas vezes nesta semana para ter histórico real.

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

## 4. Roteiro de testes

### 4.1 Automatizados (offline, `pytest -q`)

| Arquivo | O que garante |
| --- | --- |
| `test_normalize_validate.py` | URL canônica (SPA, utm, relativa), termos por palavra inteira, datas/moeda BR, YAML real válido, erros de config (id duplicado, URL inválida, override sem justificativa), `classify_page` |
| `test_http_client.py` | UA identificado, robots bloqueia/override marca, retry com backoff em 503, `Retry-After`, 404 sem retry, timeout/DNS classificados, teto de bytes, 304, circuit breaker, taxa por domínio |
| `test_discover_parse.py` | Fingerprint WordPress/GeoSiap/Betha/SPA, descoberta de portal/e-SIC/ouvidoria com divergência de config, extração completa de HTML (tabela, form, breadcrumb, acessibilidade, exportação), PDF de RREO (bimestre e período), CSV cp1252 com `;`, XLSX com título antes do cabeçalho, PDF corrompido não derruba |
| `test_crawler_pipeline.py` | Município falso ponta a ponta: seções e trilha, não sai do domínio, ignora notícias, registra robots e link quebrado, baixa RREO e CSV, respeita orçamento, renderiza SPA, SPA sem Chromium vira `vazio`, override, duas rodadas incrementais (sem novo upload, PDF via 304), um ente fora do ar não derruba a rodada, Supabase Storage com duplicado |
| `test_escopo_downloads.py` | Portal descoberto em outro host é expandido; canal externo não é; reserva de orçamento do portal; links de `onclick`/`data-href`/`<select>`; renderização por menu JavaScript e parada sem ganho; download sem extensão (Content-Disposition, Content-Type, assinatura), herança de seção e HTML devolvido à fila; relatório por host |
| `test_execucao.py` | Sem Supabase o pipeline falha em vez de gravar local; relatório com critérios; reextração só refaz versão antiga, recalcula seções e preserva a evidência; sem HTML no Storage mantém a extração antiga; preflight detecta chave publicável, robots, TLS e testa o bucket |
| `test_postgres_integration.py` | Schema v0.2 limpo aplicado 2× e v0.1 → v0.2 (o caminho do Supabase); pipeline grava run/job/snapshots/arquivos em `monitor`, mapeamento de tipos de URL, `texto`/`links` em colunas próprias, vigência sem duplicar na 2ª rodada, views (roda no CI) |

### 4.2 Smoke real por município (manual, após o Passo 3)

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

### 4.3 Medições para o relatório (item 17 do desafio)

```sql
select e.slug, j.paginas, j.requisicoes, j.renderizacoes, j.bytes/1e6 as mb, j.segundos
from monitor.crawl_job j join monitor.entity e on e.id = j.entity_id where j.run_id = <run>;
```

Esses números substituem a hipótese do plano (~100 requisições por portal) e alimentam a estimativa de custo para 5.000 portais/mês.

## 5. Limitações conhecidas

- Não preenche formulários nem pagina tabelas; consultas que só existem após filtro ficam para adaptadores específicos.
- PDF escaneado é marcado (`sem_texto=true`) mas não passa por OCR.
- CAPTCHA/anti-bot não é contornado: vira `erro` ou `vazio`.
- robots.txt ilegível (5xx) é tratado como permissivo; o job não distingue ainda "ausente" de "indisponível".
- Rotas-semente por plataforma estão vazias até serem confirmadas nos portais reais.

## 6. Próximo passo (Camada 2)

As tabelas já existem no `monitor` (criterion, evaluation, criterion_result, evidence, chunk com pgvector, human_review, ground_truth). Falta o `criteria.yaml` gerado a partir da planilha, e os verificadores lendo `page_snapshot.extraido` e `file_artifact.metadados`.
