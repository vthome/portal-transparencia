"""Armazenamento de objetos endereçado por conteúdo (chave = sha256).

O mesmo PDF publicado em 10 rodadas ocupa espaço uma vez só; o banco guarda o caminho.
Dois backends com a mesma interface:
- LocalStorage: pasta em disco (desenvolvimento, testes, execução sem Supabase)
- SupabaseStorage: API REST do Supabase Storage (bucket privado)
"""
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Protocol

import requests

log = logging.getLogger(__name__)

EXT_POR_TIPO = {"text/html": "html", "application/pdf": "pdf", "text/csv": "csv", "image/png": "png",
                "application/json": "json", "application/zip": "zip"}


def object_key(content: bytes, ext: str | None) -> str:
    """html/ab/<sha>.html · png/ab/<sha>.png · files/ab/<sha>.pdf (layout do bucket `snapshots`)."""
    sha = hashlib.sha256(content).hexdigest()
    ext = ext or "bin"
    pasta = ext if ext in {"html", "png"} else "files"
    return f"{pasta}/{sha[:2]}/{sha}.{ext}"


class Storage(Protocol):
    def put(self, content: bytes, ext: str | None, content_type: str | None = None) -> str: ...
    def get(self, key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads = 0  # quantos objetos foram de fato gravados (métrica de dedupe)

    def put(self, content: bytes, ext: str | None, content_type: str | None = None) -> str:
        key = object_key(content, ext)
        path = self.root / key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(content)
            tmp.replace(path)          # escrita atômica
            self.uploads += 1
        return key

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


class SupabaseStorage:
    """Usa a chave secreta (somente no servidor/CI — nunca no dashboard).

    - chave nova `sb_secret_...`: vai só no header `apikey` (não é JWT; o Supabase pede
      para não enviá-la em `Authorization: Bearer`)
    - chave legada `service_role` (JWT `eyJ...`): `apikey` + `Authorization: Bearer`
    """

    def __init__(self, url: str, secret_key: str, bucket: str, session: requests.Session | None = None,
                 timeout: float = 60) -> None:
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.http = session or requests.Session()
        self.http.headers["apikey"] = secret_key
        if not secret_key.startswith("sb_"):
            self.http.headers["Authorization"] = f"Bearer {secret_key}"
        self.timeout = timeout
        self._conhecidas: set[str] = set()
        self._lock = threading.Lock()
        self.uploads = 0

    def ensure_bucket(self) -> None:
        r = self.http.post(f"{self.base}/bucket", json={"id": self.bucket, "name": self.bucket, "public": False},
                           timeout=self.timeout)
        if r.status_code not in (200, 201) and not _duplicado(r):
            # o bucket normalmente já vem do sql/schema.sql; só avisamos — o upload dirá se há problema real
            log.warning("não foi possível confirmar o bucket %s: %s %s", self.bucket, r.status_code, r.text[:200])

    def put(self, content: bytes, ext: str | None, content_type: str | None = None) -> str:
        key = object_key(content, ext)
        with self._lock:
            if key in self._conhecidas:
                return key
        r = self.http.post(f"{self.base}/object/{self.bucket}/{key}", data=content, timeout=self.timeout,
                           headers={"Content-Type": content_type or "application/octet-stream",
                                    "x-upsert": "false", "cache-control": "max-age=31536000"})
        if r.status_code in (200, 201):
            self.uploads += 1
        elif not _duplicado(r):
            raise RuntimeError(f"upload falhou ({r.status_code}): {r.text[:200]}")
        with self._lock:
            self._conhecidas.add(key)
        return key

    def get(self, key: str) -> bytes:
        r = self.http.get(f"{self.base}/object/{self.bucket}/{key}", timeout=self.timeout)
        r.raise_for_status()
        return r.content


def _duplicado(r: requests.Response) -> bool:
    """Supabase responde 409, ou 400 com 'Duplicate'/'already exists', quando o objeto já existe."""
    return r.status_code == 409 or (r.status_code == 400 and ("uplicate" in r.text or "already exists" in r.text))
