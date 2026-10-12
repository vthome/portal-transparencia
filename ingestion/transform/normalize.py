"""Normalização de texto, URLs, datas e valores — funções puras, sem I/O."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

# ---------------------------------------------------------------- texto

_NAO_ALNUM = re.compile(r"[^a-z0-9]+")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_text(s: str | None) -> str:
    """minúsculas, sem acento, só [a-z0-9] separados por espaço simples."""
    if not s:
        return ""
    return _NAO_ALNUM.sub(" ", strip_accents(s).lower()).strip()


@lru_cache(maxsize=4096)
def _term_regex(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(normalize_text(term)) + r"(?![a-z0-9])")


def find_terms(text: str, terms: list[str]) -> list[str]:
    """Termos presentes em `text` como palavra inteira (texto já normalizado ou não)."""
    norm = normalize_text(text)
    return [t for t in terms if t and _term_regex(t).search(norm)]


def decode_bytes(content: bytes, content_type: str | None = None) -> str:
    """Decodifica respeitando o charset declarado; portais antigos usam latin-1/cp1252."""
    charset = None
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=")[-1].split(";")[0].strip()
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([a-zA-Z0-9_-]+)', content[:2048], re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for enc in filter(None, [charset, "utf-8", "cp1252"]):
        try:
            return content.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- URLs

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|_ga$|sessionid$|jsessionid$|phpsessid$)", re.I)
_SPA_FRAGMENT = re.compile(r"^#!?/")


def normalize_url(url: str, base: str | None = None) -> str | None:
    """URL canônica para deduplicar a fila do crawler.

    - resolve relativa contra `base`; descarta mailto:, tel:, javascript:
    - host minúsculo, sem porta padrão, sem parâmetros de rastreio, query ordenada
    - fragmento é removido, EXCETO rota de SPA (`#/despesas`, `#!/x`), que é página distinta
    """
    if not url:
        return None
    url = url.strip()
    if re.match(r"^(mailto|tel|javascript|data|whatsapp|sms):", url, re.I):
        return None
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not _TRACKING.match(k)))
    frag = "#" + parts.fragment if parts.fragment else ""
    fragment = parts.fragment if _SPA_FRAGMENT.match(frag) else ""
    return urlunsplit((parts.scheme.lower(), netloc, path, query, fragment))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def registrable_domain(host: str) -> str:
    """Aproximação do domínio registrável, suficiente para .gov.br/.com.br/.rio etc.

    niteroi.rj.gov.br -> niteroi.rj.gov.br ; transparencia.niteroi.rj.gov.br -> niteroi.rj.gov.br
    japeri.geosiap.net.br -> geosiap.net.br ; prefeitura.rio -> prefeitura.rio
    """
    host = host.lower().removeprefix("www.")
    labels = host.split(".")
    if host.endswith(".gov.br") and len(labels) >= 4 and len(labels[-3]) == 2:  # <mun>.<uf>.gov.br
        return ".".join(labels[-4:])
    if host.endswith(".br") and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_gov_domain(url: str) -> bool:
    return host_of(url).endswith(".gov.br")


def extension_of(url: str) -> str | None:
    path = urlsplit(url).path.lower()
    m = re.search(r"\.([a-z0-9]{2,5})$", path)
    return m.group(1) if m else None


# ---------------------------------------------------------------- datas e valores

_MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
          "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
          "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8,
          "set": 9, "out": 10, "nov": 11, "dez": 12}

_DATA_NUM = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)")
_DATA_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DATA_EXT = re.compile(r"(?<!\d)(\d{1,2}) de ([a-z]+) de (\d{4})")


def parse_dates_br(text: str) -> list[date]:
    """Todas as datas reconhecíveis no texto (dd/mm/aaaa, aaaa-mm-dd, '5 de março de 2026')."""
    out: list[date] = []
    for d, m, y in _DATA_NUM.findall(text or ""):
        _append_date(out, int(y), int(m), int(d))
    for y, m, d in _DATA_ISO.findall(text or ""):
        _append_date(out, int(y), int(m), int(d))
    for d, mes, y in _DATA_EXT.findall(normalize_text(text)):
        if mes in _MESES:
            _append_date(out, int(y), _MESES[mes], int(d))
    return out


def _append_date(out: list[date], y: int, m: int, d: int) -> None:
    if 1990 <= y <= 2100:
        try:
            out.append(date(y, m, d))
        except ValueError:
            pass


_MOEDA = re.compile(r"-?\(?R?\$?\s*(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\)?")


def parse_money_br(s: str | None) -> float | None:
    """'R$ 1.234,56' -> 1234.56 ; '(1.000,00)' -> -1000.0 ; texto sem valor -> None"""
    if not s:
        return None
    m = _MOEDA.search(s.strip())
    if not m:
        return None
    val = float(m.group(1).replace(".", "") + "." + m.group(2))
    neg = s.strip().startswith("-") or (s.strip().startswith("(") and s.strip().endswith(")"))
    return -val if neg else val


def normalize_header(h: str | None) -> str:
    """Cabeçalho de tabela/CSV comparável: 'Nº do Empenho ' -> 'n do empenho'."""
    return normalize_text((h or "").replace("º", "").replace("ª", ""))
