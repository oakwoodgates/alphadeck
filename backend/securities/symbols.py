"""The one shape a ticker may take before it reaches the shared security master (security hardening PR 1a).

A ticker typed into a request becomes a master row, an OpenFIGI request and a cache FILENAME
(``securities/figi.map_ticker``), so it is held to a strict pattern first: an uppercase letter or digit, then up to
eleven more letters, digits, dots or hyphens (``BRK-B``, ``BF.B``). No path separator, no space, no NUL, nothing
longer than twelve characters. The request models (``ResolveEtfRequest`` / ``FundAttachRequest``) and
``map_ticker`` share this one check, so no caller can bypass it.
"""

from __future__ import annotations

import re

TICKER_PATTERN = r"[A-Z0-9][A-Z0-9.\-]{0,11}"
_TICKER = re.compile(TICKER_PATTERN)


def normalize_ticker(raw: str) -> str:
    """Strip + uppercase ``raw`` and return it, or raise ``ValueError`` when it is not a ticker's shape."""
    ticker = raw.strip().upper()
    if _TICKER.fullmatch(ticker) is None:
        raise ValueError(
            "a ticker is 1 to 12 letters, digits, dots or hyphens, starting with a letter or digit"
        )
    return ticker


__all__ = ["TICKER_PATTERN", "normalize_ticker"]
