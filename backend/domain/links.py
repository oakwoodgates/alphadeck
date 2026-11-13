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
