"""Polygon.io fund shares-outstanding — the PRIMARY source when a key is present (ETF net flow).

``GET {polygon_base}/v3/reference/tickers/{TICKER}?date={YYYY-MM-DD}&apiKey={KEY}`` returns the fund's
reference row *as of the queried date*: ``results.share_class_shares_outstanding`` (an integer) and
``results.type`` (``'ETF'`` for funds). Measured live 2026-07-26: URA 138,771,666 @ date=2026-07-26 and
127,841,666 @ date=2026-01-15 — the count VARIES with ``date``, which is what buys the backfill real
history (the one thing the scraper pair cannot give).

- **``d`` = the QUERIED date.** The wire carries no effective-date field (``last_updated_utc`` is
  null), so the sample is stamped under the date asked for — the forward path asks for today, the
  backfill for each historical date. The ingest leg's same-``(d, shares_out)`` skip dedups an intra-day
  re-pull; unchanged-count days are honest Δ=0 flow.
- **Gaps are misses, never zeros.** An occasional date returns ``share_class_shares_outstanding: null``
  (URA 2026-06-15, measured) — that parses to ``None``: no sample, so the forward composite falls back
  to the scrapers and the backfill skips-and-counts the date (#9: nothing fabricated). A non-``ETF``
  ``type`` and an unknown ticker (404) are the same honest ``None``.
- **Politeness.** Free tier = 5 requests/min: a module-shared limiter spaces LIVE calls >=13s apart
  (cache hits never spend a slot), and a 429 waits ~61s — past the minute window — before the retry
  (``polite_get`` with a per-minute backoff). 401 (bad key) / 403 (plan) surface as a clear
  ``PolygonError``.
- **The key never leaks.** ``source_ref`` and the cache identity are the key-STRIPPED URL; every raised
  message is scrubbed (an httpx error message embeds the full request URL, apiKey included — the cause
  chain is severed deliberately so no traceback re-leaks it).

Freshness is the PRICES mechanism (cache-first under ``data/fund_cache/``; the recurring path passes
``force_refresh``) — and the cache file is keyed by (ticker, queried date), so a NEW day is
*structurally* a miss: the daily forward sample can never be served yesterday's response.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from domain.market_time import market_today
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.http import RateLimiter, polite_get

_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "fund_cache"

# Free tier = 5 requests/min. >=13s spacing keeps a long backfill safely under it; a 429 waits the
# minute window out (61s) before retrying. ONE module-shared limiter so the forward leg and the
# backfill pace against the same budget — live fetches only (a cache hit costs nothing).
_MIN_INTERVAL_S = 13.0
_RETRY_WAIT_S = 61.0
_LIMITER = RateLimiter(max_per_sec=1.0 / _MIN_INTERVAL_S)
_SLEEP = time.sleep  # the 429-wait sleeper (polite_get's `sleep=` seam), injectable for tests

_STATUS_HINTS = {
    401: " — bad POLYGON_API_KEY",
    403: " — the key's plan does not cover this endpoint",
}


class PolygonError(Exception):
    """A Polygon fetch failed for real (auth / plan / server / network) — message ALWAYS key-scrubbed.
    Distinct from a miss (``None``): a miss falls back / is skipped-and-counted; this surfaces
    fail-visible (the composite warns, the backfill captures it on the member)."""


def polygon_ticker_url(ticker: str, d: date) -> str:
    """The reference-ticker URL WITHOUT the apiKey — the ``source_ref``, the cache identity, and every
    error message use this form. The key is appended only at the socket (``_fetch``), never stored.
    """
    return (
        f"{get_settings().polygon_base}/v3/reference/tickers/{ticker.upper()}?date={d.isoformat()}"
    )


def parse_polygon(text: str) -> float | None:
    """The integer share count out of a reference-ticker response body, or ``None`` when the payload
    has no honest count: a null-count gap date, a non-``ETF`` ``type`` (the share-class read is only
    trusted for funds), or an absent/malformed ``results``. Never a zero, never a guess (#9)."""
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict):
        return None
    if results.get("type") != "ETF":
        return (
            None  # not a fund row — no sample here (the scraper chain may still cover the ticker)
        )
    count = results.get("share_class_shares_outstanding")
    if isinstance(count, bool) or not isinstance(count, (int, float)):
        return None  # the measured null-gap shape (URA 2026-06-15) — skip the date, never fake a 0
    return float(count)


