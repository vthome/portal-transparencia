-- =====================================================================
-- Usuário de banco só para a API (Render) — opcional, recomendado.
-- Lê o schema `monitor` e só pode GRAVAR revisões humanas. Se a chave vazar,
-- ninguém apaga coletas nem avaliações.
--
-- 1. Troque a senha abaixo (e guarde-a: vai no DATABASE_URL do Render).
-- 2. Rode este arquivo no SQL Editor do Supabase (depois do sql/schema.sql).
-- 3. DATABASE_URL no Render (Session pooler, porta 5432):
--    postgresql://monitor_api.<PROJECT_REF>:<SENHA>@aws-0-sa-east-1.pooler.supabase.com:5432/postgres
--    (no pooler, o usuário leva o sufixo .<PROJECT_REF>, igual ao postgres.<PROJECT_REF>)
-- Idempotente: rodar de novo só reaplica permissões (e redefine a senha).
-- =====================================================================

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'monitor_api') then
    create role monitor_api login password 'TROQUE_ESTA_SENHA';
  else
    alter role monitor_api login password 'TROQUE_ESTA_SENHA';
  end if;
end $$;

alter role monitor_api set statement_timeout = '15s';
alter role monitor_api set search_path = monitor, public, extensions;

grant usage on schema monitor to monitor_api;
-- tipo `vector` e operador <=> da busca semântica: o pgvector pode estar em public ou extensions
grant usage on schema public to monitor_api;
do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'extensions') then
    execute 'grant usage on schema extensions to monitor_api';
  end if;
end $$;
grant select on all tables in schema monitor to monitor_api;          -- tabelas e views
alter default privileges in schema monitor grant select on tables to monitor_api;
grant insert on monitor.human_review to monitor_api;

-- O schema liga RLS em todas as tabelas sem políticas (só o dono enxerga).
-- Políticas explícitas para este usuário: lê tudo, insere só revisões.
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'monitor' loop
    execute format('drop policy if exists api_leitura on monitor.%I', t);
    execute format('create policy api_leitura on monitor.%I for select to monitor_api using (true)', t);
  end loop;
end $$;

drop policy if exists api_revisao on monitor.human_review;
create policy api_revisao on monitor.human_review for insert to monitor_api
  with check (length(trim(revisor)) >= 2);
