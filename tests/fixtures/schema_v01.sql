-- =====================================================================
-- Monitor de Conformidade de Portais da Transparência — schema v0.1
-- Rodar inteiro no Supabase: SQL Editor > New query > colar > Run.
-- Idempotente: pode ser executado de novo sem erro.
-- =====================================================================

-- ---------- 1. Extensões ----------
create extension if not exists vector     with schema extensions;  -- pgvector (embeddings)
create extension if not exists pg_trgm    with schema extensions;  -- busca fuzzy por texto
create extension if not exists "uuid-ossp" with schema extensions;

-- Schema próprio: separa o projeto do "public" e não fica exposto na Data API
create schema if not exists monitor;
set search_path = monitor, public, extensions;

-- ---------- 2. Tipos ----------
do $$ begin
  create type monitor.status_criterio as enum
    ('atende', 'parcial', 'nao_atende', 'nao_aplicavel', 'nao_verificavel');
exception when duplicate_object then null; end $$;

do $$ begin
  create type monitor.status_execucao as enum
    ('pendente', 'executando', 'concluido', 'falhou', 'parcial');
exception when duplicate_object then null; end $$;

do $$ begin
  create type monitor.metodo_avaliacao as enum
    ('regra', 'embedding', 'llm', 'humano', 'pendente');
exception when duplicate_object then null; end $$;

-- ---------- 3. Catálogo ----------
create table if not exists monitor.entity (
  id              bigint generated always as identity primary key,
  slug            text unique not null,               -- ex.: 'pm-marica'
  nome            text not null,                      -- 'Prefeitura Municipal de Maricá'
  municipio       text not null,
  cod_ibge        char(7),
  uf              char(2) not null default 'RJ',
  tipo_entidade   text not null,                      -- 'prefeitura', 'camara', ...
  poder           text not null,                      -- 'executivo', 'legislativo', ...
  esfera          text not null default 'municipal',
  cnpj            text,
  ativo           boolean not null default true,
  criado_em       timestamptz not null default now()
);

create table if not exists monitor.entity_url (
  id              bigint generated always as identity primary key,
  entity_id       bigint not null references monitor.entity(id) on delete cascade,
  tipo            text not null check (tipo in ('site','portal','esic','ouvidoria','diario_oficial','dados_abertos','lgpd','outro')),
  url             text not null,
  plataforma      text,                               -- 'geosiap', 'betha', 'ecidade', 'wordpress', ...
  respeita_robots boolean not null default true,      -- override documentado no YAML
  fonte           text,                               -- de onde veio a URL
  valido_de       timestamptz not null default now(),
  valido_ate      timestamptz,                        -- null = vigente
  unique (entity_id, tipo, url, valido_de)
);
create index if not exists ix_entity_url_vigente on monitor.entity_url (entity_id, tipo) where valido_ate is null;

create table if not exists monitor.checklist_version (
  id              bigint generated always as identity primary key,
  nome            text not null unique,               -- 'checklistPortalTransp v1'
  publicado_em    date not null default current_date,
  sha256          text not null                       -- hash do criteria.yaml
);

create table if not exists monitor.criterion (
  id                    bigint generated always as identity primary key,
  checklist_version_id  bigint not null references monitor.checklist_version(id),
  codigo                text not null,                -- '4.1'
  bloco                 text not null,                -- 'DESPESA'
  dimensao              text not null,                -- uma das 5 dimensões do score
  matriz                text not null check (matriz in ('comum','executivo')),
  texto                 text not null,
  fundamentacao         text,
  peso                  smallint not null check (peso in (1,2,3)),
  essencial             boolean not null default false,
  aplica_a              text[] not null default '{comum}',
  verificador           text not null,                -- 'structured_fields', ...
  params                jsonb not null default '{}',
  onda                  smallint check (onda between 1 and 4),
  unique (checklist_version_id, codigo)
);

-- ---------- 4. Execução e coleta ----------
create table if not exists monitor.run (
  id              bigint generated always as identity primary key,
  iniciado_em     timestamptz not null default now(),
  finalizado_em   timestamptz,
  gatilho         text not null default 'manual',     -- 'manual', 'cron', 'api'
  engine_version  text not null,
  git_sha         text,
  status          monitor.status_execucao not null default 'executando',
  observacao      text
);

