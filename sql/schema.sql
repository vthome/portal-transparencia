-- =====================================================================
-- Monitor de Conformidade de Portais da Transparência — schema v0.3
-- Rodar inteiro no Supabase: SQL Editor > New query > colar > Run.
-- Idempotente: pode ser executado de novo sem erro e sem apagar dados.
--
-- v0.1  base: catálogo, execução, coleta, conclusão, views, segurança, bucket
-- v0.2  seção 6.1: colunas que a Camada 1 (coleta) grava + views de monitoramento.
-- v0.3  seção 6.2: Camada 2 — embeddings em 768 dimensões, hash de chunk (reuso entre
--       rodadas), sinais de confiança por resultado, versão do motor na avaliação.
--       Quem já rodou a v0.1/v0.2 só precisa rodar este arquivo de novo.
--
-- Também roda em Postgres puro com pgvector (CI): os trechos exclusivos do
-- Supabase (roles anon/authenticated, storage.buckets) são condicionais.
-- =====================================================================

-- ---------- 1. Extensões ----------
create schema if not exists extensions;                            -- já existe no Supabase
create extension if not exists vector     with schema extensions;  -- pgvector (embeddings)
-- Se o pgvector já estava habilitado em outro schema (ex.: public), o comando acima não faz
-- nada e a extensão continua lá. Por isso, abaixo, `vector` e `vector_cosine_ops` são
-- referenciados SEM schema: o search_path (monitor, public, extensions) encontra os dois casos.
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

-- v0.2: resultado da coleta de uma página. Tudo que não é 'ok'/'nao_modificado'
-- vira 'nao_verificavel' na Camada 2 — falha do robô nunca é falha do ente.
do $$ begin
  create type monitor.status_coleta as enum
    ('ok', 'vazio', 'soft404', 'erro', 'bloqueado_robots', 'nao_modificado');
exception when duplicate_object then null; end $$;

-- ---------- 3. Catálogo ----------
create table if not exists monitor.entity (
  id              bigint generated always as identity primary key,
  slug            text unique not null,               -- = id do municipios.yaml, ex.: 'rj-marica'
  nome            text not null,
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
  fonte           text,                               -- 'config' (YAML) ou 'descoberta' (capa do site)
  valido_de       timestamptz not null default now(),
  valido_ate      timestamptz,                        -- null = vigente
  unique (entity_id, tipo, url, valido_de)
);
create index if not exists ix_entity_url_vigente on monitor.entity_url (entity_id, tipo) where valido_ate is null;

create table if not exists monitor.checklist_version (
  id              bigint generated always as identity primary key,
  nome            text not null unique,
  publicado_em    date not null default current_date,
  sha256          text not null
);

create table if not exists monitor.criterion (
  id                    bigint generated always as identity primary key,
  checklist_version_id  bigint not null references monitor.checklist_version(id),
  codigo                text not null,
  bloco                 text not null,
  dimensao              text not null,
  matriz                text not null check (matriz in ('comum','executivo')),
  texto                 text not null,
  fundamentacao         text,
  peso                  smallint not null check (peso in (1,2,3)),
  essencial             boolean not null default false,
  aplica_a              text[] not null default '{comum}',
  verificador           text not null,
  params                jsonb not null default '{}',
  onda                  smallint check (onda between 1 and 4),
  unique (checklist_version_id, codigo)
);

