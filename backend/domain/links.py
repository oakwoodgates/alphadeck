"""The ONE rule for a URL on the wire (security hardening PR 4a).

A response that carries a link carries it only when it is a plain ``http``/``https`` URL: it parses, it names a
host, it carries no ``user:password@``, it holds no whitespace or control character, and it is at most
``MAX_URL_LENGTH`` characters. Anything else is ``None`` — no link — never the raw value and never an error.

``HttpUrlOut`` applies the rule on a RESPONSE model's field. It never rejects and never rewrites stored data:
the value in the database is left as it is, and only what goes out on the wire is checked. A request model or
a write-path domain model keeps its plain ``str``.

The frontend's ``components/ExternalLink.tsx`` applies the same rule before it renders an anchor.
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import BeforeValidator

MAX_URL_LENGTH = 2000

# whitespace of any kind (including the Unicode separators) and every C0/C1 control character
_FORBIDDEN = re.compile(r"[\s\x00-\x1f\x7f-\x9f]")


def safe_http_url(value: Any) -> str | None:
    """``value`` when it is a well-formed http(s) URL by the rule above, else ``None``."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        return None
    if _FORBIDDEN.search(value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    return value


# The validator instance itself: the response-model guard test looks for it on every url-named field.
HTTP_URL_RULE = BeforeValidator(safe_http_url)

HttpUrlOut = Annotated[str | None, HTTP_URL_RULE]
"""A response field that carries a URL: ``None`` on the wire unless ``safe_http_url`` accepts it."""


# --- a stored source reference as a link (one resolver for every surface) ---

_ACCESSION = re.compile(r"\d{18}")
EDGAR_COMPANY_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="


def edgar_company_url(cik: str | None) -> str | None:
    """The EDGAR browse-company page for ``cik``, or ``None`` when CIK is missing or malformed."""
    if not cik:
        return None
    digits = re.sub(r"\D", "", str(cik))
    return f"{EDGAR_COMPANY_URL}{digits}" if digits else None


def filing_index_url(cik: str | None, accession: str | None) -> str | None:
    """The EDGAR filing-index URL ``https://www.sec.gov/Archives/edgar/data/<cik as int>/<accession without
    dashes>/``, or ``None`` when the CIK or the accession is missing or malformed (never a guessed link).
    """
    if not cik or not accession:
        return None
    digits = re.sub(r"\D", "", str(cik))
    acc = str(accession).strip().replace("-", "")
    if not digits or not _ACCESSION.fullmatch(acc):
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(digits)}/{acc}/"


def source_url(source_ref: Any, cik: str | None) -> str | None:
    """A stored ``source_ref`` as a link: an http(s) URL that passes the one wire rule
    (``domain.links.safe_http_url``: a host, no credentials, no whitespace, at most 2,000 characters) as is, an
    accession-shaped reference through the EDGAR index, anything else (an award id, a free-text reference, a
    URL that fails the rule) no link."""
    if not isinstance(source_ref, str):
        return None
    ref = source_ref.strip()
    return safe_http_url(ref) or filing_index_url(cik, ref)


def _accession_of(source_ref: Any) -> str | None:
    """The accession a ``source_ref`` names when it is accession-shaped, else ``None``."""
    if not isinstance(source_ref, str):
        return None
    ref = source_ref.strip()
    return ref if _ACCESSION.fullmatch(ref.replace("-", "")) else None


# the public name for a page that classifies a reference (the Evidence Ledger's "traces to an EDGAR accession")
accession_of = _accession_of
