"""Carrega e valida criteria.yaml e scoring.yaml."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from compliance.checkers import REGISTRO

CFG_DIR = Path(__file__).resolve().parent / "config"


class ChecklistInvalido(ValueError):
    pass


@dataclass
class Criterio:
    codigo: str
    bloco: str
    dimensao: str
    matriz: str
    texto: str
    fundamentacao: str
    peso: int
    essencial: bool
    aplica_a: list[str]
    onda: int | None
    verificador: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | None = None                         # monitor.criterion.id, preenchido ao gravar

    @property
    def params_hash(self) -> str:
        return hashlib.sha256(json.dumps({"v": self.verificador, "p": self.params}, sort_keys=True,
                                         ensure_ascii=False).encode()).hexdigest()

    def aplica(self, tags: set[str]) -> bool:
        return bool(set(self.aplica_a) & tags)


@dataclass
class Checklist:
    nome: str
    sha256: str
    criterios: list[Criterio]

    def por_codigo(self) -> dict[str, Criterio]:
        return {c.codigo: c for c in self.criterios}


def carregar_scoring(caminho: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((caminho or CFG_DIR / "scoring.yaml").read_text(encoding="utf-8"))


def carregar_checklist(caminho: Path | None = None, scoring: dict[str, Any] | None = None) -> Checklist:
    caminho = caminho or CFG_DIR / "criteria.yaml"
    bruto = caminho.read_bytes()
    dados = yaml.safe_load(bruto.decode("utf-8"))
    scoring = scoring or carregar_scoring()
    crits = [Criterio(**{k: v for k, v in c.items() if k in Criterio.__dataclass_fields__}) for c in dados["criterios"]]
    erros = []
    vistos = set()
    for c in crits:
        if c.codigo in vistos:
            erros.append(f"{c.codigo}: duplicado")
        vistos.add(c.codigo)
        if c.verificador not in REGISTRO:
            erros.append(f"{c.codigo}: verificador desconhecido '{c.verificador}'")
        if c.peso not in (1, 2, 3):
            erros.append(f"{c.codigo}: peso {c.peso} fora de 1..3")
        if c.dimensao not in scoring["dimensoes"]:
            erros.append(f"{c.codigo}: dimensão '{c.dimensao}' não existe em scoring.yaml")
        if c.matriz not in ("comum", "executivo"):
            erros.append(f"{c.codigo}: matriz '{c.matriz}'")
    if erros:
        raise ChecklistInvalido("criteria.yaml inválido:\n  " + "\n  ".join(erros))
    sha = hashlib.sha256(bruto + (CFG_DIR / "scoring.yaml").read_bytes()).hexdigest()
    return Checklist(nome=f"checklistPortalTransp {sha[:10]}", sha256=sha, criterios=crits)
