"""Verificação prévia: tudo pronto para uma rodada gravar no Supabase?

    python -m ingestion.preflight               # tudo
    python -m ingestion.preflight --sem-rede    # pula o teste dos portais
    python -m ingestion.preflight --sem-render  # pula o Chromium

Checa, nesta ordem, e para no primeiro bloqueio que impediria a gravação:
1. variáveis de ambiente (.env)
2. Postgres: conexão, schema `monitor` v0.2 completo, pgvector
3. Storage: bucket acessível com a chave secreta (grava, lê e apaga um objeto de teste)
4. Chromium do Playwright (portais SPA e screenshots)
5. Portais: robots.txt, HTTP e TLS das URLs do municipios.yaml — só diagnóstico, não bloqueia

Código de saída 0 = pode rodar; 1 = algo bloqueante falhou.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from ingestion.extract.http_client import HttpClient
from ingestion.models import Entity
from ingestion.settings import Settings, get_settings
from ingestion.transform.validate import load_entities, load_platforms, load_secoes

# colunas que a coleta grava — se faltar alguma, o sql/schema.sql v0.2 não foi aplicado
COLUNAS_OBRIGATORIAS: dict[str, set[str]] = {
    "entity": {"slug", "cod_ibge", "atualizado_em"},
    "entity_url": {"tipo", "respeita_robots", "fonte", "dominio_gov", "evidencia", "valido_ate"},
    "run": {"gatilho", "engine_version", "git_sha", "status", "parametros"},
    "crawl_job": {"status", "tentativas", "segundos", "plataforma_detectada", "fingerprint", "requisicoes",
                  "renderizacoes", "paginas_inalteradas", "bloqueios_robots", "secoes_encontradas",
                  "secoes_ausentes"},
    "page_snapshot": {"texto", "links", "coleta_status", "trilha", "secoes", "extraido", "etag",
                      "last_modified", "inalterado", "content_type", "tamanho", "robots_override", "erro"},
    "file_artifact": {"entity_id", "texto_link", "paginas", "secoes", "erro", "metadados"},
}


@dataclass
class Check:
    nome: str
    ok: bool
    detalhe: str
    bloqueante: bool = True


def _mask(v: str | None) -> str:
    if not v:
        return "(vazio)"
    return v[:12] + "..." if len(v) > 16 else "***"


# ------------------------------------------------------------------ 1. ambiente

def checar_ambiente(s: Settings) -> list[Check]:
    out = [Check("DATABASE_URL", bool(s.database_url), _host_db(s.database_url))]
    out.append(Check("SUPABASE_URL", bool(s.supabase_url), s.supabase_url or "(vazio)"))
    k = s.supabase_secret_key
    tipo = "nova (sb_secret_)" if k and k.startswith("sb_secret_") else \
        "legada (service_role JWT)" if k and k.startswith("eyJ") else "formato desconhecido"
    out.append(Check("SUPABASE_SECRET_KEY", bool(k), f"{_mask(k)} — chave {tipo}" if k else "(vazio)"))
    if k and k.startswith("sb_publishable_"):
        out[-1] = Check("SUPABASE_SECRET_KEY", False, "é a chave PUBLICÁVEL; use a secreta (sb_secret_...)")
    ua = s.user_agent
    out.append(Check("CRAWLER_USER_AGENT", "SEU_USUARIO" not in ua, ua, bloqueante=False))
    return out


def _host_db(dsn: str | None) -> str:
    if not dsn:
        return "(vazio)"
    p = urlsplit(dsn)
    porta = p.port or 5432
    modo = {6543: "transaction pooler", 5432: "session pooler/direto"}.get(porta, "porta não padrão")
    return f"{p.hostname}:{porta} ({modo})"


# ------------------------------------------------------------------ 2. banco

def checar_banco(dsn: str) -> list[Check]:
    try:
        import psycopg
    except ImportError:
        return [Check("driver psycopg", False, "pip install 'psycopg[binary]'")]
    try:
        conn = psycopg.connect(dsn, autocommit=True, prepare_threshold=None, connect_timeout=15)
    except Exception as e:
        return [Check("conexão Postgres", False, f"{type(e).__name__}: {str(e)[:200]}")]
    out = []
    with conn:
        versao = conn.execute("select current_setting('server_version')").fetchone()[0]
        out.append(Check("conexão Postgres", True, f"PostgreSQL {versao}"))
        rows = conn.execute(
            """select table_name, column_name from information_schema.columns
               where table_schema = 'monitor'""").fetchall()
        cols: dict[str, set[str]] = {}
        for t, c in rows:
            cols.setdefault(t, set()).add(c)
        if not cols:
            out.append(Check("schema monitor", False, "schema `monitor` não existe — rode sql/schema.sql"))
            return out
        faltando = {t: sorted(req - cols.get(t, set())) for t, req in COLUNAS_OBRIGATORIAS.items()}
        faltando = {t: c for t, c in faltando.items() if c}
        out.append(Check("schema monitor v0.2", not faltando,
                         "completo" if not faltando else
                         f"faltam colunas {faltando} — rode sql/schema.sql de novo (não apaga dados)"))
        views = {r[0] for r in conn.execute(
            "select table_name from information_schema.views where table_schema = 'monitor'")}
        req_views = {"v_ultima_coleta", "v_saude_rodadas"}
        out.append(Check("views de monitoramento", req_views <= views,
                         "ok" if req_views <= views else f"faltam {sorted(req_views - views)}", bloqueante=False))
        enum = {r[0] for r in conn.execute(
            """select e.enumlabel from pg_enum e join pg_type t on t.oid = e.enumtypid
               join pg_namespace n on n.oid = t.typnamespace
               where n.nspname = 'monitor' and t.typname = 'status_coleta'""")}
        out.append(Check("enum status_coleta", "bloqueado_robots" in enum,
                         "ok" if enum else "ausente — rode sql/schema.sql"))
        vec = conn.execute("""select n.nspname, e.extversion from pg_extension e
                              join pg_namespace n on n.oid = e.extnamespace where e.extname = 'vector'""").fetchone()
        out.append(Check("pgvector", bool(vec), f"schema {vec[0]}, versão {vec[1]}" if vec else
                         "não instalado (necessário na Camada 2)", bloqueante=False))
        # Camada 2 (schema v0.3): não bloqueia a coleta, mas avisa antes de rodar a conclusão
        v03 = {"texto_hash"} <= cols.get("chunk", set()) and {"sinais"} <= cols.get("criterion_result", set()) \
            and {"engine_version", "modelo_embedding"} <= cols.get("evaluation", set())
        out.append(Check("schema v0.3 (Camada 2)", v03, "ok" if v03 else "rode sql/schema.sql de novo",
                         bloqueante=False))
        dim = conn.execute("""select atttypmod from pg_attribute
                              where attrelid = 'monitor.chunk'::regclass and attname = 'embedding'""").fetchone()
        dim = dim[0] if dim else None
        esperado = get_settings().embeddings_dim
        out.append(Check("dimensão dos embeddings", dim == esperado,
                         f"monitor.chunk.embedding = vector({dim}), EMBEDDINGS_DIM = {esperado}" if dim == esperado else
                         f"coluna vector({dim}) ≠ EMBEDDINGS_DIM={esperado}: rode sql/schema.sql v0.3 "
                         "(com monitor.chunk vazia) ou ajuste EMBEDDINGS_DIM", bloqueante=False))
        n_ent = conn.execute("select count(*) from monitor.entity").fetchone()[0]
        n_run = conn.execute("select count(*) from monitor.run").fetchone()[0]
        out.append(Check("dados existentes", True, f"{n_ent} entes, {n_run} rodadas", bloqueante=False))
    return out


# ------------------------------------------------------------------ 3. storage

def checar_storage(s: Settings, session=None) -> list[Check]:
    from ingestion.load.storage import SupabaseStorage

    st = SupabaseStorage(s.supabase_url, s.supabase_secret_key, s.supabase_bucket, session=session, timeout=30)
    chave = f"preflight/teste-{int(time.time())}.txt"
    url = f"{st.base}/object/{st.bucket}/{chave}"
    try:
        r = st.http.post(url, data=b"preflight", timeout=30,
                         headers={"Content-Type": "text/plain", "x-upsert": "true"})
        if r.status_code not in (200, 201):
            dica = " (bucket inexistente? rode sql/schema.sql)" if r.status_code in (400, 404) and \
                "not found" in r.text.lower() else ""
            return [Check(f"Storage bucket '{st.bucket}'", False, f"upload {r.status_code}: {r.text[:200]}{dica}")]
        g = st.http.get(url, timeout=30)
        lido = g.status_code == 200 and g.content == b"preflight"
        st.http.delete(f"{st.base}/object/{st.bucket}", json={"prefixes": [chave]}, timeout=30)
        return [Check(f"Storage bucket '{st.bucket}'", lido,
                      "grava, lê e apaga" if lido else f"leitura falhou ({g.status_code})")]
    except Exception as e:
        return [Check(f"Storage bucket '{st.bucket}'", False, f"{type(e).__name__}: {str(e)[:200]}")]


# ------------------------------------------------------------------ 4. Chromium

def checar_chromium() -> list[Check]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [Check("Playwright", False, "pip install playwright", bloqueante=False)]
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            v = b.version
            b.close()
        return [Check("Chromium (Playwright)", True, f"versão {v}", bloqueante=False)]
    except Exception as e:
        msg = str(e).splitlines()[0][:160]
        return [Check("Chromium (Playwright)", False,
                      f"{msg} — rode: python -m playwright install chromium (SPAs ficarão 'vazio')",
                      bloqueante=False)]


# ------------------------------------------------------------------ 5. portais

def checar_portais(entes: list[Entity], http: HttpClient) -> list[dict[str, Any]]:
    """Uma requisição por URL configurada. Não bloqueia a rodada: serve para decidir
    robots_override e verificar_tls no municipios.yaml antes de coletar."""
    linhas = []
    for e in entes:
        for tipo, url in e.urls.items():
            robots_ok = http.robots_allowed(url, verify_tls=e.verificar_tls)
            res = http.fetch(url, robots_override=True, verify_tls=e.verificar_tls)
            if res.erro == "tls":
                sugestao = "TLS inválido: avalie `verificar_tls: false` para este ente"
            elif not robots_ok and not e.robots_override:
                sugestao = "robots.txt bloqueia: sairá `bloqueado_robots` (decidir override)"
            elif res.erro in {"dns", "conexao", "timeout"}:
                sugestao = "inacessível agora: conferir URL ou tentar mais tarde"
            elif res.status and res.status >= 400:
                sugestao = f"HTTP {res.status}: conferir URL"
            else:
                sugestao = ""
            linhas.append({
                "ente": e.id, "tipo": tipo, "url": url, "http": res.status,
                "erro": res.erro, "robots": "permite" if robots_ok else "bloqueia",
                "redireciona_para": res.url_final if res.url_final and res.url_final.rstrip("/") != url.rstrip("/") else "",
                "segundos": res.duracao_s, "sugestao": sugestao,
            })
    return linhas


# ------------------------------------------------------------------ CLI

def _imprimir(checks: list[Check]) -> None:
    for c in checks:
        marca = "OK  " if c.ok else ("ERRO" if c.bloqueante else "AVISO")
        print(f"  [{marca}] {c.nome}: {c.detalhe}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verificação prévia da coleta")
    ap.add_argument("--sem-rede", action="store_true", help="não testa os portais")
    ap.add_argument("--sem-render", action="store_true", help="não testa o Chromium")
    ap.add_argument("--entes", help="ids separados por vírgula para o teste de portais")
    a = ap.parse_args(argv)
    s = get_settings()
    todos: list[Check] = []

    print("\n1. Configuração")
    try:
        entes = load_entities(only=a.entes.split(",") if a.entes else None)
        load_platforms(), load_secoes()
        cfg = [Check("YAMLs (municipios, platforms, secoes)", True, f"{len(entes)} entes ativos")]
    except Exception as e:
        entes, cfg = [], [Check("YAMLs", False, str(e)[:300])]
    cfg += checar_ambiente(s)
    _imprimir(cfg)
    todos += cfg

    print("\n2. Banco (Supabase Postgres)")
    db = checar_banco(s.database_url) if s.database_url else [Check("banco", False, "DATABASE_URL ausente")]
    _imprimir(db)
    todos += db

    print("\n3. Storage")
    if s.supabase_url and s.supabase_secret_key:
        st = checar_storage(s)
    else:
        st = [Check("Storage", False, "SUPABASE_URL/SUPABASE_SECRET_KEY ausentes")]
    _imprimir(st)
    todos += st

    if not a.sem_render:
        print("\n4. Navegador")
        ch = checar_chromium()
        _imprimir(ch)
        todos += ch

    if not a.sem_rede and entes:
        print("\n5. Portais (diagnóstico, 1 requisição por URL)")
        http = HttpClient(replace(s, tentativas=1, timeout_s=min(s.timeout_s, 20)))
        for ln in checar_portais(entes, http):
            st_txt = ln["http"] if ln["http"] else ln["erro"]
            extra = f" -> {ln['redireciona_para']}" if ln["redireciona_para"] else ""
            print(f"  {ln['ente']:<26} {ln['tipo']:<21} {str(st_txt):<16} robots:{ln['robots']:<8} "
                  f"{ln['segundos']:>5}s {ln['url']}{extra}")
            if ln["sugestao"]:
                print(f"  {'':<26} -> {ln['sugestao']}")

    bloqueios = [c for c in todos if c.bloqueante and not c.ok]
    print("\n" + ("PRONTO para rodar a coleta." if not bloqueios else
                  f"NÃO PRONTO: {len(bloqueios)} verificação(ões) bloqueante(s) falharam."))
    return 1 if bloqueios else 0


if __name__ == "__main__":
    sys.exit(main())