class PolygonFundSource:
    """The keyed Polygon adapter — ``FundSharesSource`` for the forward daily sample, plus the dated
    ``get_snapshot_at`` the historical backfill walks. Constructed only when a key exists
    (``default_fund_source`` composes it in; a keyless deploy never builds one)."""

    name = "polygon"

    def __init__(self, *, api_key: str, cache_dir: Path | None = None) -> None:
        if not api_key:
            raise ValueError("PolygonFundSource requires a non-empty api_key")
        self._api_key = api_key
        self._cache_dir = cache_dir or _DEFAULT_CACHE

    def get_snapshot(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> dict | None:
        """The forward daily sample: query TODAY and stamp ``d`` = today (the queried date — Polygon
        states no effective date of its own)."""
        return self.get_snapshot_at(
            ticker, market_today(), allow_live=allow_live, force_refresh=force_refresh
        )

    def get_snapshot_at(
        self, ticker: str, d: date, *, allow_live: bool = False, force_refresh: bool = False
    ) -> dict | None:
        """One dated snapshot ``{d, shares_out, source, source_ref}`` — ``d`` the queried date — or
        ``None`` (unknown ticker / non-ETF type / a null-count gap date: no sample, honestly)."""
        clean_url = polygon_ticker_url(ticker, d)
        text = self._fetch(clean_url, ticker, d, allow_live=allow_live, force_refresh=force_refresh)
        if text is None:
            return None  # 404 — Polygon does not know the ticker (a clean miss)
        count = parse_polygon(text)
        if count is None:
            return None
        return {"d": d, "shares_out": count, "source": self.name, "source_ref": clean_url}

    def _scrub(self, s: str) -> str:
        return s.replace(self._api_key, "***")

    def _fetch(
        self, clean_url: str, ticker: str, d: date, *, allow_live: bool, force_refresh: bool
    ) -> str | None:
        """Cache-first response body — the ``fetch_eod`` freshness contract, with the file keyed per
        (ticker, queried date) so a new day is a structural miss. ``None`` = a 404 (unknown ticker);
        auth/plan/server/network trouble raises a key-scrubbed ``PolygonError``."""
        cache_path = self._cache_dir / f"{ticker.upper()}.polygon.{d.isoformat()}.json"
        if cache_path.exists() and not (force_refresh and allow_live):
            return cache_path.read_text(encoding="utf-8")
        if not allow_live:
            raise CacheMiss(f"no cached polygon response for {clean_url!r} (live pulls disabled)")
        try:
            import httpx  # lazy, mirroring the other clients
        except ImportError:  # pragma: no cover — polite_get itself needs httpx; surfaced below
            httpx = None
        try:
            resp = polite_get(
                f"{clean_url}&apiKey={self._api_key}",
                timeout=get_settings().http_timeout_s,
                pre=_LIMITER.acquire,  # the 5/min spacing, in front of EVERY live attempt
                backoff_base=_RETRY_WAIT_S,  # a 429 on a per-MINUTE tier clears next minute, not in 1-2s
                backoff_cap=_RETRY_WAIT_S,
                sleep=_SLEEP,
            )
        except Exception as e:
            # `from None` THROUGHOUT, deliberately: httpx error messages embed the full request URL —
            # apiKey included — and a chained cause would re-leak it into any printed traceback.
            if httpx is not None and isinstance(e, httpx.HTTPStatusError):
                code = e.response.status_code
                if code == 404:
                    return None  # Polygon does not know this ticker — a miss, not an error
                raise PolygonError(
                    f"polygon: HTTP {code} for {clean_url}{_STATUS_HINTS.get(code, '')}"
                ) from None
            raise PolygonError(f"polygon: {clean_url} failed: {self._scrub(str(e))}") from None
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(resp.text, encoding="utf-8")
        return resp.text
