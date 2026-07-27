"""Fund shares-outstanding pages — cache-first fetchers + PURE parsers (ETF net flow, F1).

Two sources, both validated live 2026-07-26 (URA/SMH/LIT — the parse anchors below quote the real pages):

- **Global X (the issuer)** — ``globalxetfs.com/funds/{ticker}`` embeds a Next.js flight payload whose
  ``ETF_DETAILS`` blob carries the EXACT count *and its own stated as-of date*::

      \\"ETF_DETAILS\\":{\\"ASSETS\\":…,\\"AS_OF_DATE\\":\\"$D2026-07-24T00:00:00.000Z\\",…,
      \\"SHARES_OUTSTANDING\\":138771666,…}

- **stockanalysis.com (the aggregator fallback)** — ``stockanalysis.com/etf/{ticker}/`` embeds a JS
  object literal with a suffixed, ~10k-share-ROUNDED count and the quote's ISO trading date::

      quote:{…,td:"2026-07-24",…}, …, sharesOut:"138.77M"

  The rounding is acceptable for a flow read; the ``source`` column records the adapter so the
  operator knows the resolution. It covers funds whose issuer page is bot-gated (SMH: VanEck).

``d`` is ALWAYS the page's OWN stated date (the issuer's ``AS_OF_DATE`` / the aggregator's trading
date ``td``) — never an assumed "today" (#1: valid_from = event time; a weekend sample re-states
Friday's count under Friday's date, which the same-(d, value) ingest skip makes a no-op). A page
missing its date field parses to ``None`` — no date, no sample, honestly.

Freshness is the PRICES mechanism (a homogeneous daily cache, NOT EDGAR's key-classed TTL): cache-first;
the recurring/daily path passes ``force_refresh=True`` to re-pull + overwrite; dev/``--no-live`` stays
cache-first (``CacheMiss`` on a cold cache); a cache MISS always fetches. See ``eod_loader.fetch_eod``.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from domain.settings import get_settings
from ingest import CacheMiss
from ingest.http import polite_get

_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "fund_cache"

# The same generic UA the Yahoo price leg sends (eod_loader) — both pages 200 with it, measured live.
_UA = "Mozilla/5.0 (Alpha Deck research)"

# --- Global X (issuer) — the ETF_DETAILS blob, keys escaped (\") inside the flight payload ----------
# `\\?` tolerates both the escaped (\"KEY\") and a plain-JSON ("KEY") rendering of the same field.
_GX_SHARES = re.compile(r'SHARES_OUTSTANDING\\?"\s*:\s*(\d+)')
_GX_ASOF = re.compile(r'AS_OF_DATE\\?"\s*:\s*\\?"\$D(\d{4}-\d{2}-\d{2})')
_GX_WINDOW = (
    4000  # ETF_DETAILS holds ~15 keys; AS_OF_DATE + SHARES_OUTSTANDING sit well inside this
)

# --- stockanalysis.com (aggregator) — a JS object literal (keys UNQUOTED, values quoted) ------------
_SA_SHARES = re.compile(r'sharesOut\s*:\s*"([\d.,]+)\s*([KMBT]?)"')
# the quote object's ISO trading date; `[,{]` anchors the key so `etd:` (extended-hours) can't match
_SA_TRADE_DATE = re.compile(r'quote\s*:\s*\{[^{}]*?[,{]td\s*:\s*"(\d{4}-\d{2}-\d{2})"')
_SA_SUFFIX = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def globalx_fund_url(ticker: str) -> str:
    # NO trailing slash: /funds/{t}/ 308-redirects to /funds/{t}, and polite_get (deliberately) does
    # not follow redirects — the canonical shape 200s directly (measured live 2026-07-26).
    return f"{get_settings().globalx_funds_base}/funds/{ticker.lower()}"


def stockanalysis_etf_url(ticker: str) -> str:
    # lowercase WITH the trailing slash: /etf/URA/ 301s to /etf/ura/ (same no-redirect rule as above).
    return f"{get_settings().stockanalysis_base}/etf/{ticker.lower()}/"


def parse_globalx(html: str) -> dict | None:
    """Parse the issuer page's ``ETF_DETAILS`` blob → ``{d, shares_out}``, or ``None`` when the page
    carries no such blob (not a Global X fund page / a redesign) — a miss, never a guess. Both fields
    are required: a count without the blob's own stated date is not a sample (#1)."""
    i = html.find("ETF_DETAILS")
    if i < 0:
        return None
    window = html[i : i + _GX_WINDOW]
    shares = _GX_SHARES.search(window)
    asof = _GX_ASOF.search(window)
    if shares is None or asof is None:
        return None
    return {"d": date.fromisoformat(asof.group(1)), "shares_out": float(shares.group(1))}


def parse_stockanalysis(html: str) -> dict | None:
    """Parse the aggregator page's stats blob → ``{d, shares_out}``, or ``None`` when either field is
    absent. ``sharesOut`` is suffixed ("138.77M" → 138,770,000 — ~10k-share rounded; the caller's
    ``source`` column records that resolution); ``d`` is the quote's ISO trading date ``td``."""
    shares = _SA_SHARES.search(html)
    traded = _SA_TRADE_DATE.search(html)
    if shares is None or traded is None:
        return None
    count = float(shares.group(1).replace(",", "")) * _SA_SUFFIX[shares.group(2)]
    return {"d": date.fromisoformat(traded.group(1)), "shares_out": float(round(count))}


def _fetch_page(
    url: str,
    cache_path: Path,
    *,
    allow_live: bool,
    force_refresh: bool,
) -> str:
    """Cache-first page HTML — the ``fetch_eod`` freshness contract exactly: a cache hit is served
    unless ``force_refresh`` (live only) bypasses it; no network without ``allow_live`` (``CacheMiss``
    on a cold cache); a miss always fetches; a live fetch overwrites the cache."""
    if cache_path.exists() and not (force_refresh and allow_live):
        return cache_path.read_text(encoding="utf-8")
    if not allow_live:
        raise CacheMiss(f"no cached fund page for {url!r} (live pulls disabled)")
    resp = polite_get(  # 429/5xx backoff (D6 politeness)
        url, timeout=get_settings().http_timeout_s, headers={"User-Agent": _UA}
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(resp.text, encoding="utf-8")
    return resp.text


def fetch_globalx(
    ticker: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    force_refresh: bool = False,
) -> str:
    """Cache-first Global X fund-page HTML for ``ticker``."""
    cache_dir = cache_dir or _DEFAULT_CACHE
    return _fetch_page(
        globalx_fund_url(ticker),
        cache_dir / f"{ticker.upper()}.globalx.html",
        allow_live=allow_live,
        force_refresh=force_refresh,
    )


def fetch_stockanalysis(
    ticker: str,
    *,
    cache_dir: Path | None = None,
    allow_live: bool = False,
    force_refresh: bool = False,
) -> str:
    """Cache-first stockanalysis.com ETF-page HTML for ``ticker``."""
    cache_dir = cache_dir or _DEFAULT_CACHE
    return _fetch_page(
        stockanalysis_etf_url(ticker),
        cache_dir / f"{ticker.upper()}.stockanalysis.html",
        allow_live=allow_live,
        force_refresh=force_refresh,
    )