-- ---------- 4. Execução e coleta ----------
create table if not exists monitor.run (
  id              bigint generated always as identity primary key,
  iniciado_em     timestamptz not null default now(),
  finalizado_em   timestamptz,
  gatilho         text not null default 'manual',     -- 'manual', 'agendado', 'reprocessamento'
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
create index if not exists ix_crawl_job_fila on monitor.crawl_job (status, run_id) where status = 'pendente';

create table if not exists monitor.page_snapshot (
  id              bigint generated always as identity primary key,
  crawl_job_id    bigint not null references monitor.crawl_job(id) on delete cascade,
  entity_id       bigint not null references monitor.entity(id),
  url             text not null,
  url_final       text,
  http_status     smallint,
  content_hash    char(64),
  titulo          text,
  renderizado_js  boolean not null default false,
  profundidade    smallint not null default 0,
  storage_path    text,
  screenshot_path text,
  texto           text,                               -- texto visível extraído
  links           jsonb,                              -- [{url, texto, arquivo, na_capa_topo}]
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
  extensao          text,
  sha256            char(64),
  tamanho           bigint,
  storage_path      text,
  texto_extraido    text,
  metadados         jsonb,
  coletado_em       timestamptz not null default now()
);
create index if not exists ix_file_sha on monitor.file_artifact (sha256);

-- Embeddings: 768 dimensões (SERAFIM 100M / mpnet multilíngue). A seção 6.2 converte
-- instalações antigas em 512. Trocar de dimensão exige esvaziar monitor.chunk antes.
create table if not exists monitor.chunk (
  id                bigint generated always as identity primary key,
  page_snapshot_id  bigint references monitor.page_snapshot(id) on delete cascade,
  file_artifact_id  bigint references monitor.file_artifact(id) on delete cascade,
  ordem             integer not null default 0,
  texto             text not null,
  modelo            text not null,
  embedding         vector(768),
  check (page_snapshot_id is not null or file_artifact_id is not null)
);
create index if not exists ix_chunk_embedding on monitor.chunk
  using hnsw (embedding vector_cosine_ops);

-- ---------- 5. Conclusão ----------
create table if not exists monitor.evaluation (
  id                    bigint generated always as identity primary key,
  run_id                bigint not null references monitor.run(id) on delete cascade,
  entity_id             bigint not null references monitor.entity(id),
  checklist_version_id  bigint not null references monitor.checklist_version(id),
  score                 numeric(5,2),
  score_min             numeric(5,2),
  score_max             numeric(5,2),
  cobertura             numeric(5,2),
  essenciais_ok         boolean,
  classificacao         text,
  scores_bloco          jsonb,
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
  reutilizado_de   bigint references monitor.criterion_result(id),
  unique (evaluation_id, criterion_id)
);
create index if not exists ix_result_criterion on monitor.criterion_result (criterion_id);
create index if not exists ix_result_revisao   on monitor.criterion_result (confianca) where confianca < 0.6;

create table if not exists monitor.evidence (
  id                   bigint generated always as identity primary key,
  criterion_result_id  bigint not null references monitor.criterion_result(id) on delete cascade,
  tipo                 text not null,
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

create table if not exists monitor.ground_truth (
  id             bigint generated always as identity primary key,
  entity_id      bigint not null references monitor.entity(id),
  codigo         text not null,
  fonte          text not null,
  status         monitor.status_criterio not null,
  referencia     text,
  observado_em   date,
  unique (entity_id, codigo, fonte)
);

-- ---------- 6. Views para a API ----------
create or replace view monitor.v_latest_evaluation as
select distinct on (e.entity_id) e.*
from monitor.evaluation e
order by e.entity_id, e.avaliado_em desc;

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

-- =====================================================================
-- 6.1 v0.2 — Camada 1 (coleta): colunas que o pipeline grava
--    `add column if not exists` → seguro sobre a v0.1 já aplicada.
-- =====================================================================

alter table monitor.entity
  add column if not exists atualizado_em timestamptz not null default now();

alter table monitor.entity_url
  add column if not exists dominio_gov boolean,                         -- termina em .gov.br? (quesito 1.1)
  add column if not exists evidencia   jsonb not null default '{}';     -- texto do link, página de origem, pontos
-- uma URL vigente por (ente, tipo, url): permite atualizar sem duplicar a cada rodada
create unique index if not exists ux_entity_url_vigente_url
  on monitor.entity_url (entity_id, tipo, url) where valido_ate is null;

alter table monitor.run
  add column if not exists parametros jsonb not null default '{}';      -- orçamento, entes, render...

alter table monitor.crawl_job
  add column if not exists plataforma_detectada text,
  add column if not exists fingerprint          jsonb not null default '{}',   -- por host
  add column if not exists requisicoes          integer not null default 0,
  add column if not exists renderizacoes        integer not null default 0,
  add column if not exists paginas_inalteradas  integer not null default 0,
  add column if not exists bloqueios_robots     integer not null default 0,
  add column if not exists secoes_encontradas   text[] not null default '{}',
  add column if not exists secoes_ausentes      text[] not null default '{}';

alter table monitor.page_snapshot
  add column if not exists content_type    text,
  add column if not exists tamanho         bigint,
  add column if not exists coleta_status   monitor.status_coleta,
  add column if not exists erro            text,
  add column if not exists robots_override boolean not null default false,
  add column if not exists trilha          jsonb not null default '[]',  -- cliques desde a capa
  add column if not exists secoes          text[] not null default '{}', -- seções do secoes.yaml
  add column if not exists extraido        jsonb not null default '{}',  -- tabelas, forms, datas, breadcrumbs...
  add column if not exists etag            text,
  add column if not exists last_modified   text,
  add column if not exists inalterado      boolean not null default false;
create index if not exists ix_snapshot_status on monitor.page_snapshot (coleta_status);
create index if not exists ix_snapshot_secoes on monitor.page_snapshot using gin (secoes);
comment on column monitor.page_snapshot.content_hash is 'sha256 do corpo bruto (= chave no Storage)';
comment on column monitor.page_snapshot.extraido is 'saída de parse_html sem texto/links (que têm colunas próprias); texto_hash detecta mudança';

alter table monitor.file_artifact
  add column if not exists entity_id   bigint references monitor.entity(id),
  add column if not exists texto_link  text,
  add column if not exists paginas     integer,
  add column if not exists secoes      text[] not null default '{}',
  add column if not exists erro        text;
create index if not exists ix_file_entity_url on monitor.file_artifact (entity_id, url, coletado_em desc);

-- Views de monitoramento da coleta (página "monitoramento" do dashboard)
create or replace view monitor.v_ultima_coleta as
select distinct on (j.entity_id)
       j.entity_id, e.slug as entity_slug, e.nome, j.run_id, j.status, j.plataforma_detectada,
       j.paginas, j.arquivos, j.requisicoes, j.renderizacoes, j.bloqueios_robots,
       j.secoes_encontradas, j.secoes_ausentes, j.segundos, j.erro, j.finalizado_em
from monitor.crawl_job j join monitor.entity e on e.id = j.entity_id
order by j.entity_id, j.iniciado_em desc nulls last;

create or replace view monitor.v_saude_rodadas as
select r.id as run_id, r.gatilho, r.status, r.iniciado_em, r.finalizado_em,
       count(j.id)                                     as entes,
       count(*) filter (where j.status = 'concluido')  as concluidos,
       count(*) filter (where j.status = 'parcial')    as parciais,
       count(*) filter (where j.status = 'falhou')     as falhas,
       coalesce(sum(j.paginas), 0)  as paginas,
       coalesce(sum(j.arquivos), 0) as arquivos,
       coalesce(sum(j.bytes), 0)    as bytes
from monitor.run r left join monitor.crawl_job j on j.run_id = r.id
group by r.id;

-- =====================================================================
-- 6.2 v0.3 — Camada 2 (conclusão)
-- =====================================================================

-- Dimensão do embedding: 768. Só converte se a tabela estiver vazia (vetores de
-- dimensões diferentes não são comparáveis; recalcular é obrigatório).
do $$
declare atual int; n bigint;
begin
  select a.atttypmod into atual from pg_attribute a
   where a.attrelid = 'monitor.chunk'::regclass and a.attname = 'embedding';
  if atual is distinct from 768 then
    select count(*) into n from monitor.chunk;
    if n > 0 then
      raise exception 'monitor.chunk tem % linhas com dimensão %; rode "truncate monitor.chunk" antes de mudar para 768', n, atual;
    end if;
    drop index if exists monitor.ix_chunk_embedding;
    execute 'alter table monitor.chunk alter column embedding type vector(768)';
  end if;
end $$;
create index if not exists ix_chunk_embedding on monitor.chunk
  using hnsw (embedding vector_cosine_ops);

alter table monitor.chunk
  add column if not exists texto_hash char(64),                  -- sha256(modelo + texto): reuso entre rodadas
  add column if not exists criado_em  timestamptz not null default now();
create index if not exists ix_chunk_hash on monitor.chunk (modelo, texto_hash);
create index if not exists ix_chunk_page on monitor.chunk (page_snapshot_id);
create index if not exists ix_chunk_file on monitor.chunk (file_artifact_id);

alter table monitor.criterion
  add column if not exists params_hash char(64);                  -- muda quando o verificador/params mudam

alter table monitor.evaluation
  add column if not exists engine_version text,
  add column if not exists modelo_embedding text;

alter table monitor.criterion_result
  add column if not exists sinais jsonb not null default '{}';    -- entradas da confiança (auditoria e calibração)

-- `e.*` numa view é expandido quando ela é criada: recriar aqui, depois das colunas novas,
-- para que engine_version e modelo_embedding apareçam já na primeira execução.
create or replace view monitor.v_latest_evaluation as
select distinct on (e.entity_id) e.*
from monitor.evaluation e
order by e.entity_id, e.avaliado_em desc;

-- Resultado atual por ente e critério (última avaliação), para a API e o dashboard
create or replace view monitor.v_resultado_atual as
select ev.entity_id, e.slug as entity_slug, c.codigo, c.bloco, c.dimensao, c.peso, c.essencial,
       cr.status, cr.nota, cr.confianca, cr.metodo, cr.justificativa, ev.run_id, ev.avaliado_em
from monitor.v_latest_evaluation ev
join monitor.entity e on e.id = ev.entity_id
join monitor.criterion_result cr on cr.evaluation_id = ev.id
join monitor.criterion c on c.id = cr.criterion_id;

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

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on schema monitor from anon, authenticated;
  end if;
end $$;

-- ---------- 8. Storage ----------
-- Bucket privado para HTML, screenshots e arquivos (chave = sha256, deduplica entre rodadas).
-- Caminhos: html/<aa>/<sha256>.html · png/<aa>/<sha256>.png · files/<aa>/<sha256>.<ext>
do $$ begin
  if to_regclass('storage.buckets') is not null then
    insert into storage.buckets (id, name, public, file_size_limit)
    values ('snapshots', 'snapshots', false, 52428800)   -- 50 MB, limite do plano Free
    on conflict (id) do nothing;
  end if;
end $$;