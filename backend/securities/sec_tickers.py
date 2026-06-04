from __future__ import annotations

import json
from pathlib import Path

from securities.figi import CacheMiss  # reuse the same cache-miss signal

# Runtime cache lives under the repo's gitignored data/; tests pass a fixtures dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "sec_cache"
_SEC_URL = "https://www.sec.gov/files/company_tickers.json"


def cik_for(
    ticker: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    user_agent: str | None = None,
) -> str | None:
    """Resolve a ticker to a zero-padded 10-digit CIK from SEC's company_tickers.json. Cache-first.

    Returns ``None`` if the ticker isn't in the (cached) table; raises ``CacheMiss`` if the table
    itself isn't cached and live pulls are disabled.
    """
    ticker = ticker.upper()
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / "company_tickers.json"
    if path.exists():
        table = json.loads(path.read_text(encoding="utf-8"))
    elif allow_live:
        table = _fetch_live(user_agent)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(table), encoding="utf-8")
    else:
        raise CacheMiss("no cached SEC company_tickers.json (live pulls disabled)")
    for row in table.values():
        if str(row.get("ticker", "")).upper() == ticker:
            return f"{int(row['cik_str']):010d}"
    return None


def _fetch_live(user_agent: str | None) -> dict:
    import os

    import httpx

    ua = user_agent or os.environ.get("ALPHADECK_USER_AGENT")
    if not ua:
        raise RuntimeError(
            "set ALPHADECK_USER_AGENT (SEC requires a declared User-Agent with contact)"
        )
    resp = httpx.get(_SEC_URL, headers={"User-Agent": ua}, timeout=30)
    resp.raise_for_status()
    return resp.json()
