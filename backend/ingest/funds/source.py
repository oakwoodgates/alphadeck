"""The fund-shares source seam — swapping/adding a share-count source is an ADAPTER, not a rewrite.

A ``FundSharesSource`` yields ONE normalized snapshot ``{d, shares_out, source, source_ref}`` for a
ticker (or ``None`` = this source does not cover the fund), carrying the ``allow_live`` /
``force_refresh`` flags the ingest path needs — the ``PriceSource`` contract's sibling, deliberately
snapshot-shaped (a page states ONE current count, not a series). ``ingest_fund_shares_for_security``
depends on this interface, not on a concrete fetcher, so:

- the Polygon adapter (``ingest.funds.polygon`` — the operator's 2026-07-26 decision: PRIMARY when a
  key is present, with the dated read the historical backfill walks) slotted in exactly this way — the
  seam took it without the ingest changing; and
- a further issuer adapter (VanEck, iShares, …) would slot in front of the aggregator the same way.

``IssuerFirstFundSource`` is the primary-then-fallback composition, used twice: Polygon → the scraper
pair (keyed), and issuer → aggregator (the scraper pair itself, and the whole story when keyless). A
clean primary MISS (a 404 / a page without the fund blob / a Polygon null-count gap) is quiet; a
primary ERROR (network trouble, a redesign breaking the parse, a bad key) is warned VISIBLY and then
falls back — the sample still lands (recall over purity) but the breakage never hides. Both legs
failing raises with both stories, so the ingest leg's NameResult captures a real "no samplable source"
state (#7/#9: a fund we cannot sample is a visible condition, never a silent omission).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from domain.settings import get_settings
from ingest.funds.polygon import PolygonFundSource
from ingest.funds.snapshot_loader import (
    fetch_globalx,
    fetch_stockanalysis,
    globalx_fund_url,
    parse_globalx,
    parse_stockanalysis,
    stockanalysis_etf_url,
)


class FundSharesUnavailable(Exception):
    """No source produced a shares-out sample for this fund — every leg missed or errored (the stories
    ride the message). The ingest leg surfaces this into the per-name ``NameResult`` (fail-visible).
    """


class FundSharesSource(Protocol):
    """A source of ONE current shares-outstanding snapshot per fund ticker.

    ``get_snapshot`` returns ``{d: date, shares_out: float, source: str, source_ref: str}`` — ``d`` the
    PAGE'S own stated as-of date, ``source`` the adapter name, ``source_ref`` the exact URL sampled — or
    ``None`` when this source does not cover the fund. ``allow_live`` gates the network; ``force_refresh``
    (live only) bypasses a cache hit (the recurring/daily freshness rule).
    """

    def get_snapshot(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> dict | None: ...


def _is_http_404(e: Exception) -> bool:
    """Is ``e`` a 404 response? The one status that means "this fund is not on this site" (an expected
    issuer miss for another family's fund) rather than trouble. httpx is imported lazily, mirroring the
    other clients."""
    try:
        import httpx
    except ImportError:  # pragma: no cover — with httpx absent, no httpx error can have been raised
        return False
    return isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404


class GlobalXFundSource:
    """The Global X issuer page — the EXACT count + the issuer's own stated as-of date. A 404 (not a
    Global X fund) and a page without the ``ETF_DETAILS`` blob are clean misses (``None``); anything
    else raises to the caller (the composite warns + falls back, so a parser broken by a redesign is
    visible, never silently absorbed)."""

    name = "globalx"

    def __init__(self, *, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir

    def get_snapshot(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> dict | None:
        try:
            html = fetch_globalx(
                ticker,
                cache_dir=self._cache_dir,
                allow_live=allow_live,
                force_refresh=force_refresh,
            )
        except Exception as e:
            if _is_http_404(e):
                return None  # another family's fund — an expected miss, not an error
            raise
        parsed = parse_globalx(html)
        if parsed is None:
            return None
        return {**parsed, "source": self.name, "source_ref": globalx_fund_url(ticker)}


class StockAnalysisFundSource:
    """The stockanalysis.com aggregator page — covers every fund incl. issuer-bot-gated ones (SMH), at
    a ~10k-share rounded resolution (the ``source`` column tells the operator which). Same miss/raise
    contract as the issuer adapter."""

    name = "stockanalysis"

    def __init__(self, *, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir

    def get_snapshot(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> dict | None:
        try:
            html = fetch_stockanalysis(
                ticker,
                cache_dir=self._cache_dir,
                allow_live=allow_live,
                force_refresh=force_refresh,
            )
        except Exception as e:
            if _is_http_404(e):
                return None  # not listed there — a miss, not an error
            raise
        parsed = parse_stockanalysis(html)
        if parsed is None:
            return None
        return {**parsed, "source": self.name, "source_ref": stockanalysis_etf_url(ticker)}


class IssuerFirstFundSource:
    """Primary first, fallback second (the operator's locked source policy — the composition is used
    for BOTH shapes: polygon → the scraper pair, and issuer → aggregator inside that pair).

    - primary MISS (``None``) → quietly try the fallback (the common case: a non-Global-X fund on the
      issuer leg, a null-count gap date on the polygon leg);
    - primary ERROR → print a visible warning naming the leg, then try the fallback (the sample still
      lands, the breakage never hides);
    - fallback also ``None``/error → ``FundSharesUnavailable`` with both legs' stories (fail-visible
      into the ingest leg's ``NameResult``).
    """

    def __init__(self, issuer: FundSharesSource, fallback: FundSharesSource) -> None:
        self._issuer = issuer
        self._fallback = fallback

    def get_snapshot(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> dict | None:
        # the warn/story label: the adapter's own name when it has one (polygon/globalx/…), so a
        # polygon failure never masquerades as "issuer" trouble; a nested composite stays generic
        primary = getattr(self._issuer, "name", "issuer")
        stories: list[str] = []
        try:
            snap = self._issuer.get_snapshot(
                ticker, allow_live=allow_live, force_refresh=force_refresh
            )
            if snap is not None:
                return snap
            stories.append(f"{primary}: no coverage")
        except Exception as e:  # noqa: BLE001 — fall back, but never silently (the warn line)
            stories.append(f"{primary}: {e}")
            print(
                f"  warn: fund-shares {primary} leg failed for {ticker}: {e} — trying the fallback"
            )
        try:
            snap = self._fallback.get_snapshot(
                ticker, allow_live=allow_live, force_refresh=force_refresh
            )
            if snap is not None:
                return snap
            stories.append("fallback: no coverage")
        except Exception as e:  # noqa: BLE001 — collected into the visible combined failure below
            stories.append(f"fallback: {e}")
        raise FundSharesUnavailable(f"no samplable source for {ticker} ({'; '.join(stories)})")


def default_fund_source(*, cache_dir: Path | None = None) -> IssuerFirstFundSource:
    """The wired default: POLYGON-primary when ``POLYGON_API_KEY`` is set, falling back to the scraper
    pair (Global X issuer-first, stockanalysis.com fallback); the scraper pair alone when keyless.

    Polygon's fallback IS the whole keyless composite, nested — so a Polygon miss (null-count gap /
    unknown ticker) or error degrades to exactly today's scraper behavior, and the absence of the key
    is the off switch (no key → the adapter is never constructed)."""
    scrapers = IssuerFirstFundSource(
        GlobalXFundSource(cache_dir=cache_dir), StockAnalysisFundSource(cache_dir=cache_dir)
    )
    key = get_settings().polygon_api_key
    if not key:
        return scrapers
    return IssuerFirstFundSource(PolygonFundSource(api_key=key, cache_dir=cache_dir), scrapers)
