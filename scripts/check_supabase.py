"""Verifica se o Supabase está pronto para a etapa de coleta.

Uso:
    pip install "psycopg[binary]" httpx python-dotenv
    python scripts/check_supabase.py

Checa: conexão Postgres pelo pooler, extensões, schema `monitor`, RLS,
e escrita/leitura/remoção no bucket `snapshots` do Storage.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
ENV_PATH = RAIZ / ".env"
OBRIGATORIAS = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY")


def diagnosticar_env() -> None:
    """Explica por que o .env não foi lido, sem nunca imprimir segredos."""
    print(f".env esperado em: {ENV_PATH}")
    if not ENV_PATH.is_file():
        parecidos = sorted(p.name for p in RAIZ.glob("*env*") if p.is_file())
        print("  [FALHOU] Arquivo .env não encontrado na raiz do repositório.")
        if parecidos:
            print(f"  Arquivos parecidos na raiz: {', '.join(parecidos)}")
        if any(n.lower().startswith(".env.") and n.lower().endswith(".txt") for n in parecidos):
            print("  O Windows salvou como .env.txt. Renomeie: Rename-Item .env.txt .env")
        sys.exit(1)

    bruto = ENV_PATH.read_bytes()
    if bruto[:2] in (b"\xff\xfe", b"\xfe\xff"):
        print("  [FALHOU] .env está em UTF-16 (comum ao criar com 'echo >' no PowerShell).")
        print("  Abra no VS Code/Bloco de Notas e salve como UTF-8.")
        sys.exit(1)

    texto = bruto.decode("utf-8-sig", errors="replace")
    linhas = texto.splitlines()
    for i, linha in enumerate(linhas, 1):
        s = linha.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            print(f"  [ATENÇÃO] Linha {i} não tem '=' — provavelmente é a continuação da linha anterior."
                  " Cada variável precisa ficar inteira numa única linha.")
            continue
        chave, valor = s.split("=", 1)
        valor = valor.strip()
        if valor[:1] in "\"'" and (len(valor) < 2 or valor[-1] != valor[0]):
            print(f"  [ATENÇÃO] Linha {i} ({chave.strip()}): aspas abertas e não fechadas."
                  " Remova as aspas ou feche-as no fim da linha.")

    valores = dotenv_values(ENV_PATH, encoding="utf-8-sig")
    print(f"  Variáveis lidas: {', '.join(k for k, v in valores.items() if v) or 'nenhuma'}")
    faltando = [k for k in OBRIGATORIAS if not (valores.get(k) or "").strip()]
    for k in faltando:
        print(f"  [FALHOU] {k} ausente ou vazia no .env")

    dsn = (valores.get("DATABASE_URL") or "").strip()
    if dsn:
        m = re.match(r"^postgres(?:ql)?://([^:@/]+):(.*)@([^@:/]+):(\d+)/(\w+)$", dsn)
        if not m:
            print("  [FALHOU] DATABASE_URL incompleta. Formato esperado:")
            print("           postgresql://postgres.<ref>:<senha>@<host>.pooler.supabase.com:5432/postgres")
            inicio = re.match(r"^[a-z]+://[^:@/]*", dsn)
            print(f"           Lido: {inicio.group(0) if inicio else '?'}… ({len(dsn)} caracteres no total;"
                  " se for curto, a string quebrou de linha ou foi cortada ao colar)")
            faltando.append("DATABASE_URL")
        else:
            usuario, senha, host, porta, banco = m.groups()
            print(f"  DATABASE_URL: usuário={usuario} senha=({len(senha)} caracteres) host={host} porta={porta} banco={banco}")
            if "YOUR-PASSWORD" in senha or senha.startswith("["):
                print("  [FALHOU] A senha ainda é o marcador [YOUR-PASSWORD] ou está entre colchetes. Troque pela senha real, sem colchetes.")
                faltando.append("DATABASE_URL")
            if re.search(r"[@/?#\s]", senha):
                print("  [ATENÇÃO] A senha tem @ / ? # ou espaço sem codificação (@=%40, /=%2F, ?=%3F, #=%23).")
            if "pooler.supabase.com" not in host:
                print("  [ATENÇÃO] Host não é o pooler. Use Connect > Connection string > Session pooler.")
            if porta != "5432":
                print("  [ATENÇÃO] Porta diferente de 5432. Use o Session pooler (5432), não o Transaction (6543).")

    if faltando:
        sys.exit(1)
    load_dotenv(ENV_PATH, override=True, encoding="utf-8-sig")
    print("  [OK] .env lido\n")


diagnosticar_env()

import httpx  # noqa: E402  (importados depois do diagnóstico para ele rodar mesmo sem as libs)
import psycopg  # noqa: E402

TABELAS = {
    "entity", "entity_url", "checklist_version", "criterion", "run", "crawl_job",
    "page_snapshot", "file_artifact", "chunk", "evaluation", "criterion_result",
    "evidence", "human_review", "ground_truth",
}
EXTENSOES = {"vector", "pg_trgm", "uuid-ossp"}

ok_total = True


def check(nome: str, ok: bool, detalhe: str = "") -> None:
    global ok_total
    ok_total &= ok
    print(f"[{'OK' if ok else 'FALHOU'}] {nome}" + (f" — {detalhe}" if detalhe else ""))


def env(nome: str) -> str:
    valor = os.getenv(nome, "").strip()
    if not valor:
        print(f"Variável {nome} ausente no .env")
        sys.exit(1)
    return valor


def checar_banco(dsn: str) -> None:
    try:
        conn = psycopg.connect(dsn, connect_timeout=15)
    except Exception as exc:  # noqa: BLE001
        check("Conexão Postgres", False, str(exc).splitlines()[0])
        print("  Dica: use a string do 'Session pooler' (porta 5432) e confira a senha.")
        return
    with conn, conn.cursor() as cur:
        cur.execute("select version()")
        check("Conexão Postgres", True, cur.fetchone()[0].split(",")[0])

        cur.execute("select extname from pg_extension")
        instaladas = {r[0] for r in cur.fetchall()}
        faltando = EXTENSOES - instaladas
        check("Extensões (vector, pg_trgm, uuid-ossp)", not faltando,
              f"faltando: {', '.join(sorted(faltando))}" if faltando else "")

        cur.execute("select tablename, rowsecurity from pg_tables where schemaname = 'monitor'")
        linhas = dict(cur.fetchall())
        faltando = TABELAS - set(linhas)
        check(f"Schema monitor ({len(TABELAS)} tabelas)", not faltando,
              f"faltando: {', '.join(sorted(faltando))}" if faltando else "")
        sem_rls = [t for t, rls in linhas.items() if not rls]
        check("RLS ligado em todas as tabelas", not sem_rls, ", ".join(sem_rls))

        cur.execute("select id, public from storage.buckets where id = %s",
                    (os.getenv("SUPABASE_BUCKET", "snapshots"),))
        bucket = cur.fetchone()
        check("Bucket existe e é privado", bool(bucket) and bucket[1] is False,
              "" if bucket else "rode a seção 8 do schema.sql")


def checar_storage(url: str, chave: str, bucket: str) -> None:
    headers = {"apikey": chave}
    if chave.startswith("eyJ"):  # chave legada (JWT) também exige Authorization
        headers["Authorization"] = f"Bearer {chave}"
    caminho = f"healthcheck/{uuid.uuid4().hex}.txt"
    base = f"{url.rstrip('/')}/storage/v1/object"
    try:
        with httpx.Client(timeout=20) as http:
            r = http.post(f"{base}/{bucket}/{caminho}", headers={**headers, "Content-Type": "text/plain"},
                          content=b"ok")
            check("Storage: upload", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:120]}")
            r = http.get(f"{base}/authenticated/{bucket}/{caminho}", headers=headers)
            check("Storage: download", r.status_code == 200 and r.content == b"ok", f"HTTP {r.status_code}")
            r = http.request("DELETE", f"{base}/{bucket}", headers=headers, json={"prefixes": [caminho]})
            check("Storage: remoção", r.status_code == 200, f"HTTP {r.status_code}")
    except httpx.HTTPError as exc:
        check("Storage", False, str(exc))


if __name__ == "__main__":
    checar_banco(env("DATABASE_URL"))
    checar_storage(env("SUPABASE_URL"), env("SUPABASE_SECRET_KEY"), os.getenv("SUPABASE_BUCKET", "snapshots"))
    print("\nTudo pronto para a coleta." if ok_total else "\nCorrija os itens acima antes de seguir.")
    sys.exit(0 if ok_total else 1)
