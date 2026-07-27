"""The fund-shares source seam — swapping/adding a share-count source is an ADAPTER, not a rewrite.

A ``FundSharesSource`` yields ONE normalized snapshot ``{d, shares_out, source, source_ref}`` for a
ticker (or ``None`` = this source does not cover the fund), carrying the ``allow_live`` /
``force_refresh`` flags the ingest path needs — the ``PriceSource`` contract's sibling, deliberately
snapshot-shaped (a page states ONE current count, not a series). ``ingest_fund_shares_for_security``
depends on this interface, not on a concrete fetcher, so:

- a future ISSUER adapter (VanEck, iShares, …) slots in front of the aggregator without touching the
  ingest; and
- a future BACKFILL adapter (Polygon / Wayback — the operator is probing Polygon separately) implements
  the same protocol returning historical snapshots to a separate backfill runner — the seam stays open,
  none of it is built here (operator decision 3, 2026-07-26: forward-only for now).

``IssuerFirstFundSource`` is the operator-picked composition (decision 2): try the issuer, fall back to
the aggregator. A clean issuer MISS (a 404 / a page without the fund blob — most non-Global-X funds) is
quiet; an issuer ERROR (network trouble, a redesign breaking the parse) is warned VISIBLY and then falls
back — the sample still lands (recall over purity) but the breakage never hides. Both legs failing
raises with both stories, so the ingest leg's NameResult captures a real "no samplable source" state
(#7/#9: a fund we cannot sample is a visible condition, never a silent omission).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

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
    """Issuer first, aggregator fallback (the operator's locked source policy).

    - issuer MISS (``None``) → quietly try the fallback (the common case for non-Global-X funds);
    - issuer ERROR → print a visible warning, then try the fallback (the sample still lands, the
      breakage never hides);
    - fallback also ``None``/error → ``FundSharesUnavailable`` with both legs' stories (fail-visible
      into the ingest leg's ``NameResult``).
    """

    def __init__(self, issuer: FundSharesSource, fallback: FundSharesSource) -> None:
        self._issuer = issuer
        self._fallback = fallback

    def get_snapshot(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> dict | None:
        stories: list[str] = []
        try:
            snap = self._issuer.get_snapshot(
                ticker, allow_live=allow_live, force_refresh=force_refresh
            )
            if snap is not None:
                return snap
            stories.append("issuer: no coverage")
        except Exception as e:  # noqa: BLE001 — fall back, but never silently (the warn line)
            stories.append(f"issuer: {e}")
            print(f"  warn: fund-shares issuer leg failed for {ticker}: {e} — trying the fallback")
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
    """The wired default: Global X issuer-first, stockanalysis.com fallback."""
    return IssuerFirstFundSource(
        GlobalXFundSource(cache_dir=cache_dir), StockAnalysisFundSource(cache_dir=cache_dir)
    )
