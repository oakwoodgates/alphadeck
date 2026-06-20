"""The price-source seam — swapping the EOD source is changing an ADAPTER, not a rewrite.

A ``PriceSource`` yields NORMALIZED EOD bars (``{d, open, high, low, close, volume}`` ascending by date) —
exactly what ``ingest_prices`` already consumes — carrying the ``allow_live`` / ``force_refresh`` flags the
ingest path needs. Today's adapters wrap the existing cache-first fetchers: ``YahooPriceSource`` (the live
default) and ``StooqPriceSource`` (the formalized fallback). ``ingest_thesis`` depends on this interface, not
on a concrete fetcher.

The contract is deliberately **"a source of EOD bars", NOT "Yahoo's adjusted bars"** — so a future adapter
(e.g. a raw-prices + corporate-actions source) isn't boxed out. And there is deliberately **no
``get_splits``**: owning the split adjustment ourselves (adjusting at read time from raw bars) is a larger
storage + read change that would EXTEND this interface if/when we adopt such a source; this seam eases that
swap, it does not pre-build it. (Today's Yahoo bars are already split-adjusted + re-based on every split —
a property of the Yahoo adapter, documented in ``docs/DATA_SOURCES.md``, not an assumption baked into the
contract.)
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


class YahooPriceSource:
    """Yahoo's chart API (the current live default; close + volume are split-adjusted + re-based on every
    split — see ``docs/DATA_SOURCES.md``). A thin wrapper over the cache-first ``fetch_eod``."""

    def __init__(self, *, cache_dir: Path | None = None, range_: str = "1y") -> None:
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
