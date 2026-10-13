-- =====================================================================
-- Usuário de banco só para a API (Render) — opcional, recomendado.
-- Lê o schema `monitor` e só pode GRAVAR revisões humanas. Se a senha vazar,
-- ninguém apaga coletas nem avaliações.
--
-- A SENHA É VOCÊ QUEM INVENTA (o usuário monitor_api está sendo criado agora).
--   1. Gere uma senha forte, sem @ : / ? # (atrapalham a URL). No PowerShell:
--        python -c "import secrets; print(secrets.token_urlsafe(24))"
--   2. Copie este arquivo para o SQL Editor do Supabase e troque TROQUE_ESTA_SENHA
--      SÓ LÁ, no editor. Não salve a senha neste arquivo (ele vai para o GitHub).
--   3. Run. Guarde a senha: ela vai apenas no DATABASE_URL do Render:
--      postgresql://monitor_api.<PROJECT_REF>:<SENHA>@<HOST>:5432/postgres
--      <HOST> = o MESMO da DATABASE_URL do .env (aws-0-... ou aws-1-...: varia por projeto)
--      (no pooler, o usuário leva o sufixo .<PROJECT_REF>, igual ao postgres.<PROJECT_REF>)
--
-- Rodar de novo (ex.: depois de recriar o schema) só reaplica as permissões: a senha
-- definida na criação NÃO é alterada, então pode rodar o arquivo como está no repositório.
-- Para trocar a senha depois:  alter role monitor_api password 'nova-senha';
-- =====================================================================

do $$
declare
  senha text := 'TROQUE_ESTA_SENHA';      -- <<< troque aqui, no SQL Editor, só na primeira vez
begin
  if not exists (select 1 from pg_roles where rolname = 'monitor_api') then
    if senha = 'TROQUE_ESTA_SENHA' or length(senha) < 16 then
      raise exception 'Defina uma senha própria (16+ caracteres) na linha "senha text := ..." antes de rodar.';
    end if;
    execute format('create role monitor_api login password %L', senha);
    raise notice 'usuário monitor_api criado';
  else
    raise notice 'monitor_api já existe: senha mantida, permissões reaplicadas';
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
