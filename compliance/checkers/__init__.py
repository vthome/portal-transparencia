"""Registro dos verificadores. Importar este pacote registra todos em `REGISTRO`."""
from compliance.checkers import (  # noqa: F401
    acessibilidade,
    documentos,
    estruturados,
    existencia,
    semantico,
)
from compliance.checkers.base import (  # noqa: F401
    REGISTRO,
    VERSOES,
    Contexto,
    Evidencia,
    Resultado,
)