create table if not exists monitor.crawl_job (
  id              bigint generated always as identity primary key,
  run_id          bigint not null references monitor.run(id) on delete cascade,
  entity_id       bigint not null references monitor.entity(id),
  status          monitor.status_execucao not null default 'pendente',
  tentativas      smallint not null default 0,
  erro            text,
  paginas         integer not null default 0,
  arquivos        integer not null default 0,
  bytes           bigint not null default 0,
  segundos        numeric(10,2),
  chamadas_llm    integer not null default 0,
  custo_llm_usd   numeric(10,4) not null default 0,
  iniciado_em     timestamptz,
  finalizado_em   timestamptz,
  unique (run_id, entity_id)
);
-- fila simples: worker pega com SELECT ... FOR UPDATE SKIP LOCKED
create index if not exists ix_crawl_job_fila on monitor.crawl_job (status, run_id) where status = 'pendente';

create table if not exists monitor.page_snapshot (
  id              bigint generated always as identity primary key,
  crawl_job_id    bigint not null references monitor.crawl_job(id) on delete cascade,
  entity_id       bigint not null references monitor.entity(id),
  url             text not null,
  url_final       text,                               -- após redirecionamentos
  http_status     smallint,
  content_hash    char(64),                           -- sha256 do HTML normalizado
  titulo          text,
  renderizado_js  boolean not null default false,
  profundidade    smallint not null default 0,
  storage_path    text,                               -- snapshots/html/<sha256>.html.gz
  screenshot_path text,                               -- snapshots/png/<sha256>.png
  texto           text,                               -- texto visível extraído
  links           jsonb,                              -- [{href, texto}]
  coletado_em     timestamptz not null default now()
);
create index if not exists ix_snapshot_job      on monitor.page_snapshot (crawl_job_id);
create index if not exists ix_snapshot_url      on monitor.page_snapshot (entity_id, url, coletado_em desc);
create index if not exists ix_snapshot_hash     on monitor.page_snapshot (content_hash);
create index if not exists ix_snapshot_texto    on monitor.page_snapshot using gin (texto gin_trgm_ops);

create table if not exists monitor.file_artifact (
  id                bigint generated always as identity primary key,
  page_snapshot_id  bigint not null references monitor.page_snapshot(id) on delete cascade,
  url               text not null,
  mime              text,
  extensao          text,                             -- 'pdf', 'csv', 'xlsx', ...
  sha256            char(64),
  tamanho           bigint,
  storage_path      text,                             -- snapshots/files/<sha256>.<ext>
  texto_extraido    text,
  metadados         jsonb,                            -- tipo de documento, exercício, período...
  coletado_em       timestamptz not null default now()
);
create index if not exists ix_file_sha on monitor.file_artifact (sha256);

-- Embeddings: 512 = DistilUSE (distiluse-base-multilingual-cased-v2).
-- Se optar pelo SERAFIM, troque 512 pela dimensão do modelo escolhido ANTES da 1ª carga.
create table if not exists monitor.chunk (
  id                bigint generated always as identity primary key,
  page_snapshot_id  bigint references monitor.page_snapshot(id) on delete cascade,
  file_artifact_id  bigint references monitor.file_artifact(id) on delete cascade,
  ordem             integer not null default 0,
  texto             text not null,
  modelo            text not null,
  embedding         extensions.vector(512),
  check (page_snapshot_id is not null or file_artifact_id is not null)
);
create index if not exists ix_chunk_embedding on monitor.chunk
  using hnsw (embedding extensions.vector_cosine_ops);

-- ---------- 5. Conclusão ----------
create table if not exists monitor.evaluation (
  id                    bigint generated always as identity primary key,
  run_id                bigint not null references monitor.run(id) on delete cascade,
  entity_id             bigint not null references monitor.entity(id),
  checklist_version_id  bigint not null references monitor.checklist_version(id),
  score                 numeric(5,2),                 -- 0–100
  score_min             numeric(5,2),                 -- não verificáveis = 0
  score_max             numeric(5,2),                 -- não verificáveis = 1
  cobertura             numeric(5,2),                 -- % do peso aplicável verificado
  essenciais_ok         boolean,
  classificacao         text,
  scores_bloco          jsonb,                        -- {"DESPESA": 81.2, ...}
  pesos_usados          jsonb,
  avaliado_em           timestamptz not null default now(),
  unique (run_id, entity_id)
);
create index if not exists ix_eval_entity on monitor.evaluation (entity_id, avaliado_em desc);

