"""The price-source seam — swapping the EOD source is changing an ADAPTER, not a rewrite.

A ``PriceSource`` yields NORMALIZED EOD bars (``{d, open, high, low, close, volume}`` ascending by date) —
exactly what ``ingest_prices`` already consumes — carrying the ``allow_live`` / ``force_refresh`` flags the
ingest path needs. Today's adapters wrap the existing cache-first fetchers: ``YahooPriceSource`` (the live
default) and ``StooqPriceSource`` (the formalized fallback). ``ingest_thesis`` depends on this interface, not
on a concrete fetcher.

The contract is deliberately **"a source of EOD bars", NOT "Yahoo's adjusted bars"** — so a future adapter
(e.g. a raw-prices + corporate-actions source) isn't boxed out. There is still **no ``get_splits``** on the
seam: since S2 (2026-10-20) Alpha Deck owns the adjustment for every split on its OWN list (the
``security_split`` table, S1) — the price leg puts each vendor bar on the list's scale before storing it
(``split_normalize``), whatever the vendor serves — so the seam needs no corporate-action feed. (Yahoo's
bars are USUALLY split-adjusted + re-based, but not always and not stably: the split-tape diagnosis measured
14 companies stored on two scales, and vendor flips between them — ``docs/DATA_SOURCES.md``.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ingest.prices.eod_loader import fetch_csv, fetch_eod, parse_stooq_csv


class PriceSource(Protocol):
    """A source of normalized EOD bars. ``get_bars`` returns ``[{d, open, high, low, close, volume}]``
    ascending by date; ``allow_live`` gates the network, ``force_refresh`` (live only) bypasses the cache.
    """

    def get_bars(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> list[dict]: ...


# Yahoo's history range for a member-name pull. "1y" returns ~252 trading bars — ONE short of the 253 a
# 1Y *return* needs (today's close + the close 252 bars back), so a brand-new name's 1Y cell blanked until
# the daily cron accreted past it. sma.py reads 600 CALENDAR days (its longest read-side lookback), which
# "1y" (~365 calendar days) also under-covers. "2y" is Yahoo's next discrete range (~504 bars / ~730
# calendar days) — it covers the 253-bar 1Y return AND the 600-day SMA lookback with margin. (Benchmarks
# pull a deeper "5y" for relative strength — pipeline.ingest_benchmarks.DEFAULT_RANGE — a separate choice.)
DEFAULT_RANGE = "2y"


class YahooPriceSource:
    """Yahoo's chart API (the current live default; close + volume are split-adjusted + re-based on every
    split — see ``docs/DATA_SOURCES.md``). A thin wrapper over the cache-first ``fetch_eod``."""

    def __init__(self, *, cache_dir: Path | None = None, range_: str = DEFAULT_RANGE) -> None:
        self._cache_dir = cache_dir
        self._range = range_

    def get_bars(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> list[dict]:
        return fetch_eod(
            ticker,
            cache_dir=self._cache_dir,
            allow_live=allow_live,
            range_=self._range,
            force_refresh=force_refresh,
        )


class StooqPriceSource:
    """Stooq's free CSV (the formalized fallback; its live endpoint is now apikey/captcha-gated). A thin
    wrapper over the cache-first ``fetch_csv`` + ``parse_stooq_csv``."""

    def __init__(self, *, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir

    def get_bars(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> list[dict]:
        return parse_stooq_csv(
            fetch_csv(
                ticker,
                cache_dir=self._cache_dir,
                allow_live=allow_live,
                force_refresh=force_refresh,
            )
        )
