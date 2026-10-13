"""Verifica o repositório inteiro antes do push: arquivos completos e atualizados, sintaxe e testes.

    python scripts/verificar.py                 # tudo
    python scripts/verificar.py --sem-testes    # só arquivos, sintaxe e lint (rápido)

1. Manifesto: compara cada arquivo com scripts/manifesto.json (gerado junto com o pacote;
   o municipios.yaml fica de fora porque é configuração sua).
   Aponta arquivos FALTANDO ou DESATUALIZADOS (versão antiga copiada por engano) — a causa
   mais comum de "ImportError: cannot import name ..." no CI.
2. Sintaxe Python de todos os .py.
3. YAML, TOML e JSON legíveis (configs, workflows, checklist).
4. Lint (ruff), se instalado.
5. Testes (pytest).

Quebras de linha CRLF (Windows/git autocrlf) são normalizadas antes da comparação.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MANIFESTO = RAIZ / "scripts" / "manifesto.json"
IGNORAR = {".git", ".venv", "mprj-venv", "data", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
OK, ERRO, AVISO = "[OK]  ", "[ERRO]", "[AVISO]"


def _arquivos(sufixos: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in RAIZ.rglob("*") if p.is_file() and p.suffix in sufixos
                  and not (set(p.relative_to(RAIZ).parts) & IGNORAR))


def hash_normalizado(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def gerar_manifesto(caminhos: list[str]) -> dict[str, str]:
    return {c: hash_normalizado(RAIZ / c) for c in sorted(caminhos)}


def conferir_manifesto() -> int:
    if not MANIFESTO.exists():
        print(f"{AVISO} scripts/manifesto.json não encontrado — pulando a conferência de versões")
        return 0
    esperado = json.loads(MANIFESTO.read_text(encoding="utf-8"))
    faltando = [c for c in esperado if not (RAIZ / c).exists()]
    diferentes = [c for c in esperado if (RAIZ / c).exists() and hash_normalizado(RAIZ / c) != esperado[c]]
    for c in faltando:
        print(f"{ERRO} FALTANDO        {c}")
    for c in diferentes:
        print(f"{ERRO} DESATUALIZADO   {c}  (difere da versão do pacote)")
    if not faltando and not diferentes:
        print(f"{OK} {len(esperado)} arquivos presentes e iguais aos do pacote")
    else:
        print("       → copie esses arquivos do zip mais recente por cima dos seus "
              "(ou, se você os alterou de propósito, ignore o aviso).")
    return len(faltando) + len(diferentes)


def conferir_python() -> int:
    erros = 0
    arquivos = _arquivos((".py",))
    for p in arquivos:
        try:
            compile(p.read_text(encoding="utf-8"), str(p), "exec")
        except (SyntaxError, UnicodeDecodeError) as e:
            erros += 1
            print(f"{ERRO} sintaxe  {p.relative_to(RAIZ)}: {e}")
    if not erros:
        print(f"{OK} sintaxe de {len(arquivos)} arquivos .py")
    return erros


def conferir_configs() -> int:
    import tomllib

    try:
        import yaml
    except ImportError:
        yaml = None
        print(f"{AVISO} pyyaml não instalado — YAML não conferido")
    erros, n = 0, 0
    for p in _arquivos((".yaml", ".yml", ".toml", ".json")):
        n += 1
        try:
            texto = p.read_text(encoding="utf-8")
            if p.suffix == ".toml":
                tomllib.loads(texto)
            elif p.suffix == ".json":
                json.loads(texto)
            elif yaml is not None:
                yaml.safe_load(texto)
        except Exception as e:  # noqa: BLE001
            erros += 1
            print(f"{ERRO} formato  {p.relative_to(RAIZ)}: {str(e).splitlines()[0][:160]}")
    if not erros:
        print(f"{OK} {n} arquivos YAML/TOML/JSON legíveis")
    return erros


def rodar(nome: str, cmd: list[str]) -> int:
    print(f"\n== {nome}: {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, cwd=RAIZ)
    except FileNotFoundError:
        print(f"{AVISO} {cmd[0]} não encontrado — pulando")
        return 0
    print(f"{OK if r.returncode == 0 else ERRO} {nome}")
    return 0 if r.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sem-testes", action="store_true")
    ap.add_argument("--sem-manifesto", action="store_true",
                    help="não compara com o pacote (no CI: lá o repositório é a referência)")
    ap.add_argument("--gerar-manifesto", nargs="*", help=argparse.SUPPRESS)  # uso interno, ao empacotar
    a = ap.parse_args(argv)
    if a.gerar_manifesto is not None:
        MANIFESTO.write_text(json.dumps(gerar_manifesto(a.gerar_manifesto), indent=1), encoding="utf-8")
        print(f"manifesto com {len(a.gerar_manifesto)} arquivos")
        return 0

    falhas = 0
    if not a.sem_manifesto:
        print(f"Repositório: {RAIZ}\n\n== Arquivos do pacote")
        falhas = conferir_manifesto()
    print("\n== Sintaxe e formatos")
    falhas += conferir_python() + conferir_configs()
    import importlib.util
    import shutil

    ruff = [sys.executable, "-m", "ruff"] if importlib.util.find_spec("ruff") else \
        ([shutil.which("ruff")] if shutil.which("ruff") else None)
    if ruff:
        falhas += rodar("Lint", ruff + ["check", "ingestion", "compliance", "api", "dashboard", "scripts", "tests",
                                        "--select", "E,F,W,I", "--ignore", "E501,E741"])
    else:
        print(f"\n{AVISO} ruff não instalado — lint pulado (pip install ruff)")
    if not a.sem_testes:
        falhas += rodar("Testes", [sys.executable, "-m", "pytest", "-q"])
    print("\n" + ("TUDO OK — pode fazer o push." if not falhas else f"{falhas} problema(s) — corrija antes do push."))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
