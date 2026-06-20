from __future__ import annotations

import json
from pathlib import Path

from domain.settings import get_settings
from securities.figi import CacheMiss  # reuse the same cache-miss signal

# Runtime cache lives under the repo's gitignored data/; tests pass a fixtures dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "sec_cache"


def _load_table(
    cache_dir: Path | None, allow_live: bool, user_agent: str | None
) -> dict[str, dict]:
    """The SEC company_tickers.json table, cache-first. One file, one GET; raises ``CacheMiss`` if it
    isn't cached and live pulls are disabled. Shared by ``cik_for`` (one lookup) and ``load_all`` (bulk).
    """
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / "company_tickers.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if allow_live:
        table = _fetch_live(user_agent)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(table), encoding="utf-8")
        return table
    raise CacheMiss("no cached SEC company_tickers.json (live pulls disabled)")


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
    table = _load_table(cache_dir, allow_live, user_agent)
    for row in table.values():
        if str(row.get("ticker", "")).upper() == ticker:
            return f"{int(row['cik_str']):010d}"
    return None


def load_all(
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    user_agent: str | None = None,
) -> list[tuple[str, str, str | None]]:
    """The FULL SEC company_tickers universe as ``(cik, ticker, name)`` triples — the broadener's input.

    ``cik`` zero-padded to 10 digits (matching ``cik_for``), ``ticker`` upper-cased, ``name`` from the SEC
    ``title``. Same cache-first + ``ALPHADECK_USER_AGENT`` fetch as ``cik_for`` — ONE GET for the whole
    ~12k-row file, not a request per name. Rows missing a ticker or CIK are skipped (exact mappings only;
    never a fuzzy guess — INVARIANT #2). A CIK may appear under several tickers (dual-class), preserved here.
    """
    table = _load_table(cache_dir, allow_live, user_agent)
    out: list[tuple[str, str, str | None]] = []
    for row in table.values():
        ticker = str(row.get("ticker", "")).upper()
        cik_str = row.get("cik_str")
        if not ticker or cik_str is None:
            continue
        out.append((f"{int(cik_str):010d}", ticker, row.get("title")))
    return out


def _fetch_live(user_agent: str | None) -> dict:
    import httpx

    s = get_settings()
    ua = user_agent or s.user_agent
    if not ua:
        raise RuntimeError(
            "set ALPHADECK_USER_AGENT (SEC requires a declared User-Agent with contact)"
        )
    resp = httpx.get(
        s.sec_company_tickers_url, headers={"User-Agent": ua}, timeout=s.http_timeout_s
    )
    resp.raise_for_status()
    return resp.json()
