"""Fund-ticker resolution — the MUTUAL-FUND/ETF sibling of ``sec_tickers``.

An ETF ticker (LIT / URA / ARKK) is not an operating company: it names a fund-trust SERIES, so it has no
row in ``company_tickers_exchange.json`` and no operating CIK. The SEC's ``company_tickers_mf.json`` is
the fund table: ``{cik, seriesId, classId, symbol}`` rows mapping a fund symbol to its TRUST CIK + the
series/class ids. The seriesId is the load-bearing half — N-PORT holdings file PER SERIES (LIT and URA
share trust CIK 1432353; only the seriesId tells them apart).

Deliberately ON-THE-FLY (ETF Sleeve, Slice 2a — a locked decision): the resolved trust CIK is NEVER
written into the master's ``cik`` column — extraction/companyfacts key on ``cik`` and a trust CIK there
would mislead them (a sleeve row stays ``cik=None``). Resolution happens at the operator's holdings
click, nothing is back-filled.

Mirrors ``sec_tickers``'s cache discipline exactly: the RAW payload cached once on disk (one file, one
GET, cache-first forever); tests pass a fixtures dir; ``CacheMiss`` when uncached with live pulls off.
Shared property with ``sec_tickers``: a fund launched AFTER the cache was written won't resolve until
the cache file is refreshed — fine for a discovery seed (delete ``data/sec_cache/company_tickers_mf.json``
to re-pull).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from domain.settings import get_settings
from ingest import CacheMiss

# Runtime cache lives under the repo's gitignored data/ (the same dir as sec_tickers' table); tests pass
# a fixtures dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "sec_cache"


@dataclass(frozen=True)
class FundIdentity:
    """A fund symbol resolved to its SEC identity: the TRUST's CIK (zero-padded 10-digit, the archives
    path key) + the SERIES id (the N-PORT filter key) + the share-CLASS id (carried for completeness).
    """

    trust_cik: str
    series_id: str
    class_id: str


def resolve(
    ticker: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    user_agent: str | None = None,
) -> FundIdentity | None:
    """Resolve a fund ticker to its ``FundIdentity`` from the SEC MF table. Cache-first.

    Returns ``None`` if the symbol isn't in the (cached) table — the honest "not an SEC-registered
    fund" answer, never a guess (INVARIANT #2: exact mappings only). Raises ``CacheMiss`` if the table
    itself isn't cached and live pulls are disabled.
    """
    ticker = ticker.upper()
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = cache_dir / "company_tickers_mf.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    elif allow_live:
        raw = _fetch_live(user_agent)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw), encoding="utf-8")
    else:
        raise CacheMiss("no cached SEC company_tickers_mf.json (live pulls disabled)")
    idx = {f: i for i, f in enumerate(raw["fields"])}
    for r in raw["data"]:
        cik, series_id, symbol = r[idx["cik"]], r[idx["seriesId"]], r[idx["symbol"]]
        if cik is None or not series_id or not symbol:
            continue  # exact mappings only — a row missing its keys can't resolve anything
        if str(symbol).upper() == ticker:
            return FundIdentity(
                trust_cik=f"{int(cik):010d}",
                series_id=str(series_id),
                class_id=str(r[idx["classId"]] or ""),
            )
    return None


def _fetch_live(user_agent: str | None) -> dict:
    import httpx

    s = get_settings()
    ua = user_agent or s.user_agent
    if not ua:
        raise RuntimeError(
            "set ALPHADECK_USER_AGENT (SEC requires a declared User-Agent with contact)"
        )
    resp = httpx.get(
        s.sec_company_tickers_mf_url, headers={"User-Agent": ua}, timeout=s.http_timeout_s
    )
    resp.raise_for_status()
    return resp.json()
