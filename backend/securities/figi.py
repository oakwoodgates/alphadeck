from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from domain.settings import get_settings
from ingest import CacheMiss

# Runtime cache lives under the repo's gitignored data/; tests pass a fixtures dir instead.
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "figi_cache"

# OpenFIGI /mapping batching (documented limits): jobs per POST and request rate differ by key
# presence. The per-call RateLimiter spaces the chunks of ONE map_cusip call (the operator-click case);
# across clicks the per-CUSIP cache absorbs the repeats.
_CUSIP_BATCH_WITH_KEY = 100
_CUSIP_BATCH_NO_KEY = 10
_CUSIP_RATE_WITH_KEY = 4.0  # ~25 requests / 6s
_CUSIP_RATE_NO_KEY = 0.4  # 25 requests / minute


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


def map_cusip(
    cusips: Iterable[str],
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
) -> dict[str, str]:
    """Batch-resolve CUSIPs to their US ticker via OpenFIGI (``idType: ID_CUSIP``) — the ETF sleeve's
    overlap upgrade: most N-PORT filers stamp NO ticker on equity holdings (measured: SMH/URA/LIT all
    0), but a CUSIP rides essentially every US-line holding, so CUSIP→ticker is what lets the overlap
    resolve at all. A deterministic identifier map, the same class as ``map_ticker`` (#3-safe — an ID
    crosswalk, never a guess).

    Returns ``{cusip: ticker}`` for the MAPPED subset only — a CUSIP OpenFIGI can't place on a US line
    (foreign locals, cash/repo lines) is simply OMITTED; the caller keeps its holding visible as
    ``unresolved`` (#9: unmapped is a shown state, never an error and never a drop).

    Cache-first PER CUSIP (``cusip-<id>.json`` beside the ticker cache — the prefix keeps the two key
    spaces from colliding), and the NO-MATCH answer is cached too (``ticker: null``): a CUSIP→ticker
    mapping is stable, so both answers are paid for once and a re-click re-asks nothing. Uncached
    residue with ``allow_live=False`` raises ``CacheMiss`` (the test-transport guard, as everywhere).
    Live misses are batched into a few ``/mapping`` POSTs (jobs-per-request and request rate per
    OpenFIGI's documented key/no-key limits, spaced by a RateLimiter) — a ~26-holding ETF is one to
    three calls, then cached.
    """
    cache_dir = cache_dir or _DEFAULT_CACHE
    wanted: list[str] = []
    seen: set[str] = set()
    for c in cusips:
        cu = (c or "").strip().upper()
        if cu and cu not in seen:
            seen.add(cu)
            wanted.append(cu)
    result: dict[str, str] = {}
    misses: list[str] = []
    for cu in wanted:
        path = cache_dir / f"cusip-{cu}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("ticker"):
                result[cu] = cached["ticker"]
        else:
            misses.append(cu)
    if misses:
        if not allow_live:
            raise CacheMiss(
                f"no cached OpenFIGI CUSIP mapping for {len(misses)} ids "
                f"(first: {misses[0]!r}; live pulls disabled)"
            )
        fetched = _fetch_cusips_live(misses)
        cache_dir.mkdir(parents=True, exist_ok=True)
        for cu, ticker in fetched.items():
            (cache_dir / f"cusip-{cu}.json").write_text(
                json.dumps({"cusip": cu, "ticker": ticker}), encoding="utf-8"
            )
            if ticker:
                result[cu] = ticker
    return result


def _fetch_live(ticker: str) -> dict[str, str | None]:
    import httpx

    s = get_settings()
    headers = {"Content-Type": "application/json"}
    api_key = s.openfigi_api_key
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    body = [{"idType": "TICKER", "idValue": ticker, "exchCode": "US"}]
    resp = httpx.post(s.openfigi_url, json=body, headers=headers, timeout=s.http_timeout_s)
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


def _fetch_cusips_live(cusips: list[str]) -> dict[str, str | None]:
    """POST the uncached CUSIPs to OpenFIGI /mapping in key-sized chunks; ``None`` = no US-line match
    (cached as the negative answer). The response array is position-parallel to the jobs array."""
    import httpx

    from ingest.http import RateLimiter

    s = get_settings()
    headers = {"Content-Type": "application/json"}
    api_key = s.openfigi_api_key
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    batch = _CUSIP_BATCH_WITH_KEY if api_key else _CUSIP_BATCH_NO_KEY
    rate = RateLimiter(_CUSIP_RATE_WITH_KEY if api_key else _CUSIP_RATE_NO_KEY)
    out: dict[str, str | None] = {}
    for i in range(0, len(cusips), batch):
        chunk = cusips[i : i + batch]
        rate.acquire()
        body = [{"idType": "ID_CUSIP", "idValue": cu, "exchCode": "US"} for cu in chunk]
        resp = httpx.post(s.openfigi_url, json=body, headers=headers, timeout=s.http_timeout_s)
        resp.raise_for_status()
        for cu, item in zip(chunk, resp.json()):
            matches = (item or {}).get("data") or []
            ticker = (matches[0].get("ticker") or None) if matches else None
            out[cu] = ticker
    return out
