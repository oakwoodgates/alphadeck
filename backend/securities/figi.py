from __future__ import annotations

import json
from pathlib import Path

# Runtime cache lives under the repo's gitignored data/; tests pass a fixtures dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "figi_cache"
_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"


class CacheMiss(Exception):
    """A mapping isn't cached and live pulls are disabled (the etiquette guard: tests never hit the net)."""


def map_ticker(
    ticker: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
) -> dict[str, str | None]:
    """Resolve a ticker to ``{ticker, figi, name}`` via OpenFIGI. Cache-first; live only behind ``allow_live``."""
    ticker = ticker.upper()
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / f"{ticker}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if not allow_live:
        raise CacheMiss(f"no cached OpenFIGI mapping for {ticker!r} (live pulls disabled)")
    mapping = _fetch_live(ticker)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


def _fetch_live(ticker: str) -> dict[str, str | None]:
    import os

    import httpx

    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("OPENFIGI_API_KEY")
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    body = [{"idType": "TICKER", "idValue": ticker, "exchCode": "US"}]
    resp = httpx.post(_OPENFIGI_URL, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    matches = (resp.json()[0] or {}).get("data") or []
    if not matches:
        raise CacheMiss(f"OpenFIGI returned no match for {ticker!r}")
    top = matches[0]
    return {
        "ticker": ticker,
        "figi": top.get("compositeFIGI") or top.get("figi"),
        "name": top.get("name"),
    }
