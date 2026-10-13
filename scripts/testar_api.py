"""Teste de fumaça da API publicada (Render) ou local — só leitura por padrão.

    python scripts/testar_api.py --url https://monitor-transparencia-api.onrender.com
    python scripts/testar_api.py --url http://127.0.0.1:8000
    python scripts/testar_api.py --url ... --token SEU_API_TOKEN_REVISAO --revisao   # grava 1 revisão de teste

No plano free do Render a 1ª chamada depois de ~15 min parado acorda o serviço (pode levar ~1 min):
por isso o /health tem timeout maior.
"""
from __future__ import annotations

import argparse
import sys
import time

import requests


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Teste de fumaça da API do monitor")
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", help="X-API-Key (leitura protegida ou revisão)")
    ap.add_argument("--revisao", action="store_true", help="grava uma revisão de teste (exige --token de revisão)")
    a = ap.parse_args(argv)
    base = a.url.rstrip("/")
    h = {"X-API-Key": a.token} if a.token else {}
    falhas, ctx = 0, {}

    def chamar(nome, metodo, caminho, esperado=(200,), timeout=30, sem_chave=False, **kw):
        nonlocal falhas
        esperado = (esperado,) if isinstance(esperado, int) else esperado
        t0 = time.monotonic()
        try:
            r = requests.request(metodo, base + caminho, headers={} if sem_chave else h, timeout=timeout, **kw)
            ok = r.status_code in esperado
            corpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else None
        except requests.RequestException as e:
            ok, corpo, r = False, None, type("R", (), {"status_code": type(e).__name__})()
        ms = (time.monotonic() - t0) * 1000
        print(f"[{'OK ' if ok else 'ERRO'}] {nome:<34} {r.status_code}  {ms:7.0f} ms  {metodo} {caminho}")
        falhas += not ok
        return corpo if ok else None

    print(f"API: {base}\n")
    hc = chamar("saúde (acorda o serviço)", "GET", "/health", timeout=120)
    if hc:
        print(f"       banco={hc['banco']} última rodada={hc.get('ultima_rodada')} avaliações={hc.get('avaliacoes')} "
              f"busca={hc['busca']} schema_v03={hc.get('schema_v03')}")
    chamar("documentação Swagger", "GET", "/openapi.json")
    entes = chamar("entes", "GET", "/entes") or []
    for e in entes:
        av = e.get("avaliacao") or {}
        print(f"       {e['slug']:<28} score={av.get('score')} ({av.get('score_min')}–{av.get('score_max')}) "
              f"cobertura={av.get('cobertura')}% {av.get('classificacao')}")
    chamar("ranking", "GET", "/indicadores/ranking")
    chamar("matriz de dimensões", "GET", "/indicadores/dimensoes")
    avaliados = [e for e in entes if e.get("avaliacao")]
    if avaliados:
        slug = avaliados[0]["slug"]
        chamar(f"detalhe {slug}", "GET", f"/entes/{slug}")
        chamar("histórico", "GET", f"/entes/{slug}/historico")
        rs = chamar("resultados por quesito", "GET", f"/entes/{slug}/resultados") or []
        if rs:
            ctx["resultado"] = rs[0]["id"]
            chamar("detalhe do quesito + evidências", "GET", f"/resultados/{rs[0]['id']}")
            chamar("timeline do quesito", "GET", f"/entes/{slug}/quesitos/{rs[0]['codigo']}/timeline")
        if len((chamar("histórico (para o diff)", "GET", f"/entes/{slug}/historico") or [])) > 1:
            chamar("diff entre rodadas", "GET", f"/entes/{slug}/diff")
    fila = chamar("fila de revisão", "GET", "/revisoes/fila", params={"limite": 5}) or []
    chamar("busca", "GET", "/busca", params={"q": "despesas com pessoal", "k": 3})
    chamar("rodadas de coleta", "GET", "/rodadas", params={"limite": 3})
    chamar("checklist", "GET", "/checklist")
    chamar("ente inexistente → 404", "GET", "/entes/nao-existe", esperado=404)
    chamar("revisão sem chave é recusada", "POST", "/revisoes", esperado=(401, 503), sem_chave=True,
           json={"resultado_id": 0, "status_corrigido": "atende", "revisor": "teste"})
    if a.revisao:
        alvo = (fila[0]["id"] if fila else ctx.get("resultado"))
        if alvo:
            chamar("grava revisão de teste", "POST", "/revisoes", esperado=201,
                   json={"resultado_id": alvo, "status_corrigido": "nao_verificavel", "revisor": "teste de fumaça",
                         "comentario": "gravado por scripts/testar_api.py — pode ignorar"})
    print(f"\n{'TUDO OK' if not falhas else f'{falhas} FALHA(S)'}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