create table if not exists monitor.criterion_result (
  id               bigint generated always as identity primary key,
  evaluation_id    bigint not null references monitor.evaluation(id) on delete cascade,
  criterion_id     bigint not null references monitor.criterion(id),
  status           monitor.status_criterio not null,
  nota             numeric(3,2) check (nota in (0, 0.5, 1)),
  confianca        numeric(4,3) check (confianca between 0 and 1),
  metodo           monitor.metodo_avaliacao not null,
  checker_version  text not null,
  justificativa    text,
  reutilizado_de   bigint references monitor.criterion_result(id),  -- cache: conteúdo não mudou
  unique (evaluation_id, criterion_id)
);
create index if not exists ix_result_criterion on monitor.criterion_result (criterion_id);
create index if not exists ix_result_revisao   on monitor.criterion_result (confianca) where confianca < 0.6;

create table if not exists monitor.evidence (
  id                   bigint generated always as identity primary key,
  criterion_result_id  bigint not null references monitor.criterion_result(id) on delete cascade,
  tipo                 text not null,   -- 'link','trecho','tabela','arquivo','formulario','screenshot','trilha','externo'
  url                  text,
  page_snapshot_id     bigint references monitor.page_snapshot(id),
  file_artifact_id     bigint references monitor.file_artifact(id),
  trecho               text,
  seletor_css          text,
  screenshot_path      text,
  metadados            jsonb
);
create index if not exists ix_evidence_result on monitor.evidence (criterion_result_id);

create table if not exists monitor.human_review (
  id                   bigint generated always as identity primary key,
  criterion_result_id  bigint not null references monitor.criterion_result(id) on delete cascade,
  revisor              text not null,
  status_corrigido     monitor.status_criterio not null,
  comentario           text,
  revisado_em          timestamptz not null default now()
);

-- Rótulos externos (PNTP / Radar da Transparência) para validação do motor
create table if not exists monitor.ground_truth (
  id             bigint generated always as identity primary key,
  entity_id      bigint not null references monitor.entity(id),
  codigo         text not null,          -- código do quesito no checklist
  fonte          text not null,          -- 'pntp_2025', 'manual'
  status         monitor.status_criterio not null,
  referencia     text,                   -- id do critério na fonte original
  observado_em   date,
  unique (entity_id, codigo, fonte)
);

-- ---------- 6. Views para a API ----------
create or replace view monitor.v_latest_evaluation as
select distinct on (e.entity_id) e.*
from monitor.evaluation e
order by e.entity_id, e.avaliado_em desc;

-- Linha do tempo por (entidade, critério): marca correções e regressões
create or replace view monitor.v_criterion_timeline as
with r as (
  select ev.entity_id, c.codigo, ev.avaliado_em, cr.status, cr.nota, cr.confianca,
         lag(cr.nota) over (partition by ev.entity_id, c.codigo order by ev.avaliado_em) as nota_anterior
  from monitor.criterion_result cr
  join monitor.evaluation ev on ev.id = cr.evaluation_id
  join monitor.criterion  c  on c.id  = cr.criterion_id
)
select *,
  case
    when nota_anterior is null                    then 'primeira'
    when nota is null or nota_anterior is null    then 'indefinido'
    when nota > nota_anterior                     then 'corrigido'
    when nota < nota_anterior                     then 'regrediu'
    else 'estavel'
  end as evento
from r;

-- ---------- 7. Segurança ----------
-- RLS ligado em tudo e SEM políticas: só o backend (conexão Postgres / chave secreta)
-- acessa. O dashboard lê pela FastAPI, nunca direto do banco.
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'monitor' loop
    execute format('alter table monitor.%I enable row level security', t);
  end loop;
end $$;

revoke all on schema monitor from anon, authenticated;

-- ---------- 8. Storage ----------
-- Bucket privado para HTML, screenshots e arquivos (chave = sha256, deduplica entre rodadas).
insert into storage.buckets (id, name, public, file_size_limit)
values ('snapshots', 'snapshots', false, 52428800)   -- 50 MB, limite do plano Free
on conflict (id) do nothing;
